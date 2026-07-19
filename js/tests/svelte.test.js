const { test } = require('node:test')
const { RuleTester } = require('eslint')
const svelteParser = require('svelte-eslint-parser')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

const svelteOpts = { languageOptions: { parser: svelteParser } }
const IMP = "import { reportError } from 'tackbox/report'\n"

test('no-swallow-catch (Svelte)', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      { code: '<script>\n' + IMP + 'try { f() } catch (e) { reportError("connection lost mid-stream", e) }\n</script>', ...svelteOpts },
    ],
    invalid: [
      { code: '<script>\ntry { f() } catch (e) {}\n</script>', ...svelteOpts, errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// Svelte 5.3+ syntax the parser must handle; 0.43.x died here with
// "Unknown type: SvelteBoundary" on any rule run.
test('svelte:boundary parses (Svelte 5)', () => {
  ruleTester.run('no-console-error', require('../rules/no-console-error'), {
    valid: [
      {
        code: '<svelte:boundary onerror={(e) => f(e)}>\n<p>{x}</p>\n{#snippet failed(error)}<p>{error}</p>{/snippet}\n</svelte:boundary>',
        ...svelteOpts,
      },
    ],
    invalid: [],
  })
})

test('no-console-error (Svelte)', () => {
  ruleTester.run('no-console-error', require('../rules/no-console-error'), {
    valid: [
      { code: '<script>\nconsole.log("hi")\n</script>', ...svelteOpts },
    ],
    invalid: [
      { code: '<script>\nconsole.error("boom")\n</script>', ...svelteOpts, errors: [{ messageId: 'use' }] },
    ],
  })
})

test('valid-error-report (Svelte)', () => {
  ruleTester.run('valid-error-report', require('../rules/valid-error-report'), {
    valid: [
      { code: '<script>\n' + IMP + 'reportError("connection lost mid-stream", err, null, "api.lost")\n</script>', ...svelteOpts },
    ],
    invalid: [
      {
        code: '<script>\n' + IMP + 'reportError(`oops ${x}`, err, null, "api.lost")\n</script>',
        ...svelteOpts,
        errors: [{ messageId: 'msgNotStatic' }],
      },
    ],
  })
})

// Svelte template markers (D011 residual A8). An HTML `<!-- ... -->`
// comment carrying a no-report marker, placed immediately above an element,
// suppresses a marker-honoring rule's finding anywhere inside that element -
// wider than the line-adjacent `//` form on purpose, since an inline handler
// can span lines. Recognition lives in _shared hasMarkerAbove and is shared by
// all six marker rules; exercised below through no-swallow-catch (the natural
// template finding: an async inline handler) plus one no-swallow-promise-catch
// case proving it is shared, not per-rule.

test('svelte html-comment marker suppresses within the element', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      // adjacency: marker directly above a single-line element.
      {
        code: '<!-- no-report: intentionally quiet inline handler -->\n<button onclick={() => { try { f() } catch (e) {} }}>go</button>',
        ...svelteOpts,
      },
      // A8: multi-line element, the catch sits on a later line - element-wide
      // coverage, not line adjacency to the marker.
      {
        code: '<!-- no-report: handler spans several lines here -->\n<button\n  onclick={() => {\n    try { f() } catch (e) {}\n  }}\n>go</button>',
        ...svelteOpts,
      },
      // coverage reaches a descendant: marker above the block covers a handler
      // in a nested element.
      {
        code: '<!-- no-report: covers every handler in this block -->\n<div>\n  <button onclick={() => { try { f() } catch (e) {} }}>go</button>\n</div>',
        ...svelteOpts,
      },
    ],
    invalid: [],
  })
})

// Adversarial: the marker sits above the first element only. Planting a swallow
// in the following sibling proves coverage stops at the element boundary - the
// second button still fires, and exactly once (the first stays suppressed, or
// there would be two).
test('svelte html-comment marker does not reach the following sibling', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [],
    invalid: [
      {
        code: '<!-- no-report: only the first button is quiet on purpose -->\n<button onclick={() => { try { f() } catch (e) {} }}>first</button>\n<button onclick={() => { try { g() } catch (e) {} }}>second</button>',
        ...svelteOpts,
        errors: [{ messageId: 'swallow', line: 3 }],
      },
      // baseline without the marker: both siblings fire - shows the single error
      // above is the marker suppressing exactly the first.
      {
        code: '<button onclick={() => { try { f() } catch (e) {} }}>first</button>\n<button onclick={() => { try { g() } catch (e) {} }}>second</button>',
        ...svelteOpts,
        errors: [{ messageId: 'swallow', line: 1 }, { messageId: 'swallow', line: 2 }],
      },
    ],
  })
})

// A `/* */` block comment is never a marker (only `//` and `<!-- -->` are).
test('svelte block comment is not a marker', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [],
    invalid: [
      {
        code: '<script>\n/* no-report: block comments must not suppress a finding */\ntry { f() } catch (e) {}\n</script>',
        ...svelteOpts,
        errors: [{ messageId: 'swallow' }],
      },
    ],
  })
})

// The in-expression `//` form (a Line comment inside a mustache expression)
// keeps working - line-adjacent to the try, as in plain JS.
test('svelte in-expression // marker still works', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      {
        code: '<button onclick={() => {\n  // no-report: inline try kept quiet for a documented reason\n  try { f() } catch (e) {}\n}}>go</button>',
        ...svelteOpts,
      },
    ],
    invalid: [
      // same handler without the marker is a finding.
      {
        code: '<button onclick={() => {\n  try { f() } catch (e) {}\n}}>go</button>',
        ...svelteOpts,
        errors: [{ messageId: 'swallow' }],
      },
    ],
  })
})

// Non-Svelte files are byte-for-byte unaffected: the plain-JS `//` marker still
// suppresses and a `/* */` block comment still does not (default parser, no
// SvelteElement ancestors, no SvelteHTMLComment nodes).
test('non-svelte marker recognition unaffected', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      '// no-report: plain module, marker recognized as before\ntry { f() } catch (e) {}',
    ],
    invalid: [
      {
        code: '/* no-report: block comment is not a marker in plain JS either */\ntry { f() } catch (e) {}',
        errors: [{ messageId: 'swallow' }],
      },
    ],
  })
})

// The recognition lives in _shared, so it covers the other marker rules too -
// here no-swallow-promise-catch, whose finding is a swallowed rejection handler
// in an inline handler.
test('svelte html-comment marker covers other marker rules', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      {
        code: '<!-- no-report: fire-and-forget refresh, failures are non-fatal -->\n<button onclick={() => { doThing().catch(() => {}) }}>go</button>',
        ...svelteOpts,
      },
    ],
    invalid: [
      {
        code: '<button onclick={() => { doThing().catch(() => {}) }}>go</button>',
        ...svelteOpts,
        errors: [{ messageId: 'swallow' }],
      },
    ],
  })
})
