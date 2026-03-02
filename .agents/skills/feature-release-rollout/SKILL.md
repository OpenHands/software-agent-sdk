---
name: feature-release-rollout
description: This skill should be used when the user asks to "rollout a feature", "complete feature release", "propagate SDK feature", "track feature support", "what's missing for feature X", or mentions checking CLI/GUI/docs/blog support for SDK features. Guides agents through the multi-repository feature release workflow from SDK to docs to marketing.
triggers:
- rollout feature
- feature release
- propagate feature
- feature support
- complete release
- docs for feature
- blog for feature
- CLI support
- GUI support
- what's missing
---

# Feature Release Rollout

This skill guides the complete feature release workflow across the OpenHands ecosystem repositories.

## Overview

When a feature is implemented in the SDK, it may need propagation through several repositories:

1. **SDK** (`OpenHands/software-agent-sdk`) — Core feature implementation
2. **CLI** (`OpenHands/OpenHands-CLI`) — Terminal interface support
3. **GUI** (`OpenHands/OpenHands` frontend directory) — Web interface support
4. **Docs** (`OpenHands/docs`) — Documentation updates (sdk/ folder)
5. **Blog** (`OpenHands/growth-utils` blog-post/) — Marketing and announcements
6. **Video** — Tutorial content (using ElevenLabs + Remotion)

## Workflow

### Phase 1: Feature Discovery

First, identify what feature(s) to analyze. The user may specify:
- A release tag (e.g., `v1.9.0`)
- A specific feature name
- A PR or commit reference
- A comparison between versions

**For release tags:**
```bash
# Clone SDK if not present
git clone https://github.com/OpenHands/software-agent-sdk.git

# View release notes
cd software-agent-sdk
git log --oneline v1.8.0..v1.9.0  # Changes between versions
git show v1.9.0 --stat             # What changed in this release
```

**For specific features:**
Search the SDK codebase, examples, and changelog to understand the feature scope.

### Phase 2: Repository Analysis

Clone all relevant repositories to analyze current support:

```bash
# Clone repositories (use GITHUB_TOKEN for authenticated access)
git clone https://github.com/OpenHands/software-agent-sdk.git
git clone https://github.com/OpenHands/OpenHands-CLI.git
git clone https://github.com/OpenHands/OpenHands.git        # Frontend in frontend/
git clone https://github.com/OpenHands/docs.git
git clone https://github.com/OpenHands/growth-utils.git
```

For each feature, check support status:

| Repository | Check Location | What to Look For |
|------------|---------------|------------------|
| CLI | `openhands_cli/` | Feature flags, commands, TUI widgets |
| GUI | `OpenHands/frontend/src/` | React components, API integrations |
| Docs | `docs/sdk/` | Guide pages, API reference, examples |
| Blog | `growth-utils/blog-post/posts/` | Announcement posts |

### Phase 3: Assess Feature Importance

Not all features warrant full rollout. Evaluate each feature:

**High Impact (full rollout recommended):**
- New user-facing capabilities
- Breaking changes or migrations
- Major performance improvements
- New integrations or tools

**Medium Impact (docs + selective support):**
- New API methods or parameters
- Configuration options
- Developer experience improvements

**Low Impact (docs only or skip):**
- Internal refactoring
- Bug fixes
- Minor enhancements

**Skip rollout for:**
- Internal-only changes
- Test improvements
- Build/CI changes
- Documentation typos

### Phase 4: Create Proposal

Generate a structured proposal for the user:

```markdown
## Feature Rollout Proposal: [Feature Name]

### Feature Summary
[Brief description of the feature and its value]

### Current Support Status
| Component | Status | Notes |
|-----------|--------|-------|
| SDK | ✅ Implemented | [version/PR] |
| CLI | ❌ Missing | [what's needed] |
| GUI | ⚠️ Partial | [what's implemented vs needed] |
| Docs | ❌ Missing | [suggested pages] |
| Blog | ❌ Not started | [whether warranted] |
| Video | ❌ Not started | [whether warranted] |

### Recommended Actions
1. **CLI**: [specific implementation needed]
2. **GUI**: [specific implementation needed]
3. **Docs**: [pages to create/update]
4. **Blog**: [recommended or not, with reasoning]
5. **Video**: [recommended or not, with reasoning]

### Assessment
- **Overall Priority**: [High/Medium/Low]
- **Effort Estimate**: [days/hours per component]
- **Dependencies**: [what must be done first]
```

