import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  NpmAuditExecutionError,
  getNpmVersion,
  resolveNpmCliPath,
  runNpmAudit,
} from '../lib/npm-audit-runner.mjs';

const EXPECTED_TIMEOUT_MS = 120_000;
const EXPECTED_MAX_BUFFER = 8 * 1024 * 1024;
const SENTINEL_RANDOM_BYTES = 32;

function withTempDirectory(prefix, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  try {
    return callback(directory);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function regularFileStat() {
  return { isFile: () => true };
}

function directoryStat() {
  return { isFile: () => false };
}

function assertExecutionError(code, callback) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof NpmAuditExecutionError);
    assert.equal(error.code, code);
    return true;
  });
}

function auditRunnerOptions(cacheParent, spawnSyncImpl) {
  return {
    nodeExecutable: '/runtime/bin/node',
    npmCliPath: '/runtime/npm-cli.js',
    webDirectory: '/repo/apps/web',
    cacheParent,
    spawnSyncImpl,
  };
}

function createCacheSibling(cacheParent) {
  const sibling = path.join(cacheParent, 'npm-audit-sibling-preserve');
  fs.writeFileSync(sibling, 'keep sibling');
  return sibling;
}

function recordSuccessfulAudit(counter) {
  counter.auditCalls += 1;
  return { status: 0, signal: null, stdout: '{}', stderr: '' };
}

function withCacheLifecycleHarness(cacheParent, hooks, callback) {
  const originals = {
    randomBytes: crypto.randomBytes,
    closeSync: fs.closeSync,
    fstatSync: fs.fstatSync,
    lstatSync: fs.lstatSync,
    mkdtempSync: fs.mkdtempSync,
    openSync: fs.openSync,
    realpathSync: fs.realpathSync,
    rmSync: fs.rmSync,
  };
  const realCacheParent = originals.realpathSync(cacheParent);
  const state = {
    cacheDirectory: undefined,
    auditCalls: 0,
    sentinelPath: undefined,
    sentinelDescriptor: undefined,
    directoryDescriptor: undefined,
    randomCalls: [],
    fstatAttempts: [],
    removalAttempts: [],
    closeAttempts: [],
    removalReportedSuccess: false,
  };

  function pathKind(target) {
    if (state.cacheDirectory !== undefined && target === state.cacheDirectory) return 'directory';
    if (typeof target === 'string'
        && state.cacheDirectory !== undefined
        && path.dirname(target) === state.cacheDirectory) {
      return 'sentinel';
    }
    return undefined;
  }

  function descriptorKind(descriptor) {
    if (state.sentinelDescriptor !== undefined
        && descriptor === state.sentinelDescriptor) return 'sentinel';
    if (state.directoryDescriptor !== undefined
        && descriptor === state.directoryDescriptor) return 'directory';
    return undefined;
  }

  try {
    crypto.randomBytes = (...args) => {
      state.randomCalls.push(args);
      const context = {
        args,
        state,
        proceed: () => originals.randomBytes(...args),
      };
      return hooks.randomBytes === undefined
        ? context.proceed()
        : hooks.randomBytes(context);
    };
    fs.mkdtempSync = (...args) => {
      const cacheDirectory = originals.mkdtempSync(...args);
      state.cacheDirectory = cacheDirectory;
      if (hooks.afterMkdtemp !== undefined) hooks.afterMkdtemp({ state, args });
      return cacheDirectory;
    };
    fs.openSync = (target, flags, mode) => {
      let kind = pathKind(target);
      if (kind === 'sentinel') state.sentinelPath = target;
      kind = pathKind(target);
      const context = {
        target,
        flags,
        mode,
        kind,
        state,
        originals,
        proceed: () => originals.openSync(target, flags, mode),
      };
      const descriptor = hooks.open === undefined
        ? context.proceed()
        : hooks.open(context);
      if (kind === 'sentinel') state.sentinelDescriptor = descriptor;
      if (kind === 'directory') state.directoryDescriptor = descriptor;
      return descriptor;
    };
    fs.fstatSync = (descriptor, options) => {
      const kind = descriptorKind(descriptor);
      if (kind !== undefined) state.fstatAttempts.push(kind);
      const context = {
        descriptor,
        options,
        kind,
        state,
        originals,
        proceed: () => originals.fstatSync(descriptor, options),
      };
      return hooks.fstat === undefined ? context.proceed() : hooks.fstat(context);
    };
    fs.lstatSync = (target, options) => {
      const context = {
        target,
        options,
        kind: pathKind(target),
        state,
        proceed: () => originals.lstatSync(target, options),
      };
      return hooks.lstat === undefined ? context.proceed() : hooks.lstat(context);
    };
    fs.realpathSync = (target, options) => {
      const context = {
        target,
        options,
        kind: pathKind(target),
        state,
        proceed: () => originals.realpathSync(target, options),
      };
      return hooks.realpath === undefined ? context.proceed() : hooks.realpath(context);
    };
    fs.rmSync = (target, options) => {
      if (target !== state.cacheDirectory) return originals.rmSync(target, options);
      state.removalAttempts.push({ target, options });
      const context = {
        target,
        options,
        state,
        originals,
        proceed: () => originals.rmSync(target, options),
      };
      const result = hooks.rm === undefined ? context.proceed() : hooks.rm(context);
      state.removalReportedSuccess = true;
      return result;
    };
    fs.closeSync = (descriptor) => {
      const kind = descriptorKind(descriptor);
      if (kind !== undefined) state.closeAttempts.push(kind);
      const context = {
        descriptor,
        kind,
        state,
        proceed: () => originals.closeSync(descriptor),
      };
      return hooks.close === undefined ? context.proceed() : hooks.close(context);
    };

    return callback(state, originals, realCacheParent);
  } finally {
    crypto.randomBytes = originals.randomBytes;
    fs.closeSync = originals.closeSync;
    fs.fstatSync = originals.fstatSync;
    fs.lstatSync = originals.lstatSync;
    fs.mkdtempSync = originals.mkdtempSync;
    fs.openSync = originals.openSync;
    fs.realpathSync = originals.realpathSync;
    fs.rmSync = originals.rmSync;
  }
}

test('resolves npm from the real POSIX Node distribution rather than a PATH decoy', () => {
  const requestedStats = [];
  const originalPath = process.env.PATH;
  process.env.PATH = '/untrusted/npm-shim-directory';
  try {
    const resolved = resolveNpmCliPath({
      nodeExecutable: '/usr/local/bin/node-link',
      platform: 'linux',
      realpathSyncImpl(value) {
        assert.equal(value, '/usr/local/bin/node-link');
        return '/opt/runtime/bin/node';
      },
      statSyncImpl(value) {
        requestedStats.push(value);
        if (value === '/opt/runtime/lib/node_modules/npm/bin/npm-cli.js') {
          return regularFileStat();
        }
        throw Object.assign(new Error('missing'), { code: 'ENOENT' });
      },
    });

    assert.equal(resolved, '/opt/runtime/lib/node_modules/npm/bin/npm-cli.js');
    assert.deepEqual(requestedStats, [
      '/opt/runtime/lib/node_modules/npm/bin/npm-cli.js',
      '/opt/runtime/node_modules/npm/bin/npm-cli.js',
    ]);
    assert.equal(requestedStats.some((value) => value.includes('untrusted')), false);
  } finally {
    if (originalPath === undefined) delete process.env.PATH;
    else process.env.PATH = originalPath;
  }
});

