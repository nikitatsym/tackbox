// OMP public tool events reduced to strict host-neutral hook protocol targets.

const path = require('node:path')

const TAGGED = /^\[(.+)#[0-9A-Fa-f]{4}\]\s*$/
const ENVELOPE = /^\*\*\* (?:Begin|End) Patch\s*$/
const FILE_SENTINEL = /^\*\*\* (Update|Add|Delete) File:\s*(.+?)\s*$/
const MOVE_SENTINEL = /^\*\*\* Move to:\s*(.+?)\s*$/
const MV_OP = /^MV\s+(.+?)\s*$/
const CUT_OP = /^CUT\b/
const REM_OP = /^REM\s*$/
const PUT_OP = /^PUT\b/
const HUNK = /^@@/
const PILCROW_HEADER = /^\s*\u00b6+(.*)$/u
const SLOPPY_HEADER = /^\[([^\]\n]+)\]\s*$/
const SLOPPY_SECTION = /^\u00a7(\*?)(.*)$/u
const SLOPPY_OPERATION = /^\u00ab/u
const URI_TARGET = /^[a-zA-Z][a-zA-Z0-9+.-]+:\/\//
const CONTAINER_TARGET = /\.(?:tar|tar\.gz|tgz|zip|jar|war|ear|apk|sqlite|sqlite3|db|db3):/i
const MESSAGE_CLIP = 120

function normalize(toolName, input, cwd) {
  const tool = checkedTool(toolName)
  if (tool.failure) return tool
  if (tool.value === null) return null
  const root = checkedRoot(cwd)
  if (root.failure) return root
  if (!isObject(input)) return unknown(tool.value, 'a non-object input')
  if (tool.value === 'bash' || tool.value === 'eval') {
    return normalized(tool.value, [], null, undefined, 'opaque')
  }
  if (tool.value === 'write') return normalizeWrite(tool.value, input, root.value)
  return normalizeEdit(tool.value, input, root.value)
}

function normalizeResult(toolName, details, input, cwd, isError) {
  const tool = checkedTool(toolName)
  if (tool.failure) return tool
  if (tool.value === null) return null
  const root = checkedRoot(cwd)
  if (root.failure) return root
  if (typeof isError !== 'boolean') {
    return failure('OMP supplied a non-boolean tool-result isError flag')
  }
  if (tool.value === 'bash' || tool.value === 'eval') {
    return normalized(tool.value, [], null, !isError, 'opaque')
  }
  if (tool.value === 'write' && isOpaqueWrite(input)) {
    return normalized(tool.value, [], null, !isError, 'opaque')
  }
  if (!isObject(details)) {
    if (isError) return normalized(tool.value, [], null, false, 'failed')
    return failure('OMP supplied no object result details for a successful file mutation')
  }
  const records = detailRecords(details)
  if (records.failure) return records
  // Pinned single-path aggregate errors do not identify which entries landed.
  if (isError && !records.perFile) return normalized(tool.value, [], null, false, 'failed')
  const decoded = []
  for (const record of records.value) {
    const parsed = detailTargets(record, root.value, tool.value)
    if (parsed.failure) return parsed
    if (parsed.failed) {
      if (!isError) return failure('OMP reported a failed per-file result without an aggregate error')
      continue
    }
    decoded.push(parsed)
  }
  let targets = decoded.flatMap(result => result.targets)
  if (decoded.some(result => result.snapshotsPruned)) {
    const fallback = normalize(tool.value, input, root.value)
    if (
      fallback === null ||
      fallback.failure ||
      fallback.unknown !== null ||
      fallback.targetless !== undefined
    ) {
      return failure('OMP pruned result snapshots and the original input cannot be matched safely')
    }
    const unused = new Set(fallback.targets.map((_, index) => index))
    targets = []
    for (const parsed of decoded) {
      if (!parsed.snapshotsPruned) {
        targets.push(...parsed.targets)
        continue
      }
      const merged = mergePrunedTargets(parsed.targets, fallback.targets, unused)
      if (merged.failure) return merged
      targets.push(...merged.value)
    }
    if (!records.perFile && unused.size > 0) {
      return failure('OMP result details omitted one or more mutated input targets')
    }
  }
  if (targets.length === 0) {
    const targetless = isError
      ? 'failed'
      : decoded.every(result => result.targetless === 'no-op')
        ? 'no-op'
        : null
    if (targetless === null) return failure('OMP result details name no landed target')
    return normalized(tool.value, [], null, !isError, targetless)
  }
  return normalized(tool.value, targets, null, !isError)
}

