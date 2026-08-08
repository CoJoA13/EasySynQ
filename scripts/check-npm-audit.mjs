import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  assessNpmAudit,
  assertSupportedNpmVersion,
} from './lib/npm-audit-policy.mjs';
import {
  getNpmVersion,
  resolveNpmCliPath,
  runNpmAudit,
} from './lib/npm-audit-runner.mjs';
import {
  ROUTER_RSC_USAGE_POLICY_ID,
  checkRouterRscUsage,
} from './lib/router-rsc-policy.mjs';

const REPO_ROOT = path.resolve(import.meta.dirname, '..');
const WEB_DIRECTORY = path.join(REPO_ROOT, 'apps/web');
const LOCKFILE_PATH = path.join(WEB_DIRECTORY, 'package-lock.json');
const EXCEPTION_PATH = path.join(
  REPO_ROOT,
  '.github/security/npm-audit-exceptions.json',
);

class NpmAuditCliError extends Error {
  code;

  constructor(code, message) {
    super(message);
    this.name = 'NpmAuditCliError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new NpmAuditCliError(code, message);
}

function readJson(file, readFileSyncImpl) {
  let contents;
  try {
    contents = readFileSyncImpl(file, 'utf8');
  } catch {
    fail('E_INPUT_READ', 'a fixed npm policy input could not be read');
  }
  if (typeof contents !== 'string') {
    fail('E_INPUT_READ', 'a fixed npm policy input returned malformed contents');
  }
  try {
    return JSON.parse(contents);
  } catch {
    fail('E_INPUT_JSON', 'a fixed npm policy input is not valid JSON');
  }
}

function compareText(left, right) {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function jsonForLine(value) {
  return JSON.stringify(value)
    .replaceAll('\u2028', '\\u2028')
    .replaceAll('\u2029', '\\u2029');
}

function acceptedExceptionGroups(exceptionPolicy, acceptedRecords) {
  const groups = [];
  const claimedRecordIndexes = new Set();
  for (const exception of exceptionPolicy.exceptions) {
    const recordIndexes = exception.records.map((expected) => acceptedRecords.findIndex(
      (record, index) => !claimedRecordIndexes.has(index)
        && record.advisoryId === exception.advisoryId
        && record.usagePolicy === exception.usagePolicy
        && record.expiresAt === exception.expiresAt
        && record.package === expected.package
        && record.version === expected.version,
    ));
    const rootRecordIndex = exception.records.findIndex(
      (record) => record.package === exception.rootPackage,
    );
    if (recordIndexes[rootRecordIndex] === -1) continue;
    if (recordIndexes.some((index) => index === -1)) {
      fail('E_EXCEPTION_JOIN', 'accepted audit records did not join to one complete exception');
    }
    for (const index of recordIndexes) claimedRecordIndexes.add(index);
    groups.push({
      exception,
      records: recordIndexes.map((index) => acceptedRecords[index]),
    });
  }
  if (claimedRecordIndexes.size !== acceptedRecords.length) {
    fail('E_EXCEPTION_JOIN', 'accepted audit records did not join to a validated exception');
  }
  return groups;
}

function validateUsagePolicies(exceptionPolicy) {
  for (const exception of exceptionPolicy.exceptions) {
    if (exception.usagePolicy !== ROUTER_RSC_USAGE_POLICY_ID) {
      fail('E_USAGE_POLICY', 'the exception policy contains an unknown usage policy');
    }
  }
}

function operationalCode(error) {
  if (error !== null
      && typeof error === 'object'
      && typeof error.code === 'string'
      && /^E_[A-Z0-9_]+$/.test(error.code)) {
    return error.code;
  }
  return 'E_OPERATIONAL';
}

function assessmentBlockedLine(record) {
  return `BLOCKED ${jsonForLine({
    package: record.package,
    version: record.version,
    severity: record.severity,
    advisoryIds: [...record.advisoryIds].sort(compareText),
    reason: record.reason,
  })}`;
}

function usageBlockedLine(group, violationCount) {
  return `BLOCKED ${jsonForLine({
    advisoryId: group.exception.advisoryId,
    usagePolicy: group.exception.usagePolicy,
    reason: 'usage-policy-violation',
    violations: violationCount,
  })}`;
}

function acceptedLine(group) {
  const packages = group.records
    .map((record) => `${record.package}@${record.version}`)
    .sort((left, right) => {
      const leftIsRoot = left.startsWith(`${group.exception.rootPackage}@`);
      const rightIsRoot = right.startsWith(`${group.exception.rootPackage}@`);
      if (leftIsRoot !== rightIsRoot) return leftIsRoot ? -1 : 1;
      return compareText(left, right);
    });
  return `ACCEPTED ${jsonForLine({
    advisoryId: group.exception.advisoryId,
    rootPackage: group.exception.rootPackage,
    packages,
    expiresAt: group.exception.expiresAt,
    usagePolicy: group.exception.usagePolicy,
    reason: group.exception.reason,
  })}`;
}

function formatOutput({ assessment, acceptedGroups, usageBlocked }) {
  const acceptedLines = acceptedGroups
    .map(acceptedLine)
    .sort(compareText);
  const blockedLines = [
    ...assessment.blocked.map(assessmentBlockedLine),
    ...usageBlocked.map(({ group, violations }) => usageBlockedLine(group, violations.length)),
  ].sort(compareText);
  const summary = `SUMMARY ${jsonForLine({
    accepted: acceptedGroups.length,
    blocked: blockedLines.length,
    ignored: assessment.ignored,
  })}`;
  return [...acceptedLines, ...blockedLines, summary, ''].join('\n');
}

export function main({
  spawnSyncImpl,
  now = new Date(),
  stdout = process.stdout,
  stderr = process.stderr,
  readFileSyncImpl = fs.readFileSync,
  checkRouterRscUsageImpl = checkRouterRscUsage,
  typescript,
  execFileSyncImpl,
} = {}) {
  try {
    const lockfile = readJson(LOCKFILE_PATH, readFileSyncImpl);
    const exceptionPolicy = readJson(EXCEPTION_PATH, readFileSyncImpl);
    const npmCliPath = resolveNpmCliPath();
    const npmVersion = getNpmVersion({
      nodeExecutable: process.execPath,
      npmCliPath,
      cwd: WEB_DIRECTORY,
      spawnSyncImpl,
    });
    assertSupportedNpmVersion(npmVersion);

    const audit = runNpmAudit({
      nodeExecutable: process.execPath,
      npmCliPath,
      webDirectory: WEB_DIRECTORY,
      cacheParent: os.tmpdir(),
      spawnSyncImpl,
    });
    const assessment = assessNpmAudit({
      npmVersion,
      exitCode: audit.exitCode,
      stdout: audit.stdout,
      lockfile,
      exceptionPolicy,
      now,
    });

    // assessNpmAudit validates the complete exception document before this policy dispatch.
    validateUsagePolicies(exceptionPolicy);
    const acceptedGroups = acceptedExceptionGroups(exceptionPolicy, assessment.accepted);
    const passedGroups = [];
    const usageBlocked = [];
    for (const group of acceptedGroups) {
      const violations = checkRouterRscUsageImpl({
        repoRoot: REPO_ROOT,
        typescript,
        execFileSyncImpl,
      });
      if (!Array.isArray(violations)) {
        fail('E_USAGE_POLICY_RESULT', 'the Router usage policy returned malformed results');
      }
      if (violations.length === 0) passedGroups.push(group);
      else usageBlocked.push({ group, violations });
    }

    stdout.write(formatOutput({
      assessment,
      acceptedGroups: passedGroups,
      usageBlocked,
    }));
    return assessment.blocked.length > 0 || usageBlocked.length > 0 ? 1 : 0;
  } catch (error) {
    stderr.write(`ERROR ${jsonForLine({ code: operationalCode(error) })}\n`);
    return 2;
  }
}

const entryPath = process.argv[1];
const isDirectInvocation = typeof entryPath === 'string'
  && path.resolve(entryPath) === fileURLToPath(import.meta.url);

if (isDirectInvocation) {
  if (process.argv.length !== 2) {
    process.stderr.write('ERROR {"code":"E_ARGUMENTS"}\n');
    process.exitCode = 2;
  } else {
    process.exitCode = main({ now: new Date() });
  }
}
