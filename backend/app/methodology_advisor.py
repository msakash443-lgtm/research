"""Project-aware, deterministic research design recommendations.

Pure rule tables; recommendations are advisory and the scholar remains responsible
for selecting the design and obtaining gate G7 approval.
"""

from __future__ import annotations

from typing import Any

from app import design_tools

RESEARCH_TYPES = {
    "full_journey",
    "literature_review",
    "qualitative",
    "quantitative_survey",
    "experimental",
    "mixed_methods",
}

SIZE_TESTS = {
    "describe": ["survey_proportion"],
    "relate": ["correlation", "regression"],
    "compare_groups": ["two_means", "anova_oneway", "two_proportions"],
    "test_causal": ["two_means", "anova_oneway"],
    "explore": [],
    "understand_experience": [],
    "explain_with_depth": ["correlation", "regression"],
}

DATA_COLLECTION = {
    "quantitative": [
        ("Structured questionnaire with validated scales", "Measures defined constructs consistently across respondents.", "Self-report and common-method bias can distort associations."),
        ("Secondary or administrative data", "Existing records can measure outcomes without new participant contact.", "Variables are defined by others and access terms may constrain use."),
    ],
    "qualitative": [
        ("Semi-structured interviews", "Flexible prompts elicit detailed accounts relevant to the research question.", "Interviewer effects can shape what participants disclose."),
        ("Focus groups", "Discussion reveals shared language and differences in participants' views.", "Group conformity can suppress minority or sensitive views."),
        ("Documents and artefacts", "Existing materials show practices and meanings in their original context.", "Materials were created for purposes other than this study."),
        ("Observation with field notes", "Direct observation captures practices that participants may not report.", "The observer's presence can change behaviour."),
    ],
    "review": [
        ("Database searches with a documented strategy (this app's Search step)", "A reproducible search identifies records against explicit eligibility criteria.", "Database coverage limits which relevant studies can be found."),
        ("Citation chasing (snowballing)", "Reference and citation links can surface studies missed by database queries.", "Citation networks can reproduce visibility and citation bias."),
    ],
}

SAMPLING = {
    "quantitative": {
        "probability": [
            ("Simple random", "Each unit on a complete sampling frame has a known equal selection chance.", "Requires a complete frame and may under-represent small subgroups by chance."),
            ("Stratified random", "Sampling within strata improves coverage of prespecified subgroups.", "Requires reliable stratum information and sufficient cases per stratum."),
            ("Cluster", "Sampling intact groups reduces fieldwork cost when individuals are dispersed.", "Within-cluster similarity reduces effective sample size."),
        ],
        "nonprobability": [
            ("Convenience", "Recruitment from accessible participants is feasible when no frame exists.", "Results cannot be generalised statistically."),
            ("Quota", "Recruitment targets can ensure selected characteristics are represented.", "Results cannot be generalised statistically."),
        ],
    },
    "qualitative": [
        ("Purposive", "Selects information-rich participants who can address the research question.", "Selection depends on researcher judgement and may omit other perspectives."),
        ("Snowball", "Participants can refer others in a hard-to-reach population.", "Networks can produce a socially narrow sample."),
        ("Theoretical (grounded theory)", "Sampling follows emerging analytic categories until the developing account is sufficient.", "Requires iterative collection and analysis rather than a fixed initial sample."),
    ],
    "mixed": {
        "probability": [
            ("Simple random", "Each unit on a complete sampling frame has a known equal selection chance.", "Requires a complete frame and may under-represent small subgroups by chance."),
            ("Stratified random", "Sampling within strata improves coverage of prespecified subgroups.", "Requires reliable stratum information and sufficient cases per stratum."),
            ("Cluster", "Sampling intact groups reduces fieldwork cost when individuals are dispersed.", "Within-cluster similarity reduces effective sample size."),
        ],
        "nonprobability": [
            ("Convenience", "Recruitment from accessible participants is feasible when no frame exists.", "Results cannot be generalised statistically."),
            ("Quota", "Recruitment targets can ensure selected characteristics are represented.", "Results cannot be generalised statistically."),
        ],
    },
}

