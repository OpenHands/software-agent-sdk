/**
 * Integration tests for RemoteWorkspace
 *
 * Tests file operations and command execution against a real agent-server
 * running in a Docker container with a mounted workspace volume.
 */

import { HttpError, RemoteWorkspace, Workspace } from '../../index';
import { getTestConfig, skipIfNoConfig } from './test-config';
import {
  readWorkspaceFile,
  writeWorkspaceFile,
  workspaceFileExists,
  deleteWorkspaceFile,
  uniqueFileName,
  randomContent,
  sleep,
} from './test-utils';

const SKIP_TESTS = skipIfNoConfig();

describe('RemoteWorkspace Integration Tests', () => {
  let workspace: RemoteWorkspace;
  let config: ReturnType<typeof getTestConfig>;

  beforeAll(() => {
    if (SKIP_TESTS) {
      console.warn('Skipping integration tests: LLM_API_KEY and LLM_MODEL not set');
      return;
    }
    config = getTestConfig();
    workspace = new Workspace({
      host: config.agentServerUrl,
      workingDir: config.agentWorkspaceDir,
    });
  });

  afterAll(() => {
    if (workspace) {
      workspace.close();
    }
  });

  describe('Command Execution', () => {
    it(
      'should execute a simple echo command',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('echo "Hello, World!"');

        expect(result.exit_code).toBe(0);
        expect(result.stdout).toContain('Hello, World!');
        expect(result.stderr).toBe('');
        expect(result.timeout_occurred).toBe(false);
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute a command that returns exit code 1',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('exit 1');

        expect(result.exit_code).toBe(1);
        expect(result.timeout_occurred).toBe(false);
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute pwd command and return a valid directory',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('pwd');

        expect(result.exit_code).toBe(0);
        // pwd should return a valid path (starts with /)
        expect(result.stdout.trim()).toMatch(/^\//);
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute command with output',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('ls -la');

        expect(result.exit_code).toBe(0);
        expect(result.stdout).toBeDefined();
        // ls -la should have some output
        expect(result.stdout.length).toBeGreaterThan(0);
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute command that creates a file',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('cmd-test');
        const content = 'Created by command';

        const result = await workspace.executeCommand(
          `echo "${content}" > ${config.agentWorkspaceDir}/${fileName}`
        );

        expect(result.exit_code).toBe(0);

        // Give a moment for file to sync
        await sleep(500);

        // Verify file exists on host
        expect(workspaceFileExists(fileName)).toBe(true);
        const fileContent = readWorkspaceFile(fileName);
        expect(fileContent.trim()).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute multi-line command',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('echo "line1" && echo "line2"');

        expect(result.exit_code).toBe(0);
        expect(result.stdout).toContain('line1');
        expect(result.stdout).toContain('line2');
      },
      config?.testTimeout || 30000
    );

    it(
      'should capture stderr',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('echo "error message" >&2');

        expect(result.exit_code).toBe(0);
        expect(result.stderr).toContain('error message');
      },
      config?.testTimeout || 30000
    );

    it(
      'should execute command with environment variable',
      async () => {
        if (SKIP_TESTS) return;

        const result = await workspace.executeCommand('echo $HOME');

        expect(result.exit_code).toBe(0);
        expect(result.stdout.trim()).toBeTruthy();
      },
      config?.testTimeout || 30000
    );
  });

  describe('File Upload', () => {
    it(
      'should upload text content as a file',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('upload-test');
        const content = randomContent(200);
        const destPath = `${config.agentWorkspaceDir}/${fileName}`;

        const result = await workspace.fileUpload(content, destPath, fileName);

        expect(result.success).toBe(true);
        expect(result.destination_path).toBe(destPath);

        // Give a moment for file to sync
        await sleep(500);

        // Verify file exists on host
        expect(workspaceFileExists(fileName)).toBe(true);
        const fileContent = readWorkspaceFile(fileName);
        expect(fileContent).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should upload text using uploadText convenience method',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('uploadtext-test');
        const content = 'Test content for uploadText';
        const destPath = `${config.agentWorkspaceDir}/${fileName}`;

        const result = await workspace.uploadText(content, destPath, fileName);

        expect(result.success).toBe(true);

        // Give a moment for file to sync
        await sleep(500);

        // Verify file exists on host
        expect(workspaceFileExists(fileName)).toBe(true);
        const fileContent = readWorkspaceFile(fileName);
        expect(fileContent).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should handle special characters in content',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('special-chars');
        const content = 'Special chars: éàü中文日本語 & < > " \' \n\t';
        const destPath = `${config.agentWorkspaceDir}/${fileName}`;

        const result = await workspace.uploadText(content, destPath, fileName);

        expect(result.success).toBe(true);

        // Give a moment for file to sync
        await sleep(500);

        const fileContent = readWorkspaceFile(fileName);
        expect(fileContent).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );
  });

  describe('File Download', () => {
    it(
      'should download a file that was created via command',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('download-test');
        const content = 'Content to download';

        // Create file via command
        await workspace.executeCommand(
          `echo -n "${content}" > ${config.agentWorkspaceDir}/${fileName}`
        );

        // Give a moment for file to be created
        await sleep(500);

        // Download the file
        const result = await workspace.fileDownload(`${config.agentWorkspaceDir}/${fileName}`);

        expect(result.success).toBe(true);
        expect(result.content).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should download a file created on host',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('host-created');
        const content = 'Created on host for download test';

        // Create file on host
        writeWorkspaceFile(fileName, content);

        // Download via workspace
        const result = await workspace.fileDownload(`${config.agentWorkspaceDir}/${fileName}`);

        expect(result.success).toBe(true);
        expect(result.content).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should use downloadAsText convenience method',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('downloadtext-test');
        const content = 'Text content for downloadAsText';

        writeWorkspaceFile(fileName, content);

        const downloadedContent = await workspace.downloadAsText(
          `${config.agentWorkspaceDir}/${fileName}`
        );

        expect(downloadedContent).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should throw HttpError for non-existent file',
      async () => {
        if (SKIP_TESTS) return;

        await expect(
          workspace.fileDownload(`${config.agentWorkspaceDir}/non-existent-file-${Date.now()}.txt`)
        ).rejects.toBeInstanceOf(HttpError);
      },
      config?.testTimeout || 30000
    );
  });

  describe('Round-trip File Operations', () => {
    it(
      'should upload and download file with same content',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('roundtrip');
        const content = randomContent(500);
        const destPath = `${config.agentWorkspaceDir}/${fileName}`;

        // Upload
        const uploadResult = await workspace.uploadText(content, destPath, fileName);
        expect(uploadResult.success).toBe(true);

        // Download
        const downloadedContent = await workspace.downloadAsText(destPath);
        expect(downloadedContent).toBe(content);

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );

    it(
      'should handle JSON content correctly',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('json-test', 'json');
        const jsonContent = JSON.stringify(
          {
            name: 'test',
            values: [1, 2, 3],
            nested: { key: 'value' },
          },
          null,
          2
        );
        const destPath = `${config.agentWorkspaceDir}/${fileName}`;

        // Upload
        await workspace.uploadText(jsonContent, destPath, fileName);

        // Download
        const downloaded = await workspace.downloadAsText(destPath);
        expect(JSON.parse(downloaded)).toEqual(JSON.parse(jsonContent));

        // Cleanup
        deleteWorkspaceFile(fileName);
      },
      config?.testTimeout || 30000
    );
  });
});