function checkedTool(toolName) {
  if (typeof toolName !== 'string' || toolName.trim() === '') {
    return failure('OMP supplied a malformed toolName')
  }
  if (!['edit', 'apply_patch', 'write', 'bash', 'eval'].includes(toolName)) {
    return { value: null }
  }
  return { value: toolName }
}

function checkedRoot(cwd) {
  if (typeof cwd !== 'string' || cwd === '' || !path.isAbsolute(cwd)) {
    return failure('OMP supplied no absolute session cwd')
  }
  return { value: cwd }
}

function sessionRoot(cwd) {
  const root = checkedRoot(cwd)
  return root.failure ? null : root.value
}

function normalizeWrite(tool, input, cwd) {
  const raw = untag(stringOrNull(input.path))
  if (raw === null) return unknown(tool, 'a write with no string path')
  if (isOpaquePath(raw)) return normalized(tool, [], null, undefined, 'opaque')
  const content = stringOrNull(input.content)
  if (content === null) return unknown(tool, `a write to ${clip(raw)} with no string content`)
  return normalized(tool, [target(path.resolve(cwd, raw), 'write', true, { content })], null)
}

function normalizeEdit(tool, input, cwd) {
  const patchInput = stringOrNull(input.input) || stringOrNull(input._input)
  if (patchInput !== null) return parsePatchText(tool, patchInput, cwd)
  if (typeof input.old_string === 'string' || typeof input.new_string === 'string') {
    return normalizeReplace(tool, input, cwd)
  }
  if (Array.isArray(input.edits)) return normalizeEdits(tool, input, cwd)
  return normalizePublicPaths(tool, input, cwd)
}
function normalizePublicPaths(tool, input, cwd) {
  if (input.paths !== undefined) {
    if (!Array.isArray(input.paths) || input.paths.length === 0) {
      return unknown(tool, 'an OMP paths compatibility field that is not a non-empty array')
    }
    const paths = []
    const seen = new Set()
    for (const value of input.paths) {
      const raw = untag(stringOrNull(value))
      if (raw === null) return unknown(tool, 'an OMP paths compatibility field with a non-string path')
      if (!seen.has(raw)) {
        seen.add(raw)
        paths.push(raw)
      }
    }
    const direct = untag(stringOrNull(input.path))
    if (direct !== null && !seen.has(direct)) {
      return unknown(tool, 'conflicting OMP path and paths compatibility fields')
    }
    return normalized(
      tool,
      paths.map(raw => target(path.resolve(cwd, raw), 'edit', true, { ambiguous: true })),
      null,
    )
  }
  const raw = editPath(input)
  if (raw === null) {
    return unknown(tool, `an ${tool} payload with no input, old_string/new_string, edits, path, or paths field`)
  }
  return normalized(tool, [target(path.resolve(cwd, raw), 'edit', true, { ambiguous: true })], null)
}


function normalizeReplace(tool, input, cwd) {
  const raw = editPath(input)
  if (raw === null) return unknown(tool, 'a replace edit with no string path')
  return normalized(tool, [
    target(path.resolve(cwd, raw), 'edit', true, {
      added: [typeof input.new_string === 'string' ? input.new_string : ''],
      removed: [typeof input.old_string === 'string' ? input.old_string : ''],
    }),
  ], null)
}

