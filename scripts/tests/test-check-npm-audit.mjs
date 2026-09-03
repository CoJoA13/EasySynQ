import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

import { main } from '../check-npm-audit.mjs';

const root = path.resolve(import.meta.dirname, '../..');
const fixtureDirectory = path.join(import.meta.dirname, 'fixtures/npm-audit');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const cleanFixture = readJson(path.join(fixtureDirectory, 'audit-clean.json'));
const routerFixture = readJson(path.join(fixtureDirectory, 'audit-router-7.18.2.json'));
const unexpectedFixture = readJson(path.join(fixtureDirectory, 'audit-unexpected-high.json'));
const lockFixture = readJson(path.join(fixtureDirectory, 'package-lock.json'));
const productionExceptionPolicy = readJson(
  path.join(root, '.github/security/npm-audit-exceptions.json'),
);
const routerExceptionFixture = readJson(
  path.join(fixtureDirectory, 'exceptions-router-7.18.2.json'),
);

const beforeExpiry = new Date('2026-08-21T23:59:59.999Z');
const atExpiry = new Date('2026-08-22T00:00:00.000Z');
const clone = (value) => structuredClone(value);

function outputCapture() {
  let value = '';
  return {
    stream: {
      write(chunk) {
        value += String(chunk);
        return true;
      },
    },
    read: () => value,
  };
}

function combineReports(...reports) {
  const combined = clone(cleanFixture);
  combined.vulnerabilities = Object.assign(
    {},
    ...reports.map((report) => clone(report.vulnerabilities)),
  );
  const records = Object.values(combined.vulnerabilities);
  combined.metadata.vulnerabilities = {
    info: records.filter(({ severity }) => severity === 'info').length,
    low: records.filter(({ severity }) => severity === 'low').length,
    moderate: records.filter(({ severity }) => severity === 'moderate').length,
    high: records.filter(({ severity }) => severity === 'high').length,
    critical: records.filter(({ severity }) => severity === 'critical').length,
    total: records.length,
  };
  return combined;
}

function fakeRun({
  report = cleanFixture,
  lockfile = lockFixture,
  exceptions = productionExceptionPolicy,
  npmVersion = '11.19.0',
  auditStatus,
  auditStderr = '',
  now = beforeExpiry,
  checkRouterRscUsageImpl = () => [],
  readFileSyncImpl,
  spawnSyncImpl,
  typescript = { fixture: 'typescript-boundary' },
  execFileSyncImpl = () => '',
} = {}) {
  const stdout = outputCapture();
  const stderr = outputCapture();
  const spawnCalls = [];
  const readCalls = [];
  const boundary = spawnSyncImpl ?? ((command, args, options) => {
    spawnCalls.push({ command, args, options });
    if (args.length === 2 && args[1] === '--version') {
      return { status: 0, signal: null, stdout: `${npmVersion}\n`, stderr: '' };
    }
    assert.deepEqual(args.slice(1), [
      'audit',
      '--package-lock-only',
      '--audit-level=high',
      '--json',
    ]);
    const status = auditStatus ?? (
      report.metadata.vulnerabilities.high + report.metadata.vulnerabilities.critical > 0 ? 1 : 0
    );
    return {
      status,
      signal: null,
      stdout: typeof report === 'string' ? report : JSON.stringify(report),
      stderr: auditStderr,
    };
  });
  const reader = readFileSyncImpl ?? ((file, encoding) => {
    assert.equal(encoding, 'utf8');
    readCalls.push(file);
    if (file === path.join(root, 'apps/web/package-lock.json')) {
      return JSON.stringify(lockfile);
    }
    if (file === path.join(root, '.github/security/npm-audit-exceptions.json')) {
      return JSON.stringify(exceptions);
    }
    throw new Error(`unexpected read: ${file}`);
  });

  const exitCode = main({
    spawnSyncImpl: boundary,
    now,
    stdout: stdout.stream,
    stderr: stderr.stream,
    readFileSyncImpl: reader,
    checkRouterRscUsageImpl,
    typescript,
    execFileSyncImpl,
  });

  return {
    exitCode,
    stdout: stdout.read(),
    stderr: stderr.read(),
    spawnCalls,
    readCalls,
  };
}

