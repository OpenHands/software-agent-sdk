const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => start(JSON.parse(input)));

function start(config) {
  const prefix = config.basePath
    ? "/" + config.basePath.replace(/^\/+|\/+$/g, "")
    : "";
  // Only the authenticated adapter has a TCP listener. Unix permissions keep
  // the auth-none upstream inaccessible to other users on a shared host.
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "openhands-code-server-"),
  );
  fs.chmodSync(directory, 0o700);
  const socketPath = path.join(directory, "editor.sock");
  const digest = crypto
    .createHmac("sha256", config.token)
    .update("editor-session")
    .digest("hex");
  const cookieName =
    "openhands_editor_" +
    crypto
      .createHash("sha256")
      .update(String(config.port) + prefix)
      .digest("hex")
      .slice(0, 16);
  const equal = (a, b) => {
    const left = Buffer.from(a || "");
    const right = Buffer.from(b);
    return left.length === right.length && crypto.timingSafeEqual(left, right);
  };
  const validCookie = (req) =>
    (req.headers.cookie || "").split(";").some((value) => {
      const [name, ...rest] = value.trim().split("=");
      return name === cookieName && equal(rest.join("="), digest);
    });
  const validOrigin = (req) => {
    if (!req.headers.origin) return true;
    try {
      const host = (req.headers["x-forwarded-host"] || req.headers.host || "")
        .split(",")[0]
        .trim();
      return new URL(req.headers.origin).host === host;
    } catch {
      return false;
    }
  };
  const route = (req) => {
    if (!req.url.startsWith("/") || req.url.startsWith("//")) return null;
    let url;
    try {
      url = new URL(req.url, "http://editor.invalid");
    } catch {
      return null;
    }
    if (
      prefix &&
      url.pathname !== prefix &&
      !url.pathname.startsWith(prefix + "/")
    )
      return null;
    return url;
  };
  const upstreamPath = (url) =>
    (url.pathname.slice(prefix.length) || "/") + url.search;
  const upstreamHeaders = (req) => {
    const headers = { ...req.headers };
    delete headers.cookie;
    delete headers.authorization;
    delete headers["x-session-api-key"];
    return headers;
  };
  const server = http.createServer((req, res) => {
    res.setHeader("Referrer-Policy", "no-referrer");
    const url = route(req);
    if (!url) {
      res.writeHead(404).end();
      return;
    }
    if (!validOrigin(req)) {
      res.writeHead(403).end();
      return;
    }
    // Exchange the existing public token URL for a scoped, token-derived cookie.
    // Restarting with a rotated token invalidates every previous cookie.
    if (url.searchParams.has("tkn")) {
      if (!equal(url.searchParams.get("tkn"), config.token)) {
        res.writeHead(401).end();
        return;
      }
      url.searchParams.delete("tkn");
      const secure =
        (req.headers["x-forwarded-proto"] || "").split(",")[0].trim() ===
        "https";
      res.setHeader(
        "Set-Cookie",
        `${cookieName}=${digest}; Path=${prefix || "/"}; HttpOnly; ${secure ? "Secure; SameSite=None" : "SameSite=Lax"}`,
      );
      res.setHeader("Cache-Control", "no-store");
      res
        .writeHead(303, {
          Location:
            (url.pathname === prefix ? prefix + "/" : url.pathname) +
            url.search,
        })
        .end();
      return;
    }
    if (!validCookie(req)) {
      res.writeHead(401).end();
      return;
    }
    if (prefix && url.pathname === prefix) {
      res.writeHead(308, { Location: prefix + "/" + url.search }).end();
      return;
    }
    const upstream = http.request(
      {
        socketPath,
        path: upstreamPath(url),
        method: req.method,
        headers: upstreamHeaders(req),
      },
      (response) => {
        const headers = {
          ...response.headers,
          "referrer-policy": "no-referrer",
        };
        delete headers["set-cookie"];
        res.writeHead(response.statusCode, headers);
        response.pipe(res);
      },
    );
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.on("aborted", () => upstream.destroy());
    res.on("close", () => upstream.destroy());
    req.pipe(upstream);
  });
  const connections = new Set();
  server.on("connection", (socket) => {
    connections.add(socket);
    socket.on("close", () => connections.delete(socket));
  });
  server.on("upgrade", (req, socket, head) => {
    const url = route(req);
    if (!url || !validCookie(req) || !validOrigin(req)) {
      socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      return;
    }
    const upstream = http.request({
      socketPath,
      path: upstreamPath(url),
      headers: upstreamHeaders(req),
    });
    upstream.on("upgrade", (response, peer, upstreamHead) => {
      const lines = [
        `HTTP/1.1 ${response.statusCode} ${response.statusMessage}`,
      ];
      for (let i = 0; i < response.rawHeaders.length; i += 2)
        lines.push(`${response.rawHeaders[i]}: ${response.rawHeaders[i + 1]}`);
      socket.write(lines.join("\r\n") + "\r\n\r\n");
      if (upstreamHead.length) socket.write(upstreamHead);
      if (head.length) peer.write(head);
      socket.pipe(peer).pipe(socket);
      peer.on("error", () => socket.destroy());
      socket.on("error", () => peer.destroy());
      socket.on("close", () => peer.destroy());
      peer.on("close", () => socket.destroy());
    });
    upstream.on("response", (response) => {
      socket.end(
        `HTTP/1.1 ${response.statusCode} Rejected\r\nConnection: close\r\n\r\n`,
      );
      response.resume();
    });
    upstream.on("error", () => socket.destroy());
    socket.on("close", () => upstream.destroy());
    upstream.end();
  });
  const env = { ...process.env };
  delete env.PASSWORD;
  delete env.HASHED_PASSWORD;
  delete env.NODE_OPTIONS;
  const child = spawn(
    path.join(config.root, "bin/code-server"),
    [
      "--config",
      "/dev/null",
      "--auth",
      "none",
      "--socket",
      socketPath,
      "--socket-mode",
      "0600",
      "--session-socket",
      path.join(directory, "session.sock"),
      "--disable-telemetry",
      "--disable-update-check",
      "--disable-proxy",
      "--disable-workspace-trust",
      "--user-data-dir",
      path.join(
        os.homedir(),
        ".local/share/openhands-code-server",
        String(config.port),
      ),
    ],
    { env, stdio: ["ignore", "pipe", "pipe"] },
  );
  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);
  const cleanup = () => {
    clearInterval(timer);
    server.close();
    for (const socket of connections) socket.destroy();
    child.kill();
    fs.rmSync(directory, { recursive: true, force: true });
  };
  let listening = false;
  const timer = setInterval(() => {
    if (!fs.existsSync(socketPath)) return;
    const probe = net.connect(socketPath);
    probe.on("error", () => {});
    probe.on("connect", () => {
      probe.destroy();
      if (listening) return;
      listening = true;
      clearInterval(timer);
      server.listen(config.port, "0.0.0.0", () =>
        console.log("OpenHands editor ready"),
      );
    });
  }, 100);
  server.on("error", (error) => {
    console.error(error.message);
    cleanup();
    process.exit(1);
  });
  child.on("error", (error) => {
    console.error(error.message);
    cleanup();
    process.exit(1);
  });
  child.on("exit", (code) => {
    cleanup();
    process.exit(code || 1);
  });
  process.on("SIGTERM", () => {
    cleanup();
    process.exit(0);
  });
  process.on("SIGINT", () => {
    cleanup();
    process.exit(0);
  });
}
