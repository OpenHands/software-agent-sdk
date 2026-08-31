import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const CLIENT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MONOREPO_ROOT = path.resolve(CLIENT_ROOT, '../..');
const AUDIT_SCRIPT = path.join(CLIENT_ROOT, 'scripts/endpoint-audit.mjs');
const require = createRequire(import.meta.url);
const { postEndpointAuditReport, renderEndpointAuditReport } = require(
  path.join(MONOREPO_ROOT, '.github/scripts/post-typescript-endpoint-audit-report.cjs')
);
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'endpoint-audit-'));

const summaryWrites = [];

function fakeCore(overrides = {}) {
  let buffer = '';
  return {
    warning: assert.fail,
    info: () => {},
    ...overrides,
    summary: {
      addRaw(text) {
        buffer += text;
        return this;
      },
      async write() {
        summaryWrites.push(buffer);
        buffer = '';
        return this;
      },
    },
  };
}

function sameRepoContext(number) {
  return {
    eventName: 'pull_request',
    repo: { owner: 'OpenHands', repo: 'software-agent-sdk' },
    issue: { number },
    payload: {
      pull_request: { head: { repo: { full_name: 'OpenHands/software-agent-sdk' } } },
    },
  };
}

function forkContext(number) {
  return {
    eventName: 'pull_request',
    repo: { owner: 'OpenHands', repo: 'software-agent-sdk' },
    issue: { number },
    payload: {
      pull_request: { head: { repo: { full_name: 'georgeglarson/software-agent-sdk' } } },
    },
  };
}

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
    context: sameRepoContext(4775),
    core: fakeCore(),
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
  assert.equal(updatedComment.repo, 'software-agent-sdk');
  assert.equal(updatedComment.comment_id, 123);
  assert.equal(updatedComment.body, body);

  assert(summaryWrites.length === 1, 'a same-repo PR still writes the job summary');
  assert(summaryWrites[0].includes('Endpoint audit'), 'the summary carries the report');

  // A fork PR runs with a read-only GITHUB_TOKEN, so the comment POST 403s and
  // takes the whole job red. The report goes to the job summary and the
  // comment is skipped rather than attempted.
  summaryWrites.length = 0;
  const forkInfo = [];
  await postEndpointAuditReport({
    reportPath: path.join(fixtureRoot, '.audit/endpoint-audit.json'),
    context: forkContext(4775),
    core: fakeCore({ info: (msg) => forkInfo.push(msg) }),
    github: {
      paginate: async () => assert.fail('a fork PR must not read comments'),
      rest: {
        issues: {
          listComments: () => assert.fail('a fork PR must not read comments'),
          updateComment: async () => assert.fail('a fork PR must not post a comment'),
          createComment: async () => assert.fail('a fork PR must not post a comment'),
        },
      },
    },
  });
  assert.equal(summaryWrites.length, 1, 'a fork PR still writes the job summary');
  assert(summaryWrites[0].includes('Endpoint audit'), 'the fork summary carries the report');
  assert(
    forkInfo.some((msg) => msg.includes('fork')),
    'the skip is announced, not silent'
  );

  // A push event has no PR to comment on at all (context.issue.number is
  // undefined there), so the same guard covers it.
  summaryWrites.length = 0;
  await postEndpointAuditReport({
    reportPath: path.join(fixtureRoot, '.audit/endpoint-audit.json'),
    context: { eventName: 'push', repo: { owner: 'OpenHands', repo: 'software-agent-sdk' } },
    core: fakeCore(),
    github: {
      paginate: async () => assert.fail('a push event must not read comments'),
      rest: {
        issues: {
          listComments: () => assert.fail('a push event must not read comments'),
          updateComment: async () => assert.fail('a push event must not post a comment'),
          createComment: async () => assert.fail('a push event must not post a comment'),
        },
      },
    },
  });
  assert.equal(summaryWrites.length, 1, 'a push run still writes the job summary');

  // A missing report is still a warning with nothing published.
  summaryWrites.length = 0;
  const warnings = [];
  await postEndpointAuditReport({
    reportPath: path.join(fixtureRoot, '.audit/does-not-exist.json'),
    context: sameRepoContext(4775),
    core: fakeCore({ warning: (msg) => warnings.push(msg) }),
    github: {
      paginate: async () => assert.fail('a missing report must not read comments'),
      rest: { issues: { listComments: () => assert.fail('no report, no comment') } },
    },
  });
  assert.equal(summaryWrites.length, 0, 'no report means no summary');
  assert.equal(warnings.length, 1, 'a missing report warns');

  console.log('endpoint-audit tooling test passed');
} finally {
  fs.rmSync(fixtureRoot, { recursive: true, force: true });
}