function normalizeEdits(tool, input, cwd) {
  const raw = editPath(input)
  if (raw === null) return unknown(tool, 'a patch payload with no string path')
  if (input.edits.length === 0) return unknown(tool, 'a patch payload with an empty edits list')
  const drafts = new Map()
  const source = draftFor(drafts, path.resolve(cwd, raw))
  for (const entry of input.edits) {
    if (!isObject(entry)) return unknown(tool, 'a patch edit that is not an object')
    const op = entry.op === undefined ? 'update' : entry.op
    if (typeof op !== 'string') return unknown(tool, 'a patch edit with a non-string op')
    if (entry.rename !== undefined) {
      const renamed = stringOrNull(entry.rename)
      if (renamed === null) source.ambiguous = true
      else markMove(source, draftFor(drafts, path.resolve(cwd, renamed)))
    }
    const destination = source.operation === 'move' ? moveDestination(drafts, source) : source
    if (op === 'delete') {
      markDelete(source)
      continue
    }
    if (op === 'create') {
      destination.operation = 'write'
      destination.expectedPresent = true
      if (
        input.edits.length === 1 &&
        entry.rename === undefined &&
        typeof entry.diff === 'string' &&
        entry.diff.length > 0
      ) {
        setContent(destination, entry.diff)
      } else {
        destination.ambiguous = true
      }
      continue
    }
    if (op !== 'update' || typeof entry.diff !== 'string' || entry.diff.length === 0) {
      destination.ambiguous = true
      continue
    }
    appendPatchFragments(destination, entry.diff)
  }
  return normalized(tool, finalizeDrafts(drafts), null)
}

function parsePatchText(tool, text, cwd) {
  const drafts = new Map()
  let current = null
  let run = []
  const flush = () => {
    if (run.length > 0 && current !== null) current.added.push(run.join('\n'))
    run = []
  }
  const open = (raw, operation = 'edit') => {
    flush()
    current = draftFor(drafts, path.resolve(cwd, unquote(raw)))
    if (operation === 'write') current.operation = 'write'
    if (operation === 'delete') markDelete(current)
  }
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index++) {
    const rawLine = lines[index]
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (line.startsWith('+')) {
      if (current === null) return unknown(tool, `an added row before any file section: ${clip(line)}`)
      run.push(line.slice(1))
      continue
    }
    flush()
    if (line.trim() === '' || ENVELOPE.test(line) || HUNK.test(line)) continue
    const tagged = TAGGED.exec(line)
    if (tagged !== null) {
      open(tagged[1])
      continue
    }
    const pilcrow = PILCROW_HEADER.exec(line)
    if (pilcrow !== null) {
      const compatPath = pilcrowPath(pilcrow[1])
      if (compatPath === null) return unknown(tool, 'a paragraph-sign header with no path')
      open(compatPath)
      continue
    }
    const sloppy = SLOPPY_SECTION.exec(line)
    if (sloppy !== null) {
      const sloppyPath = sloppy[2].trim()
      if (sloppyPath !== '') {
        open(sloppyPath)
      } else if (current === null) {
        return unknown(tool, 'a sloppy operation before any file section')
      }
      current.ambiguous = true
      continue
    }
    const sloppyHeader = SLOPPY_HEADER.exec(line)
    if (
      sloppyHeader !== null &&
      (current === null || startsSloppyOperation(lines, index + 1))
    ) {
      const sloppyPath = sloppyHeader[1].trim()
      if (sloppyPath === '') return unknown(tool, 'a sloppy [path] header with no path')
      open(sloppyPath)
      continue
    }
    const sentinel = FILE_SENTINEL.exec(line)
    if (sentinel !== null) {
      open(sentinel[2], sentinel[1] === 'Add' ? 'write' : sentinel[1] === 'Delete' ? 'delete' : 'edit')
      continue
    }
    const move = MV_OP.exec(line) || MOVE_SENTINEL.exec(line)
    if (move !== null) {
      if (current === null) return unknown(tool, 'a move operation before any file section')
      const destination = draftFor(drafts, path.resolve(cwd, unquote(move[1])))
      markMove(current, destination)
      current = destination
      continue
    }
    if (line.startsWith('-') || line.startsWith(' ')) {
      if (current === null) return unknown(tool, `a patch row before any file section: ${clip(line)}`)
      continue
    }
    if (REM_OP.test(line)) {
      if (current === null) return unknown(tool, 'a remove operation before any file section')
      markDelete(current)
      continue
    }
    if (CUT_OP.test(line)) {
      if (current === null) return unknown(tool, 'a cut operation before any file section')
      continue
    }
    if (PUT_OP.test(line)) {
      if (current === null) return unknown(tool, 'a put operation before any file section')
      if (!line.endsWith(':')) current.ambiguous = true
      continue
    }
    if (current === null) return unknown(tool, `an unrecognized patch row before any file section: ${clip(line)}`)
    current.ambiguous = true
  }
  flush()
  if (drafts.size === 0) {
    return unknown(tool, 'a patch payload with no [PATH#TAG], paragraph-sign, sloppy, or `*** ... File:` section')
  }
  return normalized(tool, finalizeDrafts(drafts), null)
}