function acceptedLine(reason = routerExceptionFixture.exceptions[0].reason) {
  const json = JSON.stringify({
    advisoryId: 'GHSA-qwww-vcr4-c8h2',
    rootPackage: 'react-router',
    packages: ['react-router@7.18.2', 'react-router-dom@7.18.2'],
    expiresAt: '2026-08-22T00:00:00Z',
    usagePolicy: 'router-rsc-absent',
    reason,
  }).replaceAll('\u2028', '\\u2028').replaceAll('\u2029', '\\u2029');
  return `ACCEPTED ${json}\n`;
}

test('reads only the fixed lock and exception documents and accepts a clean report', () => {
  let routerCalls = 0;
  const result = fakeRun({
    checkRouterRscUsageImpl() {
      routerCalls += 1;
      return [];
    },
  });

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout, 'SUMMARY {"accepted":0,"blocked":0,"ignored":{"info":0,"low":0,"moderate":0}}\n');
  assert.deepEqual(result.readCalls, [
    path.join(root, 'apps/web/package-lock.json'),
    path.join(root, '.github/security/npm-audit-exceptions.json'),
  ]);
  assert.equal(routerCalls, 0);
});

test('the production policy blocks the Router pair without invoking RSC analysis', () => {
  let routerCalls = 0;
  const result = fakeRun({
    report: routerFixture,
    checkRouterRscUsageImpl() {
      routerCalls += 1;
      return [];
    },
  });

  assert.equal(result.exitCode, 1);
  assert.equal(routerCalls, 0);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.includes('ACCEPTED'), false);
  assert.equal(result.stdout, [
    `BLOCKED ${JSON.stringify({
      package: 'react-router',
      version: '7.18.2',
      severity: 'high',
      advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
      reason: 'unapproved-high-or-critical',
    })}`,
    `BLOCKED ${JSON.stringify({
      package: 'react-router-dom',
      version: '7.18.2',
      severity: 'high',
      advisoryIds: ['react-router'],
      reason: 'unapproved-high-or-critical',
    })}`,
    'SUMMARY {"accepted":0,"blocked":2,"ignored":{"info":0,"low":0,"moderate":0}}',
    '',
  ].join('\n'));
});

test('resolves fixed repository inputs and the web cwd independently of process.cwd()', () => {
  const unrelated = fs.mkdtempSync(path.join(os.tmpdir(), 'check-npm-audit-cwd-'));
  const originalCwd = process.cwd();
  try {
    process.chdir(unrelated);
    const result = fakeRun();
    assert.equal(result.exitCode, 0);
    assert.deepEqual(result.readCalls, [
      path.join(root, 'apps/web/package-lock.json'),
      path.join(root, '.github/security/npm-audit-exceptions.json'),
    ]);
    assert.equal(result.spawnCalls.length, 2);
    assert.equal(result.spawnCalls[0].options.cwd, path.join(root, 'apps/web'));
    assert.equal(result.spawnCalls[1].options.cwd, path.join(root, 'apps/web'));
  } finally {
    process.chdir(originalCwd);
    fs.rmSync(unrelated, { recursive: true, force: true });
  }
});

