"""Manitoba Legacy Mathematics curriculum PDF scraper.

Parses the K-8 Mathematics Framework of Outcomes (2013) PDF and
individual Grade 9-12 outcome PDFs to extract strand-based SLOs.

Outcome code format: Grade.Strand.Number (e.g., K.N.5, 4.PR.2, 8.SP.1)
Strands: N (Number), PR (Patterns and Relations), SS (Shape and Space),
         SP (Statistics and Probability)
"""

import re
import json
import logging
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

K8_FRAMEWORK_PDF = (
    "https://www.edu.gov.mb.ca/k12/framework/publications/"
    "math/framework_k-8/docs/full_doc.pdf"
)

# Strand GLO definitions
MATH_GLOS: dict[str, str] = {
    "Number": "Develop number sense.",
    "Patterns and Relations (Patterns)": "Use patterns to describe the world and solve problems.",
    "Patterns and Relations (Variables and Equations)": (
        "Represent algebraic expressions in multiple ways."
    ),
    "Shape and Space (Measurement)": "Use direct or indirect measurement to solve problems.",
    "Shape and Space (3-D Objects and 2-D Shapes)": (
        "Describe the characteristics of 3-D objects and 2-D shapes, "
        "and analyze the relationships among them."
    ),
    "Shape and Space (Transformations)": (
        "Describe and analyze position and motion of objects and shapes."
    ),
    "Statistics and Probability (Data Analysis)": (
        "Collect, display, and analyze data to solve problems."
    ),
    "Statistics and Probability (Chance and Uncertainty)": (
        "Use experimental or theoretical probabilities to represent "
        "and solve problems involving uncertainty."
    ),
}

# Map strand code to full strand name
STRAND_CODE_TO_NAME: dict[str, str] = {
    "N": "Number",
    "PR": "Patterns and Relations",
    "SS": "Shape and Space",
    "SP": "Statistics and Probability",
}

# Outcome code pattern: Grade.Strand.Number.
# e.g., K.N.5., 4.PR.2., 8.SP.1.
SLO_CODE_PATTERN = re.compile(r"^(\d+|K)\.([A-Z]+)\.(\d+)\.")

# Process standards abbreviations
PROCESS_STANDARDS = {
    "C": "Communication",
    "CN": "Connections",
    "ME": "Mental Mathematics and Estimation",
    "PS": "Problem Solving",
    "R": "Reasoning",
    "T": "Technology",
    "V": "Visualization",
}


def download_pdf(url: str) -> bytes:
    """Download a PDF from the given URL."""
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if "pdf" not in resp.headers.get("content-type", ""):
            raise ValueError(f"Expected PDF but got {resp.headers.get('content-type')}")
        return resp.content


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    # Remove ANSI escape codes
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\n\s*", " ", text)
    # Clean bullet markers: lone 'n' used as bullet points in PDF extraction
    # Pattern: at word boundary, replace ' n ' when it appears as a list marker
    text = re.sub(r"\bn\s+(?=[a-z])", "- ", text)
    return text


def _find_grade_page_ranges(toc: list) -> dict[str, tuple[int, int]]:
    """Find page ranges for each grade from the TOC."""
    grade_entries: list[tuple[str, int]] = []
    for level, title, page in toc:
        if level == 3:
            if title.strip() == "Kindergarten":
                grade_entries.append(("K", page - 1))
            else:
                m = re.match(r"Grade\s+(\d+)", title.strip())
                if m:
                    grade_entries.append((m.group(1), page - 1))

    ranges: dict[str, tuple[int, int]] = {}
    for i, (grade, start) in enumerate(grade_entries):
        end = grade_entries[i + 1][1] if i + 1 < len(grade_entries) else 999
        ranges[grade] = (start, end)
    return ranges


