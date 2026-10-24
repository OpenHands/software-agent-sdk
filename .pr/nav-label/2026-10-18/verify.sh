#!/usr/bin/env bash
# Run from the repository root after make build. No model credentials needed.
set -euo pipefail
export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PATH"
export AGENT_SERVER_VERIFY_RUN="$(control-agent-server launch --new --name nav-label-5521 --print-run)"
trap 'control-agent-server stop' EXIT
control-agent-server doctor
fixture_path=$(control-agent-server fixture canvas-app --name qa-nav-label --print-path)
.venv/bin/python - "$fixture_path" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest_path = root / "canvas-extension.json"
manifest = json.loads(manifest_path.read_text())
manifest["contributes"]["pages"] = [
    {"id": "hello", "title": "Hello from an extension", "path": "/hello",
     "nav_label": "Extension demo"},
    {"id": "legacy", "title": "Legacy page", "path": "/legacy"},
]
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
(root / "install-request.json").write_text(json.dumps({"source": str(root)}))
PY
control-agent-server api POST /api/canvas-extensions/install \
  --json-file "$fixture_path/install-request.json" --expect 200 \
  --check enabled eq false --save F28.install-local/install
control-agent-server api PATCH /api/canvas-extensions/installed/qa-nav-label \
  --json '{"enabled": true}' --expect 200 --check enabled eq true \
  --save F28.enable-disable/enable
control-agent-server api GET /api/canvas-extensions/installed/qa-nav-label \
  --expect 200 --check enabled eq true --check manifest.schema_version eq 1 \
  --check manifest.contributes.pages.0.nav_label eq 'Extension demo' \
  --check manifest.contributes.pages.0.title eq 'Hello from an extension' \
  --check manifest.contributes.pages.1.nav_label missing \
  --check manifest.contributes.pages.1.title eq 'Legacy page' \
  --save F28.install-local/detail
control-agent-server api GET /api/canvas-extensions/installed \
  --expect 200 --check canvas_extensions len-eq 1 \
  --check canvas_extensions.0.enabled eq true \
  --check canvas_extensions.0.manifest.contributes.pages.0.nav_label eq 'Extension demo' \
  --check canvas_extensions.0.manifest.contributes.pages.1.nav_label missing \
  --save F28.install-local/list
control-agent-server restart
control-agent-server doctor
control-agent-server api GET /api/canvas-extensions/installed/qa-nav-label \
  --expect 200 --check enabled eq true --check manifest.schema_version eq 1 \
  --check manifest.contributes.pages.0.nav_label eq 'Extension demo' \
  --check manifest.contributes.pages.1.nav_label missing \
  --save F28.restart-persistence/detail
control-agent-server evidence add --feature F28.install-local --result pass \
  --entry 'REST install, installed list and detail (nav_label scope)' \
  --expected 'Short label preserved, legacy field omitted, title and schema unchanged' \
  --actual 'All assertions passed' --artifact evidence/F28.install-local/detail.json \
  --artifact evidence/F28.install-local/list.json
control-agent-server evidence add --feature F28.enable-disable --result pass \
  --entry 'REST enable and detail (enable scope)' \
  --expected 'Enabling keeps both page manifests intact' \
  --actual 'Enabled; short label retained and legacy field omitted' \
  --artifact evidence/F28.enable-disable/enable.json
control-agent-server evidence add --feature F28.restart-persistence --result pass \
  --entry 'REST installed detail after process restart (manifest scope)' \
  --expected 'Enabled state and page metadata survive restart' \
  --actual 'All assertions passed after restart' \
  --artifact evidence/F28.restart-persistence/detail.json
control-agent-server evidence report --out "$AGENT_SERVER_VERIFY_RUN/evidence/report.md" --print-markdown
