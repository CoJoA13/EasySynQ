import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import test from 'node:test';

import {
  ROUTER_RSC_USAGE_POLICY_ID,
  RouterRscPolicyError,
  checkRouterRscUsage,
  inspectRouterRscInputs,
  isTrackedWebSourcePath,
} from '../lib/router-rsc-policy.mjs';

const root = path.resolve(import.meta.dirname, '../..');
const typescript = createRequire(path.join(root, 'apps/web/package.json'))('typescript');

const forbiddenApis = [
  'unstable_RSCHydratedRouter',
  'unstable_RSCStaticRouter',
  'unstable_createCallServer',
  'unstable_getRSCStream',
  'unstable_matchRSCServerRequest',
  'unstable_routeRSCServerRequest',
];

function inspect(text, overrides = {}) {
  return inspectRouterRscInputs({
    typescript,
    manifest: {},
    sources: [{ path: 'apps/web/src/feature.ts', text }],
    ...overrides,
  });
}

function assertPolicyError(code, callback, forbiddenText) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof RouterRscPolicyError);
    assert.equal(error.code, code);
    if (forbiddenText !== undefined) {
      assert.equal(error.message.includes(forbiddenText), false);
    }
    return true;
  });
}

function withTempRepo(callback) {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'router-rsc-policy-'));
  fs.mkdirSync(path.join(repoRoot, 'apps/web/src'), { recursive: true });
  fs.writeFileSync(path.join(repoRoot, 'apps/web/package.json'), '{}\n');
  try {
    return callback(repoRoot);
  } finally {
    fs.rmSync(repoRoot, { recursive: true, force: true });
  }
}

test('exports the policy identifier consumed by the npm exception', () => {
  assert.equal(ROUTER_RSC_USAGE_POLICY_ID, 'router-rsc-absent');
});

test('admits exactly the eight first-party source extensions', () => {
  for (const extension of ['ts', 'tsx', 'js', 'jsx', 'mts', 'mjs', 'cts', 'cjs']) {
    assert.equal(isTrackedWebSourcePath(`apps/web/src/features/example.${extension}`), true);
  }
  for (const pathName of [
    'apps/web/src/features/example.css',
    'apps/web/src/features/example.tsx.map',
    'apps/web/src/features/example.TS',
    'apps/web/source/example.ts',
    'apps/web/src.ts',
    '/apps/web/src/example.ts',
    'apps\\web\\src\\example.ts',
  ]) {
    assert.equal(isTrackedWebSourcePath(pathName), false, pathName);
  }
});

test('parses and inspects every admitted source extension', () => {
  const extensions = ['ts', 'tsx', 'js', 'jsx', 'mts', 'mjs', 'cts', 'cjs'];
  const violations = inspectRouterRscInputs({
    typescript,
    manifest: {},
    sources: extensions.map((extension) => ({
      path: `apps/web/src/feature.${extension}`,
      text: "import { unstable_RSCHydratedRouter } from 'react-router-dom';",
    })),
  });
  assert.deepEqual(violations.map(({ path: file }) => file), extensions
    .map((extension) => `apps/web/src/feature.${extension}`)
    .sort());
});

test('excludes exact test, fixture, generated, build, and install paths', () => {
  for (const pathName of [
    'apps/web/src/feature.test.ts',
    'apps/web/src/feature.spec.tsx',
    'apps/web/src/test/feature.ts',
    'apps/web/src/tests/feature.ts',
    'apps/web/src/__tests__/feature.ts',
    'apps/web/src/fixture/feature.ts',
    'apps/web/src/fixtures/feature.ts',
    'apps/web/src/__fixtures__/feature.ts',
    'apps/web/src/feature.fixture.ts',
    'apps/web/src/generated/feature.ts',
    'apps/web/src/_generated/feature.ts',
    'apps/web/src/__generated__/feature.ts',
    'apps/web/src/feature.generated.ts',
    'apps/web/src/build/feature.ts',
    'apps/web/src/dist/feature.ts',
    'apps/web/src/node_modules/feature.ts',
  ]) {
    assert.equal(isTrackedWebSourcePath(pathName), false, pathName);
  }
});

