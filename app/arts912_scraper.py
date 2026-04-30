"""Manitoba Arts Education Grades 9-12 scraper.

4 disciplines: Dance, Dramatic Arts, Music, Visual Arts.
Each has 4 Essential Learning Areas (Making, Creating, Connecting, Responding)
with 13 Recursive Learnings.

Source PDFs: edu.gov.mb.ca/k12/cur/arts/docs/{disc}_9-12.pdf
"""

import json
import logging
from pathlib import Path

import fitz
import httpx

logger = logging.getLogger(__name__)

PDF_URLS = {
    "Dance": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/dance_9-12.pdf",
    "Dramatic Arts": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/dramatic_arts_9-12.pdf",
    "Music": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/music_9-12.pdf",
    "Visual Arts": "https://www.edu.gov.mb.ca/k12/cur/arts/docs/visual_9-12.pdf",
}

DISC_PREFIXES = {
    "Dance": "DA",
    "Dramatic Arts": "DR",
    "Music": "M",
    "Visual Arts": "VA",
}

# Hardcoded recursive learning descriptions (13 per discipline)
# Extracted from the PDFs but cleaned up since the decorative layout breaks text extraction
RECURSIVE_LEARNINGS = {
    "Dance": {
        "DA–M1": "The learner develops competencies for using elements of dance in a variety of contexts.",
        "DA–M2": "The learner develops competencies for using dance techniques in a variety of contexts.",
        "DA–M3": "The learner develops expressive skills and musicality to communicate artistic intent.",
        "DA–CR1": "The learner generates ideas from a variety of sources for creating dance.",
        "DA–CR2": "The learner experiments with, develops, and uses ideas for creating dance.",
        "DA–CR3": "The learner revises, refines, and shares dance ideas and creative work.",
        "DA–C1": "The learner develops understandings about people and practices in dance.",
        "DA–C2": "The learner develops understandings about the influence and impact of dance.",
        "DA–C3": "The learner develops understandings about the roles, purposes, and meanings of dance.",
        "DA–R1": "The learner generates initial reactions to dance experiences.",
        "DA–R2": "The learner critically observes and describes dance experiences.",
        "DA–R3": "The learner analyzes and interprets dance experiences.",
        "DA–R4": "The learner applies new understandings about dance to construct identity and to act in the world.",
    },
    "Dramatic Arts": {
        "DR–M1": "The learner develops competencies for using the tools and techniques of body, mind, and voice for drama/theatre.",
        "DR–M2": "The learner develops competencies for using elements of drama/theatre in a variety of contexts.",
        "DR–M3": "The learner develops competencies for using a range of dramatic forms and styles.",
        "DR–CR1": "The learner generates ideas from a variety of sources for creating drama/theatre.",
        "DR–CR2": "The learner experiments with, develops, and uses ideas for creating drama/theatre.",
        "DR–CR3": "The learner revises, refines, and shares drama/theatre ideas and creative work.",
        "DR–C1": "The learner develops understandings about people and practices in the dramatic arts.",
        "DR–C2": "The learner develops understandings about the influence and impact of the dramatic arts.",
        "DR–C3": "The learner develops understandings about the roles, purposes, and meanings of the dramatic arts.",
        "DR–R1": "The learner generates initial reactions to drama/theatre experiences.",
        "DR–R2": "The learner critically observes and describes drama/theatre experiences.",
        "DR–R3": "The learner analyzes and interprets drama/theatre experiences.",
        "DR–R4": "The learner applies new understandings about drama/theatre to construct identity and to act in the world.",
    },
    "Music": {
        "M–M1": "The learner develops competencies for using tools and techniques to produce and respond to sound.",
        "M–M2": "The learner develops listening competencies for making music.",
        "M–M3": "The learner develops competencies for using elements of music in a variety of contexts.",
        "M–CR1": "The learner generates ideas from a variety of sources for creating music.",
        "M–CR2": "The learner experiments with, develops, and uses ideas for creating music.",
        "M–CR3": "The learner revises, refines, and shares music ideas and creative work.",
        "M–C1": "The learner develops understandings about people and practices in music.",
        "M–C2": "The learner develops understandings about the influence and impact of music.",
        "M–C3": "The learner develops understandings about the roles, purposes, and meanings of music.",
        "M–R1": "The learner generates initial reactions to music experiences.",
        "M–R2": "The learner critically listens to, observes, and describes music experiences.",
        "M–R3": "The learner analyzes and interprets music experiences.",
        "M–R4": "The learner applies new understandings about music to construct identity and to act in the world.",
    },
    "Visual Arts": {
        "VA–M1": "The learner develops competencies for using elements and principles of artistic design.",
        "VA–M2": "The learner develops competencies for using visual art media, tools, techniques, and processes.",
        "VA–M3": "The learner develops skills in observation and depiction.",
        "VA–CR1": "The learner generates and uses ideas from a variety of sources for creating visual art.",
        "VA–CR2": "The learner develops original artworks, integrating ideas and art elements, principles, and techniques.",
        "VA–CR3": "The learner revises, refines, and shares ideas and original artworks.",
        "VA–C1": "The learner develops understandings about people and practices in the visual arts.",
        "VA–C2": "The learner develops understandings about the influence and impact of the visual arts.",
        "VA–C3": "The learner develops understandings about the roles, purposes, and meanings of the visual arts.",
        "VA–R1": "The learner generates initial reactions to visual arts experiences.",
        "VA–R2": "The learner critically observes and describes visual arts experiences.",
        "VA–R3": "The learner analyzes and interprets visual arts experiences.",
        "VA–R4": "The learner applies new understandings about visual arts to construct identity and to act in the world.",
    },
}

