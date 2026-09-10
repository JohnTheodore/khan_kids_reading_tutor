"""Shared Khan curriculum labels and ordering conventions."""

from __future__ import annotations

GRADE_NAMES = (
    "Preschool (Age 2)",
    "Preschool (Age 3)",
    "Preschool (Age 4)",
    "Kindergarten",
    "1st Grade",
    "2nd Grade",
)
GRADE_SLUGS = (
    "preschool-age-2",
    "preschool-age-3",
    "preschool-age-4",
    "kindergarten",
    "1st-grade",
    "2nd-grade",
)
REPORT_VARIANTS = ("Main", "Practice 1", "Practice 2", "Basic")
LEARNING_SEQUENCE = ("Basic", "Main", "Practice 1", "Practice 2")
REPORT_GRADE_LABELS = {
    "Preschool (Age 2)": "Pre-K.Age2 : ELA",
    "Preschool (Age 3)": "Pre-K.Age3 : ELA",
    "Preschool (Age 4)": "Pre-K.Age4 : ELA",
    "Kindergarten": "K : ELA",
    "1st Grade": "Grade1 : ELA",
    "2nd Grade": "Grade2 : ELA",
}
CURRICULUM_PATH_GRADE_TOKENS = {
    "Preschool (Age 2)": "A2:",
    "Preschool (Age 3)": "A3:",
    "Preschool (Age 4)": "A4:",
    "Kindergarten": "K:",
    "1st Grade": "1:",
    "2nd Grade": "2:",
}


def normalize_report_grade_label(label: str) -> str:
    replacements = {
        "Pre-K.Age2": "Preschool (Age 2)",
        "Pre-K.Age3": "Preschool (Age 3)",
        "Pre-K.Age4": "Preschool (Age 4)",
        "Grade1": "1st Grade",
        "Grade2": "2nd Grade",
        "K :": "Kindergarten:",
        " :": ":",
    }
    for source, destination in replacements.items():
        label = label.replace(source, destination)
    return label
