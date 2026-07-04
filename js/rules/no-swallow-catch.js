const {
  blockHasThrow,
  blockHasReport,
  hasMarkerAbove,
  enclosingFn,
  fnReturnsResultLike,
  returnsResultBoundary,
} = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch must throw, call a reporter, convert to a Result boundary (return { ok: false, cause: err } when the function returns Result/Attempt), or carry // no-report: marker above the try. Boundary conversion is kin to the policy layer (specs/general/error-policies.md).' },
    messages: {
      swallow: 'catch block swallows the error: must throw, call a reporter (tackbox/report import or .tackbox-reporters declaration), convert to a Result boundary (`return { ok: false, cause: err }` when the enclosing function returns Result/Attempt), or carry `// no-report: <reason>` above the try',
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
        // Third exit: typed Result-boundary conversion (kin to error-policies.md).
        if (errName && fnReturnsResultLike(enclosingFn(node)) && returnsResultBoundary(body, errName)) return
        const tryStmt = node.parent
        if (tryStmt && hasMarkerAbove(context, tryStmt, 'no-report')) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
