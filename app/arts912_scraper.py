"""Manitoba Arts Education Grades 9-12 scraper.

4 disciplines: Dance, Dramatic Arts, Music, Visual Arts.
Each has 4 Essential Learning Areas (Making, Creating, Connecting, Responding)
with 13 Recursive Learnings, each containing Enacted Learnings and Inquiry Questions.

Source PDFs: edu.gov.mb.ca/k12/cur/arts/docs/{disc}_9-12.pdf
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URLS = {
    "Dance": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/dance_9-12.pdf",
    "Dramatic Arts": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/dramatic_arts_9-12.pdf",
    "Music": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/music_9-12.pdf",
    "Visual Arts": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/visual_9-12.pdf",
}

AREAS = [
    ("M", "Making"),
    ("CR", "Creating"),
    ("C", "Connecting"),
    ("R", "Responding"),
]


def _download_pdf(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content


def _classify_area(code_suffix: str) -> str:
    """Return the area code (M, CR, C, R) for a recursive learning suffix."""
    if code_suffix.startswith("CR"):
        return "CR"
    if code_suffix.startswith("M"):
        return "M"
    if code_suffix.startswith("C"):
        return "C"
    if code_suffix.startswith("R"):
        return "R"
    return ""


def _extract_enacted_and_inquiries(doc, page_num: int) -> tuple[list[str], list[str]]:
    """Extract enacted learnings and inquiry questions from a recursive learning's pages."""
    enacted = []
    inquiries = []

    # Enacted learnings are on the RL page itself
    text = doc[page_num - 1].get_text()
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue
        if re.match(r"^[A-Z] [a-z] [a-z]", line):
            continue
        if line in ("Q", "?"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if line in ("Making", "Creating", "Connecting", "Responding"):
            continue
        if "Inquiry Questions" in line:
            continue
        # Skip the recursive learning description text (runs along the right edge)
        if re.match(r"^(The\s+lea|of\s+d|rner|deve|lops|comp|eten)", line):
            continue
        if len(line) < 10 and not line.startswith(("using", "creating", "developing")):
            continue
        enacted.append(line)

    # Inquiry questions are on the next page
    if page_num < len(doc):
        iq_text = doc[page_num].get_text()
        iq_lines = iq_text.split("\n")
        current_q = ""
        for line in iq_lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if re.match(r"^[A-Z] [a-z] [a-z]", line):
                continue
            if line in ("Q", "?"):
                continue
            if re.match(r"^\d+$", line):
                continue
            if "Inquiry Questions" == line:
                continue
            # Inquiry questions typically start with How, What, Why, In what, etc.
            if re.match(r"^(How|What|Why|In what|When|Where|Can|Do|Is|Are|Which)", line):
                if current_q:
                    inquiries.append(current_q)
                current_q = line
            elif current_q:
                current_q += " " + line
        if current_q:
            inquiries.append(current_q)

    return enacted, inquiries


def scrape_all_arts912(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Arts Education Grades 9-12 (Dance, Drama, Music, Visual Arts)."""
    results: dict[str, list] = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for disc_name, url in PDF_URLS.items():
        if progress_callback:
            progress_callback(f"Downloading {disc_name} 9-12 PDF...")

        try:
            pdf_bytes = _download_pdf(url)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading {disc_name}: {e}")
            continue

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        if progress_callback:
            progress_callback(f"Parsing {disc_name} 9-12 ({len(doc)} pages)...")

        toc = doc.get_toc()
        rl_entries = [(t[2], t[1]) for t in toc if t[0] == 3 and re.match(r"^[A-Z]+-", t[1])]

        # Group by Essential Learning Area
        area_outcomes: dict[str, list] = {}
        for page_num, code in rl_entries:
            suffix = code.split("-", 1)[1] if "-" in code else code
            area_code = _classify_area(suffix)
            area_name = dict(AREAS).get(area_code, area_code)

            enacted, inquiries = _extract_enacted_and_inquiries(doc, page_num)

            # Combine enacted learnings into a single description
            enacted_text = " ".join(enacted).replace("  ", " ").strip()

            outcome = {
                "code": code,
                "description": enacted_text,
                "glo": [f"Essential Learning Area: {area_name}"],
                "glo_description": [f"Essential Learning Area: {area_name}"],
                "enacted_learnings": enacted,
                "inquiry_questions": inquiries,
            }

            if area_name not in area_outcomes:
                area_outcomes[area_name] = []
            area_outcomes[area_name].append(outcome)

        clusters = []
        for area_code, area_name in AREAS:
            outcomes = area_outcomes.get(area_name, [])
            if outcomes:
                clusters.append({
                    "id": f"Essential Learning Area: {area_name}",
                    "title": area_name,
                    "description": f"The learner develops language and practices for {area_name.lower()} in {disc_name.lower()}.",
                    "specific_learning_outcomes": outcomes,
                })

        results[disc_name] = clusters

        output_data = {
            "subject": f"Arts Education - {disc_name}",
            "grade": "9-12",
            "course": f"9-12 {disc_name}",
            "framework_year": "Framework 2015",
            "clusters": clusters,
        }

        safe_name = disc_name.replace(" ", "_")
        filename = f"Arts_{safe_name}_9-12.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total_rl = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        total_enacted = sum(
            len(o["enacted_learnings"])
            for c in clusters
            for o in c["specific_learning_outcomes"]
        )
        total_iq = sum(
            len(o["inquiry_questions"])
            for c in clusters
            for o in c["specific_learning_outcomes"]
        )
        if progress_callback:
            progress_callback(
                f"Saved {filename}: {len(clusters)} areas, {total_rl} recursive learnings, "
                f"{total_enacted} enacted learnings, {total_iq} inquiry questions"
            )

    return results
