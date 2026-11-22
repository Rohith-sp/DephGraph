# Runtime collectors

Tell DephGraph which packages actually load in production. Zero dependencies; a collector never raises into your app (2 s timeout, one warning on failure).

1. **Create a service token** in DephGraph (service settings, `dgs_...`) and note the service id.
2. **Set env vars** on the app:
   `DEPHGRAPH_URL` (API base URL), `DEPHGRAPH_TOKEN`, `DEPHGRAPH_SERVICE_ID`, optional `DEPHGRAPH_INTERVAL` (seconds, default 3600).
3. **Enable with one line**:
   - Python: copy `python/dephgraph_collector.py` next to your app and add `import dephgraph_collector` at the top of your entry point.
   - Node: copy `node/dephgraph-collector.cjs` and run `node -r ./dephgraph-collector.cjs app.js`.

A snapshot of loaded packages is sent about a minute after start, then every interval, and once at exit. Python needs 3.10+. Node limits: only CommonJS modules are seen, and `process.exit()` or a SIGTERM/SIGINT kill skips the exit flush (the early and periodic sends still report).
