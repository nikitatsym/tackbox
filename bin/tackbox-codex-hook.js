#!/usr/bin/env node

'use strict'

function fatal(error) {
  process.exitCode = 2
  process.stderr.write(`tackbox Codex hook: ${String((error && error.stack) || error)}\n`)
}

process.once('uncaughtException', fatal)
process.once('unhandledRejection', fatal)
require('../js/codex/hook').main()
