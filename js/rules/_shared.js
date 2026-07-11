const path = require('path')
const fs = require('fs')

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

// Module extensions, compound first: a specifier that omits the extension must
// strip to the same base as the declaration. `.svelte.ts` / `.svelte.js` are
// Svelte rune modules that keep the double extension, so a specifier of `x`,
// `x.svelte`, or `x.svelte.ts` all reduce to `x` and match a `x.svelte.ts`
// declaration. Order matters: the compound forms are tried before the simple
// ones (`.svelte.ts` before `.ts` and before `.svelte`).
const MODULE_EXTS = [
  '.svelte.ts', '.svelte.js',
  '.ts', '.tsx', '.mts', '.cts',
  '.js', '.jsx', '.mjs', '.cjs',
  '.svelte',
]

function stripModuleExt(p) {
  const base = p.slice(p.lastIndexOf('/') + 1)
  for (const ext of MODULE_EXTS) {
    if (base.length > ext.length && base.endsWith(ext)) return p.slice(0, p.length - ext.length)
  }
  return p
}

function matchesDecl(decls, file, name) {
  for (const d of decls) {
    const hash = d.lastIndexOf('#')
    if (hash < 0) continue
    if (d.slice(hash + 1) !== name) continue
    const dfile = d.slice(0, hash)
    if (dfile === file || stripModuleExt(dfile) === stripModuleExt(file)) return true
  }
  return false
}

function absFile(context) {
  const fn = context.filename || (context.getFilename && context.getFilename()) || ''
  if (path.isAbsolute(fn)) return fn
  return path.resolve(context.cwd || process.cwd(), fn)
}

const SVELTE_CONFIG_NAMES = ['svelte.config.js', 'svelte.config.ts', 'svelte.config.mjs', 'svelte.config.cjs']

// resolveAlias maps a SvelteKit `$lib` specifier to a repo-relative path.
// Deterministic and CI-safe: `$lib` -> `<nearest ancestor of the importing file
// holding svelte.config.*>/src/lib`, the committed SvelteKit convention. It
// never reads `.svelte-kit/tsconfig.json` (generated, gitignored, absent on a
// fresh clone). Returns null for any other specifier or when no svelte.config
// is found - the caller then leaves the import unresolved.
function resolveAlias(context, source, absImporter) {
  if (source !== '$lib' && !source.startsWith('$lib/')) return null
  const rest = source === '$lib' ? '' : source.slice('$lib/'.length)
  let dir = path.dirname(absImporter)
  let root = null
  for (;;) {
    if (SVELTE_CONFIG_NAMES.some(n => fs.existsSync(path.join(dir, n)))) {
      root = dir
      break
    }
    const up = path.dirname(dir)
    if (up === dir) break
    dir = up
  }
  if (root === null) return null
  return path.relative(context.cwd || process.cwd(), path.join(root, 'src', 'lib', rest))
}

