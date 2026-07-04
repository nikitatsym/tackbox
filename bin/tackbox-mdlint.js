#!/usr/bin/env node
const { lint } = require('markdownlint/promise')
const noNonAscii = require('../js/markdownlint-rules/no-non-ascii')

async function run() {
  const argv = process.argv.slice(2)
  const machine = argv.includes('--machine')
  const files = argv.filter(a => a !== '--machine')
  if (files.length === 0) {
    process.stderr.write('tackbox-mdlint: no files supplied\n')
    process.exit(2)
  }
  const result = await lint({
    files,
    config: { default: true, 'no-non-ascii': true },
    customRules: [noNonAscii],
    noInlineConfig: true,
  })
  let count = 0
  for (const [file, errors] of Object.entries(result)) {
    for (const e of errors) {
      count++
      if (machine) {
        // Internal {file, line, rule} contract for the hook; human output below
        // is unchanged.
        process.stdout.write(JSON.stringify({ file, line: e.lineNumber, rule: e.ruleNames[0] }) + '\n')
        continue
      }
      const col = e.errorRange ? ':' + e.errorRange[0] : ''
      const detail = e.errorDetail ? ' [' + e.errorDetail + ']' : ''
      const name = e.ruleNames.slice(0, 2).join('/')
      process.stdout.write(
        file + ':' + e.lineNumber + col + ' ' + name + ' ' + e.ruleDescription + detail + '\n'
      )
    }
  }
  process.exit(count > 0 ? 1 : 0)
}

// no-sentry: CLI bootstrap, no reporter wired before run() executes
run().catch(err => {
  process.stderr.write('tackbox-mdlint: ' + (err && err.stack || err) + '\n')
  process.exit(2)
})
