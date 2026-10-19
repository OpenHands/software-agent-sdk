import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const script = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  'check-client-behavior-parity.mjs'
);
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'client-parity-'));
try {
  fs.writeFileSync(
    path.join(fixture, 'typescript.ts'),
    'class RemoteWorkspace { conversationId?: string }\n'
  );
  fs.writeFileSync(path.join(fixture, 'python.py'), 'class RemoteWorkspace:\n    pass\n');
  const contract = path.join(fixture, 'contract.json');
  fs.writeFileSync(
    contract,
    JSON.stringify({
      capabilities: [
        {
          id: 'workspace.conversation-scoped-routing',
          typescript: { file: 'typescript.ts', symbol: 'conversationId' },
          python: { file: 'python.py', symbol: 'conversation_id' },
        },
      ],
      exceptions: [],
    })
  );
  const mismatch = spawnSync(process.execPath, [script, contract], { encoding: 'utf8' });
  assert.notEqual(mismatch.status, 0);
  assert.match(
    mismatch.stderr,
    /workspace\.conversation-scoped-routing: python symbol conversation_id not found/
  );

  fs.writeFileSync(
    path.join(fixture, 'python.py'),
    'class RemoteWorkspace:\n    conversation_id: str | None\n'
  );
  const matching = spawnSync(process.execPath, [script, contract], { encoding: 'utf8' });
  assert.equal(matching.status, 0, matching.stderr);
  console.log('client behavior parity tooling test passed');
} finally {
  fs.rmSync(fixture, { recursive: true, force: true });
}
