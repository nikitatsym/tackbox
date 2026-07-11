const { hasMarkerAbove, enclosingFn, someNode } = require('./_shared')

// isAllSettledCall: syntactic `Promise.allSettled(...)`. Matched by shape, not
// resolution - Promise is a global and allSettled is unambiguous.
function isAllSettledCall(node) {
  const c = node.callee
  return (
    !!c &&
    c.type === 'MemberExpression' &&
    !c.computed &&
    c.object.type === 'Identifier' &&
    c.object.name === 'Promise' &&
    c.property.type === 'Identifier' &&
    c.property.name === 'allSettled'
  )
}

// refsReason: root's subtree contains a `.reason` access (dot or computed
// string). Descends into nested functions - `.reason` is usually read inside a
// .forEach / .filter callback over the settled results, so the scan must not
// stop at function boundaries the way _shared.walk does.
function refsReason(root) {
  return someNode(
    root,
    n =>
      n.type === 'MemberExpression' &&
      ((!n.computed && n.property.type === 'Identifier' && n.property.name === 'reason') ||
        (n.computed && n.property.type === 'Literal' && n.property.value === 'reason')),
  )
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'every Promise.allSettled call needs at least one `.reason` access in the enclosing function, else rejected outcomes are silently dropped - allSettled never rejects, so a discarded result is the quietest swallow. Escape with a // no-report: marker.' },
    messages: {
      swallow: 'read `.reason` on the rejected entries in the enclosing function',
    },
    schema: [],
  },
  create(context) {
    const sc = context.sourceCode || context.getSourceCode()
    return {
      CallExpression(node) {
        if (!isAllSettledCall(node)) return
        if (hasMarkerAbove(context, node, 'no-report')) return
        if (refsReason(enclosingFn(node) || sc.ast)) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
