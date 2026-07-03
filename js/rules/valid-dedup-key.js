const {
  REPORTER_FULL, REPORTER_SYNTH,
  tier1ReporterName, isStaticString, staticStringValue, DEDUP_KEY_RE,
} = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'dedupKey must be a static literal in `area.suffix[:identifier]` form' },
    messages: {
      notLiteral: '{{name}}: dedupKey must be a static string literal so the fingerprint is stable',
      badFormat: '{{name}}: dedupKey must follow `area.suffix[:identifier]` format (got "{{value}}")',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const name = tier1ReporterName(context, node)
        if (!name) return

        let keyIdx = -1
        if (REPORTER_FULL.has(name)) keyIdx = 3
        else if (REPORTER_SYNTH.has(name)) keyIdx = 2
        else return

        const key = node.arguments[keyIdx]
        if (!key) return // missing key is reported by valid-error-report

        if (!isStaticString(key)) {
          context.report({ node: key, messageId: 'notLiteral', data: { name } })
          return
        }
        const v = staticStringValue(key)
        if (!DEDUP_KEY_RE.test(v)) {
          context.report({ node: key, messageId: 'badFormat', data: { name, value: v } })
        }
      },
    }
  },
}
