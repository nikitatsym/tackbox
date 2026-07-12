const { tier1ReporterName, resolvesToDeclaredReporter, calleeName, matchesSecret, walk } = require('./_shared')

// Deep-scan every reporter argument for a secret-named identifier or member
// (token/password/key/secret/cookie, case-insensitive substring). The name is
// the risk: an identifier/field so named carries a live secret VALUE into
// telemetry. Reaches interpolations, `+` concatenation, and nested tag values,
// since every one surfaces the offending Identifier node. String-literal prose
// is clean (a message reading "auth token expired" is text, not a value) -
// mirrors the opengrep `erc006-fingerprint-secret-arg` `$ID` match and the
// spec's "secret-like names".
// Every capture site is scanned, so recognition covers both a tier-1
// import-origin reporter and a tier-2 `.tackbox-reporters`-declared function
// (matches Go IsReporterCall / Python TBX009). tier-2's signature is unknown,
// so - like Go/Python - all args are scanned, same as the tier-1 path already is.

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'reporter args may not name a secret via identifier or member (token/password/key/secret/cookie); string-literal prose is clean' },
    messages: {
      secretIdent: '{{name}}: argument references a secret-named identifier ({{word}}); these must never reach Sentry',
    },
    schema: [],
  },
  create(context) {
    return {
      CallExpression(node) {
        const name =
          tier1ReporterName(context, node) ||
          (resolvesToDeclaredReporter(context, node) ? calleeName(node.callee) : null)
        if (!name) return
        const seen = new Set()
        for (const arg of node.arguments) {
          walk(arg, n => {
            if (n.type !== 'Identifier' || seen.has(n)) return
            seen.add(n)
            const word = matchesSecret(n.name)
            if (word) context.report({ node: n, messageId: 'secretIdent', data: { name, word } })
          })
        }
      },
    }
  },
}
