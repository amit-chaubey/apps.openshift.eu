'use strict';

const crypto = require('node:crypto');

class NextcloudError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// A path inside the caller's own files: relative, no "..", no control characters
function normalizePath(path) {
  if (typeof path !== 'string') throw new NextcloudError('path must be a string', 400);
  const parts = path.replace(/\\/g, '/').split('/').filter(p => p !== '' && p !== '.');
  if (parts.length === 0) throw new NextcloudError('path is empty', 400);
  for (const p of parts) {
    if (p === '..') throw new NextcloudError('path must not contain ..', 400);
    if (/[\u0000-\u001f]/.test(p)) throw new NextcloudError('path contains a control character', 400);
  }
  return parts.join('/');
}

function extension(path) {
  const m = /\.([A-Za-z0-9]+)$/.exec(path);
  return m ? m[1].toLowerCase() : '';
}

function tag(xml, name) {
  const m = new RegExp(`<([a-z]+:)?${name}(\\s[^>]*)?>([\\s\\S]*?)</([a-z]+:)?${name}>`, 'i').exec(xml);
  if (m) return m[3].trim();
  return new RegExp(`<([a-z]+:)?${name}(\\s[^>]*)?/>`, 'i').test(xml) ? '' : null;
}

function unescapeXml(s) {
  return s === null ? null : s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'").replace(/&amp;/g, '&');
}

class Nextcloud {
  constructor(config, fetchImpl = fetch) {
    this.base = config.nextcloudUrl;
    this.editorId = config.editorId;
    this.cacheMs = config.identityCacheSeconds * 1000;
    this.fetch = fetchImpl;
    this.identities = new Map();
  }

  async request(authorization, method, path, { body, headers = {}, json = true } = {}) {
    if (!authorization) throw new NextcloudError('no credential: send Authorization with a token or app password Nextcloud accepts', 401);
    const res = await this.fetch(this.base + path, {
      method,
      headers: { Authorization: authorization, 'OCS-APIRequest': 'true', Accept: json ? 'application/json' : '*/*', ...headers },
      body,
      redirect: 'manual',
    });
    const text = await res.text();
    if (res.status === 401 || res.status === 403) throw new NextcloudError(`Nextcloud refused the credential (${res.status})`, res.status);
    if (res.status >= 400) {
      let message = text.slice(0, 300);
      try { message = JSON.parse(text).ocs.meta.message || message; } catch (e) { /* not OCS */ }
      throw new NextcloudError(`Nextcloud ${method} ${path.split('?')[0]} answered ${res.status}: ${message}`, res.status);
    }
    if (!json) return text;
    try { return JSON.parse(text); } catch (e) {
      throw new NextcloudError(`Nextcloud ${path.split('?')[0]} answered no JSON (${res.status})`, 502);
    }
  }

  // The account behind a credential, cached briefly per credential
  async identity(authorization) {
    const key = crypto.createHash('sha256').update(authorization || '').digest('hex');
    const hit = this.identities.get(key);
    if (hit && hit.expires > Date.now()) return hit.value;
    const data = await this.request(authorization, 'GET', '/ocs/v2.php/cloud/user?format=json');
    const d = data && data.ocs && data.ocs.data;
    if (!d || !d.id) throw new NextcloudError('Nextcloud returned no user for the credential', 401);
    const value = { id: String(d.id), displayName: String(d.displayname || d['display-name'] || d.id) };
    this.identities.set(key, { value, expires: Date.now() + this.cacheMs });
    if (this.identities.size > 1000) this.identities.delete(this.identities.keys().next().value);
    return value;
  }

