const { test } = require('node:test')
const { RuleTester } = require('eslint')
const svelteParser = require('svelte-eslint-parser')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

const svelteOpts = { languageOptions: { parser: svelteParser } }

test('no-swallow-catch (Svelte)', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      { code: '<script>\ntry { f() } catch (e) { reportError("connection lost mid-stream", e) }\n</script>', ...svelteOpts },
    ],
    invalid: [
      { code: '<script>\ntry { f() } catch (e) {}\n</script>', ...svelteOpts, errors: [{ messageId: 'swallow' }] },
    ],
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
      { code: '<script>\nreportError("connection lost mid-stream", err, null, "api.lost")\n</script>', ...svelteOpts },
    ],
    invalid: [
      {
        code: '<script>\nreportError(`oops ${x}`, err, null, "api.lost")\n</script>',
        ...svelteOpts,
        errors: [{ messageId: 'msgNotStatic' }],
      },
    ],
  })
})
