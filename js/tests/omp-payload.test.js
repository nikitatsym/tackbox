const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const { normalize, normalizeResult, sessionRoot } = require('../omp/payload')

const CWD = path.resolve(path.sep === '\\' ? 'C:/repo' : '/repo')

function abs(rel) {
  return path.resolve(CWD, rel)
}

function target(rel, op, expectedPresent, fields = {}) {
  return { path: abs(rel), op, expectedPresent, ...fields }
}
function moveId(source, destination) {
  return JSON.stringify([abs(source), abs(destination)])
}


function only(result) {
  assert.equal(result.failure, undefined)
  assert.equal(result.unknown, null)
  assert.equal(result.targets.length, 1)
  return result.targets[0]
}

test('hashline body rows map to one strict edit target', () => {
  const result = normalize('edit', { input: '[a.js#ABCD]\nPUT 1.=1:\n+const b = 2\n' }, CWD)
  assert.deepEqual(only(result), target('a.js', 'edit', true, {
    added: ['const b = 2'],
    removed: [],
  }))
})

test('hashline keeps literal leading plus and merges repeated sections', () => {
  const result = normalize('edit', {
    input: '[a.js#ABCD]\nPUT 1.=1:\n++value\n[a.js#1234]\nPUT 2.=2:\n+next\n',
  }, CWD)
  assert.deepEqual(only(result), target('a.js', 'edit', true, {
    added: ['+value', 'next'],
    removed: [],
  }))
})

test('hashline cut remains an expected-present removal-only mutation', () => {
  const result = normalize('edit', { input: '[a.js#ABCD]\nCUT 1.=2\n' }, CWD)
  assert.deepEqual(only(result), target('a.js', 'edit', true, { added: [], removed: [] }))
})

test('hashline REM declares an expected-absent delete', () => {
  const result = normalize('edit', { input: '[a.js#ABCD]\nREM\n' }, CWD)
  assert.deepEqual(only(result), target('a.js', 'delete', false))
})

test('hashline MV exposes source and destination move targets', () => {
  const result = normalize('edit', { input: '[dev.py#ABCD]\nMV tools/dev.py\n' }, CWD)
  const pair = moveId('dev.py', 'tools/dev.py')
  assert.deepEqual(result.targets, [
    target('dev.py', 'move', false, { moveId: pair }),
    target('tools/dev.py', 'move', true, { added: [], removed: [], ambiguous: true, moveId: pair }),
  ])
})

test('apply_patch update, add, delete, and move sentinels keep their operations', () => {
  const update = normalize('apply_patch', {
    input: '*** Begin Patch\n*** Update File: a.js\n@@\n-old\n+new\n*** End Patch\n',
  }, CWD)
  assert.deepEqual(only(update), target('a.js', 'edit', true, {
    added: ['new'],
    removed: [],
  }))
  const add = normalize('apply_patch', {
    input: '*** Begin Patch\n*** Add File: b.js\n+const b = 1\n*** End Patch\n',
  }, CWD)
  assert.deepEqual(only(add), target('b.js', 'write', true, {
    added: ['const b = 1'],
    removed: [],
  }))
  const deleted = normalize('apply_patch', {
    input: '*** Begin Patch\n*** Delete File: c.js\n*** End Patch\n',
  }, CWD)
  assert.deepEqual(only(deleted), target('c.js', 'delete', false))
  const moved = normalize('apply_patch', {
    input: '*** Begin Patch\n*** Update File: from.js\n*** Move to: to.js\n*** End Patch\n',
  }, CWD)
  const pair = moveId('from.js', 'to.js')
  assert.deepEqual(moved.targets, [
    target('from.js', 'move', false, { moveId: pair }),
    target('to.js', 'move', true, { added: [], removed: [], ambiguous: true, moveId: pair }),
  ])
})

