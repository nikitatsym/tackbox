const tackbox = require('./js/eslint-plugin.js')
const tsParser = require('@typescript-eslint/parser')
const svelteParser = require('svelte-eslint-parser')

const base = {
  plugins: { tackbox },
  rules: tackbox.configs.recommended.rules,
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
