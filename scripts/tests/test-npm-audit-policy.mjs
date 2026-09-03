import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  NpmAuditPolicyError,
  assessNpmAudit,
  assertSupportedNpmVersion,
} from '../lib/npm-audit-policy.mjs';

const root = path.resolve(import.meta.dirname, '../..');
const fixtureDirectory = path.join(import.meta.dirname, 'fixtures/npm-audit');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const cleanFixture = readJson(path.join(fixtureDirectory, 'audit-clean.json'));
const routerFixture = readJson(path.join(fixtureDirectory, 'audit-router-7.18.2.json'));
const unexpectedFixture = readJson(path.join(fixtureDirectory, 'audit-unexpected-high.json'));
const lockFixture = readJson(path.join(fixtureDirectory, 'package-lock.json'));
const productionExceptionPolicyPath = path.join(
  root,
  '.github/security/npm-audit-exceptions.json',
);
const productionExceptionPolicyText = fs.readFileSync(productionExceptionPolicyPath, 'utf8');
const productionExceptionPolicy = JSON.parse(productionExceptionPolicyText);
const routerExceptionFixture = readJson(
  path.join(fixtureDirectory, 'exceptions-router-7.18.2.json'),
);

const beforeExpiry = new Date('2026-08-21T23:59:59.999Z');
const atExpiry = new Date('2026-08-22T00:00:00.000Z');
const afterExpiry = new Date('2026-08-22T00:00:00.001Z');

const clone = (value) => structuredClone(value);

function metadataFor(records) {
  const vulnerabilities = {
    info: 0,
    low: 0,
    moderate: 0,
    high: 0,
    critical: 0,
    total: records.length,
  };
  for (const record of records) {
    vulnerabilities[record.severity] += 1;
  }
  return vulnerabilities;
}

function replaceRecords(report, records) {
  report.vulnerabilities = Object.fromEntries(records.map((record) => [record.name, record]));
  report.metadata.vulnerabilities = metadataFor(records);
  return report;
}

function advisoryRecord(name, severity) {
  return {
    name,
    severity,
    isDirect: true,
    via: [{
      source: 1000002,
      name,
      dependency: name,
      title: `Synthetic ${severity}-severity advisory`,
      url: 'https://github.com/advisories/GHSA-dddd-eeee-ffff',
      severity,
      cwe: ['CWE-20'],
      cvss: { score: severity === 'info' ? 0 : 5, vectorString: null },
      range: '<2.0.0',
    }],
    effects: [],
    range: '<2.0.0',
    nodes: [`node_modules/${name}`],
    fixAvailable: false,
  };
}

function assess(report, overrides = {}) {
  return assessNpmAudit({
    npmVersion: '11.19.0',
    exitCode: 0,
    stdout: typeof report === 'string' ? report : JSON.stringify(report),
    lockfile: clone(lockFixture),
    exceptionPolicy: clone(routerExceptionFixture),
    now: beforeExpiry,
    ...overrides,
  });
}

function assertPolicyError(code, callback) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof NpmAuditPolicyError);
    assert.equal(error.code, code);
    return true;
  });
}

function acceptedRouterRecords() {
  return [
    {
      package: 'react-router',
      severity: 'high',
      version: '7.18.2',
      advisoryId: 'GHSA-qwww-vcr4-c8h2',
      usagePolicy: 'router-rsc-absent',
      expiresAt: '2026-08-22T00:00:00Z',
    },
    {
      package: 'react-router-dom',
      severity: 'high',
      version: '7.18.2',
      advisoryId: 'GHSA-qwww-vcr4-c8h2',
      usagePolicy: 'router-rsc-absent',
      expiresAt: '2026-08-22T00:00:00Z',
    },
  ];
}

test('supported npm versions are limited to npm 11 releases', () => {
  assert.doesNotThrow(() => assertSupportedNpmVersion('11.0.0'));
  assert.doesNotThrow(() => assertSupportedNpmVersion('11.19.0'));
  assert.doesNotThrow(() => assertSupportedNpmVersion('11.999.999'));
  // 10.9.8 is the npm Node 22 shipped: the version this gate accepted before the Node 26 pin, and
  // the one a stale local toolchain would still present. It must now be REFUSED, not tolerated.
  for (const version of ['10.9.8', '10.9.0', '12.0.0', '11.0', 'v11.0.0', '', null, 11]) {
    assertPolicyError('E_NPM_VERSION', () => assertSupportedNpmVersion(version));
  }
});

