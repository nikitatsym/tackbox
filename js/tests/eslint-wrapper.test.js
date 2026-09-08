const path = require('node:path')
const os = require('node:os')
const fs = require('node:fs')
const { spawnSync } = require('node:child_process')
const { test } = require('node:test')
const assert = require('node:assert/strict')

const WRAPPER = path.resolve(__dirname, '..', '..', 'bin', 'tackbox-eslint.js')

// An empty catch defeated by an inline eslint-disable. The hermetic wrapper must
// ignore the directive (allowInlineConfig: false) so the swallow still fails the
// run: an inline disable is an uninventoried, ungated bypass of every JS rule,
// invisible to `tackbox escapes` and the approval gate.
const DISABLED_SWALLOW = [
  'function handler() {',
  '  try {',
  '    doThing()',
  '  // eslint-disable-next-line tackbox/no-swallow-catch',
  '  } catch (e) {',
  '  }',
  '}',
  '',
].join('\n')

function inTmpDir(body) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tackbox-eslint-'))
  fs.writeFileSync(path.join(dir, 'bad.js'), DISABLED_SWALLOW)
  try {
    return body(dir)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

function runWrapper(dir, args) {
  return spawnSync('node', [WRAPPER, ...args, 'bad.js'], { cwd: dir, encoding: 'utf8' })
}

test('inline eslint-disable cannot silence a swallow (default mode)', () => {
  inTmpDir(dir => {
    const r = runWrapper(dir, [])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /no-swallow-catch/)
  })
})

test('inline eslint-disable cannot silence a swallow (--machine mode)', () => {
  inTmpDir(dir => {
    const r = runWrapper(dir, ['--machine'])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /no-swallow-catch/)
  })
})

// --files-from feeds the file set through a list-file (ARG_MAX safety); it must
// lint exactly those paths and never treat the flag or its path as a file.
function lintViaList(listContent) {
  inTmpDir(dir => {
    const list = path.join(dir, 'files.txt')
    fs.writeFileSync(list, listContent)
    const r = spawnSync('node', [WRAPPER, '--files-from', list], { cwd: dir, encoding: 'utf8' })
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /no-swallow-catch/)
  })
}

test('--files-from list is linted like positional paths', () => lintViaList('bad.js\n'))

// A list-file written on Windows carries CRLF; a trailing \r on a path is ENOENT.
test('--files-from strips CRLF line endings', () => lintViaList('bad.js\r\n'))

test('machine output preserves suppressed findings without failing human lint', () => {
  inTmpDir(dir => {
    fs.writeFileSync(path.join(dir, 'bad.js'),
      '// no-report: caller tolerates this failure\ntry { work() } catch (error) {}\n')
    const human = runWrapper(dir, [])
    assert.equal(human.status, 0, human.stderr)
    assert.equal(human.stdout, '')
    const machine = runWrapper(dir, ['--machine'])
    assert.equal(machine.status, 0, machine.stderr)
    const finding = JSON.parse(machine.stdout)
    assert.equal(finding.suppressed, true)
    assert.equal(finding.marker_kind, 'no-report')
    assert.equal(finding.marker_line, 1)
    assert.equal(finding.line, 2)
    assert.equal(finding.message, 'every catch path must throw, call a reporter, or convert to a Result boundary')
    fs.writeFileSync(path.join(dir, 'bad.js'),
      '// no-report: caller tolerates this failure\ntry { work() } catch (error) { throw error }\n')
    const remaining = JSON.parse(runWrapper(dir, ['--machine']).stdout)
    assert.equal(remaining.rule, 'tackbox/ts-useless-catch')
    assert.equal(remaining.suppressed, undefined)
  })
})
