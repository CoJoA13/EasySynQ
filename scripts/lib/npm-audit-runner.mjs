import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const TIMEOUT_MS = 120_000;
const MAX_BUFFER_BYTES = 8 * 1024 * 1024;
const CACHE_PREFIX = 'npm-audit-';
const CACHE_SENTINEL_PREFIX = '.easysynq-npm-audit-identity-';
const CACHE_SENTINEL_RANDOM_BYTES = 32;
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

function stableIdentity(stat, code, message) {
  if (stat === null
      || typeof stat !== 'object'
      || typeof stat.dev !== 'bigint'
      || typeof stat.ino !== 'bigint'
      || stat.dev < 0n
      || stat.ino <= 0n) {
    fail(code, message);
  }
  return { device: stat.dev, inode: stat.ino };
}

function identitiesMatch(left, right) {
  return left !== null
    && typeof left === 'object'
    && right !== null
    && typeof right === 'object'
    && left.device === right.device
    && left.inode === right.inode;
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
  return stableIdentity(
    childStat,
    'E_CACHE_CLEANUP',
    'the npm cache cleanup target has no stable filesystem identity',
  );
}

function cacheDirectoryOpenFlags() {
  if (process.platform === 'win32') return 'r';
  let flags = fs.constants.O_RDONLY;
  if (Number.isInteger(fs.constants.O_DIRECTORY)) flags |= fs.constants.O_DIRECTORY;
  if (Number.isInteger(fs.constants.O_NOFOLLOW)) flags |= fs.constants.O_NOFOLLOW;
  return flags;
}

function cacheSentinelOpenFlags() {
  let flags = fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_EXCL;
  if (Number.isInteger(fs.constants.O_NOFOLLOW)) flags |= fs.constants.O_NOFOLLOW;
  return flags;
}

function validateCacheSentinelPath(cacheDirectory, sentinelPath) {
  if (typeof sentinelPath !== 'string'
      || path.dirname(sentinelPath) !== cacheDirectory
      || !path.basename(sentinelPath).startsWith(CACHE_SENTINEL_PREFIX)) {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel is not the expected direct child');
  }

  let sentinelStat;
  let realSentinel;
  try {
    sentinelStat = fs.lstatSync(sentinelPath, { bigint: true });
    realSentinel = fs.realpathSync(sentinelPath);
  } catch {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel could not be inspected');
  }
  if (!sentinelStat.isFile()
      || sentinelStat.isSymbolicLink()
      || sentinelStat.size !== 0n
      || realSentinel !== sentinelPath) {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel is not an empty non-link file');
  }
  return stableIdentity(
    sentinelStat,
    'E_CACHE_CLEANUP',
    'the npm cache identity sentinel has no stable filesystem identity',
  );
}

function closeHandleOnce(handle, label) {
  if (handle === null || typeof handle !== 'object' || handle.descriptor === undefined) {
    return undefined;
  }
  const descriptor = handle.descriptor;
  handle.descriptor = undefined;
  if (!Number.isInteger(descriptor) || descriptor < 0) {
    return new NpmAuditExecutionError(
      'E_CACHE_CLEANUP',
      `the npm cache ${label} handle is unusable`,
    );
  }
  try {
    fs.closeSync(descriptor);
    return undefined;
  } catch {
    return new NpmAuditExecutionError(
      'E_CACHE_CLEANUP',
      `the npm cache ${label} handle could not be closed`,
    );
  }
}

function validateCacheSentinelHandle(cacheDirectory, sentinelHandle) {
  if (sentinelHandle === null
      || typeof sentinelHandle !== 'object'
      || !Number.isInteger(sentinelHandle.descriptor)
      || sentinelHandle.descriptor < 0
      || sentinelHandle.identity === null
      || typeof sentinelHandle.identity !== 'object') {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel handle is unusable');
  }
  let descriptorStat;
  try {
    descriptorStat = fs.fstatSync(sentinelHandle.descriptor, { bigint: true });
  } catch {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel handle could not be inspected');
  }
  const descriptorIdentity = stableIdentity(
    descriptorStat,
    'E_CACHE_CLEANUP',
    'the npm cache identity sentinel handle has no stable filesystem identity',
  );
  const pathIdentity = validateCacheSentinelPath(cacheDirectory, sentinelHandle.path);
  if (!descriptorStat.isFile()
      || descriptorStat.size !== 0n
      || !identitiesMatch(descriptorIdentity, sentinelHandle.identity)
      || !identitiesMatch(descriptorIdentity, pathIdentity)) {
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel changed identity');
  }
  return descriptorIdentity;
}

