import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const generator = join(repositoryRoot, 'scripts/generate-agent-server-api.mjs');
const checker = join(repositoryRoot, 'scripts/check-agent-server-api.mjs');
const summarizer = join(repositoryRoot, 'scripts/summarize-agent-server-api.mjs');
const reportRenderer = join(repositoryRoot, 'scripts/render-agent-server-drift-report.mjs');

function schema(properties) {
  return {
    openapi: '3.1.0',
    info: { title: 'Agent Server fixture', version: '9.9.9' },
    paths: {
      '/api/mcp/test': {
        post: {
          operationId: 'test_mcp',
          requestBody: {
            required: true,
            content: {
              'application/json': {
                schema: { $ref: '#/components/schemas/MCPServer' },
              },
            },
          },
          responses: {
            200: {
              description: 'Success',
              content: {
                'application/json': {
                  schema: { $ref: '#/components/schemas/MCPServer' },
                },
              },
            },
          },
        },
      },
    },
    components: {
      schemas: {
        MCPServer: {
          type: 'object',
          required: ['url'],
          properties,
        },
      },
    },
  };
}

function run(script, args = [], env = {}) {
  return spawnSync(process.execPath, [script, ...args], {
    cwd: repositoryRoot,
    env: { ...process.env, ...env },
    encoding: 'utf8',
  });
}

