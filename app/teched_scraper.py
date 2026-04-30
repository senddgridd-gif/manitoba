"""Manitoba Technology Education — Applied Commerce Education (ACE) scraper.

Grades 9-12 with 5 strands:
  - Business Innovations (Gr 9, stand-alone)
  - Finance (Gr 10-12)
  - Entrepreneurship (Gr 10-12)
  - Commerce (Gr 10-12)
  - Technologies, Topics, and Trends (Gr 11-12)

SLO codes: {grade}.{goal}.{glo}.{num} e.g. 9.3.1.1
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URL = "https://www.edu.gov.mb.ca/k12/cur/teched/ace_framework/docs/full_doc.pdf"

_SLO_RE = re.compile(r"^(\d{1,2})\.(\d+)\.(\d+)\.(\d+)\s+(.*)")
_GOAL_RE = re.compile(r"^Goal\s+(\d+):\s+(.*)")
_GLO_RE = re.compile(r"^GLO\s+(\d+\.\d+):\s*(.*)")

SKIP_PATTERNS = [
    "Applied Commerce Education",
    "Grades 9 to 12",
    "Grades 10 to 12",
    "Grades 11 and 12",
    "Grade 9 Business",
    "Grade 10",
    "Grade 11",
    "Grade 12",
]


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def _parse_outcomes(pdf_path: Path) -> dict[str, list[dict]]:
    """Parse all ACE outcomes from the PDF.

    Returns dict keyed by grade -> list of outcome dicts.
    """
    doc = fitz.open(str(pdf_path))

    # Extract all text
    full_lines: list[str] = []
    for p in range(doc.page_count):
        text = doc[p].get_text()
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                full_lines.append(stripped)
    doc.close()

    # Parse goals, GLOs, and SLOs
    current_goal_num = ""
    current_goal_desc = ""
    current_glo_key = ""
    current_glo_desc = ""

    # grade -> code -> outcome dict
    grade_outcomes: dict[str, dict[str, dict]] = {}

    current_slo_grade = ""
    current_slo_code = ""
    current_slo_desc = ""

    def _flush_slo():
        nonlocal current_slo_code, current_slo_desc, current_slo_grade
        if current_slo_code and current_slo_grade and current_slo_desc:
            g = current_slo_grade
            desc = re.sub(r"\s+", " ", current_slo_desc).strip()
            grade_outcomes.setdefault(g, {})
            # Keep longest description for each code (dedup)
            existing = grade_outcomes[g].get(current_slo_code)
            if not existing or len(desc) > len(existing["description"]):
                grade_outcomes[g][current_slo_code] = {
                    "code": current_slo_code,
                    "description": desc,
                    "goal_num": current_goal_num,
                    "goal_desc": current_goal_desc,
                    "glo_key": current_glo_key,
                    "glo_desc": current_glo_desc,
                }
        current_slo_code = ""
        current_slo_desc = ""
        current_slo_grade = ""

    for line in full_lines:
        # Goal header
        goal_m = _GOAL_RE.match(line)
        if goal_m:
            _flush_slo()
            current_goal_num = goal_m.group(1)
            desc = goal_m.group(2).strip()
            if "(continued)" not in line:
                current_goal_desc = desc
            continue

        # GLO header
        glo_m = _GLO_RE.match(line)
        if glo_m:
            _flush_slo()
            current_glo_key = glo_m.group(1)
            current_glo_desc = glo_m.group(2).strip()
            continue

        # SLO code
        slo_m = _SLO_RE.match(line)
        if slo_m:
            _flush_slo()
            current_slo_grade = slo_m.group(1)
            code = f"{current_slo_grade}.{slo_m.group(2)}.{slo_m.group(3)}.{slo_m.group(4)}"
            current_slo_code = code
            current_slo_desc = slo_m.group(5).strip()
            continue

        # Continuation line for current SLO
        if current_slo_code:
            # Stop if we hit a structural element
            if any(line.startswith(s) for s in ["Goal ", "GLO ", "0315", "0309",
                   "0310", "0317", "0318", "0319", "0323", "0324", "0325",
                   "0316", "0327", "0311", "0314", "0326"]):
                _flush_slo()
                continue
            if re.match(r"^\d{1,2}\.\d+\.\d+\.\d+", line):
                continue  # will be handled above
            # Skip 12A/12B course codes that leak from adjacent columns
            if re.match(r"^\d{1,2}[AB]\.\d+\.\d+\.\d+", line):
                continue
            if any(s in line for s in SKIP_PATTERNS):
                _flush_slo()
                continue
            if re.match(r"^\d{1,3}$", line):  # page numbers
                continue
            # Skip course code/level lines
            if re.match(r"^\d{2}S\s*/\s*\d{2}E\s*/\s*\d{2}M$", line):
                continue
            current_slo_desc += " " + line

    _flush_slo()

    return grade_outcomes


def scrape_all_teched(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Technology Education — Applied Commerce Education (Grades 9-12)."""
    tmp_dir = Path("/tmp/teched_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    pdf_path = tmp_dir / "ace_full_doc.pdf"

    if progress_callback:
        progress_callback("Downloading ACE framework PDF...")

    _download(PDF_URL, pdf_path)

    if progress_callback:
        progress_callback("Parsing ACE outcomes...")

    grade_outcomes = _parse_outcomes(pdf_path)
    results: dict[str, list] = {}

    for grade in sorted(grade_outcomes.keys(), key=int):
        outcomes_dict = grade_outcomes[grade]
        outcomes = list(outcomes_dict.values())

        # Group by Goal -> GLO
        goal_glo_groups: dict[str, dict[str, list[dict]]] = {}
        for o in outcomes:
            g_num = o["goal_num"]
            glo_k = o["glo_key"]
            goal_glo_groups.setdefault(g_num, {})
            goal_glo_groups[g_num].setdefault(glo_k, [])
            goal_glo_groups[g_num][glo_k].append(o)

        clusters: list[dict] = []
        for goal_num in sorted(goal_glo_groups.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            for glo_key in sorted(goal_glo_groups[goal_num].keys()):
                glo_outcomes = goal_glo_groups[goal_num][glo_key]
                goal_desc = glo_outcomes[0]["goal_desc"] if glo_outcomes else ""
                glo_desc = glo_outcomes[0]["glo_desc"] if glo_outcomes else ""

                slos = []
                for o in glo_outcomes:
                    slos.append({
                        "code": o["code"],
                        "description": o["description"],
                        "glo": [f"GLO {glo_key}"],
                        "glo_description": [f"GLO {glo_key}: {glo_desc}"],
                    })

                clusters.append({
                    "id": f"Goal {goal_num} - GLO {glo_key}: {glo_desc}",
                    "title": f"GLO {glo_key}: {glo_desc}",
                    "description": f"Goal {goal_num}: {goal_desc}",
                    "specific_learning_outcomes": slos,
                })

        results[grade] = clusters

        output_data = {
            "subject": "Technology Education - Applied Commerce",
            "grade": grade,
            "course": f"{grade} Applied Commerce Education",
            "framework_year": "Framework 2020",
            "clusters": clusters,
        }

        filename = f"TechEd_ACE_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} GLOs, {total} outcomes")

    return results
