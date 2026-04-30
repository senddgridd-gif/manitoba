"""Manitoba Legacy Social Studies curriculum PDF scraper.

Parses the K-8 Framework of Outcomes PDF and individual grade 9-12 PDFs.
Social Studies uses cluster-based organization with outcome codes like:
  {grade}-{type}{domain}-{number}{variant?}
  e.g., 2-KC-001 = Grade 2, Knowledge-Citizenship, outcome 001
       0-VH-004A = Kindergarten, Values-Historical, outcome 004 (Aboriginal variant)

Type: K=Knowledge, V=Values
Domain: C=Citizenship, I=Identity/Culture/Community, H=Historical,
        L=Land/Environment, E=Economics, P=Power/Authority, G=Global
"""

import re
import json
import logging
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.edu.gov.mb.ca/k12/cur/socstud"

K8_FRAMEWORK_PDF = f"{BASE_URL}/framework/k-8framework.pdf"

# Grade 9-12 Foundation documents
SENIOR_PDFS: dict[str, dict] = {
    "9": {
        "url": f"{BASE_URL}/foundation_gr9/document.pdf",
        "title": "Canada in the Contemporary World",
        "framework_year": "Framework 2007",
    },
    "10": {
        "url": f"{BASE_URL}/frame_found_sr2/s2_full_doc.pdf",
        "title": "Geographic Issues of the 21st Century",
        "framework_year": "Framework 2006",
    },
    # Grade 11 uses Essential Questions format (EQ 11.X.Y) with Enduring
    # Understandings instead of traditional KV outcome codes. It requires
    # a different parser. Excluded from the standard senior scraping for now.
}

# Outcome code pattern: grade-TypeDomain-Number[Variant]
# e.g., 2-KC-001, 0-VH-004A, 5-KI-008F
SLO_CODE_PATTERN = re.compile(
    r"^(\d)-([KV][CIHLPEG])-(\d{3})([A-Z]?)\b"
)

CLUSTER_HEADING_PATTERN = re.compile(
    r"Cluster\s+(\d+):\s*(.+)", re.IGNORECASE
)

# GLO definitions for Social Studies
SOCSTUD_GLOS = {
    "Identity, Culture, and Community": (
        "Students will explore concepts of identity, culture, and community "
        "in relation to individuals, societies, and nations."
    ),
    "The Land: Places and People": (
        "Students will explore the dynamic relationships of people with the land, "
        "places, and environments."
    ),
    "Historical Connections": (
        "Students will explore how people, events, and ideas of the past shape "
        "the present and influence the future."
    ),
    "Global Interdependence": (
        "Students will explore the global interdependence of people, communities, "
        "societies, nations, and environments."
    ),
    "Power and Authority": (
        "Students will explore the processes and structures of power and authority, "
        "and their implications for individuals, relationships, communities, and nations."
    ),
    "Economics and Resources": (
        "Students will explore the distribution of resources and wealth in relation "
        "to individuals, communities, and nations."
    ),
}

DOMAIN_TO_GLO = {
    "C": "Identity, Culture, and Community",
    "I": "Identity, Culture, and Community",
    "L": "The Land: Places and People",
    "H": "Historical Connections",
    "G": "Global Interdependence",
    "P": "Power and Authority",
    "E": "Economics and Resources",
}

# Page header/footer patterns to skip
SKIP_PATTERNS = [
    re.compile(r"^Kindergarten to Grade 8 Social Studies$"),
    re.compile(r"^\d+$"),  # Page numbers
    re.compile(r"^Students will\.\.\.$"),
    re.compile(r"^Students will...$"),
    re.compile(r"^Notes$"),
    re.compile(r"^%$"),
    re.compile(r"^!$"),
    re.compile(r"^&$"),
    re.compile(r"^Knowledge$"),
    re.compile(r"^Values$"),
    re.compile(r"^Skills$"),
]

GRADE_TITLES = {
    "0": "Being Together",
    "1": "Connecting and Belonging",
    "2": "Communities in Canada",
    "3": "Communities of the World",
    "4": "Manitoba, Canada, and the North: Places and Stories",
    "5": "Peoples and Stories of Canada to 1867",
    "6": "Canada: A Country of Change (1867 to Present)",
    "7": "People and Places in the World",
    "8": "World History: Societies of the Past",
}

