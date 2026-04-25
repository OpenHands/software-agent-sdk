import { existsSync, statSync } from 'node:fs';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIST_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../dist');
const IMPORT_EXPORT_SPECIFIER_PATTERN = /(from\s+|import\s*\()(['"])(\.[^'"]+)\2/g;
const KNOWN_EXTENSIONS = new Set(['.js', '.mjs', '.cjs', '.json', '.node']);
const TARGET_SUFFIXES = ['.js', '.d.ts', '.d.mts', '.d.cts'];

const hasKnownExtension = (specifier) => KNOWN_EXTENSIONS.has(path.posix.extname(specifier));

const normalizeRelativeSpecifier = (specifier, filePath) => {
  if (!specifier.startsWith('.') || hasKnownExtension(specifier)) {
    return specifier;
  }

  const resolvedSpecifierPath = path.resolve(path.dirname(filePath), specifier);

  if (existsSync(resolvedSpecifierPath) && statSync(resolvedSpecifierPath).isDirectory()) {
    return `${specifier}/index.js`;
  }

  return `${specifier}.js`;
};

const walk = async (directory) => {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const resolvedPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        return walk(resolvedPath);
      }

      return resolvedPath;
    })
  );

  return files.flat();
};

const rewriteFile = async (filePath) => {
  const original = await readFile(filePath, 'utf8');
  const rewritten = original.replace(
    IMPORT_EXPORT_SPECIFIER_PATTERN,
    (_fullMatch, prefix, quote, specifier) => {
      const normalizedSpecifier = normalizeRelativeSpecifier(specifier, filePath);
      return `${prefix}${quote}${normalizedSpecifier}${quote}`;
    }
  );

  if (rewritten !== original) {
    await writeFile(filePath, rewritten);
  }
};

const main = async () => {
  const files = await walk(DIST_DIR);
  await Promise.all(
    files
      .filter((filePath) => TARGET_SUFFIXES.some((suffix) => filePath.endsWith(suffix)))
      .map(rewriteFile)
  );
};

await main();