function acquireCacheSentinel(cacheParent, cacheDirectory, createdIdentity) {
  const sentinelHandle = {
    descriptor: undefined,
    identity: undefined,
    path: undefined,
  };
  try {
    const randomMaterial = crypto.randomBytes(CACHE_SENTINEL_RANDOM_BYTES);
    if (!Buffer.isBuffer(randomMaterial)
        || randomMaterial.length !== CACHE_SENTINEL_RANDOM_BYTES) {
      fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel name could not be generated');
    }
    sentinelHandle.path = path.join(
      cacheDirectory,
      `${CACHE_SENTINEL_PREFIX}${randomMaterial.toString('hex')}`,
    );
    sentinelHandle.descriptor = fs.openSync(
      sentinelHandle.path,
      cacheSentinelOpenFlags(),
      0o600,
    );
    const descriptorStat = fs.fstatSync(sentinelHandle.descriptor, { bigint: true });
    const descriptorIdentity = stableIdentity(
      descriptorStat,
      'E_CACHE_CLEANUP',
      'the npm cache identity sentinel has no stable filesystem identity',
    );
    const pathIdentity = validateCacheSentinelPath(cacheDirectory, sentinelHandle.path);
    const currentCacheIdentity = validateCacheChild(cacheParent, cacheDirectory);
    if (!descriptorStat.isFile()
        || descriptorStat.size !== 0n
        || !identitiesMatch(descriptorIdentity, pathIdentity)
        || !identitiesMatch(currentCacheIdentity, createdIdentity)) {
      fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel could not be bound to its directory');
    }
    sentinelHandle.identity = descriptorIdentity;
    return sentinelHandle;
  } catch (error) {
    const closeError = closeHandleOnce(sentinelHandle, 'identity sentinel');
    if (closeError !== undefined) throw closeError;
    if (error instanceof NpmAuditExecutionError) throw error;
    fail('E_CACHE_CLEANUP', 'the npm cache identity sentinel could not be acquired');
  }
}

function openCacheDirectory(
  cacheParent,
  cacheDirectory,
  createdIdentity,
  sentinelHandle,
  directoryHandle,
) {
  try {
    directoryHandle.descriptor = fs.openSync(cacheDirectory, cacheDirectoryOpenFlags());
    const descriptorStat = fs.fstatSync(directoryHandle.descriptor, { bigint: true });
    const descriptorIdentity = stableIdentity(
      descriptorStat,
      'E_CACHE_CREATE',
      'the npm cache directory handle has no stable filesystem identity',
    );
    directoryHandle.identity = descriptorIdentity;
    const pathIdentity = validateCacheChild(cacheParent, cacheDirectory);
    if (!descriptorStat.isDirectory()
        || !identitiesMatch(descriptorIdentity, pathIdentity)
        || !identitiesMatch(descriptorIdentity, createdIdentity)) {
      fail('E_CACHE_CREATE', 'the npm cache directory handle does not match its path');
    }
    validateCacheSentinelHandle(cacheDirectory, sentinelHandle);
  } catch {
    fail('E_CACHE_CREATE', 'the isolated npm cache directory could not be held open');
  }
}

