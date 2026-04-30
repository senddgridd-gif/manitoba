"""Manitoba Legacy Physical Education / Health Education curriculum scraper.

Parses K-10 grade-specific HTML pages from the Manitoba PE/HE website.
Each page contains outcomes organised under 5 General Learning Outcomes (GLOs):
  1 – Movement
  2 – Fitness Management
  3 – Safety
  4 – Personal and Social Management
  5 – Healthy Lifestyle Practices

Outcome codes follow the pattern: {K|S}.{GLO}.{Grade}.{SubStrand}.{Number}
  K = Knowledge outcome, S = Skills outcome
"""

import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.edu.gov.mb.ca/k12/cur/physhlth"

GRADE_PAGES: dict[str, str] = {
    "K": f"{BASE_URL}/kindergarten.html",
    "1": f"{BASE_URL}/grade_1.html",
    "2": f"{BASE_URL}/grade_2.html",
    "3": f"{BASE_URL}/grade_3.html",
    "4": f"{BASE_URL}/grade_4.html",
    "5": f"{BASE_URL}/grade_5.html",
    "6": f"{BASE_URL}/grade_6.html",
    "7": f"{BASE_URL}/grade_7.html",
    "8": f"{BASE_URL}/grade_8.html",
    "9": f"{BASE_URL}/grade_9.html",
    "10": f"{BASE_URL}/grade_10.html",
}

GLO_NAMES: dict[str, str] = {
    "1": "Movement",
    "2": "Fitness Management",
    "3": "Safety",
    "4": "Personal and Social Management",
    "5": "Healthy Lifestyle Practices",
}

GLO_DESCRIPTIONS: dict[str, str] = {
    "1": (
        "The student will demonstrate competency in selected movement skills, "
        "and knowledge of movement development and physical activities with "
        "respect to different types of learning experiences, environments, and cultures."
    ),
    "2": (
        "The student will demonstrate the ability to develop and follow a personal "
        "fitness plan for lifelong physical activity and well-being."
    ),
    "3": (
        "The student will demonstrate safe and responsible behaviours to manage "
        "risks and prevent injuries in physical activity and daily living."
    ),
    "4": (
        "The student will demonstrate the ability to develop self-understanding, "
        "to make health-enhancing decisions, to work cooperatively and fairly "
        "with others, and to build positive relationships with others."
    ),
    "5": (
        "The student will demonstrate the ability to make informed decisions "
        "for healthy living related to personal health practices, active living, "
        "healthy nutritional practices, substance use and abuse prevention, "
        "and human sexuality."
    ),
}

# Regex to match outcome codes: K.1.K.A.1, S.2.5.B.3a, K.1.S1.A.1, etc.
CODE_PATTERN = re.compile(r"([KS])\.(\d)\.(\d+|K|S\d)\.([A-Z])\.(\d+[a-z]?)")


def _fetch_html(url: str) -> str:
    """Download an HTML page."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_grade_html(html: str, grade: str) -> dict[str, list[dict]]:
    """Parse a PE/HE grade page and return outcomes grouped by GLO.

    Returns dict mapping GLO number (str) -> list of outcome dicts.
    """
    outcomes_by_glo: dict[str, list[dict]] = {
        "1": [], "2": [], "3": [], "4": [], "5": [],
    }

    # Split into paragraphs — each <p> contains one outcome
    paragraphs = re.split(r"<p[^>]*>", html)

    for para in paragraphs:
        para = para.split("</p>")[0] if "</p>" in para else para

        # Find all outcome codes in this paragraph
        codes_found = CODE_PATTERN.findall(para)
        if not codes_found:
            continue

        # Use the first matching code as the primary code
        # Format: (type, glo, grade, substrand, number)
        primary = codes_found[0]
        code_str = f"{primary[0]}.{primary[1]}.{primary[2]}.{primary[3]}.{primary[4]}"
        glo_num = primary[1]

        # Extract description: remove HTML tags and clean
        desc = re.sub(r"<[^>]+>", " ", para)
        desc = _clean_text(desc)

        # Remove the code itself from the description
        # There may be multiple codes (e.g., K.1.K.B.2 + K.1.1.B.2)
        for c in codes_found:
            full_code = f"{c[0]}.{c[1]}.{c[2]}.{c[3]}.{c[4]}"
            desc = desc.replace(full_code, "")
        desc = _clean_text(desc)

        if not desc:
            continue

        outcome = {
            "code": code_str,
            "description": desc,
            "glo": [GLO_NAMES.get(glo_num, f"GLO {glo_num}")],
            "glo_description": [GLO_DESCRIPTIONS.get(glo_num, "")],
        }

        if glo_num in outcomes_by_glo:
            outcomes_by_glo[glo_num].append(outcome)

    return outcomes_by_glo


def scrape_all_pehe(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Physical Education / Health Education K-10 from HTML pages."""
    results: dict[str, list] = {}

    for grade, url in GRADE_PAGES.items():
        display_grade = grade
        if progress_callback:
            progress_callback(f"Downloading PE/HE Grade {display_grade} page...")

        try:
            html = _fetch_html(url)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading Grade {display_grade}: {e}")
            continue

        if progress_callback:
            progress_callback(f"Parsing PE/HE Grade {display_grade}...")

        outcomes_by_glo = _parse_grade_html(html, grade)

        # Build clusters (one per GLO)
        clusters: list[dict] = []
        for glo_num in ["1", "2", "3", "4", "5"]:
            outcomes = outcomes_by_glo.get(glo_num, [])
            if not outcomes:
                continue
            title = GLO_NAMES[glo_num]
            clusters.append({
                "id": title,
                "title": title,
                "description": GLO_DESCRIPTIONS.get(glo_num, ""),
                "specific_learning_outcomes": outcomes,
            })

        results[display_grade] = clusters

        output_data = {
            "subject": "Physical Education/Health Education",
            "grade": display_grade,
            "course": f"{display_grade} Physical Education/Health Education",
            "framework_year": "Framework 2000",
            "clusters": clusters,
        }

        filename = f"PEHE_Grade_{display_grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        if progress_callback:
            total_outcomes = sum(len(c["specific_learning_outcomes"]) for c in clusters)
            progress_callback(
                f"Saved {filename}: {len(clusters)} GLOs, {total_outcomes} outcomes"
            )

    return results
