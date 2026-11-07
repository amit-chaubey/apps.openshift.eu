'use strict';

const { normalizePath, extension, NextcloudError } = require('./nextcloud');

const BRIDGE_PATH = '/sdkjs-plugins/agent-bridge/';
const EDITOR_BY_EXT = {
  docx: 'word', doc: 'word', odt: 'word', rtf: 'word', txt: 'word', md: 'word',
  xlsx: 'cell', xls: 'cell', ods: 'cell', csv: 'cell',
  pptx: 'slide', ppt: 'slide', odp: 'slide',
};

class SessionError extends Error {
  constructor(message, status = 409) {
    super(message);
    this.status = status;
  }
}

// One headless browser context per (account, file): the agent's seat in the co-editing session
class Sessions {
  constructor(config, nextcloud, launch, now = () => Date.now()) {
    this.config = config;
    this.nextcloud = nextcloud;
    this.launch = launch;
    this.now = now;
    this.sessions = new Map();
    this.opening = new Map();
    this.browser = null;
  }

  key(user, path) { return `${user}\n${path}`; }

  list(user) {
    return [...this.sessions.values()].filter(s => !user || s.user === user).map(s => this.describe(s));
  }

  describe(s) {
    return {
      path: s.path, editor: s.editor, user: s.user, displayName: s.displayName,
      openedAt: new Date(s.openedAt).toISOString(), idleSeconds: Math.round((this.now() - s.lastUsed) / 1000),
    };
  }

  checkLimits(user) {
    if (this.sessions.size + this.opening.size >= this.config.maxSessions) {
      throw new SessionError(`all ${this.config.maxSessions} live sessions are in use; close one first`, 429);
    }
    const mine = [...this.sessions.values()].filter(s => s.user === user).length
      + [...this.opening.keys()].filter(k => k.startsWith(user + '\n')).length;
    if (mine >= this.config.maxSessionsPerUser) {
      throw new SessionError(`this account already has ${mine} live sessions (limit ${this.config.maxSessionsPerUser}); close one first`, 429);
    }
  }

  async getBrowser() {
    if (!this.browser || (this.browser.isConnected && !this.browser.isConnected())) this.browser = await this.launch();
    return this.browser;
  }

