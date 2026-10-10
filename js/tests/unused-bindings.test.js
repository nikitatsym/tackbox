const { test } = require('node:test')
const assert = require('node:assert/strict')
const { ESLint } = require('eslint')
const path = require('node:path')

const eslint = new ESLint({
  overrideConfigFile: path.resolve(__dirname, '../../eslint.config.preset.js'),
})

async function findings(code, filePath) {
  const [result] = await eslint.lintText(code, { filePath })
  return result.messages.map(({ ruleId, message, line }) => ({ ruleId, message, line }))
}

function assertUnusedLines(messages, lines) {
  assert.equal(messages.length, lines.length)
  assert.deepEqual(new Set(messages.map(m => `${m.ruleId}:${m.line}`)), new Set(
    lines.map(line => `@typescript-eslint/no-unused-vars:${line}`),
  ))
}

test('unused bindings cover imports, locals, trailing arguments and caught errors', async () => {
  const source = `
import { missing } from './dep.js'
export function process(first, last) {
  const abandoned = 1
  try { work() } catch (caught) { throw new Error('failure', { cause: null }) }
  return first
}
`
  for (const extension of ['js', 'ts']) {
    const messages = await findings(source, `fixture.${extension}`)
    const unused = messages.filter(m => m.ruleId === '@typescript-eslint/no-unused-vars')
    assert.equal(unused.length, 4)
    for (const name of ['missing', 'last', 'abandoned', 'caught']) {
      assert.ok(unused.some(m => m.message.includes(`'${name}'`)), name)
    }
  }
})

test('underscore bindings and positional arguments before the last used argument stay clean', async () => {
  const messages = await findings(`
import './side-effect.js'
export function process(first, last, _trailing) {
  for (const _ of [1, 2]) { work() }
  const [, value] = [1, 2]
  const { omitted, ...rest } = { omitted: 1, retained: 2 }
  try { work() } catch { throw new Error('failure', { cause: null }) }
  return last + value + rest.retained
}
`, 'fixture.ts')
  assert.deepEqual(messages.filter(m => m.ruleId === '@typescript-eslint/no-unused-vars'), [])
})

test('Svelte markup references are uses but unused script bindings remain findings', async () => {
  for (const script of ['<script>', '<script lang="ts">']) {
    const messages = await findings(`${script}
const visible = 'shown'
const hidden = 'unused'
const [, selected] = ['intentional', 'shown']
</script>
<p>{visible}{selected}</p>`, 'fixture.svelte')
    assert.equal(messages.length, 1, JSON.stringify(messages))
    assert.equal(messages[0].ruleId, '@typescript-eslint/no-unused-vars')
    assert.ok(messages[0].message.includes("'hidden'"))
  }
})

test('private class members must be removed even with underscore names', async () => {
  const messages = await findings(`export class Example {
  #field = 1
  #_reserved() { return 2 }
  #used = 3
  read() { return this.#used }
}`, 'fixture.js')
  assert.equal(messages.length, 2)
  assert.deepEqual(new Set(messages.map(m => `${m.ruleId}:${m.line}`)), new Set([
    'no-unused-private-class-members:2', 'no-unused-private-class-members:3',
  ]))
})

test('used CommonJS values are not type-only references', async () => {
  assert.deepEqual(await findings(`const fs = require('node:fs')
const value = 1
module.exports = () => fs.existsSync(String(value))`, 'fixture.js'), [])
})

test('markers and ESLint directives do not suppress unused bindings', async () => {
  const messages = await findings(`// no-report: intentional binding needs no approval
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const abandoned = 1`, 'fixture.js')
  assert.ok(messages.some(m => m.ruleId === '@typescript-eslint/no-unused-vars'))
})

test('unused TypeScript declarations are findings', async () => {
  const messages = await findings(`type _Abandoned = number
interface _DeadShape { value: string }
enum _UnusedChoice { First }
type Used = string
export function identity(value: Used): Used { return value }`, 'fixture.ts')
  assertUnusedLines(messages, [1, 2, 3])
})

test('underscore prefixes do not rescue dead module declarations or imports', async () => {
  const source = `import { missing as _missing } from './dep.js'
function _dead() {}
const _x = 1`
  for (const extension of ['js', 'ts']) {
    const messages = await findings(source, `fixture.${extension}`)
    assertUnusedLines(messages, [1, 2, 3])
  }
})

test('JSX parses in JavaScript without the unused ecmaFeatures option', async () => {
  for (const extension of ['js', 'jsx', 'tsx']) {
    assert.deepEqual(await findings('export const view = <div />', `fixture.${extension}`), [])
  }
})

test('bare underscore declarations cannot escape unused checks', async () => {
  for (const declaration of [
    'function _() {}', 'class _ {}', 'const _ = compute()',
    'type _ = number', 'interface _ {}', 'enum _ { First }',
  ]) {
    const messages = await findings(declaration, 'fixture.ts')
    assert.ok(messages.some(m => m.ruleId === 'no-restricted-syntax' && m.line === 1), declaration)
  }
})

test('bare underscore loop heads stay allowed but ordinary for-loop declarations do not', async () => {
  assert.deepEqual(await findings(`for (const _ of items) { work() }
for (const _ in object) { work() }`, 'fixture.js'), [])
  const messages = await findings('for (let _ = 0; _ < 3; _++) { work() }', 'fixture.js')
  assert.ok(messages.some(m => m.ruleId === 'no-restricted-syntax'))
})

test('optional catch and array bindings have no underscore escape', async () => {
  for (const extension of ['js', 'ts']) {
    const messages = await findings(`try { work() } catch (_err) { throw new Error('failed', { cause: null }) }
const [_dead] = xs`, `fixture.${extension}`)
    assertUnusedLines(messages.filter(m => m.ruleId === '@typescript-eslint/no-unused-vars'), [1, 2])
    assert.deepEqual(await findings(`try { work() } catch { throw new Error('failed') }
const [, value] = xs
consume(value)`, `fixture.${extension}`), [])
  }
})
