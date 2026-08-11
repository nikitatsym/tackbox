"""The child-process seam: UTF-8 decoding and the lost-stream guard.

Regression cover for the Windows crash where `go list -json` output was decoded
with the ANSI code page: the reader thread died on a byte cp1252 has no mapping
for, `subprocess` returned a None stdout instead of raising, and the failure
resurfaced as a type error inside the JSON scan.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tackbox import proc

# U+201D encodes to e2 80 9d in UTF-8; 0x9d is unmapped in cp1252, the Windows
# default, and is the byte the reported crash died on.
_CURLY = "\u201d"
_REPLACEMENT = "\ufffd"
_EMIT = (
    "import sys;"
    "sys.stdout.buffer.write('\\u201d'.encode('utf-8'));"
    "sys.stderr.buffer.write('\\u201d'.encode('utf-8'))"
)


def test_the_payload_really_is_undecodable_in_cp1252():
    # Pins the fixture: without this the decode tests below would pass under any
    # encoding and would stop guarding anything.
    with pytest.raises(UnicodeDecodeError):
        _CURLY.encode("utf-8").decode("cp1252")


def test_run_decodes_child_streams_as_utf8():
    completed = proc.run([sys.executable, "-c", _EMIT], capture_output=True)
    assert completed.stdout == _CURLY
    assert completed.stderr == _CURLY


def test_run_bytes_leaves_child_output_undecoded():
    completed = proc.run_bytes([sys.executable, "-c", _EMIT], capture_output=True)
    assert completed.stdout == _CURLY.encode("utf-8")
    assert proc.decode(completed.stdout) == _CURLY


def test_undecodable_bytes_become_replacement_chars_not_an_exception():
    # A lone 0x9d is invalid UTF-8 too; errors="replace" keeps such a failure in
    # the caller's parser instead of killing the reader thread.
    emit = "import sys; sys.stdout.buffer.write(b'\\x9d')"
    completed = proc.run([sys.executable, "-c", emit], capture_output=True)
    assert completed.stdout == _REPLACEMENT


def test_lost_stream_is_loud_and_names_the_child(monkeypatch):
    def lost(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=None, stderr="")

    monkeypatch.setattr(proc.subprocess, "run", lost)
    with pytest.raises(proc.ChildStreamError) as ei:
        proc.run(["go", "list", "-json", "./..."], capture_output=True)
    assert "stdout" in str(ei.value)
    assert "go list -json ./..." in str(ei.value)


def test_an_uncaptured_stream_is_not_a_lost_stream():
    completed = proc.run([sys.executable, "-c", _EMIT], stdout=subprocess.DEVNULL)
    assert completed.stdout is None
