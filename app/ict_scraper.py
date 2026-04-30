"""Manitoba ICT (Information and Communication Technology) scraper.

Senior Years ICT is organised by COURSES, not grades. Each course
has a code, credit value, and level (15F/25S/35S).

15 courses across 3 levels:
  15F — Applying ICT 1, Applying ICT 2
  25S — Keyboarding, Print Communications, Digital Pictures, Digital Filmmaking
  35S — Desktop Publishing, Web Design, Interactive Websites, Data Collection
         and Analysis, Relational Databases, 2-D Animation, 3-D Modelling,
         Broadcast Media, Interactive Media

Plus common SLOs that apply to all courses.
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URL = "https://www.edu.gov.mb.ca/k12/cur/ict/docs/framework.pdf"

# Course metadata: (title, code, level, start_page 1-indexed, end_page 1-indexed)
COURSES = [
    ("Applying ICT 1", "0217", "15F", 19, 20),
    ("Applying ICT 2", "0218", "15F", 20, 21),
    ("Keyboarding", "1270", "25S", 21, 22),
    ("Print Communications", "0222", "25S", 22, 23),
    ("Digital Pictures", "0226", "25S", 23, 24),
    ("Digital Filmmaking", "0230", "25S", 24, 25),
    ("Desktop Publishing", "0223", "35S", 25, 26),
    ("Web Design", "0234", "35S", 26, 27),
    ("Interactive Websites", "0225", "35S", 27, 28),
    ("Data Collection and Analysis", "0254", "35S", 28, 29),
    ("Relational Databases", "0221", "35S", 29, 30),
    ("2-D Animation", "0227", "35S", 30, 31),
    ("3-D Modelling", "0236", "35S", 31, 32),
    ("Broadcast Media", "0231", "35S", 32, 33),
    ("Interactive Media", "0237", "35S", 33, 34),
]

# Common SLOs (pages 17-18) that apply to all courses
COMMON_SLOS_PAGES = (17, 18)

# GLO references in parentheses
_GLO_REF_RE = re.compile(r"\(([A-Z][a-z]?-\d+\.\d+)\)")

# GLO descriptions
GLO_DESCRIPTIONS = {
    "P": "Productivity: Students will use ICT to increase productivity.",
    "G": "Gathering: Students will use ICT to gather information.",
    "Pr": "Presenting: Students will use ICT to present information and ideas.",
    "C": "Communicating: Students will use ICT to communicate.",
    "R": "Reflecting: Students will use ICT to reflect on learning.",
    "S": "Social/Ethical: Students will use ICT responsibly.",
    "Co": "Collaborating: Students will use ICT to collaborate.",
    "M": "Managing: Students will use ICT to manage information.",
    "E": "Ethics: Students will use ICT in an ethical manner.",
}


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def _extract_numbered_slos(doc: fitz.Document, start_page: int, end_page: int) -> list[dict]:
    """Extract numbered SLOs from a range of pages.

    SLOs are numbered 1., 2., 3., etc. with descriptions that may
    span multiple lines and include bullet points.
    """
    full_text = ""
    for p in range(start_page - 1, min(end_page, doc.page_count)):
        text = doc[p].get_text()
        full_text += text + "\n"

    lines = full_text.split("\n")
    slos: list[dict] = []
    current_num = 0
    current_desc_parts: list[str] = []

    def _flush():
        nonlocal current_num, current_desc_parts
        if current_num > 0 and current_desc_parts:
            desc = " ".join(current_desc_parts).strip()
            desc = re.sub(r"\s+", " ", desc)
            # Extract GLO references
            glo_refs = _GLO_REF_RE.findall(desc)
            # Clean GLO refs from description
            clean_desc = _GLO_REF_RE.sub("", desc).strip()
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
            clean_desc = clean_desc.rstrip(".")

            glo_codes = []
            glo_descs = []
            for ref in glo_refs:
                prefix = ref.split("-")[0]
                glo_name = GLO_DESCRIPTIONS.get(prefix, f"GLO {prefix}")
                glo_codes.append(ref)
                glo_descs.append(glo_name)

            slos.append({
                "code": str(current_num),
                "description": clean_desc if clean_desc else desc,
                "glo": glo_codes if glo_codes else [],
                "glo_description": glo_descs if glo_descs else [],
            })
        current_num = 0
        current_desc_parts = []

    for line in lines:
        stripped = line.strip()

        # Skip headers
        if not stripped:
            continue
        if "Senior Years Information" in stripped:
            continue
        if stripped == "Specific Learning Outcomes":
            continue
        if stripped.startswith("Students will"):
            continue
        if stripped.startswith("Also see:"):
            _flush()
            break
        if "Common to All Courses" in stripped:
            break
        if stripped.startswith("on page"):
            continue

        # Skip course info (appears after SLOs)
        if stripped.startswith("Course Code"):
            _flush()
            break
        if stripped.startswith("Purpose"):
            _flush()
            break
        if stripped.startswith("Subject Description"):
            _flush()
            break

        # Numbered SLO start: "1." or "10."
        m = re.match(r"^(\d+)\.\s*(.*)", stripped)
        if m:
            _flush()
            current_num = int(m.group(1))
            rest = m.group(2).strip()
            if rest:
                current_desc_parts = [rest]
            continue

        # Bullet points (part of current SLO)
        if stripped.startswith("•") or stripped.startswith("–"):
            if current_num > 0:
                current_desc_parts.append(stripped)
            continue

        # Continuation line
        if current_num > 0:
            current_desc_parts.append(stripped)

    _flush()
    return slos


def scrape_all_ict(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape ICT Senior Years courses."""
    tmp_dir = Path("/tmp/ict_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    pdf_path = tmp_dir / "framework.pdf"

    if progress_callback:
        progress_callback("Downloading ICT framework PDF...")

    _download(PDF_URL, pdf_path)

    doc = fitz.open(str(pdf_path))

    # Extract common SLOs
    if progress_callback:
        progress_callback("Parsing common SLOs...")
    common_slos = _extract_numbered_slos(doc, COMMON_SLOS_PAGES[0], COMMON_SLOS_PAGES[1] + 1)

    results: dict[str, list] = {}

    for title, code, level, start_pg, end_pg in COURSES:
        if progress_callback:
            progress_callback(f"Parsing {title}...")

        course_slos = _extract_numbered_slos(doc, start_pg, end_pg)

        # Create cluster for course-specific SLOs
        clusters: list[dict] = []

        if course_slos:
            clusters.append({
                "id": f"{title} — Course Outcomes",
                "title": f"{title} — Course Outcomes",
                "description": f"Specific learning outcomes for {title} ({code}, {level})",
                "specific_learning_outcomes": [
                    {
                        "code": f"{code}.{s['code']}",
                        "description": s["description"],
                        "glo": s["glo"],
                        "glo_description": s["glo_description"],
                    }
                    for s in course_slos
                ],
            })

        # Add common SLOs as a second cluster
        clusters.append({
            "id": "Common to All ICT Courses",
            "title": "Common to All ICT Courses",
            "description": "SLOs that apply to all Senior Years ICT courses",
            "specific_learning_outcomes": [
                {
                    "code": f"COM.{s['code']}",
                    "description": s["description"],
                    "glo": s["glo"],
                    "glo_description": s["glo_description"],
                }
                for s in common_slos
            ],
        })

        # Use course code as key
        safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")
        results[safe_title] = clusters

        output_data = {
            "subject": "Information and Communication Technology",
            "grade": level,
            "course": f"{title} ({code})",
            "framework_year": "Framework 2007",
            "clusters": clusters,
        }

        filename = f"ICT_{safe_title}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {total} outcomes")

    doc.close()
    return results
