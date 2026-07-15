const { blockHasThrow, blockHasReport, notifyCaptureConflict, isTestFile } = require('./_shared')

module.exports = {
  meta: {
    type: 'problem',
    docs: { description: 'catch block must not both throw and call a reporter; nor both capture and notify on one path (D006 double-lane)' },
    messages: {
      both: 'catch block both throws and calls a reporter: pick one - upstream handler would re-capture',
      doubleLane: 'catch path both captures and notifies: error/warn already reach the user lane, so the notify double-shows - drop the notify, or use only notify with no capture',
    },
    schema: [],
  },
  create(context) {
    return {
      CatchClause(node) {
        const body = node.body
        if (!body || body.type !== 'BlockStatement') return
        const errName = node.param && node.param.type === 'Identifier' ? node.param.name : null
        if (blockHasThrow(body) && blockHasReport(context, body, errName)) {
          context.report({ node, messageId: 'both' })
        }
        // The double-lane arm is a new D006 rule and skips tests (parity with
        // Go/Java); the `both` arm is pre-existing and keeps running in tests.
        if (!isTestFile(context) && notifyCaptureConflict(context, body, errName)) {
          context.report({ node, messageId: 'doubleLane' })
        }
      },
    }
  },
}
