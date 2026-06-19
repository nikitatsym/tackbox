#!/usr/bin/env node
const path = require('path')
const { ESLint } = require('eslint')

async function main() {
  const eslint = new ESLint({
    overrideConfigFile: path.join(__dirname, '..', 'eslint.config.preset.js'),
  })
  const files = process.argv.slice(2)
  if (files.length === 0) {
    process.stderr.write('tackbox-eslint: no files supplied\n')
    process.exit(2)
  }
  const results = await eslint.lintFiles(files)
  const formatter = await eslint.loadFormatter('stylish')
  const output = await formatter.format(results)
  if (output) process.stdout.write(output + '\n')
  const fail = results.some(r => r.errorCount > 0 || r.fatalErrorCount > 0)
  process.exit(fail ? 1 : 0)
}

// no-sentry: CLI bootstrap, no reporter wired before main() runs
main().catch(err => {
  process.stderr.write('tackbox-eslint: ' + (err && err.stack || err) + '\n')
  process.exit(2)
})
