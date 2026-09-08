#!/usr/bin/env bash
# Active tool distribution must not require GitHub; passive history is outside this guard.
set -euo pipefail
cd "$(dirname "$0")/../.."
node <<'JS'
const assert = require('node:assert/strict');
const fs = require('node:fs');
for (const name of ['.gitlab-ci.yml', 'apps/api/Dockerfile', 'scripts/run-renovate.sh']) {
  const source = fs.readFileSync(name, 'utf8').split('\n').filter(line => !line.trimStart().startsWith('#')).join('\n');
  assert(!/(?:https?:\/\/[^\s]*github(?:usercontent)?\.(?:com|io)|ghcr\.io\/)/.test(source), name);
}
const config = JSON.parse(fs.readFileSync('renovate.json', 'utf8'));
const globalConfig = require(process.cwd() + '/renovate-self-hosted.cjs');
assert.equal(config.fetchChangeLogs, 'off');
assert.equal(config['github-actions'].enabled, false);
assert(config.packageRules.some(rule => JSON.stringify(rule.matchDatasources) === '["python-version"]' && rule.enabled === false));
assert.equal(config.automerge ?? false, false);
assert.equal(config.prConcurrentLimit, 5);
assert.equal(globalConfig.binarySource, 'global');
for (const domain of ['github.com', 'githubusercontent.com', 'github.io', 'githubassets.com', 'ghcr.io']) {
  assert(globalConfig.hostRules.some(rule => rule.matchHost === domain && rule.enabled === false), domain);
}
assert.equal(globalConfig.customEnvVariables.UV_PYTHON_DOWNLOADS, 'never');
assert.equal(globalConfig.exposeAllEnv ?? false, false);
// Real examples exercise captures for both bare and registry-qualified image names and digests.
const manager = config.customManagers.find(m => m.managerFilePatterns.includes('/^infra/images\\.lock$/'));
assert(manager, 'image-manifest manager exists');
const content = fs.readFileSync('infra/images.lock', 'utf8');
const matches = [...content.matchAll(new RegExp(manager.matchStrings[0], 'g'))];
const refs = content.split('\n').filter(line => line.trim() && !line.trimStart().startsWith('#'));
assert.equal(matches.length, refs.length);
assert(matches.find(m => m.groups.depName === 'axllent/mailpit').groups.currentValue.startsWith('v1.'));
assert(matches.find(m => m.groups.depName === 'quay.io/keycloak/keycloak').groups.currentDigest.startsWith('sha256:'));
console.log('GitLab distribution and image-manifest policy: PASS');
JS
