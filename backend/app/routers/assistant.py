from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit, exit_conditions as ec, stage_machine
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access, project_role
from app.database import get_db
from app.config import get_settings
from app.gates import GATE_APPROVER_ROLES, GATE_STAGES
from app.models import (
    Artifact, ArtifactStatus, AssistantConversation, AssistantMessage, ContextKind, Gate, GateStatus,
    Note, NoteKind, NoteRevision, Project, ResearchContextItem, Source, User, utcnow,
)
from app.prompt_registry import load_prompt
from app.schemas import (
    AssistantCaptureCreate, AssistantContextRead, AssistantConversationCreate, AssistantConversationDetail,
    AssistantConversationRead, AssistantMessageCreate, AssistantMessageRead, AssistantRefineCreate, AssistantRefineRead,
    ContextItemRead, NoteRead,
)
router = APIRouter(prefix="/projects/{project_id}/assistant", tags=["assistant"])
PROMPT = ("research_assistant", 1)
REFINE_PROMPT = ("research_assistant_refine", 1)
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer"],
    "properties": {"answer": {"type": "string", "minLength": 1, "maxLength": 6000}},
}
REFINE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["proposal"],
    "properties": {"proposal": {"type": "string", "minLength": 1, "maxLength": 6000}},
}


def _selected_note_context(db: Session, project: Project, user: User, note_ids: list[uuid.UUID]) -> list[dict]:
    if not note_ids:
        return []
    notes = db.scalars(select(Note).where(
        Note.id.in_(note_ids), Note.project_id == project.id, Note.ai_locked.is_(False),
        or_(Note.owner_id == user.id, Note.project_id == project.id),
    )).all()
    return [{"id": str(note.id), "body": f"<untrusted-note>{note.body}</untrusted-note>"} for note in notes]


def _context(db: Session, project: Project, user: User) -> dict:
    options = stage_machine.next_options(db, project)
    conditions = ec.conditions(db, project, project.stage)
    pending = next((code.value for code, stage in GATE_STAGES.items() if stage == project.stage), None)
    blockers = [{"id": c.id, "message": c.message, "fix": c.fix, "met": c.met} for c in conditions if not c.met]
    gates = db.scalars(select(Gate).where(Gate.project_id == project.id)).all()
    gate_state = [{"code": gate.code.value, "status": gate.status.value, "approver_roles": [role.value for role in GATE_APPROVER_ROLES[gate.code]]} for gate in gates]
    decisions = db.scalars(select(ResearchContextItem).where(ResearchContextItem.project_id == project.id, ResearchContextItem.kind == ContextKind.decision, ResearchContextItem.retired_at.is_(None)).order_by(ResearchContextItem.created_at.desc()).limit(20)).all()
    stale = db.scalars(select(Artifact).where(Artifact.project_id == project.id, Artifact.status == ArtifactStatus.stale)).all()
    evidence = db.scalars(select(Source).where(Source.project_id == project.id, Source.metadata_verified.is_(True), Source.merged_into.is_(None)).order_by(Source.created_at.desc()).limit(25)).all()
    return {
        "stage": project.stage.value,
        "pending_gate": pending,
        "blockers": blockers,
        "next": [{"stage": o.stage.value, "gate": o.gate.value if o.gate else None, "ready": o.ready} for o in options],
        "role": project_role(db, project, user).value,
        "gates": gate_state,
        "approved_decisions": [{"id": str(item.id), "kind": item.kind.value} for item in decisions],
        "stale_artifacts": [{"id": str(item.id), "kind": item.kind, "stage": item.stage.value} for item in stale],
        "selected_evidence": [{"id": str(item.id), "title": item.title} for item in evidence],
        "context_ids": [c.id for c in conditions] + [str(item.id) for item in decisions] + [str(item.id) for item in stale] + [str(item.id) for item in evidence],
    }


def _guard_conversation(db: Session, project: Project, conversation_id: uuid.UUID) -> AssistantConversation:
    conversation = db.scalar(select(AssistantConversation).where(
        AssistantConversation.id == conversation_id, AssistantConversation.project_id == project.id
    ))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/context", response_model=AssistantContextRead)
