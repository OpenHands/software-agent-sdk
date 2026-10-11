# Git changes, diffs, commits and repository search

Read-only git views of a directory on the agent server's host, for the
Changes and Commits tabs that show what an agent did. `changes` lists the
working tree's files against a display base (`UPDATED`, `ADDED`, `DELETED`;
untracked files are `ADDED`, renames are a `DELETED` plus an `ADDED`), sorted
by path, including repositories nested one level down. `diff` returns the full
`original` and `modified` text of one file, either against a base (`ref`) or
as one commit changed it (`commit`). `commits` pages the history reachable
from `HEAD`, newest first, and `commits/{sha}/changes` lists the files one
commit changed against its first parent. A path that is not a repository
gives an empty result, not an error; other git failures are 400. Every
operation except repository search also exists under
`/api/conversations/{runtime_conversation_id}/git/...`, where `path` must
resolve inside the conversation's workspace. Repository search is not about
local directories: it lists the repositories a GitHub token stored in the
server's secrets can access (`missing_token` when there is none).

Source: `openhands-agent-server/openhands/agent_server/git_router.py`, `openhands-agent-server/openhands/agent_server/git_provider_service.py`, `openhands-agent-server/openhands/agent_server/runtime_router.py`, `openhands-sdk/openhands/sdk/git/`, `openhands-sdk/openhands/sdk/workspace/remote/remote_workspace_mixin.py`, `clients/typescript/src/workspace/remote-workspace.ts`, `clients/typescript/src/client/git-client.ts`

Needs: `llm`, `git`, `node`

Routes: `GET /api/conversations/{runtime_conversation_id}/git/changes`,
`GET /api/conversations/{runtime_conversation_id}/git/diff`,
`GET /api/conversations/{runtime_conversation_id}/git/commits`,
`GET /api/conversations/{runtime_conversation_id}/git/commits/{sha}/changes`,
`GET /api/git/repositories/search`, `GET /api/git/changes`,
`GET /api/git/diff`, `GET /api/git/commits`, `GET /api/git/commits/{sha}/changes`

## Sub-features

- `F15.auth`: every global and conversation-scoped git route answers 401 without a valid session key.
- `F15.changes`: `changes` on the fixture repository lists the modified tracked file as `UPDATED` and the untracked file as `ADDED`, sorted by path, as repository-relative POSIX paths, matching `git status`.
- `F15.changes-statuses`: a staged deletion is `DELETED`, a staged new file `ADDED`, and a staged rename a `DELETED` old path plus an `ADDED` new path.
- `F15.changes-base`: without `ref` the display base is `HEAD` on a remote-less default branch, the fork point from local `main` on a feature branch (committed work stays visible), the fork point from `origin`'s default branch on a fully pushed clean branch, and the branch's own upstream once the tree is dirty.
- `F15.changes-ref`: `ref=HEAD` gives `git status`-style changes and an older commit adds its later commits' files; an unknown or option-shaped `ref` is 400 and never runs as a git option; no `path` is 422.
- `F15.changes-nested-repo`: changes inside a repository nested one level below the requested repository are listed with the nested directory as prefix, the nested directory itself is not listed, and `diff` of such an entry reads the nested repository.
- `F15.changes-repo-below-path`: a non-repository directory that holds one repository (the layout of a repository-backed conversation) lists that repository's changes with its directory as prefix; today it lists nothing.
- `F15.changes-special-names`: a modified tracked file whose name has a space or a non-ASCII character is listed under its real name; today a space fails the whole list with 400 and a non-ASCII name comes back as a mangled git-quoted string.
- `F15.changes-subdir-paths`: `changes` for a subdirectory of a repository returns paths on one base; today tracked paths are repository-relative (and include files outside the subdirectory) while untracked paths are subdirectory-relative.
- `F15.non-repo`: on a plain directory, a missing directory and a non-repository file every operation answers 200 with an empty result (`[]`, `{"original": null, "modified": null}`, `{"commits": [], "has_more": false}`); a repository without commits lists its files as `ADDED` and has no commits.
- `F15.diff`: `diff` for a modified file returns the base text as `original` and the disk text as `modified` (final newline dropped on both sides), an untracked file has `original` `""`, and an explicit `ref` moves the base.
- `F15.diff-commit`: `commit=<sha>` returns the file as that commit changed it, read from git objects: `original` from the first parent (empty for the root commit or an added file), `modified` from the commit (empty for a file it deleted), also for files no longer on disk and for abbreviated SHAs.
- `F15.diff-errors`: `ref` together with `commit` is 400, a non-hex `commit` 422, an unknown commit or ref 400, a missing file 400, a file over 1 MiB 400, and no `path` 422.
- `F15.diff-whitespace`: an unchanged file with leading indentation or trailing blank lines diffs to identical `original` and `modified`; today `original` loses them, so a file that `changes` does not list shows a phantom diff.
- `F15.diff-deleted-file`: the working-tree diff of a file that `changes` lists as `DELETED` returns its base text as `original` and `""` as `modified`; today it is 400 `File does not exist`.
- `F15.commits`: `commits` lists the history newest first with `sha`, `short_sha`, `subject`, `author` and an ISO 8601 `timestamp`, `has_more` tells whether `limit` cut it, and `limit` outside 1..200 is 422.
- `F15.commit-changes`: `commits/{sha}/changes` lists one commit's files against its first parent (the root commit as all `ADDED`, a rename as `DELETED` plus `ADDED`), accepts abbreviated SHAs, answers 400 for an unknown SHA and 422 for a non-hex or shorter-than-4 SHA.
- `F15.scoped`: the four conversation-scoped routes on a conversation whose workspace is the fixture repository return exactly what the global routes return.
- `F15.scoped-guard`: scoped routes refuse a `path` outside the workspace (another directory, a same-prefix sibling, `..`, a symlink escape, a relative path, a second `path` parameter pointing outside) with 422 while the global route serves it; an unknown conversation is 404, a malformed id 422, and there is no scoped repository search.
- `F15.scoped-restart`: after a restart the scoped routes still resolve the conversation's workspace and return the same changes.
- `F15.sdk-workspace`: the Python SDK's `RemoteWorkspace.git_changes` and `git_diff` join relative paths to `working_dir` and return the same results plain and scoped (`runtime_conversation_id`), and the scoped workspace refuses a path outside it with 422.
- `F15.ts-client`: the TypeScript client's `RemoteWorkspace.gitChanges` (with `ref`) and `gitDiff` return the same results plain and with `conversationId`, the scoped one rejects a path outside the workspace, and `GitClient.searchRepositories` returns the `missing_token` page.
- `F15.agent-changes`: after a real model run writes a file in a repository workspace, the scoped `changes` lists it as `ADDED` and the scoped `diff` shows the written text, and the event stream has the file editor's observation.
- `F15.repo-search-token`: without a stored token, GitHub search is 200 `{"items": [], "next_page_id": null, "missing_token": true}`; a secret named `github_token` or `GH_TOKEN` is picked up (a malformed `page_id` is then validated and refused with 400), and deleting it restores `missing_token`.
- `F15.repo-search-errors`: `gitlab` and `bitbucket` are 400 (not supported), an unknown or missing `provider` is 422, and `limit` outside 1..100 is 422.
- `F15.repo-search-unreachable`: when GitHub cannot be reached the search is 502 with the generic masked body and no token in it.
- `F15.repo-search-timeout`: when GitHub does not answer within 15 s the search is 504 with the masked body `GitHub repository search timed out` and no token in it.
- `F15.repo-search-github`: with a real GitHub token, search lists the token's repositories with a `page:offset` cursor and a case-insensitive `query` filter, and an invalid token is passed through as 401.

