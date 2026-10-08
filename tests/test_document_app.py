"""The local document app must enforce login, ownership and share roles."""

import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from document_app.app import AppError, Handler, Store, ThreadingHTTPServer, initialize_database, sanitize_html


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "folio.sqlite3"
        initialize_database(self.path)
        self.store = Store(self.path)
        self.owner, self.owner_token = self.store.signup("owner@example.com", "owner password")
        self.editor, self.editor_token = self.store.signup("editor@example.com", "editor password")
        self.viewer, self.viewer_token = self.store.signup("viewer@example.com", "viewer password")
        self.page = self.store.create_page(self.owner["user"]["id"], "Notes", "<p>Hello</p>")

    def tearDown(self):
        self.directory.cleanup()

    def test_login_and_session_refusals(self):
        result, token = self.store.login("OWNER@example.com", "owner password")
        self.assertEqual(result["user"]["email"], "owner@example.com")
        self.assertEqual(self.store.session(token)["id"], result["user"]["id"])
        with self.assertRaises(AppError):
            self.store.login("owner@example.com", "wrong password")
        with self.assertRaises(AppError):
            self.store.session("not a token")
        with self.assertRaises(AppError):
            self.store.csrf(token, "wrong csrf")
        self.store.logout(token)
        with self.assertRaises(AppError):
            self.store.session(token)

    def test_sharing_enforces_view_edit_owner_and_revocation(self):
        page_id = self.page["id"]
        owner_id = self.owner["user"]["id"]
        editor_id = self.editor["user"]["id"]
        viewer_id = self.viewer["user"]["id"]
        with self.assertRaises(AppError):
            self.store.page(page_id, viewer_id)
        self.store.share(page_id, owner_id, "viewer@example.com", "viewer")
        self.assertEqual(self.store.page(page_id, viewer_id)["role"], "viewer")
        with self.assertRaises(AppError):
            self.store.update_page(page_id, viewer_id, "Changed", "", 1)
        with self.assertRaises(AppError):
            self.store.share(page_id, viewer_id, "editor@example.com", "editor")
        with self.assertRaises(AppError):
            self.store.list_shares(page_id, viewer_id)
        with self.assertRaises(AppError):
            self.store.unshare(page_id, viewer_id, viewer_id)
        self.store.share(page_id, owner_id, "editor@example.com", "editor")
        updated = self.store.update_page(page_id, editor_id, "Updated", "<b>Text</b>", 1)
        self.assertEqual(updated["version"], 2)
        with self.assertRaises(AppError):
            self.store.update_page(page_id, owner_id, "Overwrite", "", 1)
        self.store.unshare(page_id, owner_id, viewer_id)
        with self.assertRaises(AppError):
            self.store.page(page_id, viewer_id)
        with self.assertRaises(AppError):
            self.store.unshare(page_id, owner_id, viewer_id)

    def test_share_requires_registered_user_and_valid_role(self):
        owner_id = self.owner["user"]["id"]
        with self.assertRaises(AppError):
            self.store.share(self.page["id"], owner_id, "absent@example.com", "viewer")
        with self.assertRaises(AppError):
            self.store.share(self.page["id"], owner_id, "editor@example.com", "owner")
        with self.assertRaises(AppError):
            self.store.share(self.page["id"], owner_id, "owner@example.com", "editor")

    def test_rich_text_is_sanitized(self):
        body = '<p onclick="bad()">Hi <strong>team</strong><script>alert(1)</script>' \
               '<a href="javascript:alert(1)">bad</a><a href="https://example.com">good</a></p>'
        clean = sanitize_html(body)
        self.assertIn("<strong>team</strong>", clean)
        self.assertIn('href="https://example.com"', clean)
        for unsafe in ("onclick", "script", "alert(1)", "javascript:"):
            self.assertNotIn(unsafe, clean)
        saved = self.store.update_page(self.page["id"], self.owner["user"]["id"],
                                       "Safe", body, 1)
        self.assertEqual(saved["body_html"], clean)

    def test_missing_database_fails_closed(self):
        with self.assertRaises(AppError):
            Store(Path(self.directory.name) / "missing.sqlite3")

    def test_database_requires_private_permissions_and_explicit_init(self):
        with self.assertRaises(AppError):
            initialize_database(self.path)
        self.path.chmod(0o644)
        with self.assertRaises(AppError):
            Store(self.path)

    def test_bad_document_inputs_are_refused(self):
        owner_id = self.owner["user"]["id"]
        with self.assertRaises(AppError):
            self.store.create_page(owner_id, "", "")
        with self.assertRaises(AppError):
            self.store.create_page(owner_id, "Valid", "x" * 200001)
        with self.assertRaises(AppError):
            self.store.update_page(self.page["id"], owner_id, "Valid", "", 0)
        with self.assertRaises(AppError):
            self.store.update_page(self.page["id"], owner_id, "Valid", "", True)
        with self.assertRaises(AppError):
            self.store.signup("bad email", "some password")
        with self.assertRaises(AppError):
            self.store.signup(None, "some password")
        with self.assertRaises(AppError):
            self.store.signup("new@example.com", "short")

    def test_unknown_schema_fails_closed(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP TABLE app_meta")
            db.execute("CREATE TABLE app_meta (version INTEGER NOT NULL)")
            db.execute("INSERT INTO app_meta VALUES (2)")
            db.commit()
        finally:
            db.close()
        with self.assertRaises(AppError):
            Store(self.path)

    def test_integrity_check_fails_closed(self):
        class CorruptDatabase:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, statement):
                return iter([("corrupt",)]) if "quick_check" in statement else iter([(1,)])

        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return next(self.rows)

            def __iter__(self):
                return self.rows

        class CorruptConnection(CorruptDatabase):
            def execute(self, statement):
                return Result(super().execute(statement))

        with patch.object(Store, "connection", return_value=CorruptConnection()):
            with self.assertRaises(AppError):
                Store(self.path)


class DocumentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "folio.sqlite3"
        initialize_database(path)
        Handler.store = Store(path)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, *, cookie=None, csrf=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, json.loads(data), response.getheader("Set-Cookie"))
        conn.close()
        return result

    def test_login_csrf_and_page_sharing_over_http(self):
        status, owner, owner_cookie = self.request("POST", "/api/signup", {
            "email": "owner@example.com", "password": "owner password"})
        self.assertEqual(status, 200)
        owner_cookie = owner_cookie.split(";", 1)[0]
        self.assertIn("folio_session=", owner_cookie)
        status, other, other_cookie = self.request("POST", "/api/signup", {
            "email": "other@example.com", "password": "other password"})
        self.assertEqual(status, 200)
        other_cookie = other_cookie.split(";", 1)[0]
        self.assertEqual(self.request("GET", "/api/pages")[0], 401)
        self.assertEqual(self.request("POST", "/api/pages", {
            "title": "Private", "body_html": ""}, cookie=owner_cookie)[0], 403)
        status, created, _ = self.request("POST", "/api/pages", {
            "title": "Private", "body_html": "<p>Secret</p>"},
            cookie=owner_cookie, csrf=owner["csrf"])
        self.assertEqual(status, 201)
        page_id = created["page"]["id"]
        self.assertEqual(self.request("GET", f"/api/pages/{page_id}", cookie=other_cookie)[0], 404)
        self.assertEqual(self.request("POST", f"/api/pages/{page_id}/shares", {
            "email": "other@example.com", "role": "viewer"},
            cookie=owner_cookie, csrf=owner["csrf"])[0], 200)
        self.assertEqual(self.request("GET", f"/api/pages/{page_id}", cookie=other_cookie)[0], 200)
        self.assertEqual(self.request("PUT", f"/api/pages/{page_id}", {
            "title": "Stolen", "body_html": "", "version": 1},
            cookie=other_cookie, csrf=other["csrf"])[0], 403)
        self.assertEqual(self.request("POST", f"/api/pages/{page_id}/shares", {
            "email": "owner@example.com", "role": "editor"},
            cookie=other_cookie, csrf=other["csrf"])[0], 403)
        self.assertEqual(self.request("DELETE", f"/api/pages/{page_id}/shares/{other['user']['id']}",
                                      cookie=owner_cookie, csrf=owner["csrf"])[0], 200)
        self.assertEqual(self.request("GET", f"/api/pages/{page_id}", cookie=other_cookie)[0], 404)

    def test_http_rejects_bad_shape_media_size_and_routes(self):
        status, owner, cookie = self.request("POST", "/api/signup", {
            "email": "shape@example.com", "password": "shape password"})
        self.assertEqual(status, 200)
        cookie = cookie.split(";", 1)[0]
        self.assertEqual(self.request("POST", "/api/pages", {
            "title": "Bad", "body_html": "", "owner_id": owner["user"]["id"]},
            cookie=cookie, csrf=owner["csrf"])[0], 400)
        self.assertEqual(self.request("GET", "/unrelated")[0], 404)
        self.assertEqual(self.request("GET", "/static/unknown.js")[0], 404)

        def raw(method, path, body, headers):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            status = response.status
            response.read()
            conn.close()
            return status

        self.assertEqual(raw("POST", "/", b"", {}), 405)
        self.assertEqual(raw("POST", "/api/login", json.dumps({
            "email": "shape@example.com", "password": "shape password"}).encode(),
            {"Content-Type": "text/plain"}), 415)
        oversized = json.dumps({"email": "shape@example.com", "password": "shape password"})
        oversized += " " * (300001 - len(oversized))
        self.assertEqual(raw("POST", "/api/login", oversized.encode(),
                             {"Content-Type": "application/json"}), 413)


if __name__ == "__main__":
    unittest.main()
