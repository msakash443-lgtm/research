# Market research: evidence-synthesis tools (X.81)

Status: docs only. No code changed, no test applies.
Checked: 2026-10-23, using the official pages and documents linked below plus search-result verification where a page could not be opened directly. Prices and features are time-sensitive; re-check before a purchasing or product decision.

Source labels used below:
- **[official]** official vendor or publisher page/document, linked and checked or independently confirmed in the current search result.
- **[official URL; not directly opened]** the vendor/publisher URL is authoritative, but this review did not open it directly.
- **[third-party]** aggregator or blog. Treat as unverified.
- **[INFERENCE]** our arithmetic or assumption, not from a source.

## 1. Competitors: features and pricing

| Tool | Pricing (as found) | Source | Notes relevant to us |
|---|---|---|---|
| Elicit | Pro $49/user/mo, billed annually at $588/yr; Scale $229/user/mo, billed annually at $2,748/yr; annual billing is shown as an effective $39/mo for Pro and $89/mo for Scale | [official] [elicit.com/pricing](https://elicit.com/pricing) | Pro includes the systematic-review workflow; Scale is an official plan, not a third-party figure. Plan features and limits change, so verify them before relying on them. |
| Covidence | Single $339 USD/yr (one review); Package $907 USD/yr (up to 3 reviews); organisational custom | [official] [covidence.org/pricing](https://www.covidence.org/pricing/) | Priced per review, not per seat. The 500-record trial is listed on the pricing page. “Free for registered Cochrane reviews” was not verified on that page and is omitted here. |
| Rayyan | Free ($0; 3 active reviews, 2 reviewers); Essential $4.99/seat/mo annual; Advanced $8.33/seat/mo annual; Business/Academic $41.67/licence/mo annual (5-licence minimum); Enterprise custom | [third-party] Educators Technology, Sept 2026 | Not verified on rayyan.ai. Confirm before use. |
| DistillerSR | No official public list price found. Third-party listing: from about $215/user/mo. Student: free 4 months, then $15/mo (university LibGuide). Enterprise often $5,000+/yr | [third-party] SourceForge; UVic LibGuide; distillersr.com student page | Official student page exists; the general price was not found on an official page. |
| Undermind | Free: 3 deep searches/mo. Pro: $16/mo billed annually (about $20/mo monthly, 30 searches). Team: $15/user/mo, 5-seat minimum. Enterprise custom | [third-party] aggregators | Figures disagree between sources. No official page was found. |

Feature comparison as found in the sources above. Not checked against live product, so treat as unverified:
- Screening, extraction, and export are described in Elicit (Pro systematic-review workflow), Covidence, Rayyan, and DistillerSR.
- Undermind is described as AI-led deep search rather than a review-management tool. [third-party]

Where this leaves us: the clearest market gap is per-review or per-seat pricing at the low end, but Elicit plan names and prices changed in this review and Rayyan remains third-party-only. We have no verified data on the features of Undermind beyond the third-party summary.

## 2. Bottom-up market size

Upper bound on the people who could use this product:
- Global researchers (full-time equivalent): **8.854 million in 2018**. Source: [official] UNESCO Science Report 2021 factsheet (unesco.org). Newer global totals were not found. UNESCO's 2026 release reports density per million for 2023 (1,486 per million), not a total. [official] uis.unesco.org/en/2026-rd-data-release

Scenario (not a forecast):
- [INFERENCE] Share of researchers who run a systematic or scoping review in a given year: unknown. No source found. Plugging in 1% (assumption) gives about 88,500 potential users.
- [INFERENCE] At Elicit Pro's $588/yr annual-billing price, 88,500 users would be about $52.0M/yr. At Covidence Single's $339/yr, about $30.0M/yr. These numbers are arithmetic on assumed inputs, not a market estimate. They cannot be used to size a market until the adoption share is measured.

## 3. Journal and publisher AI-disclosure rules

These rules matter for any output this product produces that goes into a manuscript.

- **Elsevier** [official] elsevier.com/about/policies-and-standards/generative-ai-policies-for-journals:
  - AI cannot be an author or cite as one.
  - Disclose generative AI use in a "Declaration of Generative AI and AI-assisted technologies in the writing process" section, placed before the reference list.
  - Basic grammar and spelling checks are exempt. Accessibility tools are exempt.
  - Data visualisations made with AI must be reproducible, and the tool, version, and developer go in Methods.
- **Springer Nature** [official] link.springer.com/journal/43681/submission-guidelines and group.springernature.com AI guidance:
  - LLMs cannot be authors. Document LLM use in Methods (or the nearest equivalent). Book manuscripts: acknowledgements.
  - "AI assisted copy editing" does not need declaring.
  - Disclosure should include tool version, usage dates, prompts, and the extent of AI contribution. [official] group.springernature.com
- **ICMJE** [official] [icmje.org AI guidance](https://www.icmje.org/recommendations/browse/artificial-intelligence/ai-use-by-authors.html), Section V.A “Use of AI by Authors”:
  - Disclose AI use at submission, in the cover letter and in the manuscript where appropriate.
  - AI cannot be an author. Authors remain responsible for accuracy, integrity, originality and other obligations.
  - Authors must check AI output for errors, omissions, bias and incompleteness, and ensure proper attribution.
  - Nondisclosure may require corrective action and, in some circumstances, may be misconduct.
  - AI used for data handling or analysis belongs in Methods.
  - Reviewers must not upload manuscripts to AI tools that cannot guarantee confidentiality.

Implication for us: every AI-produced artefact (screening decision, extraction, drafted text) must carry tool name, version, and date so the scholar can make a disclosure. This matches rule 5 in `AGENTS.md` (every AI output records model_id, prompt_version, inputs, actor).

Sources: EU GDPR text (Article 89), UK ICO research guidance, and the official EDPB Guidelines 1/2026 linked below.

- **Article 9(2)(j)** [official text, GDPR mirror]: special-category data (health, etc.) may be processed for research only if it is in line with Article 89(1) and based on EU or member-state law.
- **Article 89(1)** [official text]: safeguards must include data minimisation. Pseudonymisation is named as one measure. If the purpose can be met with anonymised data, use that. Pseudonymised data is still personal data under the GDPR (Recital 26 [official]).
- **Article 6**: research does not by itself give a lawful basis. A separate Article 6 basis is still needed. [official text]
- **ICO** [official] ico.org.uk research provisions: anonymise or pseudonymise as early as possible, before research use.
- **EDPB Guidelines 1/2026 on processing of personal data for scientific research purposes** [official] [PDF](https://www.edpb.europa.eu/system/files/2026-04/edpb_guidelines_202601_scientificresearch_en.pdf): adopted 15 April 2026; the public consultation ran from 16 April through 25 June 2026 and is now closed. This v1 document and the EDPB's finalisation status must be checked before treating it as settled guidance. It addresses scientific-research qualification, further processing, broad and dynamic consent, data-subject rights, controller/processor roles, and safeguards including anonymisation and pseudonymisation.
- **Ethics-board requirements** [INFERENCE]: no source found that lists universal current ethics-board requirements for AI-assisted evidence synthesis. Each institution sets its own rules. The scholar's ethics board should be consulted before any study uses human-subject data from this tool. This is an open item, not a verified finding.

Implication for us: a project that stores identifiable human-subject data needs a documented safeguards section. Anonymise or pseudonymise before ingestion if the purpose allows it.

## 5. Reporting standard (for the evidence-synthesis output)

- **PRISMA 2020** [official, Page et al. 2021, BMJ/PLOS Med; PMC8008539]: 27-item checklist (42 rows with sub-items) and a flow diagram with four templates. Automation-excluded records must be reported separately. PRISMA is a reporting guideline, not a quality score.

- Vendor pricing and features are time-sensitive. Elicit and Covidence official pricing pages were checked or independently confirmed in the current search results; re-check before a purchasing decision.
- Rayyan, DistillerSR, and Undermind remain unverified against their official product pages.
- No official Undermind or DistillerSR general list price was found.
- Bottom-up market size lacks a measured adoption rate. The numbers in §2 are scenarios only.
- Ethics-board requirements are unresolved. Needs institution-level input.
- EDPB Guidelines 1/2026 consultation closed on 25 June 2026; confirm the finalisation status before relying on the v1 text as settled guidance.
- The publisher rules above are current as checked on 2026-10-23 but may change.