test('does not turn exclusion substrings into production blind spots', () => {
  for (const pathName of [
    'apps/web/src/contest.ts',
    'apps/web/src/testimony/feature.ts',
    'apps/web/src/fixturesPage.ts',
    'apps/web/src/generatedReport.ts',
    'apps/web/src/builder/feature.ts',
    'apps/web/src/node_modulesReport.ts',
  ]) {
    assert.equal(isTrackedWebSourcePath(pathName), true, pathName);
  }
});

test('rejects forbidden direct and npm-aliased packages in every dependency section', () => {
  const sections = [
    'dependencies',
    'devDependencies',
    'optionalDependencies',
    'peerDependencies',
  ];
  for (const [index, section] of sections.entries()) {
    const manifest = {
      [section]: {
        [`alias-${index}`]: index % 2 === 0 ? 'npm:@react-router/dev@7.18.2' : 'npm:@vitejs/plugin-rsc@1.0.0',
        [index % 2 === 0 ? '@vitejs/plugin-rsc' : '@react-router/dev']: '^1.0.0',
      },
    };
    const violations = inspectRouterRscInputs({ typescript, manifest, sources: [] });
    assert.equal(violations.length, 2, section);
    assert.deepEqual(new Set(violations.map(({ code }) => code)), new Set(['forbidden-router-rsc-package']));
    assert.deepEqual(new Set(violations.map(({ path: file, line, column }) => `${file}:${line}:${column}`)), new Set([
      'apps/web/package.json:1:1',
    ]));
    assert.deepEqual(new Set(violations.map(({ specifier }) => specifier)), new Set([
      '@react-router/dev',
      '@vitejs/plugin-rsc',
    ]));
  }
});

test('allows similarly named packages and aliases to unrelated packages', () => {
  const manifest = {
    dependencies: {
      '@react-router/development': '1.0.0',
      '@vitejs/plugin-rsc-extra': '1.0.0',
      alias: 'npm:@react-router/node@7.18.2',
      text: 'contains @react-router/dev but is not an npm alias',
    },
  };
  assert.deepEqual(inspectRouterRscInputs({ typescript, manifest, sources: [] }), []);
});

test('detects all six APIs from both Router packages and documented entry points', () => {
  const sources = forbiddenApis.map((symbol, index) => ({
    path: `apps/web/src/api-${index}.ts`,
    text: `import { ${symbol} as local${index} } from '${index % 3 === 0 ? 'react-router' : index % 3 === 1 ? 'react-router/dom' : 'react-router-dom'}';`,
  }));
  const violations = inspectRouterRscInputs({ typescript, manifest: {}, sources });
  assert.deepEqual(violations.map(({ symbol }) => symbol).sort(), [...forbiddenApis].sort());
  assert.deepEqual(new Set(violations.map(({ code }) => code)), new Set(['forbidden-router-rsc-api']));
});

test('detects type-only named imports by imported name rather than local alias', () => {
  const violations = inspect(`
import type { unstable_RSCHydratedRouter as Hydrated } from 'react-router-dom';
import { type unstable_RSCStaticRouter as Static } from 'react-router';
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_RSCStaticRouter',
  ]);
});

test('detects TypeScript import-type references to forbidden Router APIs', () => {
  const violations = inspect(`
type Hydrated = typeof import('react-router').unstable_RSCHydratedRouter;
type Static = typeof import('react-router-dom').unstable_RSCStaticRouter;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_RSCStaticRouter',
  ]);
});

test('detects indexed import-type references for all Router APIs and both packages', () => {
  const violations = inspect(`
type A = typeof import((('react-router')))[(('unstable_RSCHydratedRouter'))];
type B = typeof import(\`react-router-dom\`)[\`unstable_RSCStaticRouter\`];
type C = typeof import('react-router')['unstable_createCallServer'];
type D = typeof import('react-router-dom')['unstable_getRSCStream'];
type E = typeof import('react-router')['unstable_matchRSCServerRequest'];
type F = typeof import('react-router-dom')['unstable_routeRSCServerRequest'];
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), forbiddenApis);
});

test('allows unrelated and nonliteral indexed import types', () => {
  assert.deepEqual(inspect(`
type Key = 'unstable_RSCHydratedRouter';
type A = typeof import('unrelated-router')['unstable_RSCHydratedRouter'];
type B = typeof import('react-router')[Key];
type C = typeof import(ModuleName)['unstable_RSCHydratedRouter'];
`), []);
});

test('detects named, type-only, star, and namespace re-exports', () => {
  const violations = inspect(`
export { unstable_createCallServer as createServer } from 'react-router';
export type { unstable_getRSCStream as Stream } from 'react-router-dom';
export * from 'react-router/dom';
export * as Router from 'react-router-dom';
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_createCallServer',
    'unstable_getRSCStream',
    '*',
    '*',
  ]);
});

