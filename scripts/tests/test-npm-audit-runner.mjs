import assert from 'node:assert/strict';
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
