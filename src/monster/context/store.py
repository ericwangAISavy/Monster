"""Context store: permanent projects and temporary per-task bundles in one SQLite file.

- A **project** is permanent context. People create it in the control center and add files
  or text; it stays until they delete it.
- A **bundle** is temporary context that arrives with one command (over MCP, from Claude
  Code or ChatGPT): text, attached files and images. It is deleted when that task
  succeeds, kept for a retry when it fails or stops, and expires after a week.

Every document has extracted text (when the format allows), a short summary and a
full-text index, so later steps can find and fetch the right context.
"""

import io
import json
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

SCHEMA_VERSION = 1
BUNDLE_TTL_S = 7 * 24 * 3600
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
CHUNK_CHARS = 800
SUMMARY_CHARS = 280
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".xml", ".html",
                 ".htm", ".yaml", ".yml", ".log", ".ini", ".py", ".js", ".ts", ".sql"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class ContextError(ValueError):
    """A context request that cannot be honoured; the message is safe to show."""


def _now():
    return time.time()


def _new_id():
    return uuid.uuid4().hex[:16]


def _safe_name(name):
    name = re.sub(r"[^\w.\- ]+", "_", (name or "file").strip())[:80].strip(" .")
    return name or "file"


def _docx_text(data):
    """Paragraph text of a .docx (a ZIP of XML) using only the standard library."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in ElementTree.fromstring(xml).iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _pdf_text(data):
    try:
        import pypdf
    except ImportError:
        return None  # The file is still stored and usable for uploads.
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(name, data):
    """(kind, text) for a file: 'image' with no text, or 'file' with text or None."""
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image", None
    try:
        if suffix == ".docx":
            return "file", _docx_text(data)
        if suffix == ".pdf":
            return "file", _pdf_text(data)
        if suffix in TEXT_SUFFIXES or not suffix:
            return "file", data.decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError, OSError):
        return "file", None
    return "file", None


def summarize(name, kind, text):
    """A short, local summary (no model call): the first meaningful lines of the text."""
    if kind == "image":
        return f"Image: {name}"
    if not text or not text.strip():
        return f"{name} (no readable text)"
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:SUMMARY_CHARS] + ("…" if len(flat) > SUMMARY_CHARS else "")


def chunks_of(text):
    text = (text or "").strip()
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)] if text else []


class ContextStore:
    """Thread-safe owner of the context database and its files."""

    def __init__(self, root, clock=_now):
        self.root = Path(root)
        self.files = self.root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "context.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ---- schema --------------------------------------------------------------
    def _migrate(self):
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.db.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self.db.executescript("""
                    CREATE TABLE projects (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL, updated_at REAL NOT NULL);
                    CREATE TABLE bundles (
                        id TEXT PRIMARY KEY, run_ref TEXT, status TEXT NOT NULL,
                        created_at REAL NOT NULL, expires_at REAL NOT NULL);
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY,
                        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                        bundle_id TEXT REFERENCES bundles(id) ON DELETE CASCADE,
                        name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT, size INTEGER NOT NULL,
                        text TEXT, summary TEXT NOT NULL,
                        entities_json TEXT NOT NULL DEFAULT '[]', purpose TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        CHECK ((project_id IS NULL) <> (bundle_id IS NULL)));
                    CREATE INDEX documents_project ON documents(project_id);
                    CREATE INDEX documents_bundle ON documents(bundle_id);
                    CREATE VIRTUAL TABLE chunks USING fts5(text, document_id UNINDEXED, seq UNINDEXED);
                """)
                self.db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise ContextError("The context database is from a newer Monster version.")

    # ---- projects (permanent) -----------------------------------------------
    def create_project(self, name, instructions=""):
        name = (name or "").strip()
        if not name:
            raise ContextError("Give the project a name.")
        project_id, now = _new_id(), self.clock()
        with self.lock, self.db:
            self.db.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
                            (project_id, name[:120], (instructions or "")[:20000], now, now))
        return project_id

    def update_project(self, project_id, *, name=None, instructions=None):
        with self.lock, self.db:
            row = self._project(project_id)
            self.db.execute(
                "UPDATE projects SET name = ?, instructions = ?, updated_at = ? WHERE id = ?",
                ((name or row["name"]).strip()[:120],
                 row["instructions"] if instructions is None else instructions[:20000],
                 self.clock(), project_id))

    def delete_project(self, project_id):
        with self.lock, self.db:
            self._project(project_id)
            paths = [r["path"] for r in self.db.execute(
                "SELECT path FROM documents WHERE project_id = ?", (project_id,))]
            self.db.execute("DELETE FROM chunks WHERE document_id IN "
                            "(SELECT id FROM documents WHERE project_id = ?)", (project_id,))
            self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._remove_files(paths)

    def list_projects(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id) AS documents "
                "FROM projects p ORDER BY p.updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def project(self, project_id):
        with self.lock:
            return dict(self._project(project_id))

    def _project(self, project_id):
        row = self.db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ContextError("Unknown project.")
        return row

    def add_file(self, project_id, name, data):
        with self.lock:
            self._project(project_id)
        return self._add_document(name, data, project_id=project_id)

    def add_text(self, project_id, name, text):
        with self.lock:
            self._project(project_id)
        return self._add_document(name or "Notes.txt", (text or "").encode("utf-8"),
                                  project_id=project_id, force_text=True)

    # ---- bundles (temporary, one task) ---------------------------------------
    def create_bundle(self, *, text=None, files=(), images=(), run_ref=None):
        """Store one command's temporary context. files/images: [(name, bytes)]."""
        size = len((text or "").encode("utf-8")) + sum(len(d) for _, d in (*files, *images))
        if size > MAX_BUNDLE_BYTES:
            raise ContextError(f"Temporary context is {size // 1024} KB; the limit is "
                               f"{MAX_BUNDLE_BYTES // (1024 * 1024)} MB per command.")
        if not (text and text.strip()) and not files and not images:
            return None
        bundle_id, now = _new_id(), self.clock()
        with self.lock, self.db:
            self.db.execute("INSERT INTO bundles VALUES (?, ?, 'active', ?, ?)",
                            (bundle_id, run_ref, now, now + BUNDLE_TTL_S))
        try:
            if text and text.strip():
                self._add_document("Context from the command.txt", text.encode("utf-8"),
                                   bundle_id=bundle_id, force_text=True)
            for name, data in files:
                self._add_document(name, data, bundle_id=bundle_id)
            for name, data in images:
                self._add_document(name, data, bundle_id=bundle_id, force_image=True)
        except Exception:
            self.delete_bundle(bundle_id)
            raise
        return bundle_id

    def attach_run(self, bundle_id, run_ref):
        with self.lock, self.db:
            self.db.execute("UPDATE bundles SET run_ref = ? WHERE id = ?", (run_ref, bundle_id))

    def finish_bundle(self, bundle_id, succeeded):
        """Success deletes the bundle; failure keeps it (for a retry) until it expires."""
        if succeeded:
            self.delete_bundle(bundle_id)
            return
        with self.lock, self.db:
            self.db.execute("UPDATE bundles SET status = 'kept' WHERE id = ?", (bundle_id,))

    def delete_bundle(self, bundle_id):
        with self.lock, self.db:
            paths = [r["path"] for r in self.db.execute(
                "SELECT path FROM documents WHERE bundle_id = ?", (bundle_id,))]
            self.db.execute("DELETE FROM chunks WHERE document_id IN "
                            "(SELECT id FROM documents WHERE bundle_id = ?)", (bundle_id,))
            self.db.execute("DELETE FROM bundles WHERE id = ?", (bundle_id,))
        self._remove_files(paths)

    def bundle(self, bundle_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
        return dict(row) if row else None

    def purge_expired(self):
        """Delete temporary bundles past their expiry; returns how many."""
        with self.lock:
            expired = [r["id"] for r in self.db.execute(
                "SELECT id FROM bundles WHERE expires_at < ?", (self.clock(),))]
        for bundle_id in expired:
            self.delete_bundle(bundle_id)
        return len(expired)

    # ---- documents -------------------------------------------------------------
    def _add_document(self, name, data, *, project_id=None, bundle_id=None,
                      force_text=False, force_image=False):
        if type(data) is not bytes:
            raise ContextError("File data must be bytes.")
        name = _safe_name(name)
        if force_text:
            kind, text = "text", data.decode("utf-8", "replace")
        elif force_image:
            kind, text = "image", None
        else:
            kind, text = extract_text(name, data)
        document_id = _new_id()
        path = self.files / f"{document_id}_{name}"
        path.write_bytes(data)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO documents (id, project_id, bundle_id, name, kind, path, size, text, "
                "summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (document_id, project_id, bundle_id, name, kind, str(path), len(data), text,
                 summarize(name, kind, text), self.clock()))
            for seq, chunk in enumerate(chunks_of(text)):
                self.db.execute("INSERT INTO chunks (text, document_id, seq) VALUES (?, ?, ?)",
                                (chunk, document_id, seq))
            if project_id:
                self.db.execute("UPDATE projects SET updated_at = ? WHERE id = ?",
                                (self.clock(), project_id))
        return document_id

    def delete_document(self, document_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT path FROM documents WHERE id = ?", (document_id,)).fetchone()
            if row is None:
                raise ContextError("Unknown document.")
            self.db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            self.db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self._remove_files([row["path"]])

    def documents(self, *, project_id=None, bundle_id=None):
        column, value = ("project_id", project_id) if project_id else ("bundle_id", bundle_id)
        with self.lock:
            rows = self.db.execute(
                f"SELECT id, project_id, bundle_id, name, kind, path, size, summary, created_at "
                f"FROM documents WHERE {column} = ? ORDER BY created_at", (value,)).fetchall()
        return [dict(r) for r in rows]

    def document_text(self, document_id):
        with self.lock:
            row = self.db.execute("SELECT text FROM documents WHERE id = ?", (document_id,)).fetchone()
        return row["text"] if row else None

    def search(self, query, *, project_ids=(), bundle_ids=(), limit=8):
        """Best-matching text snippets, limited to the given projects/bundles if any."""
        terms = re.findall(r"\w+", query or "")
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms[:12])
        scope, params = "", [match]
        if project_ids or bundle_ids:
            parts = []
            if project_ids:
                parts.append(f"d.project_id IN ({','.join('?' * len(project_ids))})")
                params += list(project_ids)
            if bundle_ids:
                parts.append(f"d.bundle_id IN ({','.join('?' * len(bundle_ids))})")
                params += list(bundle_ids)
            scope = " AND (" + " OR ".join(parts) + ")"
        with self.lock:
            rows = self.db.execute(
                "SELECT c.text AS text, d.id AS document_id, d.name AS name, bm25(chunks) AS score "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                f"WHERE chunks MATCH ?{scope} ORDER BY score LIMIT ?",
                (*params, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def catalog(self):
        """Every permanent project with its documents' names and summaries."""
        result = []
        for project in self.list_projects():
            result.append({**project, "files": [
                {k: d[k] for k in ("id", "name", "kind", "summary")}
                for d in self.documents(project_id=project["id"])]})
        return result

    @staticmethod
    def _remove_files(paths):
        for path in paths:
            try:
                if path:
                    Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def close(self):
        with self.lock:
            self.db.close()


def to_json(value):
    return json.dumps(value, ensure_ascii=False)
