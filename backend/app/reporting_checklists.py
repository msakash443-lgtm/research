"""Reporting-guideline checklists (spec §9).

Checklist text is sourced from the named primary publications or their official
EQUATOR/PRISMA checklist publications.  The API exposes the published item
headings verbatim enough to identify the required reporting point; source
metadata is kept separately so consumers can open the authoritative checklist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    section: str
    topic: str


@dataclass(frozen=True)
class ChecklistSource:
    citation: str
    url: str


PRISMA_2020 = (
    ChecklistItem("1", "Title", "Identify the report as a systematic review"),
    ChecklistItem("2", "Abstract", "Structured summary"),
    ChecklistItem("3", "Introduction", "Rationale"),
    ChecklistItem("4", "Introduction", "Objectives"),
    ChecklistItem("5", "Methods", "Eligibility criteria"),
    ChecklistItem("6", "Methods", "Information sources"),
    ChecklistItem("7", "Methods", "Search strategy"),
    ChecklistItem("8", "Methods", "Selection process"),
    ChecklistItem("9", "Methods", "Data collection process"),
    ChecklistItem("10", "Methods", "Data items"),
    ChecklistItem("11", "Methods", "Study risk of bias assessment"),
    ChecklistItem("12", "Methods", "Effect measures"),
    ChecklistItem("13", "Methods", "Synthesis methods"),
    ChecklistItem("14", "Methods", "Reporting bias assessment"),
    ChecklistItem("15", "Methods", "Certainty assessment"),
    ChecklistItem("16", "Results", "Study selection"),
    ChecklistItem("17", "Results", "Study characteristics"),
    ChecklistItem("18", "Results", "Risk of bias in studies"),
    ChecklistItem("19", "Results", "Results of individual studies"),
    ChecklistItem("20", "Results", "Results of syntheses"),
    ChecklistItem("21", "Results", "Reporting biases"),
    ChecklistItem("22", "Results", "Certainty of evidence"),
    ChecklistItem("23", "Discussion", "Interpretation, limitations and implications"),
    ChecklistItem("24", "Other information", "Registration and protocol"),
    ChecklistItem("25", "Other information", "Support"),
    ChecklistItem("26", "Other information", "Competing interests"),
    ChecklistItem("27", "Other information", "Availability of data, code and materials"),
)


def _items(section: str, topics: tuple[str, ...]) -> tuple[ChecklistItem, ...]:
    return tuple(ChecklistItem(str(index), section, topic) for index, topic in enumerate(topics, 1))


PRISMA_SCR = _items("PRISMA-ScR", (
    "Identify the report as a scoping review", "Structured summary", "Rationale", "Objectives",
    "Protocol and registration", "Eligibility criteria", "Information sources", "Search",
    "Selection of sources of evidence", "Data charting process", "Data items", "Critical appraisal of individual sources of evidence",
    "Synthesis of results", "Selection of sources of evidence", "Characteristics of sources of evidence",
    "Critical appraisal within sources of evidence", "Results of individual sources of evidence",
    "Synthesis of results", "Summary of evidence, limitations and conclusions", "Funding",
))
PRISMA_P = _items("PRISMA-P", (
    "Identification", "Update", "Registration", "Authors", "Contributions", "Amendments",
    "Rationale", "Objectives", "Eligibility criteria", "Information sources", "Search strategy",
    "Study records", "Data collection process", "Data items", "Outcomes and prioritization",
    "Risk of bias in individual studies", "Data synthesis",
))
STROBE = _items("STROBE", (
    "Title and abstract", "Background/rationale", "Objectives", "Study design", "Setting",
    "Participants", "Variables", "Data sources/measurement", "Bias", "Study size",
    "Quantitative variables", "Statistical methods", "Participants", "Descriptive data",
    "Outcome data", "Main results", "Other analyses", "Key results", "Limitations",
    "Interpretation", "Generalisability", "Funding",
))
COREQ = _items("COREQ", (
    "Interviewer/facilitator", "Credentials", "Occupation", "Gender", "Experience and training",
    "Relationship established with participants", "Participant knowledge of the interviewer",
    "Interviewer characteristics", "Methodological orientation and theory", "Sampling",
    "Method of approach", "Sample size", "Non-participation", "Setting of data collection",
    "Presence of non-participants", "Description of sample", "Interview guide", "Repeat interviews",
    "Audio/visual recording", "Duration", "Field notes", "Data saturation", "Transcripts returned",
    "Number of data coders", "Description of the coding tree", "Derivation of themes",
    "Software", "Participant checking", "Quotations", "Data and findings consistency",
    "Clarity of major themes", "Clarity of minor themes",
))
SRQR = _items("SRQR", (
    "Title", "Abstract", "Problem formulation", "Qualitative approach and research paradigm",
    "Researcher characteristics and reflexivity", "Context", "Sampling strategy",
    "Ethical issues pertaining to human subjects", "Data collection methods",
    "Data collection instruments and technologies", "Units of study", "Data processing",
    "Data analysis", "Synthesis and interpretation", "Links to empirical data",
    "Integration with prior work, limitations and trustworthiness", "Conflicts of interest",
    "Funding", "Data availability", "Acknowledgments", "Information for readers",
))
MOOSE = _items("MOOSE", (
    "Problem definition", "Hypothesis statement", "Study outcome(s)", "Type of exposure or intervention",
    "Study design(s)", "Population studied", "Qualifications of searchers", "Search strategy and databases",
    "Effort to include all available studies", "Communications with investigators", "Data extraction",
    "Quality and validity assessment", "Heterogeneity assessment", "Statistical methods",
    "Tables and graphics", "Study estimates", "Descriptive information for each study",
    "Sensitivity testing", "Confounding", "Publication bias", "Quantitative summary",
    "Justification for exclusion", "Quality of included studies", "Alternative explanations",
    "Future research", "Conclusions",
))
CONSORT = _items("CONSORT 2025", (
    "Title", "Abstract", "Background and objectives", "Trial design", "Participants",
    "Interventions", "Outcomes", "Sample size", "Randomisation sequence generation",
    "Allocation concealment mechanism", "Implementation", "Blinding", "Statistical methods",
    "Participant flow", "Recruitment", "Baseline data", "Numbers analysed", "Outcomes and estimation",
    "Ancillary analyses", "Harms", "Limitations", "Generalisability", "Interpretation",
    "Registration", "Protocol and funding",
))
ENTREQ = _items("ENTREQ", (
    "Aim", "Synthesis methodology", "Approach to synthesis", "Inclusion criteria", "Data sources",
    "Electronic search strategy", "Study selection", "Rationale for appraisal",
    "Appraisal instrument", "Details of appraisal", "Data extraction", "Software",
    "Number of reviewers", "Coding", "Study comparison", "Derivation of themes",
    "Quotations", "Summary of findings", "Limitations", "Conclusions", "Funding",
))
CHEERS = _items("CHEERS 2022", (
    "Title", "Abstract", "Background and objectives", "Health economic analysis plan",
    "Population and subgroups", "Setting and location", "Comparators", "Perspective",
    "Time horizon", "Discount rate", "Selection of health outcomes", "Measurement of effectiveness",
    "Measurement and valuation of preference-based outcomes", "Estimating resources and costs",
    "Currency, price date and conversion", "Model-based methods", "Analytics and assumptions",
    "Study parameters", "Summary of results", "Characterising uncertainty", "Characterising heterogeneity",
    "Background", "Limitations", "Generalisability", "Conclusions", "Conflicts of interest",
    "Source of funding", "Availability of data",
))

CHECKLIST_SOURCES = {
    "PRISMA-2020": ChecklistSource("Page et al., BMJ 2021;372:n71", "https://doi.org/10.1136/bmj.n71"),
    "PRISMA-ScR": ChecklistSource("Tricco et al., Ann Intern Med 2018;169:467-473", "https://doi.org/10.7326/M18-0850"),
    "PRISMA-P": ChecklistSource("Shamseer et al., BMJ 2015;350:g7647", "https://doi.org/10.1136/bmj.g7647"),
    "MOOSE": ChecklistSource("Stroup et al., JAMA 2000;283:2008-2012", "https://doi.org/10.1001/jama.283.15.2008"),
    "STROBE": ChecklistSource("von Elm et al., Lancet 2007;370:1453-1457", "https://doi.org/10.1016/S0140-6736(07)61602-X"),
    "CONSORT": ChecklistSource("Hopewell et al., CONSORT 2025, BMJ 2025;388:e081123", "https://www.consort-spirit.org/"),
    "COREQ": ChecklistSource("Tong et al., Int J Qual Health Care 2007;19:349-357", "https://doi.org/10.1093/intqhc/mzm042"),
    "SRQR": ChecklistSource("O'Brien et al., Acad Med 2014;89:1245-1251", "https://doi.org/10.1097/ACM.0000000000000388"),
    "ENTREQ": ChecklistSource("Tong et al., BMC Med Res Methodol 2012;12:181", "https://doi.org/10.1186/1471-2288-12-181"),
    "CHEERS": ChecklistSource("Husereau et al., CHEERS 2022, Value Health 2022;25:3-9", "https://doi.org/10.1016/j.jval.2021.11.1351"),
}

_CHECKLISTS = {
    "PRISMA-2020": PRISMA_2020, "PRISMA-ScR": PRISMA_SCR, "PRISMA-P": PRISMA_P,
    "MOOSE": MOOSE, "STROBE": STROBE, "CONSORT": CONSORT, "COREQ": COREQ,
    "SRQR": SRQR, "ENTREQ": ENTREQ, "CHEERS": CHEERS,
}


def checklist_for(guideline: str | None) -> tuple[ChecklistItem, ...] | None:
    if guideline is None:
        return None
    return _CHECKLISTS.get(guideline)

