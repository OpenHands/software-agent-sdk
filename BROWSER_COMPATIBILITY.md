# Browser Compatibility Guide

This document outlines the browser-compatible file operations API in the OpenHands TypeScript Client.

## Overview

The TypeScript client has been updated to work natively in browser environments without Node.js dependencies. The main changes involve file upload and download operations that now work with browser-native data types like `Blob`, `File`, and strings instead of file system paths.

## File Upload API

### `fileUpload(content, destinationPath, fileName?)`

Upload content to the remote workspace.

**Parameters:**
- `content: string | Blob | File` - The content to upload
- `destinationPath: string` - Where to save the file on the remote workspace
- `fileName?: string` - Optional filename (auto-detected for File objects)

**Examples:**

```typescript
const workspace = new RemoteWorkspace({
  host: 'http://localhost:3000',
  workingDir: '/tmp',
  apiKey: 'your-api-key'
});

// Upload text content
await workspace.fileUpload('Hello, World!', '/tmp/hello.txt', 'hello.txt');

// Upload a File object (from file input)
const fileInput = document.getElementById('fileInput') as HTMLInputElement;
const file = fileInput.files[0];
await workspace.fileUpload(file, '/tmp/uploads/');

// Upload a Blob
const blob = new Blob(['Some data'], { type: 'text/plain' });
await workspace.fileUpload(blob, '/tmp/data.txt', 'data.txt');
```

### Convenience Methods

#### `uploadText(text, destinationPath, fileName?)`
Shorthand for uploading text content.

```typescript
await workspace.uploadText('Hello, World!', '/tmp/hello.txt');
```

#### `uploadFileObject(file, destinationPath)`
Shorthand for uploading File objects.

```typescript
const file = fileInput.files[0];
await workspace.uploadFileObject(file, '/tmp/uploads/');
```

## File Download API

### `fileDownload(sourcePath)`

Download a file from the remote workspace. Returns content as string or Blob.

**Parameters:**
- `sourcePath: string` - Path to the file on the remote workspace

**Returns:** `Promise<FileDownloadResult>`

```typescript
interface FileDownloadResult {
  success: boolean;
  source_path: string;
  content: string | Blob;
  file_size?: number;
  error?: string;
}
```

**Example:**

```typescript
const result = await workspace.fileDownload('/tmp/data.txt');
if (result.success) {
  console.log('File content:', result.content);
}
```

### Convenience Methods

#### `downloadAsText(sourcePath)`
Download file content as a string.

```typescript
const text = await workspace.downloadAsText('/tmp/hello.txt');
console.log(text); // "Hello, World!"
```

#### `downloadAsBlob(sourcePath)`
Download file content as a Blob.

```typescript
const blob = await workspace.downloadAsBlob('/tmp/image.png');
// Use blob for further processing
```

#### `downloadAndSave(sourcePath, saveAsFileName?)`
Download a file and trigger browser download dialog.

```typescript
// This will prompt the user to save the file
await workspace.downloadAndSave('/tmp/report.pdf', 'my-report.pdf');
```

## Migration from Node.js API

### Before (Node.js only)
```typescript
// Old API - required file system paths
await workspace.fileUpload('/local/path/file.txt', '/remote/path/file.txt');
await workspace.fileDownload('/remote/path/file.txt', '/local/path/file.txt');
```

### After (Browser compatible)
```typescript
// New API - works with browser data types
const fileInput = document.getElementById('file') as HTMLInputElement;
const file = fileInput.files[0];
await workspace.fileUpload(file, '/remote/path/file.txt');

const result = await workspace.fileDownload('/remote/path/file.txt');
if (result.success) {
  // Use result.content (string or Blob)
  console.log(result.content);
}
```

## Browser Testing

A test file `test-browser.html` is included to verify browser compatibility. Open it in a browser after building the project to test the API without a running server.

## Node.js Compatibility

The new API is also compatible with Node.js environments. You can still use the client in Node.js applications by providing appropriate data types:

```typescript
import fs from 'fs';

// Read file content and upload
const content = await fs.promises.readFile('/local/file.txt', 'utf8');
await workspace.fileUpload(content, '/remote/file.txt', 'file.txt');

// Download and save
const result = await workspace.fileDownload('/remote/file.txt');
if (result.success && typeof result.content === 'string') {
  await fs.promises.writeFile('/local/downloaded.txt', result.content);
}
```