test('importing the CLI module has no npm, fixed-file, Git, or TypeScript side effects', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'check-npm-audit-import-'));
  try {
    const marker = path.join(directory, 'boundary-used');
    const preload = path.join(directory, 'deny-boundaries.cjs');
    const cliPath = path.join(root, 'scripts/check-npm-audit.mjs');
    fs.writeFileSync(preload, `
const fs = require('node:fs');
const childProcess = require('node:child_process');
const moduleApi = require('node:module');
const marker = ${JSON.stringify(marker)};
const originalRead = fs.readFileSync;
const originalRealpath = fs.realpathSync;
const originalStat = fs.statSync;
function mark(name) { fs.writeFileSync(marker, name); }
fs.readFileSync = function(file, ...args) {
  const value = String(file);
  if (value.endsWith('apps/web/package-lock.json') || value.endsWith('.github/security/npm-audit-exceptions.json')) {
    mark('read');
    throw new Error('forbidden read');
  }
  return originalRead.call(this, file, ...args);
};
fs.realpathSync = function(file, ...args) {
  if (String(file) === process.execPath) mark('realpath');
  return originalRealpath.call(this, file, ...args);
};
fs.statSync = function(file, ...args) {
  if (String(file).endsWith('npm-cli.js')) mark('stat');
  return originalStat.call(this, file, ...args);
};
childProcess.spawnSync = function() { mark('spawn'); throw new Error('forbidden spawn'); };
childProcess.execFileSync = function() { mark('git'); throw new Error('forbidden git'); };
moduleApi.createRequire = function() { mark('typescript'); throw new Error('forbidden TypeScript resolution'); };
moduleApi.syncBuiltinESMExports();
`);

    const result = spawnSync(process.execPath, [
      '--require',
      preload,
      '--input-type=module',
      '--eval',
      `await import(${JSON.stringify(pathToFileURL(cliPath).href)})`,
    ], {
      cwd: directory,
      encoding: 'utf8',
      shell: false,
      timeout: 30_000,
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.signal, null);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, '');
    assert.equal(fs.existsSync(marker), false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('validates npm 11.x before starting audit', () => {
  const calls = [];
  const result = fakeRun({
    npmVersion: '10.9.8',
    spawnSyncImpl(command, args, options) {
      calls.push({ command, args, options });
      return { status: 0, signal: null, stdout: '10.9.8\n', stderr: '' };
    },
  });

  assert.equal(result.exitCode, 2);
  assert.equal(calls.length, 1);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, 'ERROR {"code":"E_NPM_VERSION"}\n');
});

test('runs one Router check and prints one reason for the two accepted records', () => {
  const calls = [];
  const result = fakeRun({
    report: routerFixture,
    exceptions: routerExceptionFixture,
    checkRouterRscUsageImpl(options) {
      calls.push(options);
      return [];
    },
  });

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, '');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].repoRoot, root);
  assert.deepEqual(calls[0].typescript, { fixture: 'typescript-boundary' });
  assert.equal(typeof calls[0].execFileSyncImpl, 'function');
  assert.equal(result.stdout, `${acceptedLine()}SUMMARY {"accepted":1,"blocked":0,"ignored":{"info":0,"low":0,"moderate":0}}\n`);
});

