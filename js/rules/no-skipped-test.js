const { hasMarkerAbove } = require('./_shared')

const ROOTS = new Set(['it', 'test', 'describe'])
const SKIP_PROPS = new Set(['skip', 'todo', 'skipIf'])
const BARE = new Set(['xit', 'xdescribe', 'xtest'])

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

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'skipped tests silently drop coverage; remove the skip or justify with a // test-skip: <reason> marker above the statement' },
    messages: {
      skipped: 'skipped test silently drops coverage: remove the skip/todo, or justify it with a `// test-skip: <reason>` marker directly above the statement',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const callee = node.callee
        let hit = false
        if (callee.type === 'Identifier') {
          hit = BARE.has(callee.name)
        } else if (callee.type === 'MemberExpression') {
          let cur = callee
          let prop = false
          while (cur && cur.type === 'MemberExpression') {
            if (!cur.computed && cur.property.type === 'Identifier' && SKIP_PROPS.has(cur.property.name)) prop = true
            cur = cur.object
          }
          hit = prop && cur.type === 'Identifier' && ROOTS.has(cur.name)
        }
        if (!hit) return
        if (hasMarkerAbove(context, outermostCall(node), 'test-skip')) return
        context.report({ node, messageId: 'skipped' })
      },
    }
  },
}