test('resolves npm from a bounded Windows Node distribution layout', () => {
  const requestedStats = [];
  const resolved = resolveNpmCliPath({
    nodeExecutable: String.raw`C:\runtime\node-link.exe`,
    platform: 'win32',
    realpathSyncImpl(value) {
      assert.equal(value, String.raw`C:\runtime\node-link.exe`);
      return String.raw`C:\runtime\node.exe`;
    },
    statSyncImpl(value) {
      requestedStats.push(value);
      if (value === String.raw`C:\runtime\node_modules\npm\bin\npm-cli.js`) {
        return regularFileStat();
      }
      throw Object.assign(new Error('missing'), { code: 'ENOENT' });
    },
  });

  assert.equal(resolved, String.raw`C:\runtime\node_modules\npm\bin\npm-cli.js`);
  assert.deepEqual(requestedStats, [
    String.raw`C:\runtime\node_modules\npm\bin\npm-cli.js`,
    String.raw`C:\runtime\lib\node_modules\npm\bin\npm-cli.js`,
  ]);
});

test('rejects missing, non-regular, ambiguous, and unresolved npm distributions', async (t) => {
  await t.test('missing', () => {
    assertExecutionError('E_NPM_CLI_NOT_FOUND', () => resolveNpmCliPath({
      nodeExecutable: '/runtime/bin/node',
      platform: 'linux',
      realpathSyncImpl: (value) => value,
      statSyncImpl() {
        throw Object.assign(new Error('missing'), { code: 'ENOENT' });
      },
    }));
  });

  await t.test('non-regular', () => {
    assertExecutionError('E_NPM_CLI_NOT_FOUND', () => resolveNpmCliPath({
      nodeExecutable: '/runtime/bin/node',
      platform: 'linux',
      realpathSyncImpl: (value) => value,
      statSyncImpl: directoryStat,
    }));
  });

  await t.test('ambiguous', () => {
    assertExecutionError('E_NPM_CLI_AMBIGUOUS', () => resolveNpmCliPath({
      nodeExecutable: '/runtime/bin/node',
      platform: 'linux',
      realpathSyncImpl: (value) => value,
      statSyncImpl: regularFileStat,
    }));
  });

  await t.test('unresolved executable', () => {
    assertExecutionError('E_NODE_EXECUTABLE', () => resolveNpmCliPath({
      nodeExecutable: '/runtime/bin/node-link',
      platform: 'linux',
      realpathSyncImpl() {
        throw new Error('outside detail must not escape');
      },
      statSyncImpl: regularFileStat,
    }));
  });

  await t.test('unsupported platform', () => {
    assertExecutionError('E_PLATFORM', () => resolveNpmCliPath({
      nodeExecutable: '/runtime/bin/node',
      platform: 'plan9',
      realpathSyncImpl: (value) => value,
      statSyncImpl: regularFileStat,
    }));
  });
});

test('resolves the npm CLI bundled with the active Node runtime', () => {
  const npmCliPath = resolveNpmCliPath();
  assert.equal(path.basename(npmCliPath), 'npm-cli.js');
  assert.equal(fs.statSync(npmCliPath).isFile(), true);
  assert.equal(npmCliPath.includes(`${path.sep}node_modules${path.sep}npm${path.sep}`), true);
});

test('gets the npm version through Node with exact shell-free bounded options', () => {
  const calls = [];
  const original = {
    cache: process.env.NPM_CONFIG_CACHE,
    notifier: process.env.nPm_CoNfIg_UpDaTe_NoTiFiEr,
  };
  process.env.NPM_CONFIG_CACHE = '/inherited/cache';
  process.env.nPm_CoNfIg_UpDaTe_NoTiFiEr = 'true';
  try {
    const version = getNpmVersion({
      nodeExecutable: '/runtime/bin/node',
      npmCliPath: '/runtime/npm-cli.js',
      cwd: '/repo/apps/web',
      spawnSyncImpl(command, args, options) {
        calls.push({ command, args, options });
        return { status: 0, signal: null, stdout: '10.9.8\n', stderr: '' };
      },
    });

    assert.equal(version, '10.9.8');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, '/runtime/bin/node');
    assert.deepEqual(calls[0].args, ['/runtime/npm-cli.js', '--version']);
    assert.equal(calls[0].options.cwd, '/repo/apps/web');
    assert.equal(calls[0].options.shell, false);
    assert.equal(calls[0].options.encoding, 'utf8');
    assert.equal(calls[0].options.timeout, EXPECTED_TIMEOUT_MS);
    assert.equal(calls[0].options.maxBuffer, EXPECTED_MAX_BUFFER);
    assert.equal(Object.keys(calls[0].options.env)
      .some((key) => key.toLowerCase() === 'npm_config_cache'), false);
    assert.deepEqual(
      Object.entries(calls[0].options.env)
        .filter(([key]) => key.toLowerCase() === 'npm_config_update_notifier'),
      [['npm_config_update_notifier', 'false']],
    );
  } finally {
    if (original.cache === undefined) delete process.env.NPM_CONFIG_CACHE;
    else process.env.NPM_CONFIG_CACHE = original.cache;
    if (original.notifier === undefined) delete process.env.nPm_CoNfIg_UpDaTe_NoTiFiEr;
    else process.env.nPm_CoNfIg_UpDaTe_NoTiFiEr = original.notifier;
  }
});

