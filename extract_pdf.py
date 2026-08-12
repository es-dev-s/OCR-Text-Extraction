#!/usr/bin/env python3
"""Fast embedded-text PDF extractor (no OCR).

Uses PyMuPDF for low-latency extraction of text, per-page titles/headings,
tables, links, annotations, images, and document metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import pymupdf

DEFAULT_PDF = Path(__file__).resolve().parent / "Demo.pdf"

# Footer / noise patterns that should never be treated as titles
_FOOTER_RE = re.compile(
    r"(?i)^\s*(?:\d+\s*[|]\s*.*|.*\s*[|]\s*\d+\s*|page\s+\d+\s*(?:of\s+\d+)?)\s*$"
)
_WS_RE = re.compile(r"\s+")


def _clean_meta(meta: dict[str, Any]) -> dict[str, str]:
    return {k: v for k, v in meta.items() if v}


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _is_bold(span: dict[str, Any]) -> bool:
    flags = int(span.get("flags") or 0)
    font = span.get("font") or ""
    return bool(flags & 2**4) or ("Bold" in font)


def _line_objects(page: pymupdf.Page) -> list[dict[str, Any]]:
    """Structured lines with font metrics for title detection."""
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    lines: list[dict[str, Any]] = []

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            raw = "".join(s.get("text", "") for s in spans)
            text = _norm(raw)
            if not text:
                continue
            size = max(float(s.get("size") or 0.0) for s in spans)
            bold = any(_is_bold(s) for s in spans)
            bbox = line.get("bbox") or (0, 0, 0, 0)
            lines.append(
                {
                    "text": text,
                    "size": round(size, 2),
                    "bold": bold,
                    "font": spans[0].get("font") or "",
                    "y0": float(bbox[1]),
                    "y1": float(bbox[3]),
                    "x0": float(bbox[0]),
                }
            )

    lines.sort(key=lambda x: (round(x["y0"], 1), x["x0"]))
    return lines


_TRAILING_FRAG = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

# Cover-page noise: institutions, places, roles, thesis boilerplate — not the work title
_COVER_NOISE_RE = re.compile(
    r"(?ix)^(?:"
    r"(?:kth|royal\s+institute(?:\s+of\s+technology)?|university|department|school|"
    r"faculty|college|institute)\b.*"
    r"|(?:national\s+)?institute\s+of\s+technology\b.*"
    r"|master\s+of\s+(?:science|technology|engineering)(?:\s+thesis)?"
    r"|bachelor\s+of\s+(?:science|technology|engineering).*"
    r"|doctoral\s+thesis|phd\s+thesis|dissertation|project\s*reports?"
    r"|a\s+project\s+report|projectreport"
    r"|submitted\s*by|submittedby|under\s+the\s+guidance\s+of|undertheguidanceof"
    r"|in\s+partial\s+fulfil+l?ment.*"
    r"|roll\s*no\.?\s*:?.*"
    r"|student\s*:.*|examiner\s*:.*|supervisor\s*:.*|author\s*:.*"
    r"|associate\s+professor|postdoctoral\s+researcher|professor\b.*"
    r"|dr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}"  # advisor name lines
    r"|abstract|acknowledgements?|table\s+of\s+contents|contents"
    r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{4}"
    r")$"
)

_LOCATION_RE = re.compile(
    r"(?ix)^[A-Z][A-Za-z .'-]+,\s*[A-Z][A-Za-z .'-]+$"
)

_INSTITUTION_IN_TEXT_RE = re.compile(
    r"(?ix)(?:"
    r"(?:national\s+)?institute\s+of\s+technology"
    r"|\buniversity\b|\bdepartment\s+of\b|\bfaculty\s+of\b"
    r"|\broyal\s+institute\b|\bcollege\s+of\b"
    r")"
)

_DATE_RE = re.compile(
    r"(?ix)^(?:"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{4}"
    r"|\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")$"
)

_TITLE_LABEL_RE = re.compile(
    r"(?ix)^(?:a|on|title|project\s*reports?|thesis|dissertation)$"
)

_SINGLE_PLACE_RE = re.compile(
    r"(?ix)^(?:rourkela|stockholm|sweden|india|delhi|mumbai|chennai|kolkata|"
    r"bangalore|bengaluru|hyderabad|london|new\s+york)$"
)

# Contact / publisher chrome — never a document title
_CONTACT_RE = re.compile(
    r"(?ix)(?:"
    r"\b(?:tel|fax|phone|email|e-?mail)\b\.?\s*:"
    r"|@"
    r"|https?://"
    r"|www\."
    r"|\bdoi\s*:"
    r"|version\s+of\s+record"
    r"|corresponding\s+author"
    r"|manuscript_[0-9a-f]+"
    r")"
)

# City, ST, Country affiliation lines
_CITY_STATE_RE = re.compile(
    r"(?ix)^[A-Za-z .'-]+,\s*[A-Z]{2}\b(?:,?\s*(?:USA|U\.S\.A\.|UK|Canada|India))?\.?$"
)

def _heading_case(text: str) -> bool:
    """True for Title Case / ALL CAPS style headings."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text)
    words = [w for w in words if len(w) > 2]
    if not words:
        return False
    titled = sum(1 for w in words if w[0].isupper())
    if titled / len(words) >= 0.55:
        return True
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and (sum(c.isupper() for c in letters) / len(letters) >= 0.75)


