"""DephGraph runtime collector (stdlib only, Python 3.10+). Enable with: import dephgraph_collector

Env: DEPHGRAPH_URL, DEPHGRAPH_TOKEN, DEPHGRAPH_SERVICE_ID, optional DEPHGRAPH_INTERVAL
(seconds, 60..604800, default 3600). Never raises into the host app: every failure is
swallowed after one warning that never contains the token.
"""

import atexit
import json
import logging
import math
import os
import sys
import threading
import urllib.request
from importlib import metadata

log = logging.getLogger("dephgraph")
MAX_SAMPLES = 5000  # API limit per request
TIMEOUT = 2
EARLY_SEND = 60  # first report soon after start so short-lived or restarted processes still count
_warned = False


def _warn(msg):
    global _warned
    if not _warned:
        _warned = True
        log.warning("dephgraph collector: %s (further errors suppressed)", msg)


def _describe(exc):
    # Type plus HTTP status or network reason only: str(exc) could carry request details.
    detail = getattr(exc, "code", None) or getattr(exc, "reason", None)
    return type(exc).__name__ + (f" ({detail})" if detail else "")


def _interval():
    try:
        s = float(os.environ.get("DEPHGRAPH_INTERVAL", 3600))
    except ValueError:
        return 3600.0
    return s if math.isfinite(s) and 60 <= s <= 7 * 86400 else 3600.0


def _owner(candidates, mod_files):
    """Namespace packages (google, azure, ...) map to many dists: pick the one that ships the file."""
    owners = set()
    for name in candidates:
        try:
            dist = metadata.distribution(name)
            shipped = {os.path.abspath(str(dist.locate_file(f))) for f in dist.files or ()}
        except Exception:  # noqa: BLE001
            continue
        if shipped & mod_files:
            owners.add(name)
    return owners.pop() if len(owners) == 1 else None


def collect():
    """Distributions behind currently imported modules, as API samples."""
    dists = metadata.packages_distributions()
    files_by_top = {}
    for name, mod in list(sys.modules.items()):
        files = files_by_top.setdefault(name.partition(".")[0], set())
        f = getattr(mod, "__file__", None)
        if f:
            files.add(os.path.abspath(f))
    seen = {}
    for top, mod_files in files_by_top.items():
        cands = dists.get(top, [])
        dist = cands[0] if len(cands) == 1 else _owner(cands, mod_files) if cands else None
        if dist and dist not in seen:
            try:
                seen[dist] = metadata.version(dist)
            except Exception:  # noqa: BLE001 - one broken dist must not drop the rest
                continue
    return [{"name": n, "version": v, "exec_count": 1} for n, v in sorted(seen.items())]


def send(url, token, service_id, samples=None):
    samples = collect() if samples is None else samples
    for i in range(0, len(samples), MAX_SAMPLES):
        body = json.dumps({"service_id": service_id, "samples": samples[i : i + MAX_SAMPLES]}).encode()
        req = urllib.request.Request(
            url.rstrip("/") + "/runtime/samples",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token.strip()}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT).close()


def flush():
    try:
        e = os.environ
        send(e["DEPHGRAPH_URL"], e["DEPHGRAPH_TOKEN"], int(e["DEPHGRAPH_SERVICE_ID"]))
    except Exception as exc:  # noqa: BLE001 - never break the host app
        _warn(_describe(exc))


def _flush_at_exit():
    # urlopen's timeout is per socket operation (DNS is not covered): bound the whole flush.
    t = threading.Thread(target=flush, daemon=True)
    t.start()
    t.join(3)


def _spawn(interval):
    stop = threading.Event()

    def loop():
        delay = EARLY_SEND
        while not stop.wait(delay):
            flush()
            delay = interval

    threading.Thread(target=loop, name="dephgraph-collector", daemon=True).start()
    return stop


def start():
    """Start the daemon timer and the exit flush. No-op unless all three env vars are set."""
    if not all(os.environ.get(k) for k in ("DEPHGRAPH_URL", "DEPHGRAPH_TOKEN", "DEPHGRAPH_SERVICE_ID")):
        return None
    if sys.version_info < (3, 10):
        _warn("Python 3.10+ is required (importlib.metadata.packages_distributions); collector disabled")
        return None
    interval = _interval()
    stop = _spawn(interval)
    atexit.register(_flush_at_exit)
    # Threads do not survive fork (gunicorn --preload): each worker gets its own timer.
    os.register_at_fork(after_in_child=lambda: _spawn(interval))
    return stop


start()
