"""Manitoba Career Development curriculum scraper (Grades 9-12).

Uses position-based column extraction from two-column PDFs to get
full-credit course outcomes organized by Units and GLOs.
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.edu.gov.mb.ca/k12/cur/cardev/docs"

GRADE_PDFS: dict[str, str] = {
    "9": f"{BASE_URL}/gr9_half.pdf",
    "10": f"{BASE_URL}/gr10_half.pdf",
    "11": f"{BASE_URL}/gr11_half.pdf",
    "12": f"{BASE_URL}/gr12_half.pdf",
}

GLO_FULL_DESCRIPTIONS: dict[str, str] = {
    "A": "Build and maintain a positive self-image.",
    "B": "Interact positively and effectively with others.",
    "C": "Change and grow throughout life.",
    "D": "Locate and effectively use life/work information.",
    "E": "Understand the relationship between work and society/economy.",
    "F": "Maintain balanced life and work roles.",
    "G": "Understand the changing nature of life/work roles.",
    "H": "Participate in lifelong learning supportive of life/work goals.",
    "I": "Make life/work enhancing decisions.",
    "J": "Understand, engage in, and manage own life/work building process.",
    "K": "Secure/create and maintain work.",
    "L": "Understand, engage in, and manage one's own life/work building process.",
    "M": "Locate and effectively use life/work information.",
}

COLUMN_THRESHOLD = 300  # x < 300 = left column (full-credit)


def _download_pdf(url: str, dest: Path) -> Path:
    """Download a PDF file."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def _extract_left_column_lines(pdf_path: Path) -> list[str]:
    """Extract text lines from the left column only (full-credit course)."""
    doc = fitz.open(str(pdf_path))
    lines: list[str] = []

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            x0 = block["bbox"][0]
            if x0 >= COLUMN_THRESHOLD:
                continue
            for line in block["lines"]:
                text = " ".join(span["text"] for span in line["spans"]).strip()
                if text:
                    # Remove PDF bullet characters and other non-ASCII artifacts
                    text = text.lstrip("\uf0a7\uf0b7\uf0a8\u2022\u2023\u25cf ")
                    text = text.strip()
                    if text:
                        lines.append(text)

    doc.close()
    return lines


def _parse_outcomes(lines: list[str]) -> list[dict]:
    """Parse Unit/GLO/outcome structure from left-column lines."""
    current_unit = ""
    current_glo_code = ""
    current_glo_desc = ""
    current_outcome: dict | None = None
    all_outcomes: list[dict] = []

    skip_patterns = [
        "Manitoba Education", "Current as of", "This document",
        "website at", "half-credit", "full-credit", "developed for",
        "Full-Credit Course", "Half-Credit Course",
    ]

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip page numbers and headers
        if re.match(r"^\d+$", line):
            i += 1
            continue
        if any(pat.lower() in line.lower() for pat in skip_patterns):
            i += 1
            continue

        # Unit header
        m = re.match(r"Unit\s+(\d+):\s+(.+)", line)
        if m:
            if current_outcome:
                all_outcomes.append(current_outcome)
                current_outcome = None
            current_unit = m.group(2)
            i += 1
            continue

        # GLO header (may span multiple lines)
        m = re.match(r"GLO\s+([A-Z]):\s+(.+)", line)
        if m:
            if current_outcome:
                all_outcomes.append(current_outcome)
                current_outcome = None
            current_glo_code = m.group(1)
            current_glo_desc = m.group(2)
            # Check for continuation lines
            while i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if not nxt or re.match(r"(\d+\.[A-Z]\.\d+|GLO|Unit)", nxt):
                    break
                if len(nxt) < 80:
                    current_glo_desc += " " + nxt
                    i += 1
                else:
                    break
            i += 1
            continue

        # Outcome code: N.X.N
        m = re.match(r"(\d+\.[A-Z]\.\d+)\s+(.+)", line)
        if m:
            if current_outcome:
                all_outcomes.append(current_outcome)
            current_outcome = {
                "code": m.group(1),
                "description": m.group(2),
                "unit": current_unit,
                "glo_code": current_glo_code,
                "glo_desc": current_glo_desc,
            }
            i += 1
            continue

        # Continuation line for current outcome description
        if current_outcome and not line.startswith("GLO") and not line.startswith("Unit"):
            current_outcome["description"] += " " + line
            i += 1
            continue

        i += 1

    if current_outcome:
        all_outcomes.append(current_outcome)

    # Deduplicate by code
    seen: set[str] = set()
    unique: list[dict] = []
    for o in all_outcomes:
        if o["code"] not in seen:
            seen.add(o["code"])
            unique.append(o)

    return unique


def scrape_all_cardev(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Career Development Grades 9-12."""
    results: dict[str, list] = {}
    tmp_dir = Path("/tmp/cardev_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    for grade, url in GRADE_PDFS.items():
        if progress_callback:
            progress_callback(f"Downloading Career Dev Grade {grade} PDF...")

        try:
            pdf_path = tmp_dir / f"gr{grade}.pdf"
            _download_pdf(url, pdf_path)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading Grade {grade}: {e}")
            continue

        if progress_callback:
            progress_callback(f"Parsing Career Dev Grade {grade}...")

        lines = _extract_left_column_lines(pdf_path)
        outcomes = _parse_outcomes(lines)

        # Group outcomes by Unit (as clusters)
        units: dict[str, list[dict]] = {}
        for o in outcomes:
            units.setdefault(o["unit"], [])
            units[o["unit"]].append(o)

        clusters: list[dict] = []
        for unit_title, unit_outcomes in units.items():
            slos = []
            for o in unit_outcomes:
                glo_code = o["glo_code"]
                glo_desc = GLO_FULL_DESCRIPTIONS.get(
                    glo_code, o.get("glo_desc", "")
                )
                slos.append({
                    "code": o["code"],
                    "description": o["description"],
                    "glo": [f"GLO {glo_code}"],
                    "glo_description": [f"GLO {glo_code}: {glo_desc}"],
                })
            clusters.append({
                "id": unit_title,
                "title": unit_title,
                "description": "",
                "specific_learning_outcomes": slos,
            })

        results[grade] = clusters

        output_data = {
            "subject": "Career Development",
            "grade": grade,
            "course": f"{grade} Career Development",
            "framework_year": "Framework 2014",
            "clusters": clusters,
        }

        filename = f"CareerDev_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total_outcomes = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(
                f"Saved {filename}: {len(clusters)} units, {total_outcomes} outcomes"
            )

    return results
