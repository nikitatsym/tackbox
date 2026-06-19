const { blockHasThrow, blockHasReport, hasMarkerAbove } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch must throw, call reportError-family, or carry // no-sentry: marker above the try' },
    messages: {
      swallow: 'catch block swallows the error: must throw, call reportError/reportSynth/reportWarn, or carry `// no-sentry: <reason>` above the try',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        if (blockHasThrow(body) || blockHasReport(body)) return
        const tryStmt = node.parent
        if (tryStmt && hasMarkerAbove(context, tryStmt, 'no-sentry')) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
