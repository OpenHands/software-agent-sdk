# Proposed new F15 bullet: F15.pr-status (PR #6704, commit 5c03bde)

Target file: .agents/skills/verify-agent-server/references/feature-map/F15-git.md
The recipe below passed live (RECIPE_EXIT 0, fresh run, fake GitHub on a loopback CONNECT proxy).
`control-agent-server map coverage` currently reports this route as unowned, so the edits are needed.

## Edit 1 - intro paragraph (end, after "(`missing_token` when there is none).")
Append: " The same token also reads one pull request's state and combined CI result (`pull-requests/{number}`)."

## Edit 2 - the `Source:` line already lists git_router.py and git_provider_service.py; no change. Add `openhands-sdk/openhands/sdk/git/` is already there.

## Edit 3 - the `Routes:` line: append the route
    `GET /api/git/repositories/{owner}/{repo}/pull-requests/{number}`
(after `GET /api/git/repositories/search`, which sits inside the list; keep the comma-separated format).

## Edit 4 - Sub-features: insert this line right after the `F15.repo-search-rejected-token` line (end of the Sub-features list)

- `F15.pr-status`: `GET /api/git/repositories/{owner}/{repo}/pull-requests/{number}?provider=github` reads one pull request live from GitHub with the stored token and returns `git_provider`, `repository`, `number`, `state` (`open`, `closed` or `merged`), `draft`, `title`, `url`, `head_sha` and `checks` (`success`, `failure`, `pending` or `none`: any failed check run or errored status wins, an unfinished run or pending status is `pending`, no runs and no statuses is `none`); without a token it is 409 `{code: missing_provider_token}`, a provider other than `github` is 400, GitHub's 404 stays 404, its rate limit is 429 with `Retry-After`, a rejected token or other provider failure is 502 with the masked body, and invalid `owner`, `repo` (including `.`/`..`), `number` or `provider` are 422 without any provider request.

## Edit 5 - Driving section: insert the following block right after the `F15.repo-search-rejected-token` recipe (its closing paragraph ends "...connection failure and timeout are covered above."), before "## Gotchas"