function startsSloppyOperation(lines, from) {
  for (let index = from; index < lines.length; index++) {
    const trimmed = lines[index].trim()
    if (trimmed === '') continue
    return SLOPPY_SECTION.test(trimmed) || SLOPPY_OPERATION.test(trimmed)
  }
  return false
}

function pilcrowPath(value) {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const tag = /#[0-9A-Fa-f]{4}$/.exec(trimmed)
  const raw = tag === null ? trimmed : trimmed.slice(0, tag.index)
  const unquoted = unquote(raw)
  return unquoted === '' ? null : unquoted
}

function detailRecords(details) {
  if (details.perFileResults === undefined) return { value: [details], perFile: false }
  if (!Array.isArray(details.perFileResults) || details.perFileResults.length === 0) {
    return failure('OMP supplied malformed perFileResults')
  }
  const records = []
  for (const result of details.perFileResults) {
    if (!isObject(result)) return failure('OMP supplied a non-object perFileResults entry')
    records.push({
      ...result,
      snapshotsPruned: result.snapshotsPruned === undefined
        ? details.snapshotsPruned
        : result.snapshotsPruned,
    })
  }
  return { value: records, perFile: true }
}

function detailTargets(record, cwd, tool) {
  const valid = validateDetail(record)
  if (valid.failure) return valid
  if (record.isError === true) {
    return { targets: [], snapshotsPruned: false, targetless: null, failed: true }
  }
  const operation = detailOperation(record, tool)
  if (operation.failure) return operation
  if (tool === 'write' && operation.value !== 'write') {
    return failure('OMP write result details carry a non-write operation')
  }
  const paths = detailPaths(record, operation.value, cwd, tool)
  if (paths.failure) return paths
  const targets = paths.value
  const snapshotsPruned = record.snapshotsPruned === true
  const destination = targets.find(item => item.expectedPresent)
  if (destination !== undefined) {
    if (typeof record.newText === 'string') {
      delete destination.added
      delete destination.removed
      delete destination.ambiguous
      destination.content = record.newText
    } else if (!snapshotsPruned) {
      destination.ambiguous = true
    }
  }
  return {
    targets,
    snapshotsPruned,
    targetless: paths.targetless || null,
    failed: false,
  }
}

function validateDetail(record) {
  for (const key of ['path', 'resolvedPath', 'sourcePath', 'op', 'oldText', 'newText', 'diff', 'errorText']) {
    if (record[key] !== undefined && typeof record[key] !== 'string') {
      return failure(`OMP supplied a non-string result details.${key}`)
    }
  }
  if (record.isError !== undefined && typeof record.isError !== 'boolean') {
    return failure('OMP supplied a non-boolean result details.isError')
  }
  if (record.errorText !== undefined && record.isError !== true) {
    return failure('OMP supplied errorText without a failed result record')
  }
  if (record.snapshotsPruned !== undefined && typeof record.snapshotsPruned !== 'boolean') {
    return failure('OMP supplied a non-boolean result details.snapshotsPruned')
  }
  if (
    record.move !== undefined &&
    typeof record.move !== 'boolean' &&
    typeof record.move !== 'string' &&
    !isObject(record.move)
  ) {
    return failure('OMP supplied malformed result details.move')
  }
  return { value: true }
}

function detailOperation(record, tool) {
  const raw = record.op
  const move = hasMove(record)
  if (raw === undefined) return { value: move ? 'move' : tool === 'write' ? 'write' : 'edit' }
  if (['update', 'replace', 'patch', 'edit', 'apply_patch'].includes(raw)) {
    return { value: move ? 'move' : 'edit' }
  }
  if (['create', 'write'].includes(raw)) return { value: 'write' }
  if (raw === 'delete') return { value: 'delete' }
  if (['move', 'rename'].includes(raw)) return { value: 'move' }
  return failure(`OMP supplied an unsupported result details.op ${JSON.stringify(raw)}`)
}

