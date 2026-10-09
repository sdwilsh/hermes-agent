"""Runtime smoke test for the image under rootless Podman.

Rootless Podman maps the container's root to the invoking host user, so root
inside holds capabilities only within that user namespace. Build the real
image, copy it into the invoking user's Podman store, and verify that both
supported starts (root, and --user pinned to the hermes UID) boot through
stage2 without an aborted privilege drop.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator

import pytest

from tests.docker.conftest import wait_for_docker_logs


def _rootless_podman_available() -> bool:
    """Return True iff a podman CLI is on PATH and runs rootless."""
    if shutil.which("podman") is None:
        return False
    try:
        r = subprocess.run(
            ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


pytestmark = pytest.mark.skipif(
    not _rootless_podman_available(), reason="rootless Podman not available",
)


@pytest.fixture(scope="module")
def podman_image(built_image: str) -> Iterator[str]:
    """Copy the image under test from the Docker daemon into Podman."""
    r = subprocess.run(
        ["podman", "pull", "--quiet", f"docker-daemon:{built_image}"],
        capture_output=True, text=True, timeout=600,
    )
    assert r.returncode == 0, f"podman could not copy the image: {r.stderr[-2000:]}"
    image_id = r.stdout.strip()
    yield image_id
    subprocess.run(
        ["podman", "rmi", "--force", image_id],
        capture_output=True, timeout=120,
    )


@pytest.mark.parametrize(
    "user_args", [(), ("--user", "10000:10000")], ids=["root-start", "hermes-uid-start"],
)
def test_rootless_podman_boots(
    podman_image: str, container_name: str, user_args: tuple[str, ...],
) -> None:
    """Both starts must reach the supervised services with every hermes step run."""
    try:
        r = subprocess.run(
            ["podman", "run", "-d", "--name", container_name, *user_args,
             podman_image, "sleep", "infinity"],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"podman could not start the image: {r.stderr[-2000:]}"
        logs = wait_for_docker_logs(
            container_name, "Setup complete", deadline_s=120, engine="podman",
        )
    finally:
        subprocess.run(
            ["podman", "rm", "--force", "--volumes", container_name],
            capture_output=True, timeout=60,
        )

    assert "unable to set supplementary group list" not in logs, (
        f"a privilege drop failed under rootless Podman: {logs[-1500:]}"
    )
    assert "[stage2] dependency environment:" in logs, (
        f"dependency refresh did not run under rootless Podman: {logs[-1500:]}"
    )
