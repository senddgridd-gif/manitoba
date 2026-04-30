"""Manitoba International & Heritage Languages scraper.

Scrapes curriculum outcomes for:
- ASL (American Sign Language) Gr 9-12
- Spanish Gr S1-S4
- Hebrew K-6
- German Gr 7-12
- Ukrainian K-3, 4-6

All use the Manitoba language curriculum framework pattern:
GLOs (Applications, Language Competence, Global Citizenship, Strategies)
with clusters and strands containing SLOs by grade.

Source: edu.gov.mb.ca/k12/cur/languages/
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

from app.grade_splitter import split_to_per_grade

logger = logging.getLogger(__name__)

LANG_CONFIGS = {
    "ASL": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/asl/framework/full_doc.pdf",
        "grade_range": "9-12",
        "grades": ["10F", "20F", "30S", "40S"],
        "glo_pages": {
            "Applications": (21, 31),
            "Language Competence": (33, 47),
            "Global Citizenship": (49, 59),
            "Strategies": (61, 67),
        },
        "col_split": 300,
        "cluster_pattern": r"^([A-Z]+)[-–](\d+)\s+(.+)",
        "strand_pattern": r"\(([A-Z]+-?\d+\.\d+)\)",
    },
    "Spanish_S1S4": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/spanish/foundation/s1-s4/full_doc.pdf",
        "grade_range": "S1-S4",
        "grades": ["Senior 1", "Senior 2", "Senior 3", "Senior 4"],
        "grade_page_ranges": {
            "Senior 1": (37, 120),
            "Senior 2": (127, 220),
            "Senior 3": (233, 318),
            "Senior 4": (318, 412),
        },
        "col_split": None,
    },
    "Hebrew": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/hebrew/framework/full_doc.pdf",
        "grade_range": "K-6",
        "grades": ["K", "1", "2", "3", "4", "5", "6"],
    },
    "German": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/german/found_7-12/full_doc.pdf",
        "grade_range": "7-12",
        "grades": ["7", "8", "9", "10", "11", "12"],
        "grade_page_ranges": {
            "7": (33, 120),
            "8": (120, 210),
            "9": (210, 300),
            "10": (300, 390),
            "11": (390, 480),
            "12": (480, 570),
        },
        "col_split": None,
    },
    "Ukrainian_K3": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/ukrainian/found_k-3/full_doc.pdf",
        "grade_range": "K-3",
        "grades": ["K", "1", "2", "3"],
        "appendix_start": 982,
    },
    "Ukrainian_46": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/languages/ukrainian/found_4-6/full_doc.pdf",
        "grade_range": "4-6",
        "grades": ["4", "5", "6"],
        "appendix_start": 790,
    },
}


def _download_pdf(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content


def _parse_asl_columns(doc, glo_pages, col_split, grades) -> list[dict]:
    """Parse ASL-style two-column layout with fitz position-based extraction."""
    clusters = []
    grade_pairs = [(grades[0], grades[1]), (grades[2], grades[3])]

    for glo_name, (start_pg, end_pg) in glo_pages.items():
        for pg_idx in range(start_pg, end_pg):
            if pg_idx >= len(doc):
                break
            page = doc[pg_idx]

            # Determine which grade pair this page shows
            text = page.get_text()
            page_grades = []
            for g in grades:
                if g in text:
                    page_grades.append(g)

            if len(page_grades) < 2:
                continue

            left_grade = page_grades[0]
            right_grade = page_grades[1]

            # Extract lines by column
            left_lines = []
            right_lines = []
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                if "lines" not in b:
                    continue
                for line in b["lines"]:
                    line_text = "".join(s["text"] for s in line["spans"]).strip()
                    if not line_text or len(line_text) < 3:
                        continue
                    x0 = line["bbox"][0]
                    # Skip headers
                    if "General Learning Outcome" in line_text:
                        continue
                    if "ASL and DC" in line_text:
                        continue
                    if line_text in grades:
                        continue
                    if re.match(r"^[A-Z] [a-z] [a-z]", line_text):
                        continue  # spaced-out headers

                    if x0 < col_split:
                        left_lines.append(line_text)
                    else:
                        right_lines.append(line_text)

            # Parse each column
            for grade_label, lines in [(left_grade, left_lines), (right_grade, right_lines)]:
                current_cluster = ""
                current_strand = ""
                current_slos = []

                for line in lines:
                    # Check for cluster header
                    cluster_match = re.match(r"^([A-Z]+)[-–](\d+)\s+(.+)", line)
                    if cluster_match:
                        # Save previous strand
                        if current_strand and current_slos:
                            _add_to_clusters(clusters, glo_name, current_cluster, current_strand, grade_label, current_slos)
                            current_slos = []
                        current_cluster = f"{cluster_match.group(1)}-{cluster_match.group(2)}: {cluster_match.group(3)}"
                        current_strand = ""
                        continue

                    # Check for strand header (handle both - and em-dash)
                    strand_match = re.search(r"\(([A-Z]+[-\u2013]\d+\.\d+)\)", line)
                    if strand_match:
                        if current_strand and current_slos:
                            _add_to_clusters(clusters, glo_name, current_cluster, current_strand, grade_label, current_slos)
                            current_slos = []
                        strand_name = line.split("(")[0].strip()
                        strand_code = strand_match.group(1)
                        current_strand = f"{strand_name} ({strand_code})"
                        continue

                    # SLO line (indented content)
                    if line and not line.startswith("Q") and current_strand:
                        current_slos.append(line)

                # Save last strand
                if current_strand and current_slos:
                    _add_to_clusters(clusters, glo_name, current_cluster, current_strand, grade_label, current_slos)

    return clusters


def _add_to_clusters(clusters, glo_name, cluster_name, strand_name, grade, slos):
    """Helper to add SLOs to the cluster structure."""
    cluster_key = f"{glo_name} - {cluster_name}"

    existing = None
    for c in clusters:
        if c["id"] == cluster_key:
            existing = c
            break
    if not existing:
        existing = {
            "id": cluster_key,
            "title": cluster_name,
            "description": f"GLO: {glo_name}",
            "specific_learning_outcomes": [],
        }
        clusters.append(existing)

    # Combine multi-line SLOs
    combined = " ".join(slos)
    # Split on bullet markers if present
    individual = re.split(r"(?<=\w)\s*(?=\b[a-z])", combined) if "Q" not in combined else [combined]
    desc = combined.replace("  ", " ").strip()

    if desc:
        # Extract strand code from name
        code_match = re.search(r"\(([A-Z]+[-\u2013]\d+\.\d+)\)", strand_name)
        code = code_match.group(1).replace("\u2013", "-") if code_match else strand_name[:10]

        existing["specific_learning_outcomes"].append({
            "code": f"{code}_{grade}",
            "description": desc,
            "grade": grade,
            "strand": strand_name,
            "glo": [glo_name],
        })


def _parse_spanish_german(doc, grade_page_ranges, subject_prefix) -> list[dict]:
    """Parse Spanish/German Foundation for Implementation outcomes.

    These docs have outcomes on the left side of spread pages with teaching
    suggestions on the right. We extract the coded SLO entries.
    """
    clusters = []

    for grade_label, (start_pg, end_pg) in grade_page_ranges.items():
        current_glo = ""
        current_cluster = ""
        current_strand = ""

        for pg_idx in range(start_pg, min(end_pg, len(doc))):
            page = doc[pg_idx]
            text = page.get_text()
            lines = text.split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Detect GLO
                if line.startswith("General Learning Outcome"):
                    current_glo = line
                    continue

                # Detect cluster code (e.g., "1.1", "2.1")
                cluster_match = re.match(r"^(\d+\.\d+)\s+(.+)", line)
                if cluster_match:
                    code = cluster_match.group(1)
                    desc = cluster_match.group(2)
                    current_cluster = f"{code}: {desc}"
                    continue

                # Detect strand header with code (e.g., "1.1.1 Share Factual Information")
                strand_match = re.match(r"^(\d+\.\d+\.\d+)\s+(.+)", line)
                if strand_match:
                    code = strand_match.group(1)
                    name = strand_match.group(2)
                    current_strand = f"{code} {name}"

                    cluster_key = f"{current_glo[:50]} - {current_cluster}"
                    existing = None
                    for c in clusters:
                        if c["id"] == cluster_key:
                            existing = c
                            break
                    if not existing:
                        existing = {
                            "id": cluster_key,
                            "title": current_cluster,
                            "description": current_glo[:200],
                            "specific_learning_outcomes": [],
                        }
                        clusters.append(existing)

                    existing["specific_learning_outcomes"].append({
                        "code": f"{code}_{grade_label.replace(' ', '')}",
                        "description": name,
                        "grade": grade_label,
                        "strand": current_strand,
                        "glo": [current_glo[:100]],
                    })

                # Detect SLO bullet (starts with bullet or "!")
                if (line.startswith("!") or line.startswith("•")) and current_strand:
                    desc = line.lstrip("!•").strip()
                    if desc and len(desc) > 5:
                        cluster_key = f"{current_glo[:50]} - {current_cluster}"
                        for c in clusters:
                            if c["id"] == cluster_key:
                                c["specific_learning_outcomes"].append({
                                    "code": f"{current_strand.split()[0]}_{grade_label.replace(' ', '')}_slo",
                                    "description": desc,
                                    "grade": grade_label,
                                    "strand": current_strand,
                                    "glo": [current_glo[:100]],
                                })
                                break

    return clusters


def _parse_hebrew_ukrainian(doc, grades, subject_name) -> list[dict]:
    """Parse Hebrew/Ukrainian framework with multi-column grade layout.

    These docs have outcomes in multi-column layout where each column
    corresponds to a grade. We use fitz position-based extraction.
    """
    clusters = []
    current_glo = ""
    current_cluster_code = ""
    current_cluster_name = ""
    current_strand = ""

    # Find the outcomes section pages
    outcome_pages = []
    for pg_idx in range(len(doc)):
        text = doc[pg_idx].get_text()
        if "General Learning Outcome" in text and ("Grade" in text or "Kindergarten" in text):
            outcome_pages.append(pg_idx)

    for pg_idx in outcome_pages:
        page = doc[pg_idx]
        text = page.get_text()
        lines = text.split("\n")

        # Detect GLO
        for line in lines:
            if line.strip().startswith("General Learning Outcome"):
                current_glo = line.strip()
                break

        # Detect cluster code
        for line in lines:
            line = line.strip()
            cluster_m = re.match(r"^(\d+\.\d+)\s*$", line)
            if cluster_m:
                current_cluster_code = cluster_m.group(1)
                continue
            # Next line after cluster code might be the name
            if current_cluster_code and not current_cluster_name and line and not line.startswith("Grade") and not line.startswith("Kindergarten"):
                if not re.match(r"^\d", line) and len(line) > 3 and "General" not in line:
                    current_cluster_name = line

        # Detect which grades appear on this page
        page_grades = []
        blocks = page.get_text("dict")["blocks"]

        # First pass: find grade headers and their x positions
        grade_positions = []
        for b in blocks:
            if "lines" not in b:
                continue
            for bline in b["lines"]:
                btext = "".join(s["text"] for s in bline["spans"]).strip()
                for g in grades:
                    grade_label = f"Grade {g}" if g != "K" else "Kindergarten"
                    if btext == grade_label:
                        grade_positions.append((g, bline["bbox"][0]))

        if not grade_positions:
            continue

        # Sort by x position
        grade_positions.sort(key=lambda x: x[1])

        # Define column boundaries
        col_boundaries = []
        for i, (g, x) in enumerate(grade_positions):
            left = x - 20
            right = grade_positions[i + 1][1] - 20 if i + 1 < len(grade_positions) else 9999
            col_boundaries.append((g, left, right))

        # Second pass: extract outcomes by column
        # Find strand labels and numbered outcomes
        strand_x_max = grade_positions[0][1] - 30 if grade_positions else 120

        for b in blocks:
            if "lines" not in b:
                continue
            for bline in b["lines"]:
                btext = "".join(s["text"] for s in bline["spans"]).strip()
                x0 = bline["bbox"][0]

                if not btext or len(btext) < 2:
                    continue

                # Skip headers
                if "General Learning" in btext or re.match(r"^[A-Z] [a-z] [a-z]", btext):
                    continue
                if any(btext == f"Grade {g}" for g in grades) or btext == "Kindergarten":
                    continue
                if btext.startswith("By the end"):
                    continue
                if re.match(r"^\d+\.\d+$", btext):
                    continue
                if btext in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."):
                    continue

                # Strand labels (leftmost column)
                if x0 < strand_x_max and not re.match(r"^\d+\.", btext):
                    current_strand = btext
                    continue

                # Numbered outcomes - determine which grade column
                if re.match(r"^\d+\.\s*$", btext):
                    continue

                # Content text - assign to grade column
                for g, left, right in col_boundaries:
                    if left <= x0 < right:
                        cluster_key = f"{current_glo[:40]} - {current_cluster_code}"
                        existing = None
                        for c in clusters:
                            if c["id"] == cluster_key:
                                existing = c
                                break
                        if not existing:
                            existing = {
                                "id": cluster_key,
                                "title": f"{current_cluster_code}: {current_cluster_name}",
                                "description": current_glo[:200],
                                "specific_learning_outcomes": [],
                            }
                            clusters.append(existing)

                        # Check if we should append to last SLO for this grade or create new
                        last_slo = None
                        for slo in reversed(existing["specific_learning_outcomes"]):
                            if slo["grade"] == g and slo.get("strand") == current_strand:
                                last_slo = slo
                                break

                        if last_slo and not re.match(r"^\d+\.\s", btext):
                            # Continuation of previous SLO
                            last_slo["description"] += " " + btext
                        else:
                            # New SLO
                            item_match = re.match(r"^(\d+)\.\s*(.*)", btext)
                            if item_match:
                                item_num = item_match.group(1)
                                desc = item_match.group(2)
                            else:
                                item_num = "0"
                                desc = btext

                            existing["specific_learning_outcomes"].append({
                                "code": f"{current_cluster_code}_{item_num}_Gr{g}",
                                "description": desc,
                                "grade": g,
                                "strand": current_strand,
                                "glo": [current_glo[:100]],
                            })
                        break

    return clusters


def _parse_ukrainian_appendix(doc, appendix_start, grades) -> list[dict]:
    """Parse Ukrainian outcomes from Appendix A: Specific Outcomes Chart."""
    clusters = []
    current_cluster = ""
    current_grade = grades[0] if grades else "K"
    current_strand = ""

    for pg in range(appendix_start, min(appendix_start + 60, len(doc))):
        text = doc[pg].get_text()
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip decorative/header lines
            if line.startswith("_") or re.match(r'^[A-Z] [a-z] [a-z]', line):
                continue
            if line.startswith("A\u2013") or line.startswith("A-") and len(line) < 6:
                continue
            if "Appendix" in line or "NOTES:" in line:
                continue

            # Detect cluster code
            cluster_m = re.match(r'^(\d+\.\d+)$', line)
            if cluster_m:
                current_cluster = cluster_m.group(1)
                continue

            # Detect cluster name (follows code, title case)
            if current_cluster and not line.startswith("\u2022") and len(line) > 3:
                if not re.match(r'^\d', line) and line not in ("KINDERGARTEN",) and not re.match(r'^GRADE', line):
                    if not any(line == g for g in grades):
                        current_strand = line
                        continue

            # Detect grade
            if line == "KINDERGARTEN":
                current_grade = "K"
                continue
            grade_m = re.match(r'^GRADE (\d+)$', line)
            if grade_m:
                current_grade = grade_m.group(1)
                continue

            # Detect SLO bullet
            if line.startswith("\u2022") and current_cluster:
                desc = line.lstrip("\u2022 ").strip()
                if desc and len(desc) > 3:
                    cluster_key = current_cluster
                    existing = None
                    for c in clusters:
                        if c["id"] == cluster_key:
                            existing = c
                            break
                    if not existing:
                        existing = {
                            "id": cluster_key,
                            "title": f"Cluster {current_cluster}",
                            "description": "",
                            "specific_learning_outcomes": [],
                        }
                        clusters.append(existing)
                    existing["specific_learning_outcomes"].append({
                        "code": f"UK_{current_cluster}_Gr{current_grade}",
                        "description": desc,
                        "grade": current_grade,
                        "strand": current_strand,
                    })

    return clusters


def scrape_all_intl_languages(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape all international & heritage language curriculum documents."""
    results = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for lang_key, config in LANG_CONFIGS.items():
        if progress_callback:
            progress_callback(f"Downloading {lang_key} PDF...")

        try:
            pdf_bytes = _download_pdf(config["url"])
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading {lang_key}: {e}")
            continue

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        if progress_callback:
            progress_callback(f"Parsing {lang_key} ({len(doc)} pages)...")

        if lang_key == "ASL":
            clusters = _parse_asl_columns(
                doc, config["glo_pages"], config["col_split"], config["grades"]
            )
        elif lang_key in ("Spanish_S1S4", "German"):
            clusters = _parse_spanish_german(
                doc, config["grade_page_ranges"], lang_key
            )
        elif lang_key.startswith("Ukrainian"):
            appendix_start = config.get("appendix_start")
            if appendix_start:
                clusters = _parse_ukrainian_appendix(
                    doc, appendix_start, config["grades"]
                )
            else:
                clusters = []
        else:
            clusters = _parse_hebrew_ukrainian(
                doc, config["grades"], lang_key
            )

        output_data = {
            "subject": f"International Languages - {lang_key.replace('_', ' ')}",
            "grade": config["grade_range"],
            "course": f"{lang_key} Language and Culture",
            "framework_year": "Legacy Framework",
            "clusters": clusters,
        }

        filename = f"{lang_key}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} clusters, {total} outcomes")

        # Split into per-grade files
        grade_map = {
            "ASL": ["10F", "20F", "30S", "40S"],
            "Spanish_S1S4": ["Senior 1", "Senior 2", "Senior 3", "Senior 4"],
        }
        grades = grade_map.get(lang_key, config.get("grades"))
        split_to_per_grade(output_data, output_dir, lang_key, grades=grades, progress_callback=progress_callback)

        results[lang_key] = clusters

    return results