  davPath(user, path) {
    return `/remote.php/dav/files/${encodeURIComponent(user)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  }

  async editors(authorization) {
    const data = await this.request(authorization, 'GET', '/ocs/v2.php/apps/files/api/v1/directEditing?format=json');
    return (data.ocs && data.ocs.data) || { editors: {}, creators: {} };
  }

  // URL of the editor page for the file, opened as the credential's account
  async openUrl(authorization, path) {
    const ed = await this.editors(authorization);
    const editor = ed.editors && ed.editors[this.editorId];
    if (!editor) throw new NextcloudError(`Nextcloud has no direct editor ${this.editorId}: is the ONLYOFFICE app enabled?`, 501);
    const ext = extension(path);
    const mimes = [...(editor.mimetypes || []), ...(editor.optionalMimetypes || [])];
    if (ext && mimes.length && !mimes.some(m => mimeMatches(m, ext))) {
      throw new NextcloudError(`ONLYOFFICE does not edit .${ext} files here`, 415);
    }
    const data = await this.request(authorization, 'POST', '/ocs/v2.php/apps/files/api/v1/directEditing/open?format=json', {
      body: new URLSearchParams({ path: '/' + path, editorId: this.editorId }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    const url = data.ocs && data.ocs.data && data.ocs.data.url;
    if (!url) throw new NextcloudError('Nextcloud returned no editor URL', 502);
    return url;
  }

  // A new empty document from the editor's own template, then its editor URL
  async createUrl(authorization, path) {
    const ed = await this.editors(authorization);
    const ext = extension(path);
    const creator = Object.values(ed.creators || {}).find(c => c.editor === this.editorId && c.extension === ext);
    if (!creator) throw new NextcloudError(`no ONLYOFFICE creator for .${ext} files`, 415);
    const data = await this.request(authorization, 'POST', '/ocs/v2.php/apps/files/api/v1/directEditing/create?format=json', {
      body: new URLSearchParams({ path: '/' + path, editorId: this.editorId, creatorId: creator.id }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    const url = data.ocs && data.ocs.data && data.ocs.data.url;
    if (!url) throw new NextcloudError('Nextcloud returned no editor URL', 502);
    return url;
  }

  // File id and lock state (Nextcloud files_lock), as the credential's account sees them
  async fileState(authorization, user, path) {
    const body = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns"><d:prop>'
      + '<oc:fileid/><d:getlastmodified/><d:getetag/><nc:lock/><nc:lock-owner/><nc:lock-owner-displayname/><nc:lock-owner-type/>'
      + '<nc:lock-owner-editor/><nc:lock-time/><nc:lock-timeout/></d:prop></d:propfind>';
    const xml = await this.request(authorization, 'PROPFIND', this.davPath(user, path), {
      body, headers: { Depth: '0', 'Content-Type': 'application/xml' }, json: false,
    });
    const ok = /<d:status>HTTP\/1\.1 200 OK<\/d:status>/i;
    const props = xml.split(/<d:propstat>/i).filter(s => ok.test(s)).join('');
    const locked = tag(props, 'lock');
    const ownerType = tag(props, 'lock-owner-type');
    return {
      fileId: unescapeXml(tag(props, 'fileid')),
      etag: unescapeXml(tag(props, 'getetag')),
      lastModified: unescapeXml(tag(props, 'getlastmodified')),
      locked: locked === '1' || locked === 'true',
      lockOwner: unescapeXml(tag(props, 'lock-owner')),
      lockOwnerDisplayName: unescapeXml(tag(props, 'lock-owner-displayname')),
      // files_lock: 0 user, 1 app (an editor such as ONLYOFFICE), 2 token
      lockOwnerType: ownerType === null || ownerType === '' ? null : Number(ownerType),
      lockOwnerEditor: unescapeXml(tag(props, 'lock-owner-editor')),
      lockTime: Number(tag(props, 'lock-time')) || null,
      lockTimeout: Number(tag(props, 'lock-timeout')) || null,
    };
  }
}

function mimeMatches(mime, ext) {
  const table = {
    docx: 'wordprocessingml.document', xlsx: 'spreadsheetml.sheet', pptx: 'presentationml.presentation',
    odt: 'opendocument.text', ods: 'opendocument.spreadsheet', odp: 'opendocument.presentation',
    doc: 'msword', xls: 'ms-excel', ppt: 'ms-powerpoint', txt: 'text/plain', csv: 'text/csv', pdf: 'application/pdf',
    rtf: 'rtf', md: 'markdown',
  };
  return table[ext] ? mime.includes(table[ext]) : true;
}

module.exports = { Nextcloud, NextcloudError, normalizePath, extension, tag };
