const { isStaticString, staticStringValue } = require('./_shared')

const MIN = 15
const MAX = 200

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'throw new Error(msg) must use a static 15-200 char message' },
    messages: {
      notStatic: 'throw new Error: message must be a static string literal (no template interpolation)',
      tooShort: 'throw new Error: message is {{len}} chars, must be at least {{min}}',
      tooLong: 'throw new Error: message is {{len}} chars, must be at most {{max}}',
    },
    schema: [],
  },
  create(context) {
    return {
      ThrowStatement(node) {
        const arg = node.argument
        if (!arg || arg.type !== 'NewExpression') return
        if (!arg.callee || arg.callee.type !== 'Identifier') return
        if (!/Error$/.test(arg.callee.name)) return
        if (arg.arguments.length === 0) return
        const msg = arg.arguments[0]
        if (!isStaticString(msg)) {
          context.report({ node: msg, messageId: 'notStatic' })
          return
        }
        const v = staticStringValue(msg)
        if (v.length < MIN) {
          context.report({ node: msg, messageId: 'tooShort', data: { len: v.length, min: MIN } })
        } else if (v.length > MAX) {
          context.report({ node: msg, messageId: 'tooLong', data: { len: v.length, max: MAX } })
        }
      },
    }
  },
}