test('fails closed on every malformed npm version process result', async (t) => {
  const cases = [
    {
      name: 'timeout error before signal',
      result: {
        error: Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_TIMEOUT',
    },
    {
      name: 'buffer overflow before signal',
      result: {
        error: Object.assign(new Error('overflow'), { code: 'ENOBUFS' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_OUTPUT_OVERFLOW',
    },
    {
      name: 'generic error before signal',
      result: {
        error: Object.assign(new Error('private detail'), { code: 'EACCES' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_SPAWN',
    },
    {
      name: 'signal without process error',
      result: { signal: 'SIGKILL', status: null, stdout: '', stderr: '' },
      code: 'E_NPM_SIGNAL',
    },
    {
      name: 'absent status',
      result: { signal: null, status: null, stdout: '', stderr: '' },
      code: 'E_NPM_STATUS',
    },
    {
      name: 'nonzero status',
      result: { signal: null, status: 1, stdout: '10.9.8\n', stderr: '' },
      code: 'E_NPM_STATUS',
    },
    {
      name: 'non-string stdout',
      result: { signal: null, status: 0, stdout: Buffer.from('10.9.8\n'), stderr: '' },
      code: 'E_NPM_OUTPUT',
    },
    {
      name: 'non-string stderr',
      result: { signal: null, status: 0, stdout: '10.9.8\n', stderr: null },
      code: 'E_NPM_OUTPUT',
    },
  ];

  for (const fixture of cases) {
    await t.test(fixture.name, () => {
      assertExecutionError(fixture.code, () => getNpmVersion({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        cwd: '/repo/apps/web',
        spawnSyncImpl: () => fixture.result,
      }));
    });
  }
});

test('runs lock-only audit with an isolated exact-child cache and preserves status one as data', () => {
  withTempDirectory('npm-audit-runner-parent-', (cacheParent) => {
    const sibling = path.join(cacheParent, 'npm-audit-sibling-sentinel');
    fs.writeFileSync(sibling, 'keep');
    let cacheDirectory;

    const result = runNpmAudit({
      nodeExecutable: '/runtime/bin/node',
      npmCliPath: '/runtime/npm-cli.js',
      webDirectory: '/repo/apps/web',
      cacheParent,
      spawnSyncImpl(command, args, options) {
        assert.equal(command, '/runtime/bin/node');
        assert.deepEqual(args, [
          '/runtime/npm-cli.js',
          'audit',
          '--package-lock-only',
          '--audit-level=high',
          '--json',
        ]);
        assert.equal(options.cwd, '/repo/apps/web');
        assert.equal(options.shell, false);
        assert.equal(options.encoding, 'utf8');
        assert.equal(options.timeout, EXPECTED_TIMEOUT_MS);
        assert.equal(options.maxBuffer, EXPECTED_MAX_BUFFER);

        const controlledEntries = Object.entries(options.env).filter(([key]) => {
          const normalized = key.toLowerCase();
          return normalized === 'npm_config_cache'
            || normalized === 'npm_config_update_notifier';
        });
        assert.deepEqual(controlledEntries.sort(([left], [right]) => left.localeCompare(right)), [
          ['npm_config_cache', options.env.npm_config_cache],
          ['npm_config_update_notifier', 'false'],
        ]);
        cacheDirectory = options.env.npm_config_cache;
        assert.equal(path.dirname(cacheDirectory), fs.realpathSync(cacheParent));
        assert.equal(path.basename(cacheDirectory).startsWith('npm-audit-'), true);
        assert.equal(fs.lstatSync(cacheDirectory).isDirectory(), true);
        assert.equal(fs.lstatSync(cacheDirectory).isSymbolicLink(), false);
        return { status: 1, signal: null, stdout: '{"auditReportVersion":2}', stderr: 'findings' };
      },
    });

    assert.deepEqual(result, {
      exitCode: 1,
      stdout: '{"auditReportVersion":2}',
      stderr: 'findings',
    });
    assert.equal(fs.existsSync(cacheDirectory), false);
    assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep');
  });
});

test('removes every inherited cache and notifier case variant before setting controlled keys', () => {
  const inheritedKeys = [
    'NPM_CONFIG_CACHE',
    'Npm_Config_Cache',
    'NPM_CONFIG_UPDATE_NOTIFIER',
    'nPm_CoNfIg_UpDaTe_NoTiFiEr',
  ];
  const previous = new Map(inheritedKeys.map((key) => [key, process.env[key]]));
  for (const key of inheritedKeys) process.env[key] = `untrusted:${key}`;

  try {
    withTempDirectory('npm-audit-runner-env-', (cacheParent) => {
      runNpmAudit({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        webDirectory: '/repo/apps/web',
        cacheParent,
        spawnSyncImpl(_command, _args, options) {
          const relevant = Object.entries(options.env).filter(([key]) => {
            const normalized = key.toLowerCase();
            return normalized === 'npm_config_cache'
              || normalized === 'npm_config_update_notifier';
          });
          assert.equal(relevant.length, 2);
          assert.equal(relevant.some(([key, value]) => (
            key === 'npm_config_cache' && value.startsWith(`${fs.realpathSync(cacheParent)}${path.sep}`)
          )), true);
          assert.equal(relevant.some(([key, value]) => (
            key === 'npm_config_update_notifier' && value === 'false'
          )), true);
          return { status: 0, signal: null, stdout: '{}', stderr: '' };
        },
      });
    });
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test('classifies process failures before companion signals and always removes the cache', async (t) => {
  const cases = [
    {
      name: 'timeout error before signal',
      result: {
        error: Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_TIMEOUT',
    },
    {
      name: 'buffer overflow before signal',
      result: {
        error: Object.assign(new Error('overflow'), { code: 'ENOBUFS' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_OUTPUT_OVERFLOW',
    },
    {
      name: 'generic spawn error before signal',
      result: {
        error: Object.assign(new Error('secret spawn detail'), { code: 'EACCES' }),
        signal: 'SIGTERM', status: null, stdout: '', stderr: '',
      },
      code: 'E_NPM_SPAWN',
    },
    {
      name: 'signal',
      result: { signal: 'SIGKILL', status: null, stdout: '', stderr: '' },
      code: 'E_NPM_SIGNAL',
    },
    {
      name: 'absent status',
      result: { signal: null, status: null, stdout: '', stderr: '' },
      code: 'E_NPM_STATUS',
    },
    {
      name: 'unexpected status',
      result: { signal: null, status: 2, stdout: '', stderr: 'secret transport detail' },
      code: 'E_NPM_STATUS',
    },
    {
      name: 'malformed output',
      result: { signal: null, status: 0, stdout: Buffer.from('{}'), stderr: '' },
      code: 'E_NPM_OUTPUT',
    },
    {
      name: 'malformed stderr',
      result: { signal: null, status: 0, stdout: '{}', stderr: Buffer.from('') },
      code: 'E_NPM_OUTPUT',
    },
  ];

  for (const fixture of cases) {
    await t.test(fixture.name, () => {
      withTempDirectory('npm-audit-runner-failure-', (cacheParent) => {
        let cacheDirectory;
        assertExecutionError(fixture.code, () => runNpmAudit({
          nodeExecutable: '/runtime/bin/node',
          npmCliPath: '/runtime/npm-cli.js',
          webDirectory: '/repo/apps/web',
          cacheParent,
          spawnSyncImpl(_command, _args, options) {
            cacheDirectory = options.env.npm_config_cache;
            return fixture.result;
          },
        }));
        assert.equal(fs.existsSync(cacheDirectory), false);
      });
    });
  }

  await t.test('thrown spawn failure', () => {
    withTempDirectory('npm-audit-runner-throw-', (cacheParent) => {
      let cacheDirectory;
      assertExecutionError('E_NPM_SPAWN', () => runNpmAudit({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        webDirectory: '/repo/apps/web',
        cacheParent,
        spawnSyncImpl(_command, _args, options) {
          cacheDirectory = options.env.npm_config_cache;
          throw new Error('secret thrown detail');
        },
      }));
      assert.equal(fs.existsSync(cacheDirectory), false);
    });
  });
});

test('treats a replaced or missing cleanup target as an operational failure without touching siblings', async (t) => {
  await t.test('symlink replacement', () => {
    withTempDirectory('npm-audit-runner-cleanup-link-', (cacheParent) => {
      const sibling = path.join(cacheParent, 'npm-audit-sibling-sentinel');
      fs.writeFileSync(sibling, 'keep');
      let cacheDirectory;
      assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        webDirectory: '/repo/apps/web',
        cacheParent,
        spawnSyncImpl(_command, _args, options) {
          cacheDirectory = options.env.npm_config_cache;
          fs.rmSync(cacheDirectory, { recursive: true });
          fs.symlinkSync(sibling, cacheDirectory);
          return { status: 0, signal: null, stdout: '{}', stderr: '' };
        },
      }));
      assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep');
      assert.equal(fs.lstatSync(cacheDirectory).isSymbolicLink(), true);
    });
  });

  await t.test('missing target', () => {
    withTempDirectory('npm-audit-runner-cleanup-missing-', (cacheParent) => {
      const sibling = path.join(cacheParent, 'npm-audit-sibling-sentinel');
      fs.writeFileSync(sibling, 'keep');
      assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        webDirectory: '/repo/apps/web',
        cacheParent,
        spawnSyncImpl(_command, _args, options) {
          fs.rmSync(options.env.npm_config_cache, { recursive: true });
          return { status: 0, signal: null, stdout: '{}', stderr: '' };
        },
      }));
      assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep');
    });
  });

  await t.test('regular-file replacement', () => {
    withTempDirectory('npm-audit-runner-cleanup-file-', (cacheParent) => {
      const sibling = path.join(cacheParent, 'npm-audit-sibling-sentinel');
      fs.writeFileSync(sibling, 'keep');
      let cacheDirectory;
      assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
        nodeExecutable: '/runtime/bin/node',
        npmCliPath: '/runtime/npm-cli.js',
        webDirectory: '/repo/apps/web',
        cacheParent,
        spawnSyncImpl(_command, _args, options) {
          cacheDirectory = options.env.npm_config_cache;
          fs.rmSync(cacheDirectory, { recursive: true });
          fs.writeFileSync(cacheDirectory, 'replacement');
          return { status: 0, signal: null, stdout: '{}', stderr: '' };
        },
      }));
      assert.equal(fs.readFileSync(cacheDirectory, 'utf8'), 'replacement');
      assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep');
    });
  });

  await t.test('real removal failure', () => {
    withTempDirectory('npm-audit-runner-cleanup-permission-', (cacheParent) => {
      const sibling = path.join(cacheParent, 'npm-audit-sibling-sentinel');
      fs.writeFileSync(sibling, 'keep');
      let cacheDirectory;
      try {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
          nodeExecutable: '/runtime/bin/node',
          npmCliPath: '/runtime/npm-cli.js',
          webDirectory: '/repo/apps/web',
          cacheParent,
          spawnSyncImpl(_command, _args, options) {
            cacheDirectory = options.env.npm_config_cache;
            fs.writeFileSync(path.join(cacheDirectory, 'owned-file'), 'content');
            fs.chmodSync(cacheDirectory, 0o500);
            return { status: 0, signal: null, stdout: '{}', stderr: '' };
          },
        }));
      } finally {
        if (cacheDirectory && fs.existsSync(cacheDirectory)) {
          fs.chmodSync(cacheDirectory, 0o700);
        }
      }
      assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep');
    });
  });
});

test('refuses to remove an ordinary-directory substitution at the exact cache path', () => {
  withTempDirectory('npm-audit-runner-identity-', (cacheParent) => {
    const replacementSource = path.join(cacheParent, 'npm-audit-sibling-directory');
    const markerName = 'marker.txt';
    let cacheDirectory;
    let createdIdentity;
    let replacementIdentity;

    assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
      nodeExecutable: '/runtime/bin/node',
      npmCliPath: '/runtime/npm-cli.js',
      webDirectory: '/repo/apps/web',
      cacheParent,
      spawnSyncImpl(_command, _args, options) {
        cacheDirectory = options.env.npm_config_cache;
        const createdStat = fs.lstatSync(cacheDirectory, { bigint: true });
        createdIdentity = [createdStat.dev, createdStat.ino];

        fs.mkdirSync(replacementSource);
        fs.writeFileSync(path.join(replacementSource, markerName), 'preserve replacement');
        const replacementStat = fs.lstatSync(replacementSource, { bigint: true });
        replacementIdentity = [replacementStat.dev, replacementStat.ino];
        assert.equal(replacementStat.isDirectory(), true);
        assert.equal(replacementStat.isSymbolicLink(), false);
        assert.notDeepEqual(replacementIdentity, createdIdentity);

        fs.rmSync(cacheDirectory, { recursive: true });
        fs.renameSync(replacementSource, cacheDirectory);
        const substitutedStat = fs.lstatSync(cacheDirectory, { bigint: true });
        assert.equal(substitutedStat.isDirectory(), true);
        assert.equal(substitutedStat.isSymbolicLink(), false);
        assert.deepEqual([substitutedStat.dev, substitutedStat.ino], replacementIdentity);
        return { status: 0, signal: null, stdout: '{}', stderr: '' };
      },
    }));

    assert.equal(fs.readFileSync(path.join(cacheDirectory, markerName), 'utf8'), 'preserve replacement');
    assert.deepEqual(
      [
        fs.lstatSync(cacheDirectory, { bigint: true }).dev,
        fs.lstatSync(cacheDirectory, { bigint: true }).ino,
      ],
      replacementIdentity,
    );
  });
});

