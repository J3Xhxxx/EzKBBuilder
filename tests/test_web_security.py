from __future__ import annotations

import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch

from knowledge_pipeline.web.server import Handler, WorkbenchServer


class BrowserBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Mock()
        self.app.bootstrap.return_value = {"provider": "mock"}
        self.app.start_run.return_value = {"task_id": "demo", "status": "QUEUED"}
        self.server = WorkbenchServer(("127.0.0.1", 0), self.app)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.log = patch.object(Handler, "log_message")
        self.log.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.log.stop()

    def request(self, method: str, path: str, *, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body="{}" if method == "POST" else None, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_same_origin_browser_and_originless_json_client_work(self) -> None:
        self.assertEqual(200, self.request("GET", "/api/bootstrap")[0])
        for headers in ({"Content-Type": "application/json"}, {
            "Content-Type": "application/json; charset=utf-8",
            "Origin": self.origin, "Sec-Fetch-Site": "same-origin",
        }):
            self.assertEqual(202, self.request("POST", "/api/run", headers=headers)[0])
        self.assertEqual(2, self.app.start_run.call_count)

    def test_cross_origin_requests_cannot_trigger_actions(self) -> None:
        for origin in ("https://attacker.example", "null", f"http://127.0.0.1:{self.port + 1}"):
            with self.subTest(origin=origin):
                status, _ = self.request("POST", "/api/run", headers={
                    "Content-Type": "application/json", "Origin": origin,
                })
                self.assertEqual(403, status)
        self.assertEqual(403, self.request("POST", "/api/run", headers={
            "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site",
        })[0])
        self.app.start_run.assert_not_called()

    def test_browser_simple_content_types_are_rejected(self) -> None:
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(content_type=content_type):
                self.assertEqual(415, self.request("POST", "/api/run", headers={"Content-Type": content_type})[0])
        self.app.start_run.assert_not_called()

    def test_rebinding_and_malformed_host_cannot_read_workspace(self) -> None:
        for host in (f"attacker.example:{self.port}", "127.0.0.1:bad", f"user@127.0.0.1:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(403, self.request("GET", "/api/bootstrap", headers={"Host": host})[0])
        self.app.bootstrap.assert_not_called()
        self.assertEqual(200, self.request("GET", "/api/bootstrap", headers={"Host": f"localhost:{self.port}"})[0])


if __name__ == "__main__":
    unittest.main()
