const { hasMarkerAbove, makeHandledAnalysis } = require('./_shared')

// rejectionHandler returns the rejection-handler argument of a promise
// method: `.catch(onErr)` -> arg 0, `.then(onOk, onErr)` -> arg 1. A single-arg
// `.then(onOk)` propagates the rejection naturally, so there is nothing to
// check (null). Only `.catch` and `.then` are recognized.
function rejectionHandler(node) {
  const callee = node.callee
  if (!callee || callee.type !== 'MemberExpression') return null
  if (!callee.property || callee.property.type !== 'Identifier') return null
  if (callee.property.name === 'catch') return node.arguments[0] || null
  if (callee.property.name === 'then') return node.arguments.length >= 2 ? node.arguments[1] : null
  return null
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'every path out of a promise rejection handler (.catch(onErr) or the second arg of .then(onOk, onErr)) must throw or call a reporter, or carry a // no-report: marker. Result-boundary conversion is not accepted in promise handlers.' },
    messages: {
      swallow: 'promise rejection handler has a path that swallows the error: every path must throw or call a reporter (tackbox/report import or .tackbox-reporters declaration), or carry `// no-report: <reason>` above',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const handler = rejectionHandler(node)
        if (!handler) return
        if (handler.type !== 'ArrowFunctionExpression' && handler.type !== 'FunctionExpression') return
        if (hasMarkerAbove(context, node, 'no-report')) return
        const errName = handler.params[0] && handler.params[0].type === 'Identifier' ? handler.params[0].name : null
        if (makeHandledAnalysis({ context, errName, allowBoundary: false }).handled(handler.body)) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