test('the production exception policy is exactly empty', () => {
  assert.equal(productionExceptionPolicyText, [
    '{',
    '  "schemaVersion": 1,',
    '  "exceptions": []',
    '}',
    '',
  ].join('\n'));
  assert.deepEqual(productionExceptionPolicy, {
    schemaVersion: 1,
    exceptions: [],
  });
});

test('the production policy blocks the former Router pair as unapproved', () => {
  assert.deepEqual(assess(routerFixture, {
    exitCode: 1,
    exceptionPolicy: clone(productionExceptionPolicy),
  }), {
    accepted: [],
    blocked: [
      {
        package: 'react-router',
        severity: 'high',
        version: '7.18.2',
        advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
        reason: 'unapproved-high-or-critical',
      },
      {
        package: 'react-router-dom',
        severity: 'high',
        version: '7.18.2',
        advisoryIds: ['react-router'],
        reason: 'unapproved-high-or-critical',
      },
    ],
    ignored: { info: 0, low: 0, moderate: 0 },
  });
});

test('a clean v2 report with status zero is accepted', () => {
  assert.deepEqual(assess(cleanFixture), {
    accepted: [],
    blocked: [],
    ignored: { info: 0, low: 0, moderate: 0 },
  });
});

test('valid low and moderate records are counted but do not block', () => {
  const report = clone(cleanFixture);
  const lockfile = clone(lockFixture);
  const low = advisoryRecord('synthetic-low', 'low');
  const moderate = advisoryRecord('synthetic-moderate', 'moderate');
  replaceRecords(report, [low, moderate]);
  lockfile.packages['node_modules/synthetic-low'] = { version: '1.0.0' };
  lockfile.packages['node_modules/synthetic-moderate'] = { version: '1.0.0' };

  assert.deepEqual(assess(report, { lockfile }), {
    accepted: [],
    blocked: [],
    ignored: { info: 0, low: 1, moderate: 1 },
  });
});

test('an unexpected high record is blocked with a stable reason', () => {
  assert.deepEqual(assess(unexpectedFixture, { exitCode: 1 }), {
    accepted: [],
    blocked: [{
      package: 'synthetic-vulnerable',
      severity: 'high',
      version: '1.0.0',
      advisoryIds: ['GHSA-aaaa-bbbb-cccc'],
      reason: 'unapproved-high-or-critical',
    }],
    ignored: { info: 0, low: 0, moderate: 0 },
  });
});

test('an unexpected critical record is blocked', () => {
  const report = clone(unexpectedFixture);
  report.vulnerabilities['synthetic-vulnerable'].severity = 'critical';
  report.vulnerabilities['synthetic-vulnerable'].via[0].severity = 'critical';
  report.metadata.vulnerabilities.high = 0;
  report.metadata.vulnerabilities.critical = 1;

  assert.equal(assess(report, { exitCode: 1 }).blocked[0].reason, 'unapproved-high-or-critical');
});

test('the exact atomic Router pair is accepted before expiry', () => {
  assert.deepEqual(assess(routerFixture, { exitCode: 1 }), {
    accepted: acceptedRouterRecords(),
    blocked: [],
    ignored: { info: 0, low: 0, moderate: 0 },
  });
});

test('a direct advisory object cannot be replaced by an inherited package string', () => {
  const report = clone(routerFixture);
  const lockfile = clone(lockFixture);
  const advisoryIdPackage = advisoryRecord('GHSA-qwww-vcr4-c8h2', 'high');
  report.vulnerabilities['react-router'].via = ['GHSA-qwww-vcr4-c8h2'];
  replaceRecords(report, [...Object.values(report.vulnerabilities), advisoryIdPackage]);
  lockfile.packages['node_modules/GHSA-qwww-vcr4-c8h2'] = { version: '1.0.0' };

  const result = assess(report, { exitCode: 1, lockfile });
  assert.deepEqual(result.accepted, []);
  assert.deepEqual(
    result.blocked.filter(({ package: name }) => name.startsWith('react-router'))
      .map(({ package: name, reason }) => ({ name, reason })),
    [
      { name: 'react-router', reason: 'exception-record-mismatch' },
      { name: 'react-router-dom', reason: 'exception-record-mismatch' },
    ],
  );
});

