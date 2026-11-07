'use strict';

const http = require('node:http');
const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StreamableHTTPServerTransport } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { load } = require('./config');
const { Nextcloud } = require('./nextcloud');
const { Sessions } = require('./sessions');
const { register } = require('./tools');
const pkg = require('../package.json');

const MAX_BODY = 1024 * 1024;

function credentialOf(req, config) {
  const h = req.headers.authorization;
  if (h && /^(Bearer|Basic) \S+$/i.test(h)) return h;
  if (config.sharedUser && config.sharedPassword) {
    return 'Basic ' + Buffer.from(`${config.sharedUser}:${config.sharedPassword}`).toString('base64');
  }
  return '';
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', c => {
      size += c.length;
      if (size > MAX_BODY) { reject(Object.assign(new Error('request body too large'), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function createApp(config, sessions, nextcloud) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/healthz') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, sessions: sessions.sessions.size }));
      return;
    }
    if (url.pathname !== '/' && url.pathname !== '/mcp') { res.writeHead(404).end(); return; }
    if (req.method !== 'POST') {
      res.writeHead(405, { Allow: 'POST', 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed: stateless server, POST only' }, id: null }));
      return;
    }
    try {
      const raw = await readBody(req);
      const body = raw ? JSON.parse(raw) : undefined;
      const server = new McpServer({ name: 'mcp-onlyoffice-live', version: pkg.version });
      register(server, { sessions, nextcloud, config, credential: credentialOf(req, config) });
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
      res.on('close', () => { transport.close().catch(() => {}); server.close().catch(() => {}); });
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    } catch (err) {
      if (!res.headersSent) {
        res.writeHead(err.status || 400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ jsonrpc: '2.0', error: { code: -32700, message: err.message }, id: null }));
      }
    }
  });
}

async function main() {
  const config = load();
  const { chromium } = require('playwright-core');
  const launch = () => chromium.launch({
    headless: true,
    args: ['--disable-dev-shm-usage', '--disable-gpu', '--no-first-run', '--mute-audio'],
  });
  const nextcloud = new Nextcloud(config);
  const sessions = new Sessions(config, nextcloud, launch);
  const app = createApp(config, sessions, nextcloud);
  const timer = setInterval(() => sessions.sweep().catch(() => {}), 30000);
  app.listen(config.port, '0.0.0.0', () => {
    console.log(JSON.stringify({ msg: 'listening', port: config.port, nextcloud: config.nextcloudUrl, maxSessions: config.maxSessions }));
  });
  const stop = async () => { clearInterval(timer); app.close(); await sessions.shutdown(); process.exit(0); };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

if (require.main === module) main().catch(err => { console.error(err); process.exit(1); });

module.exports = { createApp, credentialOf };
