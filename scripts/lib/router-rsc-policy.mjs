import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';

export const ROUTER_RSC_USAGE_POLICY_ID = 'router-rsc-absent';

const WEB_MANIFEST_PATH = 'apps/web/package.json';
const WEB_SOURCE_PREFIX = 'apps/web/src/';
const SOURCE_EXTENSIONS = new Set(['ts', 'tsx', 'js', 'jsx', 'mts', 'mjs', 'cts', 'cjs']);
const EXCLUDED_DIRECTORY_SEGMENTS = new Set([
  'test',
  'tests',
  '__tests__',
  'fixture',
  'fixtures',
  '__fixtures__',
  'generated',
  '_generated',
  '__generated__',
  'build',
  'dist',
  'node_modules',
]);
const EXCLUDED_FILE_ROLES = new Set(['test', 'spec', 'fixture', 'generated']);
const DEPENDENCY_SECTIONS = [
  'dependencies',
  'devDependencies',
  'optionalDependencies',
  'peerDependencies',
];
const FORBIDDEN_PACKAGES = ['@react-router/dev', '@vitejs/plugin-rsc'];
const FORBIDDEN_APIS = new Set([
  'unstable_RSCHydratedRouter',
  'unstable_RSCStaticRouter',
  'unstable_createCallServer',
  'unstable_getRSCStream',
  'unstable_matchRSCServerRequest',
  'unstable_routeRSCServerRequest',
]);

export class RouterRscPolicyError extends Error {
  code;