- **Pull request status (`F15.pr-status`).** A local TLS fixture stands in for
  `api.github.com` behind an owned CONNECT proxy, as in the rejected-token
  recipe, but serves several pull requests: `1` open with passing checks, `2`
  merged with a timed-out check run, `3` an open draft with a run in progress,
  `4` closed with no checks, `5` open with only a pending commit status, `6`
  with an errored commit status; `401`, `403` (rate limited, `Retry-After: 42`)
  and `500` are provider failures and any other number is GitHub's 404. It
  records every request path and `Authorization` header it receives. Only a
  fake token is used and the real GitHub is never contacted.
  `LITELLM_LOCAL_MODEL_COST_MAP=True` keeps the restarted server from fetching
  litellm's price map through the fixture (the 502 it would get can break the
  server's startup, see Gotchas). The run must have no `github_token` or
  `GH_TOKEN` secret stored when the recipe starts, so the first call proves
  `missing_provider_token`.
  ```sh
  cat > "$F/pr-github.py" <<'PY'
  import datetime, json, pathlib, re, socketserver, ssl, sys
  from cryptography import x509
  from cryptography.hazmat.primitives import hashes, serialization
  from cryptography.hazmat.primitives.asymmetric import rsa
  from cryptography.x509.oid import NameOID
  root = pathlib.Path(sys.argv[1])
  key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'api.github.com')])
  now = datetime.datetime.now(datetime.timezone.utc)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
      .public_key(key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(now - datetime.timedelta(minutes=1))
      .not_valid_after(now + datetime.timedelta(days=1))
      .add_extension(x509.SubjectAlternativeName([x509.DNSName('api.github.com')]), critical=False)
      .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
      .sign(key, hashes.SHA256()))
  (root / 'pr-cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
  (root / 'pr-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
      serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
  context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
  context.load_cert_chain(root / 'pr-cert.pem', root / 'pr-key.pem')
  # number -> (pull fields, check runs, commit statuses)
  def sha(n): return '%040x' % n
  PRS = {
      1: ({'state': 'open', 'title': 'Add widget'}, [{'status': 'completed', 'conclusion': 'success'}], [{'state': 'success'}]),
      2: ({'state': 'closed', 'merged': True, 'title': 'Fix widget'}, [{'status': 'completed', 'conclusion': 'success'}, {'status': 'completed', 'conclusion': 'timed_out'}], []),
      3: ({'state': 'open', 'draft': True, 'title': 'WIP widget'}, [{'status': 'in_progress', 'conclusion': None}], []),
      4: ({'state': 'closed', 'title': 'Dropped widget'}, [], []),
      5: ({'state': 'open', 'title': 'Legacy status only'}, [], [{'state': 'pending'}]),
      6: ({'state': 'open', 'title': 'Status error'}, [{'status': 'completed', 'conclusion': 'success'}], [{'state': 'error'}]),
  }
  def reply(conn, status, body, extra=''):
      data = json.dumps(body).encode()
      conn.sendall(f'HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n{extra}\r\n'.encode() + data)
  def read_headers(conn):
      value = b''
      while b'\r\n\r\n' not in value:
          chunk = conn.recv(4096)
          if not chunk: return None
          value += chunk
      return value.decode('latin1')
  seen = {'requests': [], 'authorization': []}
  def log(line):
      seen['requests'].append(line['path'])
      if line['authorization'] not in seen['authorization']: seen['authorization'].append(line['authorization'])
      (root / 'pr-observed.json').write_text(json.dumps(seen))
  def route(conn, path, auth):
      log({'path': path, 'authorization': auth})
      m = re.match(r'^/repos/acme/widgets/pulls/(\d+)$', path)
      if m:
          n = int(m.group(1))
          if n == 401: return reply(conn, 401, {'message': 'Bad credentials'})
          if n == 403: return reply(conn, 403, {'message': 'API rate limit exceeded'}, 'x-ratelimit-remaining: 0\r\nretry-after: 42\r\n')
          if n == 500: return reply(conn, 500, {'message': 'boom'})
          if n not in PRS: return reply(conn, 404, {'message': 'Not Found'})
          f = PRS[n][0]
          return reply(conn, 200, {'number': n, 'state': f['state'], 'merged': f.get('merged', False), 'draft': f.get('draft', False),
              'title': f['title'], 'html_url': f'https://github.com/acme/widgets/pull/{n}', 'head': {'sha': sha(n)}})
      m = re.match(r'^/repos/acme/widgets/commits/([0-9a-f]{40})/check-runs', path)
      if m:
          runs = PRS[int(m.group(1), 16)][1]
          return reply(conn, 200, {'total_count': len(runs), 'check_runs': runs})
      m = re.match(r'^/repos/acme/widgets/commits/([0-9a-f]{40})/status', path)
      if m:
          return reply(conn, 200, {'statuses': PRS[int(m.group(1), 16)][2]})
      reply(conn, 404, {'message': 'Not Found'})
  class Handler(socketserver.BaseRequestHandler):
      def handle(self):
          head = read_headers(self.request)
          if not head or not head.startswith('CONNECT api.github.com:443 '):
              self.request.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'); return
          self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
          with context.wrap_socket(self.request, server_side=True) as conn:
              while True:
                  req = read_headers(conn)
                  if req is None: return
                  lines = req.split('\r\n')
                  auth = next((l.split(':', 1)[1].strip() for l in lines if l.lower().startswith('authorization:')), '')
                  route(conn, lines[0].split(' ')[1], auth)
  with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
      server.daemon_threads = True
      (root / 'pr-observed.json').write_text(json.dumps(seen))
      (root / 'pr-port').write_text(str(server.server_address[1]))
      server.serve_forever()
  PY
  rm -f "$F/pr-port" "$F/pr-observed.json"
  uv run --quiet python "$F/pr-github.py" "$F" > "$F/pr-log" 2>&1 &
  PR_PID=$!
  trap 'kill "$PR_PID" 2>/dev/null || true; control-agent-server api DELETE /api/settings/secrets/github_token >/dev/null 2>&1 || true; control-agent-server restart --reset-config >/dev/null 2>&1 || true' EXIT
  for i in $(seq 1 100); do test -s "$F/pr-port" && break; sleep 0.1; done
  PRS=/api/git/repositories/acme/widgets/pull-requests
  control-agent-server api GET "$PRS/1" --query provider=github --expect 409 \
    --check detail.code eq missing_provider_token --save F15.pr-status/no-token
  control-agent-server api PUT /api/settings/secrets --json '{"name":"github_token","value":"qa-f15-pr-token"}' --expect 200
  PR_PROXY="http://127.0.0.1:$(cat "$F/pr-port")"
  control-agent-server restart --env HTTPS_PROXY="$PR_PROXY" --env https_proxy="$PR_PROXY" \
    --env HTTP_PROXY="$PR_PROXY" --env http_proxy="$PR_PROXY" \
    --env "SSL_CERT_FILE=$F/pr-cert.pem" --env LITELLM_LOCAL_MODEL_COST_MAP=True --env NO_PROXY=localhost,127.0.0.1 --env no_proxy=localhost,127.0.0.1
  control-agent-server doctor
  control-agent-server api GET "$PRS/1" --query provider=github --expect 200 \
    --check state eq open --check draft eq false --check checks eq success --check title eq 'Add widget' \
    --check url eq https://github.com/acme/widgets/pull/1 --check repository eq acme/widgets --check git_provider eq github \
    --check number eq 1 --check head_sha eq 0000000000000000000000000000000000000001 --save F15.pr-status/open-success
  control-agent-server api GET "$PRS/2" --query provider=github --expect 200 \
    --check state eq merged --check checks eq failure --save F15.pr-status/merged-failure
  control-agent-server api GET "$PRS/3" --query provider=github --expect 200 \
    --check state eq open --check draft eq true --check checks eq pending --save F15.pr-status/draft-pending
  control-agent-server api GET "$PRS/4" --query provider=github --expect 200 \
    --check state eq closed --check checks eq none --save F15.pr-status/closed-none
  control-agent-server api GET "$PRS/5" --query provider=github --expect 200 --check checks eq pending
  control-agent-server api GET "$PRS/6" --query provider=github --expect 200 --check checks eq failure
  control-agent-server api GET "$PRS/99" --query provider=github --expect 404 \
    --check detail eq 'GitHub did not find it' --save F15.pr-status/not-found
  control-agent-server api GET "$PRS/403" --query provider=github --expect 429 \
    --check detail eq 'GitHub rate limit reached' --check-header retry-after eq 42 --save F15.pr-status/rate-limited
  control-agent-server api GET "$PRS/401" --query provider=github --expect 502 \
    --check exception contains 'rejected the configured token' --check . not-contains qa-f15-pr-token --save F15.pr-status/token-rejected
  control-agent-server api GET "$PRS/500" --query provider=github --expect 502 --check . not-contains qa-f15-pr-token
  control-agent-server api GET "$PRS/1" --query provider=gitlab --expect 400 \
    --check detail eq "Pull requests are not supported for provider 'gitlab'" --save F15.pr-status/gitlab
  control-agent-server api GET "$PRS/1" --expect 422 --check detail.0.loc contains provider
  control-agent-server api GET "$PRS/1" --query provider=qa-nope --expect 422 --check detail.0.loc contains provider
  control-agent-server api GET "$PRS/0" --query provider=github --expect 422 --check detail.0.loc contains number
  control-agent-server api GET "$PRS/abc" --query provider=github --expect 422 --check detail.0.loc contains number
  control-agent-server api GET /api/git/repositories/%2e%2e/widgets/pull-requests/1 --query provider=github --expect 422 \
    --check detail contains "must not be" --save F15.pr-status/dot-segment
  control-agent-server api GET /api/git/repositories/ac%20me/widgets/pull-requests/1 --query provider=github --expect 422
  control-agent-server api GET "$PRS/1" --query provider=github --auth none --expect 401
  control-agent-server assert --json "$F/pr-observed.json" \
    --check requests contains /repos/acme/widgets/pulls/1 \
    --check requests contains /repos/acme/widgets/commits/0000000000000000000000000000000000000001/status \
    --check requests contains 'check-runs?per_page=100&page=1' \
    --check requests not-contains /pulls/0 --check requests not-contains '..' --check requests not-contains gitlab \
    --check authorization len-eq 1 --check authorization contains 'Bearer qa-f15-pr-token'
  cp "$F/pr-observed.json" "$AGENT_SERVER_VERIFY_RUN/evidence/F15.pr-status/provider.json"
  control-agent-server api DELETE /api/settings/secrets/github_token --expect 200
  kill "$PR_PID"
  wait "$PR_PID" || true
  control-agent-server restart --reset-config
  control-agent-server doctor
  trap - EXIT
  ```
  Each pull request's `state`, `draft` and `checks` follow the fixture's rows
  above; the merged pull request is `merged`, not `closed`, even though
  GitHub reports `state: closed` for it. The fixture's record shows one
  `GET /repos/acme/widgets/pulls/N`, one `commits/<head>/check-runs` and one
  `commits/<head>/status` per pull request, only the fake `Bearer` token, and
  no request for the rejected inputs (`pulls/0`, the `..` owner, `gitlab`).
  Provider failures are 404, 429 (with `Retry-After: 42`) or the masked 502
  (`detail: Internal Server Error`, reason in `exception`); a provider 401 is
  502, not 401, because a 401 would read as the caller's own session key
  being wrong. There is no conversation-scoped variant of this route.

