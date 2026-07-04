const {
  hasMarkerAbove,
  enclosingFn,
  fnReturnsResultLike,
  makeHandledAnalysis,
} = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'every path out of a catch must throw, call a reporter, convert to a Result boundary (return { ok: false, cause: err } when the function returns Result/Attempt), or the try must carry a // no-report: marker. Boundary conversion is kin to the policy layer (specs/general/error-policies.md).' },
    messages: {
      swallow: 'catch has a path that swallows the error: every path must throw, call a reporter (tackbox/report import or .tackbox-reporters declaration), convert to a Result boundary (`return { ok: false, cause: err }` when the enclosing function returns Result/Attempt), or the try must carry a `// no-report: <reason>` marker',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        const tryStmt = node.parent
        if (tryStmt && hasMarkerAbove(context, tryStmt, 'no-report')) return
        const errName = node.param && node.param.type === 'Identifier' ? node.param.name : null
        const allowBoundary = fnReturnsResultLike(enclosingFn(node))
        if (makeHandledAnalysis({ context, errName, allowBoundary }).handled(body)) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