def _is_contact_or_chrome(text: str) -> bool:
    """Emails, phones, DOIs, publisher headers — not titles."""
    t = _norm(text)
    if not t:
        return True
    if _CONTACT_RE.search(t):
        return True
    if _CITY_STATE_RE.match(t):
        return True
    # Bare affiliation superscripts left after author wrap, e.g. "Mohammad2"
    if re.fullmatch(r"[A-Za-z'’\-]+\d+", t) and len(t) <= 24:
        return True
    return False


def _is_author_line(text: str) -> bool:
    """Heuristic for paper author lists (often sit directly under the title).

    Keep this intentionally simple — complex author regexes can catastrophic-backtrack
    on long abstract sentences. Avoid matching chemical formulas like CO2 / H2.
    """
    t = _norm(text)
    if not t or len(t) < 5 or len(t) > 180:
        return False
    if _is_contact_or_chrome(t):
        return True
    # Surname + affiliation marker: Planas1 / Atiyeh1,* (requires lowercase in name)
    if re.search(r"\b[A-Z][a-z][A-Za-z'’\-]*\d(?:[\s,*]|$)", t):
        commas = t.count(",")
        if commas >= 1 or " and " in t.lower():
            return True
        if len(t.split()) <= 4:
            return True
    return False


def _is_cover_noise(text: str) -> bool:
    t = _norm(text)
    if not t:
        return True
    if len(t) <= 2 and t.lower() in {"a", "on", "by", "of", "to"}:
        return True
    if _is_contact_or_chrome(t):
        return True
    if _COVER_NOISE_RE.match(t):
        return True
    if _DATE_RE.match(t):
        return True
    if _SINGLE_PLACE_RE.match(t):
        return True
    if _LOCATION_RE.match(t) and len(t.split()) <= 5:
        return True
    if _INSTITUTION_IN_TEXT_RE.search(t) and len(t.split()) <= 12:
        return True
    # Role lines often contain a colon label
    if re.match(
        r"(?i)^(student|examiner|supervisor|author|advisor|date|course|submitted)\s*:",
        t,
    ):
        return True
    # Pure month+year already covered; reject trailing year-only institution blocks
    if re.search(r"(?i)\b(?:institute|university|department|college)\b", t) and re.search(
        r"\b(?:19|20)\d{2}\b", t
    ):
        return True
    return False


def _is_plausible_title(text: str) -> bool:
    """Reject pull-quotes, dek blurbs, and sentence fragments (section headings)."""
    t = text.strip()
    if len(t) < 3:
        return False
    if _is_cover_noise(t):
        return False
    if t[0] in "\"'“‘":
        return False
    if '"' in t or "”" in t or "“" in t:
        return False
    words = t.split()
    if len(words) > 12:
        return False
    if t.endswith(",") or t.endswith(";") or t.endswith(":"):
        return False
    # Full sentences / callout endings
    if t.endswith(".") or t.endswith(".”") or t.endswith('."'):
        return False
    if words[-1].lower().strip(".,;:!?") in _TRAILING_FRAG:
        return False
    if not t[0].isalnum() or (t[0].isalpha() and not t[0].isupper()):
        return False
    if not _heading_case(t):
        return False
    # Parenthetical chart labels, e.g. "(explicit)"
    if t.startswith("(") and t.endswith(")"):
        return False
    return True


