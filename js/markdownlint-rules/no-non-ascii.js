// Custom markdownlint rule: flag any character outside the printable
// ASCII range (codepoints > 0x7F). Keeps docs strictly ASCII so
// em-dashes, curly quotes, Cyrillic, box-drawing chars, emoji, etc.
// cannot leak into prose, tables, or code fences.
//
// One escape hatch: a language marker in an HTML comment within the first
// 5 lines widens the alphabet for that one file to a declared language's
// script (plus a little typographic punctuation). It never disables the
// rule - every other non-ASCII character (emoji, zero-width, other
// scripts) is still flagged, and a misplaced / duplicate / malformed
// marker is a finding that leaves the file strict-ASCII.
//
//   <!-- tackbox: lang=ru personal experimental repo -->
//
// The marker is read from micromark HTML-comment tokens, not params.lines:
// markdownlint masks HTML-comment interiors in `lines`, so the raw code is
// only visible in the parse tree.

// code -> extra codepoints allowed when a valid marker declares it. Add a
// language by adding one entry: its script range(s) plus the typographic
// punctuation its prose uses.
const LANG_SCRIPTS = {
  ru: {
    // Cyrillic (U+0400-U+04FF).
    ranges: [[0x0400, 0x04ff]],
    // Typographic punctuation common in Russian prose: em/en dash,
    // guillemets, ellipsis, curly single/double quotes (incl. low
    // opening quotes), NBSP.
    punct: [
      0x2014, 0x2013, 0x00ab, 0x00bb, 0x2026,
      0x2018, 0x2019, 0x201c, 0x201d, 0x201e, 0x201a, 0x00a0,
    ],
  },
}

const MARKER_MAX_LINE = 5
const MARKER_RE = /<!--\s*tackbox:\s*lang=([^\s>]*)[^>]*-->/g

function collectMarkers(token, found) {
  for (const m of token.text.matchAll(MARKER_RE)) {
    const before = token.text.slice(0, m.index)
    const lineOffset = (before.match(/\n/g) || []).length
    const lastNl = before.lastIndexOf('\n')
    found.push({
      lineNumber: token.startLine + lineOffset,
      code: m[1],
      col: lineOffset === 0 ? token.startColumn + m.index : m.index - lastNl,
      len: m[0].length,
    })
  }
}

// Every marker occurrence in the file's HTML comments: {lineNumber, code,
// col, len}. Walks the micromark tree; htmlFlow / htmlText carry the raw
// comment text (their children just re-slice it, so we do not descend).
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

// Validate the marker set; emit findings for a misplaced / duplicate /
// malformed / unknown marker. Return the allow-config for a single valid
// marker, or null (strict ASCII-only) for no marker or any invalid one.
function resolveMarkers(markers, onError) {
  if (markers.length === 0) return null
  const markerErr = (m, detail) =>
    onError({ lineNumber: m.lineNumber, detail, range: [m.col, m.len] })

  if (markers.length > 1) {
    for (const dup of markers.slice(1)) {
      markerErr(dup, 'duplicate tackbox lang marker (one marker per file)')
    }
    return null
  }
  const m = markers[0]
  if (m.lineNumber > MARKER_MAX_LINE) {
    markerErr(m, `tackbox lang marker must be within the first ${MARKER_MAX_LINE} lines`)
    return null
  }
  if (m.code === '') {
    markerErr(m, 'tackbox lang marker is missing a language code')
    return null
  }
  const cfg = LANG_SCRIPTS[m.code]
  if (!cfg) {
    markerErr(m, `tackbox lang marker: unsupported language code '${m.code}'`)
    return null
  }
  return cfg
}

function isWidened(code, allow) {
  if (!allow) return false
  for (const [lo, hi] of allow.ranges) {
    if (code >= lo && code <= hi) return true
  }
  return allow.punct.includes(code)
}

module.exports = {
  names: ['MD-ASCII', 'no-non-ascii'],
  description: 'Non-ASCII character',
  tags: ['ascii'],
  parser: 'micromark',
  function: function rule(params, onError) {
    const allow = resolveMarkers(findMarkers(params.parsers.micromark.tokens), onError)
    params.lines.forEach((line, idx) => {
      let col = 0
      for (const ch of line) {
        const code = ch.codePointAt(0)
        if (code > 0x7f && !isWidened(code, allow)) {
          onError({
            lineNumber: idx + 1,
            detail: 'Non-ASCII character U+' + code.toString(16).toUpperCase() + ' (' + ch + ')',
            range: [col + 1, ch.length],
          })
        }
        col += ch.length
      }
    })
  },
}
