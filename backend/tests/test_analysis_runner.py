import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import analysis_plan as rules
from app import analysis_runner as ar

DIGEST = "python@sha256:" + "a" * 64
CSV = "a,b\n1,2\n3,4\n"
SHA = hashlib.sha256(CSV.encode("utf-8")).hexdigest()
SPEC = {"tests": ["descriptives"], "variables": ["a"], "seed": 1}
PLAN_SHA = rules.plan_hash(SPEC, SHA)


def _completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def _host_dir(argv):
    mount = argv[argv.index("-v") + 1]
    return Path(mount[: mount.rindex(":/work")])


def test_image_must_be_pinned_by_digest():
    assert ar.check_image(DIGEST) == DIGEST
    for bad in ["python:3.12", "python", "python@sha256:xyz", "Python@sha256:" + "a" * 64, "", "x@sha256:" + "A" * 64]:
        with pytest.raises(ar.RunnerError):
            ar.check_image(bad)


def test_docker_command_carries_every_isolation_flag_and_no_env_passing():
    argv = ar.docker_argv(DIGEST, "C:/tmp/run", name="analysis-test")
    for pair in [("--network", "none"), ("--cap-drop", "ALL"), ("--memory", "512m"), ("-v", "C:/tmp/run:/work:ro")]:
        assert argv[argv.index(pair[0]) + 1] == pair[1]
    assert "--read-only" in argv and "no-new-privileges" in argv
    assert not {"-e", "--env", "--env-file"} & set(argv)  # nothing from the host reaches the container
    assert argv[-4:] == ["python", "-I", "analysis.py", "dataset.csv"]


def test_cli_environment_keeps_only_what_docker_needs():
    env = ar.cli_env({"PATH": "/bin", "SYSTEMROOT": "C:/Windows", "AWS_SECRET_ACCESS_KEY": "s", "OPENAI_API_KEY": "k"})
    assert env == {"PATH": "/bin", "SYSTEMROOT": "C:/Windows"}


def test_run_uses_a_temp_dir_that_is_removed_and_returns_verified_outputs():
    seen = {}

    def executor(argv, env, timeout):
        host = _host_dir(argv)
        seen["files"] = sorted(p.name for p in host.iterdir())
        seen["env"] = env
        seen["timeout"] = timeout
        seen["host"] = host
        out = {"dataset_sha256": SHA, "plan_hash": PLAN_SHA, "descriptives": {"a": {"n": 2}}}
        import json
        return _completed(stdout=json.dumps(out))

    result = ar.run_script(
        "print('x')", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, timeout=30,
        executor=executor, base_env={"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "leak"},
    )
    assert seen["files"] == ["analysis.py", "dataset.csv"]
    assert seen["env"] == {"PATH": "/bin"} and seen["timeout"] == 30
    assert not seen["host"].exists()  # removed afterwards
    assert result["outputs"]["descriptives"] == {"a": {"n": 2}}
    assert result["image"] == DIGEST and len(result["output_sha256"]) == 64


def test_mismatched_dataset_is_refused_before_anything_runs():
    def executor(argv, env, timeout):
        pytest.fail("the sandbox must not start on a changed dataset")
    with pytest.raises(ar.RunnerError, match="no longer matches"):
        ar.run_script("print(1)", CSV + "5,6\n", dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, executor=executor)


def test_non_zero_exit_reports_the_code_and_never_echoes_stderr():
    # stderr can carry cell values (e.g. a ValueError naming a participant's answer), so it must not reach the message.
    def executor(argv, env, timeout):
        return _completed(stderr="fail: non-numeric cell 'P007=99.5'\nmore detail", code=1)
    with pytest.raises(ar.RunnerError) as exc:
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, executor=executor)
    assert "exit code 1" in str(exc.value)
    assert "P007" not in str(exc.value) and "99.5" not in str(exc.value) and "non-numeric" not in str(exc.value)


@pytest.mark.parametrize("stdout,message", [
    ("not json", "valid JSON"),
    ("[1, 2]", "not an object"),
    ('{"dataset_sha256": "wrong", "plan_hash": "x"}', "expected dataset and plan hashes"),
    ("BIG", "too large"),
])
def test_bad_output_is_refused(stdout, message):
    if stdout == "BIG":
        stdout = '{"pad": "' + "x" * 1_000_001 + '"}'

    def executor(argv, env, timeout):
        return _completed(stdout=stdout)
    with pytest.raises(ar.RunnerError, match=message):
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, executor=executor)


def test_missing_docker_and_timeouts_are_named(monkeypatch):
    def no_docker(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(ar.subprocess, "run", no_docker)
    with pytest.raises(ar.RunnerError, match="not installed"):
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=5)
    monkeypatch.setattr(ar.subprocess, "run", slow)
    with pytest.raises(ar.RunnerError, match="exceeded 5 seconds"):
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, timeout=5)


def test_script_sha256_matches_the_bytes_the_container_runs():
    """Review defect (7), M5.3.3: on Windows the script was written in text mode, so the file held CRLF while the
    recorded hash covered LF. The hash must cover exactly the bytes that run."""
    script = "import json\nprint(json.dumps({'dataset_sha256': '%s', 'plan_hash': '%s'}))\n" % (SHA, PLAN_SHA)
    seen = {}

    def executor(argv, env, timeout):
        host = _host_dir(argv)
        seen["bytes"] = (host / "analysis.py").read_bytes()
        return _completed(stdout=__import__("json").dumps({"dataset_sha256": SHA, "plan_hash": PLAN_SHA}))
    result = ar.run_script(script, CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, executor=executor)
    assert b"\r" not in seen["bytes"]
    assert hashlib.sha256(seen["bytes"]).hexdigest() == result["script_sha256"]


def test_a_timed_out_container_is_named_and_force_removed(monkeypatch):
    """Review defect (6), M5.3.3: a timeout killed only the Docker CLI, so a `while True` script kept its container
    running. The run is named, and the timeout path removes it with `docker rm -f`."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=3)
        return _completed()
    monkeypatch.setattr(ar.subprocess, "run", fake_run)
    with pytest.raises(ar.RunnerError, match="exceeded 3 seconds"):
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, timeout=3)
    run_argv = calls[0]
    name = run_argv[run_argv.index("--name") + 1]
    assert name.startswith("analysis-")
    assert calls[-1] == ["docker", "rm", "-f", name]


def test_docker_command_names_the_container_and_limits_cpu():
    argv = ar.docker_argv(DIGEST, "C:/tmp/run", name="analysis-abc")
    assert argv[argv.index("--name") + 1] == "analysis-abc"
    assert argv[argv.index("--cpus") + 1] == "1"


def test_timeout_bounds_are_checked():
    with pytest.raises(ar.RunnerError):
        ar.run_script("x", CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST, timeout=0)


def test_generated_script_works_under_the_runner_contract(tmp_path):
    """Runs the real generated script with local Python in place of the container. This tests the script and the
    runner's I/O contract only; it does not test container isolation, which needs Docker and is not verified here."""
    script = rules.generate_script(SPEC, "survey", SHA)

    def local_python(argv, env, timeout):
        host = _host_dir(argv)
        return subprocess.run([sys.executable, "-I", "analysis.py", "dataset.csv"], cwd=host, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, env=ar.cli_env(os.environ))

    result = ar.run_script(script, CSV, dataset_sha256=SHA, plan_sha256=PLAN_SHA, image=DIGEST,
                           executor=local_python)
    assert result["outputs"]["plan_hash"] == PLAN_SHA
    assert result["outputs"]["descriptives"]["a"]["n"] == 2
