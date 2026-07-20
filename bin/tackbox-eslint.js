#!/usr/bin/env node
const path = require('path')
const fs = require('fs')
const { ESLint } = require('eslint')

const REPORTERS_FLAG = '--reporters='

// Split argv into declared reporters (`--reporters=file#func,...`) and the
// files to lint.
function parseArgv(argv) {
  const decls = []
  const files = []
  let machine = false
  for (const a of argv) {
    if (a === '--machine') {
      machine = true
    } else if (a.startsWith(REPORTERS_FLAG)) {
      for (const d of a.slice(REPORTERS_FLAG.length).split(',')) {
        if (!d) continue
        const hash = d.lastIndexOf('#')
        if (hash > 0) decls.push({ raw: d, file: d.slice(0, hash), fn: d.slice(hash + 1) })
      }
    } else {
      files.push(a)
    }
  }
  return { decls, files, machine }
}

function parseModule(file, code) {
  const ext = path.extname(file)
  if (ext === '.ts' || ext === '.tsx') {
    return require('@typescript-eslint/parser').parse(code, {
      ecmaVersion: 'latest',
      sourceType: 'module',
    })
  }
  if (ext === '.svelte') {
    return require('svelte-eslint-parser').parse(code, {})
  }
  return require('espree').parse(code, { ecmaVersion: 'latest', sourceType: 'module' })
}

// hasBinding: the AST defines a function or const-arrow named `name`. Existence
// check for `.tackbox/reporters` symbol validation; a deep walk keeps it parser
// shape-agnostic across espree / ts / svelte.
function hasBinding(ast, name) {
  const seen = new Set()
  let found = false
  const visit = node => {
    if (found || !node || typeof node !== 'object' || seen.has(node)) return
    seen.add(node)
    if (Array.isArray(node)) {
      for (const c of node) visit(c)
      return
    }
    if (node.type === 'FunctionDeclaration' && node.id && node.id.name === name) {
      found = true
      return
    }
    if (
      node.type === 'VariableDeclarator' &&
      node.id && node.id.type === 'Identifier' && node.id.name === name &&
      node.init &&
      (node.init.type === 'ArrowFunctionExpression' || node.init.type === 'FunctionExpression')
    ) {
      found = true
      return
    }
    for (const k of Object.keys(node)) {
      if (k === 'parent' || k === 'loc' || k === 'range' || k === 'tokens' || k === 'comments') continue
      const c = node[k]
      if (c && typeof c === 'object') visit(c)
    }
  }
  visit(ast)
  return found
}

// Validate every declaration's symbol, independent of the lint scope: a dead
// `file#function` fails the whole run even when that file is not being linted.
function validateDeclarations(decls) {
  for (const d of decls) {
    const abs = path.resolve(process.cwd(), d.file)
    let code
    try {
      code = fs.readFileSync(abs, 'utf8')
    } catch (e) {
      throw new Error(`.tackbox/reporters: cannot read ${d.file}: ${e.message}`, { cause: e })
    }
    let ast
    try {
      ast = parseModule(d.file, code)
    } catch (e) {
      throw new Error(`.tackbox/reporters: cannot parse ${d.file}: ${e.message}`, { cause: e })
    }
    if (!hasBinding(ast, d.fn)) {
      throw new Error(`.tackbox/reporters: no top-level function ${d.fn} in ${d.file}`)
    }
  }
}

// Internal machine contract: one {file, line, rule} JSON object per error, for
// the hook. Human (stylish) output is unchanged. A message with no line emits
// line: null (location-unknown) - the caller over-reports, never drops it.
function emitMachine(results) {
  for (const r of results) {
    const file = path.relative(process.cwd(), r.filePath)
    for (const m of r.messages) {
      if (m.severity !== 2) continue
      process.stdout.write(JSON.stringify({ file, line: m.line ?? null, rule: m.ruleId, message: m.message }) + '\n')
    }
  }
}

async function main() {
  const { decls, files, machine } = parseArgv(process.argv.slice(2))
  if (files.length === 0) {
    process.stderr.write('tackbox-eslint: no files supplied\n')
    process.exit(2)
  }
  validateDeclarations(decls)
  const eslint = new ESLint({
    // Inline directives (eslint-disable ... tackbox/<rule>) would be an
    // uninventoried, ungated bypass of every tackbox JS rule. Closed here on
    // the hermetic/published wrapper path; the preset closes it for consumers.
    allowInlineConfig: false,
    overrideConfigFile: path.join(__dirname, '..', 'eslint.config.preset.js'),
    overrideConfig: [{ settings: { tackbox: { reporters: decls.map(d => d.raw) } } }],
  })
  const results = await eslint.lintFiles(files)
  if (machine) {
    emitMachine(results)
  } else {
    const formatter = await eslint.loadFormatter('stylish')
    const output = await formatter.format(results)
    if (output) process.stdout.write(output + '\n')
  }
  const fail = results.some(r => r.errorCount > 0 || r.fatalErrorCount > 0)
  process.exit(fail ? 1 : 0)
}

// no-report: CLI bootstrap, no reporter wired before main() runs
main().catch(err => {
  process.stderr.write('tackbox-eslint: ' + (err && err.stack || err) + '\n')
  process.exit(2)
})
