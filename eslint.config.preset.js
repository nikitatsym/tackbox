const tackbox = require('./js/eslint-plugin.js')
const tsParser = require('@typescript-eslint/parser')
const svelteParser = require('svelte-eslint-parser')

const base = {
  plugins: { tackbox },
  rules: tackbox.configs.recommended.rules,
  // Inline eslint-disable directives would silently defeat every tackbox rule;
  // closed for consumers who use this flat config directly.
  linterOptions: { noInlineConfig: true },
}

module.exports = [
  { files: ['**/*.{js,mjs,cjs,jsx}'], ...base },
  { files: ['**/*.{ts,tsx}'], languageOptions: { parser: tsParser }, ...base },
  {
    files: ['**/*.svelte'],
    languageOptions: { parser: svelteParser, parserOptions: { parser: tsParser } },
    ...base,
  },
]
