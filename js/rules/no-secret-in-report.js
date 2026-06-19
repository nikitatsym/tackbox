const {
  REPORTER_NAMES, calleeName, exprIsSecretRef, matchesSecret,
  isStaticString, staticStringValue,
} = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'reporter args may not name or contain secret stop-words (token/password/key/secret/cookie)' },
    messages: {
      secretIdent: '{{name}}: argument references a secret-named identifier ({{word}}); these must never reach Sentry',
      secretString: '{{name}}: argument contains the secret stop-word "{{word}}" in a string literal; redact before capture',
    },
    schema: [],
  },
  create(context) {
    function checkExpr(arg, nameArg) {
      const word = exprIsSecretRef(arg)
      if (word) {
        context.report({ node: arg, messageId: 'secretIdent', data: { name: nameArg, word } })
        return true
      }
      if (isStaticString(arg)) {
        const value = staticStringValue(arg)
        const w = matchesSecret(value)
        if (w) {
          context.report({ node: arg, messageId: 'secretString', data: { name: nameArg, word: w } })
          return true
        }
      }
      return false
    }

    return {
      CallExpression(node) {
        const name = calleeName(node.callee)
        if (!REPORTER_NAMES.has(name)) return
        for (const arg of node.arguments) {
          if (checkExpr(arg, name)) continue
          if (arg.type === 'ObjectExpression') {
            for (const prop of arg.properties) {
              if (prop.type !== 'Property') continue
              const keyName = prop.key && (prop.key.name || prop.key.value)
              const keyHit = matchesSecret(keyName)
              if (keyHit) {
                context.report({ node: prop, messageId: 'secretIdent', data: { name, word: keyHit } })
                continue
              }
              checkExpr(prop.value, name)
            }
          }
        }
      },
    }
  },
}
