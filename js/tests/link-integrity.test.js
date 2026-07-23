const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const { mkdtempSync, writeFileSync, mkdirSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')

const WRAPPER = path.resolve(__dirname, '..', '..', 'bin', 'tackbox-mdlint.js')

// Cyrillic building blocks as \u escapes so this source stays ASCII. slug()
// lowercases, so a heading "Privet" anchors as its lowercase form.
const PRIVET = '\u{41F}\u{440}\u{438}\u{432}\u{435}\u{442}'
const PRIVET_LOWER = '\u{43F}\u{440}\u{438}\u{432}\u{435}\u{442}'

function withRepo(fn) {
  const dir = mkdtempSync(path.join(tmpdir(), 'tackbox-mdlink-'))
  try { return fn(dir) } finally { rmSync(dir, { recursive: true, force: true }) }
}

// Write files (name -> content) and an inventory of [kind, path] pairs, then run
// the wrapper (cwd defaults to the repo root) with the mandatory flags.
function lint(dir, files, inventory, opts = {}) {
  for (const [name, content] of Object.entries(files)) {
    const full = path.join(dir, name)
    mkdirSync(path.dirname(full), { recursive: true })
    writeFileSync(full, content)
  }
  const inv = (inventory || []).map(([k, p]) => k + '\t' + p).join('\n') + '\n'
  const invPath = path.join(dir, '_targets.txt')
  writeFileSync(invPath, inv)
  const args = [WRAPPER, '--repo-root', dir, '--link-targets-from', invPath, ...(opts.args || Object.keys(files).filter(f => f.endsWith('.md')))]
  return spawnSync('node', args, { cwd: opts.cwd || dir, encoding: 'utf8' })
}

// -- existence against the inventory ------------------------------------------

test('broken relative path is a finding', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](missing.md)\n' }, [['F', 'a.md']])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /MD-LINK/)
    assert.match(r.stdout, /does not exist/)
  })
})

test('valid relative pair is clean', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](b.md)\n', 'b.md': '# b\n' }, [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
    assert.equal(r.stdout, '')
  })
})

test('target not in the inventory is a finding even if it is on disk', () => {
  // The CLI omits a gitignored file from the inventory, so a link to it is broken
  // in a clean clone; here the file exists on disk but is absent from F.
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](ignored.md)\n', 'ignored.md': '# i\n' }, [['F', 'a.md']])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /does not exist/)
  })
})

test('target present in the inventory is clean', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](untracked.md)\n', 'untracked.md': '# u\n' },
      [['F', 'a.md'], ['F', 'untracked.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('a ../ target escaping the repo root is a finding', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](../outside.md)\n' }, [['F', 'a.md']])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /escapes the repository root/)
  })
})

// -- fragments ---------------------------------------------------------------

test('existing target with a broken fragment is a finding', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](b.md#no-such)\n', 'b.md': '# Real Heading\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /fragment not found/)
  })
})

