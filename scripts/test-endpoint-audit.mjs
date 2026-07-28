import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AUDIT_SCRIPT = path.join(REPO_ROOT, 'scripts/endpoint-audit.mjs');
const require = createRequire(import.meta.url);
const { postEndpointAuditReport, renderEndpointAuditReport } = require(
  path.join(REPO_ROOT, '.github/scripts/post-endpoint-audit-report.cjs')
);
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'endpoint-audit-'));

try {
  fs.mkdirSync(path.join(fixtureRoot, 'src/client'), { recursive: true });

  fs.writeFileSync(
    path.join(fixtureRoot, 'openapi.json'),
    JSON.stringify({
      openapi: '3.1.0',
      paths: {
        '/api/supported': { get: {} },
        '/api/request-supported/{id}': { post: {} },
        '/api/covered/{id}': { get: {} },
        '/api/server-only': { get: {} },
      },
    })
  );
  fs.writeFileSync(
    path.join(fixtureRoot, 'src/client/agent-server-client.ts'),
    [
      "client.get<Array<Item | null>>('/api/supported');",
      'client.request<Response>({',
      "  method: 'POST',",
      '  url: `/api/request-supported/${id}`,',
      '});',
      "client.post('/api/client-only');",
      "client.get('/health');",
      '',
    ].join('\n')
  );
  fs.writeFileSync(
    path.join(fixtureRoot, 'src/client/cloud-client.ts'),
    [
      'cloud.get(`/api/v1/config/models/search${buildQuerySuffix(params)}`);',
      "cloud.get('/api/cloud-only');",
      '',
    ].join('\n')
  );
  fs.writeFileSync(
    path.join(fixtureRoot, 'src/client/shared-client.ts'),
    ["shared.get('/api/shared-events/search');", ''].join('\n')
  );
  fs.writeFileSync(
    path.join(fixtureRoot, 'endpoint-audit.config.json'),
    JSON.stringify({
      specs: [{ name: 'agent-server', role: 'gate', file: 'openapi.json' }],
      clientGlobs: ['src/client'],
      excludeClientFiles: ['src/client/cloud-client.ts', 'src/client/shared-client.ts'],
      allowClientOnly: [
        {
          endpoints: ['GET /health'],
          reason: 'Operational endpoint.',
          owner: 'Runtime maintainers',
        },
      ],
      coveredServerOperations: [
        {
          endpoints: ['GET /api/covered/{}'],
          reason: 'Exposed as a browser URL.',
          owner: 'Client maintainers',
        },
      ],
      gate: { mismatch: false, missingApi: false },
    })
  );

  const result = spawnSync(process.execPath, [AUDIT_SCRIPT], {
    cwd: fixtureRoot,
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const report = JSON.parse(
    fs.readFileSync(path.join(fixtureRoot, '.audit/endpoint-audit.json'), 'utf8')
  );
  assert.deepEqual(report.clientOnly, ['POST /api/client-only']);
  assert.deepEqual(report.serverOnly, ['GET /api/server-only']);
  assert.deepEqual(report.excludedClientFiles, [
    'src/client/cloud-client.ts',
    'src/client/shared-client.ts',
  ]);
  assert.equal(report.client, 4);
  assert.equal(report.agentServer, 4);
  assert.equal(report.allowedClientOnly[0].endpoint, 'GET /health');
  assert.equal(report.explicitlyCoveredServerOperations[0].endpoint, 'GET /api/covered/{}');
  assert.deepEqual(report.gate, { clientOnly: false, serverOnly: false });
  assert(!result.stdout.includes('/api/cloud-only'));
  assert(!result.stdout.includes('/api/v1/config/models/search{}'));
  assert(!result.stdout.includes('/api/shared-events/search'));

  const body = renderEndpointAuditReport(report);
  assert(body.includes('2 actionable Agent Server contract divergence(s)'));
  assert(body.includes('Documented non-divergences | 2'));
  assert(body.includes('`POST /api/client-only`'));
  assert(body.includes('`GET /api/server-only`'));
  assert(body.includes('<summary>Documented non-divergences (2)</summary>'));

  let updatedComment;
  await postEndpointAuditReport({
    reportPath: path.join(fixtureRoot, '.audit/endpoint-audit.json'),
    context: { repo: { owner: 'OpenHands', repo: 'typescript-client' }, issue: { number: 307 } },
    core: { warning: assert.fail },
    github: {
      paginate: async () => [{ id: 123, body: '<!-- endpoint-audit-report -->old' }],
      rest: {
        issues: {
          listComments: () => {},
          updateComment: async (args) => {
            updatedComment = args;
          },
          createComment: async () => assert.fail('expected the existing comment to be updated'),
        },
      },
    },
  });
  assert.equal(updatedComment.owner, 'OpenHands');
  assert.equal(updatedComment.repo, 'typescript-client');
  assert.equal(updatedComment.comment_id, 123);
  assert.equal(updatedComment.body, body);

  console.log('endpoint-audit tooling test passed');
} finally {
  fs.rmSync(fixtureRoot, { recursive: true, force: true });
}
