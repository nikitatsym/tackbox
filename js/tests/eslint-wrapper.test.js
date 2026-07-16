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
