"""Manitoba Legacy Science curriculum PDF scraper.

Parses K-4, 5-8, S1, and S2 Science PDFs to extract cluster-based SLOs with GLO references.

PDF layout note: In the MB Science PDFs, the cluster heading (e.g., "Kindergarten, Cluster 1: Trees")
appears as a SIDE LABEL after the SLOs on the page. So we use a two-pass approach:
1. First pass: extract all cluster headings and their titles/descriptions
2. Second pass: extract all SLOs and assign them to clusters based on the cluster number
   embedded in the SLO code (e.g., K-1-01 → cluster 1, K-2-03 → cluster 2)
"""

import re
import json
import logging
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF
import httpx

from app.glo_definitions import SCIENCE_GLOS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.edu.gov.mb.ca/k12/cur/science/outcomes"

SCIENCE_PDFS: dict[str, dict] = {
    "K-4": {
        "full_doc": f"{BASE_URL}/k-4/full_doc.pdf",
        "per_grade": {
            "K": f"{BASE_URL}/k-4/grade_k.pdf",
            "1": f"{BASE_URL}/k-4/grade1.pdf",
            "2": f"{BASE_URL}/k-4/grade2.pdf",
            "3": f"{BASE_URL}/k-4/grade3.pdf",
            "4": f"{BASE_URL}/k-4/grade4.pdf",
        },
        "framework_year": "Framework 1999",
    },
    "5-8": {
        "full_doc": f"{BASE_URL}/5-8/full_doc.pdf",
        "per_grade": {
            "5": f"{BASE_URL}/5-8/gr5.pdf",
            "6": f"{BASE_URL}/5-8/gr6.pdf",
            "7": f"{BASE_URL}/5-8/gr7.pdf",
            "8": f"{BASE_URL}/5-8/gr8.pdf",
        },
        "framework_year": "Framework 2000",
    },
    "S1": {
        "full_doc": f"{BASE_URL}/s1/full_doc.pdf",
        "per_grade": {
            "10": f"{BASE_URL}/s1/outcomes.pdf",
        },
        "framework_year": "Framework 2000",
    },
    "S2": {
        "full_doc": f"{BASE_URL}/s2/slo.pdf",
        "per_grade": {
            "11": f"{BASE_URL}/s2/slo.pdf",
        },
        "framework_year": "Framework 2001",
    },
}

# SLO code patterns:
# K-4: K-0-1a (cluster 0, skill), K-1-01 (cluster 1), 1-2-03 (grade 1, cluster 2)
# 5-8: 5-0-1a, 5-1-01, 6-2-03
# S1:  S1-0-1a, S1-1-01, S1-2-03
# The middle number is ALWAYS the cluster number.
# Cluster 0 codes use letter suffixes: X-0-1a, X-0-2b
# Thematic cluster codes use two-digit numbers: X-1-01, X-2-03

SLO_CODE_PATTERN = re.compile(
    r"^((?:S[12]|[K12345678])-(\d)-(\d{2}[a-z]?|\d[a-z]))\b"
)

CLUSTER_HEADING_PATTERN = re.compile(
    r"(?:Kindergarten|Grade\s+\d+|Senior\s+\d),?\s+Cluster\s+(\d+):\s*(.+)",
    re.IGNORECASE,
)

GLO_REF_PATTERN = re.compile(r"GLO:\s*([A-E]\d(?:\s*,\s*[A-E]\d)*)")

# Lines to skip (page headers/footers)
SKIP_PATTERNS = [
    re.compile(r"^\d+\.\d+$"),  # Page numbers like 3.10
    re.compile(r"^Specific Learning Outcomes$"),
    re.compile(r"^K[–-]\d+ Science$"),
    re.compile(r"^\d+-\d+ Science$"),  # 5-8 Science
    re.compile(r"^Senior \d+ Science$"),
    re.compile(r"^Students will"),
    re.compile(r"^Scientific Inquiry$"),
    re.compile(r"^Design Process$"),
    re.compile(r"^Initiating$"),
    re.compile(r"^Implementing a Plan"),
    re.compile(r"^Researching$"),
    re.compile(r"^Planning$"),
    re.compile(r"^Observing, Measuring"),
    re.compile(r"^Analysing and Interpreting"),
    re.compile(r"^Concluding and Applying"),
    re.compile(r"^Reflecting on Science"),
    re.compile(r"^and Technology$"),
    re.compile(r"^Demonstrating Scientific"),
    re.compile(r"^and Technological Attitudes"),
    re.compile(r"^\* Cluster 0"),
]