test('pinned paragraph-sign headers and derived public paths map conservatively', () => {
  const result = normalize('edit', {
    input: '\u00b6one.js#ABCD\nunparsed one\n\u00b6\u00b6two.js#BCDE\nunparsed two\n',
    path: 'one.js',
    paths: ['one.js', 'two.js'],
  }, CWD)
  assert.deepEqual(result.targets, [
    target('one.js', 'edit', true, { added: [], removed: [], ambiguous: true }),
    target('two.js', 'edit', true, { added: [], removed: [], ambiguous: true }),
  ])
  const fallback = normalize('edit', { path: 'one.js', paths: ['one.js', 'two.js'] }, CWD)
  assert.deepEqual(fallback.targets, [
    target('one.js', 'edit', true, { ambiguous: true }),
    target('two.js', 'edit', true, { ambiguous: true }),
  ])
  const malformed = normalize('edit', { input: 'MV b.js\n\u00b6a.js#ABCD\n' }, CWD)
  assert.match(malformed.unknown, /move operation before any file section/)
})
test('pinned OMP 18 sloppy section headers map every named file conservatively', () => {
  const selection = '\u27ea1000\u25025000\u27eb'
  const bracketed = normalize('edit', {
    input: [
      '[src/first.ts]',
      '\u00a7',
      `const timeout = ${selection};`,
      '[src/second.ts]',
      '\u00a7*',
      `const retries = ${selection};`,
    ].join('\n'),
  }, CWD)
  assert.deepEqual(bracketed.targets, [
    target('src/first.ts', 'edit', true, { added: [], removed: [], ambiguous: true }),
    target('src/second.ts', 'edit', true, { added: [], removed: [], ambiguous: true }),
  ])

  const direct = normalize('edit', {
    input: [
      '\u00a7src/config.ts',
      `const timeout = ${selection};`,
      '\u00a7',
      `const retries = ${selection};`,
      '\u00a7*src/catalog.ts',
      `logger.${selection}(`,
    ].join('\n'),
  }, CWD)
  assert.deepEqual(direct.targets, [
    target('src/config.ts', 'edit', true, { added: [], removed: [], ambiguous: true }),
    target('src/catalog.ts', 'edit', true, { added: [], removed: [], ambiguous: true }),
  ])
})



test('replace compatibility paths preserve both change sides', () => {
  for (const key of ['path', '_path', 'file_path']) {
    const result = normalize('edit', { [key]: 'a.js', old_string: 'old', new_string: 'new' }, CWD)
    assert.deepEqual(only(result), target('a.js', 'edit', true, {
      added: ['new'],
      removed: ['old'],
    }))
  }
})

test('patch update, create, delete, and rename variants map operations', () => {
  const update = normalize('edit', {
    path: 'a.js',
    edits: [{ op: 'update', diff: '@@\n-old\n+new\n' }],
  }, CWD)
  assert.deepEqual(only(update), target('a.js', 'edit', true, {
    added: ['new'],
    removed: ['old'],
  }))
  const created = normalize('edit', {
    path: 'b.js',
    edits: [{ op: 'create', diff: 'const b = 1\n' }],
  }, CWD)
  assert.deepEqual(only(created), target('b.js', 'write', true, { content: 'const b = 1\n' }))
  const literalPlus = normalize('edit', {
    path: 'literal.js',
    edits: [{ op: 'create', diff: '++first\n+second\n' }],
  }, CWD)
  assert.deepEqual(only(literalPlus), target('literal.js', 'write', true, {
    content: '++first\n+second\n',
  }))
  const deleted = normalize('edit', { path: 'c.js', edits: [{ op: 'delete' }] }, CWD)
  assert.deepEqual(only(deleted), target('c.js', 'delete', false))
  const renamed = normalize('edit', {
    path: 'from.js',
    edits: [{ op: 'update', rename: 'to.js', diff: '@@\n-old\n+new\n' }],
  }, CWD)
  const pair = moveId('from.js', 'to.js')
  assert.deepEqual(renamed.targets, [
    target('from.js', 'move', false, { moveId: pair }),
    target('to.js', 'move', true, {
      added: ['new'], removed: ['old'], ambiguous: true, moveId: pair,
    }),
  ])
})

test('write, tagged write, opaque write, bash, and eval preserve channel semantics', () => {
  assert.deepEqual(only(normalize('write', { path: 'a.js', content: 'x\n' }, CWD)), target('a.js', 'write', true, { content: 'x\n' }))
  assert.equal(only(normalize('write', { path: '[a.js#ABCD]', content: 'x\n' }, CWD)).path, abs('a.js'))
  assert.deepEqual(normalize('write', { path: 'xd://lsp', content: '{}' }, CWD), {
    tool: 'write', targets: [], unknown: null, targetless: 'opaque',
  })
  assert.deepEqual(normalize('bash', { command: 'true' }, CWD), {
    tool: 'bash', targets: [], unknown: null, targetless: 'opaque',
  })
  assert.deepEqual(normalize('eval', { code: 'x = 1' }, CWD), {
    tool: 'eval', targets: [], unknown: null, targetless: 'opaque',
  })
  assert.deepEqual(normalizeResult('eval', undefined, {}, CWD, false), {
    tool: 'eval', targets: [], unknown: null, succeeded: true, targetless: 'opaque',
  })
})

