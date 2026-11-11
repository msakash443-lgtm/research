"""Ethics pack templates (M4.5): consent form, information sheet, data management plan, IRB skeleton.

Pure and stateless: nothing is stored and no model is called (participant text never leaves the app).
Institution-specific facts (ethics committee name, approval number, funder, retention period, storage
location) are left as bracketed fields for the researcher to complete. They are never guessed.
Approving the design and ethics is the owner's G7 decision, recorded through the gates API.
"""

from __future__ import annotations

from dataclasses import dataclass

FILL = "[{}: to complete]"


@dataclass(frozen=True)
class EthicsDocument:
    key: str
    title: str
    body: str


def _clean(value: str | None, fallback: str) -> str:
    text = " ".join((value or "").split())
    return text or fallback


def build_ethics_pack(title: str, description: str | None = None, research_question: str | None = None) -> list[EthicsDocument]:
    """Return the four ethics documents for a project, in a fixed order."""
    study = _clean(title, FILL.format("study title"))
    purpose = _clean(description, FILL.format("purpose of the study"))
    question = _clean(research_question, FILL.format("research question"))
    return [
        EthicsDocument(
            key="information_sheet",
            title="Participant information sheet",
            body=(
                f"# Information sheet: {study}\n\n"
                f"**Purpose.** {purpose}\n\n"
                f"**Research question.** {question}\n\n"
                f"**What taking part involves.** {FILL.format('activities, duration and number of sessions')}\n\n"
                f"**Risks and benefits.** {FILL.format('known risks, discomfort and benefits')}\n\n"
                f"**Voluntary participation.** You may decline or withdraw at any time, without giving a reason, "
                f"and without any effect on your studies or employment. {FILL.format('withdrawal cut-off date after which data cannot be removed')}\n\n"
                f"**Confidentiality.** {FILL.format('how identities are protected, who sees data, pseudonymisation')}\n\n"
                f"**Use of the data.** {FILL.format('publication and sharing plans, including any repository')}\n\n"
                f"**Contact.** {FILL.format('researcher name and contact')}; {FILL.format('ethics committee contact')}\n"
            ),
        ),
        EthicsDocument(
            key="consent_form",
            title="Consent form",
            body=(
                f"# Consent form: {study}\n\n"
                "By signing below I confirm that:\n\n"
                "- I have read the information sheet and had the chance to ask questions.\n"
                "- I understand that taking part is voluntary and that I can withdraw until the cut-off date.\n"
                "- I understand how my data will be stored, pseudonymised and used.\n"
                f"- I agree to take part in this study. {FILL.format('any further consent items, e.g. audio recording, quotations, data sharing')}\n\n"
                "Participant name: ____________________   Signature: ____________________   Date: __________\n\n"
                "Researcher name: ____________________   Signature: ____________________   Date: __________\n"
            ),
        ),
        EthicsDocument(
            key="data_management_plan",
            title="Data management plan",
            body=(
                f"# Data management plan: {study}\n\n"
                f"**Data collected.** {FILL.format('types of data, instruments and expected volume')}\n\n"
                "**Identifiers.** Participants are given a pseudonymous code at collection. The code-to-identity key "
                f"is stored separately from the data. {FILL.format('key storage location and access list')}\n\n"
                f"**Storage and security.** {FILL.format('storage location, encryption and backup')}\n\n"
                f"**Retention and deletion.** {FILL.format('retention period and deletion date')}\n\n"
                "**Sharing.** Only anonymised data is shared. "
                f"{FILL.format('repository, licence and access conditions')}\n\n"
                "**Third-party services.** "
                f"{FILL.format('third-party processing: confirm the local model endpoint is the only processor, or none')} "
                f"{FILL.format('any other service that touches data')}\n\n"
                f"**Responsibilities.** {FILL.format('data steward and their contact')}\n"
            ),
        ),
        EthicsDocument(
            key="irb_skeleton",
            title="Ethics committee application skeleton",
            body=(
                f"# Ethics committee application: {study}\n\n"
                f"1. **Title.** {study}\n"
                f"2. **Principal investigator.** {FILL.format('name, role, affiliation')}\n"
                f"3. **Background and rationale.** {FILL.format('from the literature review')}\n"
                f"4. **Research question.** {question}\n"
                f"5. **Design and procedures.** {FILL.format('design, sampling, recruitment, procedures')}\n"
                f"6. **Participants.** {FILL.format('population, inclusion and exclusion criteria, numbers')}\n"
                f"7. **Risks and mitigation.** {FILL.format('risks and how they are reduced')}\n"
                f"8. **Consent process.** {FILL.format('consent and information documents attached')}\n"
                f"9. **Data management.** See the data management plan.\n"
                f"10. **Funding and conflicts of interest.** {FILL.format('funder and declarations')}\n"
                f"11. **Ethics committee reference.** {FILL.format('reference number, once issued')}\n"
            ),
        ),
    ]
