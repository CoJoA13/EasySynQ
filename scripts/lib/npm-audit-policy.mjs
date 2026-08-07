const SUPPORTED_NPM_VERSION = /^10\.9\.\d+$/;
const GHSA_ID = /^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$/;
const RFC3339_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const SEVERITIES = ['info', 'low', 'moderate', 'high', 'critical'];
const BLOCKING_SEVERITIES = new Set(['high', 'critical']);
const METADATA_DEPENDENCY_COUNTS = [
  'prod',
  'dev',
  'optional',
  'peer',
  'peerOptional',
  'total',
];

export class NpmAuditPolicyError extends Error {
  code;

  constructor(code, message) {
    super(message);
    this.name = 'NpmAuditPolicyError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new NpmAuditPolicyError(code, message);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.length > 0;
}

function assertObject(value, code, path) {
  if (!isObject(value)) fail(code, `${path} must be an object`);
}

function assertNonEmptyString(value, code, path) {
  if (!isNonEmptyString(value)) fail(code, `${path} must be a non-empty string`);
}

function assertNonNegativeInteger(value, code, path) {
  if (!Number.isInteger(value) || value < 0) {
    fail(code, `${path} must be a non-negative integer`);
  }
}

function assertUniqueStringArray(value, code, path, { allowEmpty = true } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    fail(code, `${path} must be ${allowEmpty ? 'an' : 'a non-empty'} array`);
  }
  const seen = new Set();
  for (const [index, item] of value.entries()) {
    assertNonEmptyString(item, code, `${path}[${index}]`);
    if (seen.has(item)) fail(code, `${path} must not contain duplicates`);
    seen.add(item);
  }
  return seen;
}

export function assertSupportedNpmVersion(npmVersion) {
  if (typeof npmVersion !== 'string' || !SUPPORTED_NPM_VERSION.test(npmVersion)) {
    fail('E_NPM_VERSION', 'npm version must match 10.9.x');
  }
}

function parseAuditReport(stdout) {
  if (typeof stdout !== 'string') fail('E_AUDIT_JSON', 'npm audit stdout must be a string');
  try {
    return JSON.parse(stdout);
  } catch {
    fail('E_AUDIT_JSON', 'npm audit stdout is not valid JSON');
  }
}