# Authoritative cluster titles from the TOC/framework document
# Used because PDF text extraction introduces artifacts
CLUSTER_TITLES: dict[str, dict[str, str]] = {
    "0": {
        "1": "Me",
        "2": "The People around Me",
        "3": "The World around Me",
    },
    "1": {
        "1": "I Belong",
        "2": "My Environment",
        "3": "Connecting with Others",
    },
    "2": {
        "1": "Our Local Community",
        "2": "Communities in Canada",
        "3": "The Canadian Community",
    },
    "3": {
        "1": "Connecting with Canadians",
        "2": "Exploring the World",
        "3": "Communities of the World",
        "4": "Exploring an Ancient Society",
    },
    "4": {
        "1": "Geography of Canada",
        "2": "Living in Canada",
        "3": "Living in Manitoba",
        "4": "History of Manitoba",
        "5": "Canada's North",
    },
    "5": {
        "1": "First Peoples",
        "2": "Early European Colonization (1600 to 1763)",
        "3": "Fur Trade",
        "4": "From British Colony to Confederation (1763 to 1867)",
    },
    "6": {
        "1": "Building a Nation (1867 to 1914)",
        "2": "An Emerging Nation (1914 to 1945)",
        "3": "Shaping Contemporary Canada (1945 to Present)",
        "4": "Canada Today: Democracy, Diversity, and the Influence of the Past",
    },
    "7": {
        "1": "World Geography",
        "2": "Global Quality of Life",
        "3": "Ways of Life in Asia, Africa, or Australasia",
        "4": "Human Impact in Europe or the Americas",
    },
    "8": {
        "1": "Understanding Societies Past and Present",
        "2": "Early Societies of Mesopotamia, Egypt, or the Indus Valley",
        "3": "Ancient Societies of Greece and Rome",
        "4": "Transition to the Modern World (Circa 500 to 1400)",
        "5": "Shaping the Modern World (Circa 1400 to 1850)",
    },
}


def download_pdf(url: str) -> bytes:
    """Download a PDF from the given URL."""
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        resp = client.get(url, headers={
            "User-Agent": "MB-Curriculum-Scraper/1.0 (educational research)"
        })
        resp.raise_for_status()
        return resp.content


def _is_skip_line(line: str) -> bool:
    for pattern in SKIP_PATTERNS:
        if pattern.match(line):
            return True
    return False


def _clean_text(text: str) -> str:
    text = text.replace("\u2013", "–").replace("\u2014", "—")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2026", "...")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _blocks_to_sorted_text(pages_blocks: list[list[tuple]]) -> str:
    """Convert page blocks to text sorted by position.

    Each text block is kept intact (not split across columns). Blocks
    are sorted by y-position then x-position for proper reading order,
    ensuring cluster headings appear before their outcomes.
    """
    all_lines: list[str] = []
    for page_blocks in pages_blocks:
        # Sort blocks by y-position (top to bottom), then x (left to right)
        sorted_blocks = sorted(
            page_blocks,
            key=lambda b: (b[1], b[0]),  # (y0, x0)
        )
        for block in sorted_blocks:
            if block[6] == 1:  # Skip image blocks
                continue
            text = block[4].strip()
            if text:
                all_lines.append(text)
    return "\n".join(all_lines)


