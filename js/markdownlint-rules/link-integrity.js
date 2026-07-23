// Custom markdownlint rule: every relative Markdown link / image target must
// exist and stay inside the repo, and a #fragment into a target .md must name a
// real anchor (D018). Cross-file only - MD051 (enabled alongside) already holds
// same-file fragments.
//
// Target existence is decided against a whole-tree link-target inventory the
// tackbox CLI builds from the raw git listing, NOT fs.exists: a link to a
// gitignored file is broken in a clean clone, so it is a finding. The inventory
// is a factory input, so the rule is constructed per run with the repo root and
// the parsed inventory baked in.
//
// Anchor semantics follow the MD051 / GitHub contract. markdownlint does not
// export its heading-fragment helper, so the GitHub slugger is ported here and
// pinned by fixtures. A nested markdownlint lint is not an option: its per-file
// token cache is module-level and reset per file, so parsing a target inside a
// rule would corrupt the linted file's own results.

const fs = require('fs')
const path = require('path')

// RFC3986 scheme: an external URL (http, mailto, tel, data, ftp, ...) is out of
// scope - the rule is fully offline, no network anywhere.
const SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/

// GitHub heading slugger, ported verbatim from markdownlint's md051
// convertHeadingToHTMLFragment minus the encodeURIComponent wrapper: anchors are
// compared decoded, so unicode headings (legal since the charset flip) match a
// literal or a percent-encoded link fragment alike.
function slug(text) {
  return text
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Mark}\p{Number}\p{Connector_Punctuation}\- ]/gu, '')
    .replace(/ /gu, '-')
}

// Reduce a raw heading line to its plain inline text before slugging: images
// drop entirely (GitHub excludes their alt), links keep their visible text, raw
// HTML / autolinks drop, word-boundary underscore emphasis is removed (intraword
// `_` stays literal per CommonMark), and backslash escapes become the char. The
// remaining markup punctuation (`*`, backticks, brackets) is stripped by slug's
// own punctuation filter, so code spans and asterisk emphasis need no pass here.
function headingInlineText(raw) {
  return raw
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/!\[[^\]]*\](\[[^\]]*\])?/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\[[^\]]*\]/g, '$1')
    .replace(/\[([^\]]*)\]/g, '$1')
    .replace(/<[^>]*>/g, '')
    .replace(/(^|[^\p{L}\p{N}])_{1,3}(?=\S)/gu, '$1')
    .replace(/(?<=\S)_{1,3}($|[^\p{L}\p{N}])/gu, '$1')
    .replace(/\\(.)/g, '$1')
}

const ID_RE = /<[^>]*?\bid\s*=\s*["']([^"']+)["']/gi
const NAME_RE = /<a\b[^>]*?\bname\s*=\s*["']([^"']+)["']/gi

function collectHtmlAnchors(line, anchors) {
  let m
  while ((m = ID_RE.exec(line)) !== null) anchors.add(m[1])
  while ((m = NAME_RE.exec(line)) !== null) anchors.add(m[1])
}

function addHeadingAnchor(rawText, anchors, counts) {
  const s = slug(headingInlineText(rawText))
  if (s === '') return
  const c = counts.get(s) || 0
  // A duplicate heading slug gets the -1 / -2 ... suffix GitHub assigns.
  if (c > 0) anchors.add(s + '-' + c)
  anchors.add(s)
  counts.set(s, c + 1)
}