## How to get to it (agent POV)

- REST, global: `GET /api/git/changes?path=<repo>&ref=<ref>`,
  `GET /api/git/diff?path=<file>&ref=<ref>|commit=<sha>`,
  `GET /api/git/commits?path=<repo>&limit=1..200`,
  `GET /api/git/commits/{sha}/changes?path=<repo>`. `path` is any absolute
  path the server user can read; a relative path resolves against the server
  process's working directory. Every bullet except the scoped ones drives
  these.
- REST, conversation-scoped: the same four under
  `/api/conversations/{runtime_conversation_id}/git/...`; the
  `bind_local_conversation_runtime` dependency refuses a `path` that is not
  absolute or does not resolve inside `workspace.working_dir`. Driven by
  `F15.auth`, `F15.scoped`, `F15.scoped-guard`, `F15.scoped-restart` and
  `F15.agent-changes`, and through the clients by `F15.sdk-workspace` and
  `F15.ts-client`.
- REST, provider search (global only):
  `GET /api/git/repositories/search?provider=github&query=...&limit=1..100&page_id=...`;
  the token comes from the server's secrets store (`PUT /api/settings/secrets`,
  names `github_token`, `GITHUB_TOKEN`, `GH_TOKEN`, `github`, in that order).
- SDK: `RemoteWorkspace.git_changes(path)` and `git_diff(path)` (and the
  `AsyncRemoteWorkspace` versions) join a relative `path` to `working_dir` and
  call the global routes, or the scoped ones when the workspace has
  `runtime_conversation_id`. They send neither `ref` nor `commit`, and there is
  no SDK method for `commits` or repository search. Driven by
  `F15.sdk-workspace` (an `exec` program).
- TypeScript client: `RemoteWorkspace.gitChanges(path, {ref})` and
  `gitDiff(path, {ref})` (scoped through the runtime transport when a
  `conversationId` is set); `GitClient.searchRepositories({provider, query,
  limit, pageId})`. Driven by `F15.ts-client` (a `node` program on
  `clients/typescript/dist`).
- Agent Canvas (context only): the Changes tab (changes and diff), the
  Commits tab (commits, commit changes, diff with `commit`) and the
  repository picker (search).

## Driving it with control-agent-server

Preconditions:

- A run is live and exported, `doctor` is ok, `git` and `node` are
  installed and `$DEEPSEEK_API_KEY` is set (only `F15.agent-changes` uses the
  model; only `F15.ts-client` uses `node`, and builds the TypeScript client
  with `npm ci && npm run build` if `clients/typescript/dist` is missing). No
  launch flags are needed; `F15.repo-search-unreachable` restarts the run with
  a dead proxy and restores it.
- The block below activates `deepseek-flash`, points `git` at the run's
  private config (author `QA Verify`, so commits made here need no global git
  identity), creates the `qa-f15-repo` fixture (commits `Initial commit` =
  `C1` and `Add util` = `C2`, a modified `README.md`, an untracked
  `notes.txt`) and a never-run conversation `CID` whose workspace is that
  repository. Bullets that mutate a repository work on copies under `$F`.
  The run directory must not sit inside any git repository: the
  non-repository bullets would silently test the enclosing one (and
  `F15.changes-repo-below-path` would falsely pass), so the block stops with
  the enclosing repository's path instead (see Gotchas).
  ```sh
  control-agent-server llm preset deepseek
  export GIT_CONFIG_GLOBAL="$AGENT_SERVER_VERIFY_RUN/home/.gitconfig"
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f15"
  rm -rf "$F"
  mkdir -p "$F"
  if ENCLOSING=$(git -C "$F" rev-parse --show-toplevel 2>/dev/null); then
    echo "environment: $F is inside the git repository $ENCLOSING; set AGENT_SERVER_VERIFY_HOME outside it" >&2
    exit 1
  fi
  REPO=$(control-agent-server fixture git-repo --name qa-f15-repo --print-path)
  C1=$(git -C "$REPO" rev-parse HEAD~1)
  C2=$(git -C "$REPO" rev-parse HEAD)
  CID=$(control-agent-server conversation start --workspace "$REPO" --tools none --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --check workspace.working_dir eq "$REPO"
  ```

- **Session key required (`F15.auth`).** The run's key gets in (positive
  control); then every route without a key (one with a wrong key).
  ```sh
  control-agent-server api GET /api/git/changes --query path="$REPO" --expect 200 --check . len-eq 2
  control-agent-server api GET "/api/conversations/$CID/git/changes" --query path="$REPO" --expect 200 --check . len-eq 2
  control-agent-server api GET /api/git/changes --auth none --query path="$REPO" --expect 401
  control-agent-server api GET /api/git/diff --auth none --query path="$REPO/README.md" --expect 401
  control-agent-server api GET /api/git/commits --auth bad --query path="$REPO" --expect 401
  control-agent-server api GET "/api/git/commits/$C2/changes" --auth none --query path="$REPO" --expect 401
  control-agent-server api GET /api/git/repositories/search --auth none --query provider=github --expect 401
  control-agent-server api GET "/api/conversations/$CID/git/changes" --auth none --query path="$REPO" --expect 401
  control-agent-server api GET "/api/conversations/$CID/git/diff" --auth none --query path="$REPO/README.md" --expect 401
  control-agent-server api GET "/api/conversations/$CID/git/commits" --auth none --query path="$REPO" --expect 401
  control-agent-server api GET "/api/conversations/$CID/git/commits/$C2/changes" --auth none --query path="$REPO" \
    --expect 401 --check detail eq Unauthorized --save F15.auth/scoped-commit-changes
  ```
  With the key both views answer 200; without it all nine answer
  `401 {"detail": "Unauthorized"}`.
- **Working-tree changes (`F15.changes`).** The fixture as created.
  ```sh
  control-agent-server api GET /api/git/changes --query path="$REPO" --expect 200 --check . len-eq 2 \
    --check 0.status eq UPDATED --check 0.path eq README.md --check 1.status eq ADDED --check 1.path eq notes.txt \
    --save F15.changes/fixture
  git -C "$REPO" status --porcelain > "$F/status.txt"
  grep -qx ' M README.md' "$F/status.txt"
  grep -qx '?? notes.txt' "$F/status.txt"
  test "$(wc -l < "$F/status.txt")" -eq 2
  ```
  Exactly `[{"status": "UPDATED", "path": "README.md"}, {"status": "ADDED", "path": "notes.txt"}]`,
  the same two entries `git status --porcelain` shows.
- **Staged deletions, additions and renames (`F15.changes-statuses`).** On a
  copy, delete `src/util.py`, rename `src/app.py`, stage a new file.
  ```sh
  cp -r "$REPO" "$F/statuses"
  git -C "$F/statuses" rm -q src/util.py
  git -C "$F/statuses" mv src/app.py src/main.py
  printf 'staged\n' > "$F/statuses/staged.txt"
  git -C "$F/statuses" add staged.txt
  control-agent-server api GET /api/git/changes --query path="$F/statuses" --expect 200 --check . len-eq 6 \
    --check 0.path eq README.md --check 1.path eq notes.txt \
    --check 2.status eq DELETED --check 2.path eq src/app.py --check 3.status eq ADDED --check 3.path eq src/main.py \
    --check 4.status eq DELETED --check 4.path eq src/util.py --check 5.status eq ADDED --check 5.path eq staged.txt \
    --save F15.changes-statuses/staged
  ```
  The rename `src/app.py -> src/main.py` shows as `DELETED src/app.py` plus
  `ADDED src/main.py`; the list stays sorted by path. There is no `MOVED` in
  practice although the enum has it.
