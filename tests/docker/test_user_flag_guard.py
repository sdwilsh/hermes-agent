"""Runtime smoke tests for Docker --user flag guard.

Build the real image and verify the actual runtime behavior:

  1. docker run --user <arbitrary-uid> is rejected with actionable guidance
  2. --user <hermes-uid> (10000) is allowed (supported non-root start)
  3. that start runs every stage2 step that a root start runs as hermes

Root start (the default) is covered by test_main_invocation.py.
"""
from __future__ import annotations

import json
import subprocess

from hermes_cli.config_migrations import SUPPORT_FLOOR_VERSION
from tests.docker.conftest import wait_for_docker_logs

# A seed the Nous re-bootstrap helper can parse and rule on. Which verdict it
# reaches does not matter here; the test asserts only that the helper ran.
SEED_AUTH_JSON = json.dumps(
    {"providers": {"nous": {"obtained_at": 1, "tokens": {}}}},
)


def test_arbitrary_user_uid_rejected(
    built_image: str,
) -> None:
    """docker run --user 1000 must be rejected with actionable guidance."""
    r = subprocess.run(
        ["docker", "run", "--rm", "--user", "1000:1000",
         built_image, "echo", "should_not_reach"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0, (
        f"container started with arbitrary --user UID unexpectedly: {r.stdout}"
    )
    assert "should_not_reach" not in r.stdout, (
        f"container ran despite --user rejection: {r.stdout}"
    )
    combined = r.stdout + r.stderr
    # Must mention the remediation env vars
    assert "HERMES_UID" in combined or "PUID" in combined, (
        f"rejection message missing remediation guidance: {combined[-500:]}"
    )




def test_user_pinned_to_hermes_uid_works(
    built_image: str,
) -> None:
    """docker run --user 10000:10000 (the hermes UID) must be allowed.

    This is the supported non-root start from #34648 / #34837.
    """
    r = subprocess.run(
        ["docker", "run", "--rm", "--user", "10000:10000",
         built_image, "sh", "-c", "echo OK"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, (
        f"--user 10000:10000 (hermes UID) was rejected: {r.stderr[-500:]}"
    )
    assert "OK" in r.stdout


def test_hermes_uid_start_runs_every_stage2_step(
    built_image: str, container_name: str,
) -> None:
    """A --user 10000:10000 start must run each stage2 step that runs as hermes.

    A root start drops to hermes with s6-setuidgid. A hermes-UID start holds
    no CAP_SETGID, so a direct s6-setuidgid aborts in setgroups(), and
    stage2's `|| echo` turns the abort into a warning and a clean boot. Each
    step's own output is checked: an absent warning would also hold if the
    step never ran.
    """
    volume = f"{container_name}-data"
    try:
        subprocess.run(
            ["docker", "volume", "create", volume],
            check=True, capture_output=True, timeout=10,
        )
        # A volume carried over from an older image: owned by the hermes UID,
        # holding the oldest config schema the image still migrates.
        r = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{volume}:/opt/data",
             "--entrypoint", "sh", built_image, "-c",
             f"echo '_config_version: {SUPPORT_FLOOR_VERSION}' > /opt/data/config.yaml"
             " && chown -R 10000:10000 /opt/data"],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"seeding the volume failed: {r.stderr[-2000:]}"
        r = subprocess.run(
            ["docker", "run", "-d", "--name", container_name,
             "--user", "10000:10000", "-v", f"{volume}:/opt/data",
             "-e", f"HERMES_AUTH_JSON_BOOTSTRAP={SEED_AUTH_JSON}",
             "-e", f"HERMES_AUTH_JSON_REBOOTSTRAP={SEED_AUTH_JSON}",
             built_image, "sleep", "infinity"],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"hermes-UID start failed: {r.stderr[-2000:]}"
        logs = wait_for_docker_logs(
            container_name, "Setup complete", deadline_s=120,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=30,
        )
        subprocess.run(
            ["docker", "volume", "rm", "-f", volume],
            capture_output=True, timeout=30,
        )

    assert "unable to set supplementary group list" not in logs, (
        f"a privilege drop ran without CAP_SETGID: {logs[-1500:]}"
    )
    assert f"Migrating config schema {SUPPORT_FLOOR_VERSION} ->" in logs, (
        f"config migration did not run: {logs[-1500:]}"
    )
    assert "[stage2] dependency environment:" in logs, (
        f"dependency refresh did not run: {logs[-1500:]}"
    )
    assert "[rebootstrap]" in logs, (
        f"Nous re-bootstrap did not run: {logs[-1500:]}"
    )
