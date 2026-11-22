'use strict';
// DephGraph runtime collector (no deps). Enable with: node -r ./dephgraph-collector.cjs app.js
// Env: DEPHGRAPH_URL, DEPHGRAPH_TOKEN, DEPHGRAPH_SERVICE_ID, optional DEPHGRAPH_INTERVAL (seconds, 60..604800, default 3600).
// Never throws into the host app: every failure is swallowed after one warning.
const fs = require('fs');
const path = require('path');

const MAX_SAMPLES = 5000; // API limit per request
const TIMEOUT_MS = 2000;
const EARLY_SEND_MS = 60000; // first report soon after start so short-lived or restarted processes still count
const NM = `${path.sep}node_modules${path.sep}`;
let warned = false;
const pkgCache = new Map(); // pkgDir -> {name, version} | null: package.json is read once per package

function warn(err) {
  if (warned) return;
  warned = true;
  console.warn(`dephgraph collector: ${err && err.message} (further errors suppressed)`);
}

function readPkg(pkgDir) {
  if (!pkgCache.has(pkgDir)) {
    let pkg = null;
    try {
      const { name, version } = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'));
      if (name && version) pkg = { name, version };
    } catch (e) {
      // unreadable package.json: skip this package only
    }
    pkgCache.set(pkgDir, pkg);
  }
  return pkgCache.get(pkgDir);
}

// shortcut: only CommonJS modules (require.cache); ESM-only imports are not seen, upgrade via a module loader hook.
function collect() {
  const dirs = new Set();
  for (const file of Object.keys(require.cache)) {
    const i = file.lastIndexOf(NM);
    if (i < 0) continue;
    const rest = file.slice(i + NM.length).split(path.sep);
    dirs.add(path.join(file.slice(0, i + NM.length), ...rest.slice(0, rest[0].startsWith('@') ? 2 : 1)));
  }
  const seen = new Map();
  for (const dir of dirs) {
    const pkg = readPkg(dir);
    if (pkg) seen.set(`${pkg.name}@${pkg.version}`, { ...pkg, exec_count: 1 });
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function intervalSeconds(raw = process.env.DEPHGRAPH_INTERVAL) {
  const n = raw === undefined || raw === '' ? NaN : Number(raw);
  return Number.isFinite(n) && n >= 60 && n <= 604800 ? n : 3600;
}

async function send(url, token, serviceId, samples = collect()) {
  for (let i = 0; i < samples.length; i += MAX_SAMPLES) {
    const res = await fetch(`${url.replace(/\/+$/, '')}/runtime/samples`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token.trim()}` },
      body: JSON.stringify({ service_id: serviceId, samples: samples.slice(i, i + MAX_SAMPLES) }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  }
}

async function flush() {
  try {
    const { DEPHGRAPH_URL: url, DEPHGRAPH_TOKEN: token, DEPHGRAPH_SERVICE_ID: sid } = process.env;
    await send(url, token, Number(sid));
  } catch (err) {
    warn(err);
  }
}

function start() {
  const { DEPHGRAPH_URL, DEPHGRAPH_TOKEN, DEPHGRAPH_SERVICE_ID } = process.env;
  if (!DEPHGRAPH_URL || !DEPHGRAPH_TOKEN || !DEPHGRAPH_SERVICE_ID) return null;
  const timer = setInterval(flush, intervalSeconds() * 1000);
  timer.unref(); // never keep the host process alive
  setTimeout(flush, EARLY_SEND_MS).unref();
  // shortcut: beforeExit does not fire on process.exit() or signals (SIGTERM/SIGINT); those exits skip the final
  // flush and rely on the early and periodic sends. No signal handlers: they would change the host's exit behaviour.
  let done = false;
  process.on('beforeExit', async () => {
    if (done) return;
    done = true;
    await flush();
  });
  return timer;
}

module.exports = { collect, send, flush, start, intervalSeconds };
start();
