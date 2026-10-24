// Browser scenario adapted from the public reproduction in OpenHands/OpenHands#18603.
// Run from the repository root after building clients/typescript (see README.md).
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { once } from "node:events";
import {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  utimes,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const mode = process.argv[2];
assert(
  ["base", "fixed"].includes(mode),
  "Usage: node .pr/download-cache/reproduce.mjs base|fixed",
);
const require = createRequire(
  join(root, ".agent_tmp/cache-validation-tools/package.json"),
);
const { chromium } = require("playwright");
const { build } = require("esbuild");
const evidence = join(root, ".pr/download-cache");
const bundle = await build({
  stdin: {
    contents:
      'export { FileClient } from "./clients/typescript/dist/clients.js"; export { RemoteWorkspace } from "./clients/typescript/dist/workspace/remote-workspace.js";',
    resolveDir: root,
  },
  bundle: true,
  write: false,
  format: "iife",
  globalName: "sdk",
  platform: "browser",
  external: ["node:*"],
});
const scratch = await mkdtemp(join(tmpdir(), "openhands-download-cache-"));
const key = randomUUID();
const portServer = createServer();
portServer.listen(0, "127.0.0.1");
await once(portServer, "listening");
const port = portServer.address().port;
await new Promise((resolve) => portServer.close(resolve));
const base = `http://127.0.0.1:${port}`;
const workspace = join(scratch, "workspace");
await mkdir(workspace);
const configPath = join(scratch, "server-config.json");
await writeFile(
  configPath,
  JSON.stringify({
    session_api_keys: [key],
    workspace_path: workspace,
    conversations_path: join(scratch, "conversations"),
    bash_events_dir: join(scratch, "bash-events"),
    conversation_worktree_root: join(scratch, "worktrees"),
    enable_vscode: false,
    enable_browser: false,
    preload_tools: false,
  }),
  { mode: 0o600 },
);
const server = spawn(
  join(root, ".venv/bin/python"),
  [
    "-m",
    "openhands.agent_server",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: scratch,
    env: {
      PATH: process.env.PATH,
      LANG: "en_US.UTF-8",
      OH_PERSISTENCE_DIR: join(scratch, "profile-store"),
      OPENHANDS_AGENT_SERVER_CONFIG_PATH: configPath,
      OPENHANDS_SUPPRESS_BANNER: "1",
      DO_NOT_TRACK: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let serverLog = "";
server.stdout.on("data", (chunk) => {
  serverLog = (serverLog + chunk).slice(-16000);
});
server.stderr.on("data", (chunk) => {
  serverLog = (serverLog + chunk).slice(-16000);
});
let browser;
try {
  let healthy = false;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (server.exitCode !== null || server.signalCode !== null)
      throw new Error(
        `Server exited ${server.exitCode ?? server.signalCode}: ${serverLog.replaceAll(key, "[test-key]")}`,
      );
    try {
      const response = await fetch(`${base}/health`, {
        signal: AbortSignal.timeout(500),
      });
      if (response.ok) {
        healthy = true;
        break;
      }
    } catch {
      /* The process is still starting. */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  assert(
    healthy,
    `Server startup timed out: ${serverLog.replaceAll(key, "[test-key]")}`,
  );
  const infoResponse = await fetch(`${base}/server_info`, {
    headers: { "X-Session-API-Key": key },
  });
  const info = await infoResponse.json();
  const create = await fetch(`${base}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Session-API-Key": key },
    body: JSON.stringify({
      workspace: { working_dir: workspace },
      agent: {
        kind: "Agent",
        tools: [],
        llm: {
          model: "openai/test",
          api_key: "unused-test-key",
          usage_id: "cache-probe",
        },
      },
    }),
  });
  assert.equal(create.status, 201, await create.clone().text());
  const conversationId = (await create.json()).id;
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1300, height: 900 },
  });
  const page = await context.newPage();
  await page.goto(`${base}/health`);
  await page.setContent(
    '<html><head><title>OpenHands SDK download cache validation</title><style>body{font:18px system-ui;margin:36px;color:#182433}h1{font-size:28px}pre{font-size:15px;white-space:pre-wrap;background:#f3f6f9;padding:20px;border-radius:8px}</style></head><body><h1>OpenHands SDK — real browser download validation</h1><p>Diagnostic runner, not the Agent Canvas UI. No model calls or mocked responses.</p><pre id="result">Running…</pre></body></html>',
  );
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  const network = [];
  const cacheIds = new Set();
  cdp.on("Network.requestServedFromCache", (event) =>
    cacheIds.add(event.requestId),
  );
  cdp.on("Network.responseReceived", (event) => {
    if (!event.response.url.includes("/file/download")) return;
    network.push({
      requestId: event.requestId,
      status: event.response.status,
      fromDiskCache: event.response.fromDiskCache ?? false,
      fromServiceWorker: event.response.fromServiceWorker ?? false,
      headers: Object.fromEntries(
        Object.entries(event.response.headers).filter(([name]) =>
          ["cache-control", "last-modified", "etag", "content-type"].includes(
            name.toLowerCase(),
          ),
        ),
      ),
    });
  });
  const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
  const results = [];
  const cases = ["host", "conversation"].flatMap((scope) =>
    ["FileClient.downloadFile", "RemoteWorkspace.downloadAsText"].map(
      (surface) => ({ scope, surface }),
    ),
  );
  for (const { scope, surface } of cases) {
    const isBinary = surface.startsWith("FileClient");
    const path = join(
      workspace,
      `${scope}-${isBinary ? "artifact.png" : "artifact.txt"}`,
    );
    const before = isBinary
      ? Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=",
          "base64",
        )
      : Buffer.from("Artifact version A\n");
    const after = isBinary
      ? Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
          "base64",
        )
      : Buffer.from("Artifact version B\n");
    await writeFile(path, before);
    // An old, valid mtime makes Chromium's Last-Modified heuristic cache deterministic.
    await utimes(
      path,
      new Date("2020-01-01T00:00:00Z"),
      new Date("2020-01-01T00:00:00Z"),
    );
    const download = (apiKey) =>
      page.evaluate(
        async ({ base, apiKey, path, isBinary, workspace, conversationId }) => {
          const options = { host: base, apiKey, conversationId };
          const client = isBinary
            ? new window.sdk.FileClient(options)
            : new window.sdk.RemoteWorkspace({
                ...options,
                workingDir: workspace,
              });
          try {
            const bytes = isBinary
              ? new Uint8Array(await client.downloadFile(path))
              : new TextEncoder().encode(await client.downloadAsText(path));
            return { ok: true, bytes: [...bytes] };
          } catch (error) {
            return { ok: false, status: error.status };
          } finally {
            client.close();
          }
        },
        {
          base,
          apiKey,
          path,
          isBinary,
          workspace,
          conversationId: scope === "conversation" ? conversationId : undefined,
        },
      );
    const first = await download(key);
    assert(first.ok);
    assert.deepEqual(Buffer.from(first.bytes), before);
    await writeFile(path, after);
    await utimes(path, new Date(), new Date());
    const second = await download(key);
    assert(second.ok);
    const invalid = await download("invalid-owned-test-key");
    const direct = await fetch(
      `${base}/api/file/download?path=${encodeURIComponent(path)}`,
      { headers: { "X-Session-API-Key": key } },
    );
    const truth = Buffer.from(await direct.arrayBuffer());
    assert.deepEqual(truth, after);
    const invalidControl = await fetch(
      `${base}/api/file/download?path=${encodeURIComponent(path)}`,
      { headers: { "X-Session-API-Key": "invalid-owned-test-key" } },
    );
    assert.equal(invalidControl.status, 401);
    const fresh = Buffer.from(second.bytes).equals(after);
    assert.equal(
      fresh,
      mode === "fixed",
      `${surface}: unexpected second-read freshness`,
    );
    if (mode === "fixed")
      assert.equal(
        invalid.status,
        401,
        `${scope} ${surface}: invalid-key browser result`,
      );
    results.push({
      scope,
      surface,
      firstHash: hash(Buffer.from(first.bytes)),
      secondHash: hash(Buffer.from(second.bytes)),
      expectedNewHash: hash(after),
      secondReadIsFresh: fresh,
      invalidKeyBrowserStatus: invalid.status ?? 200,
      invalidKeyNetworkControlStatus: invalidControl.status,
    });
  }
  const result = {
    mode,
    sourceCommit: execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
    }).trim(),
    browserBundleSha256: hash(bundle.outputFiles[0].contents),
    clientVersion: JSON.parse(
      await readFile(join(root, "clients/typescript/package.json"), "utf8"),
    ).version,
    agentServerVersion: info.version,
    browser: browser.version(),
    platform: `${process.platform}/${process.arch}`,
    timestamp: new Date().toISOString(),
    results,
    network: network.map((entry) => ({
      ...entry,
      servedFromCache: cacheIds.has(entry.requestId),
    })),
    validation:
      "Fresh browser context, cache enabled; real source Agent Server; no route interception, fetch mocks, cache disabling, or LLM calls. Only owned fixtures use an old initial mtime. Server authentication remains enforced.",
  };
  await writeFile(
    join(evidence, `${mode}.json`),
    JSON.stringify(result, null, 2) + "\n",
  );
  await page.evaluate(
    (text) => {
      document.querySelector("#result").textContent = text;
    },
    JSON.stringify(
      {
        mode,
        sourceCommit: result.sourceCommit,
        browser: result.browser,
        clientVersion: result.clientVersion,
        agentServerVersion: result.agentServerVersion,
        results: results.map(
          ({ scope, surface, secondReadIsFresh, invalidKeyBrowserStatus }) => ({
            scope,
            surface,
            secondReadIsFresh,
            invalidKeyBrowserStatus,
          }),
        ),
        cachedResponses: result.network.filter(
          (entry) => entry.fromDiskCache || entry.servedFromCache,
        ).length,
      },
      null,
      2,
    ),
  );
  await page.screenshot({
    path: join(evidence, `${mode}.png`),
    fullPage: true,
  });
  console.log(
    JSON.stringify(
      {
        mode,
        browser: result.browser,
        results,
        cachedResponses: result.network.filter(
          (entry) => entry.fromDiskCache || entry.servedFromCache,
        ).length,
      },
      null,
      2,
    ),
  );
} finally {
  if (browser) await browser.close();
  if (server.exitCode === null && server.signalCode === null) {
    server.kill("SIGTERM");
    await Promise.race([
      once(server, "exit"),
      new Promise((resolve) => setTimeout(resolve, 5000)),
    ]);
    if (server.exitCode === null && server.signalCode === null) {
      server.kill("SIGKILL");
      await once(server, "exit");
    }
  }
  await rm(scratch, { recursive: true, force: true });
}
