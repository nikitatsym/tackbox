const { hasMarkerAbove, walk, isTier1Notify, argFlows, isTestFile } = require('./_shared')

// guarded: `call` sits under an additional condition strictly inside the catch
// body - an if-branch (consequent/alternate) or a switch case. A notify
// reachable without crossing such a construct is the unconditional sole
// handling of the whole catch.
function guarded(call, catchBody) {
  for (let cur = call; cur && cur !== catchBody; cur = cur.parent) {
    const p = cur.parent
    if (!p) return false
    if (p.type === 'IfStatement' && (cur === p.consequent || cur === p.alternate)) return true
    if (p.type === 'SwitchCase') return true
  }
  return false
}

module.exports = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'a notify terminating a catch must sit under an additional condition (if/switch), not handle the whole catch unconditionally - an unconditional notify routes every error to a toast and blinds telemetry (D006)',
    },
    messages: {
      broad:
        'notify handles this catch unconditionally, routing every error to the user lane and blinding telemetry: put it under a condition and report the complement with reportError/reportWarn, or capture instead; a new // no-report: marker needs user approval',
    },
    schema: [],
  },
  create(context) {
    if (isTestFile(context)) return {}
    return {
      CatchClause(node) {
        const errName = node.param && node.param.type === 'Identifier' ? node.param.name : null
        if (errName == null) return
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        if (hasMarkerAbove(context, node.parent, 'no-report')) return
        walk(body, call => {
          if (call.type !== 'CallExpression') return
          if (!isTier1Notify(context, call) || !argFlows(call, errName)) return
          if (!guarded(call, body)) context.report({ node: call, messageId: 'broad' })
        })
      },
    }
  },
}
