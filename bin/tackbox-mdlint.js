#!/usr/bin/env node
const fs = require('fs')
const { lint } = require('markdownlint/promise')
const noNonAscii = require('../js/markdownlint-rules/no-non-ascii')

// readFilesFrom reads a newline-separated UTF-8 list-file into its non-empty
// paths. Additive to positional paths - the bin is public on npm.
function readFilesFrom(listPath) {
  return fs.readFileSync(listPath, 'utf8').split('\n').filter(Boolean)
}

async function run() {
  const argv = process.argv.slice(2)
  const machine = argv.includes('--machine')
  const files = []
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--machine') continue
    // The file set rides a list-file, not positional argv (ARG_MAX safety).
    if (a === '--files-from') { files.push(...readFilesFrom(argv[++i])); continue }
    files.push(a)
  }
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
        const message = e.ruleDescription + (e.errorDetail ? ' [' + e.errorDetail + ']' : '')
        process.stdout.write(JSON.stringify({ file, line: e.lineNumber, rule: e.ruleNames[0], message }) + '\n')
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

// no-report: CLI bootstrap, no reporter wired before run() executes
run().catch(err => {
  process.stderr.write('tackbox-mdlint: ' + (err && err.stack || err) + '\n')
  process.exit(2)
})
