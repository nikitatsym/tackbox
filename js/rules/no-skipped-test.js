const { hasMarkerAbove, matchesTestModifier, isStaticString, staticStringValue, TEST_ROOTS } = require('./_shared')

const SKIP_PROPS = new Set(['skip', 'todo', 'skipIf', 'fixme'])
const BARE = new Set(['xit', 'xdescribe', 'xtest'])
// Playwright's conditional forms carry (cond, 'reason'); skipIf/todo do not
// take a reason argument in any framework, so they stay marker-only.
const COND_REASON_PROPS = new Set(['skip', 'fixme'])

// outermostCall climbs to the whole `it.skipIf(cond)('n', fn)` statement so the
// marker sits above it; the skip property lives on the inner call, so reporting
// there and anchoring the marker higher keeps chained forms to one finding.
function outermostCall(node) {
  let cur = node
  while (cur.parent && cur.parent.type === 'CallExpression' && cur.parent.callee === cur) {
    cur = cur.parent
  }
  return cur
}

function directProp(callee) {
  if (callee.type === 'MemberExpression' && !callee.computed && callee.property.type === 'Identifier') {
    return callee.property.name
  }
  return ''
}

function isFunctionExpr(n) {
  return !!n && (n.type === 'FunctionExpression' || n.type === 'ArrowFunctionExpression')
}

// hasInCallReason: playwright `test.skip(cond, 'reason')` / `test.fixme(cond,
// 'reason')` - at least two args with a non-empty string reason last. A
// syntactic function there is the declaration form `(title, fn)` and earns
// nothing; a non-literal reason expression is trusted (mirrors ERC008).
function hasInCallReason(node) {
  if (!COND_REASON_PROPS.has(directProp(node.callee))) return false
  if (node.arguments.length < 2) return false
  const last = node.arguments[node.arguments.length - 1]
  if (isFunctionExpr(last)) return false
  if (isStaticString(last)) return staticStringValue(last).trim().length > 0
  return true
}

// skipValueVerdict classifies a node:test options `skip`/`todo` value:
// 'pass' (non-empty string reason, or trusted non-literal), 'flag'
// (reasonless: true, empty/whitespace string), null (falsy literal - the
// test is not skipped at all).
function skipValueVerdict(v) {
  if (isStaticString(v)) return staticStringValue(v).trim().length > 0 ? 'pass' : 'flag'
  if (v.type === 'Literal') return v.value ? 'flag' : null
  return 'pass'
}

// optionsSkipVerdict scans the node:test options position (`test([name][,
// options][, fn])` - first or second argument) for a skip/todo property.
function optionsSkipVerdict(node) {
  for (const arg of node.arguments.slice(0, 2)) {
    if (!arg || arg.type !== 'ObjectExpression') continue
    for (const p of arg.properties) {
      if (p.type !== 'Property' || p.computed) continue
      const key = p.key.type === 'Identifier' ? p.key.name : p.key.type === 'Literal' ? String(p.key.value) : ''
      if (key !== 'skip' && key !== 'todo') continue
      return skipValueVerdict(p.value)
    }
  }
  return null
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'skipped tests silently drop coverage; unskip, state a framework-native reason in the call (node:test options skip/todo, playwright test.skip(cond, reason)), or justify with a // test-skip: <reason> marker above the statement' },
    messages: {
      skipped: 'skipped test silently drops coverage: unskip it or state a non-empty reason',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        if (matchesTestModifier(node.callee, BARE, n => SKIP_PROPS.has(n))) {
          if (hasInCallReason(node)) return
          if (hasMarkerAbove(context, outermostCall(node), 'test-skip')) return
          context.report({ node, messageId: 'skipped' })
          return
        }
        if (node.callee.type === 'Identifier' && TEST_ROOTS.has(node.callee.name)) {
          if (optionsSkipVerdict(node) !== 'flag') return
          if (hasMarkerAbove(context, outermostCall(node), 'test-skip')) return
          context.report({ node, messageId: 'skipped' })
        }
      },
    }
  },
}
