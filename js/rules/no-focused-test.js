const { matchesTestModifier } = require('./_shared')

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
        if (!matchesTestModifier(node.callee, BARE, n => n === 'only')) return
        context.report({ node, messageId: 'focused' })
      },
    }
  },
}
