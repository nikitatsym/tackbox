const {
  hasMarkerAbove,
  enclosingFn,
  fnReturnsResultLike,
  errObjectFlows,
  walk,
} = require('./_shared')

// isJsonParseCall: syntactic `JSON.parse(...)`. v1 scope is JSON.parse only -
// syntactically unambiguous, so no name-trust is needed (plan F7c).
function isJsonParseCall(n) {
  const c = n.callee
  return (
    !!c &&
    c.type === 'MemberExpression' &&
    !c.computed &&
    c.object.type === 'Identifier' &&
    c.object.name === 'JSON' &&
    c.property.type === 'Identifier' &&
    c.property.name === 'parse'
  )
}

// tryBlockParses: the try block directly contains a JSON.parse call. walk stops
// at nested function boundaries, so a JSON.parse inside a callback (setTimeout,
// .map) - which this try does not guard in the same tick - does not trigger.
function tryBlockParses(block) {
  let found = false
  walk(block, n => {
    if (n.type === 'CallExpression' && isJsonParseCall(n)) found = true
  })
  return found
}

// boundaryPropagates: `{ ok: false, cause|message: <err object> }` - a Result
// boundary that carries the caught error as a live object. Stricter than
// _shared.isBoundaryValue: F7c breaks on stringification (message: err.message).
function boundaryPropagates(expr, errName) {
  if (!errName || !expr || expr.type !== 'ObjectExpression') return false
  const ok = expr.properties.find(
    p => p.type === 'Property' && p.key && p.key.type === 'Identifier' && p.key.name === 'ok',
  )
  if (!ok || ok.value.type !== 'Literal' || ok.value.value !== false) return false
  return expr.properties.some(
    p =>
      p.type === 'Property' &&
      p.key &&
      p.key.type === 'Identifier' &&
      (p.key.name === 'cause' || p.key.name === 'message') &&
      errObjectFlows(p.value, errName),
  )
}

// localCarrierRHS: the RHS of the last local assignment to `name` among stmts
// (a `const/let name = rhs` declarator or a `name = rhs` assignment). Resolves a
// two-step wrap (`const w = new Error(..., { cause: e }); throw w`), which F5
// credits by checking the branch-local assignment.
function localCarrierRHS(stmts, name) {
  let rhs = null
  for (const st of stmts) {
    if (st.type === 'VariableDeclaration') {
      for (const d of st.declarations) {
        if (d.id.type === 'Identifier' && d.id.name === name && d.init) rhs = d.init
      }
    } else if (
      st.type === 'ExpressionStatement' &&
      st.expression.type === 'AssignmentExpression' &&
      st.expression.operator === '=' &&
      st.expression.left.type === 'Identifier' &&
      st.expression.left.name === name
    ) {
      rhs = st.expression.right
    }
  }
  return rhs
}

// containsExit: a return or throw anywhere in stmt (not descending into nested
// functions). Used to fail closed on opaque constructs (switch/loop/try) whose
// paths the analysis does not model.
function containsExit(stmt) {
  let found = false
  walk(stmt, n => {
    if (n.type === 'ReturnStatement' || n.type === 'ThrowStatement') found = true
  })
  return found
}

// catchPropagates: every path out of the catch must terminate by re-throwing
// the caught error object or returning a Result boundary that carries it. A
// fallback value, a throw that drops or stringifies the error, or a
// report-and-continue is a swallow - no reporter credit (report+default =
// finding) and no fall-through credit. Mirror of Go ERC002 restricted to
// object-flow exits. States: 'ok' (path terminated chain-preservingly), 'bad'
// (a path swallows), 'fall' (control falls past to the next statement).
function catchPropagates(body, errName, allowBoundary) {
  const topStmts = body.type === 'BlockStatement' ? body.body : []
  // carrier resolves a bare local identifier to its assigned RHS (two-step
  // wrap); other expressions pass through unchanged.
  function carrier(expr) {
    if (expr && expr.type === 'Identifier' && expr.name !== errName) {
      const rhs = localCarrierRHS(topStmts, expr.name)
      if (rhs) return rhs
    }
    return expr
  }
  function analyze(stmt) {
    if (!stmt) return 'fall'
    switch (stmt.type) {
      case 'ThrowStatement':
        return errObjectFlows(carrier(stmt.argument), errName) ? 'ok' : 'bad'
      case 'ReturnStatement':
        return allowBoundary && boundaryPropagates(carrier(stmt.argument), errName) ? 'ok' : 'bad'
      case 'BlockStatement':
        return analyzeList(stmt.body)
      case 'IfStatement': {
        const c = analyze(stmt.consequent)
        if (c === 'bad') return 'bad'
        const a = stmt.alternate ? analyze(stmt.alternate) : 'fall'
        if (a === 'bad') return 'bad'
        return c === 'ok' && a === 'ok' ? 'ok' : 'fall'
      }
      default:
        return containsExit(stmt) ? 'bad' : 'fall'
    }
  }
  function analyzeList(stmts) {
    for (const stmt of stmts) {
      const r = analyze(stmt)
      if (r !== 'fall') return r
    }
    return 'fall'
  }
  return analyze(body) === 'ok'
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'a try containing JSON.parse must propagate the parse error on every catch path (throw the caught object, or a Result boundary carrying it). A fallback value, a stringified rethrow, or report-and-continue swallows it. Escape with a // parse-skip: marker.' },
    messages: {
      fallback: 'try around JSON.parse must propagate the parse error: every catch path must `throw` the caught error object or return a Result boundary carrying it (`return { ok: false, cause: <err> }` when the enclosing function returns Result/Attempt). A fallback value, a stringified rethrow, or report-and-continue swallows it; add `// parse-skip: <reason>` above the try to opt out',
    },
    schema: [],
  },
  create(context) {
    return {
      TryStatement(node) {
        if (!tryBlockParses(node.block)) return
        const handler = node.handler
        if (!handler || !handler.body || handler.body.type !== 'BlockStatement') return
        if (hasMarkerAbove(context, node, 'parse-skip')) return
        const errName = handler.param && handler.param.type === 'Identifier' ? handler.param.name : null
        const allowBoundary = fnReturnsResultLike(enclosingFn(node))
        if (catchPropagates(handler.body, errName, allowBoundary)) return
        context.report({ node: handler, messageId: 'fallback' })
      },
    }
  },
}