test('an inherited package string cannot be replaced by a direct advisory object', () => {
  const report = clone(routerFixture);
  report.vulnerabilities['react-router-dom'].via = [
    clone(report.vulnerabilities['react-router'].via[0]),
  ];

  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, []);
  assert.deepEqual(result.blocked.map(({ package: name, reason }) => ({ name, reason })), [
    { name: 'react-router', reason: 'exception-record-mismatch' },
    { name: 'react-router-dom', reason: 'exception-record-mismatch' },
  ]);
});

test('a critical severity change blocks the formerly exact Router pair', () => {
  const report = clone(routerFixture);
  for (const record of Object.values(report.vulnerabilities)) record.severity = 'critical';
  report.vulnerabilities['react-router'].via[0].severity = 'critical';
  report.metadata.vulnerabilities.high = 0;
  report.metadata.vulnerabilities.critical = 2;

  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, []);
  assert.ok(result.blocked.every(({ reason }) => reason === 'exception-record-mismatch'));
});

test('each Router lock version mutation blocks the atomic exception', async (t) => {
  for (const packageName of ['react-router', 'react-router-dom']) {
    await t.test(packageName, () => {
      const lockfile = clone(lockFixture);
      lockfile.packages[`node_modules/${packageName}`].version = '7.18.1';
      const result = assess(routerFixture, { exitCode: 1, lockfile });
      assert.deepEqual(result.accepted, []);
      assert.deepEqual(new Set(result.blocked.map(({ reason }) => reason)), new Set([
        'exception-record-mismatch',
      ]));
      assert.deepEqual(result.blocked.map(({ package: name }) => name), [
        'react-router',
        'react-router-dom',
      ]);
    });
  }
});

test('a missing inherited Router record blocks the remaining half', () => {
  const report = clone(routerFixture);
  replaceRecords(report, [report.vulnerabilities['react-router']]);
  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, []);
  assert.equal(result.blocked[0].package, 'react-router');
  assert.equal(result.blocked[0].reason, 'exception-record-mismatch');
});

test('an additional object cause blocks the Router exception', () => {
  const report = clone(routerFixture);
  report.vulnerabilities['react-router'].via.push({
    ...advisoryRecord('react-router', 'high').via[0],
    url: 'https://github.com/advisories/GHSA-1111-2222-3333',
  });
  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, []);
  assert.ok(result.blocked.every(({ reason }) => reason === 'exception-record-mismatch'));
});

test('an unresolved inherited package cause fails closed', () => {
  const report = clone(routerFixture);
  report.vulnerabilities['react-router-dom'].via.push('unexpected-root');
  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report, { exitCode: 1 }));
});

test('an additional resolved inherited package cause blocks the Router exception', () => {
  const report = clone(routerFixture);
  const lockfile = clone(lockFixture);
  const unexpectedRoot = advisoryRecord('unexpected-root', 'high');
  report.vulnerabilities['react-router-dom'].via.push('unexpected-root');
  replaceRecords(report, [...Object.values(report.vulnerabilities), unexpectedRoot]);
  lockfile.packages['node_modules/unexpected-root'] = { version: '1.0.0' };

  const result = assess(report, { exitCode: 1, lockfile });
  assert.deepEqual(result.accepted, []);
  assert.deepEqual(
    result.blocked.filter(({ package: name }) => name.startsWith('react-router'))
      .map(({ package: name, reason }) => ({ name, reason })),
    [
      { name: 'react-router', reason: 'exception-record-mismatch' },
      { name: 'react-router-dom', reason: 'exception-record-mismatch' },
    ],
  );
});

test('an additional effect blocks the Router exception', () => {
  const report = clone(routerFixture);
  report.vulnerabilities['react-router'].effects.push('unexpected-dependent');
  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, []);
  assert.ok(result.blocked.every(({ reason }) => reason === 'exception-record-mismatch'));
});

test('a missing lock node fails closed', async (t) => {
  for (const packageName of ['react-router', 'react-router-dom']) {
    await t.test(packageName, () => {
      const lockfile = clone(lockFixture);
      delete lockfile.packages[`node_modules/${packageName}`];
      assertPolicyError('E_LOCK_SCHEMA', () => assess(routerFixture, {
        exitCode: 1,
        lockfile,
      }));
    });
  }
});

