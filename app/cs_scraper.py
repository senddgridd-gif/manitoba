"""Manitoba Computer Science curriculum scraper (Grades 10-12).

Parses the PDF framework document with three course columns:
  - Senior 2 (20S) = Grade 10
  - Senior 3 (30S) = Grade 11
  - Senior 4 (40S) = Grade 12

Outcomes organised by 4 GLOs with SLO clusters underneath.
SLO codes: {glo}.{slo}.{num} e.g. 1.1.1, 3.2.4
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URL = "https://www.edu.gov.mb.ca/k12/cur/cs/framework.pdf"

# Column x-boundaries (determined by block analysis)
_COL_HEADER_MAX = 160   # GLO/SLO labels
_COL_SR2_MIN = 160
_COL_SR2_MAX = 300
_COL_SR3_MIN = 300
_COL_SR3_MAX = 440
_COL_SR4_MIN = 440
_COL_SR4_MAX = 590

# Outcome code pattern: N.N.N
_CODE_RE = re.compile(r"^(\d+\.\d+\.\d+)$")
_CODE_WITH_DESC_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+(.+)")

# GLO descriptions
GLO_DESCRIPTIONS = {
    "1": "Human Relations: Students will demonstrate tolerance, teamwork, leadership, and responsible, ethical, and moral behaviour.",
    "2": "Literacy and Communication: Students will demonstrate effective communication skills in listening, speaking, reading, writing, viewing, and representing.",
    "3": "Problem Solving: Students will demonstrate appropriate problem-solving skills while seeking solutions to technological challenges.",
    "4": "Programming: Students will design, write, and debug programs.",
}

# SLO titles (extracted from PDF)
SLO_TITLES = {
    "1.1": "Teamwork",
    "1.2": "Society and the Environment",
    "1.3": "Ethical Behaviour",
    "2.1": "Documentation",
    "2.2": "Oral Presentation",
    "2.3": "Careers",
    "2.4": "Project Management",
    "3.1": "Learning to Learn",
    "3.2": "Reasoning and Logic",
    "3.3": "Program Design",
    "3.4": "Testing and Debugging",
    "4.1": "Input, Processing, and Output",
    "4.2": "Data Structures",
    "4.3": "Control Structures",
    "4.4": "Object-Oriented Programming",
    "4.5": "Event-Driven Programming",
    "4.6": "Recursion",
    "4.7": "Sorting and Searching",
}


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def _classify_column(x0: float) -> str:
    """Classify a block's column based on its x position."""
    if x0 < _COL_HEADER_MAX:
        return "header"
    elif x0 < _COL_SR2_MAX:
        return "sr2"
    elif x0 < _COL_SR3_MAX:
        return "sr3"
    elif x0 < _COL_SR4_MAX:
        return "sr4"
    else:
        return "notes"


