"""Suppression markers, ported from the Go/JS F1 marker-block engine.

A marker suppresses a finding when `# <prefix> <reason>` appears in the
contiguous comment block whose bottom line sits directly above the flagged
node, with a non-empty reason. "Block" = a run of comment lines on consecutive
rows; a blank line or code breaks it. The marker may sit on any line of that
block (a long reason can spill onto adjacent comment lines). Prefix is
`no-report:` (swallowed-exception rules) or `test-skip:` (TBX008); each prefix
gets its own index so the two suppression channels stay independent.
"""

from __future__ import annotations

import tokenize

NO_REPORT = "no-report:"
TEST_SKIP = "test-skip:"


def _marker_reason_ok(comment: str, prefix: str) -> bool:
    """True iff `comment` (a `#...` token) carries `prefix` with a non-empty
    reason. Whitespace-only reason does not suppress (parity with the yaml
    `[ \\t]*\\S` guard and the Go `TrimSpace(...) == ""` check)."""
    text = comment.lstrip("#").strip()
    if not text.startswith(prefix):
        return False
    return text[len(prefix):].strip() != ""


def _is_standalone(tok: tokenize.TokenInfo) -> bool:
    """True iff only whitespace precedes the comment on its own line: a
    comment trailing code must never join or start a standalone block."""
    return tok.line[: tok.start[1]].strip() == ""


class MarkerIndex:
    """Bottom lines of comment blocks that carry a valid marker for `prefix`."""

    def __init__(
        self,
        file_tokens: list[tokenize.TokenInfo] | None,
        prefix: str = NO_REPORT,
    ):
        self._prefix = prefix
        self._suppress_bottoms: set[int] = set()
        if file_tokens:
            self._build(file_tokens)

    def _build(self, tokens: list[tokenize.TokenInfo]) -> None:
        comments = sorted(
            (tok.start[0], tok.string)
            for tok in tokens
            if tok.type == tokenize.COMMENT and _is_standalone(tok)
        )
        block_rows: list[int] = []
        block_marked = False
        for row, text in comments:
            if block_rows and row != block_rows[-1] + 1:
                self._flush(block_rows, block_marked)
                block_rows, block_marked = [], False
            block_rows.append(row)
            block_marked = block_marked or _marker_reason_ok(text, self._prefix)
        self._flush(block_rows, block_marked)

    def _flush(self, rows: list[int], marked: bool) -> None:
        if rows and marked:
            self._suppress_bottoms.add(rows[-1])

    def suppresses(self, node_line: int) -> bool:
        """True iff a marker block ends directly above `node_line`."""
        return (node_line - 1) in self._suppress_bottoms
