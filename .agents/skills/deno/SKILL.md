---
name: deno
description: If the project uses deno, use this skill. Use this skill to initialize and work with Deno projects, add/remove dependencies (JSR and npm), run tasks and scripts with appropriate permissions, and use built-in tooling (fmt/lint/test).
triggers:
- deno.json
- deno.jsonc
- deno.lock
- deno task
- jsr:
- npm:
---

# Deno

Use Deno as the default runtime/tooling when the repo contains `deno.json`/`deno.jsonc`, uses `deno.lock`, or scripts/documentation reference `deno task`, `deno run`, `deno test`, etc.

## Quick decision rules

- Prefer `deno task <name>` if the repo defines tasks.
- Use `deno add` / `deno remove` to manage dependencies (writes to config).
- Be explicit about permissions for `deno run` / `deno test`.

## Common operations

### Initialize a new project

```bash
deno init
```

### Add dependencies (JSR and npm)

```bash
# JSR (recommended for Deno-first packages)
deno add jsr:@std/path

# npm packages are supported too
deno add npm:react

# multiple at once
deno add jsr:@std/assert npm:chalk
```

### Remove dependencies

```bash
deno remove jsr:@std/path
```

### Run a script

```bash
# Minimal permissions: only what the program needs
# Examples:
#   --allow-net=api.example.com
#   --allow-read=./data
#   --allow-env=FOO,BAR

deno run --allow-net --allow-read main.ts
```

### Run tasks

```bash
# list tasks
deno task

# run a task defined in deno.json/deno.jsonc
deno task dev
```

### Formatting, linting, testing

```bash
deno fmt
deno lint
deno test

# common permissioned test run
deno test --allow-net --allow-read
```

### Install / run CLIs

```bash
# Run a JSR or npm package's CLI without installing globally
deno x jsr:@std/http/file-server -p 8080

# Install globally (requires choosing permissions at install time)
deno install -g -N -R jsr:@std/http/file-server -- -p 8080
```

## Notes / pitfalls

- Deno is secure-by-default: missing permissions cause runtime errors; add the smallest set of `--allow-*` flags needed.
- Dependency specifiers:
  - `jsr:` for JSR registry packages
  - `npm:` for npm packages
  - URL imports are also supported (and cached)
- Lockfile: `deno.lock` helps ensure reproducible dependency resolution.
