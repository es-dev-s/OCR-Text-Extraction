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
UNIT_OPS = ROOT / "2.Ourside_Unit_operations_of_chemical_engineering_.pdf"
PDF_DIR = ROOT / "PDF"

# Expected titles for local PDF/ corpus (validated against cover page text)
PDF_DIR_EXPECTED = {
    "1-Our-Process_modelling_and_simulation.pdf": (
        "Simulation of the Extractive Distillation using Ethylene Glycol as an "
        "Entrainer in the Bioethanol Dehydration"
    ),
    "1.Our.Transport_Phenomena.pdf": (
        "A Study on Supported Liquid Membrane for Selective Separation of Cr(VI)"
    ),
    "2-Our-Mas_trasnfer_III.pdf": (
        "Absorption Characteristics of Ammonia-Water System in the Cylindrical Tube Absorber"
    ),
    "2.Our.Project_7th_sem.pdf": (
        "Process Simulation of Ethanol Production from Biomass Gasification and "
        "Syngas Fermentation"
    ),
    "2.Ourside_Unit_operations_of_chemical_engineering_.pdf": (
        "Steady state simulation of Extractive Distillation system using Aspen Plus"
    ),
    "3-Our-Plant_design_project.pdf": (
        "Process Integration Approach to the Methanol (MeOH) Production Variability "
        "from Syngas and Industrial Waste Gases"
    ),
    "3.Our.Project_8th_sem.pdf": (
        "Steady state simulation of Plug Flow Reactor (PFR) in Aspen plus"
    ),
    "3.Ourside_Final_year_project_II.pdf": (
        "OPTIMIZATION ON ACRYLIC ACID PLANT BY USING ASPEN PLUS"
    ),
}


def _pdf_bytes_from_pages(pages: list[list[tuple[str, float, bool, float]]]) -> bytes:
    """Build a tiny PDF.

    pages: list of pages; each page is list of (text, fontsize, bold, y)
    """
    doc = pymupdf.open()
    for page_lines in pages:
        page = doc.new_page()
        for text, size, bold, y in page_lines:
            font = "helv" if not bold else "hebo"
            page.insert_text((72, y), text, fontsize=size, fontname=font)
    data = doc.tobytes()
    doc.close()
    return data


