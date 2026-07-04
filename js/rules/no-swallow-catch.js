const { blockHasThrow, blockHasReport, hasMarkerAbove } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch must throw, call a reporter, or carry // no-report: marker above the try' },
    messages: {
      swallow: 'catch block swallows the error: must throw, call a reporter (tackbox/report import or .tackbox-reporters declaration), or carry `// no-report: <reason>` above the try',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        if (blockHasThrow(body)) return
        const errName = node.param && node.param.type === 'Identifier' ? node.param.name : null
        if (blockHasReport(context, body, errName)) return
        const tryStmt = node.parent
        if (tryStmt && hasMarkerAbove(context, tryStmt, 'no-report')) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
