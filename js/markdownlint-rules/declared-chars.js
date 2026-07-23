// Custom markdownlint rule: a Markdown file's character repertoire is checked
// only when the file declares one. Semantics are declaration-driven, not a
// strict-ASCII default (D017):
//
//   * No marker -> the charset is not checked at all.
//   * A marker -> every codepoint must be in the always-allowed ASCII base
//     (U+0000-U+007F, the Markdown syntax alphabet) or in one of the declared
//     named sets. Everything else is a finding.
//
//   <!-- tackbox: chars=cyrillic,punct -->
//
// Sets are named by character repertoire, not by language (a set proves nothing
// about the prose's language). The marker lists them comma-joined (union);
// tokens are trimmed, so a space after a comma is allowed. An invalid marker -
// an unknown set, an empty token, a duplicate set, a duplicate marker, or a
// marker below the fifth line - is a finding on the marker itself, and the
// content charset is then not checked (a broken declaration does not pass
// silently; there is no default to fall back to).
//
// The marker is read from micromark HTML-comment tokens, not params.lines:
// markdownlint masks HTML-comment interiors in `lines`, so the raw code is
// only visible in the parse tree.

// Named character sets: extra codepoints a set adds beyond the ASCII base. Add
// a set by adding one entry (its script range(s) and/or individual points).
const CHAR_SETS = {
  // Declares the check with no extension; the ASCII base is always allowed.
  ascii: { ranges: [], points: [] },
  // Cyrillic block U+0400-U+04FF in full.
  cyrillic: { ranges: [[0x0400, 0x04ff]], points: [] },
  // Typographic punctuation: em/en dash, guillemets, ellipsis, curly
  // single/double quotes (incl. low opening quotes), NBSP.
  punct: {
    ranges: [],
    points: [
      0x2014, 0x2013, 0x00ab, 0x00bb, 0x2026,
      0x2018, 0x2019, 0x201c, 0x201d, 0x201e, 0x201a, 0x00a0,
    ],
  },
}

const MARKER_MAX_LINE = 5
const MARKER_RE = /<!--\s*tackbox:\s*chars=([^>]*?)\s*-->/g

function collectMarkers(token, found) {
  for (const m of token.text.matchAll(MARKER_RE)) {
    const before = token.text.slice(0, m.index)
    const lineOffset = (before.match(/\n/g) || []).length
    const lastNl = before.lastIndexOf('\n')
    found.push({
      lineNumber: token.startLine + lineOffset,
      list: m[1],
      col: lineOffset === 0 ? token.startColumn + m.index : m.index - lastNl,
      len: m[0].length,
    })
  }
}

// Every marker occurrence in the file's HTML comments: {lineNumber, list, col,
// len}. Walks the micromark tree; htmlFlow / htmlText carry the raw comment
// text (their children just re-slice it, so we do not descend).
function findMarkers(tokens) {
  const found = []
  const walk = (toks) => {
    for (const t of toks) {
      if (t.type === 'htmlFlow' || t.type === 'htmlText') {
        collectMarkers(t, found)
        continue
      }
      if (t.children && t.children.length) walk(t.children)
    }
  }
  walk(tokens)
  return found
}

// Validate the marker set; emit findings for a misplaced / duplicate / invalid
// marker. Return the allowed set list for a single valid marker (to check
// content against), or null (do not check content) for no marker or any invalid
// one.
function resolveMarkers(markers, onError) {
  if (markers.length === 0) return null
  const markerErr = (m, detail) =>
    onError({ lineNumber: m.lineNumber, detail, range: [m.col, m.len] })

  if (markers.length > 1) {
    for (const dup of markers.slice(1)) {
      markerErr(dup, 'duplicate tackbox chars marker (one marker per file)')
    }
    return null
  }
  const m = markers[0]
  if (m.lineNumber > MARKER_MAX_LINE) {
    markerErr(m, `tackbox chars marker must be within the first ${MARKER_MAX_LINE} lines`)
    return null
  }
  const tokens = m.list.split(',').map((t) => t.trim())
  if (tokens.some((t) => t === '')) {
    markerErr(m, 'tackbox chars marker: empty character-set list')
    return null
  }
  const seen = new Set()
  const allowed = []
  for (const t of tokens) {
    if (seen.has(t)) {
      markerErr(m, `tackbox chars marker: duplicate set '${t}'`)
      return null
    }
    seen.add(t)
    const set = CHAR_SETS[t]
    if (!set) {
      markerErr(m, `tackbox chars marker: unknown character set '${t}'`)
      return null
    }
    allowed.push(set)
  }
  return allowed
}

function isAllowed(code, allowed) {
  if (code <= 0x7f) return true
  for (const set of allowed) {
    for (const [lo, hi] of set.ranges) {
      if (code >= lo && code <= hi) return true
    }
    if (set.points.includes(code)) return true
  }
  return false
}

module.exports = {
  names: ['MD-CHARS', 'declared-chars'],
  description: 'Character outside declared repertoire',
  tags: ['charset'],
  parser: 'micromark',
  function: function rule(params, onError) {
    const allowed = resolveMarkers(findMarkers(params.parsers.micromark.tokens), onError)
    if (!allowed) return
    params.lines.forEach((line, idx) => {
      let col = 0
      for (const ch of line) {
        const code = ch.codePointAt(0)
        if (!isAllowed(code, allowed)) {
          onError({
            lineNumber: idx + 1,
            detail:
              'U+' + code.toString(16).toUpperCase() + ' (' + ch + ') is not in the declared character set',
            range: [col + 1, ch.length],
          })
        }
        col += ch.length
      }
    })
  },
}
