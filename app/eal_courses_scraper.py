"""Manitoba EAL/LAL Senior Years course scrapers.

Scrapes:
- SY EAL Literacy Courses (Stages 1-3, "I Can" statements)
- SY LAL Literacy Courses (Phases 1A/1B/2A/2B, Learning Targets)
- SY LAL Numeracy Courses (Phases 1A/1B/2A/2B, Math outcomes)

Source: edu.gov.mb.ca/k12/cur/eal/
"""

import json
import logging
import re
from pathlib import Path

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

EAL_LITERACY_URL = "https://www.edu.gov.mb.ca/k12/cur/eal/eal-literacy/sy/full_doc.pdf"
LAL_LITERACY_URL = "https://www.edu.gov.mb.ca/k12/cur/eal/lal-literacy/sy/full_doc.pdf"
LAL_NUMERACY_URL = "https://www.edu.gov.mb.ca/k12/cur/eal/lal-numeracy/sy/full_doc.pdf"


def _download_pdf(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return resp.content


def _clean(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\uf06f", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _scrape_eal_literacy(pdf) -> list[dict]:
    """Parse SY EAL Literacy I Can statements from pages 21-27."""
    clusters = []
    stage_headers = ["EAL Stage 1", "EAL Stage 2", "EAL Stage 3"]

    for p_idx in range(20, 28):
        if p_idx >= len(pdf.pages):
            break
        tables = pdf.pages[p_idx].extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue
            for row in table:
                if not row or not row[0]:
                    continue
                cell0 = str(row[0]).strip()
                if cell0 in ("Domain", ""):
                    continue

                # Parse domain rows with I Can statements
                if cell0.startswith("Domain"):
                    domain_parts = cell0.replace("\n", " | ")
                    domain_name = domain_parts.split("|")[0].strip()
                    skill_area = domain_parts.split("|")[-1].strip() if "|" in domain_parts else ""

                    cluster_title = f"{domain_name} - {skill_area}" if skill_area else domain_name

                    strands_col = str(row[1]).strip() if len(row) > 1 and row[1] else ""

                    outcomes = []
                    for i, stage_col in enumerate(row[2:5]):
                        if stage_col and str(stage_col).strip():
                            desc = _clean(str(stage_col))
                            stage_label = stage_headers[i] if i < len(stage_headers) else f"Stage {i+1}"
                            code_prefix = re.sub(r"[^A-Za-z0-9]", "", domain_name)[:6]
                            skill_short = re.sub(r"[^A-Za-z]", "", skill_area)[:4] if skill_area else ""
                            outcomes.append({
                                "code": f"EAL_LIT_{code_prefix}_{skill_short}_{stage_label.replace(' ', '')}",
                                "description": desc,
                                "glo": [domain_name],
                                "glo_description": [f"EAL Literacy: {domain_name}"],
                                "strands": strands_col.replace("\n", ", "),
                            })

                    if outcomes:
                        existing = None
                        for c in clusters:
                            if c["id"] == cluster_title:
                                existing = c
                                break
                        if not existing:
                            existing = {
                                "id": cluster_title,
                                "title": cluster_title,
                                "description": f"I Can statements for {cluster_title}",
                                "specific_learning_outcomes": [],
                            }
                            clusters.append(existing)
                        existing["specific_learning_outcomes"].extend(outcomes)

    return clusters


def _scrape_lal_literacy(pdf) -> list[dict]:
    """Parse LAL Literacy Learning Targets from pages 27-33.

    Structure: Linguistic Strand tables with Learning Targets rows (descriptions)
    and Consolidation of Learning Outcomes rows (phase-specific outcomes).
    """
    clusters = []
    phase_headers = ["Phase 1A", "Phase 1B", "Phase 2A", "Phase 2B"]

    for p_idx in range(26, 40):
        if p_idx >= len(pdf.pages):
            break
        tables = pdf.pages[p_idx].extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue
            first_cell = str(table[0][0]).strip() if table[0] and table[0][0] else ""
            if not first_cell.startswith("Linguistic Strand"):
                continue

            current_target = ""
            for row in table[2:]:
                if not row or not row[0]:
                    continue
                cell0 = str(row[0]).strip()

                if cell0.startswith("Learning Targets:"):
                    current_target = cell0.split("\n")[0].replace("Learning Targets:", "").strip()
                    continue

                if cell0.startswith("Consolidation") and current_target:
                    outcome_codes = ""
                    lines = cell0.split("\n")
                    for line in lines:
                        if re.match(r"^\d+\.\d+\.\d+", line.strip()):
                            outcome_codes = line.strip()

                    outcomes = []
                    for i, phase_col in enumerate(row[1:5]):
                        if phase_col and str(phase_col).strip():
                            desc = _clean(str(phase_col))
                            phase_label = phase_headers[i] if i < len(phase_headers) else f"Phase {i+1}"
                            outcomes.append({
                                "code": f"LAL_LIT_{re.sub(r'[^A-Za-z]', '', current_target)[:10]}_{phase_label.replace(' ', '')}",
                                "description": desc,
                                "glo": ["Linguistic Strand"],
                                "glo_description": [f"LAL Literacy: {current_target}"],
                                "eal_framework_codes": outcome_codes,
                            })

                    if outcomes:
                        cluster_key = f"Learning Target: {current_target}"
                        existing = None
                        for c in clusters:
                            if c["id"] == cluster_key:
                                existing = c
                                break
                        if not existing:
                            existing = {
                                "id": cluster_key,
                                "title": current_target,
                                "description": f"LAL Literacy: {current_target}",
                                "specific_learning_outcomes": [],
                            }
                            clusters.append(existing)
                        existing["specific_learning_outcomes"].extend(outcomes)

    return clusters


def _scrape_lal_numeracy(pdf) -> list[dict]:
    """Parse LAL Numeracy outcomes from Phase pages."""
    clusters = []
    current_phase = ""

    for p_idx in range(39, 170):
        if p_idx >= len(pdf.pages):
            break
        tables = pdf.pages[p_idx].extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue

            first_cell = str(table[0][0]).strip() if table[0] and table[0][0] else ""

            # Detect phase/topic headers
            phase_match = re.match(r"LAL Numeracy (Phase \d[AB]): (.+?)(?:\n|$)", first_cell)
            if not phase_match:
                continue

            phase = phase_match.group(1)
            topic = phase_match.group(2).strip()

            # Find outcomes row
            for row in table[1:]:
                if not row or not row[0]:
                    continue
                cell0 = str(row[0]).strip()
                if cell0.startswith("Outcomes"):
                    outcome_codes = cell0.replace("Outcomes", "").strip().replace("\n", ", ")
                    numeracy_desc = _clean(str(row[1])) if len(row) > 1 and row[1] else ""
                    language_desc = _clean(str(row[2])) if len(row) > 2 and row[2] else ""

                    desc = numeracy_desc
                    if language_desc:
                        desc += f" [Language: {language_desc}]"

                    cluster_key = f"{phase}: {topic}"
                    existing = None
                    for c in clusters:
                        if c["id"] == cluster_key:
                            existing = c
                            break
                    if not existing:
                        existing = {
                            "id": cluster_key,
                            "title": f"{phase} - {topic}",
                            "description": f"LAL Numeracy {phase}: {topic}",
                            "specific_learning_outcomes": [],
                        }
                        clusters.append(existing)

                    existing["specific_learning_outcomes"].append({
                        "code": f"LAL_NUM_{phase.replace(' ', '')}_{re.sub(r'[^A-Za-z]', '', topic)[:10]}",
                        "description": desc,
                        "glo": [phase],
                        "glo_description": [f"LAL Numeracy: {topic}"],
                        "math_codes": outcome_codes,
                    })

    return clusters


def scrape_all_eal_courses(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape all EAL/LAL Senior Years courses."""
    results = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    courses = [
        ("SY EAL Literacy", EAL_LITERACY_URL, _scrape_eal_literacy,
         "SY EAL Literacy Courses", "9-12", "Framework 2024"),
        ("SY LAL Literacy", LAL_LITERACY_URL, _scrape_lal_literacy,
         "SY LAL Literacy Courses", "9-12", "Framework 2024"),
        ("SY LAL Numeracy", LAL_NUMERACY_URL, _scrape_lal_numeracy,
         "SY LAL Numeracy Courses", "9-12", "Framework 2021"),
    ]

    for label, url, parser_fn, subject, grade, fw_year in courses:
        if progress_callback:
            progress_callback(f"Downloading {label} PDF...")
        try:
            pdf_bytes = _download_pdf(url)
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading {label}: {e}")
            continue

        pdf = pdfplumber.open(__import__("io").BytesIO(pdf_bytes))

        if progress_callback:
            progress_callback(f"Parsing {label} ({len(pdf.pages)} pages)...")

        clusters = parser_fn(pdf)

        output_data = {
            "subject": subject,
            "grade": grade,
            "course": label,
            "framework_year": fw_year,
            "clusters": clusters,
        }

        safe_name = label.replace(" ", "_")
        filename = f"{safe_name}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} clusters, {total} outcomes")

        results[label] = clusters

    return results