## Edit 6 - Gotchas: append this item at the end of the list

- Pull request status (`F15.pr-status`) is GitHub-only: other known providers
  are 400. It calls three GitHub endpoints (pull, check runs with up to 5
  pages of 100, commit status); more than 500 check runs without a failure is
  reported as `pending`, never `success`. Unlike repository search it does not
  pass a provider 401 or 403 through (502), keeps 404, and turns a rate limit
  (429, or 403 with `x-ratelimit-remaining: 0`) into 429 with the provider's
  `Retry-After`. A fixture that proxies `api.github.com` makes the restarted
  server's litellm price-map fetch fail through the same proxy; without
  `LITELLM_LOCAL_MODEL_COST_MAP=True` that can make the server exit during
  startup ("cannot import name 'LoggerContext' ... circular import").

## Edit 7 - feature-map/README.md counts (map check --fix-counts does this)
F15 index row: 29 -> 30 sub-features; total 953 -> 954.

## Edit 8 - fix three drifted existing F15 bullets (separate from the new bullet; see report)
Lines 58, 706, 714, 733, 742 of F15-git.md: replace `GitHub repository search failed` with `GitHub request failed` in the 502 exception strings and
`GitHub repository search timed out` with `GitHub request timed out` in the 504 strings; line 835: `--check detail eq 'GitHub request failed'`.
