// Exercise the repository's manager/rules through the installed Renovate updater.
// Called by run-renovate.sh validate; no registry lookup or platform initialization.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const dist = process.argv[2];
assert(dist, 'usage: node scripts/tests/test-renovate-images.mjs RENOVATE_DIST');
const moduleAt = relative => import(pathToFileURL(path.resolve(dist, relative)).href);
const { init } = await moduleAt('logger/index.js');
await init();
const { GlobalConfig } = await moduleAt('config/global.js');
const { getConfig } = await moduleAt('config/defaults.js');
const { applyPackageRules } = await moduleAt('util/package-rules/index.js');
const { extractPackageFile } = await moduleAt('modules/manager/custom/regex/index.js');
const { doAutoReplace } = await moduleAt('workers/repository/update/branch/auto-replace.js');
const config = JSON.parse(await fs.readFile('renovate.json', 'utf8'));
const manager = config.customManagers.find(m => m.managerFilePatterns.includes('/^infra/images\\.lock$/'));
assert(manager, 'image-manifest manager exists');
const packageFile = 'infra/images.lock';
const defaults = getConfig();
// Synthetic digests deliberately contain the old version digit.
const oldDigest = `sha256:${'7'.repeat(64)}`;
const newDigest = `sha256:${'a'.repeat(64)}`;
const cases = [
  { name: 'version and digest', before: `redis    redis:7@${oldDigest}`,
    after: `redis    redis:8@${newDigest}`, newValue: '8', newDigest },
  { name: 'digest only', before: `redis    redis:7@${oldDigest}`,
    after: `redis    redis:7@${newDigest}`, newValue: '7', newDigest },
  { name: 'version with unchanged digest', before: `redis    redis:7@${oldDigest}`,
    after: `redis    redis:8@${oldDigest}`, newValue: '8', newDigest: oldDigest },
  { name: 'version without a new digest', before: `redis    redis:7@${oldDigest}`,
    after: `redis    redis:8@${oldDigest}`, newValue: '8' },
  { name: 'registry-qualified image', before: `keycloak\tquay.io/keycloak/keycloak:7@${oldDigest}`,
    after: `keycloak\tquay.io/keycloak/keycloak:8@${newDigest}`, newValue: '8', newDigest },
  { name: 'unpinned dev image', before: 'mailpit     axllent/mailpit:v1.31.1',
    after: 'mailpit     axllent/mailpit:v1.32.0', newValue: 'v1.32.0' },
];
const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'easysynq-renovate-images-'));
try {
  await fs.mkdir(path.join(scratch, 'infra'));
  GlobalConfig.set({ localDir: scratch, platform: 'gitlab', allowScripts: false });
  for (const scenario of cases) {
    const prefix = '# Preserve comments and unrelated refs.\n';
    const suffix = '\ncaddy    caddy:2\n';
    const content = prefix + scenario.before + suffix;
    const expected = prefix + scenario.after + suffix;
    const extracted = extractPackageFile(content, packageFile, manager);
    const upgrade = await applyPackageRules({
      ...defaults, ...manager, ...extracted, ...extracted.deps[0],
      manager: 'regex', packageFile, depIndex: 0, baseDeps: extracted.deps,
      newValue: scenario.newValue, newDigest: scenario.newDigest,
      packageRules: config.packageRules,
    });
    await fs.writeFile(path.join(scratch, packageFile), content);
    assert.equal(await doAutoReplace(upgrade, content, false), expected, scenario.name);
    assert.equal(await fs.readFile(path.join(scratch, packageFile), 'utf8'), expected, scenario.name);
    console.log(`PASS: ${scenario.name}`);
  }
  // The manifest rule must not alter Dockerfile or other custom-manager updates.
  for (const [otherManager, otherFile] of [['dockerfile', 'infra/images.lock'], ['regex', 'apps/web/Dockerfile']]) {
    const input = { ...defaults, manager: otherManager, packageFile: otherFile, depName: 'redis',
      currentValue: '7', datasource: 'docker', packageRules: config.packageRules };
    const applied = await applyPackageRules(input);
    assert.equal(applied.autoReplaceGlobalMatch, defaults.autoReplaceGlobalMatch, otherFile);
  }
  console.log('Renovate image updates: 6 cases and rule isolation passed');
} finally {
  await fs.rm(scratch, { recursive: true, force: true });
}