def _grade_prefix(grade: str) -> str:
    """Return the SLO code prefix for a given grade."""
    if grade == "K":
        return "K-"
    if grade in ("1", "2", "3", "4", "5", "6", "7", "8"):
        return f"{grade}-"
    if grade == "10":
        return "S1-"
    if grade == "11":
        return "S2-"
    return ""


def _is_skip_line(line: str) -> bool:
    """Check if a line should be skipped (header/footer/metadata)."""
    for pattern in SKIP_PATTERNS:
        if pattern.match(line):
            return True
    return False


def download_pdf(url: str) -> bytes:
    """Download a PDF from the given URL."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url, headers={
            "User-Agent": "MB-Curriculum-Scraper/1.0 (educational research)"
        })
        resp.raise_for_status()
        return resp.content


def _clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace, fix ligatures, etc."""
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2026", "...")
    # Remove cross-references like (ELA 1.2.4, 3.1.2) or (Math SS-VI.0.1) or (TFS 2.2.1)
    text = re.sub(r"\((?:ELA|Math|TFS|SS|SP|PR)\s[^)]+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_cluster_number_from_code(code: str) -> str:
    """Extract the cluster number from an SLO code.

    K-1-01 → '1', K-0-1a → '0', S1-2-03 → '2', 5-3-01 → '3'
    """
    match = SLO_CODE_PATTERN.match(code)
    if match:
        return match.group(2)
    return "0"


def _grade_label(grade: str) -> str:
    """Return the grade label used in cluster headings."""
    if grade == "K":
        return "Kindergarten"
    if grade in ("1", "2", "3", "4", "5", "6", "7", "8"):
        return f"Grade {grade}"
    if grade == "10":
        return "Senior 1"
    if grade == "11":
        return "Senior 2"
    return ""


def _parse_grade_slos(all_text: str, grade: str) -> list[dict]:
    """Parse all SLOs for a specific grade from the document text.

    Two-pass approach:
    1. Find all cluster headings to get cluster titles and descriptions
    2. Extract all SLOs and assign to clusters by the cluster number in the code
    """
    prefix = _grade_prefix(grade)
    label = _grade_label(grade)
    lines = all_text.split("\n")

    # Build a pattern that only matches THIS grade's cluster headings
    grade_cluster_pattern = re.compile(
        rf"^{re.escape(label)},?\s+Cluster\s+(\d+):\s*(.+)",
        re.IGNORECASE,
    )

    # --- Pass 1: Find cluster headings for THIS grade only ---
    cluster_info: dict[str, dict] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        cluster_match = grade_cluster_pattern.match(stripped)
        if cluster_match:
            cluster_num = cluster_match.group(1)
            cluster_title = cluster_match.group(2).strip()
            # Remove "(continued)" artifacts from PDF pagination
            cluster_title = re.sub(r"\s*\(continued\)\s*$", "", cluster_title, flags=re.IGNORECASE)
            overview = _find_overview_near(lines, i, prefix)
            # Only store if we don't already have this cluster (first match wins)
            if cluster_num not in cluster_info:
                cluster_info[cluster_num] = {
                    "title": cluster_title,
                    "description": overview,
                }

    # --- Pass 2: Extract all SLOs for this grade ---
    slo_by_cluster: dict[str, list[dict]] = defaultdict(list)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Try to match SLO code at start of line
        slo_match = SLO_CODE_PATTERN.match(stripped)
        if slo_match and stripped.startswith(prefix):
            code = slo_match.group(1)
            cluster_num = slo_match.group(2)

            # Collect the SLO description
            desc_lines = []
            glo_refs: list[str] = []

            # Rest of current line after the code
            rest = stripped[len(code):].strip()
            # Strip leading period and/or period-space (SLO codes often followed by ". ")
            rest = re.sub(r"^\.?\s*", "", rest)
            if rest:
                desc_lines.append(rest)

            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()

                # Stop at next SLO code for this grade
                next_slo = SLO_CODE_PATTERN.match(next_line)
                if next_slo and next_line.startswith(prefix):
                    break

                # Check for GLO reference
                glo_match = GLO_REF_PATTERN.search(next_line)
                if glo_match:
                    refs = [r.strip() for r in glo_match.group(1).split(",")]
                    glo_refs.extend(refs)
                    j += 1
                    continue

                # Skip headers/footers and cluster headings
                if _is_skip_line(next_line):
                    j += 1
                    continue
                if CLUSTER_HEADING_PATTERN.search(next_line):
                    j += 1
                    continue

                if next_line:
                    # Skip cross-references like (ELA 1.2.4)
                    cleaned = re.sub(r"^\((?:ELA|Math|TFS|SS|SP|PR)\s[^)]+\)\s*", "", next_line)
                    if cleaned:
                        desc_lines.append(cleaned)

                j += 1

            description = _clean_text(" ".join(desc_lines))

            # Build GLO descriptions
            glo_descriptions = []
            for ref in glo_refs:
                glo_text = SCIENCE_GLOS.get(ref, "")
                if glo_text:
                    glo_descriptions.append(f"{ref}. {glo_text}")

            slo = {
                "code": code,
                "description": description,
                "glo": glo_refs,
                "glo_description": glo_descriptions,
            }
            slo_by_cluster[cluster_num].append(slo)
            i = j
            continue

        i += 1

    # --- Build cluster list ---
    clusters: list[dict] = []
    all_cluster_nums = sorted(set(list(cluster_info.keys()) + list(slo_by_cluster.keys())))

    for cluster_num in all_cluster_nums:
        info = cluster_info.get(cluster_num, {})
        title = info.get("title", "Overall Skills and Attitudes" if cluster_num == "0" else f"Cluster {cluster_num}")
        description = info.get("description", "")
        slos = slo_by_cluster.get(cluster_num, [])

        clusters.append({
            "id": title,
            "title": title,
            "description": description,
            "specific_learning_outcomes": slos,
        })

    return clusters


def _find_overview_near(lines: list[str], heading_idx: int, prefix: str) -> str:
    """Find the overview text near a cluster heading.

    The overview typically appears right after the heading or on the next page,
    starting with 'Overview' and ending before the first SLO code.
    """
    overview_lines = []
    found_overview = False

    # Search forward from the heading
    for j in range(heading_idx + 1, min(heading_idx + 40, len(lines))):
        stripped = lines[j].strip()

        # Stop if we hit another cluster heading
        if CLUSTER_HEADING_PATTERN.search(stripped):
            break

        # Stop if we hit an SLO code
        if SLO_CODE_PATTERN.match(stripped) and stripped.startswith(prefix):
            break

        if stripped.lower().startswith("overview"):
            found_overview = True
            rest = stripped[8:].strip()
            if rest:
                overview_lines.append(rest)
            continue

        if found_overview:
            if _is_skip_line(stripped):
                continue
            if stripped.lower().startswith("students will"):
                break
            if stripped:
                overview_lines.append(stripped)

    # Also search backward (overview often appears BEFORE the heading in the PDF text)
    if not overview_lines:
        for j in range(heading_idx - 1, max(heading_idx - 40, 0), -1):
            stripped = lines[j].strip()
            if stripped.lower().startswith("overview"):
                found_overview = True
                # Now read forward from overview
                for k in range(j + 1, heading_idx):
                    s = lines[k].strip()
                    if s.lower().startswith("students will"):
                        break
                    if _is_skip_line(s):
                        continue
                    if SLO_CODE_PATTERN.match(s):
                        break
                    if s:
                        overview_lines.append(s)
                break

    return _clean_text(" ".join(overview_lines))


def _find_grade_page_ranges(
    toc: list,
    page_count: int,
    all_pages_text: list[str] | None = None,
) -> dict[str, tuple[int, int]]:
    """From the PDF TOC or page text, find start/end page ranges for each grade section."""
    grade_ranges: dict[str, tuple[int, int]] = {}
    grade_entries = []

    # Try TOC first
    for level, title, page in toc:
        if re.match(r"^Kindergarten$", title.strip(), re.IGNORECASE):
            grade_entries.append(("K", page - 1))
        grade_match = re.match(r"^Grade\s+(\d+)$", title.strip(), re.IGNORECASE)
        if grade_match:
            grade_entries.append((grade_match.group(1), page - 1))

    # If no grade entries in TOC, scan page text for "Grade X, Cluster 0" headings
    if not grade_entries and all_pages_text:
        grade_cluster0_pattern = re.compile(
            r"(?:Grade\s+(\d+)|Senior\s+(\d)),?\s+Cluster\s+0:",
            re.IGNORECASE,
        )
        seen_grades = set()
        for page_idx, page_text in enumerate(all_pages_text):
            for line in page_text.split("\n"):
                m = grade_cluster0_pattern.search(line.strip())
                if m:
                    grade_num = m.group(1) or str(int(m.group(2)) + 9)
                    if grade_num not in seen_grades:
                        seen_grades.add(grade_num)
                        grade_entries.append((grade_num, page_idx))

    for idx, (grade, start_page) in enumerate(grade_entries):
        if idx + 1 < len(grade_entries):
            end_page = grade_entries[idx + 1][1]
        else:
            end_page = page_count
        grade_ranges[grade] = (start_page, end_page)

    return grade_ranges


def scrape_science_band(
    band: str,
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape a Science grade band (K-4, 5-8, or S1).

    Returns dict of {grade: [clusters]}.
    """
    config = SCIENCE_PDFS[band]
    pdf_url = config["full_doc"]
    framework_year = config["framework_year"]

    if progress_callback:
        progress_callback(f"Downloading {band} Science PDF...")

    pdf_bytes = download_pdf(pdf_url)

    if progress_callback:
        progress_callback(f"Parsing {band} Science PDF...")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        toc = doc.get_toc()
        all_pages_text = [doc[p].get_text() for p in range(doc.page_count)]
        grade_ranges = _find_grade_page_ranges(toc, doc.page_count, all_pages_text)
    finally:
        doc.close()

    results: dict[str, list] = {}
    grades_in_band = list(config["per_grade"].keys())

    if not grade_ranges:
        # Fallback: parse the whole document
        full_text = "\n".join(all_pages_text)
        for grade in grades_in_band:
            if progress_callback:
                progress_callback(f"Parsing Grade {grade} SLOs...")
            clusters = _parse_grade_slos(full_text, grade)
            if clusters:
                results[grade] = clusters
    else:
        for grade in grades_in_band:
            if grade not in grade_ranges:
                full_text = "\n".join(all_pages_text)
                clusters = _parse_grade_slos(full_text, grade)
                if clusters:
                    results[grade] = clusters
                continue

            start_page, end_page = grade_ranges[grade]
            grade_text = "\n".join(all_pages_text[start_page:end_page])
            if progress_callback:
                progress_callback(f"Parsing Grade {grade} SLOs (pages {start_page+1}-{end_page})...")
            clusters = _parse_grade_slos(grade_text, grade)
            results[grade] = clusters

    # Save each grade as a separate JSON file
    for grade, clusters in results.items():
        pdf_url_for_grade = config["per_grade"].get(grade, config["full_doc"])

        course_label = f"{grade} Science" if grade != "K" else "K-4 Science"
        output_data = {
            "subject": "Science",
            "grade": grade,
            "course": course_label,
            "framework_year": framework_year,
            "clusters": clusters,
        }

        filename = f"Science_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        if progress_callback:
            total_slos = sum(len(c["specific_learning_outcomes"]) for c in clusters)
            progress_callback(
                f"Saved {filename}: {len(clusters)} clusters, {total_slos} SLOs"
            )

    return results


def scrape_all_science(output_dir: Path, progress_callback=None) -> dict:
    """Scrape all Science grade bands."""
    output_dir.mkdir(parents=True, exist_ok=True)
    all_results = {}

    for band in SCIENCE_PDFS:
        if progress_callback:
            progress_callback(f"\n--- Scraping Science {band} ---")
        try:
            results = scrape_science_band(band, output_dir, progress_callback)
            all_results[band] = {
                "status": "ok",
                "grades": {g: len(c) for g, c in results.items()},
            }
        except Exception as e:
            logger.exception(f"Error scraping Science {band}")
            all_results[band] = {"status": "error", "error": str(e)}
            if progress_callback:
                progress_callback(f"ERROR scraping {band}: {e}")

    return all_results