function detailPaths(record, operation, cwd, tool) {
  const source = detailString(record, 'sourcePath') || movePath(record.move, 'source')
  const declaredPath = detailString(record, 'path')
  const resolvedPath = detailString(record, 'resolvedPath')
  const destination = tool === 'write'
    ? resolvedPath || declaredPath || movePath(record.move, 'destination')
    : declaredPath || resolvedPath || movePath(record.move, 'destination')
  if (operation === 'delete') {
    const deleted = source || destination
    if (deleted === null) return failure('OMP delete result details name no source path')
    return { value: [target(path.resolve(cwd, deleted), 'delete', false)] }
  }
  if (operation === 'move') {
    if (source === null || destination === null) {
      return failure('OMP move result details must name both sourcePath and path')
    }
    const sourcePath = path.resolve(cwd, source)
    const destinationPath = path.resolve(cwd, destination)
    const pair = moveId(sourcePath, destinationPath)
    return {
      value: [
        target(sourcePath, 'move', false, { moveId: pair }),
        target(destinationPath, 'move', true, { ambiguous: true, moveId: pair }),
      ],
    }
  }
  const landed = destination || source
  if (landed === null && operation === 'edit') {
    if (isExactNoop(record)) return { value: [], targetless: 'no-op' }
    if (hasPathlessMutation(record)) {
      return failure('OMP supplied mutating pathless edit result details')
    }
    return failure('OMP supplied malformed pathless edit result details')
  }
  if (landed === null) return failure('OMP result details name no landed path')
  return { value: [target(path.resolve(cwd, landed), operation, true, { ambiguous: true })] }
}

function isExactNoop(record) {
  return (
    record.op === 'update' &&
    record.diff === '' &&
    record.oldText === undefined &&
    record.newText === undefined &&
    record.snapshotsPruned !== true &&
    !hasMove(record)
  )
}

function hasPathlessMutation(record) {
  return ['diff', 'oldText', 'newText'].some(
    key => typeof record[key] === 'string' && record[key] !== '',
  )
}

function mergePrunedTargets(actual, intended, unused) {
  const merged = []
  for (const targetFromResult of actual) {
    const match = [...unused].find(index => sameTarget(targetFromResult, intended[index]))
    if (match === undefined) {
      return failure(`OMP pruned snapshots for ${targetFromResult.path}, which input did not match`)
    }
    unused.delete(match)
    const intent = intended[match]
    const result = {
      path: targetFromResult.path,
      op: targetFromResult.op,
      expectedPresent: targetFromResult.expectedPresent,
    }
    if (targetFromResult.moveId !== undefined) result.moveId = targetFromResult.moveId
    if (carriesLandedContent(result)) {
      if (intent.content !== undefined) result.content = intent.content
      else {
        result.added = intent.added || []
        result.removed = intent.removed || []
        if (intent.ambiguous) result.ambiguous = true
      }
    }
    merged.push(result)
  }
  return { value: merged }
}
function carriesLandedContent(value) {
  return value.expectedPresent && value.op !== 'delete'
}

function sameTarget(left, right) {
  return left.path === right.path &&
    left.op === right.op &&
    left.expectedPresent === right.expectedPresent
}

function moveId(source, destination) {
  return JSON.stringify([source, destination])
}


function hasMove(record) {
  return record.move === true || typeof record.move === 'string' || isObject(record.move)
}

function detailString(record, key) {
  return typeof record[key] === 'string' && record[key].trim() !== '' ? record[key] : null
}

function movePath(move, side) {
  if (typeof move === 'string') return side === 'destination' ? move : null
  if (!isObject(move)) return null
  const keys = side === 'source' ? ['sourcePath', 'source', 'from'] : ['path', 'destination', 'to']
  for (const key of keys) {
    if (typeof move[key] === 'string' && move[key].trim() !== '') return move[key]
  }
  return null
}

function target(file, operation, expectedPresent, fields = {}) {
  return { path: file, op: operation, expectedPresent, ...fields }
}

function draftFor(drafts, file) {
  const existing = drafts.get(file)
  if (existing !== undefined) return existing
  const draft = {
    path: file,
    operation: 'edit',
    expectedPresent: true,
    added: [],
    removed: [],
    ambiguous: false,
    moveDestination: null,
    moveId: null,
  }
  drafts.set(file, draft)
  return draft
}

