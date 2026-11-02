'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { normalizePath, Nextcloud, tag } = require('../src/nextcloud');
const { typingChunks } = require('../src/commands');
const { Sessions, browserUrl } = require('../src/sessions');
const rich = require('../src/rich');
const { load } = require('../src/config');
const { createApp, credentialOf } = require('../src/server');

test('normalizePath keeps a path inside the account', () => {
  assert.strictEqual(normalizePath('/Documents//Plan.docx'), 'Documents/Plan.docx');
  assert.strictEqual(normalizePath('./a/./b.docx'), 'a/b.docx');
  assert.throws(() => normalizePath('../x.docx'), /\.\./);
  assert.throws(() => normalizePath('a/../../x.docx'), /\.\./);
  assert.throws(() => normalizePath(''), /empty/);
  assert.throws(() => normalizePath('a\u0000b'), /control/);
  assert.throws(() => normalizePath(5), /string/);
});

test('typingChunks splits on words and keeps newlines', () => {
  assert.deepStrictEqual(typingChunks('Hello world\nnext'), ['Hello ', 'world', '\n', 'next']);
  assert.deepStrictEqual(typingChunks('x'.repeat(30), 24), ['x'.repeat(24), 'x'.repeat(6)]);
});

test('config defaults and validation', () => {
  const c = load({});
  assert.strictEqual(c.nextcloudUrl, 'http://nextcloud:8080');
  assert.strictEqual(c.maxSessions, 4);
  assert.strictEqual(load({ NEXTCLOUD_URL: 'https://cloud.example.com/' }).nextcloudUrl, 'https://cloud.example.com');
  assert.throws(() => load({ NEXTCLOUD_URL: 'ftp://x' }), /http/);
  assert.throws(() => load({ MAX_SESSIONS: '-1' }), /non-negative/);
});

test('credentialOf forwards the caller credential, else the shared account', () => {
  const cfg = load({});
  assert.strictEqual(credentialOf({ headers: { authorization: 'Bearer abc' } }, cfg), 'Bearer abc');
  assert.strictEqual(credentialOf({ headers: {} }, cfg), '');
  const shared = load({ NEXTCLOUD_USER: 'bot', NEXTCLOUD_PASSWORD: 'pw' });
  assert.strictEqual(credentialOf({ headers: {} }, shared), 'Basic ' + Buffer.from('bot:pw').toString('base64'));
  assert.strictEqual(credentialOf({ headers: { authorization: 'Weird x y' } }, cfg), '');
});

function fakeFetch(routes, calls = []) {
  return async (url, opts) => {
    calls.push({ url, opts });
    for (const [match, reply] of routes) {
      if (url.includes(match)) {
        const r = typeof reply === 'function' ? reply(url, opts) : reply;
        return { status: r.status || 200, text: async () => (typeof r.body === 'string' ? r.body : JSON.stringify(r.body)) };
      }
    }
    return { status: 404, text: async () => 'not found' };
  };
}

test('identity is cached per credential and refusals surface as 401', async () => {
  const calls = [];
  const nc = new Nextcloud(load({}), fakeFetch([
    ['/cloud/user', (u, o) => o.headers.Authorization === 'Bearer good'
      ? { body: { ocs: { data: { id: 'agent1', displayname: 'Agent One' } } } } : { status: 401, body: '' }],
  ], calls));
  assert.deepStrictEqual(await nc.identity('Bearer good'), { id: 'agent1', displayName: 'Agent One' });
  await nc.identity('Bearer good');
  assert.strictEqual(calls.length, 1);
  await assert.rejects(nc.identity('Bearer bad'), e => e.status === 401);
  await assert.rejects(nc.identity(''), e => e.status === 401);
});