def _is_plausible_document_title(text: str) -> bool:
    """Looser check for cover/thesis document titles (can be long, multi-line)."""
    t = _norm(text)
    if len(t) < 8:
        return False
    if _is_cover_noise(t) or _is_author_line(t) or _is_contact_or_chrome(t):
        return False
    if t[0] in "\"'“‘":
        return False
    words = t.split()
    if not (3 <= len(words) <= 30):
        return False
    if t.endswith(",") or t.endswith(";"):
        return False
    if t.endswith(".") and len(words) > 8:
        return False
    if not t[0].isalnum() or (t[0].isalpha() and not t[0].isupper()):
        return False
    # Title Case / ALL CAPS preferred, but sentence-case thesis titles are common
    if not _heading_case(t):
        caps = sum(1 for w in words if w[:1].isupper())
        if len(words) >= 5 and caps >= 1:
            return True
        if caps / max(len(words), 1) < 0.35:
            return False
    return True


def _body_size(lines: list[dict[str, Any]]) -> float:
    if not lines:
        return 10.0
    sizes = [ln["size"] for ln in lines]
    try:
        return float(statistics.median(sizes))
    except statistics.StatisticsError:
        return float(sizes[0])


def _looks_like_footer(text: str, y0: float, page_height: float) -> bool:
    if y0 >= page_height * 0.88:
        return True
    if _FOOTER_RE.match(text):
        return True
    return False


def _score_heading(
    line: dict[str, Any],
    body: float,
    page_height: float,
) -> float:
    text = line["text"]
    if len(text) < 3 or _looks_like_footer(text, line["y0"], page_height):
        return -1.0
    if _is_cover_noise(text):
        return -1.0

    words = text.split()
    score = 0.0
    size = line["size"]

    if size >= body * 1.45:
        score += 4.0
    elif size >= body * 1.25:
        score += 3.0
    elif size > body * 1.1:
        score += 1.5

    if line["bold"]:
        score += 2.0

    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.8:
        if 4 <= len(text) <= 90:
            score += 2.0

    if line["y0"] < page_height * 0.22:
        score += 1.25
    elif line["y0"] < page_height * 0.4:
        score += 0.5

    if 1 <= len(words) <= 12 and not text.endswith("."):
        score += 1.0
    if text.endswith(",") or text.endswith(";") or len(words) > 16:
        score -= 2.5
    if text.startswith(("“", '"', "'", "‘")):
        score -= 3.0  # pull-quotes, not titles
    if text.endswith(".") and len(words) > 5:
        score -= 2.5

    return score


def _merge_title_lines(
    candidates: list[dict[str, Any]],
    body: float,
    *,
    max_words: int = 16,
) -> list[dict[str, Any]]:
    """Merge consecutive similar-size lines into multi-line titles."""
    if not candidates:
        return []

    merged: list[dict[str, Any]] = []
    cur = dict(candidates[0])
    cur["lines"] = [candidates[0]["text"]]

    for nxt in candidates[1:]:
        close_y = abs(nxt["y0"] - cur["y1"]) <= max(10.0, cur["size"] * 1.05)
        similar_size = abs(nxt["size"] - cur["size"]) <= 1.5
        similar_score = abs(nxt["score"] - cur["score"]) <= 2.0
        short_enough = len((cur["text"] + " " + nxt["text"]).split()) <= max_words
        if close_y and similar_size and similar_score and short_enough:
            cur["text"] = _norm(cur["text"] + " " + nxt["text"])
            cur["lines"].append(nxt["text"])
            cur["y1"] = nxt["y1"]
            cur["size"] = max(cur["size"], nxt["size"])
            cur["score"] = max(cur["score"], nxt["score"])
            cur["bold"] = cur["bold"] or nxt["bold"]
        else:
            merged.append(cur)
            cur = dict(nxt)
            cur["lines"] = [nxt["text"]]
    merged.append(cur)

    # Prefer true headings over body lead-ins: require clear size lift or bold/ALLCAPS
    filtered = []
    for item in merged:
        strong = (
            item["size"] >= body * 1.2
            or item["bold"]
            or item["score"] >= 5.0
        )
        if strong:
            filtered.append(item)
    return filtered or merged


