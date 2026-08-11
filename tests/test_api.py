"""API connection and endpoint tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "Demo.pdf"
OURSIDE = ROOT / "1.Ourside_Mass_transfer.pdf"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main_mod, "API_KEY", "test-key")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def open_client(monkeypatch):
    monkeypatch.setattr(main_mod, "API_KEY", "")
    with TestClient(app) as c:
        yield c


def _tiny_pdf(title: str = "Tiny API Title") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), title, fontsize=24, fontname="hebo")
    page.insert_text((72, 160), "Body text for extract tests.", fontsize=11, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["service"] == "pdf-extract-api"
    assert body["api_key_required"] is True


def test_root_lists_endpoints(client):
    res = client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["endpoints"]["title"] == "POST /v1/title"
    assert "tester" not in body


def test_title_requires_api_key(client):
    pdf = _tiny_pdf()
    res = client.post(
        "/v1/title",
        files={"file": ("t.pdf", pdf, "application/pdf")},
    )
    assert res.status_code == 401


def test_title_success(client):
    pdf = _tiny_pdf("Wired Title Result")
    res = client.post(
        "/v1/title",
        headers={"X-API-Key": "test-key"},
        files={"file": ("t.pdf", pdf, "application/pdf")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["document_title"] == "Wired Title Result"
    assert body["pages_scanned_for_title"] >= 1
    assert "request_id" in body


def test_extract_success(client):
    pdf = _tiny_pdf("Extractable Document")
    res = client.post(
        "/v1/extract",
        headers={"X-API-Key": "test-key"},
        files={"file": ("t.pdf", pdf, "application/pdf")},
        data={"include_pages": "true", "include_full_text": "true"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["page_count"] == 1
    assert body["document_title"] == "Extractable Document"
    assert "pages" in body
    assert "full_text" in body


def test_rejects_non_pdf(client):
    res = client.post(
        "/v1/title",
        headers={"X-API-Key": "test-key"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 400


def test_batch_title(client):
    pdf = _tiny_pdf("Batch Title A")
    files = [
        ("files", ("a.pdf", pdf, "application/pdf")),
        ("files", ("b.pdf", pdf, "application/pdf")),
    ]
    res = client.post(
        "/v1/batch/title",
        headers={"X-API-Key": "test-key"},
        files=files,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert all(item["ok"] for item in body["results"])


def test_open_mode_without_api_key(open_client):
    pdf = _tiny_pdf("Open Mode Title")
    res = open_client.post(
        "/v1/title",
        files={"file": ("t.pdf", pdf, "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["document_title"] == "Open Mode Title"


@pytest.mark.skipif(not DEMO.exists(), reason="Demo.pdf not present")
def test_live_demo_title_endpoint(client):
    res = client.post(
        "/v1/title",
        headers={"X-API-Key": "test-key"},
        files={"file": ("Demo.pdf", DEMO.read_bytes(), "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["document_title"] == "THE WATER OF SYSTEMS CHANGE"


@pytest.mark.skipif(not OURSIDE.exists(), reason="Ourside PDF not present")
def test_live_ourside_title_endpoint(client):
    res = client.post(
        "/v1/title",
        headers={"X-API-Key": "test-key"},
        files={"file": ("ourside.pdf", OURSIDE.read_bytes(), "application/pdf")},
    )
    assert res.status_code == 200
    title = res.json()["document_title"]
    assert title.startswith("Analyzing CO2 Capture Using Sodium Hydroxide")
