"""Local document workspace with server-owned sessions and page permissions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import scrypt, sha256
from html import escape
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4

from geniusnew.contracts import ContractError

STATIC = Path(__file__).with_name("static")
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_REQUEST_BYTES = 300_000
MAX_HTML_BYTES = 200_000
EMAIL_RE = re.compile(r"\A[^\s@]+@[^\s@]+\.[^\s@]+\Z")
PAGE_PATH = re.compile(r"\A/api/pages/([0-9a-f]{32})\Z")
SHARES_PATH = re.compile(r"\A/api/pages/([0-9a-f]{32})/shares\Z")
SHARE_PATH = re.compile(r"\A/api/pages/([0-9a-f]{32})/shares/([0-9a-f]{32})\Z")
ALLOWED_TAGS = frozenset({"p", "div", "br", "h1", "h2", "h3", "strong", "b", "em", "i",
                          "u", "ul", "ol", "li", "blockquote", "code", "pre", "a"})


class AppError(ContractError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _fail(message: str, status: int = 400) -> None:
    raise AppError(message, status)


class SafeHTML(HTMLParser):
    """Keep formatting while discarding scripts, event handlers and unsafe URLs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "svg", "math"}:
            self.blocked += 1
            return
        if self.blocked or tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href and _safe_href(href):
                self.parts.append(f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer">')
            else:
                self.parts.append("<a>")
        else:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "svg", "math"}:
            self.blocked = max(0, self.blocked - 1)
            return
        if not self.blocked and tag in ALLOWED_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))