def detect_cover_document_title(
    lines: list[dict[str, Any]],
    page_height: float,
) -> dict[str, Any] | None:
    """Pick the real work title on thesis/report cover pages.

    Prefers the largest substantive text cluster in the upper/mid title zone,
    especially text that follows labels like "On" / "Project Report".
    """
    usable = [
        ln
        for ln in lines
        if not _looks_like_footer(ln["text"], ln["y0"], page_height)
        and not _is_cover_noise(ln["text"])
        and not _is_author_line(ln["text"])
        and len(ln["text"]) >= 3
    ]
    if not usable:
        return None

    body = _body_size(lines)
    max_size = max(ln["size"] for ln in usable)

    # Y positions of thesis labels ("On", "Project Report") to boost following title
    label_ys = [ln["y1"] for ln in lines if _TITLE_LABEL_RE.match(_norm(ln["text"]))]

    seeds = [
        ln
        for ln in usable
        if ln["bold"]
        or ln["size"] >= body + 1.0
        or ln["size"] >= body * 1.12
        or ln["size"] >= max_size - 0.5
    ]
    if not seeds:
        seeds = usable

    clusters: list[dict[str, Any]] = []
    used: set[int] = set()
    indexed = list(enumerate(usable))

    for i, seed in indexed:
        if i in used:
            continue
        if seed not in seeds and not (
            seed["bold"] or seed["size"] >= body * 1.1 or seed["size"] >= max_size - 0.5
        ):
            continue

        group = [seed]
        used.add(i)
        y1 = seed["y1"]
        size_ref = seed["size"]
        for j, nxt in indexed[i + 1 :]:
            if j in used:
                continue
            if abs(nxt["y0"] - y1) > max(16.0, size_ref * 1.35):
                if nxt["y0"] > y1 + max(22.0, size_ref * 1.6):
                    break
                continue
            if abs(nxt["size"] - size_ref) > 2.5:
                continue
            if _is_cover_noise(nxt["text"]) or _is_author_line(nxt["text"]):
                break
            group.append(nxt)
            used.add(j)
            y1 = nxt["y1"]
            size_ref = max(size_ref, nxt["size"])
            if len(" ".join(g["text"] for g in group).split()) >= 30:
                break

        text = _norm(" ".join(g["text"] for g in group))
        if not _is_plausible_document_title(text):
            continue

        words = text.split()
        y0 = group[0]["y0"]
        size = max(g["size"] for g in group)
        score = 0.0

        if any(g["bold"] for g in group):
            score += 3.0

        # Strongly prefer the visually largest text on the cover
        score += min(5.0, max(0.0, (size - body)) * 1.15)
        if size >= max_size - 0.25:
            score += 3.0
        elif size < max_size - 2.0:
            score -= 2.0

        # Thesis titles usually sit in upper/mid band — not the footer
        if page_height * 0.12 <= y0 <= page_height * 0.62:
            score += 3.5
        elif y0 > page_height * 0.70:
            score -= 4.0  # institute / date footer blocks
        elif y0 < page_height * 0.10:
            score -= 0.5

        # Text right after "On" / "Project Report" is almost always the title
        if any(0 <= (y0 - ly) <= 90 for ly in label_ys):
            score += 4.0

        if 6 <= len(words) <= 24:
            score += 3.0
        elif 4 <= len(words) <= 5:
            score += 1.5
        elif len(words) <= 3:
            score -= 2.5

        # Colon can mark a real subtitle — but not Tel:/Email:/DOI: chrome
        if ":" in text and not _CONTACT_RE.search(text):
            score += 1.0

        # Person-name shaped clusters (2-4 Title Case tokens) are weak titles
        if 2 <= len(words) <= 4 and _heading_case(text) and size <= body + 2:
            score -= 2.0

        # Reject leftover author/contact chrome if it slipped through
        if _is_author_line(text) or _is_contact_or_chrome(text):
            score -= 8.0

        clusters.append(
            {
                "text": text,
                "score": round(score, 2),
                "size": size,
                "bold": any(g["bold"] for g in group),
                "y0": y0,
                "y1": group[-1]["y1"],
            }
        )

    if not clusters:
        return None

    best = max(
        clusters,
        key=lambda c: (c["score"], c["size"], len(c["text"].split()), -c["y0"]),
    )
    if best["score"] < 3.0:
        return None

    confidence = round(min(1.0, max(0.0, (best["score"] - 2.0) / 10.0)), 3)
    return {
        "page_title": best["text"],
        "page_title_confidence": confidence,
        "title_source": "cover_document_title",
        "headings": [
            {
                "text": best["text"],
                "score": best["score"],
                "font_size": round(best["size"], 2),
                "bold": best["bold"],
                "y": round(best["y0"], 2),
            }
        ],
        "body_font_size": round(body, 2),
    }


