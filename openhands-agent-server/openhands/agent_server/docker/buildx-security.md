# Buildx security pin and applicability evidence

For OSS-13505 and OSS-13673, pin the official Buildx v0.39.0 executable
in place of the apt-provided plugin. This fixes the embedded `golang.org/x/net`
CVE-2026-78669 (`v0.59.0` → `v0.62.0`, fixed floor `v0.60.0`). Do not
change a disconnected Go manifest or replace the Docker Engine with Engine 29.

## Two module-level findings do not apply to this executable

These conclusions apply only to the checksum-pinned Linux amd64/arm64 Buildx
v0.39.0 assets, not Docker Engine, Docker CLI, BuildKit daemon, other binaries,
or future Buildx versions. They are evidence for a scoped VEX assessment, not
an ignore rule or a claim that the modules themselves are patched. No Trivy
suppression or gate change is introduced.

| Finding | Upstream vulnerable code | Code included by Buildx | Assessment |
| --- | --- | --- | --- |
| [CVE-2026-33997](https://github.com/moby/moby/security/advisories/GHSA-pxq6-2prw-chj9) | Daemon plugin privilege validation; [fix](https://github.com/moby/moby/commit/f4d6f25bf0c3fa12d4968320a45685947756a22a) changes `daemon/pkg/plugin/manager.go` | `github.com/docker/docker/pkg/namesgenerator` only | Vulnerable code not present; random builder names do not install or authorize daemon plugins |
| [CVE-2026-17106](https://github.com/moby/go-archive/security/advisories/GHSA-hfg8-hc9c-6c3h) | Root archive package `Unpack`, `UnpackLayer`, `Untar`, `UntarUncompressed`, `ApplyLayer` and helpers write outside destination | `github.com/moby/go-archive/compression` only | Vulnerable code not present; decompression is not tar filesystem extraction |

### Source evidence

In the [v0.39.0 vendored package inventory](https://github.com/docker/buildx/blob/v0.39.0/vendor/modules.txt),
the complete package sets from these modules are `pkg/namesgenerator` and
`compression`, respectively. The version strings remain Docker
`v28.5.2+incompatible` and go-archive `v0.2.0`.

- [`store/util.go`](https://github.com/docker/buildx/blob/v0.39.0/store/util.go)
  calls `namesgenerator.GetRandomName` to name builders.
- The only go-archive import in the vendored source is
  [`frontend/dockerui/context.go`](https://github.com/docker/buildx/blob/v0.39.0/vendor/github.com/moby/buildkit/frontend/dockerui/context.go).
  `archiveMaxTimeFromHTTPArchive` calls `compression.DecompressStream`, then
  `archive/tar.NewReader` to read timestamps; it does not extract files.
- The [go-archive v0.2.0...v0.3.0 diff](https://github.com/moby/go-archive/compare/v0.2.0...v0.3.0)
  changes extraction code, while the compression file changes only
  `interface{}` to `any` and adds a subprocess lint annotation.

### Actual binary evidence (2026-11-17)

Download assets and `checksums.txt` from the
[official release](https://github.com/docker/buildx/releases/tag/v0.39.0).
The Dockerfile pins these verified SHA256 values:

- amd64: `5fb31a097304fbf6dd4038649a0decf32c2b9510e4047acd7d68b9d700c46194`
- arm64: `722cdcdfdd6f3fb943fa1ba75102d057feb18ba72aab93b1b0a8cf2b2b48a25f`

`go version -m <binary>` confirms the three embedded module versions above.
Parsing ELF `.gopclntab` with Go `debug/elf` and `debug/gosym` enumerated
69,133 amd64 and 69,047 arm64 linked functions. Filtering all function names
for `github.com/docker/docker/` or `github.com/moby/go-archive` returned only:

```text
github.com/docker/docker/pkg/namesgenerator.GetRandomName
```

No daemon plugin validator or go-archive extractor is linked. Source package
inventory and import inspection support the conclusion independently of
linker dead-code elimination. The v0.38.2 amd64 binary that embeds x/net
v0.59.0 likewise had only `namesgenerator.GetRandomName` from these modules.

## Verification and limits

- `python tests/agent_server/docker_runtime/test_buildx_install.py` exercises
  the actual Dockerfile shell with only network and architecture boundaries
  stubbed: both architecture URLs, checksum rejection, in-place executable
  replacement, Docker capability exclusion, and unsupported architecture.
- The exact install shell was also run as root in an isolated existing
  `ghcr.io/openhands/agent-server:939b0da-python-amd64` container. Real download,
  checksum, installation and `docker buildx version` changed v0.38.1 to v0.39.0;
  a `/usr` search confirmed exactly one `docker-buildx` executable remained.
- Arm64 asset checksums/build metadata/function tables were verified; arm64
  execution and a complete final-image rebuild/rescan were not performed.
- The two module-level Trivy findings may remain until scoped VEX is accepted
  by the owning scan process. This PR does not silently make that decision.
- As checked on 2026-11-17, the latest official Docker v28 module remains
  v28.5.2. go-archive v0.3.0 exists and is Go 1.25 compatible, but rebuilding
  Buildx solely to update an unused extractor is unnecessary. No custom fork
  or source rebuild is introduced.