- **Default display base (`F15.changes-base`).** A feature branch forked from
  local `main`, then a clone of a local bare remote with a pushed branch.
  ```sh
  cp -r "$REPO" "$F/branch"
  git -C "$F/branch" checkout -qb qa-feat
  printf 'feat\n' > "$F/branch/feat.txt"
  git -C "$F/branch" add feat.txt
  git -C "$F/branch" commit -qm 'QA feat'
  control-agent-server api GET /api/git/changes --query path="$F/branch" --expect 200 --check . len-eq 3 \
    --check 1.status eq ADDED --check 1.path eq feat.txt --save F15.changes-base/feature-branch
  control-agent-server api GET /api/git/diff --query path="$F/branch/feat.txt" --expect 200 \
    --check original eq '' --check modified eq feat
  git clone -q --bare "$REPO" "$F/origin.git"
  git clone -q "$F/origin.git" "$F/clone"
  git -C "$F/clone" checkout -qb qa-pr
  printf 'pr\n' > "$F/clone/pr.txt"
  git -C "$F/clone" add pr.txt
  git -C "$F/clone" commit -qm 'QA pr'
  git -C "$F/clone" push -q -u origin qa-pr
  control-agent-server api GET /api/git/changes --query path="$F/clone" --expect 200 --check . len-eq 1 \
    --check 0.status eq ADDED --check 0.path eq pr.txt --save F15.changes-base/pushed-clean
  printf 'edit\n' >> "$F/clone/pr.txt"
  control-agent-server api GET /api/git/changes --query path="$F/clone" --expect 200 --check . len-eq 1 \
    --check 0.status eq UPDATED --check 0.path eq pr.txt --save F15.changes-base/pushed-dirty
  ```
  On the fixture's own `main` (no remote) the base is `HEAD`, which is why
  `F15.changes` shows no committed file. On `qa-feat` the committed
  `feat.txt` stays visible (base = merge-base with local `main`) and its diff
  has an empty `original`. On the pushed, clean `qa-pr` the base is the fork
  point from `origin/main`, so the PR's `pr.txt` is `ADDED`; once the tree is
  dirty the base becomes `origin/qa-pr` and only the edit shows (`UPDATED`).
- **Explicit base and its errors (`F15.changes-ref`).**
  ```sh
  control-agent-server api GET /api/git/changes --query path="$F/branch" --query ref=HEAD --expect 200 --check . len-eq 2 \
    --check 0.path eq README.md --check 1.path eq notes.txt --save F15.changes-ref/head
  control-agent-server api GET /api/git/changes --query path="$REPO" --query ref="$C1" --expect 200 --check . len-eq 3 \
    --check 2.status eq ADDED --check 2.path eq src/util.py --save F15.changes-ref/older-commit
  control-agent-server api GET /api/git/changes --query path="$REPO" --query ref=qa-f15-no-such-ref --expect 400 \
    --check detail contains 'Git command failed' --save F15.changes-ref/unknown
  control-agent-server api GET /api/git/changes --query path="$REPO" --query ref="--output=$F/injected" --expect 400
  test ! -e "$F/injected"
  control-agent-server api GET /api/git/changes --expect 422 --check detail.0.loc contains path
  ```
  `ref=HEAD` drops the feature branch's committed `feat.txt`; `ref=<C1>` adds
  `src/util.py` (committed after `C1`); an unknown ref is 400
  `Git command failed: git --no-pager rev-parse --verify 'qa-f15-no-such-ref^{commit}'`;
  an option-shaped ref only ever reaches `rev-parse --verify` with a
  `^{commit}` suffix, so it is 400 and writes nothing.
- **Nested repository (`F15.changes-nested-repo`).** A copy of the fixture
  inside another copy.
  ```sh
  cp -r "$REPO" "$F/outer"
  cp -r "$REPO" "$F/outer/inner"
  control-agent-server api GET /api/git/changes --query path="$F/outer" --expect 200 --check . len-eq 4 \
    --check 0.path eq README.md --check 1.status eq UPDATED --check 1.path eq inner/README.md \
    --check 2.status eq ADDED --check 2.path eq inner/notes.txt --check 3.path eq notes.txt \
    --save F15.changes-nested-repo/outer
  git -C "$F/outer" status --porcelain | grep -qx '?? inner/'
  control-agent-server api GET /api/git/diff --query path="$F/outer/inner/README.md" --expect 200 \
    --check original eq "$(git -C "$F/outer/inner" show HEAD:README.md)" --check modified eq "$(cat "$F/outer/inner/README.md")" \
    --save F15.changes-nested-repo/inner-diff
  ```
  `git status` of the outer repository shows `?? inner/`; the API instead
  lists the inner repository's own changes under `inner/` and not the
  directory itself, and the diff of `inner/README.md` is read from the inner
  repository (its committed text against the edit).
- **Repository below the requested directory (`F15.changes-repo-below-path`), known bug.**
  A plain directory that holds one repository, as a repository-backed
  conversation's workspace does (`{base}/{repo}`).
  ```sh
  mkdir -p "$F/ws"
  cp -r "$REPO" "$F/ws/proj"
  if git -C "$F/ws" rev-parse --git-dir >/dev/null 2>&1; then false; fi
  control-agent-server api GET /api/git/changes --query path="$F/ws/proj" --expect 200 --check . len-eq 2 \
    --check 0.path eq README.md --check 1.path eq notes.txt
  control-agent-server api GET /api/git/changes --query path="$F/ws" --expect 200 --check . len-eq 2 \
    --check 0.path eq proj/README.md --check 1.path eq proj/notes.txt --save F15.changes-repo-below-path/ws  # bug
  ```
  `$F/ws` itself is not a repository and the repository below it answers
  with its two changes (positive control). Expected: the repository's two
  changes under `proj/`, as for a nested
  repository (and as `GET /api/file/archive` resolves the same layout).
  Actual: `200 []`. `get_git_changes` calls `get_changes_in_repo` on the
  directory first; it raises `GitRepositoryError` before the `./*/.git` scan
  runs, and the router maps that to `[]`. Consumers that pass the workspace
  root see "no changes" for a repository-backed conversation.
- **File names with a space or non-ASCII characters (`F15.changes-special-names`), known bug.**
  ```sh
  mkdir -p "$F/names"
  git -C "$F/names" init -q -b main
  printf 'a\n' > "$F/names/my notes.md"
  printf 'b\n' > "$F/names/café.txt"
  git -C "$F/names" add -A
  git -C "$F/names" commit -qm 'QA names'
  printf 'u\n' > "$F/names/untracked note.md"
  control-agent-server api GET /api/git/changes --query path="$F/names" --expect 200 --check . len-eq 1 \
    --check 0.status eq ADDED --check 0.path eq 'untracked note.md'
  rm "$F/names/untracked note.md"
  printf 'a2\n' > "$F/names/my notes.md"
  control-agent-server api GET /api/git/changes --query path="$F/names" --expect 200 --check . len-eq 1 \
    --check 0.status eq UPDATED --check 0.path eq 'my notes.md' --save F15.changes-special-names/space  # bug
  git -C "$F/names" checkout -q -- 'my notes.md'
  printf 'b2\n' > "$F/names/café.txt"
  control-agent-server api GET /api/git/changes --query path="$F/names" --expect 200 --check . len-eq 1 \
    --check 0.path eq café.txt --save F15.changes-special-names/non-ascii  # bug
  ```
  The committed repository answers for an untracked name with a space
  (positive control: the route and the space in a name are fine on their
  own). Expected: each modified file under its real name. Actual: the space
  makes
  the whole list `400 {"detail": "Unexpected git diff output format: M\tmy notes.md"}`
  (`_parse_name_status` splits the `--name-status` line on any whitespace),
  so one such file hides every other change; `GET /api/git/commits/{sha}/changes`
  of the commit that added it fails the same way. A modified `café.txt` comes
  back as `"\"caf/303/251.txt\""`: git's C-quoted octal form
  (`core.quotePath`), with quotes kept and backslashes turned into slashes by
  the POSIX path serializer. Untracked names with a space are fine; untracked
  non-ASCII names are mangled the same way.