test('refuses a same-path directory substitution even when dev and ino are reused', () => {
  withTempDirectory('npm-audit-runner-identity-aba-', (cacheParent) => {
    if (process.platform === 'linux') {
      const probeDirectory = path.join(cacheParent, 'aba-reuse-probe');
      fs.mkdirSync(probeDirectory);
      const probeStat = fs.lstatSync(probeDirectory, { bigint: true });
      const probeIdentity = [probeStat.dev, probeStat.ino];
      let reusedProbeIdentity;
      let probeReused = false;
      fs.rmdirSync(probeDirectory);
      for (let attempt = 0; attempt < 2_000; attempt += 1) {
        fs.mkdirSync(probeDirectory);
        const replacementStat = fs.lstatSync(probeDirectory, { bigint: true });
        reusedProbeIdentity = [replacementStat.dev, replacementStat.ino];
        if (reusedProbeIdentity[0] === probeIdentity[0]
            && reusedProbeIdentity[1] === probeIdentity[1]) {
          probeReused = true;
          break;
        }
        fs.rmdirSync(probeDirectory);
      }
      if (probeReused) assert.deepEqual(reusedProbeIdentity, probeIdentity);
      if (fs.existsSync(probeDirectory)) fs.rmdirSync(probeDirectory);
    }

    const markerName = 'replacement-marker.txt';
    let cacheDirectory;
    let createdIdentity;
    let replacementIdentity;

    assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit({
      nodeExecutable: '/runtime/bin/node',
      npmCliPath: '/runtime/npm-cli.js',
      webDirectory: '/repo/apps/web',
      cacheParent,
      spawnSyncImpl(_command, _args, options) {
        cacheDirectory = options.env.npm_config_cache;
        const createdStat = fs.lstatSync(cacheDirectory, { bigint: true });
        createdIdentity = [createdStat.dev, createdStat.ino];
        try {
          fs.rmSync(cacheDirectory, { recursive: true });
        } catch (error) {
          fs.writeFileSync(path.join(cacheDirectory, markerName), 'preserve ABA replacement');
          throw error;
        }

        for (let attempt = 0; attempt < 2_000; attempt += 1) {
          fs.mkdirSync(cacheDirectory);
          const replacementStat = fs.lstatSync(cacheDirectory, { bigint: true });
          replacementIdentity = [replacementStat.dev, replacementStat.ino];
          if (replacementIdentity[0] === createdIdentity[0]
              && replacementIdentity[1] === createdIdentity[1]) {
            break;
          }
          if (attempt === 1_999) break;
          fs.rmdirSync(cacheDirectory);
        }

        assert.equal(fs.lstatSync(cacheDirectory).isDirectory(), true);
        fs.writeFileSync(path.join(cacheDirectory, markerName), 'preserve ABA replacement');
        return { status: 0, signal: null, stdout: '{}', stderr: '' };
      },
    }));

    if (replacementIdentity !== undefined
        && replacementIdentity[0] === createdIdentity[0]
        && replacementIdentity[1] === createdIdentity[1]) {
      assert.deepEqual(replacementIdentity, createdIdentity);
    }
    assert.equal(
      fs.readFileSync(path.join(cacheDirectory, markerName), 'utf8'),
      'preserve ABA replacement',
    );
  });
});