def _extract_column_outcomes(pdf_path: Path) -> dict[str, list[dict]]:
    """Extract outcomes per course column from the CS framework PDF.

    Returns dict: "sr2"|"sr3"|"sr4" -> list of {code, description, slo_key}
    """
    doc = fitz.open(str(pdf_path))

    # Collect text blocks per column, maintaining y-order
    col_blocks: dict[str, list[tuple[float, float, str]]] = {
        "header": [], "sr2": [], "sr3": [], "sr4": [], "notes": []
    }

    # Outcome pages are approximately 16-33 (0-indexed 15-32)
    for pg_idx in range(15, min(34, doc.page_count)):
        page = doc[pg_idx]
        page_text = page.get_text()
        if "General Learning Outcome" not in page_text and "SLO" not in page_text:
            continue

        blocks = page.get_text("dict")["blocks"]
        page_offset = pg_idx * 1000  # vertical offset per page

        for b in blocks:
            if "lines" not in b:
                continue
            x0 = b["bbox"][0]
            y0 = b["bbox"][1] + page_offset
            col = _classify_column(x0)

            text_parts = []
            for line in b["lines"]:
                t = " ".join(span["text"] for span in line["spans"]).strip()
                if t:
                    text_parts.append(t)
            full_text = " ".join(text_parts).strip()
            if full_text:
                col_blocks[col].append((y0, x0, full_text))

    doc.close()

    # Sort each column by y position
    for col in col_blocks:
        col_blocks[col].sort(key=lambda t: t[0])

    # Parse GLO/SLO structure from header column
    current_glo = ""
    current_slo_key = ""
    slo_y_ranges: list[tuple[float, str, str]] = []  # (y, slo_key, glo_num)

    for y, x, text in col_blocks["header"]:
        glo_m = re.match(r"General Learning Outcome\s+(\d+)", text)
        if glo_m:
            current_glo = glo_m.group(1)
            continue
        slo_m = re.match(r"SLO\s+(\d+\.\d+)", text)
        if slo_m:
            current_slo_key = slo_m.group(1)
            slo_y_ranges.append((y, current_slo_key, current_glo))

    # Parse outcomes from each course column
    results: dict[str, list[dict]] = {"sr2": [], "sr3": [], "sr4": []}

    for col_name in ["sr2", "sr3", "sr4"]:
        current_code = ""
        current_desc_parts: list[str] = []

        def _flush():
            nonlocal current_code, current_desc_parts
            if current_code:
                desc = " ".join(current_desc_parts).strip()
                desc = re.sub(r"\s+", " ", desc)
                if desc:
                    # Find which SLO this belongs to by code prefix
                    parts = current_code.split(".")
                    slo_key = f"{parts[0]}.{parts[1]}" if len(parts) >= 3 else ""
                    glo_num = parts[0] if parts else ""
                    results[col_name].append({
                        "code": current_code,
                        "description": desc,
                        "slo_key": slo_key,
                        "glo_num": glo_num,
                    })
            current_code = ""
            current_desc_parts = []

        for y, x, text in col_blocks[col_name]:
            # Skip column headers
            if "Senior" in text and "Computer" in text:
                continue
            if text == "Students will...":
                continue

            # Check for code with description on same line
            m = _CODE_WITH_DESC_RE.match(text)
            if m:
                _flush()
                current_code = m.group(1)
                current_desc_parts = [m.group(2)]
                continue

            # Check for standalone code
            m = _CODE_RE.match(text)
            if m:
                _flush()
                current_code = m.group(1)
                current_desc_parts = []
                continue

            # Continuation text
            if current_code and not text.startswith("SLO") and not text.startswith("General"):
                current_desc_parts.append(text)

        _flush()

    return results


def scrape_all_cs(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Computer Science Grades 10-12."""
    tmp_dir = Path("/tmp/cs_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    pdf_path = tmp_dir / "framework.pdf"

    if progress_callback:
        progress_callback("Downloading Computer Science framework PDF...")

    _download(PDF_URL, pdf_path)

    if progress_callback:
        progress_callback("Parsing Computer Science outcomes...")

    col_outcomes = _extract_column_outcomes(pdf_path)

    grade_map = {"sr2": "10", "sr3": "11", "sr4": "12"}
    course_map = {"sr2": "20S", "sr3": "30S", "sr4": "40S"}
    results: dict[str, list] = {}

    for col_name, grade in grade_map.items():
        outcomes = col_outcomes[col_name]

        # Group by GLO -> SLO
        glo_slo_groups: dict[str, dict[str, list[dict]]] = {}
        for o in outcomes:
            glo = o["glo_num"]
            slo = o["slo_key"]
            glo_slo_groups.setdefault(glo, {})
            glo_slo_groups[glo].setdefault(slo, [])
            glo_slo_groups[glo][slo].append(o)

        clusters: list[dict] = []
        for glo_num in sorted(glo_slo_groups.keys()):
            glo_desc = GLO_DESCRIPTIONS.get(glo_num, f"GLO {glo_num}")
            for slo_key in sorted(glo_slo_groups[glo_num].keys()):
                slo_title = SLO_TITLES.get(slo_key, f"SLO {slo_key}")
                slo_outcomes = glo_slo_groups[glo_num][slo_key]

                slos = []
                for o in slo_outcomes:
                    slos.append({
                        "code": o["code"],
                        "description": o["description"],
                        "glo": [f"GLO {glo_num}"],
                        "glo_description": [f"GLO {glo_num}: {glo_desc}"],
                    })

                clusters.append({
                    "id": f"SLO {slo_key}: {slo_title}",
                    "title": f"SLO {slo_key}: {slo_title}",
                    "description": f"GLO {glo_num}: {glo_desc}",
                    "specific_learning_outcomes": slos,
                })

        results[grade] = clusters

        output_data = {
            "subject": "Computer Science",
            "grade": grade,
            "course": f"Computer Science {course_map[col_name]}",
            "framework_year": "Framework 2014",
            "clusters": clusters,
        }

        filename = f"CompSci_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} SLOs, {total} outcomes")

    return results
