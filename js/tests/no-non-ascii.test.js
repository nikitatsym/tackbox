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
  const errs = await run('```text\nhello \u{2014} world\n```\n')
  assert.equal(errs.length, 1)
  assert.equal(errs[0].lineNumber, 2)
})

test('reports column position and astral codepoints', async () => {
  const errs = await run('abc\u{1F680}xyz\n')
  assert.equal(errs.length, 1)
  assert.match(errs[0].errorDetail, /U\+1F680/)
  assert.deepEqual(errs[0].errorRange, [4, 2])
})

// -- lang marker ----------------------------------------------------------

// Cyrillic building blocks, written as \u escapes so this source stays ASCII.
const PRIVET = '\u{41F}\u{440}\u{438}\u{432}\u{435}\u{442}' // "Privet" (hello)
const MIR = '\u{43C}\u{438}\u{440}' // "mir" (world)

function detailHexes(errs) {
  return errs.map(e => e.errorDetail.match(/U\+([0-9A-F]+)/)[1])
}

test('ru marker widens the alphabet: Russian prose + typography + ASCII code -> clean', async () => {
  const md = [
    '<!-- tackbox: lang=ru personal experimental repo -->',
    '# ' + PRIVET,
    '',
    // em-dash, guillemets, ellipsis, NBSP all allowed under ru
    PRIVET + ' \u{2014} \u{AB}' + MIR + '\u{BB}\u{2026}\u{A0}end',
    '',
    '```bash',
    'echo hi',
    '```',
  ].join('\n')
  const errs = await run(md)
  assert.equal(errs.length, 0, JSON.stringify(errs))
})

test('ru marker still flags emoji and CJK, and only those', async () => {
  const md = [
    '<!-- tackbox: lang=ru note -->',
    PRIVET + ' \u{1F680} \u{4E2D}', // rocket + CJK amid allowed Cyrillic
  ].join('\n')
  const errs = await run(md)
  assert.deepEqual(detailHexes(errs), ['1F680', '4E2D'])
})

test('no marker: Cyrillic is still flagged (default unchanged)', async () => {
  const errs = await run(PRIVET + '\n')
  assert.deepEqual(detailHexes(errs), ['41F', '440', '438', '432', '435', '442'])
})

test('marker below line 5 is a finding and does not widen', async () => {
  const md = [
    'line 1', 'line 2', 'line 3', 'line 4', 'line 5',
    '<!-- tackbox: lang=ru too late -->',
    PRIVET, // line 7
  ].join('\n')
  const errs = await run(md)
  const placement = errs.find(e => /within the first 5 lines/.test(e.errorDetail))
  assert.ok(placement, 'expected a marker-placement finding: ' + JSON.stringify(errs))
  assert.equal(placement.lineNumber, 6)
  // File stays ASCII-only, so the Cyrillic on line 7 is still flagged.
  assert.ok(errs.some(e => e.lineNumber === 7 && /U\+41F/.test(e.errorDetail)))
})

test('duplicate marker is a finding and leaves the file ASCII-only', async () => {
  const md = [
    '<!-- tackbox: lang=ru first -->',
    '<!-- tackbox: lang=ru second -->',
    PRIVET,
  ].join('\n')
  const errs = await run(md)
  const dup = errs.filter(e => /duplicate tackbox lang marker/.test(e.errorDetail))
  assert.equal(dup.length, 1)
  assert.equal(dup[0].lineNumber, 2)
  assert.ok(errs.some(e => /U\+41F/.test(e.errorDetail)), 'Cyrillic still flagged')
})

test('unknown language code is a finding and does not widen', async () => {
  const errs = await run('<!-- tackbox: lang=xx -->\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /unsupported language code 'xx'/.test(e.errorDetail)))
  assert.ok(errs.some(e => /U\+41F/.test(e.errorDetail)))
})

test('marker with no language code is a finding and does not widen', async () => {
  const errs = await run('<!-- tackbox: lang= -->\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /missing a language code/.test(e.errorDetail)))
  assert.ok(errs.some(e => /U\+41F/.test(e.errorDetail)))
})
