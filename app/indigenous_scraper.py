"""Manitoba Indigenous Education (Grade 12) scraper.

Scrapes: Current Topics in First Nations, Métis, and Inuit Studies
Source: edu.gov.mb.ca/k12/abedu/cur.html

Structure:
- 5 Clusters with Learning Experiences (LEs)
- Each LE has: Big Question, Focus Questions, Enduring Understandings
"""

import json
import logging
import re
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URL = "https://www.edu.gov.mb.ca/k12/abedu/foundation_gr12/full_doc.pdf"


def _download_pdf() -> bytes:
    resp = httpx.get(PDF_URL, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content


def scrape_indigenous_gr12(
    output_dir: Path,
    progress_callback=None,
) -> list[dict]:
    """Scrape Indigenous Education Grade 12 outcomes."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("Downloading Indigenous Education Gr 12 PDF...")

    pdf_bytes = _download_pdf()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    if progress_callback:
        progress_callback(f"Parsing Indigenous Education ({len(doc)} pages)...")

    toc = doc.get_toc()

    # Build cluster/LE structure from TOC
    cluster_list = []
    for entry in toc:
        level, title, page = entry
        if title.startswith("Cluster ") and level == 1:
            cluster_list.append({"title": title, "les": []})
        elif title.startswith("LE ") and level == 2 and cluster_list:
            le_code = title.split(":")[0].strip()
            le_name = title.split(":", 1)[1].strip() if ":" in title else title
            cluster_list[-1]["les"].append({
                "code": le_code,
                "name": le_name,
                "page": page,
            })

    # Extract outcomes from each LE
    clusters = []
    for cluster_info in cluster_list:
        cluster_outcomes = []

        for le in cluster_info["les"]:
            pg = le["page"] - 1
            big_question = ""
            focus_questions = []
            enduring_understandings = []

            for p in range(pg, min(pg + 5, len(doc))):
                text = doc[p].get_text()

                # Extract Big Question
                if "Big Question" in text and not big_question:
                    bq_match = re.search(r"Big Question\s*\n(.+?)(?=Focus Questions|$)", text, re.DOTALL)
                    if bq_match:
                        big_question = bq_match.group(1).replace("\n", " ").strip()

                # Extract Focus Questions
                if "Focus Questions" in text:
                    fq_start = text.index("Focus Questions")
                    fq_text = text[fq_start:fq_start + 1500]
                    questions = re.findall(
                        r"(\d+)\.\n(.+?)(?=\d+\.\n|enduring|$)",
                        fq_text, re.DOTALL | re.IGNORECASE,
                    )
                    for num, q in questions:
                        clean_q = q.replace("\n", " ").strip()
                        if clean_q and len(clean_q) > 10:
                            focus_questions.append(clean_q)

                # Extract Enduring Understandings
                if "enduring understandings" in text.lower():
                    eu_start = text.lower().index("enduring understandings")
                    eu_text = text[eu_start:eu_start + 800]
                    items = re.findall(r"q\s+(.+?)(?=q\s|C l u|$)", eu_text, re.DOTALL)
                    for item in items:
                        clean_eu = item.replace("\n", " ").strip()
                        if clean_eu and len(clean_eu) > 10:
                            enduring_understandings.append(clean_eu)
                    break

            # Create SLO entries from Focus Questions
            for i, fq in enumerate(focus_questions, 1):
                cluster_outcomes.append({
                    "code": f"{le['code']}_FQ{i}",
                    "description": fq,
                    "type": "Focus Question",
                    "learning_experience": le["name"],
                    "big_question": big_question,
                    "glo": [cluster_info["title"]],
                })

            # Create SLO entries from Enduring Understandings
            for i, eu in enumerate(enduring_understandings, 1):
                cluster_outcomes.append({
                    "code": f"{le['code']}_EU{i}",
                    "description": eu,
                    "type": "Enduring Understanding",
                    "learning_experience": le["name"],
                    "glo": [cluster_info["title"]],
                })

        if cluster_outcomes:
            clusters.append({
                "id": cluster_info["title"],
                "title": cluster_info["title"],
                "description": f"Grade 12 Current Topics in First Nations, Métis, and Inuit Studies",
                "specific_learning_outcomes": cluster_outcomes,
            })

    # Save
    output_data = {
        "subject": "Indigenous Education",
        "grade": "12",
        "course": "Current Topics in First Nations, Métis, and Inuit Studies",
        "framework_year": "Legacy Framework",
        "clusters": clusters,
    }

    filepath = output_dir / "Indigenous_Gr12.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)

    total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
    if progress_callback:
        progress_callback(f"Saved Indigenous_Gr12.json: {len(clusters)} clusters, {total} outcomes")

    return clusters