ANALYSIS: dict[str, list[tuple[str, str, str]]] = {
    "describe": [("Descriptive statistics with confidence intervals", "Summarises the observed distribution and its sampling uncertainty.", "Intervals rely on an appropriate sampling design and model assumptions.")],
    "relate": [
        ("Correlation", "Quantifies the direction and strength of association between variables.", "Association does not establish temporal order or causation."),
        ("Multiple regression", "Estimates adjusted associations while accounting for measured covariates.", "Unmeasured confounding and model misspecification remain possible."),
        ("Structural equation modelling", "Tests relationships among observed and latent variables in one model.", "Needs a large sample and a defensible measurement model."),
    ],
    "compare_groups": [
        ("t-test / one-way ANOVA", "Compares group means for a continuous outcome.", "Independence and distributional assumptions should be checked."),
        ("Chi-square test", "Tests association between categorical variables.", "Sparse expected cell counts make the approximation unreliable."),
        ("ANCOVA or propensity-score adjustment", "Adjusts group comparisons for measured baseline differences.", "Cannot remove bias from unmeasured confounders."),
    ],
    "test_causal": [
        ("ANOVA/ANCOVA on randomised groups", "Estimates treatment-group differences under random assignment.", "Attrition, contamination and non-compliance can weaken causal inference."),
    ],
    "explore": [
        ("Reflexive thematic analysis (Braun & Clarke)", "Develops patterned meaning across accounts through iterative interpretation.", "Themes are interpretive and require reflexive documentation."),
        ("Interpretative phenomenological analysis", "Examines how participants make sense of lived experience.", "Small, relatively homogeneous samples limit transferability."),
        ("Grounded-theory coding (this app's qualitative coding step)", "Builds an explanatory account through iterative coding and comparison.", "Requires constant comparison and theoretical sampling."),
    ],
    "understand_experience": [],
    "explain_with_depth": [],
}

NONRANDOMIZED_CAUSAL_ANALYSIS = [
    ("Difference-in-differences", "Compares outcome changes across treated and comparison groups.", "Requires a credible parallel-trends assumption."),
    ("Regression discontinuity", "Uses a threshold rule to estimate a local treatment effect.", "Requires no manipulation around the threshold and supports a local estimate."),
]
ANALYSIS["understand_experience"] = list(ANALYSIS["explore"])
ANALYSIS["explain_with_depth"] = [
    *ANALYSIS["relate"],
    ANALYSIS["explore"][0],
    ("Joint-display integration", "Places quantitative patterns beside qualitative explanations to examine convergence and difference.", "Integration is weak if the strands address different questions or samples."),
]
REVIEW_ANALYSIS = [
    ("Narrative synthesis", "Organises findings across included studies against the review question.", "Synthesis depends on the completeness and comparability of included evidence."),
    ("Meta-analysis (when studies are comparable; this app's meta-analysis step)", "Pools compatible effect estimates to summarise evidence quantitatively.", "Pooling is inappropriate when designs, measures or populations are not sufficiently comparable."),
]


def _items(rows: list[tuple[str, str, str]], first: str, second: str) -> list[dict[str, str]]:
    return [{first: name, second: why, "limitations": limitation} for name, why, limitation in rows]