def _parse_grade_outcomes(text: str, grade: str) -> list[dict]:
    """Parse outcomes for a single grade from the K-8 framework PDF text.

    Returns a list of strand-based clusters with outcomes.
    """
    lines = text.split("\n")
    prefix = f"{grade}."

    # Track current strand
    current_strand = ""
    current_strand_sub = ""
    current_glo = ""

    # Outcomes organized by full strand name
    strands: dict[str, list[dict]] = defaultdict(list)
    strand_glos: dict[str, str] = {}

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Detect strand header: "Strand:" followed by strand name
        if stripped == "Strand:":
            # Next non-empty line is the strand name
            j = i + 1
            strand_parts = []
            while j < len(lines) and len(strand_parts) < 3:
                s = lines[j].strip()
                if s == "General Learning Outcome:":
                    break
                if s and not s.startswith("[") and s != "Specific Learning Outcomes":
                    strand_parts.append(s)
                j += 1
            if strand_parts:
                full_strand = " ".join(strand_parts)
                full_strand = re.sub(r"\s+", " ", full_strand).strip()
                # Strip "(continued)" suffix — it's the same strand
                full_strand = re.sub(
                    r"\s*\(continued\)\s*$", "", full_strand, flags=re.IGNORECASE
                )
                current_strand = full_strand
                current_strand_sub = full_strand
            i = j
            continue

        # Detect GLO
        if stripped == "General Learning Outcome:":
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s and s != "Specific Learning Outcomes":
                    current_glo = s
                    strand_glos[current_strand_sub] = current_glo
                    break
                j += 1
            i = j + 1
            continue

        # Detect outcome code
        slo_match = SLO_CODE_PATTERN.match(stripped)
        if slo_match and stripped.startswith(prefix):
            grade_part = slo_match.group(1)
            strand_code = slo_match.group(2)
            number = slo_match.group(3)
            code = f"{grade_part}.{strand_code}.{number}"

            # Get description (rest of line + continuation lines)
            rest = stripped[len(code) + 1:].strip()  # +1 for trailing dot
            desc_lines = []
            if rest:
                desc_lines.append(rest)

            j = i + 1
            process_codes: list[str] = []
            while j < len(lines):
                next_line = lines[j].strip()

                # Stop at next outcome code
                if SLO_CODE_PATTERN.match(next_line) and next_line.startswith(prefix):
                    break

                # Stop at next strand header
                if next_line == "Strand:":
                    break

                # Process standards in brackets: [C, CN, ME, R, V]
                ps_match = re.match(r"^\[([A-Z,\s]+)\]\s*$", next_line)
                if ps_match:
                    process_codes = [
                        c.strip() for c in ps_match.group(1).split(",")
                    ]
                    j += 1
                    break

                # Skip achievement indicator lines (start with Q)
                if next_line.startswith("Q ") or next_line.startswith("Q\t"):
                    j += 1
                    break

                # Skip page headers/footers
                if re.match(r"^\[.+\]\s+\w+", next_line):
                    j += 1
                    continue
                if re.match(r"^\d+$", next_line):
                    j += 1
                    continue
                if "General and Specific Learning Outcomes" in next_line:
                    j += 1
                    continue
                if next_line.startswith("It is expected"):
                    j += 1
                    continue
                if next_line.startswith("Achievement Indicators"):
                    j += 1
                    continue
                if next_line.startswith("The following set"):
                    j += 1
                    continue
                if next_line.startswith("students have met"):
                    j += 1
                    continue

                if next_line:
                    # Check if this line has process standards inline
                    inline_ps = re.search(r"\[([A-Z,\s]+)\]\s*$", next_line)
                    if inline_ps:
                        process_codes = [
                            c.strip() for c in inline_ps.group(1).split(",")
                        ]
                        desc_part = next_line[:inline_ps.start()].strip()
                        if desc_part:
                            desc_lines.append(desc_part)
                        j += 1
                        break
                    desc_lines.append(next_line)
                j += 1

            description = _clean_text(" ".join(desc_lines))

            # Determine GLO for this strand
            strand_name = STRAND_CODE_TO_NAME.get(strand_code, current_strand_sub)
            glo_text = strand_glos.get(current_strand_sub, "")

            outcome = {
                "code": code,
                "description": description,
                "glo": [current_strand_sub] if current_strand_sub else [],
                "glo_description": [glo_text] if glo_text else [],
            }
            strands[current_strand_sub].append(outcome)
            i = j
            continue

        i += 1

    # Build cluster list from strands
    clusters: list[dict] = []
    cluster_num = 1
    for strand_name, outcomes in strands.items():
        glo_text = strand_glos.get(strand_name, MATH_GLOS.get(strand_name, ""))
        clusters.append({
            "id": strand_name,
            "title": strand_name,
            "description": glo_text,
            "specific_learning_outcomes": outcomes,
        })
        cluster_num += 1

    return clusters


def scrape_math_k8(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Mathematics K-8 from the framework PDF."""
    if progress_callback:
        progress_callback("Downloading Mathematics K-8 Framework PDF...")

    pdf_bytes = download_pdf(K8_FRAMEWORK_PDF)

    if progress_callback:
        progress_callback("Parsing Mathematics K-8 Framework PDF...")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        toc = doc.get_toc()
        grade_ranges = _find_grade_page_ranges(toc)

        # Extract text per page
        all_pages_text: list[str] = []
        for p in range(doc.page_count):
            all_pages_text.append(doc[p].get_text())
    finally:
        doc.close()

    results: dict[str, list] = {}

    for grade in ["K", "1", "2", "3", "4", "5", "6", "7", "8"]:
        if grade not in grade_ranges:
            if progress_callback:
                progress_callback(f"WARNING: Could not find page range for Grade {grade}")
            continue

        start_page, end_page = grade_ranges[grade]
        end_page = min(end_page, len(all_pages_text))
        grade_text = "\n".join(all_pages_text[start_page:end_page])

        if progress_callback:
            progress_callback(
                f"Parsing Mathematics Grade {grade} "
                f"(pages {start_page + 1}-{end_page})..."
            )

        clusters = _parse_grade_outcomes(grade_text, grade)
        display_grade = grade
        results[display_grade] = clusters

        output_data = {
            "subject": "Mathematics",
            "grade": display_grade,
            "course": f"{display_grade} Mathematics",
            "framework_year": "Framework 2013",
            "clusters": clusters,
        }

        filename = f"Mathematics_Grade_{display_grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        if progress_callback:
            total_slos = sum(len(c["specific_learning_outcomes"]) for c in clusters)
            progress_callback(
                f"Saved {filename}: {len(clusters)} strands, {total_slos} outcomes"
            )

    return results


def scrape_all_math(output_dir: Path, progress_callback=None) -> dict:
    """Scrape all Mathematics grades."""
    output_dir.mkdir(parents=True, exist_ok=True)
    all_results = {}

    if progress_callback:
        progress_callback("\n--- Scraping Mathematics K-8 ---")
    try:
        results = scrape_math_k8(output_dir, progress_callback)
        all_results["K-8"] = {
            "status": "ok",
            "grades": {g: len(c) for g, c in results.items()},
        }
    except Exception as e:
        logger.exception("Error scraping Mathematics K-8")
        all_results["K-8"] = {"status": "error", "error": str(e)}
        if progress_callback:
            progress_callback(f"ERROR scraping K-8: {e}")

    return all_results
