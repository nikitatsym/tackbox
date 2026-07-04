const { hasMarkerAbove, makeHandledAnalysis } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'every path out of a promise.catch(handler) must throw or call a reporter, or the .catch must carry a // no-report: marker. Result-boundary conversion is not accepted in promise handlers.' },
    messages: {
      swallow: 'promise .catch handler has a path that swallows the error: every path must throw or call a reporter (tackbox/report import or .tackbox-reporters declaration), or carry `// no-report: <reason>` above',
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
        if (hasMarkerAbove(context, node, 'no-report')) return
        const errName = handler.params[0] && handler.params[0].type === 'Identifier' ? handler.params[0].name : null
        if (makeHandledAnalysis({ context, errName, allowBoundary: false }).handled(handler.body)) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
