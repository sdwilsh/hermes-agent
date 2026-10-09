"""Runtime smoke test for the supervised processes' XDG_RUNTIME_DIR.

Build the real image and verify: the XDG_RUNTIME_DIR a supervised process
receives is a directory the hermes UID owns with mode 0700 (Bot Screen keeps
its display-allocation lock there), for a root start and for a start pinned
to the hermes UID.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.docker.conftest import poll_container

# Locate the supervised `sleep infinity` CMD and stat the XDG_RUNTIME_DIR in
# its environment. `docker exec` does not see the s6 container environment,
# so only the supervised process shows what stage2 handed out.
PROBE = (
    "for p in /proc/[0-9]*; do "
    "[ \"$(tr '\\0' ' ' < $p/cmdline 2>/dev/null)\" = 'sleep infinity ' ] || continue; "
    "d=$(tr '\\0' '\\n' < $p/environ | sed -n 's/^XDG_RUNTIME_DIR=//p'); "
    "[ -n \"$d\" ] && stat -c '%u %a' \"$d\" && exit 0; "
    "done; exit 1"
)


@pytest.mark.parametrize(
    "user_args", [(), ("--user", "10000:10000")], ids=["root-start", "hermes-uid-start"],
)
def test_supervised_xdg_runtime_dir_is_private_to_hermes(
    built_image: str, container_name: str, user_args: tuple[str, ...],
) -> None:
    """The supervised CMD's XDG_RUNTIME_DIR must be hermes-owned and 0700."""
    r = subprocess.run(
        ["docker", "run", "-d", "--name", container_name, *user_args,
         built_image, "sleep", "infinity"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"container start failed: {r.stderr[-2000:]}"
    found, out = poll_container(container_name, PROBE, deadline_s=120)
    assert found, f"supervised `sleep infinity` never appeared: {out}"
    assert out.split() == ["10000", "700"], (
        f"XDG_RUNTIME_DIR is not private to hermes (uid mode): {out}"
    )
