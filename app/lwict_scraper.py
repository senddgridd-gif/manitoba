"""Manitoba Literacy with ICT (LwICT) Continuum scraper.

Scrapes the K-12 LwICT Developmental Continuum outcomes from the web page.
The continuum has outcomes coded as:
  Q (Question/Plan), G (Gather/Make Sense), P (Produce/Show Understanding),
  C (Communicate), R (Reflect)

Source: edu.gov.mb.ca/k12/tech/lict/teachers/show_me/continuum.html
"""

import json
import logging
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.grade_splitter import split_to_per_grade

logger = logging.getLogger(__name__)

CONTINUUM_URL = "https://www.edu.gov.mb.ca/k12/tech/lict/teachers/show_me/continuum.html"

# Map code prefix to Big Idea name
BIG_IDEAS = {
    "Q": "Question and Plan",
    "G": "Gather and Make Sense",
    "P": "Produce to Show Understanding",
    "C": "Communicate",
    "R": "Reflect",
}


def scrape_lwict(
    output_dir: Path,
    progress_callback=None,
) -> list[dict]:
    """Scrape the LwICT Continuum outcomes."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("Downloading LwICT Continuum page...")

    resp = httpx.get(CONTINUUM_URL, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    text = resp.text

    if progress_callback:
        progress_callback("Parsing LwICT Continuum outcomes...")

    # Parse the page content for outcome codes
    # The outcomes follow the pattern: CODE description
    # e.g., "Q-1.1 recalls and/or records prior knowledge..."
    lines = text.split("\n")
    clusters = {}  # keyed by Big Idea prefix

    code_pattern = re.compile(r"^([QGPCR])-(\d+\.\d+)\s+(.+)")

    for line in lines:
        line = line.strip()
        # Strip HTML tags
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if not clean:
            continue

        match = code_pattern.match(clean)
        if match:
            prefix = match.group(1)
            code_num = match.group(2)
            description = match.group(3).strip()
            full_code = f"{prefix}-{code_num}"

            big_idea = BIG_IDEAS.get(prefix, prefix)
            if big_idea not in clusters:
                clusters[big_idea] = {
                    "id": big_idea,
                    "title": big_idea,
                    "description": f"LwICT Big Idea: {big_idea}",
                    "specific_learning_outcomes": [],
                }

            clusters[big_idea]["specific_learning_outcomes"].append({
                "code": full_code,
                "description": description,
                "glo": [f"Literacy with ICT: {big_idea}"],
            })

    cluster_list = list(clusters.values())

    # Also try parsing with BeautifulSoup for any missed items
    soup = BeautifulSoup(text, "html.parser")
    # Look for content divs that might contain outcomes
    for td in soup.find_all(["td", "th", "div", "p"]):
        cell_text = td.get_text(strip=True)
        match = code_pattern.match(cell_text)
        if match:
            prefix = match.group(1)
            code_num = match.group(2)
            description = match.group(3).strip()
            full_code = f"{prefix}-{code_num}"

            big_idea = BIG_IDEAS.get(prefix, prefix)
            # Check if already added
            existing = clusters.get(big_idea)
            if existing:
                codes_present = {s["code"] for s in existing["specific_learning_outcomes"]}
                if full_code not in codes_present:
                    existing["specific_learning_outcomes"].append({
                        "code": full_code,
                        "description": description,
                        "glo": [f"Literacy with ICT: {big_idea}"],
                    })
            else:
                clusters[big_idea] = {
                    "id": big_idea,
                    "title": big_idea,
                    "description": f"LwICT Big Idea: {big_idea}",
                    "specific_learning_outcomes": [{
                        "code": full_code,
                        "description": description,
                        "glo": [f"Literacy with ICT: {big_idea}"],
                    }],
                }
                cluster_list = list(clusters.values())

    cluster_list = list(clusters.values())

    output_data = {
        "subject": "Literacy with ICT",
        "grade": "K-12",
        "course": "LwICT Developmental Continuum",
        "framework_year": "Legacy Framework",
        "clusters": cluster_list,
    }

    filepath = output_dir / "LwICT_Continuum.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)

    total = sum(len(c["specific_learning_outcomes"]) for c in cluster_list)
    if progress_callback:
        progress_callback(f"Saved LwICT_Continuum.json: {len(cluster_list)} clusters, {total} outcomes")

    # Split into per-grade files
    split_to_per_grade(output_data, output_dir, "LwICT_Continuum", progress_callback=progress_callback)

    return cluster_list
