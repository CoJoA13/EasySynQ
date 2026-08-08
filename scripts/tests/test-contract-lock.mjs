import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '../..');
const contracts = path.join(root, 'packages/contracts');

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

test('contract toolchain manifest, lock, and installed versions are exact', () => {
  const manifest = readJson(path.join(contracts, 'package.json'));
  const lock = readJson(path.join(contracts, 'package-lock.json'));
  const installedRedocly = readJson(
    path.join(contracts, 'node_modules/@redocly/cli/package.json'),
  );
  const installedOpenapiTypescript = readJson(
    path.join(contracts, 'node_modules/openapi-typescript/package.json'),
  );

  assert.equal(manifest.private, true);
  assert.deepEqual(manifest.devDependencies, {
    '@redocly/cli': '2.46.0',
    'openapi-typescript': '7.13.0',
  });
  assert.deepEqual(manifest.overrides, {
    '@redocly/openapi-core': { 'js-yaml': '4.3.1' },
  });
  assert.deepEqual(lock.packages[''].devDependencies, manifest.devDependencies);
  assert.equal(lock.packages['node_modules/@redocly/cli'].version, '2.46.0');
  assert.equal(lock.packages['node_modules/openapi-typescript'].version, '7.13.0');
  assert.equal(installedRedocly.version, '2.46.0');
  assert.equal(installedOpenapiTypescript.version, '7.13.0');

  const lockedJsYaml = Object.entries(lock.packages).filter(([name]) => name.endsWith('/js-yaml'));
  assert.notEqual(lockedJsYaml.length, 0);
  for (const [name, pkg] of lockedJsYaml) {
    assert.equal(pkg.version, '4.3.1', `unexpected lock version for ${name}`);
    const installedPath = path.join(contracts, name, 'package.json');
    if (fs.existsSync(installedPath)) {
      assert.equal(readJson(installedPath).version, '4.3.1', `unexpected installed version for ${name}`);
    }
  }
  assert.equal(
    lockedJsYaml.some(([, pkg]) => pkg.version === '4.3.0'),
    false,
    'vulnerable js-yaml 4.3.0 must not be locked',
  );
});