def assistant_context(project: Project = Depends(project_access(READ_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = _context(db, project, user)
    data["permitted_actions"] = ["read_stage", "read_guidance"]
    data["suggested_questions"] = [
        "What should I do next?", "Explain this stage.", "What is missing before I can proceed?",
        "Help me refine my research question.", "Why is this stage blocked?",
    ]
    return data


@router.get("/conversations", response_model=list[AssistantConversationRead])
def list_conversations(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(AssistantConversation).where(AssistantConversation.project_id == project.id).order_by(AssistantConversation.updated_at.desc())).all()


@router.post("/conversations", response_model=AssistantConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: AssistantConversationCreate, project: Project = Depends(project_access(READ_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    conversation = AssistantConversation(project_id=project.id, created_by=user.id, title=payload.title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationDetail)
def get_conversation(conversation_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    conversation = _guard_conversation(db, project, conversation_id)
    messages = db.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id).order_by(AssistantMessage.created_at.asc())).all()
    return AssistantConversationDetail(
        id=conversation.id, project_id=conversation.project_id, created_by=conversation.created_by,
        title=conversation.title, created_at=conversation.created_at, updated_at=conversation.updated_at,
        messages=[AssistantMessageRead.model_validate(m) for m in messages],
    )


@router.post("/conversations/{conversation_id}/messages", response_model=AssistantMessageRead)
def send_message(payload: AssistantMessageCreate, conversation_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not get_settings().research_assistant_enabled:
        raise HTTPException(status_code=409, detail="Research Assistant is disabled by deployment settings")
    conversation = _guard_conversation(db, project, conversation_id)
    question = AssistantMessage(conversation_id=conversation.id, actor_id=user.id, role="user", content=payload.content, status="completed")
    db.add(question)
    db.flush()
    context = _context(db, project, user)
    selected_notes = _selected_note_context(db, project, user, payload.note_ids)
    context["context_ids"].extend(note["id"] for note in selected_notes)
    history = db.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id).order_by(AssistantMessage.created_at.desc()).limit(12)).all()
    prompt = load_prompt(*PROMPT)
    note_context = "\n".join(f"Note {note['id']}: {note['body']}" for note in selected_notes)
    user_text = prompt.render_user(context=_safe_context(context), question=payload.content, history="\n".join(f"{m.role}: {m.content}" for m in reversed(history)) + ("\nSelected notes:\n" + note_context if note_context else ""))
    try:
        llm = llm_for_project(get_settings(), project, purpose="research_assistant")
        result = llm.complete_json(prompt.system, user_text, ANSWER_SCHEMA, max_tokens=2200)
        answer = AssistantMessage(conversation_id=conversation.id, actor_id=user.id, role="assistant", content=result["answer"], status="completed", model_id=llm.model, prompt_version=prompt.ref, context_ids=context["context_ids"])
    except (AIOffError, LLMConfigurationError, LLMResponseError) as exc:
        answer = AssistantMessage(conversation_id=conversation.id, actor_id=user.id, role="assistant", content=str(exc), status="failed", context_ids=context["context_ids"])
        db.add(answer)
        db.commit()
        raise HTTPException(status_code=503, detail={"message": str(exc), "retryable": True, "question_id": str(question.id)}) from exc
    db.add(answer)
    audit.record(db, actor=audit.user_actor(user), action="assistant.message", project_id=project.id, payload={"status": "completed", "conversation_id": str(conversation.id)}, model_id=llm.model, prompt_version=prompt.ref)
    db.commit()
    db.refresh(answer)
    return answer


def _capture_message(db: Session, project: Project, payload: AssistantCaptureCreate) -> AssistantMessage:
    message = db.scalar(select(AssistantMessage).join(AssistantConversation).where(
        AssistantMessage.id == payload.message_id,
        AssistantMessage.conversation_id == payload.conversation_id,
        AssistantConversation.project_id == project.id,
    ))
    if message is None or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Assistant message not found")
    return message


@router.post("/ideas/refine", response_model=AssistantRefineRead)
def refine_idea(payload: AssistantRefineCreate, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not get_settings().research_assistant_enabled:
        raise HTTPException(status_code=409, detail="Research Assistant is disabled by deployment settings")
    message = _capture_message(db, project, AssistantCaptureCreate(
        content=payload.content, conversation_id=payload.conversation_id, message_id=payload.message_id,
        stage=payload.stage, text_origin="human",
    ))
    prompt = load_prompt(*REFINE_PROMPT)
    try:
        llm = llm_for_project(get_settings(), project, purpose="research_assistant_refine")
        result = llm.complete_json(prompt.system, prompt.render_user(stage=payload.stage, idea=payload.content), REFINE_SCHEMA, max_tokens=1800)
    except (AIOffError, LLMConfigurationError, LLMResponseError) as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc), "retryable": True, "message_id": str(message.id)}) from exc
    audit.record(db, actor=audit.user_actor(user), action="assistant.idea_refined", project_id=project.id, payload={"message_id": str(message.id)}, model_id=llm.model, prompt_version=prompt.ref)
    db.commit()
    return AssistantRefineRead(proposal=result["proposal"], model_id=llm.model, prompt_version=prompt.ref)

@router.post("/capture/idea", response_model=ContextItemRead, status_code=status.HTTP_201_CREATED)
def capture_idea(payload: AssistantCaptureCreate, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    message = _capture_message(db, project, payload)
    item = ResearchContextItem(
        project_id=project.id, kind=ContextKind.idea, content=payload.content, created_by=audit.user_actor(user),
        originating_conversation_id=payload.conversation_id, originating_message_id=message.id,
        captured_stage=payload.stage, text_origin=payload.text_origin,
    )
    db.add(item)
    audit.record(db, actor=audit.user_actor(user), action="assistant.idea_saved", project_id=project.id, payload={"item_id": str(item.id), "message_id": str(message.id)})
    db.commit()
    db.refresh(item)
    return item


@router.post("/capture/note", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def capture_note(payload: AssistantCaptureCreate, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.message_id is None:
        message = AssistantMessage(conversation_id=payload.conversation_id, actor_id=user.id, role="user", content=payload.content, status="capture_source")
        db.add(message)
        db.flush()
    else:
        message = _capture_message(db, project, payload)
    target_project = project
    if payload.destination_project_id is not None and payload.destination_project_id != project.id:
        target_project = db.get(Project, payload.destination_project_id)
        if target_project is None or project_role(db, target_project, user) is None:
            raise HTTPException(status_code=404, detail="Destination project not found")
    note = Note(
        owner_id=user.id, project_id=target_project.id, client_id=f"assistant-{uuid.uuid4()}",
        kind=NoteKind.typed, title=payload.title, body=payload.content, ai_locked=False, revision=1,
        captured_at=utcnow(), captured_stage=payload.stage,
        originating_conversation_id=payload.conversation_id, originating_message_id=message.id,
        text_origin=payload.text_origin,
    )
    db.add(note)
    db.flush()
    db.add(NoteRevision(note_id=note.id, revision=1, body=note.body, quoted_text=None, edited_by=audit.user_actor(user)))
    audit.record(db, actor=audit.user_actor(user), action="assistant.note_saved", project_id=project.id, payload={"note_id": str(note.id), "message_id": str(message.id)})
    db.commit()
    db.refresh(note)
    return note


def _safe_context(context: dict) -> str:
    return "\n".join([
        f"Current stage: {context['stage']}", f"Pending gate: {context['pending_gate'] or 'none'}",
        f"Role: {context['role']}", f"Blockers: {context['blockers']}", f"Next: {context['next']}",
        f"Gate status and approver roles: {context['gates']}",
        f"Approved decisions: {context['approved_decisions']}",
        f"Stale artifacts needing revisiting: {context['stale_artifacts']}",
        f"Accessible verified evidence records: {context['selected_evidence']}",
    ])
