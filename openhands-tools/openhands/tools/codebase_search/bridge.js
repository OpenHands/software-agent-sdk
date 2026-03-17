#!/usr/bin/env node
/**
 * Bridge script that wraps @morphllm/morphsdk for use from Python.
 *
 * Reads a single JSON object from stdin, calls the appropriate SDK method,
 * and writes the result as JSON to stdout.
 *
 * Input schema:
 *   { "type": "local"|"github", "query": "...", "repo_path": "...", "owner_repo": "...", "github_url": "...", "branch": "..." }
 *
 * Output schema:
 *   { "success": bool, "contexts": [...], "summary": "...", "error": "..." }
 */

const { WarpGrepClient } = require("@morphllm/morphsdk/tools/warp-grep");

async function main() {
  // Read JSON from stdin
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString());

  const client = new WarpGrepClient({
    morphApiKey: process.env.MORPH_API_KEY,
    morphApiUrl: process.env.MORPH_API_URL || undefined,
    timeout: Number(process.env.MORPH_WARP_GREP_TIMEOUT) || undefined,
  });

  let result;
  if (input.type === "github") {
    const github = input.github_url || input.owner_repo || "";
    result = await client.searchGitHub({
      searchTerm: input.query,
      github,
      branch: input.branch || undefined,
    });
  } else {
    result = await client.execute({
      searchTerm: input.query,
      repoRoot: input.repo_path || ".",
    });
  }

  process.stdout.write(JSON.stringify(result));
}

main().catch((err) => {
  process.stdout.write(
    JSON.stringify({ success: false, error: err.message || String(err) })
  );
  process.exit(0); // exit clean so Python gets the JSON
});