test('joins and checks distinct accepted exceptions that share advisory metadata', () => {
  const advisoryId = 'GHSA-5555-6666-7777';
  const report = clone(cleanFixture);
  const lockfile = clone(lockFixture);
  const exceptions = { schemaVersion: 1, exceptions: [] };
  report.vulnerabilities = {};
  for (const [index, packageName] of ['root-two', 'root-one'].entries()) {
    const record = clone(unexpectedFixture.vulnerabilities['synthetic-vulnerable']);
    record.name = packageName;
    record.via[0].source = 2000000 + index;
    record.via[0].name = packageName;
    record.via[0].dependency = packageName;
    record.via[0].url = `https://github.com/advisories/${advisoryId}`;
    record.nodes = [`node_modules/${packageName}`];
    report.vulnerabilities[packageName] = record;
    lockfile.packages[`node_modules/${packageName}`] = { version: '1.0.0' };
    exceptions.exceptions.push({
      advisoryId,
      rootPackage: packageName,
      advisoryUrl: `https://github.com/advisories/${advisoryId}`,
      reason: `Reviewed synthetic exception ${packageName.slice('root-'.length)}.`,
      expiresAt: '2026-08-22T00:00:00Z',
      usagePolicy: 'router-rsc-absent',
      records: [{
        package: packageName,
        version: '1.0.0',
        isDirect: true,
        causes: [advisoryId],
        effects: [],
      }],
    });
  }
  report.metadata.vulnerabilities.high = 2;
  report.metadata.vulnerabilities.total = 2;
  let routerCalls = 0;

  const result = fakeRun({
    report,
    lockfile,
    exceptions,
    checkRouterRscUsageImpl() {
      routerCalls += 1;
      return [];
    },
  });

  assert.equal(result.exitCode, 0);
  assert.equal(routerCalls, 2);
  assert.equal(result.stdout.match(/^ACCEPTED /gm)?.length, 2);
  assert.deepEqual(
    result.stdout
      .trimEnd()
      .split('\n')
      .filter((line) => line.startsWith('ACCEPTED '))
      .map((line) => JSON.parse(line.slice('ACCEPTED '.length)))
      .map(({ rootPackage, reason }) => ({ rootPackage, reason })),
    [
      { rootPackage: 'root-one', reason: 'Reviewed synthetic exception one.' },
      { rootPackage: 'root-two', reason: 'Reviewed synthetic exception two.' },
    ],
  );
  assert.equal(result.stdout.endsWith('SUMMARY {"accepted":2,"blocked":0,"ignored":{"info":0,"low":0,"moderate":0}}\n'), true);
});

test('runs an accepted Router policy despite another blocked finding', () => {
  const combined = combineReports(routerFixture, unexpectedFixture);
  let routerCalls = 0;
  const result = fakeRun({
    report: combined,
    exceptions: routerExceptionFixture,
    checkRouterRscUsageImpl() {
      routerCalls += 1;
      return [];
    },
  });

  assert.equal(result.exitCode, 1);
  assert.equal(routerCalls, 1);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout, [
    acceptedLine().trimEnd(),
    `BLOCKED ${JSON.stringify({
      package: 'synthetic-vulnerable',
      version: '1.0.0',
      severity: 'high',
      advisoryIds: ['GHSA-aaaa-bbbb-cccc'],
      reason: 'unapproved-high-or-critical',
    })}`,
    'SUMMARY {"accepted":1,"blocked":1,"ignored":{"info":0,"low":0,"moderate":0}}',
    '',
  ].join('\n'));
});

test('an operational Router failure outranks a simultaneous blocked finding', () => {
  const combined = combineReports(routerFixture, unexpectedFixture);
  const result = fakeRun({
    report: combined,
    exceptions: routerExceptionFixture,
    checkRouterRscUsageImpl() {
      throw Object.assign(new Error('/outside/repo SOURCE_BODY ENV_SECRET'), {
        code: 'E_TYPESCRIPT_RESOLUTION',
      });
    },
  });

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, 'ERROR {"code":"E_TYPESCRIPT_RESOLUTION"}\n');
  assert.equal(result.stderr.includes('/outside/repo'), false);
  assert.equal(result.stderr.includes('SOURCE_BODY'), false);
  assert.equal(result.stderr.includes('ENV_SECRET'), false);
});

test('Router violations block the exception without printing its acceptance reason', () => {
  const result = fakeRun({
    report: routerFixture,
    exceptions: routerExceptionFixture,
    checkRouterRscUsageImpl() {
      return [{
        code: 'E_ROUTER_RSC_API',
        path: 'apps/web/src/secret.ts',
        line: 12,
        column: 4,
        api: 'unstable_createCallServer',
      }];
    },
  });

  assert.equal(result.exitCode, 1);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout, [
    `BLOCKED ${JSON.stringify({
      advisoryId: 'GHSA-qwww-vcr4-c8h2',
      usagePolicy: 'router-rsc-absent',
      reason: 'usage-policy-violation',
      violations: 1,
    })}`,
    'SUMMARY {"accepted":0,"blocked":1,"ignored":{"info":0,"low":0,"moderate":0}}',
    '',
  ].join('\n'));
  assert.equal(result.stdout.includes('secret.ts'), false);
  assert.equal(result.stdout.includes('unstable_createCallServer'), false);
});