test('openUrl asks direct editing for the ONLYOFFICE editor', async () => {
  const calls = [];
  const nc = new Nextcloud(load({}), fakeFetch([
    ['/directEditing/open', { body: { ocs: { data: { url: 'https://cloud/apps/files/directEditing/tok' } } } }],
    ['/directEditing?', { body: { ocs: { data: { editors: { onlyoffice: { mimetypes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'] } }, creators: {} } } } }],
  ], calls));
  assert.strictEqual(await nc.openUrl('Bearer t', 'Docs/A.docx'), 'https://cloud/apps/files/directEditing/tok');
  const open = calls.find(c => c.url.includes('/directEditing/open'));
  assert.match(open.opts.body.toString(), /path=%2FDocs%2FA.docx/);
  assert.match(open.opts.body.toString(), /editorId=onlyoffice/);
  await assert.rejects(nc.openUrl('Bearer t', 'Docs/A.pptx'), e => e.status === 415);
});

test('fileState reads the files_lock properties', async () => {
  const xml = `<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:response><d:href>/remote.php/dav/files/a/x.docx</d:href>
  <d:propstat><d:prop><oc:fileid>42</oc:fileid><nc:lock>1</nc:lock><nc:lock-owner-type>1</nc:lock-owner-type>
  <nc:lock-owner>onlyoffice</nc:lock-owner><nc:lock-owner-editor>onlyoffice</nc:lock-owner-editor><nc:lock-time>1700000000</nc:lock-time></d:prop>
  <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  <d:propstat><d:prop><nc:lock-timeout/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response></d:multistatus>`;
  const nc = new Nextcloud(load({}), fakeFetch([['/remote.php/dav/files/a/x.docx', { status: 207, body: xml }]]));
  const st = await nc.fileState('Bearer t', 'a', 'x.docx');
  assert.strictEqual(st.fileId, '42');
  assert.strictEqual(st.locked, true);
  assert.strictEqual(st.lockOwnerType, 1);
  assert.strictEqual(st.lockOwnerEditor, 'onlyoffice');
  assert.strictEqual(st.lockTimeout, null);
  assert.strictEqual(tag('<a:b/>', 'b'), '');
});

function fakeBrowser(log) {
  return async () => ({
    isConnected: () => true,
    close: async () => log.push('browser-close'),
    newContext: async () => ({
      route: async () => {}, routeWebSocket: async () => {},
      close: async () => log.push('context-close'),
      newPage: async () => {
        const bridge = { url: () => 'https://office/sdkjs-plugins/agent-bridge/index.html', evaluate: async (fn) => {
          const src = fn.toString();
          if (src.includes('ready')) return true;
          if (src.includes('editorType')) return 'word';
          return { ok: true };
        } };
        return {
          goto: async () => {}, frames: () => [bridge], url: () => 'https://cloud/x', waitForTimeout: async () => {}, isClosed: () => false,
        };
      },
    }),
  });
}

test('sessions: reuse, per-account and global limits, idle sweep', async () => {
  let t = 0;
  const log = [];
  const nc = {
    identity: async a => ({ id: a.split(' ')[1], displayName: a }),
    openUrl: async () => 'https://cloud/x', createUrl: async () => 'https://cloud/y',
  };
  const cfg = { ...load({}), maxSessions: 3, maxSessionsPerUser: 2, idleTimeoutSeconds: 10 };
  const s = new Sessions(cfg, nc, fakeBrowser(log), () => t);
  const a = await s.open('Bearer u1', 'a.docx');
  assert.strictEqual(a.editor, 'word');
  assert.strictEqual((await s.open('Bearer u1', '/a.docx')).reused, true);
  await s.open('Bearer u1', 'b.docx');
  await assert.rejects(s.open('Bearer u1', 'c.docx'), e => e.status === 429);
  await s.open('Bearer u2', 'a.docx');
  await assert.rejects(s.open('Bearer u3', 'a.docx'), e => e.status === 429);
  await assert.rejects(s.get('Bearer u3', 'a.docx'), e => e.status === 404);
  assert.strictEqual(s.list('u1').length, 2);
  t = 5000;
  await s.get('Bearer u2', 'a.docx');
  t = 12000;
  await s.sweep();
  assert.deepStrictEqual(s.list().map(x => x.user), ['u2']);
  t = 30000;
  await s.sweep();
  assert.strictEqual(s.list().length, 0);
  assert.ok(log.includes('browser-close'));
});

test('HTTP: health, method check and tools/list', async () => {
  const cfg = load({});
  const sessions = { sessions: new Map(), list: () => [] };
  const app = createApp(cfg, sessions, {});
  await new Promise(r => app.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${app.address().port}`;
  try {
    assert.strictEqual((await fetch(base + '/healthz')).status, 200);
    assert.strictEqual((await fetch(base + '/')).status, 405);
    const res = await fetch(base + '/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} }),
    });
    const body = await res.json();
    const names = body.result.tools.map(x => x.name).sort();
    assert.deepStrictEqual(names, ['close', 'comment', 'comment_delete', 'comment_reply', 'comment_resolve', 'comments', 'create', 'edit_table',
    'file_status', 'format_cells', 'format_text', 'insert', 'insert_chart', 'insert_image', 'insert_list',
    'insert_table', 'open', 'read', 'replace', 'run', 'save', 'sessions', 'track_changes', 'view', 'write_cells']);
  } finally { app.close(); }
});

test('browserUrl moves the editor link to the public origin', () => {
  assert.strictEqual(browserUrl('https://nextcloud:8080/apps/files/directEditing/t?x=1', 'https://cloud.example.com'),
    'https://cloud.example.com/apps/files/directEditing/t?x=1');
  assert.strictEqual(browserUrl('https://a/apps/x', ''), 'https://a/apps/x');
  assert.strictEqual(load({ NEXTCLOUD_PUBLIC_URL: 'https://c.example.com/' }).publicUrl, 'https://c.example.com');
});

test('every editor snippet is a function expression that parses', () => {
  const c = require('../src/commands');
  for (const type of ['word', 'cell', 'slide']) {
    for (const [name, src] of Object.entries(c.forEditor(type))) {
      assert.doesNotThrow(() => new Function(`return (${src});`), `${type}.${name}`);
    }
  }
  assert.throws(() => c.forEditor('pdf'), /unsupported/);
});

test('every rich snippet and a script source parse', () => {
  for (const type of ['word', 'cell', 'slide']) {
    for (const [name, src] of Object.entries(rich[type])) {
      assert.doesNotThrow(() => new Function(`return (${src});`), `${type}.${name}`);
    }
  }
  assert.doesNotThrow(() => new Function(`return (${rich.scriptSource('return 1;')});`));
  assert.throws(() => rich.richFor('slide', 'write'), e => e.status === 400);
});

test('commentView gives author, resolved state and replies', () => {
  assert.deepStrictEqual(rich.commentView({ Id: '7', Data: {
    UserName: 'A', Text: 'x', QuoteText: 'q', Solved: true, Time: '0',
    Replies: [{ UserName: 'B', Text: 'y' }],
  } }), {
    id: '7', author: 'A', text: 'x', time: new Date(0).toISOString(), quote: 'q', resolved: true,
    replies: [{ author: 'B', text: 'y', time: null }],
  });
});