def _candidate_font_size(candidate: dict[str, Any] | None) -> float:
    if not candidate:
        return 0.0
    heads = candidate.get("headings") or []
    if heads and heads[0].get("font_size"):
        return float(heads[0]["font_size"])
    return 0.0


def _pick_best_title_candidate(
    *candidates: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Choose among detectors by font size, confidence, and substance — not word-count hacks."""
    opts = [c for c in candidates if c and c.get("page_title")]
    if not opts:
        return None

    def rank(c: dict[str, Any]) -> tuple:
        title = c["page_title"]
        words = len(title.split())
        font = _candidate_font_size(c)
        conf = float(c.get("page_title_confidence") or 0.0)
        score = 0.0
        if c.get("headings"):
            score = float(c["headings"][0].get("score") or 0.0)
        # Reject leftover institution-ish strings hard
        noise_pen = 5.0 if _is_cover_noise(title) else 0.0
        substantive = 1 if 5 <= words <= 28 else 0
        return (font - noise_pen, score, conf, substantive, words)

    return max(opts, key=rank)


def detect_page_titles(
    lines: list[dict[str, Any]],
    page_height: float,
) -> dict[str, Any]:
    if not lines:
        return {
            "page_title": None,
            "page_title_confidence": 0.0,
            "title_source": "none",
            "headings": [],
        }

    body = _body_size(lines)
    scored: list[dict[str, Any]] = []
    for ln in lines:
        score = _score_heading(ln, body, page_height)
        if score < 3.0:
            continue
        scored.append({**ln, "score": round(score, 2)})

    scored.sort(key=lambda x: (x["y0"], -x["score"]))
    headings = _merge_title_lines(scored, body, max_words=28)

    # Keep only title-like headings in the public list
    headings = [h for h in headings if _is_plausible_title(h["text"]) or _is_plausible_document_title(h["text"])]

    # Rank: score first, then earlier on page, then larger font
    ranked = sorted(
        headings,
        key=lambda h: (-h["score"], h["y0"], -h["size"]),
    )

    page_title = None
    confidence = 0.0
    source = "none"
    # Require a stronger score so callouts don't become page titles
    strong = [h for h in ranked if h["score"] >= 4.5]
    if strong:
        best = strong[0]
        page_title = best["text"]
        # Normalize confidence into 0..1 from heuristic score (~3..10)
        confidence = round(min(1.0, max(0.0, (best["score"] - 2.5) / 7.0)), 3)
        source = "content_font_heuristic"

    heading_out = [
        {
            "text": h["text"],
            "score": h["score"],
            "font_size": h["size"],
            "bold": h["bold"],
            "y": round(h["y0"], 2),
        }
        for h in sorted(headings, key=lambda h: h["y0"])
    ]

    return {
        "page_title": page_title,
        "page_title_confidence": confidence,
        "title_source": source,
        "headings": heading_out,
        "body_font_size": round(body, 2),
    }


def _page_is_empty(text: str, lines: list[dict[str, Any]]) -> bool:
    """True when the page has no meaningful embedded text."""
    compact = _norm(text)
    if len(compact) < 8:
        return True
    letters = sum(1 for c in compact if c.isalpha())
    if letters < 4:
        return True
    return not lines


def _merge_cluster_text(cluster: list[dict[str, Any]], max_size: float) -> str:
    merged = [cluster[0]["text"]]
    y1 = cluster[0]["y1"]
    for ln in cluster[1:]:
        if abs(ln["y0"] - y1) <= max(10.0, max_size * 0.9):
            merged.append(ln["text"])
            y1 = ln["y1"]
        elif len(merged) <= 2 and ln["y0"] > y1:
            # allow small gaps for stacked cover titles
            merged.append(ln["text"])
            y1 = ln["y1"]
        else:
            break
    return _norm(" ".join(merged))


def _largest_font_title(
    lines: list[dict[str, Any]],
    page_height: float,
) -> dict[str, Any] | None:
    """Cover/sparse-page fallback: largest (or only) title-like text cluster."""
    usable = [
        ln
        for ln in lines
        if not _looks_like_footer(ln["text"], ln["y0"], page_height)
        and len(ln["text"]) >= 2
    ]
    if not usable:
        return None

    max_size = max(ln["size"] for ln in usable)
    body = _body_size(usable)
    sparse_cover = len(usable) <= 6
    has_size_lift = max_size >= max(body * 1.35, body + 2.5)

    # Need either a clear display-size lift, or a sparse cover-like page
    if not has_size_lift and not sparse_cover:
        return None

    cluster = sorted(
        [ln for ln in usable if ln["size"] >= max_size - 1.0],
        key=lambda ln: (ln["y0"], ln["x0"]),
    )
    if not cluster:
        return None

    title = _merge_cluster_text(cluster, max_size)
    words = title.split()
    plausible = _is_plausible_title(title) or (
        2 <= len(words) <= 12 and _heading_case(title) and 4 <= len(title) <= 120
    )
    if not plausible:
        return None

    score = 5.5
    if has_size_lift:
        score += min(3.0, (max_size - body) / 4.0)
    if sparse_cover:
        score += 1.0
    return {
        "page_title": title,
        "page_title_confidence": round(min(1.0, (score - 2.5) / 7.0), 3),
        "title_source": "largest_font_cover",
        "headings": [
            {
                "text": title,
                "score": round(score, 2),
                "font_size": round(max_size, 2),
                "bold": any(ln["bold"] for ln in cluster[: min(3, len(cluster))]),
                "y": round(cluster[0]["y0"], 2),
            }
        ],
        "body_font_size": round(body, 2),
    }


def peek_page_title(page: pymupdf.Page) -> dict[str, Any]:
    """Lightweight title peek for one page (no tables/images/full extract)."""
    text = page.get_text("text", sort=True).rstrip()
    lines = _line_objects(page)
    empty = _page_is_empty(text, lines)
    if empty:
        return {
            "page": page.number + 1,
            "is_empty": True,
            "page_title": None,
            "page_title_confidence": 0.0,
            "title_source": "empty_page",
            "char_count": len(text),
        }

    cover = detect_cover_document_title(lines, page.rect.height)
    info = detect_page_titles(lines, page.rect.height)
    largest = _largest_font_title(lines, page.rect.height)
    chosen = _pick_best_title_candidate(cover, info, largest)

    if not chosen:
        chosen = {
            "page_title": None,
            "page_title_confidence": 0.0,
            "title_source": "none",
        }

    return {
        "page": page.number + 1,
        "is_empty": False,
        "page_title": chosen.get("page_title"),
        "page_title_confidence": chosen.get("page_title_confidence", 0.0),
        "title_source": chosen.get("title_source", "none"),
        "char_count": len(text),
    }


def resolve_document_title(
    doc: pymupdf.Document,
    *,
    max_pages: int = 2,
) -> dict[str, Any]:
    """First-wins PDF title: page 1, else page 2 (does not scan the whole PDF).

    Rules:
    1. Peek page 1. If it has a title, that is the document title.
    2. If page 1 is empty or has no title, peek page 2.
    3. If page 2 has a title, use that.
    4. Only if both fail, fall back to PDF metadata title (if present).
    """
    metadata = _clean_meta(doc.metadata or {})
    pages_checked: list[dict[str, Any]] = []
    limit = min(max(1, max_pages), doc.page_count)

    for i in range(limit):
        peek = peek_page_title(doc[i])
        pages_checked.append(
            {
                "page": peek["page"],
                "is_empty": peek["is_empty"],
                "title": peek["page_title"],
                "confidence": peek["page_title_confidence"],
                "source": peek["title_source"],
                "char_count": peek["char_count"],
            }
        )
        if peek["is_empty"]:
            continue
        if peek["page_title"]:
            return {
                "title": peek["page_title"],
                "title_source": f"page_{peek['page']}_content",
                "title_confidence": peek["page_title_confidence"],
                "title_page": peek["page"],
                "pages_scanned": i + 1,
                "pages_checked": pages_checked,
                "metadata": metadata,
            }

    meta_title = (metadata.get("title") or "").strip()
    if meta_title:
        return {
            "title": meta_title,
            "title_source": "pdf_metadata",
            "title_confidence": 0.8,
            "title_page": None,
            "pages_scanned": limit,
            "pages_checked": pages_checked,
            "metadata": metadata,
        }

    return {
        "title": None,
        "title_source": "none",
        "title_confidence": 0.0,
        "title_page": None,
        "pages_scanned": limit,
        "pages_checked": pages_checked,
        "metadata": metadata,
    }


def _title_result_from_doc(
    doc: pymupdf.Document,
    *,
    source: str,
    max_pages: int = 2,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    resolved = resolve_document_title(doc, max_pages=max_pages)
    result = {
        "source": source,
        "page_count": doc.page_count,
        "is_encrypted": doc.is_encrypted,
        "document_title": resolved["title"],
        "document_title_source": resolved["title_source"],
        "document_title_confidence": resolved["title_confidence"],
        "document_title_page": resolved["title_page"],
        "pages_scanned_for_title": resolved["pages_scanned"],
        "title_pages_checked": resolved["pages_checked"],
        "metadata": resolved["metadata"],
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    return result


def extract_document_title_bytes(
    data: bytes,
    *,
    source: str = "upload.pdf",
    max_pages: int = 2,
) -> dict[str, Any]:
    """Resolve PDF title from in-memory bytes (first N pages only)."""
    if not data:
        raise ValueError("Empty PDF payload")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return _title_result_from_doc(doc, source=source, max_pages=max_pages)


def extract_document_title_only(
    pdf_path: Path,
    *,
    max_pages: int = 2,
) -> dict[str, Any]:
    """Resolve PDF title by scanning only the first N pages (default 2)."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    with pymupdf.open(pdf_path) as doc:
        return _title_result_from_doc(
            doc,
            source=str(pdf_path.resolve()),
            max_pages=max_pages,
        )


def extract_page(page: pymupdf.Page) -> dict[str, Any]:
    # Plain text via dedicated path for accuracy; dict used for titles
    text = page.get_text("text", sort=True).rstrip()
    lines = _line_objects(page)
    cover = detect_cover_document_title(lines, page.rect.height)
    info = detect_page_titles(lines, page.rect.height)
    largest = _largest_font_title(lines, page.rect.height)
    title_info = _pick_best_title_candidate(cover, info, largest) or {
        "page_title": None,
        "page_title_confidence": 0.0,
        "title_source": "none",
        "headings": [],
        "body_font_size": None,
    }

    tables: list[dict[str, Any]] = []
    try:
        finder = page.find_tables()
        for i, table in enumerate(finder.tables):
            tables.append(
                {
                    "index": i,
                    "bbox": [round(x, 2) for x in table.bbox],
                    "rows": table.extract(),
                }
            )
    except Exception:
        pass

    links = [
        {
            "kind": link.get("kind"),
            "uri": link.get("uri"),
            "page": link.get("page"),
            "from": [round(x, 2) for x in link.get("from", pymupdf.Rect())],
        }
        for link in page.get_links()
    ]

    annotations: list[dict[str, Any]] = []
    for annot in page.annots() or []:
        info = annot.info or {}
        annotations.append(
            {
                "type": annot.type[1] if annot.type else None,
                "content": info.get("content") or "",
                "title": info.get("title") or "",
                "rect": [round(x, 2) for x in annot.rect],
            }
        )

    images = [
        {
            "xref": img[0],
            "width": img[2],
            "height": img[3],
            "bpc": img[4],
            "colorspace": img[5],
            "name": img[7],
        }
        for img in page.get_images(full=True)
    ]

    return {
        "page": page.number + 1,
        "width": round(page.rect.width, 2),
        "height": round(page.rect.height, 2),
        "page_title": title_info["page_title"],
        "page_title_confidence": title_info["page_title_confidence"],
        "title_source": title_info["title_source"],
        "headings": title_info["headings"],
        "body_font_size": title_info.get("body_font_size"),
        "text": text,
        "char_count": len(text),
        "tables": tables,
        "links": links,
        "annotations": annotations,
        "images": images,
    }


def _extract_result_from_doc(
    doc: pymupdf.Document,
    *,
    source: str,
    title_max_pages: int = 2,
    include_full_text: bool = True,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    # Resolve document title from page 1→2 only (no full-PDF scan)
    doc_title = resolve_document_title(doc, max_pages=title_max_pages)
    pages = [extract_page(page) for page in doc]
    result: dict[str, Any] = {
        "source": source,
        "page_count": doc.page_count,
        "is_encrypted": doc.is_encrypted,
        "metadata": doc_title["metadata"],
        "document_title": doc_title["title"],
        "document_title_source": doc_title["title_source"],
        "document_title_confidence": doc_title["title_confidence"],
        "document_title_page": doc_title["title_page"],
        "pages_scanned_for_title": doc_title["pages_scanned"],
        "title_pages_checked": doc_title["pages_checked"],
        "pages": pages,
        "page_titles": [
            {
                "page": p["page"],
                "title": p["page_title"],
                "confidence": p["page_title_confidence"],
                "headings": [h["text"] for h in p["headings"]],
            }
            for p in pages
        ],
    }
    if include_full_text:
        result["full_text"] = "\n\n".join(
            (
                f"--- Page {p['page']} | title: {p['page_title'] or '(none)'} ---\n"
                f"{p['text']}"
            )
            for p in pages
            if p["text"]
        )
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return result


def extract_pdf_bytes(
    data: bytes,
    *,
    source: str = "upload.pdf",
    title_max_pages: int = 2,
    include_full_text: bool = True,
) -> dict[str, Any]:
    """Full embedded-text extract from in-memory PDF bytes."""
    if not data:
        raise ValueError("Empty PDF payload")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return _extract_result_from_doc(
            doc,
            source=source,
            title_max_pages=title_max_pages,
            include_full_text=include_full_text,
        )


def extract_pdf(pdf_path: Path) -> dict[str, Any]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pymupdf.open(pdf_path) as doc:
        return _extract_result_from_doc(
            doc,
            source=str(pdf_path.resolve()),
            title_max_pages=2,
            include_full_text=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract embedded PDF text/data + per-page titles (no OCR)."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=str(DEFAULT_PDF),
        help="Path to PDF (default: ./Demo.pdf)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON result to this path (default: stdout)",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Print concatenated plain text instead of JSON",
    )
    parser.add_argument(
        "--titles",
        action="store_true",
        help="Print document + per-page titles summary (full extract)",
    )
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="Resolve PDF title from page 1→2 only (no full extract)",
    )
    parser.add_argument(
        "--title-max-pages",
        type=int,
        default=2,
        help="Max early pages to scan for document title (default: 2)",
    )
    args = parser.parse_args()

    try:
        if args.title_only:
            data = extract_document_title_only(
                Path(args.pdf),
                max_pages=args.title_max_pages,
            )
        else:
            data = extract_pdf(Path(args.pdf))
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Place Demo.pdf in this folder (or pass a path) and run again.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error extracting PDF: {exc}", file=sys.stderr)
        return 1

    if args.title_only:
        payload = json.dumps(
            {
                "document_title": data["document_title"],
                "document_title_source": data["document_title_source"],
                "document_title_confidence": data["document_title_confidence"],
                "document_title_page": data["document_title_page"],
                "pages_scanned_for_title": data["pages_scanned_for_title"],
                "title_pages_checked": data["title_pages_checked"],
                "page_count": data["page_count"],
                "elapsed_ms": data["elapsed_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        print(
            f"Title-only: scanned {data['pages_scanned_for_title']}/"
            f"{data['page_count']} page(s) in {data['elapsed_ms']} ms | "
            f"doc_title={data['document_title']!r}",
            file=sys.stderr,
        )
        return 0

    if args.titles:
        lines = [
            f"Document title: {data['document_title']!r}",
            f"Source: {data['document_title_source']} "
            f"(page={data.get('document_title_page')}, "
            f"confidence={data['document_title_confidence']}, "
            f"scanned={data.get('pages_scanned_for_title')})",
            "",
            "Per-page titles:",
        ]
        for item in data["page_titles"]:
            heads = "; ".join(item["headings"][1:3])
            extra = f" | also: {heads}" if heads else ""
            lines.append(
                f"  p{item['page']:>3}: {item['title']!r} "
                f"(conf={item['confidence']}){extra}"
            )
        payload = "\n".join(lines)
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
    elif args.text_only:
        payload = data["full_text"]
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        else:
            print(payload)
    else:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        else:
            print(payload)

    print(
        f"Extracted {data['page_count']} page(s) in {data['elapsed_ms']} ms | "
        f"doc_title={data['document_title']!r} "
        f"(from page {data.get('document_title_page')}, "
        f"scanned {data.get('pages_scanned_for_title')} for title)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
