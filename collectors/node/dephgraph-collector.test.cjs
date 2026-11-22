'use strict';
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const c = require('./dephgraph-collector.cjs');

function server() {
  const captured = [];
  const srv = http.createServer((req, res) => {
    let b = '';
    req.on('data', (d) => (b += d));
    req.on('end', () => {
      captured.push({ url: req.url, auth: req.headers.authorization, body: JSON.parse(b) });
      res.end('{}');
    });
  });
  return new Promise((r) =>
    srv.listen(0, '127.0.0.1', () => r({ srv, captured, url: `http://127.0.0.1:${srv.address().port}` })),
  );
}

test('send payload and header', async () => {
  const { srv, captured, url } = await server();
  await c.send(`${url}/`, 'dgs_tok', 7, [{ name: 'a', version: '1', exec_count: 1 }]);
  srv.close();
  assert.deepStrictEqual(captured[0], {
    url: '/runtime/samples',
    auth: 'Bearer dgs_tok',
    body: { service_id: 7, samples: [{ name: 'a', version: '1', exec_count: 1 }] },
  });
});

test('collect reads scoped node_modules packages from require.cache', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dg-'));
  const pkg = path.join(dir, 'node_modules', '@s', 'p');
  fs.mkdirSync(pkg, { recursive: true });
  fs.writeFileSync(path.join(pkg, 'package.json'), '{"name":"@s/p","version":"2.0.0"}');
  fs.writeFileSync(path.join(pkg, 'index.js'), 'module.exports=1');
  require(path.join(pkg, 'index.js'));
  assert.deepStrictEqual(
    c.collect().find((s) => s.name === '@s/p'),
    { name: '@s/p', version: '2.0.0', exec_count: 1 },
  );
});

test('batches within limit', async () => {
  const { srv, captured, url } = await server();
  const samples = Array.from({ length: 5001 }, (_, i) => ({ name: `p${i}`, version: '1', exec_count: 1 }));
  await c.send(url, 't', 1, samples);
  srv.close();
  assert.deepStrictEqual(captured.map((x) => x.body.samples.length), [5000, 1]);
});

test('flush swallows errors with one warning', async () => {
  Object.assign(process.env, { DEPHGRAPH_URL: 'http://127.0.0.1:1', DEPHGRAPH_TOKEN: 't', DEPHGRAPH_SERVICE_ID: '1' });
  const warns = [];
  const orig = console.warn;
  console.warn = (m) => warns.push(m);
  await c.flush();
  await c.flush();
  console.warn = orig;
  assert.strictEqual(warns.length, 1);
});

test('interval validation', () => {
  const cases = [['120', 120], ['0', 3600], ['-5', 3600], ['abc', 3600], ['59', 3600], ['1e12', 3600], ['', 3600], [undefined, 3600]];
  for (const [raw, want] of cases) assert.strictEqual(c.intervalSeconds(raw), want, String(raw));
});

test('package.json is read once per package', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dg-'));
  const pkg = path.join(dir, 'node_modules', 'cached');
  fs.mkdirSync(pkg, { recursive: true });
  fs.writeFileSync(path.join(pkg, 'package.json'), '{"name":"cached","version":"1.0.0"}');
  fs.writeFileSync(path.join(pkg, 'index.js'), 'module.exports=1');
  require(path.join(pkg, 'index.js'));
  assert.ok(c.collect().some((s) => s.name === 'cached'));
  fs.rmSync(path.join(pkg, 'package.json'));
  assert.ok(c.collect().some((s) => s.name === 'cached'));
});
