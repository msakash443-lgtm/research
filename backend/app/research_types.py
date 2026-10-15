"""Research types: which stages a project passes through, in what order (Q22; X.40 decisions 4 and 5).

A type is a file in `app/research_types/` (TOML) listing its stages in order. Each stage may carry a
label for its audience; a stage that has a gate must name that gate, so the file shows every gate that
applies. A type is a separate choice from the discipline profile (`discipline.py`).

A file is checked when it is loaded, and an invalid one is refused, never repaired:
- the chain starts at `idea` and ends with the submission branch (`submitted`, `revision_loop`, `accepted`),
  because projects start at `idea` and `stage_order` expresses that branch itself;
- each stage appears once;
- a kept stage keeps its own gate (`gates.GATE_STAGES`): a gate can be skipped only by skipping its stage;
- G2, G3, G4, G7 and G11 are in every type (decision 5).

`full_journey` is today's 19-stage chain, so a project on it behaves exactly as before.
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.gates import GATE_STAGES
from app.models import GateCode, ProjectStage

TYPE_DIR = Path(__file__).resolve().parent / "research_types"

MANDATORY_GATES: tuple[GateCode, ...] = (GateCode.G2, GateCode.G3, GateCode.G4, GateCode.G7, GateCode.G11)
_FIRST = ProjectStage.idea
_TAIL = (ProjectStage.submitted, ProjectStage.revision_loop, ProjectStage.accepted)
_GATE_OF_STAGE = {stage: code for code, stage in GATE_STAGES.items()}
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,59}$")


class ResearchTypeError(RuntimeError):
    pass


class StageStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ProjectStage
    label: str | None = Field(default=None, min_length=1, max_length=80)
    gate: GateCode | None = None


class ResearchType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    label: str
    description: str
    human_participants: bool
    stages: list[StageStep]

    @property
    def chain(self) -> list[ProjectStage]:
        return [step.stage for step in self.stages]

    @property
    def gates(self) -> list[GateCode]:
        """The type's gates, in the order its stages reach them."""
        return [step.gate for step in self.stages if step.gate is not None]


def _check(research_type: ResearchType, filename: str) -> None:
    chain = research_type.chain
    repeated = sorted({stage.value for stage in chain if chain.count(stage) > 1})
    if repeated:
        raise ResearchTypeError(f"Research type {filename} lists {repeated} more than once")
    if not chain or chain[0] != _FIRST:
        raise ResearchTypeError(f"Research type {filename} must start at 'idea'")
    if tuple(chain[-len(_TAIL):]) != _TAIL or any(stage in _TAIL for stage in chain[:-len(_TAIL)]):
        raise ResearchTypeError(f"Research type {filename} must end with {[stage.value for stage in _TAIL]}")
    for step in research_type.stages:
        own = _GATE_OF_STAGE.get(step.stage)
        if step.gate != own:
            if own is None:
                raise ResearchTypeError(f"Research type {filename} puts {step.gate.value} on '{step.stage.value}', which has no gate")
            raise ResearchTypeError(
                f"Research type {filename} detaches {own.value} from kept stage '{step.stage.value}'"
                " (a gate is skipped only by dropping its stage)"
            )
    missing = [code.value for code in MANDATORY_GATES if code not in research_type.gates]
    if missing:
        raise ResearchTypeError(f"Research type {filename} lacks mandatory gate(s) {missing}")


@lru_cache(maxsize=None)
def _load(directory: str, name: str) -> ResearchType:
    path = Path(directory) / f"{name}.toml"
    if not _NAME.match(name) or not path.is_file():
        raise ResearchTypeError(f"Research type {name!r} does not exist")
    try:
        research_type = ResearchType(**tomllib.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # TOML syntax, missing or unknown keys, an unknown stage or gate
        raise ResearchTypeError(f"Research type {path.name} is invalid: {exc}") from exc
    if research_type.name != name:
        raise ResearchTypeError(f"Research type {path.name} declares name {research_type.name!r}")
    if research_type.version < 1:
        raise ResearchTypeError(f"Research type {path.name} needs a version of 1 or more")
    _check(research_type, path.name)
    return research_type


def load_research_type(name: str) -> ResearchType:
    return _load(str(TYPE_DIR), name)


def available_research_types() -> list[str]:
    return sorted(path.stem for path in TYPE_DIR.glob("*.toml"))
