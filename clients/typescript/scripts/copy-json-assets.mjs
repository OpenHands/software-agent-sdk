// Copy non-TS assets (.json) from src/ to dist/ after tsc runs.
// tsc does not emit imported JSON files even with resolveJsonModule;
// without this step, `import x from './acp-providers.json'` resolves at
// compile time but fails at runtime in the published package.

import { copyFile, mkdir, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'src');
const DIST = path.join(ROOT, 'dist');
const EXTENSIONS = new Set(['.json']);

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await walk(full)));
    } else if (EXTENSIONS.has(path.extname(entry.name))) {
      files.push(full);
    }
  }
  return files;
}

const sources = await walk(SRC);
for (const src of sources) {
  const rel = path.relative(SRC, src);
  const dest = path.join(DIST, rel);
  await mkdir(path.dirname(dest), { recursive: true });
  await copyFile(src, dest);
}

if (sources.length > 0) {
  console.log(`copy-json-assets: copied ${sources.length} file(s) to dist/`);
}