test('an unrelated high beside the exact Router pair remains blocked', () => {
  const report = clone(routerFixture);
  const unexpected = clone(unexpectedFixture.vulnerabilities['synthetic-vulnerable']);
  replaceRecords(report, [...Object.values(report.vulnerabilities), unexpected]);
  const result = assess(report, { exitCode: 1 });
  assert.deepEqual(result.accepted, acceptedRouterRecords());
  assert.deepEqual(result.blocked.map(({ package: name, reason }) => ({ name, reason })), [{
    name: 'synthetic-vulnerable',
    reason: 'unapproved-high-or-critical',
  }]);
});

test('an unused exception is accepted before, exactly at, and after expiry', () => {
  for (const now of [beforeExpiry, atExpiry, afterExpiry]) {
    assert.deepEqual(assess(cleanFixture, { now }), {
      accepted: [],
      blocked: [],
      ignored: { info: 0, low: 0, moderate: 0 },
    });
  }
});

test('the Router exception expires at an exclusive instant', () => {
  assert.deepEqual(assess(routerFixture, { exitCode: 1, now: beforeExpiry }).blocked, []);
  for (const now of [atExpiry, afterExpiry]) {
    const result = assess(routerFixture, { exitCode: 1, now });
    assert.deepEqual(result.accepted, []);
    assert.deepEqual(result.blocked, [
      {
        package: 'react-router',
        severity: 'high',
        version: '7.18.2',
        advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
        reason: 'exception-expired',
      },
      {
        package: 'react-router-dom',
        severity: 'high',
        version: '7.18.2',
        advisoryIds: ['GHSA-qwww-vcr4-c8h2'],
        reason: 'exception-expired',
      },
    ]);
  }
});

test('direct advisory severity must agree with the top-level record severity', () => {
  const report = clone(unexpectedFixture);
  report.vulnerabilities['synthetic-vulnerable'].severity = 'low';
  report.vulnerabilities['synthetic-vulnerable'].via[0].severity = 'critical';
  report.metadata.vulnerabilities.high = 0;
  report.metadata.vulnerabilities.low = 1;

  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report));
});

test('inherited severity must agree with the recursively resolved cause', () => {
  const report = clone(routerFixture);
  report.vulnerabilities['react-router-dom'].severity = 'low';
  report.metadata.vulnerabilities.high = 1;
  report.metadata.vulnerabilities.low = 1;

  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report, { exitCode: 1 }));
});

test('cyclic inherited vulnerability causes fail closed', () => {
  const report = clone(cleanFixture);
  const lockfile = clone(lockFixture);
  const first = { ...advisoryRecord('cycle-first', 'high'), via: ['cycle-second'] };
  const second = { ...advisoryRecord('cycle-second', 'high'), via: ['cycle-first'] };
  replaceRecords(report, [first, second]);
  lockfile.packages['node_modules/cycle-first'] = { version: '1.0.0' };
  lockfile.packages['node_modules/cycle-second'] = { version: '1.0.0' };

  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report, { exitCode: 1, lockfile }));
});

test('impossible exception calendar dates fail closed whether consumed or dormant', () => {
  const exceptionPolicy = clone(routerExceptionFixture);
  exceptionPolicy.exceptions[0].expiresAt = '2026-09-31T00:00:00Z';

  assertPolicyError('E_EXCEPTION_SCHEMA', () => assess(cleanFixture, { exceptionPolicy }));
  assertPolicyError('E_EXCEPTION_SCHEMA', () => assess(routerFixture, {
    exitCode: 1,
    exceptionPolicy,
  }));
});

test('exception expiry accepts UTC seconds and exactly three fractional digits', () => {
  const exceptionPolicy = clone(routerExceptionFixture);
  exceptionPolicy.exceptions[0].expiresAt = '2026-08-22T00:00:00.123Z';
  assert.deepEqual(assess(routerFixture, { exitCode: 1, exceptionPolicy }).blocked, []);
  assert.deepEqual(assess(routerFixture, { exitCode: 1 }).blocked, []);
});

test('unsupported report and lock versions fail closed', () => {
  const report = clone(cleanFixture);
  report.auditReportVersion = 3;
  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report));

  const lockfile = clone(lockFixture);
  lockfile.lockfileVersion = 2;
  assertPolicyError('E_LOCK_SCHEMA', () => assess(cleanFixture, { lockfile }));
});