function advisoryIdFromUrl(url, source) {
  const match = url.match(/(?:^|\/)(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})(?:$|[/?#])/);
  return match?.[1] ?? `source:${source}`;
}

function validateAdvisory(value, path) {
  assertObject(value, 'E_AUDIT_SCHEMA', path);
  if (!Number.isInteger(value.source) || value.source < 0) {
    fail('E_AUDIT_SCHEMA', `${path}.source must be a non-negative integer`);
  }
  for (const field of ['name', 'dependency', 'title', 'url', 'range']) {
    assertNonEmptyString(value[field], 'E_AUDIT_SCHEMA', `${path}.${field}`);
  }
  if (!SEVERITIES.includes(value.severity)) {
    fail('E_AUDIT_SCHEMA', `${path}.severity is unsupported`);
  }
  assertUniqueStringArray(value.cwe, 'E_AUDIT_SCHEMA', `${path}.cwe`);
  assertObject(value.cvss, 'E_AUDIT_SCHEMA', `${path}.cvss`);
  if (typeof value.cvss.score !== 'number' || !Number.isFinite(value.cvss.score)) {
    fail('E_AUDIT_SCHEMA', `${path}.cvss.score must be a finite number`);
  }
  if (value.cvss.vectorString !== null && typeof value.cvss.vectorString !== 'string') {
    fail('E_AUDIT_SCHEMA', `${path}.cvss.vectorString must be a string or null`);
  }
  return advisoryIdFromUrl(value.url, value.source);
}

function validateFixAvailable(value, path) {
  if (typeof value === 'boolean') return;
  assertObject(value, 'E_AUDIT_SCHEMA', path);
  assertNonEmptyString(value.name, 'E_AUDIT_SCHEMA', `${path}.name`);
  assertNonEmptyString(value.version, 'E_AUDIT_SCHEMA', `${path}.version`);
  if (typeof value.isSemVerMajor !== 'boolean') {
    fail('E_AUDIT_SCHEMA', `${path}.isSemVerMajor must be a boolean`);
  }
}

function validateAuditRecord(key, value) {
  const path = `vulnerabilities.${key}`;
  assertObject(value, 'E_AUDIT_SCHEMA', path);
  assertNonEmptyString(value.name, 'E_AUDIT_SCHEMA', `${path}.name`);
  if (value.name !== key) fail('E_AUDIT_SCHEMA', `${path}.name must agree with its key`);
  if (!SEVERITIES.includes(value.severity)) {
    fail('E_AUDIT_SCHEMA', `${path}.severity is unsupported`);
  }
  if (typeof value.isDirect !== 'boolean') {
    fail('E_AUDIT_SCHEMA', `${path}.isDirect must be a boolean`);
  }
  if (!Array.isArray(value.via) || value.via.length === 0) {
    fail('E_AUDIT_SCHEMA', `${path}.via must be a non-empty array`);
  }
  const causes = [];
  for (const [index, cause] of value.via.entries()) {
    if (typeof cause === 'string') {
      assertNonEmptyString(cause, 'E_AUDIT_SCHEMA', `${path}.via[${index}]`);
      causes.push(cause);
    } else {
      causes.push(validateAdvisory(cause, `${path}.via[${index}]`));
    }
  }
  if (new Set(causes).size !== causes.length) {
    fail('E_AUDIT_SCHEMA', `${path}.via must not contain duplicate causes`);
  }
  const effects = [...assertUniqueStringArray(
    value.effects,
    'E_AUDIT_SCHEMA',
    `${path}.effects`,
  )];
  assertNonEmptyString(value.range, 'E_AUDIT_SCHEMA', `${path}.range`);
  const nodes = [...assertUniqueStringArray(
    value.nodes,
    'E_AUDIT_SCHEMA',
    `${path}.nodes`,
    { allowEmpty: false },
  )];
  validateFixAvailable(value.fixAvailable, `${path}.fixAvailable`);
  return {
    package: key,
    severity: value.severity,
    isDirect: value.isDirect,
    causes,
    effects,
    nodes,
  };
}

function validateAuditReport(report) {
  assertObject(report, 'E_AUDIT_SCHEMA', 'audit report');
  if (report.auditReportVersion !== 2) {
    fail('E_AUDIT_SCHEMA', 'auditReportVersion must be 2');
  }
  assertObject(report.vulnerabilities, 'E_AUDIT_SCHEMA', 'vulnerabilities');
  assertObject(report.metadata, 'E_AUDIT_SCHEMA', 'metadata');
  assertObject(
    report.metadata.vulnerabilities,
    'E_AUDIT_SCHEMA',
    'metadata.vulnerabilities',
  );
  assertObject(report.metadata.dependencies, 'E_AUDIT_SCHEMA', 'metadata.dependencies');

  const records = Object.entries(report.vulnerabilities).map(([key, value]) => {
    assertNonEmptyString(key, 'E_AUDIT_SCHEMA', 'vulnerability key');
    return validateAuditRecord(key, value);
  });
  const actualCounts = Object.fromEntries(SEVERITIES.map((severity) => [severity, 0]));
  for (const record of records) actualCounts[record.severity] += 1;

  for (const severity of SEVERITIES) {
    const count = report.metadata.vulnerabilities[severity];
    assertNonNegativeInteger(count, 'E_AUDIT_SCHEMA', `metadata.vulnerabilities.${severity}`);
    if (count !== actualCounts[severity]) {
      fail('E_AUDIT_SCHEMA', `metadata.vulnerabilities.${severity} does not match records`);
    }
  }
  assertNonNegativeInteger(
    report.metadata.vulnerabilities.total,
    'E_AUDIT_SCHEMA',
    'metadata.vulnerabilities.total',
  );
  if (report.metadata.vulnerabilities.total !== records.length) {
    fail('E_AUDIT_SCHEMA', 'metadata.vulnerabilities.total does not match records');
  }
  for (const field of METADATA_DEPENDENCY_COUNTS) {
    assertNonNegativeInteger(
      report.metadata.dependencies[field],
      'E_AUDIT_SCHEMA',
      `metadata.dependencies.${field}`,
    );
  }
  return records;
}

function lockPathMatchesPackage(node, packageName) {
  return node === `node_modules/${packageName}` || node.endsWith(`/node_modules/${packageName}`);
}

function attachLockedVersions(lockfile, records) {
  assertObject(lockfile, 'E_LOCK_SCHEMA', 'lockfile');
  if (lockfile.lockfileVersion !== 3) {
    fail('E_LOCK_SCHEMA', 'lockfileVersion must be 3');
  }
  assertObject(lockfile.packages, 'E_LOCK_SCHEMA', 'lockfile.packages');

  return records.map((record) => {
    const versions = new Set();
    for (const node of record.nodes) {
      if (!lockPathMatchesPackage(node, record.package)) {
        fail('E_LOCK_SCHEMA', `${node} does not resolve ${record.package}`);
      }
      if (!Object.hasOwn(lockfile.packages, node)) {
        fail('E_LOCK_SCHEMA', `lockfile is missing ${node}`);
      }
      const lockNode = lockfile.packages[node];
      assertObject(lockNode, 'E_LOCK_SCHEMA', `lockfile.packages.${node}`);
      assertNonEmptyString(lockNode.version, 'E_LOCK_SCHEMA', `lockfile.packages.${node}.version`);
      versions.add(lockNode.version);
    }
    return { ...record, versions: [...versions].sort() };
  });
}

function parseExpiry(value, path) {
  if (!isNonEmptyString(value) || !RFC3339_UTC.test(value)) {
    fail('E_EXCEPTION_SCHEMA', `${path} must be an RFC 3339 UTC timestamp`);
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    fail('E_EXCEPTION_SCHEMA', `${path} must be a valid timestamp`);
  }
  return timestamp;
}

function validateExceptionRecord(value, path) {
  assertObject(value, 'E_EXCEPTION_SCHEMA', path);
  assertNonEmptyString(value.package, 'E_EXCEPTION_SCHEMA', `${path}.package`);
  assertNonEmptyString(value.version, 'E_EXCEPTION_SCHEMA', `${path}.version`);
  if (typeof value.isDirect !== 'boolean') {
    fail('E_EXCEPTION_SCHEMA', `${path}.isDirect must be a boolean`);
  }
  const causes = assertUniqueStringArray(value.causes, 'E_EXCEPTION_SCHEMA', `${path}.causes`, {
    allowEmpty: false,
  });
  const effects = assertUniqueStringArray(value.effects, 'E_EXCEPTION_SCHEMA', `${path}.effects`);
  return {
    package: value.package,
    version: value.version,
    isDirect: value.isDirect,
    causes: [...causes],
    effects: [...effects],
  };
}

function validateExceptionPolicy(exceptionPolicy) {
  assertObject(exceptionPolicy, 'E_EXCEPTION_SCHEMA', 'exception policy');
  if (exceptionPolicy.schemaVersion !== 1) {
    fail('E_EXCEPTION_SCHEMA', 'exception policy schemaVersion must be 1');
  }
  if (!Array.isArray(exceptionPolicy.exceptions)) {
    fail('E_EXCEPTION_SCHEMA', 'exception policy exceptions must be an array');
  }

  const identities = new Set();
  return exceptionPolicy.exceptions.map((value, index) => {
    const path = `exceptions[${index}]`;
    assertObject(value, 'E_EXCEPTION_SCHEMA', path);
    for (const field of [
      'advisoryId',
      'rootPackage',
      'advisoryUrl',
      'reason',
      'usagePolicy',
    ]) {
      assertNonEmptyString(value[field], 'E_EXCEPTION_SCHEMA', `${path}.${field}`);
    }
    if (!GHSA_ID.test(value.advisoryId)) {
      fail('E_EXCEPTION_SCHEMA', `${path}.advisoryId must be a GHSA identifier`);
    }
    if (!value.advisoryUrl.startsWith('https://') || !value.advisoryUrl.endsWith(value.advisoryId)) {
      fail('E_EXCEPTION_SCHEMA', `${path}.advisoryUrl must identify the advisory over HTTPS`);
    }
    const expiresAtMs = parseExpiry(value.expiresAt, `${path}.expiresAt`);
    if (!Array.isArray(value.records) || value.records.length === 0) {
      fail('E_EXCEPTION_SCHEMA', `${path}.records must be a non-empty array`);
    }
    const records = value.records.map((record, recordIndex) => validateExceptionRecord(
      record,
      `${path}.records[${recordIndex}]`,
    ));
    const packages = new Set(records.map((record) => record.package));
    if (packages.size !== records.length) {
      fail('E_EXCEPTION_SCHEMA', `${path}.records must have unique packages`);
    }
    const rootRecord = records.find((record) => record.package === value.rootPackage);
    if (!rootRecord || !rootRecord.causes.includes(value.advisoryId)) {
      fail('E_EXCEPTION_SCHEMA', `${path}.rootPackage must identify the advisory root record`);
    }
    const identity = `${value.advisoryId}\0${value.rootPackage}`;
    if (identities.has(identity)) {
      fail('E_EXCEPTION_SCHEMA', 'exception identities must be unique');
    }
    identities.add(identity);
    return {
      advisoryId: value.advisoryId,
      rootPackage: value.rootPackage,
      expiresAt: value.expiresAt,
      expiresAtMs,
      usagePolicy: value.usagePolicy,
      records,
    };
  });
}

function sameSet(left, right) {
  return left.length === right.length && left.every((value) => right.includes(value));
}

function recordMatchesException(record, expected) {
  return record.package === expected.package
    && record.severity === 'high'
    && record.isDirect === expected.isDirect
    && record.versions.length === 1
    && record.versions[0] === expected.version
    && sameSet(record.causes, expected.causes)
    && sameSet(record.effects, expected.effects);
}

function blockedRecords(records, reason, advisoryIds) {
  return records.flatMap((record) => record.versions.map((version) => ({
    package: record.package,
    severity: record.severity,
    version,
    advisoryIds: advisoryIds ?? [...record.causes].sort(),
    reason,
  })));
}

function acceptedRecords(exception) {
  return exception.records.map((record) => ({
    package: record.package,
    severity: 'high',
    version: record.version,
    advisoryId: exception.advisoryId,
    usagePolicy: exception.usagePolicy,
    expiresAt: exception.expiresAt,
  }));
}

export function assessNpmAudit({
  npmVersion,
  exitCode,
  stdout,
  lockfile,
  exceptionPolicy,
  now,
}) {
  assertSupportedNpmVersion(npmVersion);
  if (exitCode !== 0 && exitCode !== 1) {
    fail('E_EXIT_STATUS', 'npm audit exit status must be zero or one');
  }
  const report = parseAuditReport(stdout);
  const records = attachLockedVersions(lockfile, validateAuditReport(report));
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    fail('E_CLOCK', 'now must be a valid Date');
  }
  const exceptions = validateExceptionPolicy(exceptionPolicy);

  const blockingRecords = records.filter((record) => BLOCKING_SEVERITIES.has(record.severity));
  if ((exitCode === 1) !== (blockingRecords.length > 0)) {
    fail('E_STATUS_REPORT', 'npm audit exit status contradicts high or critical findings');
  }

  const accepted = [];
  const blocked = [];
  const consumedPackages = new Set();

  for (const exception of exceptions) {
    const expectedPackages = new Set(exception.records.map((record) => record.package));
    const candidates = blockingRecords.filter((record) => expectedPackages.has(record.package));
    if (candidates.length === 0) continue;

    const actualByPackage = new Map(candidates.map((record) => [record.package, record]));
    const exact = candidates.length === exception.records.length
      && exception.records.every((expected) => {
        const record = actualByPackage.get(expected.package);
        return record !== undefined && recordMatchesException(record, expected);
      });

    for (const record of candidates) consumedPackages.add(record.package);
    if (!exact) {
      blocked.push(...blockedRecords(
        candidates,
        'exception-record-mismatch',
        [exception.advisoryId],
      ));
      continue;
    }
    if (now.getTime() >= exception.expiresAtMs) {
      blocked.push(...blockedRecords(
        candidates,
        'exception-expired',
        [exception.advisoryId],
      ));
      continue;
    }
    accepted.push(...acceptedRecords(exception));
  }

  const unapproved = blockingRecords.filter((record) => !consumedPackages.has(record.package));
  blocked.push(...blockedRecords(unapproved, 'unapproved-high-or-critical'));

  return {
    accepted,
    blocked,
    ignored: {
      info: report.metadata.vulnerabilities.info,
      low: report.metadata.vulnerabilities.low,
      moderate: report.metadata.vulnerabilities.moderate,
    },
  };
}
