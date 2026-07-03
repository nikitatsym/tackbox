const { blockHasThrow, blockHasReport, hasMarkerAbove } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'promise.catch(handler) must throw, call a reporter, or carry // no-sentry: marker' },
    messages: {
      swallow: 'promise .catch handler swallows the error: must throw, call a reporter (tackbox/report import or .tackbox-reporters declaration), or carry `// no-sentry: <reason>` above',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const callee = node.callee
        if (!callee || callee.type !== 'MemberExpression') return
        if (!callee.property || callee.property.type !== 'Identifier' || callee.property.name !== 'catch') return
        if (node.arguments.length === 0) return
        const handler = node.arguments[0]
        if (handler.type !== 'ArrowFunctionExpression' && handler.type !== 'FunctionExpression') return
        const errName = handler.params[0] && handler.params[0].type === 'Identifier' ? handler.params[0].name : null
        const body = handler.body
        if (!body) return
        if (body.type !== 'BlockStatement') {
          const synthetic = { type: 'BlockStatement', body: [{ type: 'ExpressionStatement', expression: body }] }
          if (blockHasReport(context, synthetic, errName)) return
          if (hasMarkerAbove(context, node, 'no-sentry')) return
          context.report({ node, messageId: 'swallow' })
          return
        }
        if (blockHasThrow(body)) return
        if (blockHasReport(context, body, errName)) return
        if (hasMarkerAbove(context, node, 'no-sentry')) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
