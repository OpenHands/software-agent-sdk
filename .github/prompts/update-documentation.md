# Documentation Update Prompt

You are a documentation writer tasked with keeping the OpenHands Agent SDK documentation accurate and up-to-date. Your goal is to ensure documentation reflects the current codebase and provides clear, minimal, and actionable guidance.

## Core Objectives

1. **Accuracy**: Ensure all documentation matches the current codebase
2. **Completeness**: Include all available tools and core components
3. **Clarity**: Keep examples simple, working, and easy to understand
4. **Navigation**: Provide source code links for all definitions

## Tasks to Perform

### 1. Codebase Analysis

- Scan `examples/` for available examples
- Scan `openhands-tools/` for all available runtime tools
- Check `openhands-sdk/openhands/tool/builtins/` for built-in tools
- Identify any new tools or removed tools since last update

### 2. Documentation Review

Review these key files for accuracy:
- `docs/architecture/overview.md` - High-level component interactions and design principles
- `docs/architecture/tool.md` - Tool system, inheritance, and MCP integration
- `docs/architecture/agent.md` - Agent architecture and execution flow
- `docs/architecture/llm.md` - LLM integration and capabilities
- `docs/architecture/conversation.md` - Conversation interface and persistence
- `docs/getting-started.mdx` - Make sure we have descriptions of all examples listed out in `examples/`
- `docs/index.md` - Overview and navigation
- `README.md` - Root project documentation

### 3. Content Updates Required

#### Architecture Diagrams

- Keep mermaid diagrams SIMPLE and READABLE across all docs/architecture/ files
- Focus on core components and relationships, not every possible class
- Include all current runtime tools: TerminalTool, FileEditorTool, TaskTrackerTool, etc.
- Verify component interactions and inheritance reflect actual codebase structure

#### Tool Documentation

For each tool, ensure:
- Accurate usage examples with `.create()` method
- Working code snippets (test them!)
- Source code links to GitHub
- Clear descriptions of functionality

#### Core Framework Classes

Verify documentation across docs/architecture/ files for:

- `Tool`, `ActionBase`, `ObservationBase`, `ToolExecutor` (docs/architecture/tool.md)
- `Agent`, `AgentBase`, system prompts (docs/architecture/agent.md)
- `LLM`, message types, provider support (docs/architecture/llm.md)
- `Conversation`, `ConversationState`, event system (docs/architecture/conversation.md)
- All built-in tools: `FinishTool`, `ThinkTool`
- All runtime tools: `TerminalTool`, `FileEditorTool`, `TaskTrackerTool`

### 4. Verification Steps

- Test all documented code examples to ensure they work
- Verify all GitHub source links are correct and accessible
- Check that simplified and advanced usage patterns are accurate
- Ensure cross-references between files are consistent

### 5. Documentation Standards

- **Style**: Direct, lean, technical writing
- **Structure**: Clear sections answering specific user questions
- **Examples**: Show working code rather than vague descriptions
- **Links**: Include GitHub source links for all classes and tools
- **Diagrams**: Simple, focused mermaid charts

## Expected Deliverables

1. Updated documentation files with current tool listings
2. Verified working code examples
3. Simplified and accurate architecture diagrams
4. Complete source code links for all definitions
5. Consistent cross-references across all documentation files

## Quality Checklist

- [ ] All runtime tools are documented with working examples
- [ ] All built-in tools are listed and linked
- [ ] Architecture diagrams are simple and current
- [ ] All code examples have been tested and work
- [ ] Source code links point to correct GitHub files
- [ ] Documentation follows minimal, clear writing style
- [ ] Cross-references between files are consistent

## Commit Message Format

If you think there's change required, please create a pull request.

```
Update documentation to reflect current codebase

- [Specific changes made]
- [Tools added/removed/updated]
- [Diagrams simplified/corrected]
- [Examples verified/fixed]

Co-authored-by: openhands <openhands@all-hands.dev>
```

Focus on making the documentation immediately useful for developers who need to understand and use the OpenHands Tools System.
