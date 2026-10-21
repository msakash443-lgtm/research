# Market research: evidence-synthesis tools (X.81)

Status: docs only. No code changed, no test applies.
Checked: 2026-10-21, from web search results. Vendor pages were not opened directly, so every price and feature below must be re-checked on the vendor page before any decision that depends on it.

Source labels used below:
- **[official]** vendor or publisher page.
- **[third-party]** aggregator or blog. Treat as unverified.
- **[INFERENCE]** our arithmetic or assumption, not from a source.

## 1. Competitors: features and pricing

| Tool | Pricing (as found) | Source | Notes relevant to us |
|---|---|---|---|
| Elicit | Plus $11/user/mo, billed $132/yr; Pro $39/user/mo, billed $468/yr | [official] elicit.com/pricing | Pro adds a Systematic Review workflow (screens up to 5,000 papers) and 20 table columns. Plus: RIS/CSV/BIB/PDF/DOCX export, 5 columns. Third-party sites quote different figures (e.g. Scale $89/mo, Team $79/seat/mo); ignore those. |
| Covidence | Single $339 USD/yr (one review); Package $907 USD/yr (up to 3 reviews); organisational custom | [official] covidence.org/pricing | Priced per review, not per seat. Free for registered Cochrane reviews. Free trial of 500 records. |
| Rayyan | Free ($0; 3 active reviews, 2 reviewers); Essential $4.99/seat/mo annual; Advanced $8.33/seat/mo annual; Business/Academic $41.67/licence/mo annual (5-licence minimum); Enterprise custom | [third-party] Educators Technology, Sept 2026 | Not verified on rayyan.ai. Confirm before use. |
| DistillerSR | No official public list price found. Third-party listing: from about $215/user/mo. Student: free 4 months, then $15/mo (university LibGuide). Enterprise often $5,000+/yr | [third-party] SourceForge; UVic LibGuide; distillersr.com student page | Official student page exists; the general price was not found on an official page. |
| Undermind | Free: 3 deep searches/mo. Pro: $16/mo billed annually (about $20/mo monthly, 30 searches). Team: $15/user/mo, 5-seat minimum. Enterprise custom | [third-party] aggregators | Figures disagree between sources. No official page was found. |

Feature comparison as found in the sources above. Not checked against live product, so treat as unverified:
- Screening, extraction, and export exist in Elicit (Pro systematic-review workflow), Covidence, Rayyan, and DistillerSR.
- Undermind is described as AI-led deep search rather than a review-management tool. [third-party]

Where this leaves us: the clearest market gap is per-review or per-seat pricing at the low end (Elicit Plus, Rayyan Essential), with the scholar's own review as the unit. We have no verified data on the features of Undermind beyond the third-party summary.

## 2. Bottom-up market size

Upper bound on the people who could use this product:
- Global researchers (full-time equivalent): **8.854 million in 2018**. Source: [official] UNESCO Science Report 2021 factsheet (unesco.org). Newer global totals were not found. UNESCO's 2026 release reports density per million for 2023 (1,486 per million), not a total. [official] uis.unesco.org/en/2026-rd-data-release

Scenario (not a forecast):
- [INFERENCE] Share of researchers who run a systematic or scoping review in a given year: unknown. No source found. Plugging in 1% (assumption) gives about 88,500 potential users.
- [INFERENCE] At Elicit Plus's $132/yr list price, 88,500 users would be about $11.7M/yr. At Covidence Single's $339/yr, about $30M/yr. These numbers are arithmetic on assumed inputs, not a market estimate. They cannot be used to size a market until the adoption share is measured.

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
- **ICMJE** [official] icmje.org/recommendations/browse/artificial-intelligence/ai-use-by-authors.html (Section II.A.4, updated January 2026):
  - Disclose AI use at submission, in the cover letter and in the manuscript.
  - AI cannot be an author. Authors are responsible for accuracy and must check AI output.
  - Nondisclosure may be treated as misconduct in some circumstances.
  - AI used for data handling or analysis belongs in Methods. [third-party secondary summary: Springer, Indian Pediatrics 2026] Check against the official text.
  - Reviewers must not upload manuscripts to AI tools that cannot guarantee confidentiality. [official]

Implication for us: every AI-produced artefact (screening decision, extraction, drafted text) must carry tool name, version, and date so the scholar can make a disclosure. This matches rule 5 in `AGENTS.md` (every AI output records model_id, prompt_version, inputs, actor).

## 4. GDPR and ethics-board requirements

Sources: EU GDPR text (Article 89), UK ICO research guidance, and EDPB guidelines on scientific research as reported in 2026 law-firm summaries. The EDPB summaries are third-party; read the EDPB text before relying on them.

- **Article 9(2)(j)** [official text, gdpr-text.com mirror]: special-category data (health, etc.) may be processed for research only if it is in line with Article 89(1) and based on EU or member-state law.
- **Article 89(1)** [official text]: safeguards must include data minimisation. Pseudonymisation is named as one measure. If the purpose can be met with anonymised data, use that. Pseudonymised data is still personal data under the GDPR (Recital 26 [official]).
- **Article 6**: research does not by itself give a lawful basis. A separate Article 6 basis is still needed. [official text]
- **ICO** [official] ico.org.uk research provisions: anonymise or pseudonymise as early as possible, before research use.
- **EDPB 2026 guidelines on scientific research** [third-party summary, Ropes & Gray, Goodwin]: confirm against the EDPB text. Summaries report a documented anonymise → pseudonymise → identifiable hierarchy, and that research interest can carry weight under Article 6(1)(f) legitimate interest.
- **Ethics-board requirements** [INFERENCE]: no source found yet that lists current ethics-board requirements for AI-assisted evidence synthesis. Each institution sets its own rules. The scholar's ethics board should be consulted before any study uses human-subject data from this tool. This is an open item, not a verified finding.

Implication for us: a project that stores identifiable human-subject data needs a documented safeguards section. Anonymise or pseudonymise before ingestion if the purpose allows it.

## 5. Reporting standard (for the evidence-synthesis output)

- **PRISMA 2020** [official, Page et al. 2021, BMJ/PLOS Med; PMC8008539]: 27-item checklist (42 rows with sub-items) and a flow diagram with four templates. Automation-excluded records must be reported separately. PRISMA is a reporting guideline, not a quality score.

## 6. Open items and what was not verified

- Vendor pricing pages were not opened directly. Re-check Rayyan, DistillerSR, and Undermind on the official sites before any pricing claim.
- No official Undermind or DistillerSR list price was found.
- Bottom-up market size lacks a measured adoption rate. The numbers in §2 are scenarios only.
- Ethics-board requirements are unresolved. Needs institution-level input.
- EDPB 2026 guidance was read only through third-party summaries.
- The publisher rules above were read through search results and may have changed since 2026-10-21.