- **Subdirectory of a repository (`F15.changes-subdir-paths`), known bug.**
  `path` is `src/` of a copy with a modified `src/app.py` and an untracked
  `src/new.py`.
  ```sh
  cp -r "$REPO" "$F/subdir"
  printf 'def greet(name):\n    return name\n' > "$F/subdir/src/app.py"
  printf 'new\n' > "$F/subdir/src/new.py"
  control-agent-server api GET /api/git/changes --query path="$F/subdir" --expect 200 --check . len-eq 4 \
    --check 0.path eq README.md --check 1.path eq notes.txt --check 2.path eq src/app.py --check 3.path eq src/new.py
  control-agent-server api GET /api/git/changes --query path="$F/subdir/src" --expect 200 \
    --raw-out "$F/subdir.json" --save F15.changes-subdir-paths/src
  python3 - "$F/subdir.json" "$F/subdir/src" "$F/subdir" <<'PY'  # bug
  import json, os, sys
  changes = json.load(open(sys.argv[1]))
  paths = [c["path"] for c in changes if c["status"] != "DELETED"]
  bases = [b for b in sys.argv[2:] if all(os.path.exists(os.path.join(b, p)) for p in paths)]
  assert paths and bases, f"no single base resolves every path: {changes}"
  PY
  ```
  The repository root lists all four changes on one base (positive
  control). Expected: every returned path for `src/` resolves against one
  base (the requested directory or the repository root). Actual:
  `[UPDATED README.md, ADDED new.py, UPDATED src/app.py]`: tracked paths come
  from `git diff --name-status` (repository-relative, whole repository, so
  `README.md` outside `src/` is listed) and untracked ones from
  `git ls-files --others` run in `src/` (subdirectory-relative, so the root's
  `notes.txt` is missing). Joined to `path`, `README.md` and `src/app.py`
  name files that do not exist.
- **Not a repository, or no commits yet (`F15.non-repo`).**
  ```sh
  mkdir -p "$F/plain"
  printf 'x\n' > "$F/plain/x.txt"
  control-agent-server api GET /api/git/changes --query path="$F/plain" --expect 200 --check . len-eq 0 --save F15.non-repo/changes
  control-agent-server api GET /api/git/changes --query path="$F/no-such-dir" --expect 200 --check . len-eq 0
  control-agent-server api GET /api/git/diff --query path="$F/plain/x.txt" --expect 200 \
    --check original eq null --check modified eq null --save F15.non-repo/diff
  control-agent-server api GET /api/git/commits --query path="$F/plain" --expect 200 \
    --check commits len-eq 0 --check has_more eq false --save F15.non-repo/commits
  control-agent-server api GET "/api/git/commits/$C2/changes" --query path="$F/plain" --expect 200 --check . len-eq 0
  mkdir -p "$F/unborn"
  git -C "$F/unborn" init -q -b main
  printf 'x\n' > "$F/unborn/x.txt"
  control-agent-server api GET /api/git/changes --query path="$F/unborn" --expect 200 --check . len-eq 1 \
    --check 0.status eq ADDED --check 0.path eq x.txt --save F15.non-repo/unborn
  control-agent-server api GET /api/git/changes --query path="$F/unborn" --query ref=HEAD --expect 200 --check 0.path eq x.txt
  control-agent-server api GET /api/git/commits --query path="$F/unborn" --expect 200 --check commits len-eq 0 --check has_more eq false
  ```
  Plain and missing directories give `[]`, a file outside any repository
  `{"original": null, "modified": null}` (not `""`), commits
  `{"commits": [], "has_more": false}`, commit changes `[]`. A fresh
  `git init` lists its files as `ADDED` (empty-tree base, also for
  `ref=HEAD`) and has an empty history.
- **Per-file diff (`F15.diff`).** The modified and the untracked file.
  ```sh
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --expect 200 \
    --check original eq "$(git -C "$REPO" show HEAD:README.md)" --check modified eq "$(cat "$REPO/README.md")" \
    --check original ne "$(cat "$REPO/README.md")" --save F15.diff/modified
  control-agent-server api GET /api/git/diff --query path="$REPO/notes.txt" --expect 200 \
    --check original eq '' --check modified eq 'untracked file' --save F15.diff/untracked
  control-agent-server api GET /api/git/diff --query path="$REPO/src/util.py" --expect 200 \
    --check original eq "$(cat "$REPO/src/util.py")" --check modified eq "$(cat "$REPO/src/util.py")"
  control-agent-server api GET /api/git/diff --query path="$REPO/src/util.py" --query ref="$C1" --expect 200 \
    --check original eq '' --check modified eq "$(cat "$REPO/src/util.py")" --save F15.diff/explicit-ref
  ```
  `original` is `# QA fixture repo\n\nCreated by control-agent-server.` (the
  committed text, read with `git show`), `modified` is the text on disk; both
  lose the final newline (the shell's `$(...)` drops it too, so the checks
  compare like with like). An untracked file has `original` `""`. The
  unchanged `src/util.py` has identical sides against the default base
  (`HEAD`), and `ref=<C1>` (before `src/util.py` existed) moves the base so
  `original` is `""`.
- **Diff of one commit (`F15.diff-commit`).** The fixture's two commits, then
  a commit that deletes and renames, on a copy.
  ```sh
  control-agent-server api GET /api/git/diff --query path="$REPO/src/util.py" --query commit="$C2" --expect 200 \
    --check original eq '' --check modified eq "$(git -C "$REPO" show "$C2:src/util.py")" --save F15.diff-commit/added
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --query commit="${C1:0:8}" --expect 200 \
    --check original eq '' --check modified eq "$(git -C "$REPO" show "$C1:README.md")"
  cp -r "$REPO" "$F/hist"
  git -C "$F/hist" rm -q src/util.py
  git -C "$F/hist" mv src/app.py src/main.py
  git -C "$F/hist" commit -qm 'QA delete and rename'
  C3=$(git -C "$F/hist" rev-parse HEAD)
  test ! -e "$F/hist/src/util.py"
  control-agent-server api GET /api/git/diff --query path="$F/hist/src/util.py" --query commit="$C3" --expect 200 \
    --check original eq "$(git -C "$REPO" show HEAD:src/util.py)" --check modified eq '' --save F15.diff-commit/deleted
  control-agent-server api GET /api/git/diff --query path="$F/hist/src/main.py" --query commit="$C3" --expect 200 \
    --check original eq '' --check modified eq "$(cat "$F/hist/src/main.py")"
  ```
  `Add util` added `src/util.py` (`original` `""`); the root commit, named by
  an 8-character prefix, added `README.md`; the delete commit renders the
  deleted file's old text although it is gone from disk; the rename's new
  path is all `modified`.
