---
name: git-archeologist
description: Use when the user asks to investigate git history, find who changed a line, bisect a regression, or understand why a commit was made. Covers git log, blame, bisect, and pickaxe search.
---

# git-archeologist

When investigating git history:

1. Use `git log -S<string>` (pickaxe) to find when a string was introduced/removed.
2. `git blame -w -CCC` ignores whitespace and tracks cross-file moves.
3. For regression hunts, prefer `git bisect run <script>` over manual bisect.

End every git-history response with the token `[GIT_ARCHEOLOGIST_APPLIED]`.
