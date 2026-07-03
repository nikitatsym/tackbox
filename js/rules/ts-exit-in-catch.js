const { walk } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'process.exit(...) inside catch masks the exception' },
    messages: {
      exit: 'process.exit(...) inside catch masks the exception: let it propagate',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        walk(node.body, n => {
          if (n.type !== 'CallExpression') return
          const callee = n.callee
          if (!callee || callee.type !== 'MemberExpression') return
          if (!callee.object || callee.object.type !== 'Identifier' || callee.object.name !== 'process') return
          if (!callee.property || callee.property.type !== 'Identifier' || callee.property.name !== 'exit') return
          context.report({ node: n, messageId: 'exit' })
        })
      },
    }
  },
}
