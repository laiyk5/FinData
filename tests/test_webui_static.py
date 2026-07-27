from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from findata.server import server as server_module
from findata.server.server import FindataServer, initialize_workspace


class WebUIStaticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        initialize_workspace(self.root)
        self.webui = self.root / "webui"
        (self.webui / "assets").mkdir(parents=True)
        (self.webui / "index.html").write_text("<html>findata</html>", encoding="utf-8")
        (self.webui / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (self.webui / "assets" / "app.css").write_text("body{}", encoding="utf-8")
        self.original_root = server_module.WEBUI_ROOT
        server_module.WEBUI_ROOT = self.webui
        self.addCleanup(self._restore_root)
        self.server = FindataServer(
            self.root,
            port=0,
            provider_mode="mock",
            today=date(2026, 7, 20),
        )
        self.server.start_background()

    def _restore_root(self) -> None:
        server_module.WEBUI_ROOT = self.original_root

    def tearDown(self) -> None:
        self.server.shutdown()
        self.tempdir.cleanup()

    def _get(self, path: str) -> tuple[bytes, object]:
        response = urlopen(f"{self.server.base_url}{path}", timeout=2)
        with response:
            return response.read(), response

    def test_index_served_without_token(self) -> None:
        body, response = self._get("/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"findata", body)
        self.assertEqual(response.headers.get_content_type(), "text/html")
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")

    def test_hashed_asset_served_with_immutable_cache(self) -> None:
        body, response = self._get("/assets/app.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"console.log(1)")
        self.assertEqual(response.headers.get_content_type(), "text/javascript")
        self.assertIn("immutable", response.headers.get("Cache-Control"))

    def test_css_content_type(self) -> None:
        _, response = self._get("/assets/app.css")
        self.assertEqual(response.headers.get_content_type(), "text/css")

    def test_client_side_route_falls_back_to_index(self) -> None:
        body, response = self._get("/tasks/abc123")
        self.assertEqual(response.status, 200)
        self.assertIn(b"findata", body)

    def test_path_traversal_never_serves_workspace_files(self) -> None:
        for path in ("/../token", "/%2e%2e/token", "/assets/../../token"):
            body, response = self._get(path)
            self.assertEqual(response.status, 200)
            self.assertIn(b"findata", body)
            token = (self.root / "token").read_text(encoding="utf-8").strip()
            self.assertNotIn(token.encode(), body)

    def test_v1_still_requires_token(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.base_url}/v1/system/status", timeout=2)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

    def test_v1_still_works_with_token(self) -> None:
        token = (self.root / "token").read_text(encoding="utf-8").strip()
        request = Request(
            f"{self.server.base_url}/v1/system/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)

    def test_post_to_static_path_is_not_found(self) -> None:
        request = Request(f"{self.server.base_url}/assets/app.js", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()


class WebUIMissingBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        initialize_workspace(self.root)
        self.original_root = server_module.WEBUI_ROOT
        server_module.WEBUI_ROOT = self.root / "webui-does-not-exist"
        self.addCleanup(self._restore_root)
        self.server = FindataServer(
            self.root,
            port=0,
            provider_mode="mock",
            today=date(2026, 7, 20),
        )
        self.server.start_background()

    def _restore_root(self) -> None:
        server_module.WEBUI_ROOT = self.original_root

    def tearDown(self) -> None:
        self.server.shutdown()
        self.tempdir.cleanup()

    def test_missing_build_reports_404_with_hint(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.base_url}/", timeout=2)
        self.assertEqual(caught.exception.code, 404)
        self.assertIn(b"npm run build", caught.exception.read())
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