test('retains independent cache directory and sentinel identities through guarded cleanup', async (t) => {
  await t.test('initial path validation failure preserves the untrusted child and sibling', () => {
    withTempDirectory('npm-audit-runner-initial-validation-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      withCacheLifecycleHarness(cacheParent, {
        realpath({ kind, proceed }) {
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated initial validation failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state, originals) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.removalAttempts, []);
        assert.equal(originals.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('initial lstat failure preserves the untrusted child and sibling', () => {
    withTempDirectory('npm-audit-runner-initial-lstat-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      withCacheLifecycleHarness(cacheParent, {
        lstat({ kind, proceed }) {
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated initial lstat failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state, originals) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.removalAttempts, []);
        assert.equal(originals.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('initial unstable filesystem identity preserves the untrusted child', () => {
    withTempDirectory('npm-audit-runner-initial-identity-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      withCacheLifecycleHarness(cacheParent, {
        lstat({ kind, proceed }) {
          const stat = proceed();
          if (kind !== 'directory') return stat;
          return {
            ...stat,
            ino: 0n,
            isDirectory: () => true,
            isSymbolicLink: () => false,
          };
        },
      }, (state, originals) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.removalAttempts, []);
        assert.equal(originals.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('random-name failure preserves the untrusted child without removal', () => {
    withTempDirectory('npm-audit-runner-random-failure-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        randomBytes({ args }) {
          assert.deepEqual(args, [SENTINEL_RANDOM_BYTES]);
          throw Object.assign(new Error('simulated random failure'), { code: 'EIO' });
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(state.randomCalls.length, 1);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, []);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  for (const [name, randomMaterial] of [
    ['wrong length', Buffer.alloc(SENTINEL_RANDOM_BYTES - 1)],
    ['non-Buffer', new Uint8Array(SENTINEL_RANDOM_BYTES)],
  ]) {
    await t.test(`malformed random-name material (${name}) preserves the untrusted child`, () => {
      withTempDirectory('npm-audit-runner-random-malformed-', (cacheParent) => {
        withCacheLifecycleHarness(cacheParent, {
          randomBytes() {
            return randomMaterial;
          },
        }, (state) => {
          assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
            cacheParent,
            () => recordSuccessfulAudit(state),
          )));
          assert.equal(state.auditCalls, 0);
          assert.deepEqual(state.removalAttempts, []);
          assert.deepEqual(state.closeAttempts, []);
          assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
        });
      });
    });
  }

  await t.test('sentinel open uses exact exclusive flags and mode, then fails without removal', () => {
    withTempDirectory('npm-audit-runner-sentinel-open-', (cacheParent) => {
      const randomMaterial = Buffer.alloc(SENTINEL_RANDOM_BYTES, 0xa5);
      let observedSentinelOpen;
      withCacheLifecycleHarness(cacheParent, {
        randomBytes() {
          return randomMaterial;
        },
        open({ kind, target, flags, mode, proceed }) {
          if (kind !== 'sentinel') return proceed();
          observedSentinelOpen = { target, flags, mode };
          throw Object.assign(new Error('simulated sentinel open failure'), { code: 'EACCES' });
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.randomCalls, [[SENTINEL_RANDOM_BYTES]]);
        let expectedFlags = fs.constants.O_RDWR
          | fs.constants.O_CREAT
          | fs.constants.O_EXCL;
        if (Number.isInteger(fs.constants.O_NOFOLLOW)) {
          expectedFlags |= fs.constants.O_NOFOLLOW;
        }
        assert.deepEqual(observedSentinelOpen, {
          target: state.sentinelPath,
          flags: expectedFlags,
          mode: 0o600,
        });
        assert.equal(
          path.basename(observedSentinelOpen.target).includes(randomMaterial.toString('hex')),
          true,
        );
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, []);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('sentinel fstat failure closes it once and preserves the cache path', () => {
    withTempDirectory('npm-audit-runner-sentinel-fstat-', (cacheParent) => {
      let failureArmed = true;
      withCacheLifecycleHarness(cacheParent, {
        fstat({ kind, proceed }) {
          if (kind === 'sentinel' && failureArmed) {
            failureArmed = false;
            throw Object.assign(new Error('simulated sentinel fstat failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(failureArmed, false);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, ['sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(fs.lstatSync(state.sentinelPath).isFile(), true);
      });
    });
  });

  await t.test('sentinel path validation failure closes it once and does not remove', () => {
    withTempDirectory('npm-audit-runner-sentinel-path-', (cacheParent) => {
      let failureArmed = true;
      withCacheLifecycleHarness(cacheParent, {
        lstat({ kind, proceed }) {
          if (kind === 'sentinel' && failureArmed) {
            failureArmed = false;
            throw Object.assign(new Error('simulated sentinel path failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state, originals) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(failureArmed, false);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, ['sentinel']);
        assert.equal(originals.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(originals.lstatSync(state.sentinelPath).isFile(), true);
      });
    });
  });

  await t.test('setup-time same-path replacement is rejected despite mocked directory identity reuse', () => {
    withTempDirectory('npm-audit-runner-setup-aba-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      const markerName = 'replacement-marker.txt';
      let replacementInstalled = false;
      let createdDirectoryStat;
      let createdSentinelIdentity;
      let replacementSentinelIdentity;
      withCacheLifecycleHarness(cacheParent, {
        open({ kind, state, originals, proceed }) {
          if (kind !== 'directory') return proceed();
          const descriptor = proceed();
          createdDirectoryStat = fs.lstatSync(state.cacheDirectory, { bigint: true });
          const replacementSentinel = state.sentinelPath
            ?? path.join(state.cacheDirectory, '.replacement-sentinel');
          if (state.sentinelDescriptor !== undefined) {
            const createdSentinelStat = originals.fstatSync(
              state.sentinelDescriptor,
              { bigint: true },
            );
            createdSentinelIdentity = [createdSentinelStat.dev, createdSentinelStat.ino];
          }
          originals.rmSync(state.cacheDirectory, { recursive: true });
          fs.mkdirSync(state.cacheDirectory);
          fs.writeFileSync(replacementSentinel, '');
          const replacementSentinelStat = originals.lstatSync(
            replacementSentinel,
            { bigint: true },
          );
          replacementSentinelIdentity = [
            replacementSentinelStat.dev,
            replacementSentinelStat.ino,
          ];
          if (createdSentinelIdentity !== undefined) {
            assert.notDeepEqual(replacementSentinelIdentity, createdSentinelIdentity);
          }
          fs.writeFileSync(path.join(state.cacheDirectory, markerName), 'preserve replacement');
          replacementInstalled = true;
          return descriptor;
        },
        lstat({ kind, proceed }) {
          if (replacementInstalled && kind === 'directory') return createdDirectoryStat;
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(replacementInstalled, true);
        assert.notEqual(replacementSentinelIdentity, undefined);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(
          fs.readFileSync(path.join(state.cacheDirectory, markerName), 'utf8'),
          'preserve replacement',
        );
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('directory open failure preserves the path because the complete identity is unavailable', () => {
    withTempDirectory('npm-audit-runner-directory-open-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      withCacheLifecycleHarness(cacheParent, {
        open({ kind, proceed }) {
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated directory open failure'), { code: 'EACCES' });
          }
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, ['sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
        assert.equal(fs.lstatSync(state.sentinelPath).isFile(), true);
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('movable sentinel cannot authorize cleanup without a directory handle', () => {
    withTempDirectory('npm-audit-runner-directory-open-aba-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      const relocatedOriginal = path.join(cacheParent, 'npm-audit-relocated-original');
      const markerName = 'replacement-marker.txt';
      let createdDirectoryStat;
      let createdSentinelIdentity;
      let replacementDirectoryIdentity;
      let movedSentinelIdentity;
      let replacementInstalled = false;
      withCacheLifecycleHarness(cacheParent, {
        open({ kind, state, originals, proceed }) {
          if (kind !== 'directory') return proceed();
          createdDirectoryStat = originals.lstatSync(state.cacheDirectory, { bigint: true });
          const createdSentinelStat = originals.fstatSync(
            state.sentinelDescriptor,
            { bigint: true },
          );
          createdSentinelIdentity = [createdSentinelStat.dev, createdSentinelStat.ino];
          const sentinelName = path.basename(state.sentinelPath);
          fs.renameSync(state.cacheDirectory, relocatedOriginal);
          fs.mkdirSync(state.cacheDirectory);
          const replacementDirectoryStat = originals.lstatSync(
            state.cacheDirectory,
            { bigint: true },
          );
          replacementDirectoryIdentity = [
            replacementDirectoryStat.dev,
            replacementDirectoryStat.ino,
          ];
          fs.renameSync(
            path.join(relocatedOriginal, sentinelName),
            state.sentinelPath,
          );
          const movedSentinelStat = originals.lstatSync(state.sentinelPath, { bigint: true });
          movedSentinelIdentity = [movedSentinelStat.dev, movedSentinelStat.ino];
          fs.writeFileSync(path.join(state.cacheDirectory, markerName), 'preserve replacement');
          replacementInstalled = true;
          throw Object.assign(new Error('simulated directory open failure'), { code: 'EACCES' });
        },
        lstat({ kind, proceed }) {
          if (replacementInstalled && kind === 'directory') return createdDirectoryStat;
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(replacementInstalled, true);
        assert.notEqual(createdDirectoryStat, undefined);
        assert.notEqual(createdSentinelIdentity, undefined);
        assert.notEqual(replacementDirectoryIdentity, undefined);
        assert.notEqual(movedSentinelIdentity, undefined);
        assert.notDeepEqual(
          replacementDirectoryIdentity,
          [createdDirectoryStat.dev, createdDirectoryStat.ino],
        );
        assert.deepEqual(movedSentinelIdentity, createdSentinelIdentity);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, ['sentinel']);
        assert.equal(
          fs.readFileSync(path.join(state.cacheDirectory, markerName), 'utf8'),
          'preserve replacement',
        );
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('one-shot directory fstat failure is cleaned while both handles remain open', () => {
    withTempDirectory('npm-audit-runner-directory-fstat-', (cacheParent) => {
      let failureArmed = true;
      withCacheLifecycleHarness(cacheParent, {
        fstat({ kind, proceed }) {
          if (kind === 'directory' && failureArmed) {
            failureArmed = false;
            throw Object.assign(new Error('simulated first directory fstat failure'), { code: 'EIO' });
          }
          return proceed();
        },
        rm({ state, originals, proceed }) {
          assert.deepEqual(state.fstatAttempts.slice(-2), ['directory', 'sentinel']);
          assert.doesNotThrow(() => originals.fstatSync(
            state.directoryDescriptor,
            { bigint: true },
          ));
          assert.doesNotThrow(() => originals.fstatSync(
            state.sentinelDescriptor,
            { bigint: true },
          ));
          return proceed();
        },
        close({ state, proceed }) {
          assert.equal(state.removalReportedSuccess, true);
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CREATE', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(failureArmed, false);
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.existsSync(state.cacheDirectory), false);
      });
    });
  });

  await t.test('persistent directory fstat failure prevents removal and closes both handles', () => {
    withTempDirectory('npm-audit-runner-directory-fstat-persistent-', (cacheParent) => {
      let failureCount = 0;
      withCacheLifecycleHarness(cacheParent, {
        fstat({ kind, proceed }) {
          if (kind === 'directory') {
            failureCount += 1;
            throw Object.assign(new Error('simulated persistent fstat failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(failureCount >= 2, true);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('post-open path validation failure is cleaned after a later successful revalidation', () => {
    withTempDirectory('npm-audit-runner-directory-post-validation-', (cacheParent) => {
      let failureArmed = true;
      withCacheLifecycleHarness(cacheParent, {
        realpath({ kind, state, proceed }) {
          if (kind === 'directory'
              && state.directoryDescriptor !== undefined
              && failureArmed) {
            failureArmed = false;
            throw Object.assign(new Error('simulated post-open validation failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CREATE', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )));
        assert.equal(state.auditCalls, 0);
        assert.equal(failureArmed, false);
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.existsSync(state.cacheDirectory), false);
      });
    });
  });

  await t.test('post-acquisition sentinel fstat failure prevents removal', () => {
    withTempDirectory('npm-audit-runner-sentinel-late-fstat-', (cacheParent) => {
      let failureArmed = false;
      withCacheLifecycleHarness(cacheParent, {
        fstat({ kind, proceed }) {
          if (kind === 'sentinel' && failureArmed) {
            throw Object.assign(new Error('simulated late sentinel fstat failure'), { code: 'EIO' });
          }
          return proceed();
        },
      }, (state) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => {
            state.auditCalls += 1;
            failureArmed = true;
            return { status: 0, signal: null, stdout: '{}', stderr: '' };
          },
        )));
        assert.equal(state.auditCalls, 1);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('normal cleanup keeps both handles open, removes once, closes once, and verifies ENOENT', () => {
    withTempDirectory('npm-audit-runner-two-handles-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      const randomMaterial = Buffer.from(Array.from(
        { length: SENTINEL_RANDOM_BYTES },
        (_value, index) => index,
      ));
      let finalVerificationObserved = false;
      withCacheLifecycleHarness(cacheParent, {
        randomBytes({ args }) {
          assert.deepEqual(args, [SENTINEL_RANDOM_BYTES]);
          return randomMaterial;
        },
        rm({ options, state, originals, proceed }) {
          assert.deepEqual(options, { recursive: true, force: false, maxRetries: 0 });
          assert.deepEqual(state.fstatAttempts.slice(-2), ['directory', 'sentinel']);
          assert.doesNotThrow(() => originals.fstatSync(
            state.directoryDescriptor,
            { bigint: true },
          ));
          assert.doesNotThrow(() => originals.fstatSync(
            state.sentinelDescriptor,
            { bigint: true },
          ));
          return proceed();
        },
        close({ state, proceed }) {
          assert.equal(state.removalReportedSuccess, true);
          assert.equal(fs.existsSync(state.cacheDirectory), false);
          return proceed();
        },
        lstat({ kind, state, proceed }) {
          if (kind === 'directory' && state.removalReportedSuccess) {
            assert.equal(state.closeAttempts.length, 2);
            finalVerificationObserved = true;
          }
          return proceed();
        },
      }, (state, originals) => {
        const result = runNpmAudit(auditRunnerOptions(cacheParent, (_command, _args, options) => {
          assert.equal(options.env.npm_config_cache, state.cacheDirectory);
          assert.equal(path.dirname(state.sentinelPath), state.cacheDirectory);
          assert.equal(
            path.basename(state.sentinelPath).includes(randomMaterial.toString('hex')),
            true,
          );
          const sentinelStat = originals.lstatSync(state.sentinelPath);
          assert.equal(sentinelStat.isFile(), true);
          assert.equal(sentinelStat.isSymbolicLink(), false);
          assert.equal(sentinelStat.size, 0);
          if (process.platform !== 'win32') {
            assert.equal(sentinelStat.mode & 0o777, 0o600);
          }
          assert.doesNotThrow(() => originals.fstatSync(state.directoryDescriptor));
          assert.doesNotThrow(() => originals.fstatSync(state.sentinelDescriptor));
          return { status: 0, signal: null, stdout: '{}', stderr: '' };
        }));
        assert.deepEqual(result, { exitCode: 0, stdout: '{}', stderr: '' });
        assert.equal(state.randomCalls.length, 1);
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(finalVerificationObserved, true);
        assert.equal(fs.existsSync(state.cacheDirectory), false);
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });

  await t.test('removal failure outranks close and audit failures without a retry', () => {
    withTempDirectory('npm-audit-runner-removal-precedence-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        rm({ options, state, originals }) {
          assert.deepEqual(options, { recursive: true, force: false, maxRetries: 0 });
          assert.deepEqual(state.fstatAttempts.slice(-2), ['directory', 'sentinel']);
          assert.doesNotThrow(() => originals.fstatSync(
            state.directoryDescriptor,
            { bigint: true },
          ));
          assert.doesNotThrow(() => originals.fstatSync(
            state.sentinelDescriptor,
            { bigint: true },
          ));
          throw Object.assign(new Error('simulated removal failure'), { code: 'EACCES' });
        },
        close({ kind, proceed }) {
          const result = proceed();
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated directory close failure'), { code: 'EIO' });
          }
          return result;
        },
      }, (state) => {
        assert.throws(() => runNpmAudit(auditRunnerOptions(cacheParent, () => ({
          error: Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }),
          signal: 'SIGTERM',
          status: null,
          stdout: '',
          stderr: '',
        }))), (error) => {
          assert.ok(error instanceof NpmAuditExecutionError);
          assert.equal(error.code, 'E_CACHE_CLEANUP');
          assert.equal(error.message, 'the npm cache could not be removed');
          return true;
        });
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('a reported removal success must end with the cache path absent', () => {
    withTempDirectory('npm-audit-runner-removal-noop-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        rm({ options }) {
          assert.deepEqual(options, { recursive: true, force: false, maxRetries: 0 });
          return undefined;
        },
      }, (state) => {
        assert.throws(() => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => ({ status: 0, signal: null, stdout: '{}', stderr: '' }),
        )), (error) => {
          assert.ok(error instanceof NpmAuditExecutionError);
          assert.equal(error.code, 'E_CACHE_CLEANUP');
          assert.equal(error.message, 'the npm cache still exists after cleanup');
          return true;
        });
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('missing directory identity outranks sentinel close and setup failures', () => {
    withTempDirectory('npm-audit-runner-setup-close-precedence-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        open({ kind, proceed }) {
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated directory open failure'), { code: 'EACCES' });
          }
          return proceed();
        },
        close({ kind, proceed }) {
          const result = proceed();
          if (kind === 'sentinel') {
            throw Object.assign(new Error('simulated sentinel close failure'), { code: 'EIO' });
          }
          return result;
        },
      }, (state) => {
        assert.throws(() => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => recordSuccessfulAudit(state),
        )), (error) => {
          assert.ok(error instanceof NpmAuditExecutionError);
          assert.equal(error.code, 'E_CACHE_CLEANUP');
          assert.equal(error.message, 'the npm cache directory handle is unavailable');
          return true;
        });
        assert.equal(state.auditCalls, 0);
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual(state.closeAttempts, ['sentinel']);
        assert.equal(fs.lstatSync(state.cacheDirectory).isDirectory(), true);
      });
    });
  });

  await t.test('directory close failure outranks the original audit failure', () => {
    withTempDirectory('npm-audit-runner-audit-close-precedence-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        close({ kind, proceed }) {
          const result = proceed();
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated directory close failure'), { code: 'EIO' });
          }
          return result;
        },
      }, (state) => {
        assert.throws(() => runNpmAudit(auditRunnerOptions(cacheParent, () => ({
          error: Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' }),
          signal: 'SIGTERM',
          status: null,
          stdout: '',
          stderr: '',
        }))), (error) => {
          assert.ok(error instanceof NpmAuditExecutionError);
          assert.equal(error.code, 'E_CACHE_CLEANUP');
          assert.equal(error.message, 'the npm cache directory handle could not be closed');
          return true;
        });
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.existsSync(state.cacheDirectory), false);
      });
    });
  });

  for (const failingKind of ['directory', 'sentinel']) {
    await t.test(`${failingKind} close failure still attempts both closes once`, () => {
      withTempDirectory(`npm-audit-runner-${failingKind}-close-`, (cacheParent) => {
        let finalVerificationObserved = false;
        withCacheLifecycleHarness(cacheParent, {
          close({ kind, proceed }) {
            const result = proceed();
            if (kind === failingKind) {
              throw Object.assign(new Error('simulated close failure'), { code: 'EIO' });
            }
            return result;
          },
          lstat({ kind, state, proceed }) {
            if (kind === 'directory' && state.removalReportedSuccess) {
              finalVerificationObserved = true;
            }
            return proceed();
          },
        }, (state) => {
          assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
            cacheParent,
            () => ({ status: 0, signal: null, stdout: '{}', stderr: '' }),
          )));
          assert.equal(state.removalAttempts.length, 1);
          assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
          assert.equal(finalVerificationObserved, true);
          assert.equal(fs.existsSync(state.cacheDirectory), false);
        });
      });
    });
  }

  await t.test('final verification failure outranks a close failure', () => {
    withTempDirectory('npm-audit-runner-final-verification-', (cacheParent) => {
      withCacheLifecycleHarness(cacheParent, {
        close({ kind, proceed }) {
          const result = proceed();
          if (kind === 'directory') {
            throw Object.assign(new Error('simulated directory close failure'), { code: 'EIO' });
          }
          return result;
        },
        lstat({ kind, state, proceed }) {
          if (kind === 'directory'
              && state.removalReportedSuccess
              && state.closeAttempts.length === 2) {
            throw Object.assign(new Error('simulated final verification failure'), { code: 'EACCES' });
          }
          return proceed();
        },
      }, (state) => {
        assert.throws(() => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => ({ status: 0, signal: null, stdout: '{}', stderr: '' }),
        )), (error) => {
          assert.ok(error instanceof NpmAuditExecutionError);
          assert.equal(error.code, 'E_CACHE_CLEANUP');
          assert.equal(error.message, 'the npm cache removal could not be verified');
          return true;
        });
        assert.equal(state.removalAttempts.length, 1);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(fs.existsSync(state.cacheDirectory), false);
      });
    });
  });

  await t.test('sentinel path substitution during audit preserves the cache directory', () => {
    withTempDirectory('npm-audit-runner-sentinel-substitution-', (cacheParent) => {
      const sibling = createCacheSibling(cacheParent);
      withCacheLifecycleHarness(cacheParent, {}, (state, originals) => {
        assertExecutionError('E_CACHE_CLEANUP', () => runNpmAudit(auditRunnerOptions(
          cacheParent,
          () => {
            originals.rmSync(state.sentinelPath);
            fs.writeFileSync(state.sentinelPath, '');
            fs.writeFileSync(
              path.join(state.cacheDirectory, 'replacement-marker.txt'),
              'preserve sentinel replacement',
            );
            return { status: 0, signal: null, stdout: '{}', stderr: '' };
          },
        )));
        assert.deepEqual(state.removalAttempts, []);
        assert.deepEqual([...state.closeAttempts].sort(), ['directory', 'sentinel']);
        assert.equal(
          fs.readFileSync(
            path.join(state.cacheDirectory, 'replacement-marker.txt'),
            'utf8',
          ),
          'preserve sentinel replacement',
        );
        assert.equal(fs.readFileSync(sibling, 'utf8'), 'keep sibling');
      });
    });
  });
});

test('executes a real npm-cli.js fixture through process.execPath without a shell', () => {
  withTempDirectory('npm-audit-real-boundary-', (directory) => {
    const npmCliPath = path.join(directory, 'npm-cli.js');
    const webDirectory = path.join(directory, 'web');
    const cacheParent = path.join(directory, 'cache-parent');
    fs.mkdirSync(webDirectory);
    fs.mkdirSync(cacheParent);
    fs.writeFileSync(npmCliPath, `
const args = process.argv.slice(2);
if (args.length === 1 && args[0] === '--version') {
  process.stdout.write('10.9.8\\n');
} else if (JSON.stringify(args) === JSON.stringify([
  'audit', '--package-lock-only', '--audit-level=high', '--json'
])) {
  if (!process.env.npm_config_cache || process.env.npm_config_update_notifier !== 'false') {
    process.exitCode = 9;
  } else {
    process.stdout.write(JSON.stringify({ auditReportVersion: 2, fixture: 'real-spawn' }));
    process.stderr.write('fixture-stderr');
    process.exitCode = 1;
  }
} else {
  process.exitCode = 8;
}
`);

    assert.equal(getNpmVersion({
      nodeExecutable: process.execPath,
      npmCliPath,
      cwd: webDirectory,
    }), '10.9.8');

    assert.deepEqual(runNpmAudit({
      nodeExecutable: process.execPath,
      npmCliPath,
      webDirectory,
      cacheParent,
    }), {
      exitCode: 1,
      stdout: '{"auditReportVersion":2,"fixture":"real-spawn"}',
      stderr: 'fixture-stderr',
    });
    assert.deepEqual(fs.readdirSync(cacheParent), []);
  });
});
