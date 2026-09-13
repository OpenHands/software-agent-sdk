import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(
  process.argv[2] ?? path.join(scriptDir, '../clients/typescript/dist')
);
const { RemoteWorkspace } = await import(
  pathToFileURL(path.join(distDir, 'workspace/remote-workspace.js')).href
);

assert.equal(typeof document, 'undefined', 'Run this script in Node.js without a DOM shim.');

let requestCount = 0;
const originalFetch = globalThis.fetch;
globalThis.fetch = async () => {
  requestCount += 1;
  return new Response('local fixture contents', {
    headers: { 'content-type': 'text/plain' },
  });
};

const workspace = new RemoteWorkspace({
  host: 'https://agent-server.example.invalid',
  workingDir: '/workspace',
});

try {
  let observedError;
  try {
    await workspace.downloadAndSave('/workspace/file.txt');
  } catch (error) {
    observedError = error;
  }

  console.log(`node=${process.version}`);
  console.log(`error_name=${observedError?.name ?? 'none'}`);
  console.log(`error_message=${observedError?.message ?? 'none'}`);
  console.log(`fetch_requests=${requestCount}`);

  assert.ok(observedError instanceof Error, 'Expected a clear environment error.');
  assert.equal(observedError.name, 'Error');
  assert.equal(
    observedError.message,
    'downloadAndSave() is only available in browser environments. ' +
      'Use downloadAsBlob() or downloadAsText() in Node.js.'
  );
  assert.equal(requestCount, 0, 'A rejected browser download must not request the file.');
  console.log('PASS: Node.js receives actionable guidance before any HTTP request.');
} finally {
  workspace.close();
  globalThis.fetch = originalFetch;
}
