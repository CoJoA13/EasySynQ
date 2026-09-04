import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '../..');
const web = path.join(root, 'apps/web');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

test('web lock selects approved patched dependency versions', () => {
  const manifest = readJson(path.join(web, 'package.json'));
  const lock = readJson(path.join(web, 'package-lock.json'));
  const versions = (name) => new Set(
    Object.entries(lock.packages)
      .filter(([packagePath]) => packagePath.split('node_modules/').at(-1) === name)
      .map(([, packageInfo]) => packageInfo.version),
  );

  assert.equal(manifest.dependencies['react-router-dom'], '^7.18.3');
  assert.deepEqual(versions('brace-expansion'), new Set(['1.1.18', '5.0.9']));
  assert.deepEqual(versions('undici'), new Set(['8.10.1']));
  assert.deepEqual(versions('nanoid'), new Set(['3.3.18']));
  assert.deepEqual(versions('react-router'), new Set(['7.18.3']));
  assert.deepEqual(versions('react-router-dom'), new Set(['7.18.3']));
  assert.equal(manifest.dependencies.nanoid, undefined);
  assert.equal(manifest.devDependencies.nanoid, undefined);
  assert.equal(manifest.optionalDependencies?.nanoid, undefined);
  assert.equal(manifest.peerDependencies?.nanoid, undefined);
  assert.equal(manifest.overrides.nanoid, undefined);
  assert.equal(manifest.overrides['react-router'], undefined);
  assert.equal(manifest.overrides['react-router-dom'], undefined);
  assert.deepEqual(manifest.overrides, {
    'eslint-plugin-jsx-a11y': { eslint: '$eslint' },
  });
});
