"""Session-wide env hygiene: cache/store redirects + host-git isolation.

Every subprocess started by a test inherits `os.environ`, so pointing
TACKBOX_CACHE_HOME and XDG_DATA_HOME at session tmp dirs here guarantees no
test can write into the developer's real `~/.cache/tackbox` or
`~/.local/share/tackbox` (the engine store). Individual tests that want
tighter isolation override the env vars themselves (see test_cli_cache.py,
test_engines_store.py).

A git hook (pre-commit runs this suite) exports GIT_DIR/GIT_INDEX_FILE for
the host repo; inherited by nested git in tests, they redirect every git
call into the host repo instead of the test's tmp repo.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_HOST_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
)


def _redirect(env_var: str, prefix: str) -> None:
    if os.environ.get(env_var):
        return
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ[env_var] = tmp
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)


def pytest_configure(config):
    for var in _HOST_GIT_ENV:
        os.environ.pop(var, None)
    _redirect("TACKBOX_CACHE_HOME", "tackbox-test-cache-")
    _redirect("XDG_DATA_HOME", "tackbox-test-xdg-")
