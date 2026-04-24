/**
 * Utility functions for integration tests
 */

import * as fs from 'fs';
import * as path from 'path';
import { getServerTestConfig } from './test-config';

/**
 * Wait for a condition to become true, with timeout
 */
export async function waitFor(
  condition: () => boolean | Promise<boolean>,
  options: { timeout?: number; interval?: number; message?: string } = {}
): Promise<void> {
  const { timeout = 30000, interval = 100, message = 'Condition not met' } = options;
  const start = Date.now();

  while (Date.now() - start < timeout) {
    if (await condition()) {
      return;
    }
    await sleep(interval);
  }

  throw new Error(`Timeout waiting: ${message}`);
}

/**
 * Sleep for a given number of milliseconds
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Read a file from the host workspace directory
 */
export function readWorkspaceFile(relativePath: string): string {
  const config = getServerTestConfig();
  const fullPath = path.join(config.hostWorkspaceDir, relativePath);
  return fs.readFileSync(fullPath, 'utf-8');
}

/**
 * Write a file to the host workspace directory
 */
export function writeWorkspaceFile(relativePath: string, content: string): void {
  const config = getServerTestConfig();
  const fullPath = path.join(config.hostWorkspaceDir, relativePath);
  const dir = path.dirname(fullPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(fullPath, content, 'utf-8');
}

/**
 * Check if a file exists in the host workspace directory
 */
export function workspaceFileExists(relativePath: string): boolean {
  const config = getServerTestConfig();
  const fullPath = path.join(config.hostWorkspaceDir, relativePath);
  return fs.existsSync(fullPath);
}

/**
 * Delete a file from the host workspace directory
 */
export function deleteWorkspaceFile(relativePath: string): void {
  const config = getServerTestConfig();
  const fullPath = path.join(config.hostWorkspaceDir, relativePath);
  if (fs.existsSync(fullPath)) {
    fs.unlinkSync(fullPath);
  }
}

/**
 * Clean the workspace directory (remove all files)
 */
export function cleanWorkspace(): void {
  const config = getServerTestConfig();
  if (fs.existsSync(config.hostWorkspaceDir)) {
    const files = fs.readdirSync(config.hostWorkspaceDir);
    for (const file of files) {
      const fullPath = path.join(config.hostWorkspaceDir, file);
      const stat = fs.statSync(fullPath);
      if (stat.isDirectory()) {
        fs.rmSync(fullPath, { recursive: true });
      } else {
        fs.unlinkSync(fullPath);
      }
    }
  }
}

/**
 * Create a unique test file name
 */
export function uniqueFileName(prefix: string = 'test', extension: string = 'txt'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(7)}.${extension}`;
}

/**
 * Create a unique test directory name
 */
export function uniqueDirName(prefix: string = 'test-dir'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(7)}`;
}

/**
 * Generate random text content
 */
export function randomContent(length: number = 100): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ';
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

/**
 * Wait for the agent to complete (not running)
 */
export async function waitForAgentIdle(
  checkStatus: () => Promise<string>,
  options: { timeout?: number; interval?: number } = {}
): Promise<void> {
  const { timeout = 120000, interval = 500 } = options;
  const start = Date.now();

  while (Date.now() - start < timeout) {
    const status = await checkStatus();
    if (status === 'idle' || status === 'finished' || status === 'error' || status === 'stuck') {
      return;
    }
    await sleep(interval);
  }

  throw new Error('Timeout waiting for agent to become idle');
}
