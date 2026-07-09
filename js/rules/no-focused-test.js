const ROOTS = new Set(['it', 'test', 'describe'])
const BARE = new Set(['fit', 'fdescribe', 'ftest'])

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'focused tests disable the rest of the suite; no escape hatch, remove them' },
    messages: {
      focused: 'focused test disables the rest of the suite: remove the `.only` / `f`-prefix so every test runs',
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
            if (!cur.computed && cur.property.type === 'Identifier' && cur.property.name === 'only') prop = true
            cur = cur.object
          }
          hit = prop && cur.type === 'Identifier' && ROOTS.has(cur.name)
        }
        if (!hit) return
        context.report({ node, messageId: 'focused' })
      },
    }
  },
}
