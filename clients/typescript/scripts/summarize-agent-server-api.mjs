import { spawnSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const option = argv[index];
    const value = argv[index + 1];
    if (!option?.startsWith('--') || value === undefined) {
      throw new Error(`Expected --option value arguments, received ${JSON.stringify(argv)}`);
    }
    values[option.slice(2)] = value;
  }
  for (const required of ['before', 'after', 'output']) {
    if (!values[required]) {
      throw new Error(`Missing required --${required} argument`);
    }
  }
  return values;
}

function exportedTypeNames(source) {
  return new Set(
    [...source.matchAll(/^export type ([A-Za-z_$][\w$]*)\b/gm)].map((match) => match[1])
  );
}

function sortedDifference(left, right) {
  return [...left].filter((value) => !right.has(value)).sort();
}

function list(names) {
  if (names.length === 0) {
    return 'none';
  }
  const shown = names.slice(0, 25).map((name) => `\`${name}\``);
  if (names.length > shown.length) {
    shown.push(`and ${names.length - shown.length} more`);
  }
  return shown.join(', ');
}

function lineCounts(beforePath, afterPath) {
  const result = spawnSync('git', ['diff', '--no-index', '--numstat', beforePath, afterPath], {
    encoding: 'utf8',
  });
  if (result.error) {
    throw result.error;
  }
  if (![0, 1].includes(result.status ?? -1)) {
    throw new Error(result.stderr || `git diff exited with ${result.status}`);
  }
  if (result.status === 0) {
    return { added: 0, removed: 0 };
  }
  const [added, removed] = result.stdout.trim().split(/\s+/, 2).map(Number);
  if (!Number.isFinite(added) || !Number.isFinite(removed)) {
    throw new Error(`Unable to parse git diff --numstat output: ${result.stdout}`);
  }
  return { added, removed };
}

const args = parseArgs(process.argv.slice(2));
const beforePath = resolve(args.before);
const afterPath = resolve(args.after);
const outputPath = resolve(args.output);
const [before, after] = await Promise.all([
  readFile(beforePath, 'utf8'),
  readFile(afterPath, 'utf8'),
]);
const beforeNames = exportedTypeNames(before);
const afterNames = exportedTypeNames(after);
const addedTypes = sortedDifference(afterNames, beforeNames);
const removedTypes = sortedDifference(beforeNames, afterNames);
const lines = lineCounts(beforePath, afterPath);

const markdown =
  before === after
    ? 'No generated TypeScript contract changes.\n'
    : [
        `- Generated TypeScript diff: **+${lines.added} / -${lines.removed} lines**`,
        `- Added exported types: ${list(addedTypes)}`,
        `- Removed exported types: ${list(removedTypes)}`,
        '- Existing exported declarations may also contain field-level changes; review the generated diff.',
        '',
      ].join('\n');

await writeFile(outputPath, markdown);
process.stdout.write(markdown);
