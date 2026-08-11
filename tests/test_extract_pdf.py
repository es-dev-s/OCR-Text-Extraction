"""Unit tests for PDF title/text extraction."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from extract_pdf import (
    _is_cover_noise,
    extract_document_title_bytes,
    extract_pdf_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "Demo.pdf"
OURSIDE = ROOT / "1.Ourside_Mass_transfer.pdf"


def _pdf_bytes_from_pages(pages: list[list[tuple[str, float, bool, float]]]) -> bytes:
    """Build a tiny PDF.

    pages: list of pages; each page is list of (text, fontsize, bold, y)
    """
    doc = pymupdf.open()
    for page_lines in pages:
        page = doc.new_page()
        for text, size, bold, y in page_lines:
            # PyMuPDF insert_text doesn't reliably set bold via fontname on all builds;
            # use fontfile-less fontname hint.
            font = "helv" if not bold else "hebo"
            page.insert_text((72, y), text, fontsize=size, fontname=font)
    data = doc.tobytes()
    doc.close()
    return data


def test_cover_noise_filters_location_and_roles():
    assert _is_cover_noise("Stockholm, Sweden")
    assert _is_cover_noise("KTH Royal Institute of Technology")
    assert _is_cover_noise("Master of Science Thesis")
    assert _is_cover_noise("Student: Ibrahim Abidemi Lawal")
    assert _is_cover_noise("June 10, 2024")
    assert _is_cover_noise("Abstract")
    assert not _is_cover_noise(
        "Analyzing CO2 Capture Using Sodium Hydroxide in a Spray Column"
    )


def test_title_first_page_wins():
    data = _pdf_bytes_from_pages(
        [
            [("First Page Wins", 28, True, 100)],
            [("Second Page Should Lose", 28, True, 100)],
        ]
    )
    result = extract_document_title_bytes(data, source="first.pdf")
    assert result["document_title"] == "First Page Wins"
    assert result["document_title_page"] == 1
    assert result["pages_scanned_for_title"] == 1


def test_title_falls_back_when_page1_empty():
    data = _pdf_bytes_from_pages(
        [
            [],  # empty page 1
            [("Fallback Document Title", 26, True, 90)],
        ]
    )
    result = extract_document_title_bytes(data, source="fallback.pdf")
    assert result["document_title"] == "Fallback Document Title"
    assert result["document_title_page"] == 2
    assert result["pages_scanned_for_title"] == 2
    assert result["title_pages_checked"][0]["is_empty"] is True


def test_title_skips_cover_noise_for_thesis_like_page():
    data = _pdf_bytes_from_pages(
        [
            [
                ("KTH Royal Institute of Technology", 16, False, 80),
                ("Stockholm, Sweden", 16, True, 110),
                ("Master of Science Thesis", 16, True, 200),
                ("Analyzing CO2 Capture Using Sodium Hydroxide in a Spray Column:", 14, True, 300),
                ("Operational Influences on Absorption Efficiency and Regeneration", 14, True, 320),
                ("Student: Someone", 12, True, 420),
                ("June 10, 2024", 12, False, 500),
            ]
        ]
    )
    result = extract_document_title_bytes(data, source="thesis.pdf")
    title = result["document_title"] or ""
    assert "Analyzing CO2 Capture" in title
    assert "Stockholm" not in title
    assert "Master of Science" not in title


def test_extract_bytes_returns_text_and_page_titles():
    data = _pdf_bytes_from_pages(
        [
            [("Sample Report Title", 22, True, 100), ("Body paragraph one.", 11, False, 160)],
            [("Body page two continues here with more text.", 11, False, 100)],
        ]
    )
    result = extract_pdf_bytes(data, source="sample.pdf", include_full_text=True)
    assert result["page_count"] == 2
    assert result["document_title"] == "Sample Report Title"
    assert result["pages"][0]["char_count"] > 0
    assert "Sample Report Title" in result["full_text"]
    assert isinstance(result["page_titles"], list)
    assert result["elapsed_ms"] >= 0


@pytest.mark.skipif(not DEMO.exists(), reason="Demo.pdf not present")
def test_real_demo_pdf_title():
    result = extract_document_title_bytes(DEMO.read_bytes(), source="Demo.pdf")
    assert result["document_title"] == "THE WATER OF SYSTEMS CHANGE"
    assert result["document_title_page"] == 1
    assert result["pages_scanned_for_title"] == 1


@pytest.mark.skipif(not OURSIDE.exists(), reason="Ourside PDF not present")
def test_real_ourside_pdf_title():
    result = extract_document_title_bytes(OURSIDE.read_bytes(), source="ourside.pdf")
    assert result["document_title"] == (
        "Analyzing CO2 Capture Using Sodium Hydroxide in a Spray Column: "
        "Operational Influences on Absorption Efficiency and Regeneration"
    )
    assert result["document_title_page"] == 1


@pytest.mark.skipif(not DEMO.exists(), reason="Demo.pdf not present")
def test_real_demo_full_extract_has_text():
    result = extract_pdf_bytes(DEMO.read_bytes(), source="Demo.pdf", include_full_text=False)
    assert result["page_count"] == 20
    assert sum(p["char_count"] for p in result["pages"]) > 50000
    assert result["document_title"] == "THE WATER OF SYSTEMS CHANGE"
