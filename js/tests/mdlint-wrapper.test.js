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

// --files-from feeds the file set through a list-file (ARG_MAX safety); it must
// lint exactly those paths and never treat the flag or its path as a file.
test('--files-from list is linted like positional paths', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '# hi\n\nrocket: \u{1F680}\n')
    writeFileSync(path.join(dir, 'files.txt'), 'bad.md\n')
    const r = spawnSync('node', [WRAPPER, '--files-from', path.join(dir, 'files.txt')], { cwd: dir, encoding: 'utf8' })
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /no-non-ascii/)
  })
})

// End-to-end (default rules + noInlineConfig): the lang marker survives the
// real CLI path, not just the rule unit tests.

const PRIVET = '\u{41F}\u{440}\u{438}\u{432}\u{435}\u{442}' // "Privet" (hello)
const MIR = '\u{43C}\u{438}\u{440}' // "mir" (world)

test('ru marker: Russian prose passes the real wrapper clean', () => {
  withTmp(dir => {
    writeFileSync(
      path.join(dir, 'ru.md'),
      '<!-- tackbox: lang=ru personal repo -->\n\n# notes\n\n' + PRIVET + ' \u{2014} ' + MIR + '.\n'
    )
    const r = lintInTmp(dir, 'ru.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

test('ru marker still flags an emoji, not the Cyrillic', () => {
  withTmp(dir => {
    writeFileSync(
      path.join(dir, 'ru.md'),
      '<!-- tackbox: lang=ru note -->\n\n# notes\n\n' + PRIVET + ' \u{1F680}\n'
    )
    const r = lintInTmp(dir, 'ru.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /U\+1F680/)
    assert.doesNotMatch(r.stdout, /U\+41F/)
  })
})
