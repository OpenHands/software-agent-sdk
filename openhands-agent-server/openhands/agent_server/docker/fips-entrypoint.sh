#!/bin/sh
set -eu

providers=$(openssl list -providers)
printf '%s\n' "$providers" | grep -q 'OpenSSL FIPS Provider'
printf '%s\n' "$providers" | grep -q 'OpenSSL Base Provider'

python -c 'import ctypes; lib = ctypes.CDLL("libcrypto.so.3"); lib.EVP_default_properties_is_fips_enabled.restype = ctypes.c_int; raise SystemExit(0 if lib.EVP_default_properties_is_fips_enabled(None) == 1 else 1)'
node -e 'const c = require("crypto"); if (c.getFips() !== 1) process.exit(1); c.createHash("sha256").update("ok").digest()'

# The primary Node is a FIPS shim (see Dockerfile.fips); the base image's
# non-FIPS ACP runtime must still start, since it shares no OpenSSL with it.
acp_node="${ACP_NODE_DIR:-/acp-node}/bin/node"
if [ -x "$acp_node" ]; then
    "$acp_node" -e 'process.exit(0)'
fi

# Re-exec through the base image's init. Every other agent-server target ends
# its ENTRYPOINT with `tini` because the server spawns long-lived subprocess
# trees (terminals, tmux, shell/git APIs) that need reaping; execing the server
# directly would leave it as PID 1 and orphan those processes.
exec tini -- "$@"