test('malformed audit JSON fails closed', () => {
  assertPolicyError('E_AUDIT_JSON', () => assess('{not-json'));
});

test('unsupported exit statuses fail closed', () => {
  assertPolicyError('E_EXIT_STATUS', () => assess(cleanFixture, { exitCode: 2 }));
  assertPolicyError('E_EXIT_STATUS', () => assess(cleanFixture, { exitCode: '0' }));
});

test('exit status and high or critical findings must agree', () => {
  assertPolicyError('E_STATUS_REPORT', () => assess(unexpectedFixture, { exitCode: 0 }));
  assertPolicyError('E_STATUS_REPORT', () => assess(cleanFixture, { exitCode: 1 }));
});

test('metadata vulnerability counts must agree with records', () => {
  const report = clone(routerFixture);
  report.metadata.vulnerabilities.high = 1;
  assertPolicyError('E_AUDIT_SCHEMA', () => assess(report, { exitCode: 1 }));
});

test('required audit report and record fields reject wrong types', async (t) => {
  const cases = [
    ['vulnerabilities', (report) => { report.vulnerabilities = []; }],
    ['metadata', (report) => { report.metadata = null; }],
    ['metadata vulnerabilities', (report) => { report.metadata.vulnerabilities = []; }],
    ['metadata count', (report) => { report.metadata.vulnerabilities.high = '2'; }],
    ['metadata dependencies', (report) => { report.metadata.dependencies = []; }],
    ['dependency count', (report) => { report.metadata.dependencies.prod = -1; }],
    ['record name', (report) => { report.vulnerabilities['react-router'].name = 7; }],
    ['record name agreement', (report) => { report.vulnerabilities['react-router'].name = 'other'; }],
    ['severity', (report) => { report.vulnerabilities['react-router'].severity = 'severe'; }],
    ['isDirect', (report) => { report.vulnerabilities['react-router'].isDirect = 'false'; }],
    ['via', (report) => { report.vulnerabilities['react-router'].via = {}; }],
    ['string cause', (report) => { report.vulnerabilities['react-router-dom'].via = [1]; }],
    ['effects', (report) => { report.vulnerabilities['react-router'].effects = 'react-router-dom'; }],
    ['range', (report) => { report.vulnerabilities['react-router'].range = null; }],
    ['nodes', (report) => { report.vulnerabilities['react-router'].nodes = 'node_modules/react-router'; }],
    ['node', (report) => { report.vulnerabilities['react-router'].nodes = [1]; }],
    ['fixAvailable scalar', (report) => { report.vulnerabilities['react-router'].fixAvailable = null; }],
    ['fixAvailable name', (report) => { report.vulnerabilities['react-router-dom'].fixAvailable.name = 1; }],
    ['fixAvailable version', (report) => { report.vulnerabilities['react-router-dom'].fixAvailable.version = null; }],
    ['fixAvailable isSemVerMajor', (report) => { report.vulnerabilities['react-router-dom'].fixAvailable.isSemVerMajor = 'true'; }],
    ['advisory source', (report) => { report.vulnerabilities['react-router'].via[0].source = '1117136'; }],
    ['advisory title', (report) => { report.vulnerabilities['react-router'].via[0].title = null; }],
    ['advisory URL', (report) => { report.vulnerabilities['react-router'].via[0].url = 1; }],
    ['advisory CWE', (report) => { report.vulnerabilities['react-router'].via[0].cwe = 'CWE-79'; }],
    ['advisory CVSS', (report) => { report.vulnerabilities['react-router'].via[0].cvss = null; }],
    ['advisory CVSS score', (report) => { report.vulnerabilities['react-router'].via[0].cvss.score = '8.1'; }],
    ['advisory CVSS vector', (report) => { report.vulnerabilities['react-router'].via[0].cvss.vectorString = 1; }],
    ['advisory range', (report) => { report.vulnerabilities['react-router'].via[0].range = false; }],
  ];

  for (const [name, mutate] of cases) {
    await t.test(name, () => {
      const report = clone(routerFixture);
      mutate(report);
      assertPolicyError('E_AUDIT_SCHEMA', () => assess(report, { exitCode: 1 }));
    });
  }
});