test('punctuation heading: slug drops the punctuation', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](b.md#step-1-go)\n', 'b.md': '# Step 1: Go!\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('unicode heading anchors under its lowercase slug', () => {
  withRepo(dir => {
    const r = lint(dir,
      { 'a.md': '# a\n\n[x](b.md#' + PRIVET_LOWER + ')\n', 'b.md': '# ' + PRIVET + '\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('duplicate headings get the -1 suffix', () => {
  withRepo(dir => {
    const b = '# Dup\n\ntext\n\n# Dup\n'
    const ok = lint(dir, { 'a.md': '# a\n\n[x](b.md#dup-1)\n', 'b.md': b }, [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
    const bad = lint(dir, { 'a.md': '# a\n\n[x](b.md#dup-2)\n', 'b.md': b }, [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
  })
})

test('HTML id= and <a name=> anchors are valid targets', () => {
  withRepo(dir => {
    const b = '# b\n\n<a name="legacy"></a>\n\n<div id="marker"></div>\n'
    const r = lint(dir,
      { 'a.md': '# a\n\n[x](b.md#legacy)\n[y](b.md#marker)\n', 'b.md': b },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('#top is always a valid fragment', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](b.md#top)\n', 'b.md': 'no headings here\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('percent-encoded path is decoded before the match', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](a%20b.md)\n', 'a b.md': '# spaced\n' },
      [['F', 'a.md'], ['F', 'a b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('query before the fragment is dropped', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](b.md?v=1#real)\n', 'b.md': '# Real\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

// -- reference links, images -------------------------------------------------

test('reference-style link resolves through its definition', () => {
  withRepo(dir => {
    const bad = lint(dir, { 'a.md': '# a\n\n[x][ref]\n\n[ref]: missing.md\n' }, [['F', 'a.md']])
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    const ok = lint(dir, { 'a.md': '# a\n\n[x][ref]\n\n[ref]: b.md\n', 'b.md': '# b\n' },
      [['F', 'a.md'], ['F', 'b.md']])
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

test('image target existence is checked', () => {
  withRepo(dir => {
    const bad = lint(dir, { 'a.md': '# a\n\n![alt](gone.png)\n' }, [['F', 'a.md']])
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
    const ok = lint(dir, { 'a.md': '# a\n\n![alt](pic.png)\n' }, [['F', 'a.md'], ['F', 'pic.png']])
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
  })
})

// -- schemes, directories, symlinks, submodules ------------------------------

test('URI-scheme targets (tel:, data:, http:) are not checked', () => {
  withRepo(dir => {
    const md = '# a\n\n[t](tel:+123)\n[d](data:text/plain,hi)\n[h](http://example.com/x)\n'
    const r = lint(dir, { 'a.md': md }, [['F', 'a.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('directory link is valid when the prefix holds an entry, broken when empty', () => {
  withRepo(dir => {
    const ok = lint(dir, { 'a.md': '# a\n\n[d](sub)\n' }, [['F', 'a.md'], ['F', 'sub/x.md']])
    assert.equal(ok.status, 0, ok.stdout + ok.stderr)
    const trailing = lint(dir, { 'a.md': '# a\n\n[d](sub/)\n' }, [['F', 'a.md'], ['F', 'sub/x.md']])
    assert.equal(trailing.status, 0, trailing.stdout + trailing.stderr)
    const bad = lint(dir, { 'a.md': '# a\n\n[d](empty)\n' }, [['F', 'a.md'], ['F', 'sub/x.md']])
    assert.equal(bad.status, 1, bad.stdout + bad.stderr)
  })
})

test('tracked symlink target is clean and its fragment is never checked', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](link.md#anything)\n' }, [['F', 'a.md'], ['L', 'link.md']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

test('target under a submodule (gitlink) is skipped', () => {
  withRepo(dir => {
    const r = lint(dir, { 'a.md': '# a\n\n[x](vendor/sub/doc.md)\n' }, [['F', 'a.md'], ['G', 'vendor/sub']])
    assert.equal(r.status, 0, r.stdout + r.stderr)
  })
})

// -- the mandatory-flag bin contract -----------------------------------------

test('missing --link-targets-from is a usage error (exit 2)', () => {
  withRepo(dir => {
    writeFileSync(path.join(dir, 'a.md'), '# a\n')
    const r = spawnSync('node', [WRAPPER, '--repo-root', dir, 'a.md'], { cwd: dir, encoding: 'utf8' })
    assert.equal(r.status, 2, r.stdout + r.stderr)
    assert.match(r.stderr, /required/)
  })
})

test('missing --repo-root is a usage error (exit 2)', () => {
  withRepo(dir => {
    writeFileSync(path.join(dir, 'a.md'), '# a\n')
    writeFileSync(path.join(dir, 't.txt'), 'F\ta.md\n')
    const r = spawnSync('node', [WRAPPER, '--link-targets-from', path.join(dir, 't.txt'), 'a.md'],
      { cwd: dir, encoding: 'utf8' })
    assert.equal(r.status, 2, r.stdout + r.stderr)
    assert.match(r.stderr, /required/)
  })
})

test('called from a subdirectory with correct flags: findings carry repo-relative paths', () => {
  withRepo(dir => {
    mkdirSync(path.join(dir, 'docs'), { recursive: true })
    writeFileSync(path.join(dir, 'docs', 'a.md'), '# a\n\n[x](missing.md)\n')
    writeFileSync(path.join(dir, '_targets.txt'), 'F\tdocs/a.md\n')
    // cwd is the subdirectory; the linted path is given relative to it.
    const r = spawnSync('node',
      [WRAPPER, '--repo-root', dir, '--link-targets-from', path.join(dir, '_targets.txt'), 'a.md'],
      { cwd: path.join(dir, 'docs'), encoding: 'utf8' })
    assert.equal(r.status, 1, r.stdout + r.stderr)
    assert.match(r.stdout, /does not exist: docs\/missing\.md/)
  })
})
