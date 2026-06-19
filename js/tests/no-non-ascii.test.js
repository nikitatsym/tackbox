const { test } = require('node:test')
const assert = require('node:assert/strict')
const { lint } = require('markdownlint/promise')
const rule = require('../markdownlint-rules/no-non-ascii')

async function run(markdown) {
  const out = await lint({
    strings: { 'in.md': markdown },
    config: { default: false, 'no-non-ascii': true },
    customRules: [rule],
  })
  return out['in.md']
}

test('passes on pure ASCII content', async () => {
  const errs = await run([
    '# heading',
    '',
    'A paragraph with - dash, "quotes", `code`, and 0x7f end.',
    '',
    '```bash',
    'echo hi',
    '```',
  ].join('\n'))
  assert.equal(errs.length, 0)
})

test('flags assorted non-ASCII (em-dash, curly, Cyrillic, box)', async () => {
  const errs = await run('em \u{2014} curly \u{201C}x\u{201D} hi \u{43F}\n\u{251C}\n')
  const hexes = errs.map(e => e.errorDetail.match(/U\+([0-9A-F]+)/)[1])
  assert.deepEqual(hexes, ['2014', '201C', '201D', '43F', '251C'])
})

test('flags non-ASCII inside fenced code blocks', async () => {
  const errs = await run('```text\nhello — world\n```\n')
  assert.equal(errs.length, 1)
  assert.equal(errs[0].lineNumber, 2)
})

test('reports column position and astral codepoints', async () => {
  const errs = await run('abc\u{1F680}xyz\n')
  assert.equal(errs.length, 1)
  assert.match(errs[0].errorDetail, /U\+1F680/)
  assert.deepEqual(errs[0].errorRange, [4, 2])
})