function markDelete(draft) {
  draft.operation = 'delete'
  draft.expectedPresent = false
  draft.added = []
  draft.content = undefined
  draft.ambiguous = false
  draft.moveDestination = null
  draft.moveId = null
}

function markMove(source, destination) {
  const pair = moveId(source.path, destination.path)
  source.operation = 'move'
  source.expectedPresent = false
  source.added = []
  source.content = undefined
  source.ambiguous = false
  source.moveDestination = destination.path
  source.moveId = pair
  destination.operation = 'move'
  destination.expectedPresent = true
  destination.ambiguous = true
  destination.moveId = pair
}

function moveDestination(drafts, source) {
  return source.moveDestination === null ? source : draftFor(drafts, source.moveDestination)
}

function setContent(draft, content) {
  draft.content = content.endsWith('\n') ? content : `${content}\n`
  draft.added = []
  draft.removed = []
  draft.ambiguous = false
}


function finalizeDrafts(drafts) {
  return [...drafts.values()].map(finalizeDraft)
}

function finalizeDraft(draft) {
  const value = target(draft.path, draft.operation, draft.expectedPresent)
  if (draft.operation === 'move' && draft.moveId !== null) value.moveId = draft.moveId
  if (draft.content !== undefined) {
    value.content = draft.content
    return value
  }
  if (draft.operation !== 'delete' && !(draft.operation === 'move' && !draft.expectedPresent)) {
    value.added = draft.added
    value.removed = draft.removed
  }
  if (draft.ambiguous) value.ambiguous = true
  return value
}

function appendPatchFragments(targetDraft, diff) {
  let inHunk = false
  let added = []
  let removed = []
  const flush = () => {
    if (added.length > 0) targetDraft.added.push(added.join('\n'))
    if (removed.length > 0) targetDraft.removed.push(removed.join('\n'))
    added = []
    removed = []
  }
  const lines = diff.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index]
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (line.startsWith('@@')) {
      flush()
      inHunk = true
      continue
    }
    if (line === '' && index === lines.length - 1) continue
    if (!inHunk) {
      targetDraft.ambiguous = true
      continue
    }
    if (line.startsWith('+')) {
      if (removed.length > 0) {
        targetDraft.removed.push(removed.join('\n'))
        removed = []
      }
      added.push(line.slice(1))
      continue
    }
    if (line.startsWith('-')) {
      if (added.length > 0) {
        targetDraft.added.push(added.join('\n'))
        added = []
      }
      removed.push(line.slice(1))
      continue
    }
    if (line.startsWith(' ')) {
      flush()
      continue
    }
    targetDraft.ambiguous = true
  }
  flush()
  if (!inHunk) targetDraft.ambiguous = true
}


function editPath(input) {
  return stringOrNull(input.path) || stringOrNull(input._path) || stringOrNull(input.file_path)
}

function stringOrNull(value) {
  if (typeof value !== 'string' || value.trim() === '') return null
  return value
}

function untag(value) {
  if (value === null) return null
  const tagged = TAGGED.exec(value)
  return tagged === null ? value : tagged[1].trim()
}

function unquote(value) {
  const trimmed = value.trim()
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function normalized(tool, targets, unknown, succeeded, targetless) {
  const value = { tool, targets, unknown }
  if (succeeded !== undefined) value.succeeded = succeeded
  if (targetless !== undefined) value.targetless = targetless
  return value
}

function unknown(tool, detail) {
  return normalized(
    tool,
    [],
    `tackbox cannot classify this ${tool} call (${detail}). Re-issue it in a documented form; dev.py check remains required.`,
  )
}

function failure(reason) {
  return { failure: `tackbox cannot verify this hook event: ${reason}` }
}

function isOpaqueWrite(input) {
  return isObject(input) && isOpaquePath(untag(stringOrNull(input.path)))
}

function isOpaquePath(value) {
  return value !== null && (URI_TARGET.test(value) || CONTAINER_TARGET.test(value))
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function clip(text) {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > MESSAGE_CLIP ? `${flat.slice(0, MESSAGE_CLIP)}...` : flat
}

module.exports = { normalize, normalizeResult, sessionRoot }
