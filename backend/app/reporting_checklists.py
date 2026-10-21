"""Reporting-guideline checklists (spec §9): the items a project's reporting guideline asks it to address.

Pure data and a lookup, no DB or HTTP. A guideline is returned only once its items are written out here;
for any other guideline `checklist_for` returns None, so the API says "not shipped" instead of an empty list.

Items are short topic labels, not the guideline's own wording. Check them against the published guideline
before relying on them (PRISMA 2020: Page MJ et al., BMJ 2021;372:n71).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    section: str
    topic: str


PRISMA_2020 = (
    ChecklistItem("1", "Title", "Identify the report as a systematic review"),
    ChecklistItem("2", "Abstract", "Structured summary of the review"),
    ChecklistItem("3", "Introduction", "Rationale for the review"),
    ChecklistItem("4", "Introduction", "Explicit objectives or questions"),
    ChecklistItem("5", "Methods", "Eligibility criteria"),
    ChecklistItem("6", "Methods", "Information sources and dates searched"),
    ChecklistItem("7", "Methods", "Full search strategy for each database"),
    ChecklistItem("8", "Methods", "Selection process (who screened, how)"),
    ChecklistItem("9", "Methods", "Data collection process"),
    ChecklistItem("10", "Methods", "Data items sought"),
    ChecklistItem("11", "Methods", "Risk-of-bias assessment of studies"),
    ChecklistItem("12", "Methods", "Effect measures"),
    ChecklistItem("13", "Methods", "Synthesis methods"),
    ChecklistItem("14", "Methods", "Assessment of reporting biases"),
    ChecklistItem("15", "Methods", "Certainty-of-evidence assessment"),
    ChecklistItem("16", "Results", "Study selection, with the reasons for exclusion"),
    ChecklistItem("17", "Results", "Characteristics of included studies"),
    ChecklistItem("18", "Results", "Risk of bias in included studies"),
    ChecklistItem("19", "Results", "Results of individual studies"),
    ChecklistItem("20", "Results", "Results of syntheses"),
    ChecklistItem("21", "Results", "Reporting biases"),
    ChecklistItem("22", "Results", "Certainty of evidence"),
    ChecklistItem("23", "Discussion", "Interpretation, limitations and implications"),
    ChecklistItem("24", "Other information", "Registration and protocol"),
    ChecklistItem("25", "Other information", "Sources of funding and support"),
    ChecklistItem("26", "Other information", "Competing interests"),
    ChecklistItem("27", "Other information", "Availability of data, code and other materials"),
)

_CHECKLISTS = {"PRISMA-2020": PRISMA_2020}


def checklist_for(guideline: str | None) -> tuple[ChecklistItem, ...] | None:
    if guideline is None:
        return None
    return _CHECKLISTS.get(guideline)
