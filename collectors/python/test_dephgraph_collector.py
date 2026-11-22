import http.server
import json
import os
import sys
import types
import threading
import unittest
from unittest import mock

import dephgraph_collector as dc


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.captured.append((self.path, self.headers["Authorization"], body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.captured = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        dc._warned = False

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_collect_maps_modules_to_dists(self):
        mods = {"fakeone": types.ModuleType("fakeone"), "fakeone.sub": types.ModuleType("fakeone.sub"),
                "nsns": types.ModuleType("nsns"), "nodist": types.ModuleType("nodist")}
        pd = {"fakeone": ["one-dist"], "nsns": ["ns-a", "ns-b"]}  # nsns is ambiguous, no file: skipped
        with mock.patch.dict(sys.modules, mods), mock.patch.object(dc.metadata, "packages_distributions", return_value=pd), \
                mock.patch.object(dc.metadata, "version", return_value="1.2"):
            samples = [s for s in dc.collect() if s["name"] in ("one-dist", "ns-a", "ns-b")]
        self.assertEqual(samples, [{"name": "one-dist", "version": "1.2", "exec_count": 1}])

    def test_namespace_package_resolved_by_file(self):
        mod = types.ModuleType("nsns")
        mod.__file__ = "/site/nsns/x.py"

        class Dist:
            def __init__(self, files):
                self.files = files

            def locate_file(self, f):
                return "/site/" + f

        dists = {"ns-a": Dist(["nsns/x.py"]), "ns-b": Dist(["nsns/y.py"])}
        with mock.patch.dict(sys.modules, {"nsns": mod}), \
                mock.patch.object(dc.metadata, "packages_distributions", return_value={"nsns": ["ns-a", "ns-b"]}), \
                mock.patch.object(dc.metadata, "distribution", side_effect=dists.get), \
                mock.patch.object(dc.metadata, "version", return_value="3"):
            names = [s["name"] for s in dc.collect()]
        self.assertIn("ns-a", names)
        self.assertNotIn("ns-b", names)

    def test_interval_validation(self):
        for raw, want in [("120", 120.0), ("0", 3600.0), ("-5", 3600.0), ("nan", 3600.0), ("inf", 3600.0),
                          ("x", 3600.0), ("59", 3600.0), ("999999999", 3600.0)]:
            with mock.patch.dict(os.environ, {"DEPHGRAPH_INTERVAL": raw}):
                self.assertEqual(dc._interval(), want, raw)

    def test_send_payload_and_header(self):
        dc.send(self.url + "/", "dgs_tok", 7, [{"name": "a", "version": "1", "exec_count": 1}])
        path, auth, body = self.srv.captured[0]
        self.assertEqual(path, "/runtime/samples")
        self.assertEqual(auth, "Bearer dgs_tok")
        self.assertEqual(body, {"service_id": 7, "samples": [{"name": "a", "version": "1", "exec_count": 1}]})

    def test_batches_within_limit(self):
        samples = [{"name": f"p{i}", "version": "1", "exec_count": 1} for i in range(dc.MAX_SAMPLES + 1)]
        dc.send(self.url, "t", 1, samples)
        self.assertEqual([len(c[2]["samples"]) for c in self.srv.captured], [dc.MAX_SAMPLES, 1])

    def test_flush_swallows_errors_with_one_warning(self):
        env = {"DEPHGRAPH_URL": "http://127.0.0.1:1", "DEPHGRAPH_TOKEN": "t", "DEPHGRAPH_SERVICE_ID": "1"}
        sample = [{"name": "a", "version": "1", "exec_count": 1}]
        with mock.patch.dict(os.environ, env), mock.patch.object(dc, "collect", return_value=sample), self.assertLogs("dephgraph", "WARNING") as cm:
            dc.flush()
            dc.flush()
        self.assertEqual(len(cm.records), 1)

    def test_token_never_logged(self):
        env = {"DEPHGRAPH_URL": self.url, "DEPHGRAPH_TOKEN": "dgs_SECRET", "DEPHGRAPH_SERVICE_ID": "1"}
        with mock.patch.dict(os.environ, env), mock.patch.object(dc, "send", side_effect=RuntimeError("Bearer dgs_SECRET")):
            with self.assertLogs("dephgraph", "WARNING") as cm:
                dc.flush()
        self.assertNotIn("dgs_SECRET", cm.output[0])

    def test_token_whitespace_stripped(self):
        dc.send(self.url, " dgs_tok\n", 1, [{"name": "a", "version": "1", "exec_count": 1}])
        self.assertEqual(self.srv.captured[0][1], "Bearer dgs_tok")

    def test_start_noop_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(dc.start())


if __name__ == "__main__":
    unittest.main()
