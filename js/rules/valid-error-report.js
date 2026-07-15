const {
  REPORTER_FULL, REPORTER_SYNTH,
  tier1ReporterName, isTier1Notify, isStaticString, staticStringValue,
} = require('./_shared')

const MIN = 15
const MAX = 200

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'reporter/notify args: static 15-200 msg, cause non-null, tags non-empty, dedupKey present (notify shares the msg/cause/tags/dedupKey shape - D007/D008)' },
    messages: {
      noArgs: '{{name}} requires at least (msg, cause)',
      msgNotStatic: '{{name}}: first arg (msg) must be a static string literal (no template interpolation)',
      msgTooShort: '{{name}}: msg is {{len}} chars, must be at least {{min}}',
      msgTooLong: '{{name}}: msg is {{len}} chars, must be at most {{max}}',
      causeMissing: '{{name}}: second arg (cause) must be present and not null/undefined',
      tagsEmpty: '{{name}}: tags arg must not be {} — drop the arg or supply real tags',
      dedupMissing: '{{name}}: dedupKey is required (last arg) — spec mandates per-site dedupKey',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        let name = tier1ReporterName(context, node)
        // notify is not a reporter (never credits a swallow), but its user-lane
        // msg must be a static literal (D007) and it carries the full
        // (msg, cause, tags, dedupKey) shape, so it is validated like one.
        let notifyVerb = false
        if (!name) {
          if (isTier1Notify(context, node)) {
            name = 'notify'
            notifyVerb = true
          } else {
            return
          }
        }

        const args = node.arguments
        if (args.length < 1) {
          context.report({ node, messageId: 'noArgs', data: { name } })
          return
        }

        const msg = args[0]
        if (!isStaticString(msg)) {
          context.report({ node: msg, messageId: 'msgNotStatic', data: { name } })
        } else {
          const v = staticStringValue(msg)
          if (v.length < MIN) {
            context.report({ node: msg, messageId: 'msgTooShort', data: { name, len: v.length, min: MIN } })
          } else if (v.length > MAX) {
            context.report({ node: msg, messageId: 'msgTooLong', data: { name, len: v.length, max: MAX } })
          }
        }

        const isFull = REPORTER_FULL.has(name) || notifyVerb
        const isSynth = REPORTER_SYNTH.has(name)

        // (msg, cause, tags, dedupKey) for full reporters.
        if (isFull) {
          if (args.length < 2) {
            context.report({ node, messageId: 'causeMissing', data: { name } })
            return
          }
          const cause = args[1]
          if (cause.type === 'Literal' && (cause.value === null || cause.value === undefined)) {
            context.report({ node: cause, messageId: 'causeMissing', data: { name } })
          }
          if (cause.type === 'Identifier' && cause.name === 'undefined') {
            context.report({ node: cause, messageId: 'causeMissing', data: { name } })
          }
          const tags = args[2]
          if (tags && tags.type === 'ObjectExpression' && tags.properties.length === 0) {
            context.report({ node: tags, messageId: 'tagsEmpty', data: { name } })
          }
          if (args.length < 4) {
            context.report({ node, messageId: 'dedupMissing', data: { name } })
          }
          return
        }

        // (msg, tags, dedupKey) for synth reporters.
        if (isSynth) {
          const tags = args[1]
          if (tags && tags.type === 'ObjectExpression' && tags.properties.length === 0) {
            context.report({ node: tags, messageId: 'tagsEmpty', data: { name } })
          }
          if (args.length < 3) {
            context.report({ node, messageId: 'dedupMissing', data: { name } })
          }
        }
      },
    }
  },
}