function validateCacheDirectoryHandle(directoryHandle, createdIdentity) {
  if (directoryHandle === null
      || typeof directoryHandle !== 'object'
      || !Number.isInteger(directoryHandle.descriptor)
      || directoryHandle.descriptor < 0) {
    fail('E_CACHE_CLEANUP', 'the npm cache directory handle is unavailable');
  }

  let descriptorStat;
  try {
    descriptorStat = fs.fstatSync(directoryHandle.descriptor, { bigint: true });
  } catch {
    fail('E_CACHE_CLEANUP', 'the npm cache directory handle could not be inspected');
  }
  const descriptorIdentity = stableIdentity(
    descriptorStat,
    'E_CACHE_CLEANUP',
    'the npm cache directory handle has no stable filesystem identity',
  );
  if (!descriptorStat.isDirectory()
      || !identitiesMatch(descriptorIdentity, createdIdentity)
      || (directoryHandle.identity !== undefined
        && !identitiesMatch(descriptorIdentity, directoryHandle.identity))) {
    fail('E_CACHE_CLEANUP', 'the npm cache directory handle changed identity');
  }
  directoryHandle.identity = descriptorIdentity;
  return descriptorIdentity;
}

function verifyCacheAbsent(cacheDirectory) {
  let stillExists = false;
  try {
    fs.lstatSync(cacheDirectory);
    stillExists = true;
  } catch (error) {
    if (error === null || typeof error !== 'object' || error.code !== 'ENOENT') {
      fail('E_CACHE_CLEANUP', 'the npm cache removal could not be verified');
    }
  }
  if (stillExists) fail('E_CACHE_CLEANUP', 'the npm cache still exists after cleanup');
}

function removeCacheChild(
  cacheParent,
  cacheDirectory,
  createdIdentity,
  sentinelHandle,
  directoryHandle,
) {
  let removalError;
  let removalReportedSuccess = false;
  try {
    const descriptorIdentity = validateCacheDirectoryHandle(
      directoryHandle,
      createdIdentity,
    );
    const currentIdentity = validateCacheChild(cacheParent, cacheDirectory);
    if (!identitiesMatch(currentIdentity, createdIdentity)
        || !identitiesMatch(currentIdentity, descriptorIdentity)) {
      fail('E_CACHE_CLEANUP', 'the npm cache cleanup target changed identity');
    }
    validateCacheSentinelHandle(cacheDirectory, sentinelHandle);
    fs.rmSync(cacheDirectory, { recursive: true, force: false, maxRetries: 0 });
    removalReportedSuccess = true;
  } catch (error) {
    removalError = error instanceof NpmAuditExecutionError
      ? error
      : new NpmAuditExecutionError('E_CACHE_CLEANUP', 'the npm cache could not be removed');
  }

  let closeError = closeHandleOnce(directoryHandle, 'directory');
  const sentinelCloseError = closeHandleOnce(sentinelHandle, 'identity sentinel');
  if (closeError === undefined) closeError = sentinelCloseError;

  let verificationError;
  if (removalReportedSuccess) {
    try {
      verifyCacheAbsent(cacheDirectory);
    } catch (error) {
      verificationError = error instanceof NpmAuditExecutionError
        ? error
        : new NpmAuditExecutionError(
          'E_CACHE_CLEANUP',
          'the npm cache removal could not be verified',
        );
    }
  }

  if (removalError !== undefined) throw removalError;
  if (verificationError !== undefined) throw verificationError;
  if (closeError !== undefined) throw closeError;
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
  try {
    cacheDirectory = fs.mkdtempSync(path.join(realCacheParent, CACHE_PREFIX));
  } catch {
    fail('E_CACHE_CREATE', 'the isolated npm cache could not be created');
  }

  const cacheIdentity = validateCacheChild(realCacheParent, cacheDirectory);
  const sentinelHandle = acquireCacheSentinel(
    realCacheParent,
    cacheDirectory,
    cacheIdentity,
  );
  const directoryHandle = { descriptor: undefined, identity: undefined };
  try {
    openCacheDirectory(
      realCacheParent,
      cacheDirectory,
      cacheIdentity,
      sentinelHandle,
      directoryHandle,
    );
  } catch (setupError) {
    removeCacheChild(
      realCacheParent,
      cacheDirectory,
      cacheIdentity,
      sentinelHandle,
      directoryHandle,
    );
    if (setupError instanceof NpmAuditExecutionError) throw setupError;
    fail('E_CACHE_CREATE', 'the isolated npm cache could not be opened');
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

  removeCacheChild(
    realCacheParent,
    cacheDirectory,
    cacheIdentity,
    sentinelHandle,
    directoryHandle,
  );
  if (auditError !== undefined) throw auditError;
  return auditResult;
}