// resolveDeclTarget resolves an Identifier callee to the {file, name} of its
// definition: a local top-level def in this file, a single-hop relative import,
// or a `$lib` SvelteKit alias import. Barrel re-exports are not followed (plan:
// direct import or wrapper declaration only).
function resolveDeclTarget(context, idNode) {
  const variable = resolveVar(context, idNode)
  if (!variable || !variable.defs || variable.defs.length === 0) return null
  const info = importInfo(variable)
  if (info) {
    const source = info.source
    if (typeof source !== 'string') return null
    const importedName = info.kind === 'named' ? info.imported : idNode.name
    let resolved
    if (source.startsWith('.')) {
      resolved = path.normalize(path.join(path.dirname(relFile(context)), source))
    } else {
      resolved = resolveAlias(context, source, absFile(context))
      if (resolved === null) return null
    }
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

// hasMarkerAbove returns true when the comment block directly above node
// carries `// <prefix>: <non-empty reason>` on any of its lines - not only the
// line immediately above, so a long reason can be followed by human context.
// A blank line breaks the block (adjacency required).
function hasMarkerAbove(context, node, prefix) {
  if (!node || !node.loc) return false
  const sourceCode = context.sourceCode || context.getSourceCode()
  const byEndLine = new Map()
  for (const c of sourceCode.getAllComments()) {
    if (c.type === 'Line') byEndLine.set(c.loc.end.line, c)
  }
  for (let line = node.loc.start.line - 1; byEndLine.has(line); line--) {
    const text = byEndLine.get(line).value.trim()
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

// --- F2b: path-sensitive no-swallow analysis -----------------------------
// One coherent path analysis for all three legal catch exits: throw and a
// Result-boundary return terminate a path; a recognized reporter call is a
// sticky event (statements after it on the path are fine). A path reaching the
// end of the handler without a terminator or event swallows. Ported from gmux
// makeHandledAnalysis; reporter recognition stays tackbox origin-gating.
// Result-boundary is kin to the policy layer (specs/general/error-policies.md):
// annotation-based (no type program) - only a syntactic Result / Attempt /
// Promise<Result|Attempt> return type earns the boundary credit.

function isResultLikeType(t) {
  if (!t || t.type !== 'TSTypeReference' || !t.typeName || t.typeName.type !== 'Identifier') return false
  const name = t.typeName.name
  if (name === 'Result' || name === 'Attempt') return true
  if (name === 'Promise') {
    const args = (t.typeArguments && t.typeArguments.params) || (t.typeParameters && t.typeParameters.params)
    return Array.isArray(args) && args.length >= 1 && isResultLikeType(args[0])
  }
  return false
}

function enclosingFn(node) {
  let cur = node && node.parent
  while (cur) {
    if (
      cur.type === 'FunctionDeclaration' ||
      cur.type === 'FunctionExpression' ||
      cur.type === 'ArrowFunctionExpression'
    ) return cur
    cur = cur.parent
  }
  return null
}

function fnReturnsResultLike(fn) {
  return !!fn && !!fn.returnType && isResultLikeType(fn.returnType.typeAnnotation)
}

function exprRefsIdent(node, name) {
  if (name == null) return false
  let found = false
  walk(node, n => {
    if (n.type === 'Identifier' && n.name === name) found = true
  })
  return found
}

// stringifyingNode: a construct that coerces its content to a string, so an
// err inside it is a stringified occurrence, not object flow. The JS analog of
// the Go astutil.stringifies set: a `.message`/`.stack` property access, a
// `String(...)` conversion, an `x.toString()` call, a template literal, or `+`
// concatenation.
function stringifyingNode(n) {
  if (
    n.type === 'MemberExpression' &&
    !n.computed &&
    n.property.type === 'Identifier' &&
    (n.property.name === 'message' || n.property.name === 'stack')
  ) return true
  if (n.type === 'CallExpression' && n.callee.type === 'Identifier' && n.callee.name === 'String') return true
  if (
    n.type === 'CallExpression' &&
    n.callee.type === 'MemberExpression' &&
    !n.callee.computed &&
    n.callee.property.type === 'Identifier' &&
    n.callee.property.name === 'toString'
  ) return true
  if (n.type === 'TemplateLiteral') return true
  if (n.type === 'BinaryExpression' && n.operator === '+') return true
  return false
}

// errObjectFlows reports whether errName reaches root as a live object: found as
// a bare identifier outside any stringifying construct. Subtrees that stringify
// their content are pruned - an err inside them is a stringified occurrence, not
// object flow. The JS analog of Go astutil.errObjectFlows (F5 object-flow: a
// composite literal, a constructor argument, or a bare rethrow propagates; the
// chain breaks only when every occurrence of err passes through a string).
// someNode: explicit-stack DFS over an ESTree subtree, returning true as soon
// as match(node) holds. prune(node) (optional) skips a node and its subtree.
// Unlike walk() this descends into nested function bodies (object-flow must not
// stop at a boundary), so it takes predicates rather than pruning structurally.
function someNode(root, match, prune) {
  const stack = [root]
  while (stack.length) {
    const n = stack.pop()
    if (!n || typeof n !== 'object') continue
    if (Array.isArray(n)) {
      for (const c of n) stack.push(c)
      continue
    }
    if (prune && prune(n)) continue
    if (match(n)) return true
    for (const key of Object.keys(n)) {
      if (key === 'parent' || key === 'loc' || key === 'range') continue
      const child = n[key]
      if (child && typeof child === 'object') stack.push(child)
    }
  }
  return false
}

function errObjectFlows(root, errName) {
  if (errName == null) return false
  return someNode(root, n => n.type === 'Identifier' && n.name === errName, stringifyingNode)
}

// objectCarriesErr: `{ ok: false, cause|message: <valueCarries(v, err)> }`.
// A bare { ok: false } drops the caught error and does not qualify. valueCarries
// decides whether a property value carries err (a plain ref, or object flow).
function objectCarriesErr(expr, errName, valueCarries) {
  if (!errName || !expr || expr.type !== 'ObjectExpression') return false
  const okProp = expr.properties.find(
    p => p.type === 'Property' && p.key && p.key.type === 'Identifier' && p.key.name === 'ok',
  )
  if (!okProp || !okProp.value || okProp.value.type !== 'Literal' || okProp.value.value !== false) return false
  return expr.properties.some(
    p =>
      p.type === 'Property' &&
      p.key &&
      p.key.type === 'Identifier' &&
      (p.key.name === 'cause' || p.key.name === 'message') &&
      valueCarries(p.value, errName),
  )
}

function isBoundaryValue(expr, errName) {
  return objectCarriesErr(expr, errName, exprRefsIdent)
}

function containsReturn(node) {
  let found = false
  walk(node, n => {
    if (n.type === 'ReturnStatement') found = true
  })
  return found
}

// isReporterExpr: a (possibly awaited / void-wrapped) recognized reporter call.
// Unwrapping preserves tackbox's existing recognition of `await reportError(e)`;
// F2b changes path-completeness, not which calls count as reporters.
function isReporterExpr(context, expr, errName) {
  let e = expr
  while (e && (e.type === 'AwaitExpression' || (e.type === 'UnaryExpression' && e.operator === 'void'))) {
    e = e.argument
  }
  return !!e && e.type === 'CallExpression' && isReporterCall(context, e, errName)
}

// isExecutorRejectCall: a call to the enclosing `new Promise((resolve,
// reject) => ...)` executor's second parameter carrying the err object - the
// promise's own rethrow channel. Resolution is structural (the scope binding
// must be that exact parameter); a free-standing function named `reject`
// earns nothing, and a stringified argument breaks the chain.
function isExecutorRejectCall(context, expr, errName) {
  let e = expr
  while (e && e.type === 'AwaitExpression') e = e.argument
  if (!e || e.type !== 'CallExpression' || e.callee.type !== 'Identifier') return false
  if (!e.arguments.length || !errObjectFlows(e.arguments, errName)) return false
  const sc = context.sourceCode || context.getSourceCode()
  let variable = null
  for (let s = sc.getScope(e.callee); s && !variable; s = s.upper) {
    variable = s.variables.find(v => v.name === e.callee.name) || null
  }
  if (!variable || variable.defs.length !== 1) return false
  const def = variable.defs[0]
  if (def.type !== 'Parameter') return false
  const fn = def.node
  if (!fn.params || fn.params[1] !== def.name) return false
  const parent = fn.parent
  return (
    !!parent &&
    parent.type === 'NewExpression' &&
    parent.callee.type === 'Identifier' &&
    parent.callee.name === 'Promise' &&
    parent.arguments[0] === fn
  )
}

// isBareErrReturn: the returned expression IS the caught error object (an
// await-unwrapped bare identifier). The settled value being the error itself
// is the recognized rejection-to-value idiom; any wrapper object is not the
// error and stays refused (the F2 boundary refusal in promise handlers).
function isBareErrReturn(expr, errName) {
  let e = expr
  while (e && e.type === 'AwaitExpression') e = e.argument
  return !!errName && !!e && e.type === 'Identifier' && e.name === errName
}

// makeHandledAnalysis: path-sensitive walk of a catch / rejection handler.
// Per-statement verdict: 'terminal' (no path falls past - throw or boundary
// return), 'bad' (some path exits unhandled), { reported } (falls through;
// reported true when every falling path passed the sticky event). Constructs
// not modeled (switch, loops, nested try) are opaque: a hidden return fails
// closed, reporters inside do not count. Ported from gmux. returnIdentity
// credits `return <errName>` as terminal (promise handlers only: the settled
// value is the error object itself).
function makeHandledAnalysis(opts) {
  const { context, errName, allowBoundary, returnIdentity } = opts
  function analyzeStmt(stmt, reported) {
    if (!stmt) return { reported }
    if (stmt.type === 'ExpressionStatement') {
      if (isExecutorRejectCall(context, stmt.expression, errName)) return 'terminal'
      return isReporterExpr(context, stmt.expression, errName) ? { reported: true } : { reported }
    }
    if (stmt.type === 'ThrowStatement') return 'terminal'
    if (stmt.type === 'ReturnStatement') {
      if (reported) return 'terminal'
      if (allowBoundary && isBoundaryValue(stmt.argument, errName)) return 'terminal'
      if (returnIdentity && isBareErrReturn(stmt.argument, errName)) return 'terminal'
      return 'bad'
    }
    if (stmt.type === 'BlockStatement') return analyzeList(stmt.body, reported)
    if (stmt.type === 'IfStatement') {
      const c = analyzeStmt(stmt.consequent, reported)
      if (c === 'bad') return 'bad'
      const a = stmt.alternate ? analyzeStmt(stmt.alternate, reported) : { reported }
      if (a === 'bad') return 'bad'
      if (c === 'terminal' && a === 'terminal') return 'terminal'
      return { reported: (c === 'terminal' || c.reported) && (a === 'terminal' || a.reported) }
    }
    return containsReturn(stmt) ? 'bad' : { reported }
  }
  function analyzeList(stmts, reported) {
    for (const stmt of stmts) {
      const r = analyzeStmt(stmt, reported)
      if (r === 'bad' || r === 'terminal') return r
      reported = r.reported
    }
    return { reported }
  }
  function handled(body) {
    if (!body) return false
    if (body.type !== 'BlockStatement') {
      if (isReporterExpr(context, body, errName)) return true
      if (isExecutorRejectCall(context, body, errName)) return true
      if (returnIdentity && isBareErrReturn(body, errName)) return true
      return !!allowBoundary && isBoundaryValue(body, errName)
    }
    const r = analyzeList(body.body, false)
    if (r === 'bad') return false
    return r === 'terminal' || r.reported
  }
  return { handled }
}

const TEST_ROOTS = new Set(['it', 'test', 'describe'])

// matchesTestModifier: does callee name a test-modifier form - a bare alias in
// bareSet (fit/xit/...) or a member chain (it.only / it.skip) whose leaf
// property satisfies isModifierProp and whose root is a test root.
function matchesTestModifier(callee, bareSet, isModifierProp) {
  if (callee.type === 'Identifier') return bareSet.has(callee.name)
  if (callee.type === 'MemberExpression') {
    let cur = callee
    let prop = false
    while (cur && cur.type === 'MemberExpression') {
      if (!cur.computed && cur.property.type === 'Identifier' && isModifierProp(cur.property.name)) prop = true
      cur = cur.object
    }
    return prop && cur.type === 'Identifier' && TEST_ROOTS.has(cur.name)
  }
  return false
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
  enclosingFn,
  fnReturnsResultLike,
  someNode,
  errObjectFlows,
  objectCarriesErr,
  matchesTestModifier,
  makeHandledAnalysis,
}
