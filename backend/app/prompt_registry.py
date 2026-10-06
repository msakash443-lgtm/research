"""Versioned prompts, stored as files in the repo (app/prompts/<name>.v<N>.toml).

Why files: the wording behind an AI answer must be recoverable and reviewable. Each AI
output records `prompt.ref` (for example `evidence_synthesis@1`) next to its model id.
Published versions are immutable (a test pins their checksums): change a prompt by adding
a new version file and moving the caller to it deliberately.

A prompt file holds `name`, `version`, `placeholders`, `system` and a `user` template that
uses `${placeholder}` fields. Loading is strict: a mismatch between the file name, its
declared name/version, or its placeholders and the template raises `PromptError` at once,
so a bad prompt fails loudly instead of producing a subtly different request.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# Fields a prompt's optional `source_block` template may use (see Prompt.render_source).
SOURCE_BLOCK_FIELDS = frozenset({"index", "nonce", "title", "status", "details", "excerpt"})


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    system: str
    user_template: str
    placeholders: tuple[str, ...]
    # Optional template for one fenced source block (M1.2). None means the legacy unfenced layout (v1).
    source_block: str | None = None

    @property
    def ref(self) -> str:
        """The identifier stored with every AI output, e.g. `evidence_synthesis@1`."""
        return f"{self.name}@{self.version}"

    def render_source(self, **values: str) -> str:
        """Fill the per-source block template. Only prompts that define `source_block` can do this."""
        if self.source_block is None:
            raise PromptError(f"Prompt {self.ref} has no source_block template")
        if set(values) != SOURCE_BLOCK_FIELDS:
            raise PromptError(f"Prompt {self.ref} source blocks need exactly {sorted(SOURCE_BLOCK_FIELDS)}, got {sorted(values)}")
        return Template(self.source_block).substitute(values)

    def render_user(self, **values: str) -> str:
        """Fill the user template. Every placeholder must be given, and nothing else."""
        if set(values) != set(self.placeholders):
            raise PromptError(
                f"Prompt {self.ref} needs exactly {sorted(self.placeholders)}, got {sorted(values)}"
            )
        return Template(self.user_template).substitute(values)


@lru_cache(maxsize=None)
def _load(directory: str, name: str, version: int) -> Prompt:
    path = Path(directory) / f"{name}.v{version}.toml"
    if not path.is_file():
        raise PromptError(f"Prompt {name}@{version} does not exist ({path.name} not found)")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PromptError(f"Prompt file {path.name} could not be read: {exc}") from exc

    if data.get("name") != name or data.get("version") != version:
        raise PromptError(f"Prompt file {path.name} declares {data.get('name')}@{data.get('version')}, not {name}@{version}")
    for key in ("system", "user", "placeholders"):
        if key not in data:
            raise PromptError(f"Prompt file {path.name} is missing '{key}'")
    placeholders = tuple(data["placeholders"])
    if not all(isinstance(p, str) for p in placeholders) or not data["system"].strip() or not data["user"].strip():
        raise PromptError(f"Prompt file {path.name} has empty text or non-string placeholders")
    used = set(Template(data["user"]).get_identifiers())
    if used != set(placeholders):
        raise PromptError(
            f"Prompt {name}@{version}: template uses {sorted(used)} but declares {sorted(placeholders)}"
        )
    if Template(data["system"]).get_identifiers():
        raise PromptError(f"Prompt {name}@{version}: the system text must not contain placeholders")
    source_block = data.get("source_block")
    if source_block is not None:
        if not isinstance(source_block, str) or set(Template(source_block).get_identifiers()) != SOURCE_BLOCK_FIELDS:
            raise PromptError(f"Prompt {name}@{version}: source_block must use exactly {sorted(SOURCE_BLOCK_FIELDS)}")
    return Prompt(name, version, data["system"], data["user"], placeholders, source_block)


def load_prompt(name: str, version: int) -> Prompt:
    """Load one exact version. Callers pin the version; there is no 'latest'."""
    return _load(str(PROMPT_DIR), name, version)


def available_versions(name: str) -> list[int]:
    return sorted(int(p.stem.rsplit(".v", 1)[1]) for p in PROMPT_DIR.glob(f"{name}.v*.toml"))