const fixtureRoot = await mkdtemp(join(tmpdir(), 'agent-server-api-tooling-'));
try {
  const baselineSchema = join(fixtureRoot, 'baseline-openapi.json');
  const additiveSchema = join(fixtureRoot, 'additive-openapi.json');
  const baselineGenerated = join(fixtureRoot, 'baseline.ts');
  const additiveGenerated = join(fixtureRoot, 'additive.ts');
  const summary = join(fixtureRoot, 'summary.md');
  const packageFixture = join(fixtureRoot, 'package.json');
  const report = join(fixtureRoot, 'drift-report.md');

  await writeFile(baselineSchema, `${JSON.stringify(schema({ url: { type: 'string' } }))}\n`);
  await writeFile(
    additiveSchema,
    `${JSON.stringify(
      schema({
        url: { type: 'string' },
        description: { type: ['string', 'null'] },
      })
    )}\n`
  );

  for (const [openapi, output] of [
    [baselineSchema, baselineGenerated],
    [additiveSchema, additiveGenerated],
  ]) {
    const result = run(generator, [], {
      AGENT_SERVER_OPENAPI_PATH: openapi,
      AGENT_SERVER_GENERATED_OUTPUT: output,
      AGENT_SERVER_SCHEMA_SOURCE: 'fixture@exact-sha',
    });
    if (result.status !== 0) {
      throw new Error(result.stderr || result.stdout);
    }
  }

  const baseline = await readFile(baselineGenerated, 'utf8');
  const additive = await readFile(additiveGenerated, 'utf8');
  if (baseline === additive || !additive.includes('description?: string | null')) {
    throw new Error('An additive Agent Server field did not create a generated type diff');
  }

  const summarizeResult = run(summarizer, [
    '--before',
    baselineGenerated,
    '--after',
    additiveGenerated,
    '--output',
    summary,
  ]);
  if (summarizeResult.status !== 0) {
    throw new Error(summarizeResult.stderr || summarizeResult.stdout);
  }
  const summaryText = await readFile(summary, 'utf8');
  if (!summaryText.includes('Generated TypeScript diff: **+')) {
    throw new Error('The API summary did not report the additive generated diff');
  }

  const exactSdkSha = '1234567890abcdef1234567890abcdef12345678';
  await writeFile(
    packageFixture,
    `${JSON.stringify({
      config: {
        agentServerImage: 'ghcr.io/openhands/agent-server:1.37.0-python',
      },
    })}\n`
  );
  const reportResult = run(reportRenderer, [
    '--sdk-sha',
    exactSdkSha,
    '--summary',
    summary,
    '--package',
    packageFixture,
    '--run-url',
    'https://github.com/OpenHands/typescript-client/actions/runs/123',
    '--output',
    report,
  ]);
  if (reportResult.status !== 0) {
    throw new Error(reportResult.stderr || reportResult.stdout);
  }
  const reportText = await readFile(report, 'utf8');
  if (
    !reportText.includes(`software-agent-sdk/commit/${exactSdkSha}`) ||
    !reportText.includes(exactSdkSha)
  ) {
    throw new Error('The scheduled audit report did not include the exact SDK commit SHA');
  }

  const staleGenerated = join(fixtureRoot, 'stale.ts');
  await writeFile(staleGenerated, baseline);
  const staleResult = run(checker, [], {
    AGENT_SERVER_OPENAPI_PATH: additiveSchema,
    AGENT_SERVER_GENERATED_OUTPUT: staleGenerated,
    AGENT_SERVER_SCHEMA_SOURCE: 'fixture@exact-sha',
  });
  if (staleResult.status === 0 || !staleResult.stderr.includes('was stale')) {
    throw new Error('Pinned CI checker did not fail for a stale generated file');
  }
  if ((await readFile(staleGenerated, 'utf8')) !== additive) {
    throw new Error('Pinned CI checker did not regenerate the stale file deterministically');
  }

  const cleanResult = run(checker, [], {
    AGENT_SERVER_OPENAPI_PATH: additiveSchema,
    AGENT_SERVER_GENERATED_OUTPUT: staleGenerated,
    AGENT_SERVER_SCHEMA_SOURCE: 'fixture@exact-sha',
  });
  if (cleanResult.status !== 0) {
    throw new Error(cleanResult.stderr || cleanResult.stdout);
  }

  const promptEnhancementSchema = schema({ url: { type: 'string' } });
  promptEnhancementSchema.paths['/api/prompt-enhancement/enhance'] = {
    post: {
      operationId: 'enhance_prompt',
      requestBody: {
        required: true,
        content: {
          'application/json': {
            schema: { $ref: '#/components/schemas/PromptEnhancementRequest' },
          },
        },
      },
      responses: {
        200: {
          description: 'Success',
          content: {
            'application/json': {
              schema: { $ref: '#/components/schemas/PromptEnhancementResponse' },
            },
          },
        },
      },
    },
  };
  promptEnhancementSchema.components.schemas.PromptEnhancementRequest = {
    type: 'object',
    required: ['text'],
    properties: { text: { type: 'string' } },
  };
  promptEnhancementSchema.components.schemas.PromptEnhancementResponse = {
    type: 'object',
    required: ['enhanced_text'],
    properties: { enhanced_text: { type: 'string' } },
  };
  const promptOpenapi = join(fixtureRoot, 'prompt-enhancement-openapi.json');
  const promptGenerated = join(fixtureRoot, 'prompt-enhancement.ts');
  await writeFile(promptOpenapi, `${JSON.stringify(promptEnhancementSchema)}\n`);
  const promptEnv = {
    AGENT_SERVER_OPENAPI_PATH: promptOpenapi,
    AGENT_SERVER_OPENAPI_PATH_PREFIX: '/api/prompt-enhancement/',
    AGENT_SERVER_GENERATED_OUTPUT: promptGenerated,
    AGENT_SERVER_SCHEMA_SOURCE: 'local prompt-enhancement fixture',
  };
  const promptGenerateResult = run(generator, [], promptEnv);
  if (promptGenerateResult.status !== 0) {
    throw new Error(promptGenerateResult.stderr || promptGenerateResult.stdout);
  }
  const promptGeneratedText = await readFile(promptGenerated, 'utf8');
  if (
    !promptGeneratedText.includes('PromptEnhancementRequest') ||
    promptGeneratedText.includes('MCPServer') ||
    promptGeneratedText.includes('TestMcpServerApiMcpTestPost')
  ) {
    throw new Error('Path-filtered generation included unrelated Agent Server contracts');
  }
  const promptCheckResult = run(checker, [], promptEnv);
  if (promptCheckResult.status !== 0) {
    throw new Error(promptCheckResult.stderr || promptCheckResult.stdout);
  }

  process.stdout.write(
    'Agent Server API tooling fixtures passed (additive diff, stale-file failure, and path filtering).\n'
  );
} finally {
  await rm(fixtureRoot, { recursive: true, force: true });
}
