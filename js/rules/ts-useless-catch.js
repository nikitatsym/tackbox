module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch that only re-throws the caught error is a no-op wrapper' },
    messages: {
      useless: 'catch only re-throws the caught error: remove the try/catch and let it propagate',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const param = node.param
        if (!param || param.type !== 'Identifier') return
        const body = node.body && node.body.body
        if (!body || body.length !== 1) return
        const stmt = body[0]
        if (stmt.type !== 'ThrowStatement') return
        if (!stmt.argument || stmt.argument.type !== 'Identifier') return
        if (stmt.argument.name !== param.name) return
        context.report({ node, messageId: 'useless' })
      },
    }
  },
}
