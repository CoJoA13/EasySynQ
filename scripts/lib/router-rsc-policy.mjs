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
    'isArrowFunction',
    'isAsExpression',
    'isArrayBindingPattern',
    'isBlock',
    'isBinaryExpression',
    'isCallExpression',
    'isCatchClause',
    'isClassDeclaration',
    'isClassExpression',
    'isComputedPropertyName',
    'isElementAccessExpression',
    'isEnumDeclaration',
    'isExportDeclaration',
    'isFunctionDeclaration',
    'isFunctionExpression',
    'isFunctionLike',
    'isIdentifier',
    'isImportDeclaration',
    'isImportEqualsDeclaration',
    'isImportTypeNode',
    'isIndexedAccessTypeNode',
    'isLiteralTypeNode',
    'isModuleBlock',
    'isModuleDeclaration',
    'isNamedExports',
    'isNamedImports',
    'isNamespaceExport',
    'isNamespaceImport',
    'isNoSubstitutionTemplateLiteral',
    'isNonNullExpression',
    'isObjectBindingPattern',
    'isParameter',
    'isParenthesizedExpression',
    'isParenthesizedTypeNode',
    'isPropertyAccessExpression',
    'isSatisfiesExpression',
    'isStringLiteral',
    'isTypeAssertionExpression',
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
  const candidate = unwrapTransparentExpression(typescript, node);
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

function unwrapTransparentExpression(typescript, node) {
  let current = node;
  while (current !== undefined
      && (typescript.isParenthesizedExpression(current)
        || typescript.isAsExpression(current)
        || typescript.isSatisfiesExpression(current)
        || typescript.isNonNullExpression(current)
        || typescript.isTypeAssertionExpression(current))) {
    current = current.expression;
  }
  return current;
}

function unwrapTransparentType(typescript, node) {
  let current = node;
  while (current !== undefined && typescript.isParenthesizedTypeNode(current)) {
    current = current.type;
  }
  return current;
}