def _safe_href(value: str) -> bool:
    value = value.strip()
    if any(ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https", "mailto"} and bool(parsed.path or parsed.netloc)


def sanitize_html(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_HTML_BYTES:
        _fail("Page content is too large")
    parser = SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _email(value: str) -> str:
    if not isinstance(value, str):
        _fail("Enter a valid email address")
    normalized = value.strip().casefold()
    if len(normalized) > 254 or not EMAIL_RE.fullmatch(normalized):
        _fail("Enter a valid email address")
    return normalized


def _password(value: str) -> bytes:
    if not isinstance(value, str) or not 8 <= len(value) <= 256:
        _fail("Password must be 8 to 256 characters")
    return value.encode("utf-8")


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _csrf_for(token: str) -> str:
    return hmac.new(token.encode("ascii"), b"folio-csrf-v1", sha256).hexdigest()


def _password_hash(password: bytes, salt: bytes) -> bytes:
    return scrypt(password, salt=salt, n=2**14, r=8, p=1, dklen=32)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE app_meta (version INTEGER NOT NULL CHECK (version = 1));
INSERT INTO app_meta VALUES (1);
CREATE TABLE users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
  password_hash BLOB NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE sessions (
  token_digest TEXT PRIMARY KEY, csrf_digest TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL
);
CREATE TABLE pages (
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, body_html TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE shares (
  page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('viewer', 'editor')),
  PRIMARY KEY (page_id, user_id)
);
CREATE INDEX shares_user_idx ON shares(user_id);
"""


def initialize_database(path: Path) -> None:
    path = path.expanduser().resolve()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _fail("Database already exists")
    os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    except BaseException:
        connection.close()
        path.unlink(missing_ok=True)
        raise
    connection.close()


class Store:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        if not self.path.is_file():
            _fail("Document database is missing", 503)
        if self.path.stat().st_mode & 0o077:
            _fail("Document database permissions are too broad", 503)
        with self.connection() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                _fail("Document database failed integrity check", 503)
            if [row[0] for row in db.execute("SELECT version FROM app_meta")] != [1]:
                _fail("Unknown document database schema", 503)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(f"file:{self.path}?mode=rw", uri=True, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.row_factory = sqlite3.Row
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def signup(self, email: str, password: str):
        email = _email(email)
        password_bytes = _password(password)
        salt = secrets.token_bytes(16)
        user_id = uuid4().hex
        with self.connection() as db:
            try:
                db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                           (user_id, email, salt, _password_hash(password_bytes, salt), int(time.time())))
            except sqlite3.IntegrityError:
                _fail("An account with that email already exists", 409)
        return self._new_session(user_id, email)

    def login(self, email: str, password: str):
        email = _email(email)
        password_bytes = _password(password)
        with self.connection() as db:
            row = db.execute("SELECT id, salt, password_hash FROM users WHERE email=?", (email,)).fetchone()
        salt = row["salt"] if row else b"\0" * 16
        valid = hmac.compare_digest(_password_hash(password_bytes, salt),
                                    row["password_hash"] if row else b"\0" * 32)
        if not valid or row is None:
            _fail("Email or password is incorrect", 401)
        return self._new_session(row["id"], email)

    def _new_session(self, user_id: str, email: str):
        token = secrets.token_urlsafe(32)
        csrf = _csrf_for(token)
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (_digest(token), _digest(csrf), user_id, int(time.time()) + SESSION_SECONDS))
        return {"user": {"id": user_id, "email": email}, "csrf": csrf}, token

    def session(self, token: str | None):
        if not token or len(token) > 100:
            _fail("Log in to continue", 401)
        with self.connection() as db:
            row = db.execute("SELECT users.id, users.email, sessions.csrf_digest "
                             "FROM sessions JOIN users ON users.id=sessions.user_id "
                             "WHERE sessions.token_digest=? AND sessions.expires_at>?",
                             (_digest(token), int(time.time()))).fetchone()
        if row is None:
            _fail("Log in to continue", 401)
        return dict(row)

    def csrf(self, token: str | None, presented: str | None):
        user = self.session(token)
        if not presented or len(presented) > 100 or not hmac.compare_digest(
                user["csrf_digest"], _digest(presented)):
            _fail("Invalid request token", 403)
        return user

    def logout(self, token: str):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_digest=?", (_digest(token),))

    def list_pages(self, user_id: str):
        with self.connection() as db:
            rows = db.execute("SELECT p.id, p.title, p.updated_at, "
                              "CASE WHEN p.owner_id=? THEN 'owner' ELSE s.role END AS role, "
                              "EXISTS(SELECT 1 FROM shares x WHERE x.page_id=p.id) AS shared "
                              "FROM pages p LEFT JOIN shares s ON s.page_id=p.id AND s.user_id=? "
                              "WHERE p.owner_id=? OR s.user_id=? ORDER BY p.updated_at DESC",
                              (user_id, user_id, user_id, user_id)).fetchall()
        return [dict(row) for row in rows]

    def _page(self, db, page_id: str, user_id: str):
        row = db.execute("SELECT p.*, u.email AS owner_email, "
                         "CASE WHEN p.owner_id=? THEN 'owner' ELSE s.role END AS role "
                         "FROM pages p JOIN users u ON u.id=p.owner_id "
                         "LEFT JOIN shares s ON s.page_id=p.id AND s.user_id=? "
                         "WHERE p.id=? AND (p.owner_id=? OR s.user_id=?)",
                         (user_id, user_id, page_id, user_id, user_id)).fetchone()
        if row is None:
            _fail("Page not found", 404)
        return dict(row)

    def page(self, page_id: str, user_id: str):
        with self.connection() as db:
            return self._page(db, page_id, user_id)

    def create_page(self, user_id: str, title: str, body_html: str):
        title, body_html = self._content(title, body_html)
        page_id, now = uuid4().hex, int(time.time())
        with self.connection() as db:
            db.execute("INSERT INTO pages VALUES (?, ?, ?, ?, 1, ?, ?)",
                       (page_id, user_id, title, body_html, now, now))
            return self._page(db, page_id, user_id)

    def update_page(self, page_id: str, user_id: str, title: str, body_html: str, version: int):
        title, body_html = self._content(title, body_html)
        if type(version) is not int or version < 1:
            _fail("Page version is required")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            page = self._page(db, page_id, user_id)
            if page["role"] == "viewer":
                _fail("You cannot edit this page", 403)
            changed = db.execute("UPDATE pages SET title=?, body_html=?, version=version+1, "
                                 "updated_at=? WHERE id=? AND version=?",
                                 (title, body_html, int(time.time()), page_id, version))
            if changed.rowcount != 1:
                _fail("This page changed elsewhere. Reload before saving.", 409)
            return self._page(db, page_id, user_id)

    @staticmethod
    def _content(title, body_html):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
            _fail("Page title must be 1 to 160 characters")
        return title.strip(), sanitize_html(body_html)

    def list_shares(self, page_id: str, user_id: str):
        with self.connection() as db:
            page = self._page(db, page_id, user_id)
            if page["role"] != "owner":
                _fail("Only the owner can manage sharing", 403)
            return [dict(row) for row in db.execute(
                "SELECT users.id AS user_id, users.email, shares.role "
                "FROM shares JOIN users ON users.id=shares.user_id "
                "WHERE shares.page_id=? ORDER BY users.email", (page_id,)).fetchall()]

    def share(self, page_id: str, owner_id: str, email: str, role: str):
        email = _email(email)
        if role not in {"viewer", "editor"}:
            _fail("Permission must be viewer or editor")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            page = self._page(db, page_id, owner_id)
            if page["role"] != "owner":
                _fail("Only the owner can manage sharing", 403)
            target = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if target is None:
                _fail("That person needs a Folio account first", 404)
            if target["id"] == owner_id:
                _fail("You already own this page")
            db.execute("INSERT INTO shares VALUES (?, ?, ?) ON CONFLICT(page_id, user_id) "
                       "DO UPDATE SET role=excluded.role", (page_id, target["id"], role))

    def unshare(self, page_id: str, owner_id: str, target_id: str):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            page = self._page(db, page_id, owner_id)
            if page["role"] != "owner":
                _fail("Only the owner can manage sharing", 403)
            changed = db.execute("DELETE FROM shares WHERE page_id=? AND user_id=?",
                                 (page_id, target_id))
            if changed.rowcount != 1:
                _fail("Share not found", 404)


class Handler(BaseHTTPRequestHandler):
    store: Store
    server_version = "Folio/0.1"

    def log_message(self, format, *args):
        # Paths can carry user input; the local server omits request logs.
        pass

    def _send(self, status, data, *, cookie=None):
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def _cookie(self):
        parsed = SimpleCookie()
        try:
            parsed.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        return parsed["folio_session"].value if "folio_session" in parsed else None

    def _body(self, expected):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            _fail("JSON content type required", 415)
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            _fail("Content length required", 411)
        if size < 0 or size > MAX_REQUEST_BYTES:
            _fail("Request is too large", 413)
        try:
            data = json.loads(self.rfile.read(size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("Invalid JSON")
        if not isinstance(data, dict) or set(data) != expected:
            _fail("Unexpected request fields")
        return data

    def _dispatch(self):
        path = urlsplit(self.path).path
        method = self.command
        if path.startswith("/static/") or path == "/":
            if method != "GET":
                _fail("Method not allowed", 405)
            return self._static(path)
        if not path.startswith("/api/"):
            _fail("Not found", 404)
        if path in {"/api/signup", "/api/login"} and method == "POST":
            data = self._body({"email", "password"})
            result, token = (self.store.signup if path.endswith("signup") else self.store.login)(
                data["email"], data["password"])
            return self._send(200, result, cookie=f"folio_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}")
        token = self._cookie()
        user = self.store.session(token)
        if path == "/api/me" and method == "GET":
            csrf = _csrf_for(token)
            return self._send(200, {"user": {"id": user["id"], "email": user["email"]}, "csrf": csrf})
        if method not in {"GET", "HEAD"}:
            user = self.store.csrf(token, self.headers.get("X-CSRF-Token"))
        if path == "/api/logout" and method == "POST":
            self.store.logout(token)
            return self._send(200, {"ok": True}, cookie="folio_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        if path == "/api/pages" and method == "GET":
            return self._send(200, {"pages": self.store.list_pages(user["id"])})
        if path == "/api/pages" and method == "POST":
            data = self._body({"title", "body_html"})
            return self._send(201, {"page": self.store.create_page(user["id"], **data)})
        match = PAGE_PATH.fullmatch(path)
        if match and method == "GET":
            return self._send(200, {"page": self.store.page(match[1], user["id"])})
        if match and method == "PUT":
            data = self._body({"title", "body_html", "version"})
            return self._send(200, {"page": self.store.update_page(match[1], user["id"], **data)})
        match = SHARES_PATH.fullmatch(path)
        if match and method == "GET":
            return self._send(200, {"shares": self.store.list_shares(match[1], user["id"])})
        if match and method == "POST":
            data = self._body({"email", "role"})
            self.store.share(match[1], user["id"], **data)
            return self._send(200, {"ok": True})
        match = SHARE_PATH.fullmatch(path)
        if match and method == "DELETE":
            self.store.unshare(match[1], user["id"], match[2])
            return self._send(200, {"ok": True})
        _fail("Not found", 404)

    def _static(self, path):
        files = {"/": ("index.html", "text/html"), "/static/app.css": ("app.css", "text/css"),
                 "/static/app.js": ("app.js", "text/javascript")}
        if path not in files:
            _fail("Not found", 404)
        name, content_type = files[path]
        raw = (STATIC / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        try:
            self._dispatch()
        except AppError as error:
            self._send(error.status, {"error": str(error)})
        except (sqlite3.Error, OSError):
            self._send(503, {"error": "Document service is unavailable"})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()


def main():
    parser = argparse.ArgumentParser(description="Run the local Folio document workspace")
    parser.add_argument("command", choices={"init", "serve"})
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "init":
        initialize_database(args.database)
        print("Folio database initialized")
        return
    Handler.store = Store(args.database)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Folio is running at http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