test('unknown payloads, malformed tool names, and missing absolute cwd fail safely', () => {
  const unknown = normalize('edit', {}, CWD)
  assert.equal(unknown.targets.length, 0)
  assert.match(unknown.unknown, /cannot classify/)
  assert.match(normalize('', {}, CWD).failure, /malformed toolName/)
  assert.match(normalize('write', { path: 'a.js', content: 'x' }, '').failure, /absolute session cwd/)
  assert.equal(sessionRoot(''), null)
})

test('result details win over contradictory input and retain landed newText', () => {
  const result = normalizeResult(
    'edit',
    {
      path: 'actual.js',
      sourcePath: 'actual.js',
      op: 'update',
      move: false,
      diff: '@@\n-old\n+new\n',
      oldText: 'old',
      newText: 'const actual = true\n',
      snapshotsPruned: false,
    },
    { path: 'input.js', old_string: 'old', new_string: 'wrong' },
    CWD,
    false,
  )
  assert.deepEqual(result, {
    tool: 'edit',
    targets: [target('actual.js', 'edit', true, { content: 'const actual = true\n' })],
    unknown: null,
    succeeded: true,
  })
})
test('write result details use OMP resolvedPath rather than input path', () => {
  const result = normalizeResult(
    'write',
    {
      resolvedPath: abs('actual.js'),
      madeExecutable: false,
      diagnostics: { messages: [] },
      meta: {},
    },
    { path: 'input.js', content: 'wrong\n' },
    CWD,
    false,
  )
  assert.deepEqual(result, {
    tool: 'write',
    targets: [target('actual.js', 'write', true, { ambiguous: true })],
    unknown: null,
    succeeded: true,
  })
})


test('result details map delete and move from authoritative paths', () => {
  const deleted = normalizeResult(
    'edit',
    { path: 'gone.js', sourcePath: 'gone.js', op: 'delete', snapshotsPruned: false },
    {},
    CWD,
    false,
  )
  assert.deepEqual(deleted.targets, [target('gone.js', 'delete', false)])
  const moved = normalizeResult(
    'edit',
    { path: 'to.js', sourcePath: 'from.js', op: 'move', move: true, snapshotsPruned: false },
    {},
    CWD,
    false,
  )
  const pair = moveId('from.js', 'to.js')
  assert.deepEqual(moved.targets, [
    target('from.js', 'move', false, { moveId: pair }),
    target('to.js', 'move', true, { ambiguous: true, moveId: pair }),
  ])
})

test('perFileResults pins the OMP public multi-file compatibility fields', () => {
  const result = normalizeResult(
    'edit',
    {
      path: 'unused.js',
      sourcePath: 'unused.js',
      op: 'update',
      move: false,
      diff: '@@\n-old\n+new\n',
      oldText: 'old',
      newText: 'unused\n',
      snapshotsPruned: false,
      perFileResults: [
        {
          path: 'one.js', sourcePath: 'one.js', op: 'update', move: false,
          diff: '@@\n-old\n+one\n', oldText: 'old', newText: 'one\n', snapshotsPruned: false,
        },
        {
          path: 'two.js', sourcePath: 'two.js', op: 'update', move: false,
          diff: '@@\n-old\n+two\n', oldText: 'old', newText: 'two\n', snapshotsPruned: false,
        },
      ],
    },
    {},
    CWD,
    false,
  )
  assert.deepEqual(result.targets, [
    target('one.js', 'edit', true, { content: 'one\n' }),
    target('two.js', 'edit', true, { content: 'two\n' }),
  ])
  assert.equal(result.succeeded, true)
})
test('per-file result details inherit only snapshotsPruned', () => {
  const result = normalizeResult(
    'edit',
    {
      path: 'aggregate.js',
      op: 'update',
      newText: 'aggregate\n',
      snapshotsPruned: false,
      perFileResults: [{ path: 'inner.js', op: 'update' }],
    },
    {},
    CWD,
    false,
  )
  assert.deepEqual(result.targets, [
    target('inner.js', 'edit', true, { ambiguous: true }),
  ])
})