test('duplicate causes and effects fail closed', () => {
  const duplicateCause = clone(routerFixture);
  duplicateCause.vulnerabilities['react-router-dom'].via.push('react-router');
  assertPolicyError('E_AUDIT_SCHEMA', () => assess(duplicateCause, { exitCode: 1 }));

  const duplicateEffect = clone(routerFixture);
  duplicateEffect.vulnerabilities['react-router'].effects.push('react-router-dom');
  assertPolicyError('E_AUDIT_SCHEMA', () => assess(duplicateEffect, { exitCode: 1 }));
});

test('required lock fields and node versions reject wrong types', () => {
  for (const mutate of [
    (lockfile) => { lockfile.packages = []; },
    (lockfile) => { lockfile.packages['node_modules/react-router'] = null; },
    (lockfile) => { delete lockfile.packages['node_modules/react-router'].version; },
    (lockfile) => { lockfile.packages['node_modules/react-router'].version = 7.182; },
  ]) {
    const lockfile = clone(lockFixture);
    mutate(lockfile);
    assertPolicyError('E_LOCK_SCHEMA', () => assess(routerFixture, { exitCode: 1, lockfile }));
  }
});

test('the exception policy schema fails closed when malformed', async (t) => {
  const cases = [
    ['policy object', () => null],
    ['schema version', (policy) => { policy.schemaVersion = 2; }],
    ['exceptions', (policy) => { policy.exceptions = {}; }],
    ['exception object', (policy) => { policy.exceptions[0] = null; }],
    ['advisoryId', (policy) => { policy.exceptions[0].advisoryId = 1; }],
    ['rootPackage', (policy) => { policy.exceptions[0].rootPackage = ''; }],
    ['advisoryUrl', (policy) => { policy.exceptions[0].advisoryUrl = false; }],
    ['reason', (policy) => { policy.exceptions[0].reason = ''; }],
    ['expiresAt', (policy) => { policy.exceptions[0].expiresAt = 'not-a-date'; }],
    ['usagePolicy', (policy) => { policy.exceptions[0].usagePolicy = null; }],
    ['records', (policy) => { policy.exceptions[0].records = {}; }],
    ['record package', (policy) => { policy.exceptions[0].records[0].package = 1; }],
    ['record version', (policy) => { policy.exceptions[0].records[0].version = ''; }],
    ['record isDirect', (policy) => { policy.exceptions[0].records[0].isDirect = 'false'; }],
    ['record causes', (policy) => { policy.exceptions[0].records[0].causes = 'advisory'; }],
    ['record effects', (policy) => { policy.exceptions[0].records[0].effects = null; }],
    ['duplicate record package', (policy) => { policy.exceptions[0].records[1].package = 'react-router'; }],
    ['duplicate record cause', (policy) => { policy.exceptions[0].records[0].causes.push('GHSA-qwww-vcr4-c8h2'); }],
  ];

  for (const [name, mutate] of cases) {
    await t.test(name, () => {
      let policy = clone(routerExceptionFixture);
      const replacement = mutate(policy);
      if (replacement === null) policy = replacement;
      assertPolicyError('E_EXCEPTION_SCHEMA', () => assess(cleanFixture, {
        exceptionPolicy: policy,
      }));
    });
  }
});

test('the injected clock must be a valid Date', () => {
  assertPolicyError('E_CLOCK', () => assess(cleanFixture, { now: new Date('invalid') }));
  assertPolicyError('E_CLOCK', () => assess(cleanFixture, { now: '2026-08-21T00:00:00Z' }));
});

test('unknown extra fields are tolerated at every validated boundary', () => {
  const report = clone(routerFixture);
  const lockfile = clone(lockFixture);
  const exceptionPolicy = clone(routerExceptionFixture);
  report.futureReportField = true;
  report.metadata.futureMetadataField = true;
  report.vulnerabilities['react-router'].futureRecordField = true;
  report.vulnerabilities['react-router'].via[0].futureAdvisoryField = true;
  report.vulnerabilities['react-router-dom'].fixAvailable.futureFixField = true;
  lockfile.futureLockField = true;
  lockfile.packages['node_modules/react-router'].futurePackageField = true;
  exceptionPolicy.futurePolicyField = true;
  exceptionPolicy.exceptions[0].futureExceptionField = true;
  exceptionPolicy.exceptions[0].records[0].futureRecordField = true;

  assert.deepEqual(assess(report, {
    exitCode: 1,
    lockfile,
    exceptionPolicy,
  }).accepted, acceptedRouterRecords());
});
