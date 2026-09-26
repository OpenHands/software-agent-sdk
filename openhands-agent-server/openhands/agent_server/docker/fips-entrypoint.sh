#!/bin/sh
set -eu

providers=$(openssl list -providers)
printf '%s\n' "$providers" | grep -q 'OpenSSL FIPS Provider'
printf '%s\n' "$providers" | grep -q 'OpenSSL Base Provider'

python -c 'import ctypes; lib = ctypes.CDLL("libcrypto.so.3"); lib.EVP_default_properties_is_fips_enabled.restype = ctypes.c_int; raise SystemExit(0 if lib.EVP_default_properties_is_fips_enabled(None) == 1 else 1)'
node -e 'const c = require("crypto"); if (c.getFips() !== 1) process.exit(1); c.createHash("sha256").update("ok").digest()'

exec "$@"
