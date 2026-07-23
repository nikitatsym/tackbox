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

// The bin now requires --repo-root and --link-targets-from (D018). These charset
// / style / built-in tests carry no relative cross-file links, so an inventory
// naming just the linted file keeps the link rule silent.
function mandatoryFlags(dir, ...targets) {
  const inv = path.join(dir, '_targets.txt')
  writeFileSync(inv, targets.map(t => 'F\t' + t).join('\n') + '\n')
  return ['--repo-root', dir, '--link-targets-from', inv]
}

function lintInTmp(dir, file) {
  return spawnSync('node', [WRAPPER, ...mandatoryFlags(dir, file), file], { cwd: dir, encoding: 'utf8' })
}

// Cyrillic building blocks, written as \u escapes so this source stays ASCII.
const PRIVET = '\u{41F}\u{440}\u{438}\u{432}\u{435}\u{442}' // "Privet" (hello)
const MIR = '\u{43C}\u{438}\u{440}' // "mir" (world)

// -- declared charset: the marker drives the check end to end ------------

test('no marker: non-ASCII is not checked (the MD2a flip)', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'free.md'), '# notes\n\n' + PRIVET + ' \u{2014} \u{1F680}\n')
    const r = lintInTmp(dir, 'free.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

test('chars=ascii marker flags a non-ASCII character (MD-CHARS)', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'ascii.md'), '<!-- tackbox: chars=ascii -->\n# notes\n\nem \u{2014} dash\n')
    const r = lintInTmp(dir, 'ascii.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /MD-CHARS/)
    assert.match(r.stdout, /U\+2014/)
  })
})

test('chars=cyrillic marker: Russian prose is clean, emoji is flagged', () => {
  withTmp(dir => {
    writeFileSync(
      path.join(dir, 'ru.md'),
      '<!-- tackbox: chars=cyrillic -->\n\n# ' + PRIVET + '\n\n' + PRIVET + ' \u{1F680}\n'
    )
    const r = lintInTmp(dir, 'ru.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /U\+1F680/)
    assert.doesNotMatch(r.stdout, /U\+41F/)
  })
})

test('chars=cyrillic,punct marker: em-dash and guillemets ride along clean', () => {
  withTmp(dir => {
    writeFileSync(
      path.join(dir, 'ru.md'),
      '<!-- tackbox: chars=cyrillic,punct -->\n\n' + PRIVET + ' \u{2014} \u{AB}' + MIR + '\u{BB}.\n'
    )
    const r = lintInTmp(dir, 'ru.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

test('ignores consumer .markdownlint.json that disables the rule', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, '.markdownlint.json'), '{"default": false, "declared-chars": false}')
    writeFileSync(path.join(dir, 'bad.md'), '<!-- tackbox: chars=ascii -->\n# hi\n\nrocket: \u{1F680}\n')
    const r = lintInTmp(dir, 'bad.md')
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /declared-chars/)
    assert.match(r.stdout, /U\+1F680/)
  })
})

test('rejects inline markdownlint-disable for the charset rule', () => {
  withTmp(dir => {
    const md = [
      '<!-- tackbox: chars=ascii -->',
      '# hi',
      '',
      '<!-- markdownlint-disable declared-chars -->',
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

test('exits 0 on a clean ASCII file with no marker', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'ok.md'), '# clean\n\nplain ASCII only.\n')
    const r = lintInTmp(dir, 'ok.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

// -- style preset off, link-reference built-ins on -----------------------

test('style rules are disabled: a long, heading-less line is clean', () => {
  withTmp(dir => {
    const long =
      'This is a plain paragraph, definitely past eighty characters, so MD013 ' +
      'and MD041 would fire if the markdownlint style preset were still on here.'
    writeFileSync(path.join(dir, 'style.md'), long + '\n')
    const r = lintInTmp(dir, 'style.md')
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

test('MD011 reversed link is flagged; the correct form is clean', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '# links\n\n(click)[http://example.com]\n')
    const bad = lintInTmp(dir, 'bad.md')
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    assert.match(bad.stdout, /MD011/)
    writeFileSync(path.join(dir, 'ok.md'), '# links\n\n[click](http://example.com)\n')
    const ok = lintInTmp(dir, 'ok.md')
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

test('MD042 empty link is flagged; a non-empty link is clean', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '# links\n\n[text]()\n')
    const bad = lintInTmp(dir, 'bad.md')
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    assert.match(bad.stdout, /MD042/)
    writeFileSync(path.join(dir, 'ok.md'), '# links\n\n[text](http://example.com)\n')
    const ok = lintInTmp(dir, 'ok.md')
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

test('MD051 broken link fragment is flagged; a valid fragment is clean', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '# Heading\n\n[x](#no-such-heading)\n')
    const bad = lintInTmp(dir, 'bad.md')
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    assert.match(bad.stdout, /MD051/)
    writeFileSync(path.join(dir, 'ok.md'), '# Heading\n\n[x](#heading)\n')
    const ok = lintInTmp(dir, 'ok.md')
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

test('MD052 undefined reference link is flagged; a defined one is clean', () => {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '# refs\n\n[text][missing]\n')
    const bad = lintInTmp(dir, 'bad.md')
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    assert.match(bad.stdout, /MD052/)
    writeFileSync(path.join(dir, 'ok.md'), '# refs\n\n[text][ref]\n\n[ref]: http://example.com\n')
    const ok = lintInTmp(dir, 'ok.md')
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

// -- --files-from feeds the file set through a list-file (ARG_MAX safety) --

function lintViaList(listContent) {
  withTmp(dir => {
    writeFileSync(path.join(dir, 'bad.md'), '<!-- tackbox: chars=ascii -->\n# hi\n\nrocket: \u{1F680}\n')
    writeFileSync(path.join(dir, 'files.txt'), listContent)
    const r = spawnSync('node', [WRAPPER, ...mandatoryFlags(dir, 'bad.md'), '--files-from', path.join(dir, 'files.txt')], { cwd: dir, encoding: 'utf8' })
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /declared-chars/)
  })
}

test('--files-from list is linted like positional paths', () => lintViaList('bad.md\n'))

// A list-file written on Windows carries CRLF; a trailing \r on a path is ENOENT.
test('--files-from strips CRLF line endings', () => lintViaList('bad.md\r\n'))