def test_cover_noise_filters_location_and_roles():
    assert _is_cover_noise("Stockholm, Sweden")
    assert _is_cover_noise("KTH Royal Institute of Technology")
    assert _is_cover_noise("National Institute of Technology")
    assert _is_cover_noise("National Institute of Technology Rourkela May 2015")
    assert _is_cover_noise("Master of Science Thesis")
    assert _is_cover_noise("Project Report")
    assert _is_cover_noise("Submitted by")
    assert _is_cover_noise("Rourkela")
    assert _is_cover_noise("May 2015")
    assert _is_cover_noise("Student: Ibrahim Abidemi Lawal")
    assert _is_cover_noise("June 10, 2024")
    assert _is_cover_noise("Abstract")
    assert _is_cover_noise(
        "Tel.: +1.405.744.8397; Fax: +1.405.744.6059. Email address: hasan.atiyeh@okstate.edu"
    )
    assert _is_cover_noise(
        "Version of Record: https://www.sciencedirect.com/science/article/pii/S0960852417315079"
    )
    assert _is_cover_noise("Stillwater, OK, USA")
    assert not _is_cover_noise(
        "Analyzing CO2 Capture Using Sodium Hydroxide in a Spray Column"
    )
    assert not _is_cover_noise(
        "Steady state simulation of Extractive Distillation system using Aspen Plus"
    )
    assert not _is_cover_noise(
        "Process Simulation of Ethanol Production from Biomass Gasification and Syngas Fermentation"
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
            [],
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
                ("Stockholm, Sweden", 16, False, 110),
                ("Master of Science Thesis", 16, True, 200),
                (
                    "Analyzing CO2 Capture Using Sodium Hydroxide in a Spray Column:",
                    18,
                    True,
                    300,
                ),
                (
                    "Operational Influences on Absorption Efficiency and Regeneration",
                    18,
                    True,
                    325,
                ),
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


def test_project_report_cover_prefers_work_title_not_institute():
    """NIT-style project report: On + largest title, not footer institute/date."""
    data = _pdf_bytes_from_pages(
        [
            [
                ("A", 14, False, 70),
                ("Project Report", 14, False, 95),
                ("On", 14, False, 120),
                ("Steady state simulation of Extractive", 20, False, 150),
                ("Distillation system using Aspen Plus", 20, False, 185),
                ("Submitted by", 14, False, 250),
                ("Pritam Kumar Bala", 14, False, 295),
                ("Bachelor of Technology in Chemical Engineering", 14, False, 390),
                ("Department of Chemical Engineering", 18, False, 620),
                ("National Institute of Technology", 18, False, 655),
                ("Rourkela", 18, False, 690),
                ("May 2015", 18, False, 725),
            ]
        ]
    )
    result = extract_document_title_bytes(data, source="unitops.pdf")
    title = result["document_title"] or ""
    assert "Steady state simulation of Extractive" in title
    assert "Aspen Plus" in title
    assert "National Institute" not in title
    assert "Rourkela" not in title
    assert "May 2015" not in title


def test_journal_page_prefers_title_not_contact_email():
    """Article front matter: title wins over Tel/Fax/Email author chrome."""
    data = _pdf_bytes_from_pages(
        [
            [
                (
                    "Process Simulation of Ethanol Production from Biomass Gasification and Syngas",
                    12,
                    True,
                    120,
                ),
                ("Fermentation", 12, True, 145),
                (
                    "Oscar Pardo-Planas1, Hasan K. Atiyeh1,*, John R. Phillips1, Clint P. Aichele2 and Sayeed",
                    12,
                    False,
                    175,
                ),
                ("Mohammad2", 12, False, 200),
                (
                    "1 Department of Biosystems and Agricultural Engineering, Oklahoma State University,",
                    12,
                    False,
                    230,
                ),
                ("Stillwater, OK, USA", 12, False, 255),
                (
                    "Tel.: +1.405.744.8397; Fax: +1.405.744.6059. Email address: hasan.atiyeh@okstate.edu",
                    12,
                    False,
                    310,
                ),
                ("Abstract", 12, False, 350),
                (
                    "The hybrid gasification-syngas fermentation platform can produce more bioethanol",
                    12,
                    False,
                    380,
                ),
            ]
        ]
    )
    result = extract_document_title_bytes(data, source="journal.pdf")
    title = result["document_title"] or ""
    assert "Process Simulation of Ethanol Production" in title
    assert "Fermentation" in title
    assert "Tel." not in title
    assert "@" not in title
    assert "Email" not in title


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


@pytest.mark.skipif(not UNIT_OPS.exists(), reason="Unit ops PDF not present")
def test_real_unit_ops_project_report_title():
    result = extract_document_title_bytes(UNIT_OPS.read_bytes(), source="unitops.pdf")
    title = result["document_title"] or ""
    assert title == (
        "Steady state simulation of Extractive Distillation system using Aspen Plus"
    )
    assert result["document_title_page"] == 1


@pytest.mark.skipif(not PDF_DIR.is_dir(), reason="PDF/ corpus folder not present")
@pytest.mark.parametrize("filename,expected", sorted(PDF_DIR_EXPECTED.items()))
def test_pdf_folder_titles(filename: str, expected: str):
    path = PDF_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} missing from PDF/")
    result = extract_document_title_bytes(path.read_bytes(), source=filename)
    assert result["document_title"] == expected
    assert result["document_title_page"] == 1


@pytest.mark.skipif(not DEMO.exists(), reason="Demo.pdf not present")
def test_real_demo_full_extract_has_text():
    result = extract_pdf_bytes(DEMO.read_bytes(), source="Demo.pdf", include_full_text=False)
    assert result["page_count"] == 20
    assert sum(p["char_count"] for p in result["pages"]) > 50000
    assert result["document_title"] == "THE WATER OF SYSTEMS CHANGE"
