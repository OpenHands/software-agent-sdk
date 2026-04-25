#!/usr/bin/env bash

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.." || exit $?

npm run lint || exit $?
npm run build || exit $?
npm run test:coverage || exit $?
npm run format:check || exit $?