### Phase 5: User Confirmation

Wait for explicit user approval before proceeding. Ask:
- Which components to implement
- Priority ordering
- Any modifications to the proposal

### Phase 6: Implementation

Only after user confirmation:

**Create GitHub Issues:**
```bash
# Create issue on relevant repo
gh issue create --repo OpenHands/OpenHands-CLI \
  --title "Support [feature] in CLI" \
  --body "## Context\n[Feature description]\n\n## Implementation\n[Details]\n\n## Related\n- SDK: [link]\n- Docs: [link]"
```

**Implementation order:**
1. CLI/GUI support (can be parallel)
2. Documentation (depends on 1)
3. Blog post (depends on 2)
4. Video (depends on 3)

## Repository-Specific Guidelines

### CLI (OpenHands/OpenHands-CLI)

- Check `AGENTS.md` for development guidelines
- Use `uv` for dependency management
- Run `make lint` and `make test` before commits
- TUI components in `openhands_cli/tui/`
- Snapshot tests for UI changes

### GUI (OpenHands/OpenHands frontend)

- Frontend in `frontend/` directory
- React/TypeScript codebase
- Run `npm run lint:fix && npm run build` in frontend/
- Follow TanStack Query patterns for data fetching
- i18n translations in `frontend/src/i18n/`

### Docs (OpenHands/docs)

- SDK docs in `sdk/` folder
- Uses Mintlify (`.mdx` files)
- Code blocks can auto-sync from SDK examples
- Run `mint broken-links` to validate
- Follow `openhands/DOC_STYLE_GUIDE.md`

### Blog (OpenHands/growth-utils)

- Posts in `blog-post/posts/YYYYMMDD-title.md`
- Assets in `blog-post/assets/YYYYMMDD-title/`
- Frontmatter format:
  ```yaml
  ---
  title: "Post Title"
  excerpt: "Brief description"
  coverImage: "/assets/blog/YYYYMMDD-title/cover.png"
  date: "YYYY-MM-DDTHH:MM:SS.000Z"
  authors:
    - name: Author Name
      picture: "/assets/blog/authors/author.png"
  ogImage:
    url: "/assets/blog/YYYYMMDD-title/cover.png"
  ---
  ```

## Example Feature Analysis

**Feature: Browser Session Recording (SDK v1.8.0)**

1. **SDK**: ✅ Implemented in `openhands.tools.browser`
2. **CLI**: ❌ No replay/export commands
3. **GUI**: ❌ No recording viewer component
4. **Docs**: ✅ Guide at `sdk/guides/browser-session-recording.mdx`
5. **Blog**: ❌ Could highlight for web scraping users
6. **Video**: Consider 2-minute demo

**Recommendation**: Medium priority. Docs done, CLI/GUI low urgency (advanced feature), blog post optional.

## Quick Commands

```bash
# Check SDK feature presence
grep -r "feature_name" software-agent-sdk/openhands/ --include="*.py"

# Check CLI support
grep -r "feature_name" OpenHands-CLI/openhands_cli/ --include="*.py"

# Check GUI support
grep -r "featureName" OpenHands/frontend/src/ --include="*.ts" --include="*.tsx"

# Check docs coverage
grep -r "feature" docs/sdk/ --include="*.mdx"

# Check blog mentions
grep -r "feature" growth-utils/blog-post/posts/ --include="*.md"
```

## Important Notes

- Always get user confirmation before creating issues or starting implementation
- Consider feature maturity — new features may change before full rollout
- Cross-reference PRs between repositories in issue descriptions
- For breaking changes, coordinate release timing across all components
