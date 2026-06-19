const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const { mkdtempSync, writeFileSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')

const WRAPPER = path.resolve(__dirname, '..', '..', 'bin', 'tackbox-mdlint.js')

function withTmp(fn) {
  const dir = mkdtempSync(path.join(tmpdir(), 'tackbox-mdlint-'))
  try { return fn(dir) } finally { rmSync(dir, { recursive: true, force: true }) }
}

function lintInTmp(dir, file) {
  return spawnSync('node', [WRAPPER, file], { cwd: dir, encoding: 'utf8' })
}

test('ignores consumer .markdownlint.json that disables defaults', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, '.markdownlint.json'), '{"default": false}')
    writeFileSync(path.join(dir, 'bad.md'), '# hi\n\nrocket: \u{1F680}\n')
    const r = lintInTmp(dir, 'bad.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /no-non-ascii/)
    assert.match(r.stdout, /U\+1F680/)
  })
})

test('rejects inline markdownlint-disable for the ASCII rule', () => {
  withTmp(dir => {
    const md = [
      '# hi',
      '',
      '<!-- markdownlint-disable no-non-ascii -->',
      '',
      'still flagged: \u{2014}',
      '',
    ].join('\n')
    writeFileSync(path.join(dir, 'bad.md'), md)
    const r = lintInTmp(dir, 'bad.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /U\+2014/)
  })
})

test('exits 0 on clean ASCII file', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'ok.md'), '# clean\n\nplain ASCII only.\n')
    const r = lintInTmp(dir, 'ok.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})
