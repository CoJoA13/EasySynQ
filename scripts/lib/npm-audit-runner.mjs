import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const TIMEOUT_MS = 120_000;
const MAX_BUFFER_BYTES = 8 * 1024 * 1024;
const CACHE_PREFIX = 'npm-audit-';
const POSIX_PLATFORMS = new Set([
  'aix',
  'darwin',
  'freebsd',
  'linux',
  'openbsd',
  'sunos',
]);
const CONTROLLED_NPM_ENV_KEYS = new Set([
  'npm_config_cache',
  'npm_config_update_notifier',
]);

export class NpmAuditExecutionError extends Error {
  code;

  constructor(code, message) {
    super(message);
    this.name = 'NpmAuditExecutionError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new NpmAuditExecutionError(code, message);
}

function pathApiForPlatform(platform) {
  if (platform === 'win32') return path.win32;
  if (POSIX_PLATFORMS.has(platform)) return path.posix;
  fail('E_PLATFORM', 'the active platform has no supported Node distribution layout');
}

function npmCliCandidates(realNodeExecutable, platform) {
  const pathApi = pathApiForPlatform(platform);
  const executableDirectory = pathApi.dirname(realNodeExecutable);
  if (platform === 'win32') {
    return [
      pathApi.join(executableDirectory, 'node_modules', 'npm', 'bin', 'npm-cli.js'),
      pathApi.join(executableDirectory, 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    ];
  }
  const distributionRoot = pathApi.dirname(executableDirectory);
  return [
    pathApi.join(distributionRoot, 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    pathApi.join(distributionRoot, 'node_modules', 'npm', 'bin', 'npm-cli.js'),
  ];
}

export function resolveNpmCliPath({
  nodeExecutable = process.execPath,
  platform = process.platform,
  realpathSyncImpl = fs.realpathSync,
  statSyncImpl = fs.statSync,
} = {}) {
  if (typeof nodeExecutable !== 'string' || nodeExecutable.length === 0) {
    fail('E_NODE_EXECUTABLE', 'the Node executable must be a non-empty path');
  }
  if (typeof realpathSyncImpl !== 'function') {
    fail('E_NODE_EXECUTABLE', 'the Node realpath boundary must be a function');
  }
  if (typeof statSyncImpl !== 'function') {
    fail('E_NPM_CLI_NOT_FOUND', 'the npm CLI stat boundary must be a function');
  }

  const pathApi = pathApiForPlatform(platform);
  let realNodeExecutable;
  try {
    realNodeExecutable = realpathSyncImpl(nodeExecutable);
  } catch {
    fail('E_NODE_EXECUTABLE', 'could not resolve the active Node executable');
  }
  if (typeof realNodeExecutable !== 'string'
      || !pathApi.isAbsolute(realNodeExecutable)) {
    fail('E_NODE_EXECUTABLE', 'the active Node executable did not resolve to an absolute path');
  }

  const matches = [];
  for (const candidate of npmCliCandidates(realNodeExecutable, platform)) {
    try {
      const stat = statSyncImpl(candidate);
      if (stat && typeof stat.isFile === 'function' && stat.isFile()) matches.push(candidate);
    } catch {
      // A candidate that cannot be inspected is not a usable regular npm CLI entry point.
    }
  }
  if (matches.length === 0) {
    fail('E_NPM_CLI_NOT_FOUND', 'could not find npm-cli.js in the active Node distribution');
  }
  if (matches.length !== 1) {
    fail('E_NPM_CLI_AMBIGUOUS', 'the active Node distribution has multiple npm CLI entry points');
  }
  return matches[0];
}

function controlledEnvironment(cacheDirectory) {
  const environment = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (!CONTROLLED_NPM_ENV_KEYS.has(key.toLowerCase())) environment[key] = value;
  }
  if (cacheDirectory !== undefined) environment.npm_config_cache = cacheDirectory;
  environment.npm_config_update_notifier = 'false';
  return environment;
}

function spawnOptions(cwd, cacheDirectory) {
  return {
    cwd,
    shell: false,
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    maxBuffer: MAX_BUFFER_BYTES,
    env: controlledEnvironment(cacheDirectory),
  };
}

function spawnResult({
  nodeExecutable,
  args,
  cwd,
  cacheDirectory,
  spawnSyncImpl,
  allowedStatuses,
}) {
  let result;
  try {
    result = spawnSyncImpl(
      nodeExecutable,
      args,
      spawnOptions(cwd, cacheDirectory),
    );
  } catch {
    fail('E_NPM_SPAWN', 'npm execution could not be started');
  }
  if (result === null || typeof result !== 'object') {
    fail('E_NPM_SPAWN', 'npm execution returned no process result');
  }

  if (result.error !== undefined && result.error !== null) {
    const errorCode = typeof result.error === 'object' ? result.error.code : undefined;
    if (errorCode === 'ETIMEDOUT') {
      fail('E_NPM_TIMEOUT', 'npm execution exceeded its fixed timeout');
    }
    if (errorCode === 'ENOBUFS') {
      fail('E_NPM_OUTPUT_OVERFLOW', 'npm execution exceeded its fixed output limit');
    }
    fail('E_NPM_SPAWN', 'npm execution failed at the process boundary');
  }
  if (typeof result.signal === 'string' && result.signal.length > 0) {
    fail('E_NPM_SIGNAL', 'npm execution ended from a signal');
  }
  if (!Number.isInteger(result.status) || !allowedStatuses.has(result.status)) {
    fail('E_NPM_STATUS', 'npm execution returned an unsupported status');
  }
  if (typeof result.stdout !== 'string' || typeof result.stderr !== 'string') {
    fail('E_NPM_OUTPUT', 'npm execution returned malformed output');
  }
  return result;
}

export function getNpmVersion({
  nodeExecutable = process.execPath,
  npmCliPath,
  cwd,
  spawnSyncImpl = spawnSync,
} = {}) {
  const result = spawnResult({
    nodeExecutable,
    args: [npmCliPath, '--version'],
    cwd,
    spawnSyncImpl,
    allowedStatuses: new Set([0]),
  });
  const version = result.stdout.trim();
  if (version.length === 0) fail('E_NPM_OUTPUT', 'npm returned an empty version');
  return version;
}

function resolveCacheParent(cacheParent) {
  if (typeof cacheParent !== 'string' || !path.isAbsolute(cacheParent)) {
    fail('E_CACHE_PARENT', 'the cache parent must be an absolute path');
  }
  let parentStat;
  let realParent;
  try {
    parentStat = fs.lstatSync(cacheParent);
    realParent = fs.realpathSync(cacheParent);
  } catch {
    fail('E_CACHE_PARENT', 'the cache parent could not be inspected');
  }
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) {
    fail('E_CACHE_PARENT', 'the cache parent must be a non-link directory');
  }
  return realParent;
}

function validateCacheChild(cacheParent, cacheDirectory) {
  if (typeof cacheDirectory !== 'string'
      || path.dirname(cacheDirectory) !== cacheParent
      || !path.basename(cacheDirectory).startsWith(CACHE_PREFIX)) {
    fail('E_CACHE_CLEANUP', 'the npm cache is not the expected direct child');
  }
  let childStat;
  let realChild;
  try {
    childStat = fs.lstatSync(cacheDirectory, { bigint: true });
    realChild = fs.realpathSync(cacheDirectory);
  } catch {
    fail('E_CACHE_CLEANUP', 'the npm cache cleanup target could not be inspected');
  }
  if (!childStat.isDirectory()
      || childStat.isSymbolicLink()
      || realChild !== cacheDirectory) {
    fail('E_CACHE_CLEANUP', 'the npm cache cleanup target is not a non-link directory');
  }
  if (typeof childStat.dev !== 'bigint'
      || typeof childStat.ino !== 'bigint'
      || childStat.dev < 0n
      || childStat.ino <= 0n) {
    fail('E_CACHE_CLEANUP', 'the npm cache cleanup target has no stable filesystem identity');
  }
  return { device: childStat.dev, inode: childStat.ino };
}

function removeCacheChild(cacheParent, cacheDirectory, createdIdentity) {
  const currentIdentity = validateCacheChild(cacheParent, cacheDirectory);
  if (currentIdentity.device !== createdIdentity.device
      || currentIdentity.inode !== createdIdentity.inode) {
    fail('E_CACHE_CLEANUP', 'the npm cache cleanup target changed identity');
  }
  try {
    fs.rmSync(cacheDirectory, { recursive: true, force: false, maxRetries: 0 });
  } catch {
    fail('E_CACHE_CLEANUP', 'the npm cache could not be removed');
  }
  if (fs.existsSync(cacheDirectory)) {
    fail('E_CACHE_CLEANUP', 'the npm cache still exists after cleanup');
  }
}

export function runNpmAudit({
  nodeExecutable = process.execPath,
  npmCliPath,
  webDirectory,
  cacheParent,
  spawnSyncImpl = spawnSync,
} = {}) {
  const realCacheParent = resolveCacheParent(cacheParent);
  let cacheDirectory;
  let cacheIdentity;
  try {
    cacheDirectory = fs.mkdtempSync(path.join(realCacheParent, CACHE_PREFIX));
    cacheIdentity = validateCacheChild(realCacheParent, cacheDirectory);
  } catch (error) {
    if (error instanceof NpmAuditExecutionError) throw error;
    fail('E_CACHE_CREATE', 'the isolated npm cache could not be created');
  }

  let auditResult;
  let auditError;
  try {
    const result = spawnResult({
      nodeExecutable,
      args: [
        npmCliPath,
        'audit',
        '--package-lock-only',
        '--audit-level=high',
        '--json',
      ],
      cwd: webDirectory,
      cacheDirectory,
      spawnSyncImpl,
      allowedStatuses: new Set([0, 1]),
    });
    auditResult = {
      exitCode: result.status,
      stdout: result.stdout,
      stderr: result.stderr,
    };
  } catch (error) {
    auditError = error;
  }

  removeCacheChild(realCacheParent, cacheDirectory, cacheIdentity);
  if (auditError !== undefined) throw auditError;
  return auditResult;
}
