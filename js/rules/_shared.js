const path = require('path')

// Canonical reporter names. A call counts as a reporter only when its
// callee resolves (scope analysis) to an import of `tackbox`/`tackbox/report`
// carrying one of these names (tier-1), or to a function declared in
// `.tackbox-reporters` (tier-2). A bare identifier that merely shares the
// name is not trusted - name-only matching is dead.
const REPORTER_NAMES = new Set([
  'reportError',
  'reportSynth',
  'reportSynthError',
  'reportApiError',
  'reportWarn',
  'reportLayerError',
])

// Reporters that require (msg, cause, tags, dedupKey) - 4 args.
const REPORTER_FULL = new Set([
  'reportError',
  'reportWarn',
  'reportApiError',
  'reportLayerError',
])

// Reporters that omit cause: (msg, tags, dedupKey) - 3 args.
const REPORTER_SYNTH = new Set(['reportSynth', 'reportSynthError'])

// Modules whose imports are trusted as reporter origins (tier-1).
const TACKBOX_MODULES = new Set(['tackbox', 'tackbox/report'])

// Stop-words for secret-named identifiers (case-insensitive substring).
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

// --- tier-1: import-origin resolution ------------------------------------

function resolveVar(context, idNode) {
  const sc = context.sourceCode || context.getSourceCode()
  let scope = sc.getScope(idNode)
  while (scope) {
    for (const ref of scope.references) {
      if (ref.identifier === idNode) return ref.resolved || null
    }
    scope = scope.upper
  }
  return null
}

// importInfo classifies the binding a variable came from:
// {source, kind: 'named'|'default'|'namespace', imported?}. Covers ESM
// imports and CJS `require`. Returns null for locals / non-tackbox origins.
function importInfo(variable) {
  if (!variable || !variable.defs || variable.defs.length === 0) return null
  const def = variable.defs[0]
  if (def.type === 'ImportBinding') {
    const source = def.parent && def.parent.source && def.parent.source.value
    const node = def.node
    if (node.type === 'ImportNamespaceSpecifier') return { source, kind: 'namespace' }
    if (node.type === 'ImportDefaultSpecifier') return { source, kind: 'default' }
    const imported = node.imported
      ? node.imported.name || node.imported.value
      : node.local.name
    return { source, kind: 'named', imported }
  }
  if (def.type === 'Variable' && def.node && def.node.type === 'VariableDeclarator') {
    return requireInfo(def.node, variable.name)
  }
  return null
}

function requireInfo(declarator, localName) {
  const init = declarator.init
  if (!init || init.type !== 'CallExpression') return null
  if (init.callee.type !== 'Identifier' || init.callee.name !== 'require') return null
  const arg = init.arguments[0]
  if (!arg || arg.type !== 'Literal' || typeof arg.value !== 'string') return null
  const source = arg.value
  if (declarator.id.type === 'Identifier') return { source, kind: 'namespace' }
  if (declarator.id.type === 'ObjectPattern') {
    for (const p of declarator.id.properties) {
      if (
        p.type === 'Property' &&
        p.value.type === 'Identifier' &&
        p.value.name === localName &&
        p.key
      ) {
        return { source, kind: 'named', imported: p.key.name || p.key.value }
      }
    }
  }
  return null
}

// tier1ReporterName returns the reporter name when `call`'s callee resolves
// to a REPORTER_NAMES import of a tackbox module, else null. Origin-gated:
// mirrors the Go side's package-gated capture table.
function tier1ReporterName(context, call) {
  const callee = call.callee
  if (!callee) return null
  if (callee.type === 'Identifier') {
    const info = importInfo(resolveVar(context, callee))
    if (!info || !TACKBOX_MODULES.has(info.source) || info.kind !== 'named') return null
    return REPORTER_NAMES.has(info.imported) ? info.imported : null
  }
  if (
    callee.type === 'MemberExpression' &&
    !callee.computed &&
    callee.object.type === 'Identifier' &&
    callee.property.type === 'Identifier'
  ) {
    const info = importInfo(resolveVar(context, callee.object))
    if (!info || !TACKBOX_MODULES.has(info.source)) return null
    if (info.kind !== 'namespace' && info.kind !== 'default') return null
    return REPORTER_NAMES.has(callee.property.name) ? callee.property.name : null
  }
  return null
}

