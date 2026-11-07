'use strict';

function int(name, fallback, env) {
  const raw = env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value) || value < 0) throw new Error(`${name} must be a non-negative integer`);
  return value;
}

function load(env = process.env) {
  const nextcloudUrl = (env.NEXTCLOUD_URL || 'http://nextcloud:8080').replace(/\/+$/, '');
  const url = new URL(nextcloudUrl);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('NEXTCLOUD_URL must be http or https');
  const publicUrl = (env.NEXTCLOUD_PUBLIC_URL || '').replace(/\/+$/, '');
  if (publicUrl && !/^https?:\/\//.test(publicUrl)) throw new Error('NEXTCLOUD_PUBLIC_URL must be http or https');
  return {
    port: int('PORT', 13081, env),
    nextcloudUrl,
    // origin the browser opens editor pages on, when the API URL is an internal one; empty:
    // the URL Nextcloud returns
    publicUrl,
    // a fixed account for callers that send no Authorization header; empty: none
    sharedUser: env.NEXTCLOUD_USER || '',
    sharedPassword: env.NEXTCLOUD_PASSWORD || '',
    editorId: env.EDITOR_ID || 'onlyoffice',
    maxSessions: int('MAX_SESSIONS', 4, env),
    maxSessionsPerUser: int('MAX_SESSIONS_PER_USER', 2, env),
    idleTimeoutSeconds: int('IDLE_TIMEOUT_SECONDS', 900, env),
    openTimeoutSeconds: int('OPEN_TIMEOUT_SECONDS', 120, env),
    typingDelayMs: int('TYPING_DELAY_MS', 120, env),
    identityCacheSeconds: int('IDENTITY_CACHE_SECONDS', 60, env),
    maxTextLength: int('MAX_TEXT_LENGTH', 100000, env),
    screenshots: (env.SCREENSHOTS || 'true') === 'true',
    scripts: (env.SCRIPTS || 'true') === 'true',
    scriptTimeoutSeconds: int('SCRIPT_TIMEOUT_SECONDS', 60, env),
    closeSettleMs: int('CLOSE_SETTLE_MS', 2000, env),
  };
}

module.exports = { load };
