// Names treated as capture-site invocations. Match by the final
// identifier of the callee so `reportError(...)` and
// `report.reportError(...)` both qualify.
const REPORTER_NAMES = new Set([
  'reportError',
  'reportSynth',
  'reportSynthError',
  'reportApiError',
  'reportWarn',
  'reportLayerError',
])

// Reporters that require (msg, cause, tags, dedupKey) — 4 args.
const REPORTER_FULL = new Set([
  'reportError',
  'reportWarn',
  'reportApiError',
  'reportLayerError',
])

// Reporters that omit cause: (msg, tags, dedupKey) — 3 args.
const REPORTER_SYNTH = new Set(['reportSynth', 'reportSynthError'])

// Stop-words for secret-named identifiers (case-insensitive
// substring match).
const SECRET_WORDS = ['token', 'password', 'key', 'secret', 'cookie']

const DEDUP_KEY_RE = /^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*(:[a-zA-Z0-9_.-]+)?$/

function calleeName(node) {
  if (!node) return ''
  if (node.type === 'Identifier') return node.name
  if (node.type === 'MemberExpression' && node.property) {
    if (node.property.type === 'Identifier') return node.property.name
  }
  return ''
}

function isReporterCall(call) {
  return call && call.type === 'CallExpression' && REPORTER_NAMES.has(calleeName(call.callee))
}

function isThrowStatement(stmt) {
  return stmt && stmt.type === 'ThrowStatement'
}

function isStaticString(node) {
  if (!node) return false
  if (node.type === 'Literal' && typeof node.value === 'string') return true
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return true
  return false
}

function staticStringValue(node) {
  if (node.type === 'Literal') return String(node.value)
  if (node.type === 'TemplateLiteral') return node.quasis.map(q => q.value.cooked).join('')
  return ''
}

function walk(node, fn) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const c of node) walk(c, fn)
    return
  }
  fn(node)
  for (const key of Object.keys(node)) {
    if (key === 'parent' || key === 'loc' || key === 'range') continue
    const child = node[key]
    if (!child || typeof child !== 'object') continue
    if (
      (node.type === 'FunctionExpression' ||
        node.type === 'ArrowFunctionExpression' ||
        node.type === 'FunctionDeclaration') &&
      key === 'body'
    ) {
      continue
    }
    walk(child, fn)
  }
}

function blockHasThrow(block) {
  let found = false
  walk(block, n => {
    if (isThrowStatement(n)) found = true
  })
  return found
}

function blockHasReport(block) {
  let found = false
  walk(block, n => {
    if (n.type === 'CallExpression' && isReporterCall(n)) found = true
  })
  return found
}

// hasMarkerAbove returns true when the line directly above node
// carries `// <prefix>: <non-empty reason>`.
function hasMarkerAbove(context, node, prefix) {
  if (!node || !node.loc) return false
  const sourceCode = context.sourceCode || context.getSourceCode()
  const targetLine = node.loc.start.line - 1
  for (const c of sourceCode.getAllComments()) {
    if (c.type !== 'Line') continue
    if (c.loc.end.line !== targetLine) continue
    const text = c.value.trim()
    if (!text.startsWith(prefix + ':')) continue
    const reason = text.slice(prefix.length + 1).trim()
    if (reason.length > 0) return true
  }
  return false
}

function matchesSecret(name) {
  if (!name) return null
  const lower = String(name).toLowerCase()
  for (const w of SECRET_WORDS) {
    if (lower.includes(w)) return w
  }
  return null
}

function exprIsSecretRef(expr) {
  if (!expr) return null
  if (expr.type === 'Identifier') return matchesSecret(expr.name)
  if (expr.type === 'MemberExpression' && expr.property && expr.property.type === 'Identifier') {
    return matchesSecret(expr.property.name)
  }
  return null
}

module.exports = {
  REPORTER_NAMES,
  REPORTER_FULL,
  REPORTER_SYNTH,
  SECRET_WORDS,
  DEDUP_KEY_RE,
  calleeName,
  isReporterCall,
  isThrowStatement,
  isStaticString,
  staticStringValue,
  walk,
  blockHasThrow,
  blockHasReport,
  hasMarkerAbove,
  matchesSecret,
  exprIsSecretRef,
}
