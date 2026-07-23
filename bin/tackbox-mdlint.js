#!/usr/bin/env node
const fs = require('fs')
const { lint } = require('markdownlint/promise')
const declaredChars = require('../js/markdownlint-rules/declared-chars')
const linkIntegrity = require('../js/markdownlint-rules/link-integrity')

// readFilesFrom reads a newline-separated UTF-8 list-file into its non-empty
// paths. Additive to positional paths - the bin is public on npm.
function readFilesFrom(listPath) {
  return fs.readFileSync(listPath, 'utf8').split(/\r?\n/).filter(Boolean)
}

// Parse the link-target inventory list-file (LF, `<kind>\t<path>` per line) the
// tackbox CLI writes: F = linkable files, L = tracked symlinks, G = gitlink
// roots. Repo-relative paths, kept verbatim.
function readLinkTargets(listPath) {
  const F = new Set()
  const L = new Set()
  const G = []
  for (const line of readFilesFrom(listPath)) {
    const tab = line.indexOf('\t')
    const kind = line.slice(0, tab)
    const p = line.slice(tab + 1)
    if (kind === 'F') F.add(p)
    else if (kind === 'L') L.add(p)
    else if (kind === 'G') G.push(p)
  }
  return { F, L, G }
}

const USAGE =
  'tackbox-mdlint: --repo-root <dir> and --link-targets-from <list-file> are ' +
  'required (the cross-file link rule needs the whole-tree target inventory)\n'

async function run() {
  const argv = process.argv.slice(2)
  const machine = argv.includes('--machine')
  const files = []
  let repoRoot = null
  let linkTargetsFrom = null
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--machine') continue
    // The file set rides a list-file, not positional argv (ARG_MAX safety).
    if (a === '--files-from') { files.push(...readFilesFrom(argv[++i])); continue }
    if (a === '--repo-root') { repoRoot = argv[++i]; continue }
    if (a === '--link-targets-from') { linkTargetsFrom = argv[++i]; continue }
    files.push(a)
  }
  if (!repoRoot || !linkTargetsFrom) {
    process.stderr.write(USAGE)
    process.exit(2)
  }
  if (files.length === 0) {
    process.stderr.write('tackbox-mdlint: no files supplied\n')
    process.exit(2)
  }
  const { F, L, G } = readLinkTargets(linkTargetsFrom)
  const result = await lint({
    files,
    // Style preset off; only the link-reference built-ins plus the declared-
    // charset and cross-file link rules run (D017/D018). noInlineConfig blocks
    // in-file rule toggles.
    config: {
      default: false,
      MD011: true,
      MD042: true,
      MD051: true,
      MD052: true,
      'declared-chars': true,
      'link-integrity': true,
    },
    customRules: [declaredChars, linkIntegrity.makeRule({ repoRoot, F, L, G })],
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