function literalTypeText(typescript, node) {
  const candidate = unwrapTransparentType(typescript, node);
  return candidate !== undefined && typescript.isLiteralTypeNode(candidate)
    ? literalText(typescript, candidate.literal)
    : undefined;
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

function createScope(parent, region = parent?.region) {
  return { parent, region, bindings: new Map() };
}

function nearestFunctionScope(scope) {
  let current = scope;
  while (current.parent !== undefined && !current.functionScope && !current.sourceScope) {
    current = current.parent;
  }
  return current;
}

function bindName(
  typescript,
  name,
  scope,
  binding = { kind: 'local' },
  reuseExisting = false,
) {
  if (typescript.isIdentifier(name)) {
    if (reuseExisting && scope.bindings.has(name.text)) {
      return scope.bindings.get(name.text);
    }
    if (binding.homeRegion === undefined) binding.homeRegion = scope.region;
    scope.bindings.set(name.text, binding);
    return binding;
  }
  if (typescript.isObjectBindingPattern(name) || typescript.isArrayBindingPattern(name)) {
    for (const element of name.elements) {
      if (element.name !== undefined) {
        bindName(typescript, element.name, scope, undefined, reuseExisting);
      }
    }
  }
  return undefined;
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
  const rootScope = createScope(undefined, sourceFile);
  rootScope.sourceScope = true;
  const scopeByNode = new WeakMap();
  const writeCandidates = [];
  const assignmentCandidates = [];
  const callCandidates = [];
  const functionRegionByBinding = new Map();
  let candidateOrder = 0;

  function booleanLiteralValue(node) {
    const candidate = unwrapTransparentExpression(typescript, node);
    if (candidate?.kind === typescript.SyntaxKind.TrueKeyword) return true;
    if (candidate?.kind === typescript.SyntaxKind.FalseKeyword) return false;
    return undefined;
  }

  const NORMAL_COMPLETION = 'normal';
  const RETURN_COMPLETION = 'return';
  const THROW_COMPLETION = 'throw';
  const BREAK_COMPLETION = 'break';
  const CONTINUE_COMPLETION = 'continue';

  function unionOutcomes(...outcomeSets) {
    return new Set(outcomeSets.flatMap((outcomes) => [...outcomes]));
  }

  function sequenceOutcomes(statements) {
    let outcomes = new Set([NORMAL_COMPLETION]);
    for (const statement of statements) {
      const carried = new Set(
        [...outcomes].filter((outcome) => outcome !== NORMAL_COMPLETION),
      );
      outcomes = outcomes.has(NORMAL_COMPLETION)
        ? unionOutcomes(carried, statementOutcomes(statement))
        : carried;
    }
    return outcomes;
  }

  function loopOutcomes(bodyOutcomes, canSkipBody) {
    const outcomes = new Set(canSkipBody ? [NORMAL_COMPLETION] : []);
    for (const outcome of bodyOutcomes) {
      if (outcome === RETURN_COMPLETION || outcome === THROW_COMPLETION) {
        outcomes.add(outcome);
      } else {
        // A break exits normally. Normal and continue paths may eventually
        // leave this deliberately bounded loop model.
        outcomes.add(NORMAL_COMPLETION);
      }
    }
    return outcomes;
  }

  function tryOutcomes(node) {
    const fromTry = statementOutcomes(node.tryBlock);
    let incoming = new Set(fromTry);
    if (node.catchClause !== undefined) {
      const fromCatch = statementOutcomes(node.catchClause.block);
      incoming.delete(THROW_COMPLETION);
      if (fromTry.has(THROW_COMPLETION) || fromTry.has(NORMAL_COMPLETION)) {
        incoming = unionOutcomes(incoming, fromCatch);
      }
    }
    if (node.finallyBlock === undefined) return incoming;

    const fromFinally = statementOutcomes(node.finallyBlock);
    const combined = new Set();
    for (const incomingOutcome of incoming) {
      if (fromFinally.has(NORMAL_COMPLETION)) combined.add(incomingOutcome);
      for (const finallyOutcome of fromFinally) {
        if (finallyOutcome !== NORMAL_COMPLETION) combined.add(finallyOutcome);
      }
    }
    return combined;
  }

  function statementOutcomes(node) {
    if (node === undefined) return new Set([NORMAL_COMPLETION]);
    if (node.kind === typescript.SyntaxKind.ReturnStatement) {
      return new Set([RETURN_COMPLETION]);
    }
    if (node.kind === typescript.SyntaxKind.ThrowStatement) {
      return new Set([THROW_COMPLETION]);
    }
    if (node.kind === typescript.SyntaxKind.BreakStatement) {
      return new Set([BREAK_COMPLETION]);
    }
    if (node.kind === typescript.SyntaxKind.ContinueStatement) {
      return new Set([CONTINUE_COMPLETION]);
    }
    if (typescript.isBlock(node) || typescript.isSourceFile(node)) {
      return sequenceOutcomes(node.statements);
    }
    if (node.kind === typescript.SyntaxKind.IfStatement) {
      const condition = booleanLiteralValue(node.expression);
      const fromThen = statementOutcomes(node.thenStatement);
      const fromElse = statementOutcomes(node.elseStatement);
      if (condition === true) return fromThen;
      if (condition === false) return fromElse;
      return unionOutcomes(fromThen, fromElse);
    }
    if (node.kind === typescript.SyntaxKind.TryStatement) return tryOutcomes(node);
    if (node.kind === typescript.SyntaxKind.DoStatement) {
      return loopOutcomes(statementOutcomes(node.statement), false);
    }
    if (node.kind === typescript.SyntaxKind.WhileStatement
        || node.kind === typescript.SyntaxKind.ForStatement) {
      const expression = node.kind === typescript.SyntaxKind.WhileStatement
        ? node.expression
        : node.condition;
      const condition = expression === undefined ? true : booleanLiteralValue(expression);
      if (condition === false) return new Set([NORMAL_COMPLETION]);
      return loopOutcomes(statementOutcomes(node.statement), condition !== true);
    }
    if (node.kind === typescript.SyntaxKind.ForInStatement
        || node.kind === typescript.SyntaxKind.ForOfStatement) {
      return loopOutcomes(statementOutcomes(node.statement), true);
    }
    if (node.kind === typescript.SyntaxKind.SwitchStatement) {
      let outcomes = new Set([NORMAL_COMPLETION]);
      for (const clause of node.caseBlock.clauses) {
        outcomes = unionOutcomes(outcomes, sequenceOutcomes(clause.statements));
      }
      if (outcomes.delete(BREAK_COMPLETION)) outcomes.add(NORMAL_COMPLETION);
      return outcomes;
    }
    if (node.kind === typescript.SyntaxKind.LabeledStatement) {
      const outcomes = statementOutcomes(node.statement);
      if (outcomes.delete(BREAK_COMPLETION)) outcomes.add(NORMAL_COMPLETION);
      return outcomes;
    }
    return new Set([NORMAL_COMPLETION]);
  }

  function reachabilityFor(outcomes, acceptedOutcomes) {
    const acceptedCount = [...outcomes]
      .filter((outcome) => acceptedOutcomes.has(outcome)).length;
    if (acceptedCount === 0) return 'never';
    return acceptedCount === outcomes.size ? 'always' : 'maybe';
  }

  function combineExecutionModes(left, right) {
    if (left === 'never' || right === 'never') return 'never';
    return left === 'maybe' || right === 'maybe' ? 'maybe' : 'always';
  }

  function precedingNormalMode(node, region) {
    let current = node;
    let mode = 'always';
    while (current !== region && current.parent !== undefined) {
      const statements = current.parent.statements;
      if (Array.isArray(statements)) {
        const index = statements.indexOf(current);
        if (index >= 0) {
          const outcomes = sequenceOutcomes(statements.slice(0, index));
          mode = combineExecutionModes(
            mode,
            reachabilityFor(outcomes, new Set([NORMAL_COMPLETION])),
          );
          if (mode === 'never') return mode;
        }
      }
      current = current.parent;
    }
    return mode;
  }

  function executionMode(node, region) {
    let current = node;
    let mode = 'always';
    while (current !== region && current.parent !== undefined) {
      const parent = current.parent;
      if (parent.kind === typescript.SyntaxKind.IfStatement
          && current !== parent.expression) {
        const condition = booleanLiteralValue(parent.expression);
        const isThen = current === parent.thenStatement;
        const branchRuns = condition === undefined
          ? undefined
          : (isThen ? condition : !condition);
        if (branchRuns === false) return 'never';
        if (branchRuns === undefined) mode = 'maybe';
      } else if (parent.kind === typescript.SyntaxKind.ConditionalExpression
          && current !== parent.condition) {
        const condition = booleanLiteralValue(parent.condition);
        const isThen = current === parent.whenTrue;
        const branchRuns = condition === undefined
          ? undefined
          : (isThen ? condition : !condition);
        if (branchRuns === false) return 'never';
        if (branchRuns === undefined) mode = 'maybe';
      } else if (parent.kind === typescript.SyntaxKind.ForStatement
          && (current === parent.statement || current === parent.incrementor)) {
        const condition = parent.condition === undefined
          ? true
          : booleanLiteralValue(parent.condition);
        if (condition === false) return 'never';
        if (current === parent.incrementor) {
          const bodyMode = reachabilityFor(
            statementOutcomes(parent.statement),
            new Set([NORMAL_COMPLETION, CONTINUE_COMPLETION]),
          );
          if (bodyMode === 'never') return 'never';
          mode = combineExecutionModes(mode, bodyMode);
        }
        if (condition === undefined) mode = 'maybe';
      } else if ((parent.kind === typescript.SyntaxKind.ForInStatement
          || parent.kind === typescript.SyntaxKind.ForOfStatement)
          && current === parent.statement) {
        mode = 'maybe';
      } else if (parent.kind === typescript.SyntaxKind.WhileStatement
          && current === parent.statement) {
        const condition = booleanLiteralValue(parent.expression);
        if (condition === false) return 'never';
        if (condition === undefined) mode = 'maybe';
      } else if (parent.kind === typescript.SyntaxKind.DoStatement
          && current === parent.expression) {
        const bodyMode = reachabilityFor(
          statementOutcomes(parent.statement),
          new Set([NORMAL_COMPLETION, CONTINUE_COMPLETION]),
        );
        if (bodyMode === 'never') return 'never';
        mode = combineExecutionModes(mode, bodyMode);
      } else if (parent.kind === typescript.SyntaxKind.CatchClause
          && current === parent.block) {
        const fromTry = statementOutcomes(parent.parent.tryBlock);
        if (!fromTry.has(THROW_COMPLETION) && !fromTry.has(NORMAL_COMPLETION)) return 'never';
        if (fromTry.size !== 1 || !fromTry.has(THROW_COMPLETION)) mode = 'maybe';
      } else if (parent.kind === typescript.SyntaxKind.SwitchStatement
          && current === parent.caseBlock) {
        mode = 'maybe';
      } else if (typescript.isBinaryExpression(parent)
          && current === parent.right
          && (parent.operatorToken.kind === typescript.SyntaxKind.AmpersandAmpersandToken
            || parent.operatorToken.kind === typescript.SyntaxKind.BarBarToken
            || parent.operatorToken.kind === typescript.SyntaxKind.QuestionQuestionToken)) {
        const left = booleanLiteralValue(parent.left);
        if ((parent.operatorToken.kind === typescript.SyntaxKind.AmpersandAmpersandToken
              && left === false)
            || (parent.operatorToken.kind === typescript.SyntaxKind.BarBarToken
              && left === true)) {
          return 'never';
        }
        if (parent.operatorToken.kind === typescript.SyntaxKind.QuestionQuestionToken
            || left === undefined) {
          mode = 'maybe';
        }
      }
      current = parent;
    }
    return combineExecutionModes(mode, precedingNormalMode(node, region));
  }

  function collect(node, incomingScope, functionBody = false) {
    let scope = incomingScope;
    let declarationBinding;

    if (typescript.isFunctionDeclaration(node) && node.name !== undefined) {
      declarationBinding = bindName(
        typescript,
        node.name,
        incomingScope,
        { kind: 'local' },
        true,
      );
      functionRegionByBinding.set(declarationBinding, node);
    } else if (typescript.isClassDeclaration(node) && node.name !== undefined) {
      bindName(typescript, node.name, incomingScope);
    } else if (typescript.isModuleDeclaration(node) && typescript.isIdentifier(node.name)) {
      bindName(typescript, node.name, incomingScope);
    } else if (typescript.isEnumDeclaration(node)) {
      bindName(typescript, node.name, incomingScope);
    }

    if (typescript.isFunctionLike(node)) {
      scope = createScope(incomingScope, node);
      scope.functionScope = true;
      if ((typescript.isFunctionExpression(node) || typescript.isFunctionDeclaration(node))
          && node.name !== undefined) {
        const functionBinding = bindName(
          typescript,
          node.name,
          scope,
          declarationBinding,
        );
        functionRegionByBinding.set(functionBinding, node);
      }
      for (const parameter of node.parameters ?? []) bindName(typescript, parameter.name, scope);
    } else if ((typescript.isClassExpression(node) || typescript.isClassDeclaration(node))) {
      scope = createScope(incomingScope);
      if (node.name !== undefined) bindName(typescript, node.name, scope);
    } else if (typescript.isModuleDeclaration(node)) {
      scope = createScope(incomingScope);
    } else if (typescript.isEnumDeclaration(node)) {
      scope = createScope(incomingScope);
      bindName(typescript, node.name, scope);
    } else if ((typescript.isBlock(node) && !functionBody)
        || typescript.isModuleBlock(node)
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
      const binding = bindName(
        typescript,
        node.name,
        bindingScope,
        { kind: 'local' },
        !isBlockScoped,
      );
      if (typescript.isIdentifier(node.name) && node.initializer !== undefined) {
        writeCandidates.push({
          binding,
          expression: node.initializer,
          node,
          order: candidateOrder++,
          position: node.initializer.end,
          region: scope.region,
          scope,
        });
      }
    } else if (typescript.isBinaryExpression(node)
        && node.operatorToken.kind === typescript.SyntaxKind.EqualsToken
        && typescript.isIdentifier(node.left)) {
      assignmentCandidates.push({
        expression: node.right,
        name: node.left.text,
        node,
        order: candidateOrder++,
        position: node.end,
        region: scope.region,
        scope,
      });
    } else if (typescript.isCatchClause(node) && node.variableDeclaration !== undefined) {
      bindName(typescript, node.variableDeclaration.name, scope);
    } else if (typescript.isParameter(node)) {
      bindName(typescript, node.name, scope);
    }

    if (typescript.isCallExpression(node)) {
      const callee = unwrapTransparentExpression(typescript, node.expression);
      if (typescript.isIdentifier(callee)) {
        callCandidates.push({
          name: callee.text,
          node,
          order: candidateOrder++,
          position: node.end,
          region: scope.region,
          scope,
        });
      }
    }

    typescript.forEachChild(node, (child) => {
      const childIsFunctionBody = typescript.isFunctionLike(node) && child === node.body;
      collect(child, scope, childIsFunctionBody);
    });
  }

  collect(sourceFile, rootScope);

  function requireSpecifier(expression, scope) {
    const candidate = unwrapTransparentExpression(typescript, expression);
    const callee = typescript.isCallExpression(candidate)
      ? unwrapTransparentExpression(typescript, candidate.expression)
      : undefined;
    if (!typescript.isCallExpression(candidate)
        || candidate.arguments.length !== 1
        || !typescript.isIdentifier(callee)
        || callee.text !== 'require'
        || resolveBinding(scope, 'require') !== undefined) {
      return undefined;
    }
    return literalText(typescript, candidate.arguments[0]);
  }

  function callableRegionForExpression(expression) {
    const candidate = unwrapTransparentExpression(typescript, expression);
    return typescript.isArrowFunction(candidate) || typescript.isFunctionExpression(candidate)
      ? candidate
      : undefined;
  }

  for (const candidate of assignmentCandidates) {
    const binding = resolveBinding(candidate.scope, candidate.name);
    if (binding !== undefined) writeCandidates.push({ ...candidate, binding });
  }

  const effectsByRegion = new Map();

  function addEffect(region, effect) {
    const effects = effectsByRegion.get(region) ?? [];
    effects.push(effect);
    effectsByRegion.set(region, effects);
  }

  function executionPosition(node, region, sourcePosition) {
    let current = node;
    while (current !== region && current.parent !== undefined) {
      const parent = current.parent;
      if (parent.kind === typescript.SyntaxKind.ForStatement
          && current === parent.incrementor) {
        return parent.end;
      }
      current = parent;
    }
    return sourcePosition;
  }

  for (const candidate of writeCandidates) {
    const specifier = requireSpecifier(candidate.expression, candidate.scope);
    addEffect(candidate.region, {
      binding: candidate.binding,
      kind: 'write',
      mode: executionMode(candidate.node, candidate.region),
      order: candidate.order,
      position: executionPosition(candidate.node, candidate.region, candidate.position),
      sourcePosition: candidate.position,
      callableRegion: callableRegionForExpression(candidate.expression),
      specifier: isRouterApiSource(specifier) ? specifier : undefined,
    });
  }

  for (const candidate of callCandidates) {
    const binding = resolveBinding(candidate.scope, candidate.name);
    if (binding !== undefined) {
      addEffect(candidate.region, {
        calleeBinding: binding,
        kind: 'call',
        mode: executionMode(candidate.node, candidate.region),
        order: candidate.order,
        position: executionPosition(candidate.node, candidate.region, candidate.position),
        sourcePosition: candidate.position,
        region: candidate.region,
      });
    }
  }

  for (const effects of effectsByRegion.values()) {
    effects.sort((left, right) => left.position - right.position
      || left.sourcePosition - right.sourcePosition
      || (left.kind === right.kind ? left.order - right.order : left.kind === 'call' ? -1 : 1));
  }

  function initialState(binding) {
    return new Set([binding.kind === 'router-namespace' ? binding.specifier : undefined]);
  }

  function unionStates(left, right) {
    return new Set([...left, ...right]);
  }

  function initialCallableState(binding) {
    return new Set([functionRegionByBinding.get(binding)]);
  }

  function applyCallableWrite(binding, state, effect) {
    if (effect.kind !== 'write' || effect.binding !== binding || effect.mode === 'never') {
      return state;
    }
    const changedState = new Set([effect.callableRegion]);
    return effect.mode === 'maybe' ? unionStates(state, changedState) : changedState;
  }

  function reachableCallableHomeState(binding) {
    let state = initialCallableState(binding);
    let reachable = new Set(state);
    for (const effect of effectsByRegion.get(binding.homeRegion) ?? []) {
      state = applyCallableWrite(binding, state, effect);
      reachable = unionStates(reachable, state);
    }
    return reachable;
  }

  function callableHomeStateAt(binding, position) {
    let state = initialCallableState(binding);
    for (const effect of effectsByRegion.get(binding.homeRegion) ?? []) {
      if (effect.position >= position) break;
      state = applyCallableWrite(binding, state, effect);
    }
    return state;
  }

  function callableTargetsAt(binding, region, position, callEnvironment) {
    let state = binding.homeRegion === region
      ? initialCallableState(binding)
      : callEnvironment.has(binding.homeRegion)
        ? callableHomeStateAt(binding, callEnvironment.get(binding.homeRegion))
        : reachableCallableHomeState(binding);
    for (const [callerRegion, callerPosition] of callEnvironment) {
      if (callerRegion === binding.homeRegion || callerRegion === region) continue;
      for (const effect of effectsByRegion.get(callerRegion) ?? []) {
        if (effect.position >= callerPosition) break;
        state = applyCallableWrite(binding, state, effect);
      }
    }
    for (const effect of effectsByRegion.get(region) ?? []) {
      if (effect.position >= position) break;
      state = applyCallableWrite(binding, state, effect);
    }
    return state;
  }

  function runFunction(
    calleeRegion,
    binding,
    inputState,
    activeFunctions,
    callEnvironment,
  ) {
    if (activeFunctions.has(calleeRegion)) return new Set(inputState);
    activeFunctions.add(calleeRegion);
    try {
      return runEffects(
        binding,
        calleeRegion,
        Infinity,
        inputState,
        activeFunctions,
        callEnvironment,
      );
    } finally {
      activeFunctions.delete(calleeRegion);
    }
  }

  function applyEffect(binding, state, effect, activeFunctions, callEnvironment) {
    if (effect.mode === 'never') return state;
    let changedState;
    if (effect.kind === 'write') {
      if (effect.binding !== binding) return state;
      changedState = new Set([effect.specifier]);
    } else {
      changedState = new Set();
      for (const calleeRegion of callableTargetsAt(
        effect.calleeBinding,
        effect.region,
        effect.position,
        callEnvironment,
      )) {
        const calleeEnvironment = new Map(callEnvironment);
        calleeEnvironment.set(effect.region, effect.position);
        changedState = unionStates(
          changedState,
          calleeRegion === undefined
            ? state
            : runFunction(
              calleeRegion,
              binding,
              state,
              activeFunctions,
              calleeEnvironment,
            ),
        );
      }
    }
    return effect.mode === 'maybe' ? unionStates(state, changedState) : changedState;
  }

  function runEffects(
    binding,
    region,
    position,
    inputState,
    activeFunctions,
    callEnvironment,
  ) {
    let state = new Set(inputState);
    for (const effect of effectsByRegion.get(region) ?? []) {
      if (effect.position > position) break;
      state = applyEffect(binding, state, effect, activeFunctions, callEnvironment);
    }
    return state;
  }

  function reachableHomeState(binding, activeFunctions, callEnvironment) {
    let state = initialState(binding);
    let reachable = new Set(state);
    for (const effect of effectsByRegion.get(binding.homeRegion) ?? []) {
      state = applyEffect(binding, state, effect, activeFunctions, callEnvironment);
      reachable = unionStates(reachable, state);
    }
    return reachable;
  }

  function stateAt(binding, region, position) {
    const activeFunctions = new Set();
    const callEnvironment = new Map([[region, position]]);
    const entryState = binding.homeRegion === region
      ? initialState(binding)
      : reachableHomeState(binding, activeFunctions, callEnvironment);
    return runEffects(
      binding,
      region,
      position,
      entryState,
      activeFunctions,
      callEnvironment,
    );
  }

  function routerSpecifier(state) {
    return [...state]
      .filter((specifier) => isRouterApiSource(specifier))
      .sort(compareText)[0];
  }

  function namespaceSpecifierAt(binding, region, position) {
    return routerSpecifier(stateAt(binding, region, position));
  }

  function liveNamespaceSpecifier(binding, region, position) {
    const activeFunctions = new Set();
    const callEnvironment = new Map([[region, Infinity]]);
    const entryState = binding.homeRegion === region
      ? initialState(binding)
      : reachableHomeState(binding, activeFunctions, callEnvironment);
    let state = new Set(entryState);
    let reachableAfterExport;
    for (const effect of effectsByRegion.get(region) ?? []) {
      if (effect.position > position && reachableAfterExport === undefined) {
        reachableAfterExport = new Set(state);
      }
      state = applyEffect(binding, state, effect, activeFunctions, callEnvironment);
      if (effect.position > position) {
        reachableAfterExport = unionStates(reachableAfterExport, state);
      }
    }
    return routerSpecifier(reachableAfterExport ?? state);
  }

  return {
    liveNamespaceSpecifier,
    namespaceSpecifierAt,
    scopeByNode,
    requireSpecifier,
  };
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
  try {
    if (!isObject(sourceFile)) {
      fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible source file');
    }
    const parseDiagnostics = sourceFile.parseDiagnostics;
    if (!Array.isArray(parseDiagnostics)) {
      fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible source file');
    }
    if (parseDiagnostics.length > 0) {
      const diagnostic = parseDiagnostics[0];
      if (!isObject(diagnostic)) {
        fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible parse diagnostic');
      }
      const start = diagnostic.start;
      let line = 1;
      let column = 1;
      if (start !== undefined) {
        if (!Number.isInteger(start) || start < 0) {
          fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible parse diagnostic');
        }
        const location = sourceFile.getLineAndCharacterOfPosition(start);
        if (!isObject(location)
            || !Number.isInteger(location.line)
            || location.line < 0
            || !Number.isInteger(location.character)
            || location.character < 0) {
          fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible parse diagnostic');
        }
        line = location.line + 1;
        column = location.character + 1;
      }
      fail('E_SOURCE_PARSE', `${sourceInput.path}:${line}:${column}: TypeScript parse diagnostic`);
    }
  } catch (error) {
    if (error instanceof RouterRscPolicyError) throw error;
    fail('E_TYPESCRIPT_API', 'TypeScript returned an incompatible parse diagnostic');
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
    const candidate = unwrapTransparentExpression(typescript, expression);
    if (typescript.isIdentifier(candidate)) {
      const binding = resolveBinding(scope, candidate.text);
      const specifier = binding === undefined
        ? undefined
        : lexicalModel.namespaceSpecifierAt(binding, scope.region, candidate.pos);
      return specifier === undefined ? undefined : { kind: 'router-namespace', specifier };
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

    if (typescript.isIndexedAccessTypeNode(node)) {
      const importType = unwrapTransparentType(typescript, node.objectType);
      const specifier = typescript.isImportTypeNode(importType)
        ? literalTypeText(typescript, importType.argument)
        : undefined;
      const symbol = literalTypeText(typescript, node.indexType);
      if (isRouterApiSource(specifier) && FORBIDDEN_APIS.has(symbol)) {
        addViolation('forbidden-router-rsc-api', node.indexType, { symbol, specifier });
      }
    } else if (typescript.isImportTypeNode(node) && node.qualifier !== undefined) {
      const specifier = literalTypeText(typescript, node.argument);
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
          const specifier = binding === undefined
            ? undefined
            : lexicalModel.liveNamespaceSpecifier(binding, scope.region, localNode.pos);
          if (specifier !== undefined) {
            addViolation('forbidden-router-rsc-api', localNode, {
              symbol: '*',
              specifier,
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

function isCanonicalWebSourceRecord(sourcePath) {
  if (!sourcePath.startsWith(WEB_SOURCE_PREFIX) || sourcePath === WEB_SOURCE_PREFIX) return false;
  return sourcePath.split('/').every((segment) => (
    segment.length > 0 && segment !== '.' && segment !== '..'
  ));
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
    if (!isCanonicalWebSourceRecord(sourcePath)
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
