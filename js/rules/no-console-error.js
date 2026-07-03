const { isInDeclaredReporterBody } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'ban console.error in favor of reportError' },
    messages: { use: 'console.error is banned; use reportError/reportSynth instead' },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const c = node.callee
        if (!c || c.type !== 'MemberExpression') return
        if (c.object && c.object.type === 'Identifier' && c.object.name === 'console' &&
            c.property && c.property.type === 'Identifier' && c.property.name === 'error') {
          if (isInDeclaredReporterBody(context, node)) return
          context.report({ node, messageId: 'use' })
        }
      },
    }
  },
}
