const { blockHasThrow, blockHasReport } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch block must not both throw and call a reporter' },
    messages: {
      both: 'catch block both throws and calls a reporter: pick one - upstream handler would re-capture',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        const errName = node.param && node.param.type === 'Identifier' ? node.param.name : null
        if (blockHasThrow(body) && blockHasReport(context, body, errName)) {
          context.report({ node, messageId: 'both' })
        }
      },
    }
  },
}