  async open(authorization, rawPath, { create = false } = {}) {
    const path = normalizePath(rawPath);
    const me = await this.nextcloud.identity(authorization);
    const key = this.key(me.id, path);
    const existing = this.sessions.get(key);
    if (existing) { existing.lastUsed = this.now(); return { ...this.describe(existing), reused: true }; }
    if (this.opening.has(key)) return this.opening.get(key);
    this.checkLimits(me.id);
    const started = this.now();
    const task = (async () => {
      const raw = create ? await this.nextcloud.createUrl(authorization, path) : await this.nextcloud.openUrl(authorization, path);
      const url = browserUrl(raw, this.config.publicUrl);
      const browser = await this.getBrowser();
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US', serviceWorkers: 'block' });
      try {
        const page = await context.newPage();
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: this.config.openTimeoutSeconds * 1000 });
        const bridge = await waitForBridge(page, this.config.openTimeoutSeconds * 1000);
        await confine(context, page, bridge);
        let editor = '';
        try { editor = await bridge.evaluate(() => window.agentBridge.editorType()); } catch (e) { editor = ''; }
        if (!['word', 'cell', 'slide'].includes(editor)) editor = EDITOR_BY_EXT[extension(path)] || 'word';
        const s = { key, path, user: me.id, displayName: me.displayName, editor, context, page, bridge, openedAt: this.now(), lastUsed: this.now() };
        this.sessions.set(key, s);
        return { ...this.describe(s), reused: false, openMs: this.now() - started };
      } catch (err) {
        await context.close().catch(() => {});
        throw err;
      }
    })();
    this.opening.set(key, task);
    try { return await task; } finally { this.opening.delete(key); }
  }

  async get(authorization, rawPath) {
    const path = normalizePath(rawPath);
    const me = await this.nextcloud.identity(authorization);
    const s = this.sessions.get(this.key(me.id, path));
    if (!s) throw new SessionError(`no live session for ${path}: call office_live_open first`, 404);
    s.lastUsed = this.now();
    if (s.page.isClosed()) { this.sessions.delete(s.key); throw new SessionError('the editor page closed; open the file again', 410); }
    return s;
  }

  // an editor that does not answer holds the session's queue: the session is closed
  async answer(s, call) {
    const ms = this.config.scriptTimeoutSeconds * 1000;
    let timer;
    const late = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new SessionError(`the editor did not answer within ${ms / 1000} seconds; the session is closed, open the file again`, 504)), ms);
    });
    try {
      const result = await Promise.race([call(), late]);
      s.lastUsed = this.now();
      return result;
    } catch (err) {
      if (err.status === 504) await this.drop(s);
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  async command(s, source, scope = {}, recalculate = true) {
    const result = await this.answer(s, () => s.bridge.evaluate(([src, sc, rc]) => window.agentBridge.command(src, sc, rc), [source, scope, recalculate]));
    if (result && typeof result === 'object' && result.onlyoffice_id_error_message) throw new SessionError(result.onlyoffice_id_error_message, 422);
    return result;
  }

  method(s, name, args) {
    return this.answer(s, () => s.bridge.evaluate(([n, a]) => window.agentBridge.method(n, a), [name, args]));
  }

  // the editor frame's own API, for what no plugin method covers (save)
  async editorEval(s, fn, arg) {
    const frame = s.page.frames().find(f => f.url().includes('/web-apps/apps/') && f.url().includes('/main/index'));
    if (!frame) throw new SessionError('editor frame not found', 500);
    return frame.evaluate(fn, arg);
  }

  async close(authorization, rawPath) {
    const s = await this.get(authorization, rawPath);
    await this.flush(s);
    await this.drop(s);
    return { closed: s.path };
  }

  // Edits still in the browser die with it: send them and wait until the editor holds none
  async flush(s) {
    const unsent = () => this.editorEval(s, () => {
      const api = window.Asc && window.Asc.editor;
      if (!api) return false;
      if (typeof api.asc_Save === 'function') api.asc_Save();
      return typeof api.isDocumentModified === 'function' ? api.isDocumentModified() : null;
    });
    try {
      for (let i = 0; i < 15; i++) {
        await s.page.waitForTimeout(1000);
        if ((await unsent()) === false) break;
      }
      await s.page.waitForTimeout(this.config.closeSettleMs);
    } catch (err) {
      // a page that is gone has nothing left to send
    }
  }

  async drop(s) {
    this.sessions.delete(s.key);
    await s.context.close().catch(() => {});
  }

  async sweep() {
    const limit = this.config.idleTimeoutSeconds * 1000;
    for (const s of [...this.sessions.values()]) {
      if (this.now() - s.lastUsed > limit || s.page.isClosed()) await this.drop(s);
    }
    if (this.sessions.size === 0 && this.opening.size === 0 && this.browser) {
      const b = this.browser; this.browser = null; await b.close().catch(() => {});
    }
  }

  async shutdown() {
    for (const s of [...this.sessions.values()]) await this.drop(s);
    if (this.browser) await this.browser.close().catch(() => {});
  }
}

// The editor page on the public origin: Nextcloud builds the link from the host the API was
// called on, an internal Service name the Document Server page cannot run under
function browserUrl(url, publicUrl) {
  if (!publicUrl) return url;
  const u = new URL(url);
  const p = new URL(publicUrl);
  const base = p.pathname.replace(/\/+$/, '');
  return p.origin + base + u.pathname + u.search + u.hash;
}

// The browser talks to the origins of the open editor only: a document script runs with the
// network of this pod otherwise
async function confine(context, page, bridge) {
  const allowed = new Set(['data:', 'blob:', 'about:']);
  const origin = u => { try { return new URL(u).origin; } catch (e) { return ''; } };
  allowed.add(origin(page.url()));
  allowed.add(origin(bridge.url()));
  const permitted = u => {
    const url = new URL(u);
    if (allowed.has(url.protocol)) return true;
    const httpOrigin = url.origin.replace(/^ws/, 'http');
    return allowed.has(httpOrigin);
  };
  await context.route(u => !permitted(u.href), route => route.abort('blockedbyclient'));
  await context.routeWebSocket(u => !permitted(u.href), ws => ws.close());
}

async function waitForBridge(page, timeout) {
  const end = Date.now() + timeout;
  let editorSeen = false;
  while (Date.now() < end) {
    for (const f of page.frames()) {
      const u = f.url();
      if (u.includes('/web-apps/apps/')) editorSeen = true;
      if (!u.includes(BRIDGE_PATH)) continue;
      try {
        if (await f.evaluate(() => !!(window.agentBridge && window.agentBridge.ready))) return f;
      } catch (e) { /* frame navigating */ }
    }
    await page.waitForTimeout(250);
  }
  if (!editorSeen) throw new NextcloudError('the editor did not load: check that Nextcloud opens the file in ONLYOFFICE', 504);
  throw new NextcloudError('the editor loaded without the agent-bridge plugin: the Document Server must carry it in sdkjs-plugins', 501);
}

module.exports = { Sessions, SessionError, waitForBridge, browserUrl, EDITOR_BY_EXT };
