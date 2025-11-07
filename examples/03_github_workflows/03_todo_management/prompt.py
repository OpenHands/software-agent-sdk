"""Prompt template for TODO implementation."""

PROMPT = """Please implement a TODO comment in a codebase.

IMPORTANT - Creating a Pull Request:
- Use the `gh pr create` command to create the PR
- The GITHUB_TOKEN environment variable is available for authentication
- PR Title: "[Openhands] {description}"
- Branch name "openhands/todo/***"

Your task is to:
1. Analyze the TODO comment and understand what needs to be implemented
2. Search in github for any existing PRs that adress this TODO
    Filter by title [Openhands]... Don't implement anything if such a PR exists
2. Create a feature branch for this implementation
3. Implement what is asked by the TODO
4. Create a pull request with your changes
5. Add 2 reviewers
    * Tag the person who wrote the TODO as a reviewer
    * read the git blame information for the files, and find the most recent and
    active contributors to the file/location of the changes.
    Assign one of these people as a reviewer.

Please make sure to:
- Create a descriptive branch name related to the TODO
- Fix the issue with clean code
- Include a test if needed, but not always necessary

TODO Details:
- File: {file_path}
- Line: {line_num}
- Description: {description}
"""