function isTier1ReporterCall(context, call) {
  return tier1ReporterName(context, call) !== null
}

// --- tier-2: .tackbox-reporters declarations -----------------------------

function declaredReporters(context) {
  const s = context.settings && context.settings.tackbox && context.settings.tackbox.reporters
  return Array.isArray(s) ? s : []
}

function relFile(context) {
  const fn = context.filename || (context.getFilename && context.getFilename()) || ''
  const cwd = context.cwd || process.cwd()
  return path.isAbsolute(fn) ? path.relative(cwd, fn) : fn
}

function stripExt(p) {
  const dot = p.lastIndexOf('.')
  const slash = p.lastIndexOf('/')
  return dot > slash ? p.slice(0, dot) : p
}

function matchesDecl(decls, file, name) {
  for (const d of decls) {
    const hash = d.lastIndexOf('#')
    if (hash < 0) continue
    if (d.slice(hash + 1) !== name) continue
    const dfile = d.slice(0, hash)
    if (dfile === file || stripExt(dfile) === stripExt(file)) return true
  }
  return false
}

// resolveDeclTarget resolves an Identifier callee to the {file, name} of its
// definition: a local top-level def in this file, or a single-hop relative
// import. Barrel re-exports are not followed (plan: direct import or wrapper
// declaration only).
function resolveDeclTarget(context, idNode) {
  const variable = resolveVar(context, idNode)
  if (!variable || !variable.defs || variable.defs.length === 0) return null
  const info = importInfo(variable)
  if (info) {
    const source = info.source
    if (typeof source !== 'string' || !source.startsWith('.')) return null
    const importedName = info.kind === 'named' ? info.imported : idNode.name
    const resolved = path.normalize(path.join(path.dirname(relFile(context)), source))
    return { file: resolved, name: importedName }
  }
  const def = variable.defs[0]
  if (def.type === 'FunctionName' || def.type === 'Variable') {
    return { file: relFile(context), name: variable.name }
  }
  return null
}

// argFlows: the caught error identifier appears somewhere in the call's
// arguments (catch param, promise-catch handler param, or recover value).
function argFlows(call, errName) {
  if (errName == null) return false
  let found = false
  for (const arg of call.arguments) {
    walk(arg, n => {
      if (n.type === 'Identifier' && n.name === errName) found = true
    })
  }
  return found
}

function isDeclaredReporterCall(context, call, errName) {
  const decls = declaredReporters(context)
  if (decls.length === 0) return false
  const callee = call.callee
  if (!callee || callee.type !== 'Identifier') return false
  const target = resolveDeclTarget(context, callee)
  if (!target || !matchesDecl(decls, target.file, target.name)) return false
  return argFlows(call, errName)
}

// isInDeclaredReporterBody: `node` is lexically inside a function declared in
// `.tackbox-reporters` for this file - no-console-error does not apply there
// (the declared function is itself the reporter).
function isInDeclaredReporterBody(context, node) {
  const decls = declaredReporters(context)
  if (decls.length === 0) return false
  const file = relFile(context)
  let cur = node.parent
  while (cur) {
    let fname = null
    if (cur.type === 'FunctionDeclaration' && cur.id) {
      fname = cur.id.name
    } else if (
      (cur.type === 'FunctionExpression' || cur.type === 'ArrowFunctionExpression') &&
      cur.parent &&
      cur.parent.type === 'VariableDeclarator' &&
      cur.parent.id.type === 'Identifier'
    ) {
      fname = cur.parent.id.name
    }
    if (fname && matchesDecl(decls, file, fname)) return true
    cur = cur.parent
  }
  return false
}

// --- recognition + block scanning ----------------------------------------

function isReporterCall(context, call, errName) {
  return isTier1ReporterCall(context, call) || isDeclaredReporterCall(context, call, errName)
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

function blockHasReport(context, block, errName) {
  let found = false
  walk(block, n => {
    if (n.type === 'CallExpression' && isReporterCall(context, n, errName)) found = true
  })
  return found
}

// hasMarkerAbove returns true when the line directly above node carries
// `// <prefix>: <non-empty reason>`.
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
  TACKBOX_MODULES,
  SECRET_WORDS,
  DEDUP_KEY_RE,
  calleeName,
  tier1ReporterName,
  isTier1ReporterCall,
  isDeclaredReporterCall,
  isReporterCall,
  isInDeclaredReporterBody,
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
