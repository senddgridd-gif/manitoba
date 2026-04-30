"""Manitoba Sustainable Tourism curriculum scraper (Grades 11-12).

Parses the PDF framework document which has two courses side-by-side:
  - 0301 Introduction to Tourism (Grade 11) — 30S/30E/30M
  - 0302 Sustainable Tourism (Grade 12) — 40S/40E/40M

Outcomes are organised by 8 Goals with GLOs underneath.
SLO codes: {grade}.{goal}.{glo}.{num} e.g. 11.1.1.1
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URL = "https://www.edu.gov.mb.ca/k12/cur/tourism/docs/full_doc.pdf"

# SLO code pattern: 11.1.1.1 or 12.2.3.1
_SLO_RE = re.compile(r"^(\d{2})\.(\d+)\.(\d+)\.(\d+)\s*(.*)")
# GLO pattern: GLO 1.1: ...
_GLO_RE = re.compile(r"^GLO\s+(\d+)\.(\d+):\s*(.*)")
# Goal pattern: Goal N: ...
_GOAL_RE = re.compile(r"^Goal\s+(\d+):\s*(.*)")

SKIP_PATTERNS = [
    "Grades 11 and 12 Sustainable Tourism",
    "Manitoba Curriculum Framework of Outcomes",
    "General and Specific Learning Outcomes",
    "0301", "0302",
    "Introduction to Tourism (11)",
    "Sustainable Tourism (12)",
    "30S/30E/30M", "40S/40E/40M",
]


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def _parse_outcomes(pdf_path: Path) -> dict[str, list[dict]]:
    """Parse outcomes from the Sustainable Tourism PDF.

    Returns dict keyed by grade ("11", "12") -> list of cluster dicts.
    """
    doc = fitz.open(str(pdf_path))
    # Extract text from outcome pages (pages 17-28, 0-indexed 16-27)
    full_text = ""
    for p in range(doc.page_count):
        text = doc[p].get_text()
        if "Goal" in text and ("GLO" in text or "11." in text or "12." in text):
            full_text += text + "\n"
    doc.close()

    lines = full_text.split("\n")

    # State
    current_goal_num = ""
    current_goal_desc = ""
    current_glo_key = ""  # "goal.glo" e.g. "1.1"
    current_glo_desc = ""

    # Accumulate SLOs per grade
    # grade -> goal_num -> glo_key -> list of SLO dicts
    grade_data: dict[str, dict[str, dict[str, list[dict]]]] = {
        "11": {}, "12": {}
    }
    glo_descs: dict[str, str] = {}  # glo_key -> description
    goal_descs: dict[str, str] = {}  # goal_num -> description

    current_slo_grade = ""
    current_slo_code = ""
    current_slo_desc = ""

    def _flush_slo():
        nonlocal current_slo_code, current_slo_desc, current_slo_grade
        if current_slo_code and current_slo_grade:
            g = current_slo_grade
            desc = re.sub(r"\s+", " ", current_slo_desc).strip()
            if desc:
                grade_data[g].setdefault(current_goal_num, {})
                grade_data[g][current_goal_num].setdefault(current_glo_key, [])
                grade_data[g][current_goal_num][current_glo_key].append({
                    "code": current_slo_code,
                    "description": desc,
                })
        current_slo_code = ""
        current_slo_desc = ""
        current_slo_grade = ""

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Skip headers/footers/blanks
        if not stripped or any(stripped.startswith(s) for s in SKIP_PATTERNS):
            i += 1
            continue
        # Skip page numbers
        if re.match(r"^\d{1,2}$", stripped):
            i += 1
            continue

        # Goal header
        goal_m = _GOAL_RE.match(stripped)
        if goal_m:
            _flush_slo()
            current_goal_num = goal_m.group(1)
            current_goal_desc = goal_m.group(2).rstrip(".")
            # Handle continuation and "(continued)"
            if "(continued)" not in stripped:
                goal_descs[current_goal_num] = current_goal_desc
            i += 1
            continue

        # GLO header
        glo_m = _GLO_RE.match(stripped)
        if glo_m:
            _flush_slo()
            goal_n = glo_m.group(1)
            glo_n = glo_m.group(2)
            current_glo_key = f"{goal_n}.{glo_n}"
            desc = glo_m.group(3).strip()
            # Remove CATT references
            desc = re.sub(r"\s*\(CATT[^)]*\)", "", desc).strip()
            current_glo_desc = desc
            glo_descs[current_glo_key] = desc
            # May continue on next line
            while i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if not nxt or _SLO_RE.match(nxt) or _GLO_RE.match(nxt) or _GOAL_RE.match(nxt):
                    break
                if nxt.startswith("(CATT"):
                    i += 1
                    continue
                current_glo_desc += " " + nxt
                glo_descs[current_glo_key] = current_glo_desc
                i += 1
            i += 1
            continue

        # SLO code
        slo_m = _SLO_RE.match(stripped)
        if slo_m:
            _flush_slo()
            current_slo_grade = slo_m.group(1)
            goal = slo_m.group(2)
            glo = slo_m.group(3)
            num = slo_m.group(4)
            current_slo_code = f"{current_slo_grade}.{goal}.{glo}.{num}"
            current_glo_key = f"{goal}.{glo}"
            current_goal_num = goal
            desc_start = slo_m.group(5).strip()
            # Remove CATT refs from description
            desc_start = re.sub(r"\s*\(CATT[^)]*\)", "", desc_start).strip()
            current_slo_desc = desc_start
            i += 1
            continue

        # Continuation line for current SLO
        if current_slo_code:
            line_clean = re.sub(r"\s*\(CATT[^)]*\)", "", stripped).strip()
            if line_clean:
                current_slo_desc += " " + line_clean
        i += 1

    _flush_slo()

    # Build cluster structure per grade
    results: dict[str, list[dict]] = {}
    for grade in ["11", "12"]:
        clusters: list[dict] = []
        for goal_num in sorted(grade_data[grade].keys(), key=int):
            goal_desc = goal_descs.get(goal_num, f"Goal {goal_num}")
            for glo_key in sorted(grade_data[grade][goal_num].keys()):
                glo_desc = glo_descs.get(glo_key, f"GLO {glo_key}")
                slos = grade_data[grade][goal_num][glo_key]
                cluster = {
                    "id": f"Goal {goal_num} - GLO {glo_key}: {glo_desc}",
                    "title": f"GLO {glo_key}: {glo_desc}",
                    "description": f"Goal {goal_num}: {goal_desc}",
                    "specific_learning_outcomes": [
                        {
                            "code": s["code"],
                            "description": s["description"],
                            "glo": [f"GLO {glo_key}"],
                            "glo_description": [f"GLO {glo_key}: {glo_desc}"],
                        }
                        for s in slos
                    ],
                }
                clusters.append(cluster)
        results[grade] = clusters

    return results


def scrape_all_sustour(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Sustainable Tourism Grades 11-12."""
    tmp_dir = Path("/tmp/sustour_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    pdf_path = tmp_dir / "full_doc.pdf"

    if progress_callback:
        progress_callback("Downloading Sustainable Tourism PDF...")

    _download(PDF_URL, pdf_path)

    if progress_callback:
        progress_callback("Parsing Sustainable Tourism outcomes...")

    results = _parse_outcomes(pdf_path)

    for grade, clusters in results.items():
        course_name = "Introduction to Tourism" if grade == "11" else "Sustainable Tourism"
        output_data = {
            "subject": "Sustainable Tourism",
            "grade": grade,
            "course": f"{grade} {course_name}",
            "framework_year": "Framework 2013",
            "clusters": clusters,
        }

        filename = f"SustTourism_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} GLOs, {total} outcomes")

    return results
