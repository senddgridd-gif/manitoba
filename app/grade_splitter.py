"""Utility to split combined-grade JSON output into per-grade files.

Two modes:
1. filter mode: SLOs have a 'grade' field → filter clusters by grade
2. duplicate mode: SLOs have no 'grade' field → copy entire content per grade
"""

import copy
import json
from pathlib import Path


GRADE_EXPANSIONS = {
    "K-3": ["K", "1", "2", "3"],
    "K-6": ["K", "1", "2", "3", "4", "5", "6"],
    "K-8": ["K", "1", "2", "3", "4", "5", "6", "7", "8"],
    "K-12": ["K", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"],
    "4-6": ["4", "5", "6"],
    "4-8": ["4", "5", "6", "7", "8"],
    "7-12": ["7", "8", "9", "10", "11", "12"],
    "9-12": ["9", "10", "11", "12"],
    "S1-S4": ["S1", "S2", "S3", "S4"],
}


def _has_grade_field(data: dict) -> bool:
    """Check if SLOs in the data have a 'grade' field."""
    for cluster in data.get("clusters", []):
        for slo in cluster.get("specific_learning_outcomes", []):
            return "grade" in slo
    return False


def split_to_per_grade(
    data: dict,
    output_dir: Path,
    filename_prefix: str,
    grades: list[str] | None = None,
    progress_callback=None,
) -> list[str]:
    """Split a combined-grade output into per-grade files.

    Returns list of created filenames.
    """
    grade_range = data.get("grade", "")
    if not grades:
        grades = GRADE_EXPANSIONS.get(grade_range)

    if not grades:
        return []

    created = []
    has_grade = _has_grade_field(data)

    for grade in grades:
        grade_data = copy.deepcopy(data)
        grade_data["grade"] = grade

        if has_grade:
            # Filter SLOs to only those matching this grade
            for cluster in grade_data["clusters"]:
                cluster["specific_learning_outcomes"] = [
                    slo for slo in cluster["specific_learning_outcomes"]
                    if str(slo.get("grade", "")).lower() == str(grade).lower()
                ]
            # Remove empty clusters
            grade_data["clusters"] = [
                c for c in grade_data["clusters"]
                if c["specific_learning_outcomes"]
            ]

        safe_grade = grade.replace(" ", "_")
        filename = f"{filename_prefix}_Gr{safe_grade}.json"
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(grade_data, f, indent=4, ensure_ascii=False)

        total = sum(len(c["specific_learning_outcomes"]) for c in grade_data["clusters"])
        if progress_callback:
            progress_callback(f"  Split: {filename} ({total} outcomes)")

        created.append(filename)

    return created
