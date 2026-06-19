const { blockHasThrow, blockHasReport } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch block must not both throw and call reportError-family' },
    messages: {
      both: 'catch block both throws and calls reportError-family: pick one — upstream handler would re-capture',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || (body.type !== 'BlockStatement' && body.type !== 'BlockStmt')) return
        if (blockHasThrow(body) && blockHasReport(body)) {
          context.report({ node, messageId: 'both' })
        }
      },
    }
  },
}
