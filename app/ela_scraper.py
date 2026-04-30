"""Manitoba English Language Arts curriculum scraper.

Two sources:
1. NEW Framework (Draft August 2025) — K-12 per-grade PDFs with SLO codes like ELA.3.A1.2
   URL pattern: /k12/cur/ela/docs/framework/grade_{k|1-12}.pdf
2. LEGACY Framework (1998) — Senior 1-4 PDFs with codes like (1.1.1)
   Kept as fallback in case the new framework PDFs are removed.
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.edu.gov.mb.ca/k12/cur/ela/docs"

# New K-12 Framework PDF URLs (Draft August 2025)
NEW_FRAMEWORK_BASE = f"{BASE_URL}/framework"
NEW_FRAMEWORK_GRADES: dict[str, str] = {
    "K": f"{NEW_FRAMEWORK_BASE}/grade_k.pdf",
    "1": f"{NEW_FRAMEWORK_BASE}/grade_1.pdf",
    "2": f"{NEW_FRAMEWORK_BASE}/grade_2.pdf",
    "3": f"{NEW_FRAMEWORK_BASE}/grade_3.pdf",
    "4": f"{NEW_FRAMEWORK_BASE}/grade_4.pdf",
    "5": f"{NEW_FRAMEWORK_BASE}/grade_5.pdf",
    "6": f"{NEW_FRAMEWORK_BASE}/grade_6.pdf",
    "7": f"{NEW_FRAMEWORK_BASE}/grade_7.pdf",
    "8": f"{NEW_FRAMEWORK_BASE}/grade_8.pdf",
    "9": f"{NEW_FRAMEWORK_BASE}/grade_9.pdf",
    "10": f"{NEW_FRAMEWORK_BASE}/grade_10.pdf",
    "11": f"{NEW_FRAMEWORK_BASE}/grade_11.pdf",
    "12": f"{NEW_FRAMEWORK_BASE}/grade_12.pdf",
}

# Strand titles for the new framework
NEW_STRANDS: dict[str, str] = {
    "A": "Explore and Discover Language and Literacy",
    "B": "Comprehend and Respond to Multimodal Texts",
    "C": "Compose and Create Multimodal Texts",
    "D": "Communicate Ideas and Build New Understandings",
}

# Legacy S1-S4 GLO titles
LEGACY_GLO_TITLES: dict[str, str] = {
    "1": "Explore thoughts, ideas, feelings, and experiences.",
    "2": "Comprehend and respond personally and critically to oral, print, and other media texts.",
    "3": "Manage ideas and information.",
    "4": "Enhance the clarity and artistry of communication.",
    "5": "Celebrate and build community.",
}


def _download(url: str, dest: Path) -> Path:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


# ---------------------------------------------------------------------------
# NEW FRAMEWORK SCRAPER (K-12, Draft August 2025)
# ---------------------------------------------------------------------------

# GLO code: ELA.{grade}.{strand}{num} (no trailing .number)
_GLO_PATTERN = re.compile(r"^(ELA\.(?:\d+|K)\.([A-D])(\d+))$")
# SLO code: ELA.{grade}.{strand}{num}.{sub} — standalone on a line
_SLO_PATTERN = re.compile(r"^(ELA\.(?:\d+|K)\.([A-D])(\d+)\.(\d+))$")
# SLO code inline with description (Grades 10-12 style): "ELA.10.A1.1  Listen..."
_SLO_INLINE_PATTERN = re.compile(r"^(ELA\.(?:\d+|K)\.([A-D])(\d+)\.(\d+))\s{2,}(.+)")


def _scrape_new_framework_grade(
    grade: str,
    tmp_dir: Path,
    progress_callback,
) -> list[dict]:
    """Scrape a single grade from the new K-12 ELA framework PDFs.

    Returns list of cluster dicts (one per GLO).
    """
    url = NEW_FRAMEWORK_GRADES[grade]
    pdf_path = tmp_dir / f"ela_new_{grade}.pdf"

    if progress_callback:
        progress_callback(f"Downloading ELA Grade {grade} (new framework)...")

    _download(url, pdf_path)

    doc = fitz.open(str(pdf_path))
    full_text = "\n".join(doc[p].get_text() for p in range(doc.page_count))
    doc.close()

    lines = full_text.split("\n")

    # Parse GLOs and SLOs
    current_glo_code = ""
    current_glo_title = ""
    current_strand = ""
    glo_clusters: dict[str, dict] = {}  # glo_code -> cluster dict
    current_slo_code = ""
    current_slo_desc_lines: list[str] = []

    def _flush_slo():
        nonlocal current_slo_code, current_slo_desc_lines
        if current_slo_code and current_glo_code:
            desc = " ".join(current_slo_desc_lines).strip()
            desc = re.sub(r"\s+", " ", desc)
            if desc and current_glo_code in glo_clusters:
                glo_clusters[current_glo_code]["specific_learning_outcomes"].append({
                    "code": current_slo_code,
                    "description": desc,
                    "glo": [current_glo_code],
                    "glo_description": [f"{current_glo_code}: {glo_clusters[current_glo_code]['title']}"],
                })
        current_slo_code = ""
        current_slo_desc_lines = []

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Skip headers/footers
        if stripped in ("DRAFT ENGLISH LANGUAGE ARTS", "DRAFT AUGUST 2025", ""):
            i += 1
            continue

        # Detect strand heading
        strand_match = re.match(r"Strand\s+([A-D]):\s*(.+)", stripped)
        if strand_match:
            current_strand = strand_match.group(1)
            i += 1
            continue

        # Detect GLO marker line
        if stripped == "GENERAL LEARNING OUTCOME":
            _flush_slo()
            # Next non-empty line should be the GLO code
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                glo_match = _GLO_PATTERN.match(lines[j].strip())
                if glo_match:
                    current_glo_code = glo_match.group(1)
                    # Next non-empty line is the GLO title
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines):
                        current_glo_title = lines[k].strip()
                        strand_letter = glo_match.group(2)
                        strand_name = NEW_STRANDS.get(strand_letter, f"Strand {strand_letter}")

                        if current_glo_code not in glo_clusters:
                            glo_clusters[current_glo_code] = {
                                "id": f"{strand_name} - {current_glo_title}",
                                "title": current_glo_title,
                                "description": f"Strand {strand_letter}: {strand_name}",
                                "specific_learning_outcomes": [],
                            }
                        i = k + 1
                        continue
            i = j + 1 if j < len(lines) else i + 1
            continue

        # Detect SLO code (standalone on a line)
        slo_match = _SLO_PATTERN.match(stripped)
        if slo_match:
            _flush_slo()
            current_slo_code = slo_match.group(1)
            current_slo_desc_lines = []
            i += 1
            continue

        # Detect SLO code inline with description (Grades 10-12 style)
        slo_inline_match = _SLO_INLINE_PATTERN.match(stripped)
        if slo_inline_match:
            _flush_slo()
            current_slo_code = slo_inline_match.group(1)
            current_slo_desc_lines = [slo_inline_match.group(5)]
            i += 1
            continue

        # Skip GLO code lines that appear inline (without GENERAL LEARNING OUTCOME header)
        if _GLO_PATTERN.match(stripped):
            i += 1
            continue

        # Accumulate SLO description
        if current_slo_code:
            # Skip sub-heading labels that appear between SLOs
            if re.match(r"^(Listening|Speaking|Interacting|Phonological|Alphabetic|Phonics|Word Study|Vocabulary|Reading Fluency|Activate|Engage|Review|Identify|Make|Monitor|Infer|Visualize|Summarize|Synthesize|Analyze|Generate|Plan|Draft|Revise|Edit|Publish|Present|Reflect|Collaborate|Assess|Set Goals)", stripped) and len(stripped) < 80:
                i += 1
                continue
            # Skip page artifacts
            if re.match(r"^\d+$", stripped):
                i += 1
                continue
            current_slo_desc_lines.append(stripped)

        i += 1

    _flush_slo()

    # Build ordered cluster list
    clusters = list(glo_clusters.values())
    return clusters


# ---------------------------------------------------------------------------
# LEGACY FRAMEWORK SCRAPER (S1-S4, 1998)
# ---------------------------------------------------------------------------

def _parse_outcomes_from_page(text: str) -> list[dict]:
    """Parse outcomes from a single page of text using (N.N.N) codes as delimiters."""
    text = text.replace("\uf0a7", "").replace("\uf0b7", "")
    text_flat = re.sub(r"\n", " ", text)
    text_flat = re.sub(r"\s+", " ", text_flat)

    parts = re.split(r"\((\d\.\d\.\d)\)", text_flat)

    outcomes: list[dict] = []
    for i in range(1, len(parts), 2):
        code = parts[i]
        pre = parts[i - 1].strip()
        post = parts[i + 1].strip() if i + 1 < len(parts) else ""

        words = pre.split()
        title_words: list[str] = []
        for w in reversed(words):
            if re.match(r"^[A-Z][a-z]+$", w) or w.lower() in [
                "and", "with", "to", "of", "the", "in", "for", "a",
            ]:
                title_words.insert(0, w)
            elif w == "Others\u2019" or re.match(r"^[A-Z][a-z]+[\u2019']s$", w):
                title_words.insert(0, w)
            else:
                break
        title = " ".join(title_words)

        if i + 2 < len(parts):
            desc_words = post.split()
            while desc_words:
                w = desc_words[-1]
                if re.match(r"^[A-Z][a-z]+$", w) or w.lower() in [
                    "and", "with", "to", "of", "the", "in", "for", "a",
                ]:
                    desc_words.pop()
                elif w == "Others\u2019" or re.match(r"^[A-Z][a-z]+[\u2019']s$", w):
                    desc_words.pop()
                else:
                    break
            desc = " ".join(desc_words)
        else:
            desc = post

        outcomes.append({
            "code": code,
            "title": title,
            "description": desc,
        })

    return outcomes


def _scrape_legacy_s1_s2(grade: str, tmp_dir: Path, progress_callback) -> list[dict]:
    """Scrape Senior 1 or 2 using individual outcome PDFs (legacy framework)."""
    grade_key = grade.lower()
    clusters: list[dict] = []

    for glo_num in range(1, 6):
        url = f"{BASE_URL}/{grade_key}_framework/outcome{glo_num}.pdf"
        pdf_path = tmp_dir / f"{grade_key}_outcome{glo_num}.pdf"

        if progress_callback:
            progress_callback(f"Downloading ELA {grade} GLO {glo_num} (legacy)...")

        try:
            _download(url, pdf_path)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading GLO {glo_num}: {e}")
            continue

        doc = fitz.open(str(pdf_path))
        text = doc[0].get_text()
        doc.close()

        outcomes = _parse_outcomes_from_page(text)

        glo_title = LEGACY_GLO_TITLES.get(str(glo_num), f"GLO {glo_num}")
        slos = []
        for o in outcomes:
            slos.append({
                "code": o["code"],
                "description": o["description"],
                "glo": [f"GLO {glo_num}"],
                "glo_description": [f"GLO {glo_num}: {glo_title}"],
            })

        cluster_title = f"GLO {glo_num}: {glo_title}"
        clusters.append({
            "id": cluster_title,
            "title": cluster_title,
            "description": glo_title,
            "specific_learning_outcomes": slos,
        })

    return clusters


def _scrape_legacy_s3_s4(grade: str, tmp_dir: Path, progress_callback) -> list[dict]:
    """Scrape Senior 3 or 4 from full document PDF with 3 focus areas (legacy framework)."""
    grade_key = grade.lower()
    url = f"{BASE_URL}/{grade_key}_framework/{grade_key}_fulldoc.pdf"
    pdf_path = tmp_dir / f"{grade_key}_fulldoc.pdf"

    if progress_callback:
        progress_callback(f"Downloading ELA {grade} full document (legacy)...")

    _download(url, pdf_path)

    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()

    focus_areas: list[tuple[str, int]] = []
    glo_entries: list[tuple[str, int]] = []
    for level, title, page in toc:
        if "Focus" in title and "Outcomes" in title:
            clean = title.replace(" and Standards", "").strip()
            focus_areas.append((clean, page))
        if "General Learning Outcome" in title or "General Leanring" in title or "General Learnng" in title:
            glo_entries.append((title, page))

    clusters: list[dict] = []

    for fa_idx, (focus_name, fa_start) in enumerate(focus_areas):
        fa_end = focus_areas[fa_idx + 1][1] if fa_idx + 1 < len(focus_areas) else doc.page_count

        fa_glos = [(t, p) for t, p in glo_entries if fa_start <= p < fa_end]

        for glo_idx, (glo_title_raw, glo_start) in enumerate(fa_glos):
            glo_end = fa_glos[glo_idx + 1][1] if glo_idx + 1 < len(fa_glos) else fa_end

            m = re.search(r"(\d)", glo_title_raw)
            glo_num = m.group(1) if m else str(glo_idx + 1)

            page_idx = glo_start - 1
            if page_idx < 0 or page_idx >= doc.page_count:
                continue

            text = doc[page_idx].get_text()
            outcomes = _parse_outcomes_from_page(text)

            if not outcomes:
                continue

            glo_title = LEGACY_GLO_TITLES.get(glo_num, f"GLO {glo_num}")
            cluster_title = f"{focus_name} - GLO {glo_num}: {glo_title}"

            slos = []
            for o in outcomes:
                slos.append({
                    "code": o["code"],
                    "description": o["description"],
                    "glo": [f"GLO {glo_num}"],
                    "glo_description": [f"GLO {glo_num}: {glo_title}"],
                })

            clusters.append({
                "id": cluster_title,
                "title": cluster_title,
                "description": f"{focus_name} — {glo_title}",
                "specific_learning_outcomes": slos,
            })

    doc.close()
    return clusters


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def scrape_all_ela(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape ELA K-12 using the new framework PDFs (Draft August 2025)."""
    results: dict[str, list] = {}
    tmp_dir = Path("/tmp/ela_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    for grade, url in NEW_FRAMEWORK_GRADES.items():
        if progress_callback:
            progress_callback(f"Scraping ELA Grade {grade}...")

        try:
            clusters = _scrape_new_framework_grade(grade, tmp_dir, progress_callback)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR scraping ELA Grade {grade}: {e}")
            continue

        results[grade] = clusters

        output_data = {
            "subject": "English Language Arts",
            "grade": grade,
            "course": f"{grade} English Language Arts",
            "framework_year": "Framework 2025 (Draft)",
            "clusters": clusters,
        }

        filename = f"ELA_Grade_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} GLOs, {total} outcomes")

    return results


def scrape_ela_legacy(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape ELA Senior 1-4 using the legacy (1998) framework PDFs."""
    results: dict[str, list] = {}
    tmp_dir = Path("/tmp/ela_pdfs")
    tmp_dir.mkdir(exist_ok=True)

    for grade in ["S1", "S2", "S3", "S4"]:
        if progress_callback:
            progress_callback(f"Scraping ELA {grade} (legacy)...")

        try:
            if grade in ("S1", "S2"):
                clusters = _scrape_legacy_s1_s2(grade, tmp_dir, progress_callback)
            else:
                clusters = _scrape_legacy_s3_s4(grade, tmp_dir, progress_callback)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR scraping ELA {grade}: {e}")
            continue

        results[grade] = clusters

        output_data = {
            "subject": "English Language Arts",
            "grade": grade,
            "course": f"{grade} English Language Arts",
            "framework_year": "Framework 1998",
            "clusters": clusters,
        }

        filename = f"ELA_{grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} clusters, {total} outcomes")

    return results
