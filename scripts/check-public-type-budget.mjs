#!/usr/bin/env node

import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distRoot = resolve(repositoryRoot, 'dist');
const budgetPath = resolve(repositoryRoot, 'config/public-type-budget.json');

const publicEntryPoints = [
  'index.d.ts',
  'clients.d.ts',
  'client/http-client.d.ts',
  'client/device-flow-client.d.ts',
  'events/remote-events-list.d.ts',
  'workspace/remote-workspace.d.ts',
];

const normalizePath = (value) => value.replaceAll('\\', '/');
const normalizeTypeText = (value) => value.replace(/\s+/g, ' ').trim();

function declarationName(node) {
  if (!node.name) return null;
  if (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name)) {
    return node.name.text;
  }
  if (ts.isStringLiteral(node.name) || ts.isNumericLiteral(node.name)) {
    return node.name.text;
  }
  return node.name.getText();
}

function pathLabel(node) {
  const name = declarationName(node);
  if (ts.isInterfaceDeclaration(node)) return `interface:${name}`;
  if (ts.isTypeAliasDeclaration(node)) return `type:${name}`;
  if (ts.isClassDeclaration(node)) return `class:${name ?? '<anonymous>'}`;
  if (ts.isFunctionDeclaration(node)) return `function:${name ?? '<anonymous>'}`;
  if (ts.isPropertySignature(node) || ts.isPropertyDeclaration(node)) {
    return `property:${name ?? '<computed>'}`;
  }
  if (ts.isMethodSignature(node) || ts.isMethodDeclaration(node)) {
    return `method:${name ?? '<computed>'}`;
  }
  if (ts.isGetAccessorDeclaration(node)) return `getter:${name ?? '<computed>'}`;
  if (ts.isSetAccessorDeclaration(node)) return `setter:${name ?? '<computed>'}`;
  if (ts.isParameter(node)) return `parameter:${name ?? '<destructured>'}`;
  if (ts.isTypeParameterDeclaration(node)) return `type-parameter:${name}`;
  if (ts.isIndexSignatureDeclaration(node)) return 'index-signature';
  if (ts.isCallSignatureDeclaration(node)) return 'call-signature';
  if (ts.isConstructSignatureDeclaration(node)) return 'construct-signature';
  if (ts.isConstructorDeclaration(node)) return 'constructor';
  return null;
}

function isPrivateDeclaration(node) {
  return Boolean(
    node.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.PrivateKeyword)
  );
}

function containingTypeNode(node) {
  let current = node;
  while (current.parent) {
    const parent = current.parent;
    if (
      ((ts.isPropertySignature(parent) ||
        ts.isPropertyDeclaration(parent) ||
        ts.isParameter(parent) ||
        ts.isIndexSignatureDeclaration(parent) ||
        ts.isMethodSignature(parent) ||
        ts.isMethodDeclaration(parent) ||
        ts.isFunctionDeclaration(parent) ||
        ts.isGetAccessorDeclaration(parent) ||
        ts.isSetAccessorDeclaration(parent)) &&
        parent.type === current) ||
      (ts.isTypeAliasDeclaration(parent) && parent.type === current)
    ) {
      return current;
    }
    current = parent;
  }
  return node;
}

