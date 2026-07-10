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
