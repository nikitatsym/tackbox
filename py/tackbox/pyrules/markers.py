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

# D009: a marker's reason must be at least this many chars after trimming.
# Non-empty was too cheap (`ok` / `todo` passed).
_MIN_REASON = 10


def _marker_reason_ok(comment: str, prefix: str) -> bool:
    """True iff `comment` (a `#...` token) carries `prefix` with a reason of at
    least _MIN_REASON chars after trimming (D009; parity with the Go/JS/Java
    marker parsers)."""
    text = comment.lstrip("#").strip()
    if not text.startswith(prefix):
        return False
    return len(text[len(prefix):].strip()) >= _MIN_REASON


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
        self._suppress_bottoms: dict[int, int] = {}
        if file_tokens:
            self._build(file_tokens)

    def _build(self, tokens: list[tokenize.TokenInfo]) -> None:
        comments = sorted(
            (tok.start[0], tok.string)
            for tok in tokens
            if tok.type == tokenize.COMMENT and _is_standalone(tok)
        )
        block_rows: list[int] = []
        block_marked = 0
        for row, text in comments:
            if block_rows and row != block_rows[-1] + 1:
                self._flush(block_rows, block_marked)
                block_rows, block_marked = [], 0
            block_rows.append(row)
            if _marker_reason_ok(text, self._prefix):
                block_marked = row
        self._flush(block_rows, block_marked)

    def _flush(self, rows: list[int], marked: int) -> None:
        if rows and marked:
            self._suppress_bottoms[rows[-1]] = marked

    def above(self, node_line: int) -> int | None:
        """Return the marker's line when its block ends above `node_line`."""
        return self._suppress_bottoms.get(node_line - 1)
