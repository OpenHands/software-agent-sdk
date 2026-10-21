# OpenHands SDK React Example

This is a basic React application built with Vite that demonstrates successful integration with the OpenHands Agent Server TypeScript Client SDK.

## Features

- ✅ **Local SDK Integration**: Uses a locally built version of the SDK via file dependency
- ✅ **Automatic Build**: Builds the SDK before starting the app
- ✅ **TypeScript Support**: Full TypeScript integration with type safety
- ✅ **Import Verification**: Displays all imported SDK classes and enums
- ✅ **Real-time Status**: Shows SDK import status and available functionality

## What's Demonstrated

The app successfully imports and displays:

### Main Classes
- `RemoteConversation` - Main conversation management class
- `RemoteWorkspace` - Workspace file operations class  
- `HttpClient` - HTTP client for API communication
- `AgentExecutionStatus` - Enum for agent execution states
- `EventSortOrder` - Enum for event sorting options

### Enum Values
- **AgentExecutionStatus**: IDLE, RUNNING, PAUSED, FINISHED, ERROR
- **EventSortOrder**: TIMESTAMP, REVERSE_TIMESTAMP

## Getting Started

### Prerequisites
- Node.js (v18 or higher)
- npm

### Installation & Running

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Start development server**:
   ```bash
   npm run dev
   ```
   This will:
   - Build the SDK from `../agent-server-typescript-client`
   - Start the Vite dev server on port 12000
   - Open the app at `http://localhost:12000`

3. **Build for production**:
   ```bash
   npm run build
   ```

## Project Structure

```
example/
├── src/
│   ├── App.tsx          # Main React component with SDK integration
│   ├── App.css          # Styling
│   ├── main.tsx         # React entry point
│   └── index.css        # Global styles
├── package.json         # Dependencies and scripts
├── vite.config.ts       # Vite configuration
├── tsconfig.json        # TypeScript configuration
└── index.html           # HTML template
```

## Key Configuration

### package.json
- **Local SDK Dependency**: `"@openhands/agent-server-typescript-client": "file:../agent-server-typescript-client"`
- **Build Script**: `"build:sdk": "cd ../agent-server-typescript-client && npm run build"`
- **Dev Script**: Builds SDK before starting Vite

### vite.config.ts
- Configured for React with TypeScript
- CORS enabled for development
- Host configuration for external access

## SDK Integration Details

The app demonstrates that the TypeScript SDK is properly:
1. **Built as ES Modules** - Compatible with Vite's module system
2. **Type-safe** - Full TypeScript support with proper type definitions
3. **Functional** - All main classes and enums are importable and usable
4. **Up-to-date** - Uses the latest local build of the SDK

## Notes

- The SDK includes some Node.js-specific functionality (fs, path modules) that are externalized for browser compatibility
- This is expected behavior and doesn't affect the core SDK functionality in browser environments
- The app serves as a "Hello World" example to verify SDK integration works correctly