const { test } = require('node:test')
const assert = require('node:assert/strict')
const { lint } = require('markdownlint/promise')
const rule = require('../markdownlint-rules/declared-chars')

async function run(markdown) {
  const out = await lint({
    strings: { 'in.md': markdown },
    config: { default: false, 'declared-chars': true },
    customRules: [rule],
  })
  return out['in.md']
}

// Cyrillic building blocks, written as \u escapes so this source stays ASCII.
const PRIVET = '\u{41F}\u{440}\u{438}\u{432}\u{435}\u{442}' // "Privet" (hello)
const MIR = '\u{43C}\u{438}\u{440}' // "mir" (world)

function detailHexes(errs) {
  return errs
    .map(e => e.errorDetail && e.errorDetail.match(/U\+([0-9A-F]+)/))
    .filter(Boolean)
    .map(m => m[1])
}

// -- the flip: no marker -> charset is not checked at all -----------------

test('no marker: Cyrillic, em-dash, and emoji are all clean', async () => {
  const errs = await run(PRIVET + ' \u{2014} \u{1F680}\n')
  assert.equal(errs.length, 0, JSON.stringify(errs))
})

test('no marker: pure ASCII is clean', async () => {
  const errs = await run('# heading\n\nplain text.\n')
  assert.equal(errs.length, 0)
})

// -- chars=ascii: declares the check with no extension -------------------

test('chars=ascii: em-dash is flagged (MD-CHARS)', async () => {
  const errs = await run('<!-- tackbox: chars=ascii -->\n\nem \u{2014} dash\n')
  assert.equal(errs.length, 1, JSON.stringify(errs))
  assert.deepEqual(errs[0].ruleNames, ['MD-CHARS', 'declared-chars'])
  assert.match(errs[0].errorDetail, /U\+2014/)
})

test('chars=ascii: Cyrillic is flagged', async () => {
  const errs = await run('<!-- tackbox: chars=ascii -->\n\n' + PRIVET + '\n')
  assert.deepEqual(detailHexes(errs), ['41F', '440', '438', '432', '435', '442'])
})

test('chars=ascii: pure ASCII is clean', async () => {
  const errs = await run('<!-- tackbox: chars=ascii -->\n\nplain code `x` and - dash.\n')
  assert.equal(errs.length, 0, JSON.stringify(errs))
})

// -- multi-set union -----------------------------------------------------

test('chars=ascii,cyrillic: mixed ru/en prose is clean; em-dash is flagged', async () => {
  const clean = await run('<!-- tackbox: chars=ascii,cyrillic -->\n\n' + PRIVET + ' and ' + MIR + '\n')
  assert.equal(clean.length, 0, JSON.stringify(clean))
  const errs = await run('<!-- tackbox: chars=ascii,cyrillic -->\n\n' + PRIVET + ' \u{2014}\n')
  assert.deepEqual(detailHexes(errs), ['2014'])
})

test('chars=cyrillic,punct: Cyrillic + em-dash + guillemets clean; emoji flagged', async () => {
  const clean = await run(
    '<!-- tackbox: chars=cyrillic,punct -->\n\n' + PRIVET + ' \u{2014} \u{AB}' + MIR + '\u{BB}\n'
  )
  assert.equal(clean.length, 0, JSON.stringify(clean))
  const errs = await run('<!-- tackbox: chars=cyrillic,punct -->\n\n' + PRIVET + ' \u{1F680}\n')
  assert.deepEqual(detailHexes(errs), ['1F680'])
})

test('chars=ascii, cyrillic: a space after the comma is valid', async () => {
  const errs = await run('<!-- tackbox: chars=ascii, cyrillic -->\n\n' + PRIVET + ' text\n')
  assert.equal(errs.length, 0, JSON.stringify(errs))
})

// -- invalid markers: finding on the marker, content NOT checked ---------

test('unknown set: finding on the marker, content is not checked', async () => {
  const errs = await run('<!-- tackbox: chars=xx -->\n\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /unknown character set 'xx'/.test(e.errorDetail)), JSON.stringify(errs))
  // The Cyrillic content is NOT additionally flagged - a broken declaration
  // leaves no default, it does not silently strict-check.
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

test('adversarial: a mistyped set name does not silently enable the check', async () => {
  // Typing chars=cyrilic (missing an l) must be rejected loudly, not treated as
  // "declared cyrillic" that then passes the Cyrillic content.
  const errs = await run('<!-- tackbox: chars=cyrilic -->\n\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /unknown character set 'cyrilic'/.test(e.errorDetail)), JSON.stringify(errs))
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

test('empty list: finding on the marker, content is not checked', async () => {
  const errs = await run('<!-- tackbox: chars= -->\n\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /empty character-set list/.test(e.errorDetail)), JSON.stringify(errs))
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

test('duplicate set: finding on the marker, content is not checked', async () => {
  const errs = await run('<!-- tackbox: chars=cyrillic,cyrillic -->\n\n' + PRIVET + '\n')
  assert.ok(errs.some(e => /duplicate set 'cyrillic'/.test(e.errorDetail)), JSON.stringify(errs))
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

test('duplicate marker: finding on the second, content is not checked', async () => {
  const md = [
    '<!-- tackbox: chars=cyrillic -->',
    '<!-- tackbox: chars=cyrillic -->',
    PRIVET,
  ].join('\n')
  const errs = await run(md)
  const dup = errs.filter(e => /duplicate tackbox chars marker/.test(e.errorDetail))
  assert.equal(dup.length, 1)
  assert.equal(dup[0].lineNumber, 2)
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

test('marker below the fifth line: placement finding, content is not checked', async () => {
  const md = [
    'line 1', 'line 2', 'line 3', 'line 4', 'line 5',
    '<!-- tackbox: chars=cyrillic -->',
    PRIVET, // line 7
  ].join('\n')
  const errs = await run(md)
  const placement = errs.find(e => /within the first 5 lines/.test(e.errorDetail))
  assert.ok(placement, 'expected a placement finding: ' + JSON.stringify(errs))
  assert.equal(placement.lineNumber, 6)
  assert.equal(detailHexes(errs).length, 0, JSON.stringify(errs))
})

// -- reporting shape (checked under a valid marker) ----------------------

test('reports column position and astral codepoints', async () => {
  const errs = await run('<!-- tackbox: chars=ascii -->\n\nabc\u{1F680}xyz\n')
  assert.equal(errs.length, 1)
  assert.match(errs[0].errorDetail, /U\+1F680/)
  assert.deepEqual(errs[0].errorRange, [4, 2])
})

test('checks non-ASCII inside fenced code blocks under a marker', async () => {
  const errs = await run('<!-- tackbox: chars=ascii -->\n\n```text\nhello \u{2014} world\n```\n')
  assert.equal(errs.length, 1)
  assert.equal(errs[0].lineNumber, 4)
})
