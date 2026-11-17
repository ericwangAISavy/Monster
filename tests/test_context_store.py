"""Context store: permanent projects, temporary per-task bundles, extraction and search."""

import io
import zipfile

import pytest

from monster.context.store import BUNDLE_TTL_S, ContextError, ContextStore, extract_text


@pytest.fixture
def store(tmp_path):
    now = [1000.0]
    s = ContextStore(tmp_path / "context", clock=lambda: now[0])
    s.test_clock = now
    yield s
    s.close()


def docx_bytes(*paragraphs):
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_projects_hold_files_and_text_until_deleted(store, tmp_path):
    project = store.create_project("Job applications – Jane", "Use Jane's details.")
    resume = store.add_file(project, "resume.docx", docx_bytes("Jane Doe", "Phone: 555-0100",
                                                               "Senior backend engineer"))
    store.add_text(project, "Salary.txt", "Expected salary: 150k")
    store.add_file(project, "photo.png", b"\x89PNG fake")
    docs = store.documents(project_id=project)
    assert [d["kind"] for d in docs] == ["file", "text", "image"]
    assert "Jane Doe" in store.document_text(resume)
    assert store.list_projects()[0]["documents"] == 3
    hits = store.search("phone number", project_ids=[project])
    assert hits and "555-0100" in hits[0]["text"]
    files = list((tmp_path / "context" / "files").iterdir())
    assert len(files) == 3
    store.delete_project(project)
    assert store.list_projects() == [] and not list((tmp_path / "context" / "files").iterdir())
    assert store.search("phone") == []


def test_bundle_is_deleted_on_success_and_kept_on_failure(store, tmp_path):
    ok = store.create_bundle(text="Job description: backend role", files=[("req.md", b"# Reqs")],
                             images=[("sketch.png", b"PNG")])
    assert len(store.documents(bundle_id=ok)) == 3
    store.finish_bundle(ok, succeeded=True)
    assert store.bundle(ok) is None and store.documents(bundle_id=ok) == []
    kept = store.create_bundle(text="Requirements: payments")
    store.finish_bundle(kept, succeeded=False)
    assert store.bundle(kept)["status"] == "kept" and store.search("payments", bundle_ids=[kept])
    store.test_clock[0] += BUNDLE_TTL_S + 1
    assert store.purge_expired() == 1 and store.bundle(kept) is None
    assert not list((tmp_path / "context" / "files").iterdir())


def test_bundles_and_projects_are_searched_separately(store):
    project = store.create_project("Payment design")
    store.add_text(project, "design.md", "Payment service uses a ledger database")
    bundle = store.create_bundle(text="Ledger notes for this task only")
    assert {h["name"] for h in store.search("ledger", project_ids=[project])} == {"design.md"}
    assert {h["name"] for h in store.search("ledger", bundle_ids=[bundle])} == {"Context from the command.txt"}
    assert len(store.search("ledger")) == 2


def test_limits_and_empty_bundles(store):
    with pytest.raises(ContextError, match="limit"):
        store.create_bundle(files=[("big.bin", b"x" * (21 * 1024 * 1024))])
    assert store.create_bundle(text="   ") is None
    with pytest.raises(ContextError):
        store.create_project("  ")


def test_text_extraction_by_type():
    assert extract_text("a.md", b"# Title") == ("file", "# Title")
    assert extract_text("a.png", b"...") == ("image", None)
    assert extract_text("a.docx", docx_bytes("Hello", "World"))[1] == "Hello\nWorld"
    assert extract_text("broken.docx", b"not a zip") == ("file", None)


def test_catalog_lists_projects_with_file_summaries(store):
    project = store.create_project("Jane")
    store.add_text(project, "bio.txt", "Jane is a backend engineer with ten years of experience.")
    catalog = store.catalog()
    assert catalog[0]["name"] == "Jane" and catalog[0]["files"][0]["summary"].startswith("Jane is")


def test_pdf_text_is_extracted_and_searchable(store):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject(
        {NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 100 Td (Jane Doe phone 555-0100) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    project = store.create_project("Jane")
    store.add_file(project, "resume.pdf", buffer.getvalue())
    hits = store.search("phone", project_ids=[project])
    assert hits and "555-0100" in hits[0]["text"]


def test_files_get_a_clean_upload_copy_that_is_removed_with_them(store, tmp_path):
    project = store.create_project("Thomas")
    resume = store.add_file(project, "resume.pdf", b"%PDF-1.4 fake")
    store.add_text(project, "answers.txt", "Phone: 555")
    assert [name for name, _ in store.uploadable(project_ids=[project])] == ["resume.pdf"]
    path = store.upload_path(resume)
    assert path.endswith("resume.pdf") and "upload" in path
    from pathlib import Path
    assert Path(path).read_bytes() == b"%PDF-1.4 fake" and Path(path).name == "resume.pdf"
    store.delete_document(resume)
    assert not Path(path).exists()


def test_linked_files_are_used_in_place_and_never_deleted(store, tmp_path):
    original = tmp_path / "profiles" / "Thomas Dang - Senior ML Engineer - 2026.pdf"
    original.parent.mkdir()
    original.write_bytes(b"%PDF-1.4 resume")
    project = store.create_project("Job Application")
    doc = store.link_file(project, str(original))
    assert store.upload_path(doc) == str(original.resolve())  # The exact local file.
    assert [d["linked"] for d in store.documents(project_id=project)] == [1]
    store.delete_document(doc)
    assert original.exists()  # Forgetting the link keeps the user's file.
    store.link_file(project, str(original))
    store.delete_project(project)
    assert original.exists()
    bundle = store.create_bundle(links=[str(original)])
    store.finish_bundle(bundle, succeeded=True)
    assert original.exists()
    with pytest.raises(ContextError, match="not a readable file"):
        store.link_file(store.create_project("x"), str(tmp_path / "missing.pdf"))


def test_version_one_databases_gain_linked_documents(tmp_path):
    import sqlite3

    root = tmp_path / "old"
    ContextStore(root).close()
    db = sqlite3.connect(root / "context.sqlite3")
    db.executescript("""
        CREATE TABLE documents_old AS SELECT * FROM documents;
        DROP TABLE documents;
        CREATE TABLE documents (id TEXT PRIMARY KEY, project_id TEXT, bundle_id TEXT, name TEXT NOT NULL,
            kind TEXT NOT NULL, path TEXT, size INTEGER NOT NULL, text TEXT, summary TEXT NOT NULL,
            entities_json TEXT NOT NULL DEFAULT '[]', purpose TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL);
        UPDATE schema_version SET version = 1;
    """)
    db.commit()
    db.close()
    store = ContextStore(root)
    try:
        project = store.create_project("Upgraded")
        store.add_text(project, "a.txt", "hello")
        assert store.documents(project_id=project)[0]["linked"] == 0
    finally:
        store.close()