test('a malformed Router policy result is an operational failure', () => {
  const result = fakeRun({
    report: routerFixture,
    exceptions: routerExceptionFixture,
    checkRouterRscUsageImpl() {
      return { length: 0 };
    },
  });

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, 'ERROR {"code":"E_USAGE_POLICY_RESULT"}\n');
});

test('does not run Router policy for an unused or policy-rejected exception', async (t) => {
  await t.test('unused', () => {
    let calls = 0;
    const result = fakeRun({
      exceptions: routerExceptionFixture,
      checkRouterRscUsageImpl() {
        calls += 1;
        return [];
      },
    });
    assert.equal(result.exitCode, 0);
    assert.equal(calls, 0);
  });

  await t.test('expired', () => {
    let calls = 0;
    const result = fakeRun({
      report: routerFixture,
      exceptions: routerExceptionFixture,
      now: atExpiry,
      checkRouterRscUsageImpl() {
        calls += 1;
        return [];
      },
    });
    assert.equal(result.exitCode, 1);
    assert.equal(calls, 0);
    assert.equal(result.stdout.includes('ACCEPTED'), false);
    assert.equal(result.stdout.match(/^BLOCKED /gm)?.length, 2);
  });

  await t.test('record mismatch', () => {
    const mismatchedLock = clone(lockFixture);
    mismatchedLock.packages['node_modules/react-router'].version = '7.18.1';
    let calls = 0;
    const result = fakeRun({
      report: routerFixture,
      exceptions: routerExceptionFixture,
      lockfile: mismatchedLock,
      checkRouterRscUsageImpl() {
        calls += 1;
        return [];
      },
    });
    assert.equal(result.exitCode, 1);
    assert.equal(calls, 0);
    assert.equal(result.stdout.includes('ACCEPTED'), false);
  });
});

test('rejects an unknown usage policy even when its exception is unused', () => {
  const exceptions = clone(routerExceptionFixture);
  exceptions.exceptions.push({
    advisoryId: 'GHSA-1111-2222-4444',
    rootPackage: 'unused-root',
    advisoryUrl: 'https://github.com/advisories/GHSA-1111-2222-4444',
    reason: 'Synthetic unused exception for complete policy dispatch validation.',
    expiresAt: '2026-08-22T00:00:00Z',
    usagePolicy: 'unknown-future-policy',
    records: [{
      package: 'unused-root',
      version: '1.0.0',
      isDirect: true,
      causes: ['GHSA-1111-2222-4444'],
      effects: [],
    }],
  });
  let routerCalls = 0;
  const result = fakeRun({
    report: routerFixture,
    exceptions,
    checkRouterRscUsageImpl() {
      routerCalls += 1;
      return [];
    },
  });

  assert.equal(result.exitCode, 2);
  assert.equal(routerCalls, 0);
  assert.equal(result.stdout, '');
  assert.equal(result.stderr, 'ERROR {"code":"E_USAGE_POLICY"}\n');
});

test('prints accepted reasons as one JSON-escaped physical line', () => {
  const exceptions = clone(routerExceptionFixture);
  const reason = 'Reviewed reason with "quotes"\nand separators\u2028between\u2029lines';
  exceptions.exceptions[0].reason = reason;
  const result = fakeRun({ report: routerFixture, exceptions });

  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout.startsWith(acceptedLine(reason)), true);
  assert.equal(result.stdout.split('\n').length, 3);
  assert.equal(result.stdout.includes('"quotes"\nand'), false);
  assert.equal(result.stdout.includes('\\"quotes\\"\\nand separators'), true);
  assert.equal(result.stdout.includes('\u2028'), false);
  assert.equal(result.stdout.includes('\u2029'), false);
  assert.equal(result.stdout.includes('\\u2028'), true);
  assert.equal(result.stdout.includes('\\u2029'), true);
});