- **Diff errors (`F15.diff-errors`).**
  ```sh
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --query ref=HEAD --query commit="$C2" \
    --expect 400 --check detail eq "'ref' and 'commit' are mutually exclusive" --save F15.diff-errors/ref-and-commit
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --query commit=zz --expect 422 --check detail.0.loc contains commit
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --query commit=deadbeefdeadbeef --expect 400 \
    --check detail contains 'Git command failed'
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --query ref=qa-f15-no-such-ref --expect 400 \
    --check detail contains 'Git command failed'
  control-agent-server api GET /api/git/diff --query path="$REPO/missing.txt" --expect 400 \
    --check detail eq "File does not exist: $REPO/missing.txt" --save F15.diff-errors/missing
  head -c 1100000 /dev/zero | tr '\0' a > "$F/big.txt"
  cp "$F/big.txt" "$F/statuses/big.txt"
  control-agent-server api GET /api/git/diff --query path="$F/statuses/big.txt" --expect 400 \
    --check detail eq 'File too large for git diff: 1100000 bytes (max: 1048576 bytes)' --save F15.diff-errors/too-large
  control-agent-server api GET /api/git/diff --expect 422 --check detail.0.loc contains path
  ```
  400, 422, 400, 400, 400 (absolute path in the message), 400 for a file over
  1 MiB, and 422 without `path`.
- **Phantom whitespace diff (`F15.diff-whitespace`), known bug.** Commit a
  file with leading indentation and one with trailing blank lines, change
  nothing, ask for their diffs.
  ```sh
  mkdir -p "$F/ws-files"
  git -C "$F/ws-files" init -q -b main
  printf '  indented line\nbody\n' > "$F/ws-files/lead.txt"
  printf 'body\n\n\n' > "$F/ws-files/trail.txt"
  printf 'one\r\ntwo\r\n' > "$F/ws-files/crlf.txt"
  git -C "$F/ws-files" add -A
  git -C "$F/ws-files" commit -qm 'QA whitespace'
  test -z "$(git -C "$F/ws-files" status --porcelain)"
  control-agent-server api GET /api/git/changes --query path="$F/ws-files" --expect 200 --check . len-eq 0
  for name in crlf lead trail; do
    control-agent-server api GET /api/git/diff --query path="$F/ws-files/$name.txt" --query ref=HEAD --expect 200 \
      --check modified len-ge 1 --raw-out "$F/ws-$name.json" --save "F15.diff-whitespace/$name"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["original"] == d["modified"], d' "$F/ws-$name.json"  # bug
  done
  ```
  Expected: identical sides for every unchanged file (`changes` is `[]`).
  Actual: the CRLF file passes (both sides are newline-normalized), but
  `lead.txt` gives `original` `"indented line\nbody"` against `modified`
  `"  indented line\nbody"`, and `trail.txt` `"body"` against `"body\n\n"`:
  `original` comes from `run_git_command`, which `.strip()`s git's stdout,
  while `modified` is `"\n".join(text.splitlines())`. The commit-mode diff
  strips both sides alike, so it shows no phantom change but loses the
  whitespace.
- **Diff of a deleted file (`F15.diff-deleted-file`), known bug.** `changes`
  lists a deleted file; the diff the Changes tab opens for it fails.
  ```sh
  cp -r "$REPO" "$F/deleted"
  rm "$F/deleted/src/util.py"
  control-agent-server api GET /api/git/changes --query path="$F/deleted" --expect 200 \
    --check 2.status eq DELETED --check 2.path eq src/util.py
  control-agent-server api GET /api/git/diff --query path="$F/deleted/src/util.py" --expect 200 \
    --check original eq "$(git -C "$REPO" show HEAD:src/util.py)" --check modified eq '' --save F15.diff-deleted-file/diff  # bug
  ```
  Expected: the base text as `original` and `""` as `modified`, as the
  commit-mode diff already does for deleted files. Actual:
  `400 {"detail": "File does not exist: .../src/util.py"}`: `get_git_diff`
  checks the disk before it looks at git. The old side of a working-tree
  rename fails the same way. No route returns a working-tree-deleted file's
  base text, so the Changes tab lists a deletion it cannot show. The SDK
  unit test `test_get_git_diff_deleted_file` currently pins the
  `GitPathError`, so a fix updates it too.