// Anchor set of a target .md's text: heading slugs (with GitHub duplicate
// suffixes), HTML id= / <a name=> anchors, and #top (always valid). Text-based
// so no markdownlint reentrancy; generous extraction (extra anchors) only ever
// misses a broken link, never invents one.
function computeAnchors(text) {
  const anchors = new Set(['top'])
  const counts = new Map()
  const lines = text.split(/\r?\n/)
  let fence = null
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const open = line.match(/^ {0,3}(`{3,}|~{3,})/)
    if (fence === null && open) {
      fence = open[1][0]
      continue
    }
    if (fence !== null) {
      if (new RegExp('^ {0,3}' + fence + '{3,}[ \\t]*$').test(line)) fence = null
      continue
    }
    const atx = line.match(/^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/)
    if (atx) {
      addHeadingAnchor((atx[2] || '').replace(/[ \t]+#+[ \t]*$/, ''), anchors, counts)
      collectHtmlAnchors(line, anchors)
      continue
    }
    const next = i + 1 < lines.length ? lines[i + 1] : ''
    if (
      line.trim() !== '' &&
      !/^ {0,3}#/.test(line) &&
      /^ {0,3}(=+|-+)[ \t]*$/.test(next)
    ) {
      addHeadingAnchor(line.trim(), anchors, counts)
    }
    collectHtmlAnchors(line, anchors)
  }
  return anchors
}

// Percent-decode without throwing (unlike decodeURIComponent): each maximal run
// of %XX escapes is decoded as UTF-8 bytes, invalid bytes become U+FFFD, and a
// stray % stays literal. A malformed link must never crash the lint.
function percentDecode(s) {
  return s.replace(/(?:%[0-9A-Fa-f]{2})+/g, (seq) => {
    const bytes = new Uint8Array(seq.length / 3)
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = parseInt(seq.slice(i * 3 + 1, i * 3 + 3), 16)
    }
    return new TextDecoder('utf-8').decode(bytes)
  })
}

// Split a raw destination into { pathPart, fragment }, or null to skip it. The
// fragment is everything after the first '#'; the query before it is dropped.
// External schemes, absolute paths, empty and same-file (#...) destinations are
// skipped - the latter is MD051's job.
function splitDest(dest) {
  if (dest === '' || SCHEME_RE.test(dest) || dest.startsWith('/') || dest.startsWith('#')) {
    return null
  }
  const hash = dest.indexOf('#')
  let before = hash >= 0 ? dest.slice(0, hash) : dest
  const fragment = hash >= 0 ? dest.slice(hash + 1) : null
  const q = before.indexOf('?')
  if (q >= 0) before = before.slice(0, q)
  if (before === '') return null
  return { pathPart: percentDecode(before), fragment: fragment === null ? null : percentDecode(fragment) }
}

// The rule's own destination string(s), never a nested link/image's: prune the
// walk at nested link/image boundaries so `[![alt](img)](target)` reports both
// img and target, each against its own token.
function ownLinkData(linkToken) {
  const data = { dest: null, ref: null, label: null }
  const rec = (toks) => {
    for (const t of toks) {
      if (t !== linkToken && (t.type === 'link' || t.type === 'image')) continue
      if (t.type === 'resourceDestinationString' && data.dest === null) data.dest = t.text
      if (t.type === 'referenceString' && data.ref === null) data.ref = t.text
      if (t.type === 'labelText' && data.label === null) data.label = t.text
      if (t.children && t.children.length) rec(t.children)
    }
  }
  rec([linkToken])
  return data
}

function normLabel(s) {
  return s.trim().replace(/\s+/g, ' ').toLowerCase()
}

// Every link / image in the file as { dest, line, endLine, startColumn,
// endColumn }: inline destinations plus reference / collapsed / shortcut ones
// resolved through the file's link definitions.
function collectLinks(tokens) {
  const definitions = new Map()
  const links = []
  const walk = (toks) => {
    for (const t of toks) {
      if (t.type === 'definition') {
        let label = null
        let dest = null
        const rec = (xs) => {
          for (const x of xs) {
            if (x.type === 'definitionLabelString' && label === null) label = x.text
            if (x.type === 'definitionDestinationString' && dest === null) dest = x.text
            if (x.children && x.children.length) rec(x.children)
          }
        }
        rec(t.children || [])
        if (label !== null && dest !== null) definitions.set(normLabel(label), dest)
      }
      if (t.type === 'link' || t.type === 'image') {
        links.push({ token: t, data: ownLinkData(t) })
      }
      if (t.children && t.children.length) walk(t.children)
    }
  }
  walk(tokens)
  const out = []
  for (const { token, data } of links) {
    let dest = data.dest
    if (dest === null) {
      const key = data.ref && data.ref !== '' ? data.ref : data.label
      if (key === null) continue
      dest = definitions.get(normLabel(key))
      if (dest === undefined || dest === null) continue
    }
    out.push({
      dest,
      line: token.startLine,
      endLine: token.endLine,
      startColumn: token.startColumn,
      endColumn: token.endColumn,
    })
  }
  return out
}

// Build the rule bound to `repoRoot` (absolute) and the parsed inventory. F =
// linkable files, L = tracked symlinks (exist, not dereferenced), G = gitlink
// roots (targets under them are skipped). dirs holds every ancestor prefix of an
// F/L entry, so a directory link resolves in O(1).
function makeRule({ repoRoot, F, L, G }) {
  // realpath both the root and each linted file: a symlinked temp root (macOS
  // /tmp -> /private/tmp) would otherwise make path.relative diverge, since a
  // spawned node resolves process.cwd() through the symlink.
  const absRoot = fs.realpathSync(path.resolve(repoRoot))
  const dirs = new Set()
  for (const p of [...F, ...L]) {
    const parts = p.split('/')
    for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/'))
  }
  const anchorCache = new Map()
  const anchorsOf = (rel) => {
    if (!anchorCache.has(rel)) {
      anchorCache.set(rel, computeAnchors(fs.readFileSync(path.resolve(absRoot, rel), 'utf8')))
    }
    return anchorCache.get(rel)
  }
  const underGitlink = (rel) => G.some((g) => rel === g || rel.startsWith(g + '/'))

  return {
    names: ['MD-LINK', 'link-integrity'],
    description: 'Broken relative link target',
    tags: ['links'],
    parser: 'micromark',
    function: function rule(params, onError) {
      const abs = fs.realpathSync(path.resolve(process.cwd(), params.name))
      const relDir = path.relative(absRoot, abs).split(path.sep).join('/').replace(/[^/]*$/, '')
      for (const link of collectLinks(params.parsers.micromark.tokens)) {
        const split = splitDest(link.dest)
        if (split === null) continue
        const rel = path.posix.normalize(path.posix.join(relDir, split.pathPart)).replace(/\/$/, '')
        const report = (detail) => {
          const single = link.line === link.endLine
          onError({
            lineNumber: link.line,
            detail,
            range: single ? [link.startColumn, link.endColumn - link.startColumn] : undefined,
          })
        }
        if (rel === '..' || rel.startsWith('../')) {
          report('link target escapes the repository root: ' + split.pathPart)
          continue
        }
        if (underGitlink(rel)) continue
        if (F.has(rel)) {
          if (split.fragment && rel.endsWith('.md') && !anchorsOf(rel).has(split.fragment)) {
            report('link fragment not found in target: ' + rel + '#' + split.fragment)
          }
          continue
        }
        if (L.has(rel)) continue
        if (dirs.has(rel)) continue
        report('link target does not exist: ' + rel)
      }
    },
  }
}

module.exports = { makeRule, computeAnchors, slug, headingInlineText, percentDecode, splitDest }