def recommend(
    research_type: str,
    goal: str,
    *,
    human_participants: bool,
    can_randomize: bool | None = None,
    accessible_population: bool | None = None,
    vulnerable_population: bool | None = None,
    sensitive_topic: bool | None = None,
) -> dict[str, Any]:
    if research_type not in RESEARCH_TYPES:
        raise ValueError(f"unknown research type {research_type!r}")
    base = design_tools.recommend_method(
        goal, can_randomize=can_randomize, accessible_population=accessible_population
    )
    approach = "review" if research_type == "literature_review" else base["approach"]
    cautions = list(base["cautions"])
    if research_type == "qualitative" and base["approach"] == "quantitative":
        cautions.append("Your research type is qualitative but this goal usually needs a quantitative approach; reconsider the goal or the research type.")
    if research_type in {"quantitative_survey", "experimental"} and base["approach"] == "qualitative":
        cautions.append("Your research type is quantitative but this goal usually needs a qualitative approach; reconsider the goal or the research type.")
    if research_type == "experimental" and goal != "test_causal":
        cautions.append("An experimental research type is built to test causal effects; check that the goal fits.")

    if approach == "review":
        design = {
            "candidates": ["Systematic review", "Scoping review", "Narrative review"],
            "reasoning": f"{base['goal_description']}: this calls for a review approach.",
            "advantages": ["Synthesises existing studies without collecting new participant data."],
            "disadvantages": ["The review is limited by the quality and availability of existing studies."],
            "threats_to_validity": ["Publication bias", "Search coverage", "Selective reporting"],
        }
        data_collection = _items(DATA_COLLECTION["review"], "method", "why")
        sampling = [{"strategy": "All records meeting the eligibility criteria within the stated databases and dates", "why": "Eligibility criteria define the review corpus reproducibly.", "limitations": "Language and grey-literature gaps can leave relevant evidence out."}]
    else:
        design = {
            "candidates": list(base["candidate_designs"]),
            "reasoning": f"{base['goal_description']}: this calls for a {approach} approach.",
            "advantages": list(base["advantages"]),
            "disadvantages": list(base["disadvantages"]),
            "threats_to_validity": list(base["threats_to_validity"]),
        }
        collection_rows = list(DATA_COLLECTION["quantitative"][:1]) if approach == "mixed" else list(DATA_COLLECTION[approach])
        if approach == "quantitative" and goal == "test_causal":
            collection_rows.append(("Experimental outcome measures (tasks, behavioural or physiological)", "Directly measures outcomes relevant to the causal contrast.", "Laboratory tasks and measures may create artificial conditions."))
        if approach == "mixed":
            collection_rows.extend([
                DATA_COLLECTION["qualitative"][0],
                ("Integration plan (joint display)", "Makes explicit how the quantitative and qualitative strands answer the shared question.", "Integration is often weak when strands are planned independently."),
            ])
        data_collection = _items(collection_rows, "method", "why")
        if approach == "qualitative":
            sample_rows = SAMPLING["qualitative"]
        else:
            probability = accessible_population is True
            sample_rows = SAMPLING[approach]["probability" if probability else "nonprobability"]
            if not probability and "Without access to a sampling frame, probability sampling is not possible; say so in the limitations." not in cautions:
                cautions.append("No sampling frame: probability sampling is not possible; state this as a limitation.")
        sampling = _items(sample_rows, "strategy", "why")
        if approach == "mixed":
            sampling.append({"strategy": "Purposive", "why": "Select participants who can explain the quantitative patterns.", "limitations": "Selection depends on researcher judgement and may omit other perspectives."})
            sampling.append({"strategy": "Sample relationship between strands (identical, nested, parallel)", "why": "Specifies whether the strands share, overlap or independently select participants.", "limitations": "An unclear relationship between samples can weaken integration."})

    if approach in {"quantitative", "mixed"}:
        sample_size = {"tool": "/design/sample-size", "suggested_tests": list(SIZE_TESTS[goal])}
    elif approach == "qualitative":
        sample_size = {"tool": "/design/saturation-guidance", "suggested_tests": []}
    else:
        sample_size = {"tool": None, "suggested_tests": []}

    analyses = list(REVIEW_ANALYSIS if approach == "review" else ANALYSIS[goal])
    if approach != "review" and goal == "test_causal" and can_randomize is False:
        analyses.extend(NONRANDOMIZED_CAUSAL_ANALYSIS)
    analysis = _items(analyses, "technique", "why")
    ethics: list[dict[str, str]] = []
    if human_participants:
        ethics.extend([
            {"consideration": "Informed consent", "why": "Participants need an understandable account of the study before they agree."},
            {"consideration": "Confidentiality and anonymisation", "why": "Limit access to identifiable data and remove identifiers where appropriate."},
            {"consideration": "Data protection and a data-management plan (see /ethics-pack)", "why": "Specify lawful handling, access, retention and disposal of participant data."},
            {"consideration": "Right to withdraw", "why": "Explain when and how participants may withdraw without penalty."},
            {"consideration": "Ethics-board approval before data collection (gate G7)", "why": "Obtain institutional approval before collecting participant data."},
        ])
    if vulnerable_population is True:
        ethics.append({"consideration": "Extra safeguards for vulnerable participants (guardian consent, power imbalance)", "why": "Additional safeguards reduce coercion and protect participants with reduced autonomy."})
    if sensitive_topic is True:
        ethics.append({"consideration": "Distress protocol and referral information", "why": "Participants need support options if the topic causes distress."})
    if goal == "test_causal" and can_randomize is not False:
        ethics.append({"consideration": "Equipoise and withholding of treatment; debriefing if any deception", "why": "Random allocation must be justified and any deception disclosed and debriefed."})
    if any(item["method"] == "Secondary or administrative data" for item in data_collection):
        ethics.append({"consideration": "Data-use terms and re-identification risk", "why": "Secondary or administrative records can contain identifiable information and use restrictions."})
    if approach == "review" or not human_participants:
        ethics.append({"consideration": "No participant ethics review expected; record citation integrity and publication-bias handling", "why": "A review or non-participant study still needs transparent and accurate evidence handling."})

    return {
        "research_type": research_type,
        "goal": goal,
        "goal_description": base["goal_description"],
        "approach": approach,
        "design": design,
        "data_collection": data_collection,
        "sampling": sampling,
        "sample_size": sample_size,
        "analysis": analysis,
        "ethics": ethics,
        "cautions": cautions,
        "note": "Rule-based suggestion from the project's research type and goal. The scholar chooses the design; gate G7 still applies.",
    }