def _find_grade_page_ranges(doc) -> dict[str, tuple[int, int]]:
    """Find page ranges for each grade in the K-8 framework PDF.

    Uses outcome codes to find boundaries, then scans backward to include
    overview/title pages.
    """
    code_pattern = re.compile(r"^(\d)-[KV][CIHLPEG]-\d{3}")
    grade_first_code_page: dict[str, int] = {}

    for p in range(35, min(doc.page_count, 157)):
        text = doc[p].get_text()
        for line in text.split("\n"):
            m = code_pattern.match(line.strip())
            if m:
                grade = m.group(1)
                if grade not in grade_first_code_page:
                    grade_first_code_page[grade] = p

    # Scan backward from first code page to find title/overview page
    grade_starts: dict[str, int] = {}
    for grade, first_code_page in grade_first_code_page.items():
        start = first_code_page
        for scan_p in range(first_code_page - 1, max(first_code_page - 8, 34), -1):
            scan_text = doc[scan_p].get_text().strip()
            if len(scan_text) < 200:
                start = scan_p
                break
            if "Grade Overview" in scan_text or "Cluster Overview" in scan_text:
                start = min(start, scan_p)
        grade_starts[grade] = start

    # Build ranges
    grade_ranges: dict[str, tuple[int, int]] = {}
    grades_sorted = sorted(grade_starts.keys(), key=int)
    for i, grade in enumerate(grades_sorted):
        start = grade_starts[grade]
        if i + 1 < len(grades_sorted):
            end = grade_starts[grades_sorted[i + 1]]
        else:
            end = min(doc.page_count - 5, 128)  # Exclude appendices
        grade_ranges[grade] = (start, end)

    return grade_ranges


def _parse_grade_outcomes(text: str, grade: str) -> list[dict]:
    """Parse all outcomes for a grade from the framework text.

    The PDF layout places a bare "Cluster N" sidebar marker BEFORE the outcomes,
    and the full "Cluster N: Title" heading AFTER the outcomes. So we:
    1. Collect all cluster titles from "Cluster N: Title" headings
    2. Track current cluster using both bare "Cluster N" markers and full headings
    3. Assign outcomes to whichever cluster marker was most recently seen
    """
    lines = text.split("\n")
    prefix = f"{grade}-"

    # Bare cluster marker (sidebar): just "Cluster 1" or "Cluster 2" on its own line
    bare_cluster_pattern = re.compile(r"^Cluster\s+(\d+)$", re.IGNORECASE)

    # --- Pass 1: Find cluster titles from full headings ---
    # Titles may wrap across lines, so collect continuation lines
    cluster_info: dict[str, str] = {}
    i_scan = 0
    while i_scan < len(lines):
        stripped = lines[i_scan].strip()
        cluster_match = CLUSTER_HEADING_PATTERN.match(stripped)
        if cluster_match:
            cluster_num = cluster_match.group(1)
            title_parts = [cluster_match.group(2).strip()]
            # Check next 1-2 lines for title continuation (short, no special chars)
            for j_scan in range(i_scan + 1, min(i_scan + 3, len(lines))):
                next_s = lines[j_scan].strip()
                if not next_s or len(next_s) > 60:
                    break
                if SLO_CODE_PATTERN.match(next_s):
                    break
                if bare_cluster_pattern.match(next_s):
                    break
                if CLUSTER_HEADING_PATTERN.match(next_s):
                    break
                if _is_skip_line(next_s):
                    break
                if next_s.startswith("Students "):
                    break
                # Skip lines with sidebar artifacts
                if re.match(r"^[%!&/,\s•\")+\d]+$", next_s):
                    break
                if any(kw in next_s.lower() for kw in [
                    "overview", "cluster overview", "being together",
                    "connecting and belonging", "in cluster",
                ]):
                    break
                title_parts.append(next_s)
            cluster_title = " ".join(title_parts)
            cluster_title = re.sub(
                r"\s*\(continued\)\s*$", "", cluster_title, flags=re.IGNORECASE
            )
            # Clean up sidebar artifacts from PDF extraction
            cluster_title = re.sub(r"\s*[%!&/]+\s*$", "", cluster_title)
            cluster_title = re.sub(r"\s*\)\s*$", "", cluster_title)
            cluster_title = re.sub(r"\s+\d+\s*$", "", cluster_title)
            cluster_title = re.sub(r"\s*[,/]\s*$", "", cluster_title)
            cluster_title = re.sub(r"\s*\"\s*$", "", cluster_title)
            cluster_title = re.sub(r"\s+", " ", cluster_title).strip()
            # Remove sentences that snuck into the title
            if ". " in cluster_title and len(cluster_title) > 80:
                cluster_title = cluster_title.split(". ")[0]
            if cluster_num not in cluster_info:
                cluster_info[cluster_num] = cluster_title
        i_scan += 1

    # --- Pass 2: Extract outcomes, tracking current cluster ---
    outcomes_by_cluster: dict[str, list[dict]] = defaultdict(list)
    current_cluster = "1"
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Update current cluster from bare marker OR full heading
        bare_match = bare_cluster_pattern.match(stripped)
        if bare_match:
            current_cluster = bare_match.group(1)
            i += 1
            continue

        full_match = CLUSTER_HEADING_PATTERN.match(stripped)
        if full_match:
            current_cluster = full_match.group(1)
            i += 1
            continue

        slo_match = SLO_CODE_PATTERN.match(stripped)
        if slo_match and stripped.startswith(prefix):
            code = f"{slo_match.group(1)}-{slo_match.group(2)}-{slo_match.group(3)}"
            variant = slo_match.group(4)
            if variant:
                code += variant

            domain_letter = slo_match.group(2)[1]
            glo_name = DOMAIN_TO_GLO.get(domain_letter, "")

            # Collect description
            desc_lines = []
            rest = stripped[len(code):].strip()
            if rest:
                desc_lines.append(rest)

            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()

                # Stop at next outcome code for this grade
                next_slo = SLO_CODE_PATTERN.match(next_line)
                if next_slo and next_line.startswith(prefix):
                    break

                # Stop at cluster markers
                if CLUSTER_HEADING_PATTERN.match(next_line):
                    break
                if bare_cluster_pattern.match(next_line):
                    break

                # Stop at sidebar text markers
                if next_line.startswith("Students ") and len(next_line) > 50:
                    break

                if _is_skip_line(next_line):
                    j += 1
                    continue

                if next_line:
                    desc_lines.append(next_line)

                j += 1

            description = _clean_text(" ".join(desc_lines))

            outcome = {
                "code": code,
                "description": description,
                "glo": [glo_name] if glo_name else [],
                "glo_description": [SOCSTUD_GLOS[glo_name]] if glo_name and glo_name in SOCSTUD_GLOS else [],
            }
            outcomes_by_cluster[current_cluster].append(outcome)
            i = j
            continue

        i += 1

    # --- Build cluster list ---
    clusters: list[dict] = []
    all_cluster_nums = sorted(
        set(list(cluster_info.keys()) + list(outcomes_by_cluster.keys()))
    )

    # Use authoritative titles when available, fall back to PDF-extracted ones
    auth_titles = CLUSTER_TITLES.get(grade, {})

    for cluster_num in all_cluster_nums:
        title = auth_titles.get(
            cluster_num,
            cluster_info.get(cluster_num, f"Cluster {cluster_num}"),
        )
        outcomes = outcomes_by_cluster.get(cluster_num, [])

        clusters.append({
            "id": title,
            "title": title,
            "description": "",
            "specific_learning_outcomes": outcomes,
        })

    return clusters


