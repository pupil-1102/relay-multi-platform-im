"""Run pytest with Windows-sandbox-friendly directory modes.

On this Windows host the file sandbox turns a directory created with POSIX
mode ``0700`` into an unreadable one (``PermissionError: [WinError 5]``),
which breaks pytest's ``tmp_path`` factory.  pytest itself creates its temp
directories with ``mkdir(mode=0o700)``, so every fixture fails before the
test body runs.

This wrapper forces such modes to ``0755`` before pytest starts and then
delegates to the real test runner.  It is a local verification helper only;
it does not change application behaviour.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\run_tests.py -q
    .\\.venv\\Scripts\\python.exe scripts\\run_tests.py backend/tests/test_relay.py -q
"""
from __future__ import annotations

import os
import sys


def _public_mode(mode: int | None) -> int:
    if mode is None:
        return 0o777
    # Any mode without group/other access is what the sandbox rejects.
    if mode & 0o077 == 0:
        return 0o755
    return mode


_real_mkdir = os.mkdir
_real_makedirs = os.makedirs


def _mkdir(path, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
    return _real_mkdir(path, _public_mode(mode), dir_fd=dir_fd)


def _makedirs(name, mode=0o777, exist_ok=False):  # type: ignore[no-untyped-def]
    return _real_makedirs(name, _public_mode(mode), exist_ok=exist_ok)


def main(argv: list[str]) -> int:
    os.mkdir = _mkdir
    os.makedirs = _makedirs
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    # Keep pytest's temp root inside the workspace: the system temp root can
    # contain stale 0700 directories from earlier sandboxed runs.
    tmp_root = os.path.join(repo_root, ".pytest-tmp")
    os.makedirs(tmp_root, exist_ok=True)
    os.environ["TMP"] = tmp_root
    os.environ["TEMP"] = tmp_root
    import pytest

    return int(pytest.main(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
