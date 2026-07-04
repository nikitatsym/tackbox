const { walk } = require('./_shared')

// new AggregateError([...errors], msg) preserves the caught error in its
// errors array, so it does not also need { cause }.
function aggregateHoldsError(newExpr, errName) {
  const callee = newExpr.callee
  if (!callee || callee.type !== 'Identifier' || callee.name !== 'AggregateError') return false
  const first = newExpr.arguments[0]
  if (!first || first.type !== 'ArrayExpression') return false
  return first.elements.some(el => {
    if (!el) return false
    if (el.type === 'Identifier') return el.name === errName
    if (el.type === 'SpreadElement' && el.argument.type === 'Identifier') return el.argument.name === errName
    return false
  })
}

function optionsHasCause(newExpr, errName) {
  for (const arg of newExpr.arguments) {
    if (arg.type !== 'ObjectExpression') continue
    for (const prop of arg.properties) {
      if (prop.type !== 'Property' || prop.computed) continue
      const key =
        prop.key.type === 'Identifier'
          ? prop.key.name
          : prop.key.type === 'Literal'
            ? prop.key.value
            : null
      if (key !== 'cause') continue
      if (prop.value.type === 'Identifier' && prop.value.name === errName) return true
    }
  }
  return false
}

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'throwing a new error in catch without { cause: <caught> } discards the original stack' },
    messages: {
      noCause: 'throw new Error in catch must pass { cause: <caught error> } to preserve the stack chain',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const param = node.param
        if (!param || param.type !== 'Identifier') return
        const errName = param.name
        walk(node.body, n => {
          if (n.type !== 'ThrowStatement') return
          const arg = n.argument
          if (!arg || arg.type !== 'NewExpression') return
          if (optionsHasCause(arg, errName)) return
          if (aggregateHoldsError(arg, errName)) return
          context.report({ node: n, messageId: 'noCause' })
        })
      },
    }
  },
}
