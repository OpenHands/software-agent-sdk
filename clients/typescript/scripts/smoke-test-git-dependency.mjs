import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const tempDir = await mkdtemp(path.join(os.tmpdir(), 'openhands-typescript-client-gitdep-'));
const dependencyUrl = `git+${pathToFileURL(repoRoot).href}`;

try {
  await writeFile(
    path.join(tempDir, 'package.json'),
    JSON.stringify(
      {
        name: 'openhands-typescript-client-gitdep-smoke',
        private: true,
        type: 'module',
        dependencies: {
          '@openhands/typescript-client': dependencyUrl,
        },
      },
      null,
      2
    )
  );

  execFileSync('npm', ['install', '--no-fund', '--no-audit'], {
    cwd: tempDir,
    stdio: 'inherit',
  });

  execFileSync(
    'node',
    [
      '--input-type=module',
      '-e',
      [
        "import { ServerClient } from '@openhands/typescript-client/clients';",
        "import { HttpClient } from '@openhands/typescript-client/client/http-client';",
        "import { isOpenHandsCloudHost } from '@openhands/typescript-client/client/device-flow-client';",
        "import { RemoteEventsList } from '@openhands/typescript-client/events/remote-events-list';",
        "import { RemoteWorkspace } from '@openhands/typescript-client/workspace/remote-workspace';",
        'console.log(typeof ServerClient, typeof HttpClient, typeof isOpenHandsCloudHost, typeof RemoteEventsList, typeof RemoteWorkspace);',
      ].join('\n'),
    ],
    {
      cwd: tempDir,
      stdio: 'inherit',
    }
  );
} finally {
  await rm(tempDir, { recursive: true, force: true });
}
