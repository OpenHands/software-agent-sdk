#!/usr/bin/env node
/**
 * Endpoint audit — reconciles the client's HTTP surface against the
 * agent-server OpenAPI spec:
 *
 *   - The client is GATED against the agent-server spec only. Any endpoint the
 *     client calls that the agent-server does NOT expose fails the gate — this
 *     is an agent-server client, so off-contract calls are surfaced as errors
 *     rather than silently allowlisted.
 *   - To explain WHERE each off-contract call actually goes, extra `classify`
 *     specs (e.g. the cloud app at app.all-hands.dev) are loaded and used only
 *     to label each gated endpoint with the backend that serves it, or to mark
 *     it as served by NO known backend (genuinely unsupported).
 *   - missing API — agent-server exposes an endpoint the client does NOT
 *     implement (informational, not gated).
 *
 * Specs are fetched live from each backend (preferred) or read from a committed
 * fallback file. A `gate` spec with neither is a hard error; a `classify` spec
 * that is unavailable is skipped (its endpoints just go unlabeled).
 *
 * Exit code is non-zero on gating violations (see config.gate).
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'endpoint-audit.config.json'), 'utf8'));

const VERBS = ['get', 'post', 'put', 'patch', 'delete'];

// "/api/x/${id}" or "/api/x/{name}" -> "/api/x/{}", strip query + trailing slash
const norm = (verb, p) =>
  `${verb.toUpperCase()} ${
    p
      .split('?')[0]
      .replace(/\$?\{[^{}]*\}/g, '{}')
      .replace(/\/+$/, '') || '/'
  }`;

// ---------------------------------------------------------------------------
// 1. Backend specs: agent-server (gate) + others (classify)
// ---------------------------------------------------------------------------
async function loadSpec(spec) {
  if (spec.url) {
    try {
      const res = await fetch(spec.url, { signal: AbortSignal.timeout(8000) });
      if (res.ok) return await res.json();
      console.warn(`  ! ${spec.name}: ${spec.url} -> HTTP ${res.status}, falling back to file`);
    } catch (e) {
      console.warn(
        `  ! ${spec.name}: ${spec.url} unreachable (${e.message}), falling back to file`
      );
    }
  }
  if (spec.file && fs.existsSync(path.join(ROOT, spec.file)))
    return JSON.parse(fs.readFileSync(path.join(ROOT, spec.file), 'utf8'));
  if (spec.role === 'classify') {
    console.warn(
      `  ! ${spec.name}: no reachable url and no fallback file — skipping (classify only)`
    );
    return null;
  }
  throw new Error(`spec "${spec.name}": no reachable url and no fallback file`);
}

const specToSet = (doc) => {
  const set = new Set(); // normalized "VERB /path"
  for (const [p, methods] of Object.entries(doc.paths ?? {}))
    for (const verb of Object.keys(methods)) if (VERBS.includes(verb)) set.add(norm(verb, p));
  return set;
};

const gate = new Set(); // union of all `gate` specs (the agent-server contract)
const classifiers = []; // [{ name, set }] for labelling off-contract calls
for (const spec of cfg.specs) {
  const doc = await loadSpec(spec);
  if (!doc) continue;
  const set = specToSet(doc);
  if (spec.role === 'classify') classifiers.push({ name: spec.name, set });
  else for (const k of set) gate.add(k);
}

// ---------------------------------------------------------------------------
// 2. Client surface: static extraction of every endpoint the client can call
// ---------------------------------------------------------------------------
function walk(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === '__tests__' || e.name === 'node_modules') continue;
      out.push(...walk(full));
    } else if (e.name.endsWith('.ts')) out.push(full);
  }
  return out;
}

const VERB_CALL = /\.(get|post|put|patch|delete)\s*(<[^>]*>)?\(/;
const PATH_LIT = /[`'"](\/(?:api|server_info|alive|health|ready)[^`'"]*)[`'"]/;

const client = new Set();
for (const glob of cfg.clientGlobs) {
  for (const file of walk(path.join(ROOT, glob))) {
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((ln, i) => {
      // direct `.get/post/...(` calls, plus generic `.request({ method, url })`
      let verb = ln.match(VERB_CALL)?.[1];
      let win = 3;
      if (!verb && /\.request\s*\(/.test(ln)) {
        const block = lines.slice(i, i + 6).join('\n');
        verb = block.match(/method:\s*['"`](GET|POST|PUT|PATCH|DELETE)['"`]/i)?.[1];
        win = 6;
      }
      if (!verb) return;
      const p = lines
        .slice(i, i + win)
        .join('\n')
        .match(PATH_LIT)?.[1];
      if (p) client.add(norm(verb, p));
    });
  }
}

// ---------------------------------------------------------------------------
// 3. Diff + classify
// ---------------------------------------------------------------------------
const sorted = (it) => [...it].sort();
const ignoredApi = (k) => (cfg.ignoreServerOnly ?? []).some((pre) => k.includes(pre));

const NO_BACKEND = '(no known backend)';
const classify = (k) => classifiers.find((c) => c.set.has(k))?.name ?? NO_BACKEND;

// Every client call the agent-server does NOT expose is off-contract -> gated.
const mismatch = sorted([...client].filter((k) => !gate.has(k)));
const byBackend = {}; // backend name -> [endpoints]
for (const k of mismatch) (byBackend[classify(k)] ??= []).push(k);

const missingApi = sorted([...gate].filter((k) => !client.has(k) && !ignoredApi(k)));

// ---------------------------------------------------------------------------
// 4. Report
// ---------------------------------------------------------------------------
const section = (title, items) => {
  console.log(`\n${title} (${items.length})`);
  for (const k of items) console.log(`  ${k}`);
};
console.log(
  `\n=== Endpoint audit — agent-server=${gate.size} client=${client.size}` +
    ` classifiers=[${classifiers.map((c) => c.name).join(', ') || 'none'}] ===`
);
console.log(`\n❌ NOT ON AGENT-SERVER — off-contract client calls (${mismatch.length})`);
for (const name of Object.keys(byBackend).sort()) {
  const tag = name === NO_BACKEND ? `${name} ⛔` : `served by: ${name}`;
  console.log(`  ${tag} (${byBackend[name].length})`);
  for (const k of byBackend[name]) console.log(`    ${k}`);
}
section('➕ MISSING API — agent-server has it, client does not implement', missingApi);

fs.mkdirSync(path.join(ROOT, '.audit'), { recursive: true });
fs.writeFileSync(
  path.join(ROOT, '.audit/endpoint-audit.json'),
  JSON.stringify(
    {
      agentServer: gate.size,
      client: client.size,
      classifiers: classifiers.map((c) => c.name),
      mismatch,
      byBackend,
      missingApi,
    },
    null,
    2
  )
);

// ---------------------------------------------------------------------------
// 5. Gate
// ---------------------------------------------------------------------------
const g = cfg.gate ?? {};
const violations = [];
if (g.mismatch !== false && mismatch.length)
  violations.push(`${mismatch.length} off-contract (not on agent-server)`);
if (g.missingApi && missingApi.length) violations.push(`${missingApi.length} missing API`);

if (violations.length) {
  console.error(`\n❌ endpoint audit failed: ${violations.join(', ')}`);
  process.exit(1);
}
console.log('\n✅ endpoint audit passed');