  constructor(code, message) {
    super(message);
    this.name = 'RouterRscPolicyError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new RouterRscPolicyError(code, message);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assertTypescriptApi(typescript) {
  const functions = [
    'createSourceFile',
    'forEachChild',
    'isArrayBindingPattern',
    'isBlock',
    'isCallExpression',
    'isCatchClause',
    'isClassDeclaration',
    'isClassExpression',
    'isComputedPropertyName',
    'isElementAccessExpression',
    'isExportDeclaration',
    'isFunctionDeclaration',
    'isFunctionExpression',
    'isFunctionLike',
    'isIdentifier',
    'isImportDeclaration',
    'isImportEqualsDeclaration',
    'isImportTypeNode',
    'isLiteralTypeNode',
    'isNamedExports',
    'isNamedImports',
    'isNamespaceExport',
    'isNamespaceImport',
    'isNoSubstitutionTemplateLiteral',
    'isObjectBindingPattern',
    'isParameter',
    'isParenthesizedExpression',
    'isPropertyAccessExpression',
    'isStringLiteral',
    'isVariableDeclaration',
    'isVariableDeclarationList',
  ];
  if (!isObject(typescript)
      || !isObject(typescript.ScriptKind)
      || !isObject(typescript.ScriptTarget)
      || !isObject(typescript.NodeFlags)
      || !isObject(typescript.SyntaxKind)
      || functions.some((name) => typeof typescript[name] !== 'function')) {
    fail('E_TYPESCRIPT_API', 'TypeScript compiler API is incompatible with the Router RSC policy');
  }
}

export function isTrackedWebSourcePath(repoRelativePath) {
  if (typeof repoRelativePath !== 'string'
      || !repoRelativePath.startsWith(WEB_SOURCE_PREFIX)
      || repoRelativePath.includes('\\')
      || repoRelativePath.includes('\0')) {
    return false;
  }
  const segments = repoRelativePath.split('/');
  if (segments.some((segment) => segment.length === 0 || segment === '.' || segment === '..')) {
    return false;
  }
  const sourceSegments = segments.slice(3);
  if (sourceSegments.length === 0
      || sourceSegments.slice(0, -1).some((segment) => EXCLUDED_DIRECTORY_SEGMENTS.has(segment))) {
    return false;
  }
  const fileName = sourceSegments.at(-1);
  const extensionIndex = fileName.lastIndexOf('.');
  if (extensionIndex <= 0) return false;
  const extension = fileName.slice(extensionIndex + 1);
  if (!SOURCE_EXTENSIONS.has(extension)) return false;
  const stem = fileName.slice(0, extensionIndex);
  const role = stem.includes('.') ? stem.slice(stem.lastIndexOf('.') + 1) : '';
  return !EXCLUDED_FILE_ROLES.has(role);
}

function forbiddenPackageFor(dependencyName, versionSpec) {
  if (FORBIDDEN_PACKAGES.includes(dependencyName)) return dependencyName;
  for (const packageName of FORBIDDEN_PACKAGES) {
    const aliasPrefix = `npm:${packageName}`;
    if (versionSpec === aliasPrefix || versionSpec.startsWith(`${aliasPrefix}@`)) {
      return packageName;
    }
  }
  return undefined;
}

function inspectManifest(manifest) {
  if (!isObject(manifest)) {
    fail('E_MANIFEST_SHAPE', 'web manifest must be an object');
  }
  const violations = [];
  for (const sectionName of DEPENDENCY_SECTIONS) {
    const section = manifest[sectionName];
    if (section === undefined) continue;
    if (!isObject(section)) {
      fail('E_MANIFEST_SHAPE', `${sectionName} must be an object`);
    }
    for (const [dependencyName, versionSpec] of Object.entries(section)) {
      if (dependencyName.length === 0
          || typeof versionSpec !== 'string'
          || versionSpec.length === 0) {
        fail('E_MANIFEST_SHAPE', `${sectionName} entries must use non-empty strings`);
      }
      const forbiddenPackage = forbiddenPackageFor(dependencyName, versionSpec);
      if (forbiddenPackage !== undefined) {
        violations.push({
          code: 'forbidden-router-rsc-package',
          path: WEB_MANIFEST_PATH,
          line: 1,
          column: 1,
          symbol: dependencyName,
          specifier: forbiddenPackage,
        });
      }
    }
  }
  return violations;
}

function scriptKindForPath(typescript, sourcePath) {
  const extension = sourcePath.slice(sourcePath.lastIndexOf('.') + 1);
  if (extension === 'tsx') return typescript.ScriptKind.TSX;
  if (extension === 'jsx') return typescript.ScriptKind.JSX;
  if (extension === 'js' || extension === 'mjs' || extension === 'cjs') {
    return typescript.ScriptKind.JS;
  }
  return typescript.ScriptKind.TS;
}

function literalText(typescript, node) {
  const candidate = unwrapParentheses(typescript, node);
  if (candidate !== undefined
      && (typescript.isStringLiteral(candidate)
        || typescript.isNoSubstitutionTemplateLiteral(candidate))) {
    return candidate.text;
  }
  return undefined;
}

function propertyNameText(typescript, node) {
  if (typescript.isIdentifier(node)) return node.text;
  if (typescript.isComputedPropertyName(node)) return literalText(typescript, node.expression);
  return literalText(typescript, node);
}

function unwrapParentheses(typescript, node) {
  let current = node;
  while (current !== undefined && typescript.isParenthesizedExpression(current)) {
    current = current.expression;
  }
  return current;
}

function isRouterApiSource(specifier) {
  return typeof specifier === 'string'
    && (specifier === 'react-router'
    || specifier.startsWith('react-router/')
    || specifier === 'react-router-dom'
    || specifier.startsWith('react-router-dom/'));
}

function isForbiddenDynamicImport(specifier) {
  if (specifier === 'react-router'
      || specifier === 'react-router/dom'
      || specifier === 'react-router-dom') {
    return true;
  }
  for (const packageName of ['react-router', 'react-router-dom']) {
    const prefix = `${packageName}/`;
    if (specifier.startsWith(prefix)) {
      const subpath = specifier.slice(prefix.length);
      return subpath.includes('rsc') || subpath.includes('react-server');
    }
  }
  return false;
}

function createScope(parent) {
  return { parent, bindings: new Map() };
}

function nearestFunctionScope(scope) {
  let current = scope;
  while (current.parent !== undefined && !current.functionScope && !current.sourceScope) {
    current = current.parent;
  }
  return current;
}

function bindName(typescript, name, scope, binding = { kind: 'local' }) {
  if (typescript.isIdentifier(name)) {
    scope.bindings.set(name.text, binding);
    return;
  }
  if (typescript.isObjectBindingPattern(name) || typescript.isArrayBindingPattern(name)) {
    for (const element of name.elements) {
      if (element.name !== undefined) bindName(typescript, element.name, scope);
    }
  }
}

function resolveBinding(scope, name) {
  let current = scope;
  while (current !== undefined) {
    const binding = current.bindings.get(name);
    if (binding !== undefined) return binding;
    current = current.parent;
  }
  return undefined;
}

function externalModuleSpecifier(typescript, node) {
  return node.moduleReference?.expression === undefined
    ? undefined
    : literalText(typescript, node.moduleReference.expression);
}

function buildLexicalModel(typescript, sourceFile) {
  const rootScope = createScope(undefined);
  rootScope.sourceScope = true;
  const scopeByNode = new WeakMap();
  const requireCandidates = [];

  function collect(node, incomingScope, functionBody = false) {
    let scope = incomingScope;

    if (typescript.isFunctionDeclaration(node) && node.name !== undefined) {
      bindName(typescript, node.name, incomingScope);
    } else if (typescript.isClassDeclaration(node) && node.name !== undefined) {
      bindName(typescript, node.name, incomingScope);
    }

    if (typescript.isFunctionLike(node)) {
      scope = createScope(incomingScope);
      scope.functionScope = true;
      if ((typescript.isFunctionExpression(node) || typescript.isFunctionDeclaration(node))
          && node.name !== undefined) {
        bindName(typescript, node.name, scope);
      }
      for (const parameter of node.parameters ?? []) bindName(typescript, parameter.name, scope);
    } else if ((typescript.isClassExpression(node) || typescript.isClassDeclaration(node))) {
      scope = createScope(incomingScope);
      if (node.name !== undefined) bindName(typescript, node.name, scope);
    } else if ((typescript.isBlock(node) && !functionBody)
        || typescript.isCatchClause(node)
        || node.kind === typescript.SyntaxKind.ForStatement
        || node.kind === typescript.SyntaxKind.ForInStatement
        || node.kind === typescript.SyntaxKind.ForOfStatement
        || node.kind === typescript.SyntaxKind.SwitchStatement) {
      scope = createScope(incomingScope);
    }

    scopeByNode.set(node, scope);

    if (typescript.isImportDeclaration(node)) {
      const specifier = literalText(typescript, node.moduleSpecifier);
      const importClause = node.importClause;
      if (importClause?.name !== undefined) bindName(typescript, importClause.name, scope);
      if (importClause?.namedBindings !== undefined
          && typescript.isNamespaceImport(importClause.namedBindings)) {
        bindName(typescript, importClause.namedBindings.name, scope, isRouterApiSource(specifier)
          ? { kind: 'router-namespace', specifier }
          : { kind: 'local' });
      } else if (importClause?.namedBindings !== undefined
          && typescript.isNamedImports(importClause.namedBindings)) {
        for (const element of importClause.namedBindings.elements) {
          bindName(typescript, element.name, scope);
        }
      }
    } else if (typescript.isImportEqualsDeclaration(node)) {
      const specifier = externalModuleSpecifier(typescript, node);
      bindName(typescript, node.name, scope, isRouterApiSource(specifier)
        ? { kind: 'router-namespace', specifier }
        : { kind: 'local' });
    } else if (typescript.isVariableDeclaration(node)) {
      const declarationList = typescript.isVariableDeclarationList(node.parent)
        ? node.parent
        : undefined;
      const isBlockScoped = declarationList !== undefined
        && (declarationList.flags & typescript.NodeFlags.BlockScoped) !== 0;
      const bindingScope = isBlockScoped ? scope : nearestFunctionScope(scope);
      const binding = { kind: 'local' };
      bindName(typescript, node.name, bindingScope, binding);
      if (typescript.isIdentifier(node.name) && node.initializer !== undefined) {
        requireCandidates.push({ binding, initializer: node.initializer, scope });
      }
    } else if (typescript.isCatchClause(node) && node.variableDeclaration !== undefined) {
      bindName(typescript, node.variableDeclaration.name, scope);
    } else if (typescript.isParameter(node)) {
      bindName(typescript, node.name, scope);
    }

    typescript.forEachChild(node, (child) => {
      const childIsFunctionBody = typescript.isFunctionLike(node) && child === node.body;
      collect(child, scope, childIsFunctionBody);
    });
  }

  collect(sourceFile, rootScope);

  function requireSpecifier(expression, scope) {
    const candidate = unwrapParentheses(typescript, expression);
    if (!typescript.isCallExpression(candidate)
        || candidate.arguments.length !== 1
        || !typescript.isIdentifier(candidate.expression)
        || candidate.expression.text !== 'require'
        || resolveBinding(scope, 'require') !== undefined) {
      return undefined;
    }
    return literalText(typescript, candidate.arguments[0]);
  }

  for (const candidate of requireCandidates) {
    const specifier = requireSpecifier(candidate.initializer, candidate.scope);
    if (isRouterApiSource(specifier)) {
      candidate.binding.kind = 'router-namespace';
      candidate.binding.specifier = specifier;
    }
  }

  return { scopeByNode, requireSpecifier };
}

function inspectSource(typescript, sourceInput) {
  let sourceFile;
  try {
    sourceFile = typescript.createSourceFile(
      sourceInput.path,
      sourceInput.text,
      typescript.ScriptTarget.Latest,
      true,
      scriptKindForPath(typescript, sourceInput.path),
    );
  } catch {
    fail('E_TYPESCRIPT_PARSE', `TypeScript could not parse ${sourceInput.path}`);
  }
  if (!isObject(sourceFile) || !Array.isArray(sourceFile.parseDiagnostics)) {
    fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible source file');
  }
  if (sourceFile.parseDiagnostics.length > 0) {
    const diagnostic = sourceFile.parseDiagnostics[0];
    let line = 1;
    let column = 1;
    if (Number.isInteger(diagnostic.start) && diagnostic.start >= 0) {
      try {
        const location = sourceFile.getLineAndCharacterOfPosition(diagnostic.start);
        line = location.line + 1;
        column = location.character + 1;
      } catch {
        fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible parse diagnostic');
      }
    }
    fail('E_SOURCE_PARSE', `${sourceInput.path}:${line}:${column}: TypeScript parse diagnostic`);
  }

  let lexicalModel;
  try {
    lexicalModel = buildLexicalModel(typescript, sourceFile);
  } catch (error) {
    if (error instanceof RouterRscPolicyError) throw error;
    fail('E_TYPESCRIPT_INSPECTION', `TypeScript could not inspect ${sourceInput.path}`);
  }

  const violations = [];

  function addViolation(code, node, fields) {
    const location = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    violations.push({
      code,
      path: sourceInput.path,
      line: location.line + 1,
      column: location.character + 1,
      ...fields,
    });
  }

  function namespaceBindingForExpression(expression, scope) {
    const candidate = unwrapParentheses(typescript, expression);
    if (typescript.isIdentifier(candidate)) {
      const binding = resolveBinding(scope, candidate.text);
      return binding?.kind === 'router-namespace' ? binding : undefined;
    }
    const specifier = lexicalModel.requireSpecifier(candidate, scope);
    return isRouterApiSource(specifier)
      ? { kind: 'router-namespace', specifier }
      : undefined;
  }

  function inspectBindingPattern(name, initializer, scope) {
    if (!typescript.isObjectBindingPattern(name)) return;
    const binding = namespaceBindingForExpression(initializer, scope);
    if (binding === undefined) return;
    for (const element of name.elements) {
      if (element.dotDotDotToken !== undefined) continue;
      const propertyNode = element.propertyName ?? element.name;
      const propertyName = propertyNameText(typescript, propertyNode);
      if (FORBIDDEN_APIS.has(propertyName)) {
        addViolation('forbidden-router-rsc-api', propertyNode, {
          symbol: propertyName,
          specifier: binding.specifier,
        });
      }
    }
  }

  function visit(node) {
    const scope = lexicalModel.scopeByNode.get(node);

    if (typescript.isImportTypeNode(node)
        && typescript.isLiteralTypeNode(node.argument)
        && node.qualifier !== undefined) {
      const specifier = literalText(typescript, node.argument.literal);
      let symbolNode = node.qualifier;
      while (symbolNode.left !== undefined) symbolNode = symbolNode.left;
      const symbol = typescript.isIdentifier(symbolNode) ? symbolNode.text : undefined;
      if (isRouterApiSource(specifier) && FORBIDDEN_APIS.has(symbol)) {
        addViolation('forbidden-router-rsc-api', symbolNode, { symbol, specifier });
      }
    } else if (typescript.isImportDeclaration(node)) {
      const specifier = literalText(typescript, node.moduleSpecifier);
      if (isRouterApiSource(specifier)
          && node.importClause?.namedBindings !== undefined
          && typescript.isNamedImports(node.importClause.namedBindings)) {
        for (const element of node.importClause.namedBindings.elements) {
          const importedNode = element.propertyName ?? element.name;
          const importedName = importedNode.text;
          if (FORBIDDEN_APIS.has(importedName)) {
            addViolation('forbidden-router-rsc-api', importedNode, {
              symbol: importedName,
              specifier,
            });
          }
        }
      }
    } else if (typescript.isExportDeclaration(node)) {
      const specifier = literalText(typescript, node.moduleSpecifier);
      if (isRouterApiSource(specifier)) {
        if (node.exportClause === undefined || typescript.isNamespaceExport(node.exportClause)) {
          addViolation('forbidden-router-rsc-api', node.exportClause ?? node.moduleSpecifier, {
            symbol: '*',
            specifier,
          });
        } else if (typescript.isNamedExports(node.exportClause)) {
          for (const element of node.exportClause.elements) {
            const importedNode = element.propertyName ?? element.name;
            const importedName = importedNode.text;
            if (FORBIDDEN_APIS.has(importedName)) {
              addViolation('forbidden-router-rsc-api', importedNode, {
                symbol: importedName,
                specifier,
              });
            }
          }
        }
      } else if (node.moduleSpecifier === undefined && typescript.isNamedExports(node.exportClause)) {
        for (const element of node.exportClause.elements) {
          const localNode = element.propertyName ?? element.name;
          const binding = resolveBinding(scope, localNode.text);
          if (binding?.kind === 'router-namespace') {
            addViolation('forbidden-router-rsc-api', localNode, {
              symbol: '*',
              specifier: binding.specifier,
            });
          }
        }
      }
    } else if (typescript.isCallExpression(node)
        && node.expression.kind === typescript.SyntaxKind.ImportKeyword
        && node.arguments.length >= 1) {
      const specifier = literalText(typescript, node.arguments[0]);
      if (specifier !== undefined && isForbiddenDynamicImport(specifier)) {
        addViolation('forbidden-router-rsc-import', node.arguments[0], { specifier });
      }
    } else if (typescript.isPropertyAccessExpression(node)) {
      const symbol = node.name.text;
      const binding = namespaceBindingForExpression(node.expression, scope);
      if (binding !== undefined && FORBIDDEN_APIS.has(symbol)) {
        addViolation('forbidden-router-rsc-api', node.name, {
          symbol,
          specifier: binding.specifier,
        });
      }
    } else if (typescript.isElementAccessExpression(node)) {
      const symbol = literalText(typescript, node.argumentExpression);
      const binding = namespaceBindingForExpression(node.expression, scope);
      if (binding !== undefined && FORBIDDEN_APIS.has(symbol)) {
        addViolation('forbidden-router-rsc-api', node.argumentExpression, {
          symbol,
          specifier: binding.specifier,
        });
      }
    } else if (typescript.isVariableDeclaration(node) && node.initializer !== undefined) {
      inspectBindingPattern(node.name, node.initializer, scope);
    }

    typescript.forEachChild(node, visit);
  }

  try {
    visit(sourceFile);
  } catch (error) {
    if (error instanceof RouterRscPolicyError) throw error;
    fail('E_TYPESCRIPT_INSPECTION', `TypeScript could not inspect ${sourceInput.path}`);
  }
  return violations;
}

function compareText(left, right) {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function compareViolations(left, right) {
  return compareText(left.path, right.path)
    || left.line - right.line
    || left.column - right.column
    || compareText(left.code, right.code)
    || compareText(left.symbol ?? '', right.symbol ?? '')
    || compareText(left.specifier ?? '', right.specifier ?? '');
}

function uniqueSortedViolations(violations) {
  const unique = new Map();
  for (const violation of violations) {
    const key = JSON.stringify(violation);
    if (!unique.has(key)) unique.set(key, violation);
  }
  return [...unique.values()].sort(compareViolations);
}

export function inspectRouterRscInputs({ typescript, manifest, sources } = {}) {
  assertTypescriptApi(typescript);
  const violations = inspectManifest(manifest);
  if (!Array.isArray(sources)) {
    fail('E_SOURCE_SHAPE', 'sources must be an array');
  }
  const seenPaths = new Set();
  for (const source of sources) {
    if (!isObject(source)
        || typeof source.path !== 'string'
        || source.path.length === 0
        || typeof source.text !== 'string') {
      fail('E_SOURCE_SHAPE', 'each source must contain string path and text fields');
    }
    if (seenPaths.has(source.path)) {
      fail('E_DUPLICATE_SOURCE', 'source paths must be unique');
    }
    seenPaths.add(source.path);
  }
  for (const source of sources) {
    if (isTrackedWebSourcePath(source.path)) {
      violations.push(...inspectSource(typescript, source));
    }
  }
  return uniqueSortedViolations(violations);
}

function readManifest(repoRoot) {
  const manifestPath = path.join(repoRoot, WEB_MANIFEST_PATH);
  try {
    const contents = fs.readFileSync(manifestPath, 'utf8');
    return JSON.parse(contents);
  } catch {
    fail('E_MANIFEST_READ', `could not read or parse ${WEB_MANIFEST_PATH}`);
  }
}

function trackedPaths(repoRoot, execFileSyncImpl) {
  let output;
  try {
    output = execFileSyncImpl(
      'git',
      ['-C', repoRoot, 'ls-files', '-z', '--', 'apps/web/src'],
      { encoding: 'utf8' },
    );
  } catch {
    fail('E_GIT_EXEC', 'could not enumerate Git-tracked web sources');
  }
  if (typeof output !== 'string' || (output.length > 0 && !output.endsWith('\0'))) {
    fail('E_GIT_OUTPUT', 'Git returned a malformed NUL-delimited source list');
  }
  const paths = output.length === 0 ? [] : output.slice(0, -1).split('\0');
  const seen = new Set();
  for (const sourcePath of paths) {
    if (sourcePath.length === 0
        || !sourcePath.startsWith(WEB_SOURCE_PREFIX)
        || seen.has(sourcePath)) {
      fail('E_GIT_OUTPUT', 'Git returned a malformed NUL-delimited source list');
    }
    seen.add(sourcePath);
  }
  return paths;
}

function loadTypescript(repoRoot) {
  try {
    return createRequire(path.join(repoRoot, WEB_MANIFEST_PATH))('typescript');
  } catch {
    fail('E_TYPESCRIPT_RESOLUTION', 'could not load TypeScript from the frozen web dependency tree');
  }
}

export function checkRouterRscUsage({
  repoRoot,
  typescript,
  execFileSyncImpl = execFileSync,
} = {}) {
  if (typeof repoRoot !== 'string' || !path.isAbsolute(repoRoot)) {
    fail('E_REPO_ROOT', 'repoRoot must be an absolute path');
  }
  if (typeof execFileSyncImpl !== 'function') {
    fail('E_GIT_EXEC', 'Git execution boundary must be a function');
  }
  const compiler = typescript ?? loadTypescript(repoRoot);
  const manifest = readManifest(repoRoot);
  const sources = [];
  for (const sourcePath of trackedPaths(repoRoot, execFileSyncImpl)) {
    if (!isTrackedWebSourcePath(sourcePath)) continue;
    try {
      sources.push({
        path: sourcePath,
        text: fs.readFileSync(path.join(repoRoot, sourcePath), 'utf8'),
      });
    } catch {
      fail('E_SOURCE_READ', `could not read tracked source ${sourcePath}`);
    }
  }
  return inspectRouterRscInputs({ typescript: compiler, manifest, sources });
}
