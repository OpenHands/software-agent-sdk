# Git Tool

The Git Tool provides git operation capabilities for AI agents, enabling them to perform version control tasks within their workspace.

## Features

- **Repository Management**: Initialize, clone repositories
- **Branch Operations**: Create, switch, list branches
- **Change Tracking**: Status, diff, log
- **Staging & Committing**: Add files, create commits
- **Remote Operations**: Push, pull, manage remotes
- **Advanced Features**: Stash, reset, remote management

## Usage

```python
from openhands.sdk import Agent, Conversation, Tool
from openhands.tools.git import GitTool

agent = Agent(
    llm=llm,
    tools=[Tool(name=GitTool.name)],
)

conversation = Conversation(agent=agent, workspace="/path/to/repo")
conversation.send_message("Check git status and commit any changes")
conversation.run()
```

## Available Commands

### Repository Management

- `init`: Initialize a new git repository
- `clone`: Clone a repository from a URL

### Branch Operations

- `branch`: List or create branches
- `checkout`: Switch branches or create new branch with `-b`

### Change Tracking

- `status`: Show working tree status
- `diff`: Show changes between commits or working tree
- `log`: Show commit history

### Staging & Committing

- `add`: Add files to staging area
- `commit`: Create a commit with staged changes

### Remote Operations

- `push`: Push commits to remote repository
- `pull`: Pull changes from remote repository
- `remote`: Manage remote repositories

### Advanced Operations

- `reset`: Reset current HEAD to specified state
- `stash`: Stash changes in working directory

## Examples

### Check Status
```python
{
    "command": "status"
}
```

### Add and Commit Changes
```python
{
    "command": "add",
    "files": ["."]
}
{
    "command": "commit",
    "message": "feat: add new feature"
}
```

### Create and Switch to New Branch
```python
{
    "command": "checkout",
    "branch_name": "feature-branch",
    "create_branch": true
}
```

### Push Changes
```python
{
    "command": "push",
    "remote": "origin",
    "branch_name": "main"
}
```

### View Commit History
```python
{
    "command": "log",
    "max_count": 10,
    "oneline": true
}
```

### Clone Repository
```python
{
    "command": "clone",
    "url": "https://github.com/owner/repo.git",
    "repo_path": "./repo"
}
```

## Security Notes

- The tool uses subprocess-based git command execution with proper argument sanitization
- Commands are executed with timeouts to prevent hanging
- Repository paths are validated before operations
- Sensitive operations (force push, hard reset) require explicit parameters

## Error Handling

The tool provides clear error messages for common issues:
- Repository not initialized
- Missing required parameters
- Authentication failures
- Merge conflicts
- Invalid branch/commit references

## Implementation Details

- Built on top of the existing `openhands.sdk.git` utilities
- Uses secure subprocess execution without shell injection vulnerabilities
- Validates git repositories before operations
- Provides structured observations for agent decision-making