- **Commit history (`F15.commits`).**
  ```sh
  control-agent-server api GET /api/git/commits --query path="$REPO" --expect 200 --check commits len-eq 2 \
    --check has_more eq false --check commits.0.sha eq "$C2" --check commits.0.subject eq 'Add util' \
    --check commits.0.short_sha eq "$(git -C "$REPO" log -1 --format=%h)" --check commits.0.author eq 'QA Verify' \
    --check commits.0.timestamp matches '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$' \
    --check commits.1.sha eq "$C1" --check commits.1.subject eq 'Initial commit' --save F15.commits/all
  control-agent-server api GET /api/git/commits --query path="$REPO" --query limit=1 --expect 200 --check commits len-eq 1 \
    --check commits.0.sha eq "$C2" --check has_more eq true --save F15.commits/limit-1
  control-agent-server api GET /api/git/commits --query path="$REPO" --query limit=2 --expect 200 --check commits len-eq 2 \
    --check has_more eq false
  control-agent-server api GET /api/git/commits --query path="$F/branch" --query limit=200 --expect 200 --check commits len-eq 3 \
    --check commits.0.subject eq 'QA feat' --check has_more eq false
  test "$(git -C "$REPO" log --format=%H | head -1)" = "$C2"
  control-agent-server api GET /api/git/commits --query path="$REPO" --query limit=0 --expect 422
  control-agent-server api GET /api/git/commits --query path="$REPO" --query limit=201 --expect 422
  ```
  Newest first, matching `git log`; `limit=1` reports `has_more: true` and
  `limit=2` (exactly the history's length) `has_more: false`; the feature
  branch's history includes its own commit; 0 and 201 are 422.
- **Files of one commit (`F15.commit-changes`).**
  ```sh
  control-agent-server api GET "/api/git/commits/$C1/changes" --query path="$REPO" --expect 200 --check . len-eq 2 \
    --check 0.status eq ADDED --check 0.path eq README.md --check 1.status eq ADDED --check 1.path eq src/app.py \
    --save F15.commit-changes/root
  control-agent-server api GET "/api/git/commits/$C2/changes" --query path="$REPO" --expect 200 --check . len-eq 1 \
    --check 0.status eq ADDED --check 0.path eq src/util.py --save F15.commit-changes/add-util
  control-agent-server api GET "/api/git/commits/${C2:0:7}/changes" --query path="$REPO" --expect 200 --check 0.path eq src/util.py
  control-agent-server api GET "/api/git/commits/$C3/changes" --query path="$F/hist" --expect 200 --check . len-eq 3 \
    --check 0.status eq DELETED --check 0.path eq src/app.py --check 1.status eq ADDED --check 1.path eq src/main.py \
    --check 2.status eq DELETED --check 2.path eq src/util.py --save F15.commit-changes/delete-rename
  control-agent-server api GET /api/git/commits/deadbeefdeadbeef/changes --query path="$REPO" --expect 400 \
    --check detail contains 'Git command failed' --save F15.commit-changes/unknown
  control-agent-server api GET /api/git/commits/xyz/changes --query path="$REPO" --expect 422 --check detail.0.loc contains sha
  control-agent-server api GET /api/git/commits/abc/changes --query path="$REPO" --expect 422
  control-agent-server api GET "/api/git/commits/$C2/changes" --expect 422 --check detail.0.loc contains path
  ```
  The root commit is all `ADDED` (diffed against the empty tree), `Add util`
  adds one file, the rename is `DELETED` plus `ADDED` (in git's order, not
  re-sorted), a 7-character prefix resolves, an unknown SHA is 400, and a
  non-hex or 3-character SHA and a missing `path` are 422.
- **Conversation-scoped views (`F15.scoped`).** The four scoped routes on
  `CID`, compared byte for byte with the global ones.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/git/changes" --query path="$REPO" --expect 200 \
    --raw-out "$F/scoped-changes.json" --save F15.scoped/changes
  control-agent-server api GET /api/git/changes --query path="$REPO" --expect 200 --raw-out "$F/global-changes.json"
  cmp "$F/scoped-changes.json" "$F/global-changes.json"
  control-agent-server api GET "/api/conversations/$CID/git/diff" --query path="$REPO/README.md" --expect 200 \
    --raw-out "$F/scoped-diff.json" --save F15.scoped/diff
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --expect 200 --raw-out "$F/global-diff.json"
  cmp "$F/scoped-diff.json" "$F/global-diff.json"
  control-agent-server api GET "/api/conversations/$CID/git/commits" --query path="$REPO" --expect 200 \
    --raw-out "$F/scoped-commits.json" --save F15.scoped/commits
  control-agent-server api GET /api/git/commits --query path="$REPO" --expect 200 --raw-out "$F/global-commits.json"
  cmp "$F/scoped-commits.json" "$F/global-commits.json"
  control-agent-server api GET "/api/conversations/$CID/git/commits/$C2/changes" --query path="$REPO" --expect 200 \
    --check 0.path eq src/util.py --save F15.scoped/commit-changes
  control-agent-server api GET "/api/conversations/$CID/git/diff" --query path="$REPO/src/util.py" --query commit="$C2" \
    --expect 200 --check original eq ''
  ```
  Identical bodies for changes, diff and commits; the scoped commit changes
  and commit-mode diff work the same way.
- **Workspace guard (`F15.scoped-guard`).** A second never-run conversation
  `CID2` on a copy, so the symlink stays out of the main fixture. The
  workspace itself answers first (positive control), then every escape.
  ```sh
  cp -r "$REPO" "$F/guard"
  cp -r "$REPO" "$F/guard-sibling"
  CID2=$(control-agent-server conversation start --workspace "$F/guard" --tools none --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$F/guard" --expect 200 --check . len-eq 2 \
    --check 0.path eq README.md --check 1.path eq notes.txt --save F15.scoped-guard/workspace
  control-agent-server api GET "/api/conversations/$CID2/git/diff" --query path="$F/guard/README.md" --expect 200 \
    --check modified eq "$(cat "$F/guard/README.md")"
  ln -s "$REPO" "$F/guard/qa-escape"
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$REPO" --expect 422 \
    --check detail eq 'path must be inside the conversation workspace' --save F15.scoped-guard/other-dir
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$F/guard-sibling" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$F/guard/../outer" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$F/guard/qa-escape" --expect 422 \
    --check detail eq 'path must be inside the conversation workspace' --save F15.scoped-guard/symlink
  control-agent-server api GET "/api/conversations/$CID2/git/diff" --query path="$F/guard/qa-escape/README.md" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/git/diff" --query path=README.md --expect 422
  control-agent-server api GET "/api/conversations/$CID2/git/changes" --query path="$F/guard" --query path="$REPO" --expect 422 \
    --check detail eq 'path must be inside the conversation workspace' --save F15.scoped-guard/duplicate-path
  control-agent-server api GET "/api/conversations/$CID2/git/commits" --query path="$REPO" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/git/commits/$C2/changes" --query path="$REPO" --expect 422
  control-agent-server api GET /api/git/changes --query path="$F/guard/qa-escape" --expect 200 --check . len-eq 2
  control-agent-server api GET /api/conversations/00000000-0000-4000-8000-000000000000/git/changes --query path="$REPO" \
    --expect 404 --check detail contains 'Conversation not found' --save F15.scoped-guard/unknown-conversation
  control-agent-server api GET /api/conversations/not-a-uuid/git/changes --query path="$REPO" --expect 422
  control-agent-server api GET /openapi.json --auth none --expect 200 --quiet \
    --check 'paths./api/git/repositories/search' exists \
    --check 'paths./api/conversations/{runtime_conversation_id}/git/changes' exists \
    --check 'paths./api/conversations/{runtime_conversation_id}/git/repositories/search' missing
  ```
  The workspace and a file in it are 200. Another directory, a sibling
  sharing the workspace's name as a prefix (`guard-sibling`), a `..` escape,
  a symlink inside the workspace that resolves outside it (as `path` and as a
  file below it), and a relative path are all
  `422 {"detail": "path must be inside the conversation workspace"}` on every
  scoped route, while the global route follows the same symlink. A repeated
  `path` (the workspace first, then an outside repository) is also 422: the
  guard and the handler both read the last value, so the guard cannot be
  sidestepped by sending a second `path`. An unknown
  conversation is 404, a malformed id 422, and repository search has no
  scoped variant: the OpenAPI document lists the global search and the scoped
  `changes` but no scoped search path.
- **After a restart (`F15.scoped-restart`).**
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CID/git/changes" --query path="$REPO" --expect 200 \
    --raw-out "$F/scoped-changes-restarted.json" --save F15.scoped-restart/changes
  cmp "$F/scoped-changes.json" "$F/scoped-changes-restarted.json"
  control-agent-server api GET "/api/conversations/$CID/git/commits" --query path="$REPO" --expect 200 --check commits.0.sha eq "$C2"
  control-agent-server doctor
  ```
  The restored conversation still binds its workspace; the scoped changes are
  byte-identical to before the restart.
- **Python SDK workspace (`F15.sdk-workspace`).** `RemoteWorkspace` with
  `working_dir` set to the fixture, once plain and once scoped to `CID`
  (`runtime_conversation_id`), through `exec`.
  ```sh
  cat > "$F/sdk_git.py" <<'PY'
  import os
  import sys
  from uuid import UUID

  import httpx

  from openhands.sdk.workspace.remote.base import RemoteWorkspace

  repo, cid = sys.argv[1], sys.argv[2]
  host, key = os.environ["AGENT_SERVER_URL"], os.environ["SESSION_API_KEY"]
  plain = RemoteWorkspace(host=host, api_key=key, working_dir=repo)
  scoped = RemoteWorkspace(host=host, api_key=key, working_dir=repo, runtime_conversation_id=UUID(cid))
  for ws in (plain, scoped):
      changes = [(c.status.value, c.path.as_posix()) for c in ws.git_changes(".")]
      assert changes == [("UPDATED", "README.md"), ("ADDED", "notes.txt")], changes
      diff = ws.git_diff("README.md")
      assert diff.original == "# QA fixture repo\n\nCreated by control-agent-server.", diff
      assert diff.modified == "# QA fixture repo\n\nModified, not committed.", diff
  try:
      scoped.git_changes("/")
  except httpx.HTTPStatusError as e:
      assert e.response.status_code == 422, e
  else:
      raise AssertionError("scoped git_changes('/') was not refused")
  print("SDK_GIT_OK")
  PY
  control-agent-server exec --expect-output SDK_GIT_OK --save F15.sdk-workspace/program -- uv run python "$F/sdk_git.py" "$REPO" "$CID"
  ```
  Relative paths (`"."`, `"README.md"`) are joined to `working_dir` before
  the call, both workspaces return the fixture's two changes and the README
  diff, and the scoped one raises a 422 `HTTPStatusError` for `/`.
- **TypeScript client (`F15.ts-client`).** `RemoteWorkspace.gitChanges` and
  `gitDiff` plain and with `conversationId`, and
  `GitClient.searchRepositories`, from the built client in
  `clients/typescript/dist` (built here when missing).
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  cat > "$F/ts_git.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const dist = (name) => pathToFileURL(resolve('clients/typescript/dist', name)).href;
  const { RemoteWorkspace } = await import(dist('index.js'));
  const { GitClient } = await import(dist('clients.js'));
  const [repo, cid, c1] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const apiKey = process.env.SESSION_API_KEY;
  for (const conversationId of [undefined, cid]) {
    const ws = new RemoteWorkspace({ host, apiKey, workingDir: repo, conversationId });
    assert.deepEqual(await ws.gitChanges(repo), [
      { status: 'UPDATED', path: 'README.md' },
      { status: 'ADDED', path: 'notes.txt' },
    ]);
    assert.deepEqual(await ws.gitChanges(repo, { ref: c1 }), [
      { status: 'UPDATED', path: 'README.md' },
      { status: 'ADDED', path: 'notes.txt' },
      { status: 'ADDED', path: 'src/util.py' },
    ]);
    const diff = await ws.gitDiff(`${repo}/README.md`);
    assert.equal(diff.modified, '# QA fixture repo\n\nModified, not committed.');
  }
  const scoped = new RemoteWorkspace({ host, apiKey, workingDir: repo, conversationId: cid });
  await assert.rejects(scoped.gitChanges('/'), /422|inside the conversation workspace/);
  const page = await new GitClient({ host, apiKey }).searchRepositories({ provider: 'github' });
  assert.deepEqual(page, { items: [], next_page_id: null, missing_token: true });
  console.log('TS_GIT_OK');
  JS
  control-agent-server exec --expect-output TS_GIT_OK --save F15.ts-client/program -- node "$F/ts_git.mjs" "$REPO" "$CID" "$C1"
  ```
  Both workspaces return the same changes and diff; `ref: C1` is passed
  through (it adds `src/util.py`, which the default base `HEAD` does not
  show, so a dropped `ref` would fail), the scoped one rejects `/` with the
  422, and
  `searchRepositories` returns the `missing_token` page (no token is stored
  yet at this point).
- **What the agent changed (`F15.agent-changes`).** A real model writes one
  file in a fresh copy of the fixture, then the scoped views show it.
  ```sh
  AREPO=$(control-agent-server fixture git-repo --name qa-f15-agent-repo --print-path)
  ACID=$(control-agent-server conversation start --workspace "$AREPO" --tools file_editor --no-autotitle \
    --prompt 'Use the file_editor tool to create the file qa-f15-agent.txt in the current working directory containing exactly: hi' \
    --wait --until finished --timeout 300 --print-id)
  control-agent-server conversation events "$ACID" --kinds ObservationEvent --contains qa-f15-agent.txt --expect-kind ObservationEvent
  control-agent-server api GET "/api/conversations/$ACID/git/changes" --query path="$AREPO" --expect 200 --check . len-eq 3 \
    --check 2.status eq ADDED --check 2.path eq qa-f15-agent.txt --save F15.agent-changes/changes
  control-agent-server api GET "/api/conversations/$ACID/git/diff" --query path="$AREPO/qa-f15-agent.txt" --expect 200 \
    --check original eq '' --check modified eq hi --save F15.agent-changes/diff
  ```
  The file editor's observation names the file; `changes` adds
  `ADDED qa-f15-agent.txt` after the fixture's two entries, and its diff is
  `""` against `hi`.
- **Token lookup (`F15.repo-search-token`).** No token, then each of two
  accepted secret names. A token's presence is observed without the network:
  `page_id` is only parsed once a token was found.
  ```sh
  control-agent-server api GET /api/git/repositories/search --query provider=github --expect 200 \
    --check items len-eq 0 --check next_page_id eq null --check missing_token eq true --save F15.repo-search-token/none
  control-agent-server api GET /api/git/repositories/search --query provider=github --query page_id=abc --expect 200 \
    --check missing_token eq true
  control-agent-server api PUT /api/settings/secrets --json '{"name": "github_token", "value": "qa-f15-not-a-real-token"}' --expect 200
  control-agent-server api GET /api/git/repositories/search --query provider=github --query page_id=abc --expect 400 \
    --check detail eq 'Invalid repository page_id' --save F15.repo-search-token/github-token
  control-agent-server api GET /api/git/repositories/search --query provider=github --query page_id=1:-1 --expect 400
  control-agent-server api DELETE /api/settings/secrets/github_token --expect 200
  control-agent-server api PUT /api/settings/secrets --json '{"name": "GH_TOKEN", "value": "qa-f15-not-a-real-token"}' --expect 200
  control-agent-server api GET /api/git/repositories/search --query provider=github --query page_id=0 --expect 400 \
    --check detail eq 'Invalid repository page_id' --save F15.repo-search-token/gh-token
  control-agent-server api DELETE /api/settings/secrets/GH_TOKEN --expect 200
  control-agent-server api GET /api/git/repositories/search --query provider=github --query page_id=abc --expect 200 \
    --check missing_token eq true --save F15.repo-search-token/deleted
  ```
  Without a token the answer is `missing_token: true` and a garbage `page_id`
  is ignored; with `github_token` or `GH_TOKEN` stored the same `page_id` is
  400 (no request reaches GitHub); after the delete it is `missing_token`
  again.
- **Provider and parameter validation (`F15.repo-search-errors`).**
  ```sh
  control-agent-server api GET /api/git/repositories/search --query provider=gitlab --expect 400 \
    --check detail eq "Repository discovery is not supported for provider 'gitlab'" --save F15.repo-search-errors/gitlab
  control-agent-server api GET /api/git/repositories/search --query provider=bitbucket --expect 400
  control-agent-server api GET /api/git/repositories/search --query provider=qa-nope --expect 422 --check detail.0.loc contains provider
  control-agent-server api GET /api/git/repositories/search --expect 422 --check detail.0.loc contains provider
  control-agent-server api GET /api/git/repositories/search --query provider=github --query limit=0 --expect 422
  control-agent-server api GET /api/git/repositories/search --query provider=github --query limit=101 --expect 422
  ```
  `gitlab` and `bitbucket` are known providers without discovery (400); an
  unknown or missing provider and `limit` 0 or 101 are 422.
- **GitHub unreachable (`F15.repo-search-unreachable`).** Store a token, then
  restart the server with its HTTPS proxy pointed at a closed port, so the
  call to `api.github.com` fails on any machine; restore afterwards.
  ```sh
  control-agent-server api PUT /api/settings/secrets --json '{"name": "github_token", "value": "qa-f15-not-a-real-token"}' --expect 200
  control-agent-server restart --env HTTPS_PROXY=http://127.0.0.1:9 --env https_proxy=http://127.0.0.1:9 \
    --env NO_PROXY=localhost --env no_proxy=localhost
  control-agent-server api GET /api/git/repositories/search --query provider=github --expect 502 \
    --check detail eq 'Internal Server Error' --check exception eq '502: GitHub repository search failed' \
    --check . not-contains qa-f15-not-a-real-token --save F15.repo-search-unreachable/dead-proxy
  control-agent-server api DELETE /api/settings/secrets/github_token --expect 200
  control-agent-server restart --reset-config
  control-agent-server api GET /api/git/repositories/search --query provider=github --expect 200 --check missing_token eq true
  control-agent-server doctor
  ```
  502 with the server's masked 5xx body
  (`{"detail": "Internal Server Error", "exception": "502: GitHub repository search failed"}`),
  without the token. `restart --reset-config` drops the dead proxy again
  (the run has no launch flags to lose), and the search is back to
  `missing_token` once the secret is deleted.
- **GitHub timeout (`F15.repo-search-timeout`).** A local listener that
  accepts connections and never answers stands in for the HTTPS proxy, so the
  call to GitHub hits the server's own 15 s timeout (the timer is under test,
  so the bullet measures the wait). The listener is plain `python3`, started
  in the background and killed at the end.
  ```sh
  python3 -c 'import socket, time; s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8); print(s.getsockname()[1], flush=True); time.sleep(90)' \
    > "$F/hang-port" 2>/dev/null < /dev/null &
  HANG_PID=$!
  for i in $(seq 50); do test -s "$F/hang-port" && break; sleep 0.1; done
  HANG="http://127.0.0.1:$(cat "$F/hang-port")"
  control-agent-server api PUT /api/settings/secrets --json '{"name": "github_token", "value": "qa-f15-not-a-real-token"}' --expect 200
  control-agent-server restart --env HTTPS_PROXY="$HANG" --env https_proxy="$HANG" --env NO_PROXY=localhost --env no_proxy=localhost
  T0=$SECONDS
  control-agent-server api GET /api/git/repositories/search --query provider=github --expect 504 --expect-max-ms 30000 \
    --check detail eq 'Internal Server Error' --check exception eq '504: GitHub repository search timed out' \
    --check . not-contains qa-f15-not-a-real-token --save F15.repo-search-timeout/silent-proxy
  test $((SECONDS - T0)) -ge 14
  kill "$HANG_PID"
  control-agent-server api DELETE /api/settings/secrets/github_token --expect 200
  control-agent-server restart --reset-config
  control-agent-server doctor
  ```
  504 after about 15 s (between 14 and 30), with the masked 5xx body
  `{"detail": "Internal Server Error", "exception": "504: GitHub repository search timed out"}`
  and no token in it.
- **Live GitHub listing (`F15.repo-search-github`), blocked.** Needs a GitHub
  token with repository read access in `$QA_GITHUB_TOKEN` and egress that
  allows `GET https://api.github.com/user/repos`. On the machine this was
  mapped on, the egress proxy answers 403 for that path whatever the token,
  and the server passed that 403 through as `403 {"detail": "GitHub repository search failed"}`.
  ```sh
  control-agent-server api PUT /api/settings/secrets --json "{\"name\": \"github_token\", \"value\": \"$QA_GITHUB_TOKEN\"}" --expect 200
  NEXT=$(control-agent-server api GET /api/git/repositories/search --query provider=github --query limit=2 --expect 200 \
    --check missing_token eq false --check items len-eq 2 --check items.0.git_provider eq github \
    --check next_page_id eq 1:2 --save F15.repo-search-github/page-1 --field next_page_id)
  control-agent-server api GET /api/git/repositories/search --query provider=github --query limit=2 --query page_id="$NEXT" \
    --expect 200 --check items len-ge 1 --save F15.repo-search-github/page-2
  control-agent-server api GET /api/git/repositories/search --query provider=github --query query=AGENT-SDK --query limit=5 \
    --expect 200 --check items.0.full_name matches '(?i)agent-sdk'
  control-agent-server api PUT /api/settings/secrets --json '{"name": "github_token", "value": "qa-f15-not-a-real-token"}' --expect 200
  control-agent-server api GET /api/git/repositories/search --query provider=github --expect 401 \
    --check detail eq 'GitHub repository search failed' --save F15.repo-search-github/bad-token
  control-agent-server api DELETE /api/settings/secrets/github_token --expect 200
  ```
  Two repositories per page with a `1:2` cursor (page 1, offset 2) that
  continues the listing, a case-insensitive `query` match on `full_name`, and
  GitHub's 401 for a bad token passed through (403 also passes through; other
  4xx become 400, 5xx 502, a timeout 504).

## Gotchas

- `changes` and `diff` without `ref` use the *display* base policy
  (`get_valid_ref(..., purpose="display")`), which differs from the *export*
  policy of `GET /api/file/archive` (`git-delta`): on a remote-less fixture
  the Changes tab compares against `HEAD` while the archive patch is against
  the empty tree. Do not expect them to agree.
- A repository with an `origin` but no `origin/HEAD` makes base detection run
  `git remote show origin`, a network call with a 30 s timeout. Use
  remote-less fixtures or a local bare remote cloned with `git clone` (which
  records `origin/HEAD`), as `F15.changes-base` does.
- A non-repository `path` is not an error: `changes` is `[]`, commits are
  empty and `diff` is `{"original": null, "modified": null}` (null, not `""`).
  A missing file inside a repository is 400, and so is a file over 1 MiB.
- "Not a repository" means no ancestor holds a `.git`: git searches upward
  and `diff` walks up in Python. A stray `.git` above the runs home (for
  example a `git init` that ran in `/tmp`) turns every fixture into part of
  that repository: `F15.non-repo` then lists `x.txt` as `ADDED` and
  `F15.changes-repo-below-path` passes for the wrong reason. The
  Preconditions block refuses such a run; launch with
  `AGENT_SERVER_VERIFY_HOME` outside the enclosing repository (for example
  under `/var/tmp`) or have its owner remove the stray `.git`.
- A relative `path` on the global routes resolves against the server
  process's working directory (the run directory for `launch`); always send
  absolute paths. The SDK joins relative paths to `working_dir` before
  calling.
- Both diff sides drop the final newline and line endings are normalized
  (`\r\n` becomes `\n`, so a file whose only change is LF to CRLF is listed as
  `UPDATED` but diffs to identical sides). Leading and trailing whitespace is
  lost on `original` only (`F15.diff-whitespace`).
- `diff` on a directory inside a repository is not refused: it returns
  `git show`'s tree listing (`"tree <sha>:src\n\napp.py\nutil.py"`) as
  `original` and `""` as `modified`.
- Nested repositories are only scanned one level below a `path` that is
  itself a repository (`./*/.git`); deeper ones show up as an untracked
  directory entry.
- `commits/{sha}/changes` keeps git's output order (renames as
  `DELETED`+`ADDED` pairs), unlike `changes`, which is sorted by path.
- Git calls reset `idle_time` (`update_last_execution_time`), like file
  operations; see `F01.idle-time`.
- Repository search is not a local directory scan: it needs a GitHub token in
  the server's secrets store (encrypted with `OH_SECRET_KEY`) and calls
  `https://api.github.com/user/repos` through the server's own proxy
  environment. A garbage `page_id` is only rejected once a token exists.
  Statuses from GitHub: 401 and 403 pass through, other 4xx are 400, 5xx and
  transport errors 502 (masked as `Internal Server Error` with the reason in
  `exception`), a timeout 504.
- `restart --env` values persist for later restarts of the run; clear them
  with `restart --reset-config` as `F15.repo-search-unreachable` does (that
  also drops `Launch:` flags, which this family has none of).
