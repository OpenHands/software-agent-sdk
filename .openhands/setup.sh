#!/usr/bin/env bash

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.." || exit $?

needs_install=0

if [ ! -d node_modules ]; then
  needs_install=1
elif [ ! -f node_modules/.package-lock.json ]; then
  needs_install=1
elif [ package-lock.json -nt node_modules/.package-lock.json ]; then
  needs_install=1
fi

if [ "$needs_install" -eq 1 ]; then
  npm ci || exit $?
fi
