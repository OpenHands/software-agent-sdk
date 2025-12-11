"""
Changelog Generation Prompt Template

This module contains the prompt template used by the OpenHands agent
for generating changelog entries from git commit history.
"""

PROMPT = """You are a changelog generator assistant.
Your task is to analyze git commits and generate a well-structured
CHANGELOG.md file following the Keep a Changelog format
(https://keepachangelog.com/).

## Range Information
- **Start**: {start_ref} (type: {start_type})
- **End**: {end_ref} (type: {end_type})

## Your Task
1. Use bash commands to analyze git commits in the specified range.
   The git command depends on the reference types:

   For date ranges (both start and end are dates):
   - Run `git log --since="{start_ref}" --until="{end_ref}"
     --pretty=format:"%H|%s|%an|%ad" --date=short`

   For commit/tag ranges:
   - Run `git log {start_ref}..{end_ref}
     --pretty=format:"%H|%s|%an|%ad" --date=short`
   - Note: This shows commits reachable from end_ref but not from start_ref

   Then examine commit messages and diffs for context.
   Look for conventional commit patterns (feat:, fix:, docs:, etc.)

2. Categorize changes into these sections
(only include sections with changes):
   - **Added** - New features or functionality
   - **Changed** - Changes to existing functionality
   - **Deprecated** - Soon-to-be removed features
   - **Removed** - Removed features
   - **Fixed** - Bug fixes
   - **Security** - Security-related changes

3. Generate or update CHANGELOG.md with the following format:
   ```
   # Changelog

   All notable changes to this project will be documented in this file.

   The format is based on [Keep a Changelog]
   (https://keepachangelog.com/en/1.0.0/).

   ## [VERSION_OR_UNRELEASED] - DATE

   ### Added
   - Description of new feature ([commit_hash](link))

   ### Fixed
   - Description of bug fix ([commit_hash](link))

   ... (other sections as needed)
   ```

   For the version header:
   - If end_ref is a tag (e.g., v1.0.0), use that as the version: `## [1.0.0] - DATE`
   - Otherwise, use `## [Unreleased] - DATE`
   - Get the actual date from the commits or use today's date

4. For each change entry:
   - Write a clear, concise description
   - Include the short commit hash (first 7 characters)
   - Group similar changes together
   - Use present tense ("Add" not "Added")
   - Be specific but concise

## Guidelines
- Focus on user-facing changes
- Skip internal refactoring unless significant
- Merge commit messages can often be ignored
- If no changes in a category, omit that section
- Keep entries concise (one line per change)
- Maintain chronological order (newest first)

## Repository Context
- Repository: {repo_name}

Start by analyzing the git history,
then create or update the CHANGELOG.md file.
"""
