// Spawn the pinned tackbox wheel and translate its protocol response.

const { spawn } = require('node:child_process')

const PROTOCOL = 1
const SUBCOMMAND = 'hook-protocol'
const TIMEOUT_MS = 20000
const COMMAND_ENV = 'TACKBOX_OMP_COMMAND'

const ALLOW = 'allow'
const ASK = 'ask'
const BLOCK = 'block'
const UNVERIFIED = 'unverified'
const WIRE_WARN = 'warn'
const WIRE_KINDS = new Set([ALLOW, ASK, BLOCK, WIRE_WARN])
const DEFAULT_TIMERS = { set: setTimeout, clear: clearTimeout }

function request(phase, cwd, normalized) {
  const event = {
    protocol: PROTOCOL,
    phase,
    cwd,
    tool: normalized.tool,
    targets: normalized.targets,
    unknown: normalized.unknown,
  }
  if (normalized.targetless !== undefined) event.targetless = normalized.targetless
  if (phase === 'post') event.succeeded = normalized.succeeded
  return event
}

async function decide(event, options) {
  const argv = resolveArgv(options.env, options.version)
  if (argv.error !== null) return unverified(argv.error)
  const run = await execute(argv.value, event, options.timers || DEFAULT_TIMERS)
  if (run.failure !== null) return unverified(run.failure)
  if (run.code !== 0) {
    const detail = firstLine(run.stderr) || `${argv.value[0]} exited with ${run.code}`
    return unverified(detail)
  }
  const decoded = parseJson(run.stdout.trim())
  if (decoded.error !== null) return unverified(`unreadable hook decision (${decoded.error})`)
  return asDecision(decoded.value)
}

function resolveArgv(env, version) {
  const override = env[COMMAND_ENV]
  if (typeof override !== 'string' || override.trim() === '') {
    return { value: ['uvx', `tackbox@${version}`, SUBCOMMAND], error: null }
  }
  const decoded = parseJson(override)
  const argv = decoded.value
  const usable =
    Array.isArray(argv) &&
    argv.length > 0 &&
    argv.every(value => typeof value === 'string' && value.trim() !== '')
  if (!usable) {
    return {
      value: null,
      error: `${COMMAND_ENV} must be a JSON array of argv strings; the tackbox hook did not run`,
    }
  }
  return { value: [...argv, SUBCOMMAND], error: null }
}

function execute(argv, event, timers, spawnChild = spawn) {
  return new Promise(resolve => {
    let child
    // no-report: synchronous spawn failure resolves the protocol failure outcome.
    try {
      child = spawnChild(argv[0], argv.slice(1), {
        cwd: event.cwd,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      })
    } catch (error) {
      resolve({ code: null, stdout: '', stderr: '', failure: `cannot run ${argv[0]}: ${message(error)}` })
      return
    }
    if (!child.stdin || !child.stdout || !child.stderr) {
      resolve({ code: null, stdout: '', stderr: '', failure: `cannot run ${argv[0]}: missing protocol stream` })
      return
    }
    let stdout = ''
    let stderr = ''
    let timer = null
    let settled = false
    let closed = false
    let closeCode = null
    let stdinFinished = false
    const finish = (failure, code) => {
      if (settled) return
      settled = true
      if (timer !== null) timers.clear(timer)
      resolve({ code, stdout, stderr, failure })
    }
    const stop = failure => {
      let detail = failure
      if (!child.kill()) detail += '; child had already exited'
      finish(detail, null)
    }
    const finishClose = () => {
      if (closed && stdinFinished) finish(null, closeCode)
    }
    timer = timers.set(() => {
      stop(`the tackbox hook timed out after ${TIMEOUT_MS / 1000}s`)
    }, TIMEOUT_MS)
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', chunk => {
      stdout += chunk
    })
    child.stdout.on('error', error => {
      stop(`the tackbox hook stdout stream failed: ${message(error)}`)
    })
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', chunk => {
      stderr += chunk
    })
    child.stderr.on('error', error => {
      stop(`the tackbox hook stderr stream failed: ${message(error)}`)
    })
    child.on('error', error => finish(`cannot run ${argv[0]}: ${message(error)}`, null))
    child.on('close', code => {
      closed = true
      closeCode = code
      finishClose()
    })
    child.stdin.on('finish', () => {
      stdinFinished = true
      finishClose()
    })
    child.stdin.on('error', error => {
      stop(`the tackbox hook stdin stream failed: ${message(error)}`)
    })
    // no-report: a synchronous stdin failure has the same unverified outcome as EPIPE.
    try {
      child.stdin.end(JSON.stringify(event))
    } catch (error) {
      stop(`the tackbox hook stdin stream failed: ${message(error)}`)
    }
  })
}

function asDecision(payload) {
  if (!isObject(payload)) return unverified('the hook decision was not a JSON object')
  if (!Number.isInteger(payload.protocol) || payload.protocol !== PROTOCOL) {
    return unverified(
      `the hook decision speaks protocol ${JSON.stringify(payload.protocol)}; this extension speaks ${PROTOCOL}`,
    )
  }
  if (!WIRE_KINDS.has(payload.decision)) {
    return unverified(`unknown hook decision ${JSON.stringify(payload.decision)}`)
  }
  if (typeof payload.reason !== 'string') {
    return unverified('the hook decision carried a non-string reason')
  }
  if (payload.decision === ALLOW && payload.reason !== '') {
    return unverified('the hook allow decision unexpectedly carried a reason')
  }
  if (payload.decision !== ALLOW && payload.reason.trim() === '') {
    return unverified('the hook decision omitted its required reason')
  }
  if (payload.decision === WIRE_WARN) return unverified(payload.reason)
  return { kind: payload.decision, reason: payload.reason }
}

function parseJson(text) {
  // parse-skip: protocol JSON is intentionally parsed only at this boundary.
  // no-report: malformed JSON becomes an explicit unverified outcome.
  try {
    return { value: JSON.parse(text), error: null }
  } catch (error) {
    return { value: null, error: message(error) }
  }
}

function unverified(reason) {
  const text = String(reason)
  return {
    kind: UNVERIFIED,
    reason: text.startsWith('tackbox') ? text : `tackbox: ${text}`,
  }
}

function firstLine(text) {
  for (const line of String(text || '').split('\n')) {
    if (line.trim() !== '') return line.trim()
  }
  return ''
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function message(error) {
  return String((error && error.message) || error)
}

module.exports = {
  ALLOW,
  ASK,
  BLOCK,
  UNVERIFIED,
  COMMAND_ENV,
  TIMEOUT_MS,
  decide,
  execute,
  request,
  resolveArgv,
  asDecision,
  unverified,
}