test('sorts blocked JSON summaries and never emits raw child stderr or unsafe values', () => {
  const report = clone(unexpectedFixture);
  const first = clone(report.vulnerabilities['synthetic-vulnerable']);
  const second = clone(first);
  first.name = 'zeta';
  first.via[0].name = 'zeta';
  first.via[0].dependency = 'zeta';
  first.via[0].url = 'https://github.com/advisories/GHSA-zzzz-yyyy-xxxx';
  first.nodes = ['node_modules/zeta'];
  second.name = 'alpha\nENV_SECRET';
  second.via[0].name = second.name;
  second.via[0].dependency = second.name;
  second.via[0].url = 'https://github.com/advisories/GHSA-1111-2222-3333';
  second.nodes = [`node_modules/${second.name}`];
  report.vulnerabilities = { zeta: first, [second.name]: second };
  report.metadata.vulnerabilities.high = 2;
  report.metadata.vulnerabilities.total = 2;
  const lockfile = clone(lockFixture);
  lockfile.packages['node_modules/zeta'] = { version: '1.0.0' };
  lockfile.packages[`node_modules/${second.name}`] = { version: '1.0.0' };

  const result = fakeRun({
    report,
    lockfile,
    auditStderr: '/outside/repo SOURCE_BODY child stderr',
  });

  assert.equal(result.exitCode, 1);
  assert.equal(result.stderr, '');
  const lines = result.stdout.trimEnd().split('\n');
  assert.equal(lines.length, 3);
  assert.equal(lines[0].startsWith('BLOCKED {"package":"alpha\\nENV_SECRET"'), true);
  assert.equal(lines[1].startsWith('BLOCKED {"package":"zeta"'), true);
  assert.equal(result.stdout.includes('/outside/repo'), false);
  assert.equal(result.stdout.includes('SOURCE_BODY'), false);
  assert.equal(result.stdout.includes('child stderr'), false);
  assert.equal(result.stdout.includes('alpha\nENV_SECRET'), false);
});

