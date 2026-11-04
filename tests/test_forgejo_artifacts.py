import base64
import hashlib
import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.integrations import forgejo_artifacts


class UploadTest(unittest.TestCase):
    def test_upload_artifact(self):
        """Протокол v3: создать контейнер, PUT файлов, PATCH с итоговым размером — на локальном сервере."""
        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n)

            def _reply(self, obj):
                raw = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                calls.append(("POST", self.path, json.loads(self._body()), self.headers["Authorization"]))
                self._reply({"fileContainerResourceUrl": f"http://127.0.0.1:{port}/upload/1"})

            def do_PUT(self):
                body = self._body()
                md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
                if self.headers.get("x-actions-results-md5") != md5:     # как Forgejo: «md5 not match»
                    self.send_response(500)
                    self.end_headers()
                    return
                calls.append(("PUT", self.path, body, self.headers["Content-Range"]))
                self._reply({})

            def do_PATCH(self):
                calls.append(("PATCH", self.path, json.loads(self._body()), None))
                self._reply({})

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "summary.md").write_text("сводка", encoding="utf-8")
            (tmp / "junit.xml").write_text("<testsuite/>", encoding="utf-8")
            env = {"ACTIONS_RUNTIME_URL": f"http://127.0.0.1:{port}/", "ACTIONS_RUNTIME_TOKEN": "t",
                   "GITHUB_RUN_ID": "7"}
            n = forgejo_artifacts.upload("otk-gate", [tmp / "summary.md", tmp / "junit.xml", tmp / "нет.txt"], env,
                                         log=lambda m: None)
            self.assertEqual(n, 2)
            self.assertEqual(calls[0][0], "POST")
            self.assertIn("/_apis/pipelines/workflows/7/artifacts", calls[0][1])
            self.assertEqual(calls[0][2], {"Type": "actions_storage", "Name": "otk-gate"})
            self.assertEqual(calls[0][3], "Bearer t")
            puts = [c for c in calls if c[0] == "PUT"]
            self.assertEqual(len(puts), 2)
            self.assertIn("itemPath=otk-gate%2Fsummary.md", puts[0][1])
            self.assertEqual(puts[0][3], f"bytes 0-{len('сводка'.encode()) - 1}/{len('сводка'.encode())}")
            patch = calls[-1]
            self.assertEqual(patch[0], "PATCH")
            self.assertIn("artifactName=otk-gate", patch[1])
            self.assertEqual(patch[2], {"Size": len("сводка".encode()) + len(b"<testsuite/>")})
            self.assertEqual(forgejo_artifacts.upload("x", [tmp / "summary.md"], {}, log=lambda m: None), 0)
        finally:
            srv.shutdown()
            rmtree_force(tmp)


if __name__ == "__main__":
    unittest.main()
