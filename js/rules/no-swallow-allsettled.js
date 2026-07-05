const { hasMarkerAbove, enclosingFn } = require('./_shared')

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

// resultIsBound: the allSettled result flows somewhere (assigned, returned,
// passed as an argument, or continued with `.then`) rather than being a
// discarded statement expression. A bare `await Promise.allSettled(x)`
// fire-and-forget is not bound and is out of scope (plan: binding of the result).
function resultIsBound(node) {
  let cur = node
  if (cur.parent && cur.parent.type === 'AwaitExpression') cur = cur.parent
  const p = cur.parent
  return !!p && p.type !== 'ExpressionStatement'
}

// refsReason: root's subtree contains a `.reason` access (dot or computed
// string). Descends into nested functions - `.reason` is usually read inside a
// .forEach / .filter callback over the settled results, so the scan must not
// stop at function boundaries the way _shared.walk does.
function refsReason(root) {
  const stack = [root]
  while (stack.length) {
    const n = stack.pop()
    if (!n || typeof n !== 'object') continue
    if (Array.isArray(n)) {
      for (const c of n) stack.push(c)
      continue
    }
    if (
      n.type === 'MemberExpression' &&
      ((!n.computed && n.property.type === 'Identifier' && n.property.name === 'reason') ||
        (n.computed && n.property.type === 'Literal' && n.property.value === 'reason'))
    ) {
      return true
    }
    for (const key of Object.keys(n)) {
      if (key === 'parent' || key === 'loc' || key === 'range') continue
      const child = n[key]
      if (child && typeof child === 'object') stack.push(child)
    }
  }
  return false
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'a bound Promise.allSettled result must have at least one `.reason` access in the enclosing function, else rejected outcomes are silently dropped. Escape with a // no-report: marker.' },
    messages: {
      swallow: 'Promise.allSettled result is bound but no `.reason` is read in the enclosing function: rejected outcomes are silently dropped. Read `.reason` on the rejected entries, or carry `// no-report: <reason>` above',
    },
    schema: [],
  },
  create(context) {
    const sc = context.sourceCode || context.getSourceCode()
    return {
      CallExpression(node) {
        if (!isAllSettledCall(node)) return
        if (!resultIsBound(node)) return
        if (hasMarkerAbove(context, node, 'no-report')) return
        if (refsReason(enclosingFn(node) || sc.ast)) return
        context.report({ node, messageId: 'swallow' })
      },
    }
  },
}