test('maps malformed JSON, filesystem, and subprocess failures to redacted operational exit two', async (t) => {
  await t.test('malformed audit JSON', () => {
    const result = fakeRun({
      report: '{not-json',
      auditStatus: 0,
      auditStderr: '/outside/repo ENV_SECRET',
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, 'ERROR {"code":"E_AUDIT_JSON"}\n');
  });

  await t.test('fixed-file read failure', () => {
    const result = fakeRun({
      readFileSyncImpl() {
        throw new Error('/outside/repo ENV_SECRET');
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, 'ERROR {"code":"E_INPUT_READ"}\n');
  });

  await t.test('npm spawn failure', () => {
    const result = fakeRun({
      spawnSyncImpl() {
        return {
          error: Object.assign(new Error('/outside/repo ENV_SECRET'), { code: 'EACCES' }),
          signal: 'SIGTERM',
          status: null,
          stdout: '',
          stderr: 'SOURCE_BODY',
        };
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, 'ERROR {"code":"E_NPM_SPAWN"}\n');
  });
});

test('the real executable rejects extra arguments before npm, file, Git, or TypeScript work', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'check-npm-audit-argv-'));
  try {
    const marker = path.join(directory, 'boundary-used');
    const preload = path.join(directory, 'deny-boundaries.cjs');
    const cliPath = path.join(root, 'scripts/check-npm-audit.mjs');
    fs.writeFileSync(preload, `
const fs = require('node:fs');
const childProcess = require('node:child_process');
const moduleApi = require('node:module');
const marker = ${JSON.stringify(marker)};
const originalRead = fs.readFileSync;
const originalRealpath = fs.realpathSync;
const originalStat = fs.statSync;
function mark(name) { fs.writeFileSync(marker, name); }
fs.readFileSync = function(file, ...args) {
  const value = String(file);
  if (value.endsWith('apps/web/package-lock.json') || value.endsWith('.github/security/npm-audit-exceptions.json')) {
    mark('read');
    throw new Error('forbidden read');
  }
  return originalRead.call(this, file, ...args);
};
fs.realpathSync = function(file, ...args) {
  if (String(file) === process.execPath) mark('realpath');
  return originalRealpath.call(this, file, ...args);
};
fs.statSync = function(file, ...args) {
  if (String(file).endsWith('npm-cli.js')) mark('stat');
  return originalStat.call(this, file, ...args);
};
childProcess.spawnSync = function() { mark('spawn'); throw new Error('forbidden spawn'); };
childProcess.execFileSync = function() { mark('git'); throw new Error('forbidden git'); };
moduleApi.createRequire = function() { mark('typescript'); throw new Error('forbidden TypeScript resolution'); };
moduleApi.syncBuiltinESMExports();
`);

    const result = spawnSync(process.execPath, [
      '--require',
      preload,
      cliPath,
      'unexpected-argument',
    ], {
      cwd: directory,
      encoding: 'utf8',
      shell: false,
      timeout: 30_000,
    });

    assert.equal(result.status, 2, result.stderr);
    assert.equal(result.signal, null);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, 'ERROR {"code":"E_ARGUMENTS"}\n');
    assert.equal(fs.existsSync(marker), false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('the zero-argument executable supplies the real current Date to policy evaluation', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'check-npm-audit-clock-'));
  try {
    const preload = path.join(directory, 'controlled-clock-and-npm.cjs');
    const cliPath = path.join(root, 'scripts/check-npm-audit.mjs');
    fs.writeFileSync(preload, `
const fs = require('node:fs');
const childProcess = require('node:child_process');
const moduleApi = require('node:module');
const originalRead = fs.readFileSync;
const policyPath = ${JSON.stringify(path.join(root, '.github/security/npm-audit-exceptions.json'))};
const exceptionPolicy = ${JSON.stringify(JSON.stringify(routerExceptionFixture))};
fs.readFileSync = function(file, ...args) {
  if (String(file) === policyPath) return exceptionPolicy;
  return originalRead.call(this, file, ...args);
};
const RealDate = Date;
global.Date = class ControlledDate extends RealDate {
  constructor(...args) {
    super(...(args.length === 0 ? ['2026-08-22T00:00:00.000Z'] : args));
  }
  static now() { return RealDate.parse('2026-08-22T00:00:00.000Z'); }
};
const report = ${JSON.stringify(JSON.stringify(routerFixture))};
childProcess.spawnSync = function(_command, args) {
  if (args.length === 2 && args[1] === '--version') {
    return { status: 0, signal: null, stdout: '11.19.0\\n', stderr: '' };
  }
  if (JSON.stringify(args.slice(1)) === JSON.stringify([
    'audit', '--package-lock-only', '--audit-level=high', '--json'
  ])) {
    return { status: 1, signal: null, stdout: report, stderr: '' };
  }
  return { status: 9, signal: null, stdout: '', stderr: '' };
};
moduleApi.syncBuiltinESMExports();
`);

    const result = spawnSync(process.execPath, [
      '--require',
      preload,
      cliPath,
    ], {
      cwd: directory,
      encoding: 'utf8',
      shell: false,
      timeout: 30_000,
    });

    assert.equal(result.status, 1, result.stderr);
    assert.equal(result.signal, null);
    assert.equal(result.stderr, '');
    assert.equal(result.stdout, [
      `BLOCKED ${JSON.stringify({
        package: 'react-router',
        version: '7.18.2',
        severity: 'high',
        advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
        reason: 'exception-expired',
      })}`,
      `BLOCKED ${JSON.stringify({
        package: 'react-router-dom',
        version: '7.18.2',
        severity: 'high',
        advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
        reason: 'exception-expired',
      })}`,
      'SUMMARY {"accepted":0,"blocked":2,"ignored":{"info":0,"low":0,"moderate":0}}',
      '',
    ].join('\n'));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
