"""Manitoba EAL Framework K-12 scraper.

Scrapes the K-12 Curriculum Framework for English as an Additional Language (EAL)
and Literacy, Academics, and Language (LAL) Programming (2021).

3 PDFs: Early Years (K-3, 3 stages), Middle Years (4-8, 4 stages), Senior Years (9-12, 5 stages).
Each has EAL Domains with Clusters and Strands, plus LAL Domains (MY/SY only).

Source: edu.gov.mb.ca/k12/cur/eal/docs/framework/
"""

import json
import logging
import re
from pathlib import Path

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

PDF_CONFIGS = {
    "EAL_EarlyYears": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/eal/docs/framework/early_years.pdf",
        "grade_range": "K-3",
        "stage_count": 3,
        "eal_pages": range(28, 42),
        "lal_pages": None,
    },
    "EAL_MiddleYears": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/eal/docs/framework/middle_years.pdf",
        "grade_range": "4-8",
        "stage_count": 4,
        "eal_pages": range(28, 42),
        "lal_pages": range(54, 68),
    },
    "EAL_SeniorYears": {
        "url": "https://www.edu.gov.mb.ca/k12/cur/eal/docs/framework/senior_years.pdf",
        "grade_range": "9-12",
        "stage_count": 5,
        "eal_pages": range(28, 42),
        "lal_pages": range(54, 68),
    },
}


def _download_pdf(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return resp.content


def _parse_eal_tables(pdf, page_range) -> list[dict]:
    """Parse EAL domain/cluster/strand tables from PDF pages."""
    clusters = []
    current_domain = ""
    current_domain_desc = ""
    current_cluster_id = ""
    current_cluster_desc = ""
    stage_headers = []

    for page_idx in page_range:
        if page_idx >= len(pdf.pages):
            break
        page = pdf.pages[page_idx]
        tables = page.extract_tables()

        for table in tables:
            if not table or len(table) < 2:
                continue

            for row in table:
                if not row or not row[0]:
                    continue

                cell0 = str(row[0]).strip()

                # Detect Domain header
                if cell0.startswith("Domain"):
                    current_domain = cell0.split("\n")[0].strip()
                    current_domain_desc = "\n".join(cell0.split("\n")[1:]).strip()
                    continue

                # Detect Cluster header
                if re.match(r"^Cluster \d+\.\d+", cell0):
                    current_cluster_id = cell0.strip()
                    current_cluster_desc = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                    continue

                # Detect Stage headers row
                if cell0 == "Strands":
                    stage_headers = [str(c).strip() for c in row[1:] if c and "Stage" in str(c)]
                    continue

                # Detect strand outcome rows (start with digit pattern like 1.1.1)
                strand_match = re.match(r"^(\d+\.\d+\.\d+[a-z]?)\s", cell0)
                if not strand_match and re.match(r"^(\d+\.\d+\.\d+)", cell0):
                    strand_match = re.match(r"^(\d+\.\d+\.\d+)", cell0)

                if strand_match:
                    strand_code = strand_match.group(1)
                    strand_name = cell0.replace("\n", " ").strip()

                    outcomes = []
                    for i, stage_val in enumerate(row[1:]):
                        if stage_val and str(stage_val).strip():
                            stage_label = stage_headers[i] if i < len(stage_headers) else f"Stage {i+1}"
                            desc = str(stage_val).replace("\n", " ").strip()
                            # Clean up bullet points
                            desc = re.sub(r"\s*•\s*", "; ", desc).strip("; ")
                            outcomes.append({
                                "code": f"{strand_code}_{stage_label.replace(' ', '')}",
                                "description": desc,
                                "glo": [current_domain],
                                "glo_description": [current_domain_desc],
                            })

                    if outcomes:
                        # Find or create cluster
                        cluster_key = f"{current_domain} - {current_cluster_id}"
                        existing = None
                        for c in clusters:
                            if c["id"] == cluster_key:
                                existing = c
                                break
                        if not existing:
                            existing = {
                                "id": cluster_key,
                                "title": f"{current_cluster_id}",
                                "description": current_cluster_desc,
                                "specific_learning_outcomes": [],
                            }
                            clusters.append(existing)
                        existing["specific_learning_outcomes"].extend(outcomes)

    return clusters


def scrape_all_eal_framework(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape EAL Framework K-12."""
    results = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for doc_key, config in PDF_CONFIGS.items():
        if progress_callback:
            progress_callback(f"Downloading {doc_key} PDF...")

        try:
            pdf_bytes = _download_pdf(config["url"])
        except Exception as e:
            if progress_callback:
                progress_callback(f"ERROR downloading {doc_key}: {e}")
            continue

        pdf = pdfplumber.open(
            __import__("io").BytesIO(pdf_bytes)
        )

        if progress_callback:
            progress_callback(f"Parsing {doc_key} EAL domains ({len(pdf.pages)} pages)...")

        # Parse EAL domains
        eal_clusters = _parse_eal_tables(pdf, config["eal_pages"])

        output_data = {
            "subject": "English as an Additional Language",
            "grade": config["grade_range"],
            "course": f"EAL Framework {config['grade_range']}",
            "framework_year": "Framework 2021",
            "stage_count": config["stage_count"],
            "clusters": eal_clusters,
        }

        filename = f"EAL_Framework_{config['grade_range'].replace('-', '_')}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in eal_clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(eal_clusters)} clusters, {total} outcomes")

        results[doc_key] = eal_clusters

        # Parse LAL domains if present
        if config["lal_pages"]:
            if progress_callback:
                progress_callback(f"Parsing {doc_key} LAL domains...")

            lal_clusters = _parse_eal_tables(pdf, config["lal_pages"])

            lal_data = {
                "subject": "Literacy, Academics, and Language (LAL)",
                "grade": config["grade_range"],
                "course": f"LAL Framework {config['grade_range']}",
                "framework_year": "Framework 2021",
                "clusters": lal_clusters,
            }

            lal_filename = f"LAL_Framework_{config['grade_range'].replace('-', '_')}.json"
            lal_filepath = output_dir / lal_filename
            with open(lal_filepath, "w", encoding="utf-8") as f:
                json.dump(lal_data, f, indent=4, ensure_ascii=False)

            lal_total = sum(len(c["specific_learning_outcomes"]) for c in lal_clusters)
            if progress_callback:
                progress_callback(f"Saved {lal_filename}: {len(lal_clusters)} clusters, {lal_total} outcomes")

            results[f"{doc_key}_LAL"] = lal_clusters

    return results