def scrape_socstud_k8(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Social Studies K-8 from the framework PDF."""
    if progress_callback:
        progress_callback("Downloading Social Studies K-8 Framework PDF...")

    pdf_bytes = download_pdf(K8_FRAMEWORK_PDF)

    if progress_callback:
        progress_callback("Parsing Social Studies K-8 Framework PDF...")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        grade_ranges = _find_grade_page_ranges(doc)
        # Extract blocks per page for position-aware parsing
        all_pages_blocks: list[list[tuple]] = []
        for p in range(doc.page_count):
            blocks = doc[p].get_text("blocks")
            all_pages_blocks.append(blocks)
    finally:
        doc.close()

    results: dict[str, list] = {}

    for grade in ["0", "1", "2", "3", "4", "5", "6", "7", "8"]:
        display_grade = "K" if grade == "0" else grade
        if grade not in grade_ranges:
            if progress_callback:
                progress_callback(f"WARNING: Could not find page range for Grade {display_grade}")
            continue

        start_page, end_page = grade_ranges[grade]
        # Build text from blocks sorted by position (y then x) for proper reading order
        grade_text = _blocks_to_sorted_text(all_pages_blocks[start_page:end_page])

        if progress_callback:
            progress_callback(
                f"Parsing Social Studies Grade {display_grade} "
                f"(pages {start_page + 1}-{end_page})..."
            )

        clusters = _parse_grade_outcomes(grade_text, grade)
        results[display_grade] = clusters

        # Save JSON
        output_data = {
            "subject": "Social Studies",
            "grade": display_grade,
            "course": f"{display_grade} Social Studies",
            "framework_year": "Framework 2003",
            "clusters": clusters,
        }

        filename = f"Social_Studies_Grade_{display_grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        if progress_callback:
            total_outcomes = sum(len(c["specific_learning_outcomes"]) for c in clusters)
            progress_callback(
                f"Saved {filename}: {len(clusters)} clusters, {total_outcomes} outcomes"
            )

    return results


def scrape_socstud_senior(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Social Studies 9-12 from individual grade PDFs."""
    results: dict[str, list] = {}

    for grade, config in SENIOR_PDFS.items():
        url = config["url"]
        if progress_callback:
            progress_callback(f"Downloading Social Studies Grade {grade} PDF...")

        try:
            pdf_bytes = download_pdf(url)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading Grade {grade}: {e}")
            continue

        if progress_callback:
            progress_callback(f"Parsing Social Studies Grade {grade} PDF...")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            clusters = _parse_senior_grade(doc, grade, config)
        finally:
            doc.close()
        results[grade] = clusters

        output_data = {
            "subject": "Social Studies",
            "grade": grade,
            "course": f"{grade} Social Studies",
            "framework_year": config["framework_year"],
            "clusters": clusters,
        }

        filename = f"Social_Studies_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        if progress_callback:
            total_outcomes = sum(len(c["specific_learning_outcomes"]) for c in clusters)
            progress_callback(
                f"Saved {filename}: {len(clusters)} clusters, {total_outcomes} outcomes"
            )

    return results


def _parse_senior_grade(doc: fitz.Document, grade: str, config: dict) -> list[dict]:
    """Parse outcomes from a senior grade (9-12) Social Studies PDF.

    Senior grades use codes without a grade prefix:
    KC-001, KI-016, VH-008, KL-024A, etc.
    Format: {Type}{Domain}-{Number}{Variant?}
    """
    senior_code_pattern = re.compile(
        r"^([KV][CIHLPEG])-(\d{3})([A-Z]?)\b"
    )

    toc = doc.get_toc()

    # Find cluster page ranges
    cluster_ranges: list[tuple[str, str, int, int]] = []

    # Method 1: From TOC (Grade 9)
    cluster_entries: list[tuple[str, str, int]] = []
    for level, title, page in toc:
        m = re.match(
            r"Grade\s+\d+\s+Cluster\s+(\d+):\s+(.+)",
            title.strip(),
            re.IGNORECASE,
        )
        if m:
            cluster_entries.append((m.group(1), m.group(2).strip(), page - 1))

    if cluster_entries:
        # Find end boundary
        end_boundary = doc.page_count
        for level, title, page in toc:
            if re.match(r"(References|Appendix|Appendices)", title.strip(), re.IGNORECASE):
                end_boundary = page - 1
                break
        for idx, (cnum, ctitle, start) in enumerate(cluster_entries):
            end = cluster_entries[idx + 1][2] if idx + 1 < len(cluster_entries) else end_boundary
            cluster_ranges.append((cnum, ctitle, start, end))
    else:
        # Method 2: Parse TOC page for "Cluster N: Title    page_num" (Grade 10)
        toc_pattern = re.compile(r"Cluster\s+(\d+):\s+(.+?)\s{2,}(\d+)")
        page_offset = 0
        for p in range(min(15, doc.page_count)):
            text = doc[p].get_text()
            if "Contents" in text:
                matches = toc_pattern.findall(text)
                if matches:
                    # Determine page offset: find a page number footer to calibrate
                    for check_p in range(p + 5, min(p + 20, doc.page_count)):
                        footer_text = doc[check_p].get_text()
                        footer_lines = [l.strip() for l in footer_text.split("\n") if l.strip()]
                        if footer_lines and re.match(r"^\d+$", footer_lines[-1]):
                            internal_num = int(footer_lines[-1])
                            page_offset = check_p - internal_num
                            break
                    for cnum, ctitle, page_str in matches:
                        pdf_page = int(page_str) + page_offset
                        cluster_entries.append((cnum, ctitle.strip(), pdf_page))
                    # Determine end pages
                    for idx, (cnum, ctitle, start) in enumerate(cluster_entries):
                        if idx + 1 < len(cluster_entries):
                            end = cluster_entries[idx + 1][2]
                        else:
                            # Find appendix start
                            end = doc.page_count
                            app_pattern = re.compile(r"Appendix|Appendices")
                            for line in text.split("\n"):
                                am = re.match(r"(Appendix|Appendices)\s*", line.strip())
                                if am:
                                    end = min(end, start + 80)
                                    break
                        cluster_ranges.append((cnum, ctitle, start, end))
                break

    if not cluster_ranges:
        return []

    # Parse outcomes for each cluster
    result: list[dict] = []
    seen_codes: set[str] = set()

    for cluster_num, cluster_title, start_page, end_page in cluster_ranges:
        text = "\n".join(doc[p].get_text() for p in range(start_page, min(end_page, doc.page_count)))
        lines = text.split("\n")

        outcomes: list[dict] = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            slo_match = senior_code_pattern.match(stripped)
            if slo_match:
                type_domain = slo_match.group(0).split("-")[0]
                number = slo_match.group(2)
                variant = slo_match.group(3)
                code = f"{type_domain}-{number}"
                if variant:
                    code += variant

                if code in seen_codes:
                    i += 1
                    continue
                seen_codes.add(code)

                domain_letter = type_domain[1]
                glo_name = DOMAIN_TO_GLO.get(domain_letter, "")

                rest = stripped[len(slo_match.group(0)):].strip()
                desc_lines = []
                if rest:
                    desc_lines.append(rest)

                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if senior_code_pattern.match(next_line):
                        break
                    if _is_skip_line(next_line):
                        j += 1
                        continue
                    if not next_line:
                        j += 1
                        continue
                    if re.match(r"^Grade\s+\d+", next_line) and "Cluster" not in next_line:
                        j += 1
                        continue
                    if re.match(r"^\d+$", next_line):
                        j += 1
                        continue
                    desc_lines.append(next_line)
                    j += 1

                description = _clean_text(" ".join(desc_lines))
                if description:
                    outcomes.append({
                        "code": code,
                        "description": description,
                        "glo": [glo_name] if glo_name else [],
                        "glo_description": [SOCSTUD_GLOS[glo_name]] if glo_name and glo_name in SOCSTUD_GLOS else [],
                    })
                i = j
                continue
            i += 1

        result.append({
            "id": cluster_title,
            "title": cluster_title,
            "description": "",
            "specific_learning_outcomes": outcomes,
        })

    return result


def scrape_all_socstud(output_dir: Path, progress_callback=None) -> dict:
    """Scrape all Social Studies grades."""
    output_dir.mkdir(parents=True, exist_ok=True)
    all_results = {}

    # K-8
    if progress_callback:
        progress_callback("\n--- Scraping Social Studies K-8 ---")
    try:
        results = scrape_socstud_k8(output_dir, progress_callback)
        all_results["K-8"] = {
            "status": "ok",
            "grades": {g: len(c) for g, c in results.items()},
        }
    except Exception as e:
        logger.exception("Error scraping Social Studies K-8")
        all_results["K-8"] = {"status": "error", "error": str(e)}
        if progress_callback:
            progress_callback(f"ERROR scraping K-8: {e}")

    # 9-12
    if progress_callback:
        progress_callback("\n--- Scraping Social Studies 9-12 ---")
    try:
        results = scrape_socstud_senior(output_dir, progress_callback)
        all_results["9-12"] = {
            "status": "ok",
            "grades": {g: len(c) for g, c in results.items()},
        }
    except Exception as e:
        logger.exception("Error scraping Social Studies 9-12")
        all_results["9-12"] = {"status": "error", "error": str(e)}
        if progress_callback:
            progress_callback(f"ERROR scraping 9-12: {e}")

    return all_results