AREAS = [
    ("M", "Making"),
    ("CR", "Creating"),
    ("C", "Connecting"),
    ("R", "Responding"),
]


def scrape_all_arts912(
    output_dir: Path,
    progress_callback=None,
) -> dict[str, list]:
    """Scrape Arts Education Grades 9-12 (Dance, Drama, Music, Visual Arts)."""
    results: dict[str, list] = {}

    for disc_name, prefix in DISC_PREFIXES.items():
        learnings = RECURSIVE_LEARNINGS[disc_name]

        clusters: list[dict] = []
        for area_code, area_name in AREAS:
            outcomes = []
            for code, desc in sorted(learnings.items()):
                suffix = code.split("–")[1]
                if area_code == "M" and suffix.startswith("M") and not suffix.startswith("M–"):
                    outcomes.append({"code": code, "description": desc,
                                     "glo": [f"Essential Learning Area: {area_name}"],
                                     "glo_description": [f"Essential Learning Area: {area_name}"]})
                elif area_code == "CR" and suffix.startswith("CR"):
                    outcomes.append({"code": code, "description": desc,
                                     "glo": [f"Essential Learning Area: {area_name}"],
                                     "glo_description": [f"Essential Learning Area: {area_name}"]})
                elif area_code == "C" and suffix.startswith("C") and not suffix.startswith("CR"):
                    outcomes.append({"code": code, "description": desc,
                                     "glo": [f"Essential Learning Area: {area_name}"],
                                     "glo_description": [f"Essential Learning Area: {area_name}"]})
                elif area_code == "R" and suffix.startswith("R"):
                    outcomes.append({"code": code, "description": desc,
                                     "glo": [f"Essential Learning Area: {area_name}"],
                                     "glo_description": [f"Essential Learning Area: {area_name}"]})

            if outcomes:
                clusters.append({
                    "id": f"Essential Learning Area: {area_name}",
                    "title": area_name,
                    "description": f"The learner develops language and practices for {area_name.lower()} in {disc_name.lower()}.",
                    "specific_learning_outcomes": outcomes,
                })

        results[disc_name] = clusters

        output_data = {
            "subject": f"Arts Education - {disc_name}",
            "grade": "9-12",
            "course": f"9-12 {disc_name}",
            "framework_year": "Framework 2015",
            "clusters": clusters,
        }

        safe_name = disc_name.replace(" ", "_")
        filename = f"Arts_{safe_name}_9-12.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in clusters)
        if progress_callback:
            progress_callback(f"Saved {filename}: {len(clusters)} areas, {total} recursive learnings")

    return results
