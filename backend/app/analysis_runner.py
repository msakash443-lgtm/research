"""Sandboxed run of a locked analysis script in a Docker container (M5.3.3, owner decision 2026-10-17).

Isolation, in the order the container enforces it:
- the image must be pinned by digest (`name@sha256:<64 hex>`); a tag is refused, so the same image runs every time;
- no network, read-only root filesystem, all Linux capabilities dropped, no privilege escalation, memory and process
  limits, and a hard timeout;
- the data and script are mounted read-only from a fresh temp directory that is deleted afterwards;
- no environment variables are passed into the container (`docker run` does not forward the host environment), and the
  Docker CLI itself is started with a minimal environment rather than the server's full one.

The script is run as `python -I`, so the container's own site-packages and the working directory are not imported.
Output is accepted only if it is a JSON object whose dataset and plan hashes match what the caller expects.

Nothing here reads participant rows itself: the dataset text is handed to the container, and only its JSON result comes
back. Rule-based code, so the Q4 amendment (2026-10-17) applies; no model is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}@sha256:[0-9a-f]{64}$")
DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 1_000_000
MEMORY_LIMIT = "512m"
CPU_LIMIT = "1"
PIDS_LIMIT = "128"
SCRIPT_NAME = "analysis.py"
DATA_NAME = "dataset.csv"
# Only what the Docker CLI needs to find itself and its configuration. Everything else is dropped.
CLI_ENV_KEYS = ("PATH", "SYSTEMROOT", "USERPROFILE", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")

Executor = Callable[[list[str], dict[str, str], int], "subprocess.CompletedProcess[str]"]


class RunnerError(ValueError):
    """A run that cannot be started or did not produce a valid result. The message is safe to show."""


def check_image(image: str) -> str:
    image = (image or "").strip()
    if not IMAGE_RE.match(image):
        raise RunnerError("ANALYSIS_SANDBOX_IMAGE must be pinned by digest (name@sha256:<64 hex characters>).")
    return image


def docker_argv(image: str, host_dir: str, *, name: str, docker: str = "docker") -> list[str]:
    """The exact `docker run` command. Tested as a list so each isolation flag is visible and checked.

    The container is named so a timed-out run can be removed by name (`docker rm -f`); killing the CLI alone leaves
    the container running.
    """
    return [
        docker, "run", "--rm",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", MEMORY_LIMIT,
        "--cpus", CPU_LIMIT,
        "--pids-limit", PIDS_LIMIT,
        "-v", f"{host_dir}:/work:ro",
        "-w", "/work",
        image,
        "python", "-I", SCRIPT_NAME, DATA_NAME,
    ]


def cli_env(base: Mapping[str, str]) -> dict[str, str]:
    return {key: base[key] for key in CLI_ENV_KEYS if base.get(key)}


def _default_executor(argv: list[str], env: dict[str, str], timeout: int) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RunnerError("Docker is not installed or not on PATH on this server.") from exc
    except subprocess.TimeoutExpired as exc:
        # Killing the CLI does not stop the container, so remove it by name. Best effort: a failure here is not
        # reported as a different error, but the timeout still is.
        name = argv[argv.index("--name") + 1]
        try:
            subprocess.run([argv[0], "rm", "-f", name], env=env, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        raise RunnerError(f"The analysis run exceeded {timeout} seconds and was stopped.") from exc


def run_script(
    script: str,
    csv_text: str,
    *,
    dataset_sha256: str,
    plan_sha256: str,
    image: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    executor: Executor | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one locked script over one dataset in the sandbox and return the verified result.

    Refuses before starting anything if the image is not digest-pinned or the CSV does not match its hash.
    """
    image = check_image(image)
    if not (1 <= timeout <= 3600):
        raise RunnerError("The sandbox timeout must be between 1 and 3600 seconds.")
    csv_bytes = csv_text.encode("utf-8")
    if hashlib.sha256(csv_bytes).hexdigest() != dataset_sha256:
        raise RunnerError("The dataset no longer matches the hash the plan was locked on; nothing was run.")
    workdir = tempfile.mkdtemp(prefix="analysis-run-")
    try:
        # Bytes, not text: text mode would write CRLF on Windows and the recorded hash would no longer match what ran.
        Path(workdir, SCRIPT_NAME).write_bytes(script.encode("utf-8"))
        Path(workdir, DATA_NAME).write_bytes(csv_bytes)
        env = cli_env(os.environ if base_env is None else base_env)
        argv = docker_argv(image, workdir, name=f"analysis-{uuid.uuid4().hex}")
        completed = (executor or _default_executor)(argv, env, timeout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if completed.returncode != 0:
        # stderr is deliberately not shown: a traceback can quote cell values from the participant dataset.
        raise RunnerError(f"The analysis script failed in the sandbox (exit code {completed.returncode}). "
                          "Its error output is not shown because it may contain participant data.")
    stdout = completed.stdout or ""
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise RunnerError("The analysis output is too large to store.")
    try:
        outputs = json.loads(stdout)
    except ValueError as exc:
        raise RunnerError("The analysis script did not print valid JSON.") from exc
    if not isinstance(outputs, dict):
        raise RunnerError("The analysis script printed JSON that is not an object.")
    if outputs.get("dataset_sha256") != dataset_sha256 or outputs.get("plan_hash") != plan_sha256:
        raise RunnerError("The analysis output does not carry the expected dataset and plan hashes.")
    return {
        "outputs": outputs,
        "output_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "image": image,
    }