test('detects namespace property and literal element access through parentheses and optional chains', () => {
  const violations = inspect(`
import * as Router from 'react-router-dom';
Router.unstable_RSCHydratedRouter;
(Router)?.unstable_RSCStaticRouter;
Router['unstable_createCallServer'];
(Router)?.[\`unstable_getRSCStream\`];
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), forbiddenApis.slice(0, 4));
});

test('detects TypeScript import-equals namespace access', () => {
  const violations = inspect(`
import Router = require('react-router');
Router.unstable_matchRSCServerRequest;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), ['unstable_matchRSCServerRequest']);
});

test('detects direct, bound, and destructured CommonJS API access', () => {
  const violations = inspect(`
require('react-router-dom').unstable_RSCHydratedRouter;
(require(\`react-router\`))?.['unstable_RSCStaticRouter'];
const { unstable_createCallServer: createServer } = require('react-router');
const Router = require(\`react-router-dom\`);
Router.unstable_getRSCStream;
const { unstable_matchRSCServerRequest: match } = Router;
const route = (Router)?.[\`unstable_routeRSCServerRequest\`];
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), forbiddenApis);
});

test('detects literal string and template properties in computed CommonJS destructuring', () => {
  const violations = inspect(`
const { ['unstable_RSCHydratedRouter']: Hydrated } = require('react-router-dom');
const Router = require('react-router');
const { [\`unstable_RSCStaticRouter\`]: Static } = Router;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_RSCStaticRouter',
  ]);
});

test('detects forbidden literal dynamic imports and allows nonliteral expressions', () => {
  const violations = inspect(`
import('react-router');
import(\`react-router/dom\`);
import('react-router-dom');
import('react-router/rsc');
import('react-router/internal/react-server-client');
import('react-router-dom/rsc/client');
import('react-router-dom/react-server');
const root = 'react-router';
import(root);
import('react-' + 'router');
import(\`react-router/${'${'}root}\`);
import('react-router/server');
import('react-router-dom/server');
`);
  assert.deepEqual(violations.map(({ specifier }) => specifier), [
    'react-router',
    'react-router/dom',
    'react-router-dom',
    'react-router/rsc',
    'react-router/internal/react-server-client',
    'react-router-dom/rsc/client',
    'react-router-dom/react-server',
  ]);
  assert.deepEqual(new Set(violations.map(({ code }) => code)), new Set(['forbidden-router-rsc-import']));
});

test('detects a literal Router dynamic import that carries import options', () => {
  const violations = inspect("import('react-router', { with: { type: 'json' } });");
  assert.deepEqual(violations.map(({ specifier }) => specifier), ['react-router']);
});

test('unwraps parenthesized module and property literals', () => {
  const violations = inspect(`
const Router = require((\`react-router\`));
Router[('unstable_RSCHydratedRouter')];
import((\`react-router-dom\`));
`);
  assert.deepEqual(violations.map(({ symbol, specifier }) => symbol ?? specifier), [
    'unstable_RSCHydratedRouter',
    'react-router-dom',
  ]);
});

test('recursively unwraps every runtime-transparent TypeScript expression wrapper', () => {
  const violations = inspect(`
import(('react-router' as string));
import((('react-router-dom' satisfies string)!));
import(<string>'react-router/dom');
require('react-router' as string).unstable_RSCHydratedRouter;
require((('react-router-dom' satisfies string)!)).unstable_RSCStaticRouter;
const Router = require('react-router');
(Router as typeof Router).unstable_createCallServer;
((Router satisfies typeof Router)!).unstable_getRSCStream;
(<typeof Router>Router)['unstable_matchRSCServerRequest' as const];
Router[((('unstable_routeRSCServerRequest' satisfies string)!))];
`);
  assert.deepEqual(violations.map(({ symbol, specifier }) => symbol ?? specifier), [
    'react-router',
    'react-router-dom',
    'react-router/dom',
    ...forbiddenApis,
  ]);
});

test('does not turn wrapped nonliteral expressions into Router violations', () => {
  assert.deepEqual(inspect(`
const moduleName = 'react-router';
const propertyName = 'unstable_RSCHydratedRouter';
import(moduleName as string);
require((moduleName satisfies string)!).unstable_RSCHydratedRouter;
const Router = require('react-router');
Router[propertyName as string];
(localNamespace as typeof localNamespace).unstable_RSCHydratedRouter;
`), []);
});

test('ignores comments, strings, local APIs, and unrelated module imports', () => {
  assert.deepEqual(inspect(`
// import { unstable_RSCHydratedRouter } from 'react-router';
const prose = "require('react-router').unstable_RSCStaticRouter";
function unstable_createCallServer() {}
unstable_createCallServer();
import { unstable_getRSCStream } from 'unrelated-router';
import * as Other from 'unrelated-router';
Other.unstable_matchRSCServerRequest;
const local = { unstable_routeRSCServerRequest: true };
local.unstable_routeRSCServerRequest;
`), []);
});

test('allows side-effect imports without an import clause', () => {
  assert.deepEqual(inspect("import './theme.css';"), []);
});

test('uses lexical binding identity so shadowed Router bindings do not match', () => {
  const violations = inspect(`
import * as Router from 'react-router';
Router.unstable_RSCHydratedRouter;
function parameterShadow(Router) {
  Router.unstable_RSCStaticRouter;
}
{
  const Router = { unstable_createCallServer: true };
  Router.unstable_createCallServer;
}
function declarationShadow() {
  function Router() {}
  Router.unstable_getRSCStream;
}
Router.unstable_matchRSCServerRequest;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_matchRSCServerRequest',
  ]);
});

test('uses lexical binding identity for bound CommonJS namespaces', () => {
  const violations = inspect(`
const Router = require('react-router-dom');
Router.unstable_RSCHydratedRouter;
function shadowed(Router) {
  Router.unstable_RSCStaticRouter;
}
{
  let Router;
  Router.unstable_createCallServer;
}
Router.unstable_getRSCStream;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_getRSCStream',
  ]);
});

test('shares one position-aware runtime binding across valid var redeclarations', () => {
  assert.deepEqual(inspect(`
var Router = require('react-router');
var Router;
Router.unstable_RSCHydratedRouter;
`).map(({ symbol }) => symbol), ['unstable_RSCHydratedRouter']);

  assert.deepEqual(inspect(`
var Router;
var Router = require('react-router-dom');
Router.unstable_RSCStaticRouter;
`).map(({ symbol }) => symbol), ['unstable_RSCStaticRouter']);

  assert.deepEqual(inspect(`
var Router;
Router.unstable_createCallServer;
`), []);
});

test('uses the latest initialized declaration or assignment at each namespace access', () => {
  const violations = inspect(`
var Router = require('react-router');
Router.unstable_RSCHydratedRouter;
var Router = {};
Router.unstable_RSCStaticRouter;
var Router = require('react-router-dom');
Router.unstable_createCallServer;
Router = {};
Router.unstable_getRSCStream;
Router = require('react-router');
Router.unstable_matchRSCServerRequest;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), [
    'unstable_RSCHydratedRouter',
    'unstable_createCallServer',
    'unstable_matchRSCServerRequest',
  ]);
});

test('uses position-aware CommonJS identity for local namespace re-exports', () => {
  const violations = inspect(`
var Router = require('react-router');
export { Router as RouterBeforeReset };
Router = {};
export { Router as RouterAfterReset };
`);
  assert.deepEqual(violations.map(({ symbol, specifier }) => ({ symbol, specifier })), [{
    symbol: '*',
    specifier: 'react-router',
  }]);
});

test('keeps nested namespace value bindings scoped to their ModuleBlock', () => {
  const outerViolations = inspect(`
import * as Router from 'react-router';
namespace Inner {
  namespace Router {}
  Router.unstable_RSCHydratedRouter;
}
Router.unstable_RSCStaticRouter;
`);
  assert.deepEqual(outerViolations.map(({ symbol }) => symbol), ['unstable_RSCStaticRouter']);

  const innerViolations = inspect(`
const Router = {};
module Inner {
  const Router = require('react-router-dom');
  Router.unstable_createCallServer;
}
Router.unstable_getRSCStream;
`);
  assert.deepEqual(innerViolations.map(({ symbol }) => symbol), ['unstable_createCallServer']);
});

test('treats a nested enum as a value binding that shadows only inside its scope', () => {
  const violations = inspect(`
import * as Router from 'react-router-dom';
function inner() {
  enum Router { Local }
  Router.unstable_RSCHydratedRouter;
}
Router.unstable_RSCStaticRouter;
`);
  assert.deepEqual(violations.map(({ symbol }) => symbol), ['unstable_RSCStaticRouter']);
});

test('filters excluded source inputs before parsing or inspection', () => {
  const forbidden = "import { unstable_RSCHydratedRouter } from 'react-router';";
  const sources = [
    { path: 'apps/web/src/feature.test.ts', text: forbidden },
    { path: 'apps/web/src/fixtures/feature.ts', text: forbidden },
    { path: 'apps/web/src/generated/feature.ts', text: forbidden },
    { path: 'apps/web/src/build/feature.ts', text: forbidden },
    { path: 'apps/web/src/node_modules/pkg/index.ts', text: forbidden },
    { path: 'other/untracked.ts', text: forbidden },
  ];
  assert.deepEqual(inspectRouterRscInputs({ typescript, manifest: {}, sources }), []);
});

test('sorts shuffled source inputs deterministically without duplicate violations', () => {
  const sources = [
    {
      path: 'apps/web/src/z.ts',
      text: "import { unstable_getRSCStream } from 'react-router';",
    },
    {
      path: 'apps/web/src/a.ts',
      text: "import('react-router');\nimport { unstable_RSCHydratedRouter } from 'react-router-dom';",
    },
  ];
  const expected = inspectRouterRscInputs({ typescript, manifest: {}, sources });
  const shuffled = inspectRouterRscInputs({ typescript, manifest: {}, sources: [...sources].reverse() });
  assert.deepEqual(shuffled, expected);
  assert.deepEqual(expected.map(({ path: file, line }) => [file, line]), [
    ['apps/web/src/a.ts', 1],
    ['apps/web/src/a.ts', 2],
    ['apps/web/src/z.ts', 1],
  ]);
  assert.equal(new Set(expected.map((violation) => JSON.stringify(violation))).size, expected.length);
});

test('returns stable one-based source locations and violation shapes', () => {
  assert.deepEqual(inspect("\nimport { unstable_RSCHydratedRouter as Hydrated } from 'react-router-dom';"), [{
    code: 'forbidden-router-rsc-api',
    path: 'apps/web/src/feature.ts',
    line: 2,
    column: 10,
    symbol: 'unstable_RSCHydratedRouter',
    specifier: 'react-router-dom',
  }]);
});

test('fails closed on malformed manifests and dependency sections', () => {
  for (const manifest of [null, [], 'manifest']) {
    assertPolicyError('E_MANIFEST_SHAPE', () => inspectRouterRscInputs({
      typescript,
      manifest,
      sources: [],
    }));
  }
  for (const dependencies of [null, [], 'dependencies']) {
    assertPolicyError('E_MANIFEST_SHAPE', () => inspectRouterRscInputs({
      typescript,
      manifest: { dependencies },
      sources: [],
    }));
  }
  assertPolicyError('E_MANIFEST_SHAPE', () => inspectRouterRscInputs({
    typescript,
    manifest: { dependencies: { package: 42 } },
    sources: [],
  }));
  assertPolicyError('E_MANIFEST_SHAPE', () => inspectRouterRscInputs({
    typescript,
    manifest: { dependencies: { '': '1.0.0' } },
    sources: [],
  }));
});

test('fails closed on malformed source arrays, source records, and duplicate paths', () => {
  for (const sources of [null, {}, 'sources']) {
    assertPolicyError('E_SOURCE_SHAPE', () => inspectRouterRscInputs({ typescript, manifest: {}, sources }));
  }
  for (const source of [
    null,
    [],
    {},
    { path: '', text: '' },
    { path: 42, text: '' },
    { path: 'apps/web/src/a.ts', text: 42 },
  ]) {
    assertPolicyError('E_SOURCE_SHAPE', () => inspectRouterRscInputs({
      typescript,
      manifest: {},
      sources: [source],
    }));
  }
  assertPolicyError('E_DUPLICATE_SOURCE', () => inspectRouterRscInputs({
    typescript,
    manifest: {},
    sources: [
      { path: 'apps/web/src/a.ts', text: '' },
      { path: 'apps/web/src/a.ts', text: '' },
    ],
  }));
});

test('fails closed on an incompatible TypeScript compiler API', () => {
  for (const compiler of [null, {}, { createSourceFile() {} }]) {
    assertPolicyError('E_TYPESCRIPT_API', () => inspectRouterRscInputs({
      typescript: compiler,
      manifest: {},
      sources: [],
    }));
  }
});

test('rejects parse diagnostics with a location and without source-body leakage', () => {
  const secret = 'TOP_SECRET_SOURCE_BODY';
  assert.throws(
    () => inspect(`const ${secret} = ;`),
    (error) => {
      assert.ok(error instanceof RouterRscPolicyError);
      assert.equal(error.code, 'E_SOURCE_PARSE');
      assert.match(error.message, /apps\/web\/src\/feature\.ts:1:\d+/);
      assert.equal(error.message.includes(secret), false);
      return true;
    },
  );
});

test('sanitizes unexpected compiler failures without leaking source bodies', () => {
  const secret = 'SOURCE_BODY_SENTINEL';
  const brokenCompiler = {
    ...typescript,
    createSourceFile() {
      throw new Error(secret);
    },
  };
  assertPolicyError('E_TYPESCRIPT_PARSE', () => inspectRouterRscInputs({
    typescript: brokenCompiler,
    manifest: {},
    sources: [{ path: 'apps/web/src/a.ts', text: secret }],
  }), secret);
});

test('sanitizes incompatible TypeScript parse-diagnostic locations', () => {
  const secret = 'PARSE_LOCATION_SENTINEL';
  const brokenCompiler = {
    ...typescript,
    createSourceFile() {
      return {
        parseDiagnostics: [{ start: 0 }],
        getLineAndCharacterOfPosition() { throw new Error(secret); },
      };
    },
  };
  assertPolicyError('E_TYPESCRIPT_API', () => inspectRouterRscInputs({
    typescript: brokenCompiler,
    manifest: {},
    sources: [{ path: 'apps/web/src/a.ts', text: secret }],
  }), secret);
});

test('sanitizes throwing compiler-result getters without leaking source bodies', () => {
  const secret = 'SOURCE_BODY_SENTINEL';
  for (const createSourceFile of [
    () => ({
      get parseDiagnostics() { throw new Error(secret); },
    }),
    () => ({
      parseDiagnostics: new Proxy([], {
        get(target, property, receiver) {
          if (property === 'length') throw new Error(secret);
          return Reflect.get(target, property, receiver);
        },
      }),
    }),
    () => ({
      parseDiagnostics: [{
        get start() { throw new Error(secret); },
      }],
    }),
  ]) {
    const brokenCompiler = { ...typescript, createSourceFile };
    assertPolicyError('E_TYPESCRIPT_API', () => inspectRouterRscInputs({
      typescript: brokenCompiler,
      manifest: {},
      sources: [{ path: 'apps/web/src/a.ts', text: secret }],
    }), secret);
  }
});

test('sanitizes null diagnostics and malformed diagnostic locations', () => {
  const secret = 'SOURCE_BODY_SENTINEL';
  for (const createSourceFile of [
    () => ({ parseDiagnostics: [null] }),
    () => ({
      parseDiagnostics: [{ start: 0 }],
      getLineAndCharacterOfPosition: () => null,
    }),
    () => ({
      parseDiagnostics: [{ start: 0 }],
      getLineAndCharacterOfPosition: () => ({ line: -1, character: 0 }),
    }),
  ]) {
    const brokenCompiler = { ...typescript, createSourceFile };
    assertPolicyError('E_TYPESCRIPT_API', () => inspectRouterRscInputs({
      typescript: brokenCompiler,
      manifest: {},
      sources: [{ path: 'apps/web/src/a.ts', text: secret }],
    }), secret);
  }
});

test('checks only admitted Git-tracked files using the exact NUL-delimited Git boundary', () => {
  withTempRepo((repoRoot) => {
    fs.writeFileSync(
      path.join(repoRoot, 'apps/web/src/tracked.ts'),
      "import { unstable_RSCHydratedRouter } from 'react-router-dom';",
    );
    fs.writeFileSync(
      path.join(repoRoot, 'apps/web/src/untracked.ts'),
      "import { unstable_RSCStaticRouter } from 'react-router';",
    );
    let invocation;
    const execFileSyncImpl = (command, args, options) => {
      invocation = { command, args, options };
      return 'apps/web/src/tracked.ts\0apps/web/src/missing.test.ts\0apps/web/src/generated/missing.ts\0';
    };

    const violations = checkRouterRscUsage({ repoRoot, typescript, execFileSyncImpl });
    assert.deepEqual(invocation, {
      command: 'git',
      args: ['-C', repoRoot, 'ls-files', '-z', '--', 'apps/web/src'],
      options: { encoding: 'utf8' },
    });
    assert.deepEqual(violations.map(({ path: file }) => file), ['apps/web/src/tracked.ts']);
  });
});

test('reads manifest policy through the repository boundary', () => {
  withTempRepo((repoRoot) => {
    fs.writeFileSync(path.join(repoRoot, 'apps/web/package.json'), JSON.stringify({
      optionalDependencies: { rscAlias: 'npm:@react-router/dev@7.18.2' },
    }));
    const violations = checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => '',
    });
    assert.deepEqual(violations, [{
      code: 'forbidden-router-rsc-package',
      path: 'apps/web/package.json',
      line: 1,
      column: 1,
      symbol: 'rscAlias',
      specifier: '@react-router/dev',
    }]);
  });
});

test('fails closed with stable sanitized errors at Git, manifest, file-read, and NUL-list boundaries', () => {
  withTempRepo((repoRoot) => {
    assertPolicyError('E_GIT_EXEC', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl() { throw new Error('GIT_OUTPUT_SENTINEL'); },
    }), 'GIT_OUTPUT_SENTINEL');

    assertPolicyError('E_GIT_OUTPUT', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => 'apps/web/src/a.ts',
    }));
    assertPolicyError('E_GIT_OUTPUT', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => 'apps/web/src/a.ts\0apps/web/src/a.ts\0',
    }));
    assertPolicyError('E_GIT_OUTPUT', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => 'outside/source.ts\0',
    }));

    fs.writeFileSync(path.join(repoRoot, 'apps/web/package.json'), '{ MANIFEST_BODY_SENTINEL');
    assertPolicyError('E_MANIFEST_READ', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => '',
    }), 'MANIFEST_BODY_SENTINEL');

    fs.writeFileSync(path.join(repoRoot, 'apps/web/package.json'), '{}');
    assertPolicyError('E_SOURCE_READ', () => checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => 'apps/web/src/missing.ts\0',
    }));
  });
});

test('rejects every noncanonical Git path record before source exclusions', () => {
  withTempRepo((repoRoot) => {
    for (const gitOutput of [
      'apps/web/src/../escape.ts\0',
      'apps/web/src//escape.ts\0',
      'apps/web/src/\0',
    ]) {
      assertPolicyError('E_GIT_OUTPUT', () => checkRouterRscUsage({
        repoRoot,
        typescript,
        execFileSyncImpl: () => gitOutput,
      }));
    }

    assert.deepEqual(checkRouterRscUsage({
      repoRoot,
      typescript,
      execFileSyncImpl: () => 'apps/web/src/missing.test.ts\0apps/web/src/generated/missing.ts\0',
    }), []);
  });
});

test('fails closed when TypeScript cannot be resolved from the web manifest anchor', () => {
  withTempRepo((repoRoot) => {
    assertPolicyError('E_TYPESCRIPT_RESOLUTION', () => checkRouterRscUsage({
      repoRoot,
      execFileSyncImpl: () => '',
    }));
  });
});

test('loads TypeScript from apps/web/package.json when it is not injected', () => {
  const violations = checkRouterRscUsage({
    repoRoot: root,
    execFileSyncImpl: () => '',
  });
  assert.deepEqual(violations, []);
});