function resolveDeclarationModule(fromFile, moduleSpecifier) {
  if (!moduleSpecifier.startsWith('.')) return null;
  const base = resolve(dirname(fromFile), moduleSpecifier);
  const declarationBase = base.replace(/\.(?:m|c)?js$/, '.d.ts');
  const candidates = [declarationBase, base, `${base}.d.ts`, resolve(base, 'index.d.ts')];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function collectReachableDeclarationFiles() {
  const pending = publicEntryPoints.map((entry) => resolve(distRoot, entry));
  const visited = new Set();

  while (pending.length > 0) {
    const declarationFile = pending.pop();
    if (!declarationFile || visited.has(declarationFile)) continue;
    if (!existsSync(declarationFile)) {
      throw new Error(
        `Missing ${normalizePath(relative(repositoryRoot, declarationFile))}. Run npm run build first.`
      );
    }
    visited.add(declarationFile);

    const source = ts.createSourceFile(
      declarationFile,
      readFileSync(declarationFile, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS
    );

    const followSpecifier = (specifier) => {
      const resolved = resolveDeclarationModule(declarationFile, specifier);
      if (resolved && !normalizePath(relative(distRoot, resolved)).startsWith('generated/')) {
        pending.push(resolved);
      }
    };

    const visit = (node) => {
      if (
        (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      ) {
        followSpecifier(node.moduleSpecifier.text);
      } else if (
        ts.isImportTypeNode(node) &&
        ts.isLiteralTypeNode(node.argument) &&
        ts.isStringLiteral(node.argument.literal)
      ) {
        followSpecifier(node.argument.literal.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  return [...visited].sort();
}

function collectWeakSites() {
  const sites = [];
  const printer = ts.createPrinter({ removeComments: true });

  for (const declarationFile of collectReachableDeclarationFiles()) {
    const source = ts.createSourceFile(
      declarationFile,
      readFileSync(declarationFile, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS
    );
    const modulePath = normalizePath(relative(distRoot, declarationFile));

    const visit = (node, semanticPath) => {
      if (isPrivateDeclaration(node)) return;
      const label = pathLabel(node);
      const nextPath = label ? [...semanticPath, label] : semanticPath;

      const kind =
        node.kind === ts.SyntaxKind.AnyKeyword
          ? 'any'
          : node.kind === ts.SyntaxKind.UnknownKeyword
            ? 'unknown'
            : null;
      if (kind) {
        const typeNode = containingTypeNode(node);
        sites.push({
          baseId: `${modulePath}::${nextPath.join('/')}::${kind}`,
          kind,
          fingerprint: normalizeTypeText(
            printer.printNode(ts.EmitHint.Unspecified, typeNode, source)
          ),
          module: modulePath,
          position: node.pos,
        });
      }

      ts.forEachChild(node, (child) => visit(child, nextPath));
    };
    visit(source, []);
  }

  const duplicateCounts = new Map();
  return sites
    .sort(
      (left, right) => left.baseId.localeCompare(right.baseId) || left.position - right.position
    )
    .map(({ baseId, position: _position, ...site }) => {
      const occurrence = (duplicateCounts.get(baseId) ?? 0) + 1;
      duplicateCounts.set(baseId, occurrence);
      return {
        id: occurrence === 1 ? baseId : `${baseId}[${occurrence}]`,
        ...site,
      };
    });
}

function validateBudgetDocument(budget) {
  if (budget.version !== 1 || !Array.isArray(budget.groups)) {
    throw new Error('public-type-budget.json must contain version 1 and a groups array.');
  }
  const seen = new Set();
  const entries = [];
  for (const group of budget.groups) {
    for (const field of ['category', 'reason', 'owner']) {
      if (typeof group[field] !== 'string' || group[field].trim() === '') {
        throw new Error(`Budget group ${group.category ?? '<unknown>'} is missing ${field}.`);
      }
    }
    if (!Array.isArray(group.entries)) {
      throw new Error(`Budget group ${group.category} is missing its entries array.`);
    }
    for (const location of group.entries) {
      const entry = Array.isArray(location)
        ? {
            id: location[0],
            kind: location[1],
            fingerprint: location[2],
          }
        : location;
      for (const field of ['id', 'kind', 'fingerprint']) {
        if (typeof entry[field] !== 'string' || entry[field].trim() === '') {
          throw new Error(`Budget entry ${entry.id ?? '<unknown>'} is missing ${field}.`);
        }
      }
      if (entry.kind !== 'any' && entry.kind !== 'unknown') {
        throw new Error(`Budget entry ${entry.id} has unsupported kind ${entry.kind}.`);
      }
      if (seen.has(entry.id)) {
        throw new Error(`Duplicate public weak-type budget entry: ${entry.id}`);
      }
      seen.add(entry.id);
      entries.push({
        ...entry,
        category: group.category,
        reason: group.reason,
        owner: group.owner,
      });
    }
  }
  return entries;
}

export function compareBudget(currentSites, budgetEntries) {
  const errors = [];
  const current = new Map(currentSites.map((site) => [site.id, site]));
  const budget = new Map(budgetEntries.map((entry) => [entry.id, entry]));

  for (const site of currentSites) {
    const approved = budget.get(site.id);
    if (!approved) {
      errors.push(
        `New public ${site.kind} type: ${site.id} (${site.fingerprint}). ` +
          'Tighten the declaration or add a reviewed budget entry with a reason and owner.'
      );
      continue;
    }
    if (approved.kind !== site.kind || approved.fingerprint !== site.fingerprint) {
      errors.push(
        `Public weak type changed at ${site.id}: budgeted ` +
          `${approved.kind} ${approved.fingerprint}, current ${site.kind} ${site.fingerprint}.`
      );
    }
  }

  for (const entry of budgetEntries) {
    if (!current.has(entry.id)) {
      errors.push(
        `Public weak type was removed: ${entry.id}. Remove its budget entry to lower the budget permanently.`
      );
    }
  }
  return errors;
}

function defaultCategory(modulePath) {
  if (
    modulePath === 'client/http-client.d.ts' ||
    modulePath === 'client/openhands-client.d.ts' ||
    modulePath === 'client/server-client.d.ts'
  ) {
    return 'transport-internal';
  }
  if (modulePath === 'client/cloud-client.d.ts') return 'opaque-proxy';
  return 'domain-contract';
}

function printCurrentSites() {
  const groups = new Map();
  for (const site of collectWeakSites()) {
    const category = defaultCategory(site.module);
    const reason =
      category === 'transport-internal'
        ? 'Generic HTTP transport accepts opaque payloads; domain clients must layer generated types over it.'
        : category === 'opaque-proxy'
          ? 'Cloud proxy payload is deliberately opaque pending a separately versioned Cloud API contract.'
          : 'Existing public domain boundary; tracked for incremental migration under OSS-6127.';
    const key = `${category}\0${reason}`;
    const group = groups.get(key) ?? {
      category,
      reason,
      owner: 'neubig',
      entries: [],
    };
    group.entries.push([site.id, site.kind, site.fingerprint]);
    groups.set(key, group);
  }
  process.stdout.write(
    `${JSON.stringify({ version: 1, groups: [...groups.values()] }, null, 2)}\n`
  );
}

function selfTest() {
  const approved = [
    {
      id: 'models/example.d.ts::interface:Example/property:value::unknown',
      kind: 'unknown',
      fingerprint: 'unknown',
    },
  ];
  assert.deepEqual(
    compareBudget(
      [
        {
          id: approved[0].id,
          kind: 'unknown',
          fingerprint: 'unknown',
        },
      ],
      approved
    ),
    []
  );
  assert.match(
    compareBudget(
      [
        ...approved,
        {
          id: 'models/example.d.ts::interface:Example/property:extra::any',
          kind: 'any',
          fingerprint: 'any',
        },
      ],
      approved
    )[0],
    /New public any type/
  );
  assert.match(
    compareBudget([{ id: approved[0].id, kind: 'unknown', fingerprint: 'unknown[]' }], approved)[0],
    /changed/
  );
  assert.match(compareBudget([], approved)[0], /lower the budget permanently/);
  assert.throws(
    () =>
      validateBudgetDocument({
        version: 1,
        groups: [
          {
            category: 'domain-contract',
            reason: 'Tracked legacy boundary.',
            owner: '',
            entries: [[approved[0].id, approved[0].kind, approved[0].fingerprint]],
          },
        ],
      }),
    /missing owner/
  );
  process.stdout.write('public-type-budget self-test passed\n');
}

function main() {
  if (process.argv.includes('--self-test')) {
    selfTest();
    return;
  }
  if (process.argv.includes('--print-current')) {
    printCurrentSites();
    return;
  }

  if (!existsSync(budgetPath)) {
    throw new Error(
      'Missing config/public-type-budget.json. Run with --print-current to inspect the current sites.'
    );
  }
  const budget = JSON.parse(readFileSync(budgetPath, 'utf8'));
  const budgetEntries = validateBudgetDocument(budget);
  const sites = collectWeakSites();
  const errors = compareBudget(sites, budgetEntries);
  if (errors.length > 0) {
    process.stderr.write(`${errors.map((error) => `- ${error}`).join('\n')}\n`);
    process.exitCode = 1;
    return;
  }

  const counts = sites.reduce(
    (result, site) => {
      result[site.kind] += 1;
      return result;
    },
    { any: 0, unknown: 0 }
  );
  process.stdout.write(
    `Public weak-type budget unchanged: ${sites.length} sites ` +
      `(${counts.any} any, ${counts.unknown} unknown).\n`
  );
}

main();
