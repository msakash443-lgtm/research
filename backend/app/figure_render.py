"""Graphviz rendering for figures (plan M8.3).

`to_dot` turns a normalized spec into DOT source; every label is escaped, so no text from a document or a model
can change the graph's structure. `render_svg` runs `dot -Tsvg` once, with no shell, DOT on stdin, and a time
limit. `check_svg` rejects output that carries script, event-handler attributes or links outside the document.

Failure is always an exception: a missing `dot` raises `RendererUnavailable`; a crash, a timeout or unsafe output
raises `RenderFailed`. Nothing here draws a placeholder.
"""

from __future__ import annotations

import re
import subprocess

from app import figure_spec


class RenderError(Exception):
    """A figure could not be rendered to SVG."""


class RendererUnavailable(RenderError):
    """The Graphviz `dot` executable is missing or cannot be started."""


class RenderFailed(RenderError):
    """`dot` ran but failed, timed out, or produced SVG that is not safe to serve."""


_NODE_SHAPE = {
    figure_spec.METHODOLOGY_FLOWCHART: {"start": "oval", "end": "oval", "process": "box", "decision": "diamond", "data": "parallelogram"},
    figure_spec.CONCEPTUAL_FRAMEWORK: {},
}
_EDGE_STYLE = {"mediation": "dashed", "moderation": "dotted"}
_SCRIPT = re.compile(rb"<script", re.IGNORECASE)
_EVENT_ATTRIBUTE = re.compile(rb"<[^>]*\son[a-z]+\s*=[^>]*>", re.IGNORECASE)
_HREF = re.compile(rb"""href\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _escape(text: str) -> str:
    """Escape for a double-quoted DOT string: backslash and quote. Newlines never reach here (normalize rejects
    control characters)."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def to_dot(kind: str, spec: dict) -> str:
    """DOT source for a spec. Nodes are named `n0`, `n1`, ... in spec order, so no author id reaches DOT syntax.

    Raises `figure_spec.SpecError` if the spec is malformed, or an edge names a node that is not in the spec:
    a figure that cannot be drawn faithfully is not drawn."""
    clean = figure_spec.normalize(spec, kind)
    names = {node["id"]: f"n{index}" for index, node in enumerate(clean["nodes"])}
    lines = [
        "digraph figure {",
        '  graph [rankdir=TB, fontname="Helvetica"];',
        '  node [fontname="Helvetica", fontsize=11, shape=box];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    shapes = _NODE_SHAPE[kind]
    for node in clean["nodes"]:
        label = f"{_escape(node['label'])}\\n({_escape(node['role'])})"
        attributes = [f'label="{label}"']
        if node["role"] in shapes:
            attributes.append(f"shape={shapes[node['role']]}")
        if node["role"] == "control":
            attributes.append("style=dashed")
        lines.append(f"  {names[node['id']]} [{', '.join(attributes)}];")
    for edge in clean["edges"]:
        for endpoint in (edge["from"], edge["to"]):
            if endpoint not in names:
                raise figure_spec.SpecError(f"edge refers to node {endpoint!r}, which is not in the figure")
        attributes = []
        if edge["label"]:
            attributes.append(f'label="{_escape(edge["label"])}"')
        if edge["kind"] in _EDGE_STYLE:
            attributes.append(f"style={_EDGE_STYLE[edge['kind']]}")
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f"  {names[edge['from']]} -> {names[edge['to']]}{suffix};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_svg(dot: str, *, dot_path: str, timeout_seconds: float) -> bytes:
    """Run `dot -Tsvg` on `dot` and return the checked SVG bytes."""
    try:
        completed = subprocess.run(
            [dot_path, "-Tsvg"],
            input=dot.encode("utf-8"),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        raise RendererUnavailable(f"Graphviz dot could not be started from {dot_path!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderFailed(f"Graphviz did not finish within {timeout_seconds} seconds") from exc
    if completed.returncode != 0:
        raise RenderFailed(f"Graphviz exited with status {completed.returncode}")
    return check_svg(completed.stdout)


def check_svg(svg: bytes) -> bytes:
    """Return `svg` unchanged if it is safe to serve; raise `RenderFailed` otherwise."""
    if _SCRIPT.search(svg):
        raise RenderFailed("rendered SVG contains a script element")
    if _EVENT_ATTRIBUTE.search(svg):
        raise RenderFailed("rendered SVG contains an event-handler attribute")
    for match in _HREF.finditer(svg):
        if not match.group(1).startswith(b"#"):
            raise RenderFailed("rendered SVG links outside the document")
    return svg