test('snapshotsPruned uses input only after the authoritative flag', () => {
  const pruned = normalizeResult(
    'edit',
    { path: 'a.js', sourcePath: 'a.js', op: 'update', snapshotsPruned: true },
    { path: 'a.js', old_string: 'old', new_string: 'new' },
    CWD,
    false,
  )
  assert.deepEqual(pruned.targets, [target('a.js', 'edit', true, {
    added: ['new'], removed: ['old'],
  })])
  const notPruned = normalizeResult(
    'edit',
    {
      path: 'actual.js',
      sourcePath: 'actual.js',
      op: 'update',
      diff: '@@\n-old\n+new\n',
      snapshotsPruned: false,
    },
    { path: 'input.js', old_string: 'old', new_string: 'new' },
    CWD,
    false,
  )
  assert.deepEqual(notPruned.targets, [target('actual.js', 'edit', true, {
    ambiguous: true,
  })])
})

test('pathless successful hashline no-op details stay target-free without input fallback', () => {
  const result = normalizeResult(
    'edit',
    { op: 'update', diff: '', snapshotsPruned: false },
    { input: '[input.js#ABCD]\nPUT 1.=1:\n+new\n' },
    CWD,
    false,
  )
  assert.deepEqual(result, {
    tool: 'edit',
    targets: [],
    unknown: null,
    succeeded: true,
    targetless: 'no-op',
  })
})

test('a pruned delete preserves its expected-absent wire target', () => {
  const result = normalizeResult(
    'edit',
    {
      snapshotsPruned: true,
      perFileResults: [{ path: 'gone.js', sourcePath: 'gone.js', op: 'delete' }],
    },
    { input: '*** Begin Patch\n*** Delete File: gone.js\n*** End Patch\n' },
    CWD,
    false,
  )
  assert.deepEqual(result.targets, [target('gone.js', 'delete', false)])
})

test('failed result skips expected-presence checks and preserves succeeded false', () => {
  const result = normalizeResult('edit', undefined, {}, CWD, true)
  assert.deepEqual(result, {
    tool: 'edit', targets: [], unknown: null, succeeded: false, targetless: 'failed',
  })
})

test('partial per-file failures keep successful landed siblings when the aggregate errors', () => {
  const result = normalizeResult(
    'apply_patch',
    {
      perFileResults: [
        {
          path: 'landed.js',
          sourcePath: 'landed.js',
          op: 'update',
          newText: 'const landed = true\n',
          snapshotsPruned: false,
        },
        {
          path: 'failed.js',
          isError: true,
          errorText: 'hashline mismatch',
        },
      ],
    },
    {},
    CWD,
    true,
  )
  assert.deepEqual(result, {
    tool: 'apply_patch',
    targets: [target('landed.js', 'edit', true, { content: 'const landed = true\n' })],
    unknown: null,
    succeeded: false,
  })
})

test('per-record pruning preserves unpruned authoritative sibling content', () => {
  const result = normalizeResult(
    'edit',
    {
      snapshotsPruned: false,
      perFileResults: [
        {
          path: 'landed.js',
          op: 'update',
          newText: 'const landed = true\n',
          snapshotsPruned: false,
        },
        {
          path: 'pruned.js',
          op: 'update',
          snapshotsPruned: true,
        },
      ],
    },
    {
      input: '*** Begin Patch\n*** Update File: pruned.js\n@@\n-old\n+const fallback = true\n*** End Patch\n',
    },
    CWD,
    false,
  )
  assert.deepEqual(result.targets, [
    target('landed.js', 'edit', true, { content: 'const landed = true\n' }),
    target('pruned.js', 'edit', true, { added: ['const fallback = true'], removed: [] }),
  ])
})

test('a pathless non-noop result is unverified', () => {
  const result = normalizeResult('edit', { op: 'update', diff: 'changed' }, {}, CWD, false)
  assert.match(result.failure, /pathless/)
})

test('malformed result flags and unmatched pruned input become unverified adapter failures', () => {
  assert.match(normalizeResult('edit', { path: 'a.js', snapshotsPruned: 'yes' }, {}, CWD, false).failure, /snapshotsPruned/)
  assert.match(
    normalizeResult(
      'edit',
      { path: 'a.js', sourcePath: 'a.js', op: 'update', snapshotsPruned: true },
      { path: 'b.js', old_string: 'old', new_string: 'new' },
      CWD,
      false,
    ).failure,
    /pruned snapshots/,
  )
  assert.match(
    normalizeResult(
      'edit',
      { perFileResults: [{ path: 'a.js', errorText: 'failed' }] },
      {},
      CWD,
      false,
    ).failure,
    /errorText/,
  )
  assert.match(normalizeResult('edit', {}, {}, CWD, 'false').failure, /non-boolean/)
})
