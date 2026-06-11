SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

# Colors for output
ECHO := printf '%b\n'
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
CYAN := \033[36m
RESET := \033[0m
UNDERLINE := \033[4m

# Required uv version
REQUIRED_UV_VERSION := 0.8.13
PKGS ?= openhands-sdk openhands-tools openhands-workspace openhands-agent-server
AGENT_CANVAS_PACKAGE ?= @openhands/agent-canvas
AGENT_SERVER_FRONTEND_DIR := openhands-agent-server/openhands/agent_server/_frontend

.PHONY: build build-with-frontend agent-canvas-frontend format lint clean help check-uv-version

# Default target
.DEFAULT_GOAL := help


check-uv-version:
	@$(ECHO) "$(YELLOW)Checking uv version...$(RESET)"
	@UV_VERSION=$$(uv --version | cut -d' ' -f2); \
	REQUIRED_VERSION=$(REQUIRED_UV_VERSION); \
	if [ "$$(printf '%s\n' "$$REQUIRED_VERSION" "$$UV_VERSION" | sort -V | head -n1)" != "$$REQUIRED_VERSION" ]; then \
		$(ECHO) "$(RED)Error: uv version $$UV_VERSION is less than required $$REQUIRED_VERSION$(RESET)"; \
		$(ECHO) "$(YELLOW)Please update uv with: uv self update$(RESET)"; \
		exit 1; \
	fi; \
	$(ECHO) "$(GREEN)uv version $$UV_VERSION meets requirements$(RESET)"

build: check-uv-version
	@$(ECHO) "$(CYAN)Setting up OpenHands V1 development environment...$(RESET)"
	@$(ECHO) "$(YELLOW)Installing dependencies with uv sync --dev...$(RESET)"
	@uv sync --dev
	@$(ECHO) "$(GREEN)Dependencies installed successfully.$(RESET)"
	@$(ECHO) "$(YELLOW)Setting up pre-commit hooks...$(RESET)"
	@uv run pre-commit install
	@$(ECHO) "$(GREEN)Pre-commit hooks installed successfully.$(RESET)"
	@$(ECHO) "$(GREEN)Build complete! Development environment is ready.$(RESET)"

build-with-frontend: build agent-canvas-frontend
	@$(ECHO) "$(GREEN)Build complete with bundled agent-canvas frontend.$(RESET)"

agent-canvas-frontend:
	@$(ECHO) "$(CYAN)Fetching prebuilt agent-canvas frontend...$(RESET)"
	@tmp_dir=$$(mktemp -d); \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	npm --silent pack "$(AGENT_CANVAS_PACKAGE)" --pack-destination "$$tmp_dir" >/dev/null; \
	tarball=$$(find "$$tmp_dir" -maxdepth 1 -name '*.tgz' -print -quit); \
	if [ -z "$$tarball" ]; then \
		$(ECHO) "$(RED)No agent-canvas tarball was downloaded.$(RESET)"; \
		exit 1; \
	fi; \
	tar -xzf "$$tarball" -C "$$tmp_dir"; \
	if [ ! -d "$$tmp_dir/package/build" ]; then \
		$(ECHO) "$(RED)agent-canvas package does not contain build/ assets.$(RESET)"; \
		exit 1; \
	fi; \
	rm -rf "$(AGENT_SERVER_FRONTEND_DIR)"; \
	mkdir -p "$(AGENT_SERVER_FRONTEND_DIR)"; \
	cp -R "$$tmp_dir/package/build/." "$(AGENT_SERVER_FRONTEND_DIR)/"
	@$(ECHO) "$(GREEN)Bundled frontend assets in $(AGENT_SERVER_FRONTEND_DIR).$(RESET)"

format:
	@$(ECHO) "$(YELLOW)Formatting code with uv format...$(RESET)"
	@uv run ruff format
	@$(ECHO) "$(GREEN)Code formatted successfully.$(RESET)"

lint:
	@$(ECHO) "$(YELLOW)Linting code with ruff...$(RESET)"
	@uv run ruff check --fix
	@$(ECHO) "$(GREEN)Linting completed.$(RESET)"

pre-commit:
	@$(ECHO) "$(YELLOW)Run pre-commit...$(RESET)"
	uv run pre-commit run --all-files
	@$(ECHO) "$(GREEN)Pre-commit run successfully.$(RESET)"

clean:
	@$(ECHO) "$(YELLOW)Cleaning up cache files...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache 2>/dev/null || true
	@$(ECHO) "$(GREEN)Cache files cleaned.$(RESET)"


# Show help
help:
	@$(ECHO) "$(CYAN)OpenHands V1 Makefile$(RESET)"
	@$(ECHO) ""
	@$(ECHO) "$(UNDERLINE)Usage:$(RESET) make <COMMAND>"
	@$(ECHO) ""
	@$(ECHO) "$(UNDERLINE)Commands:$(RESET)"
	@$(ECHO) "  $(GREEN)build$(RESET)                Setup development environment (install deps + hooks)"
	@$(ECHO) "  $(GREEN)build-with-frontend$(RESET)  Setup dev env and bundle agent-canvas frontend"
	@$(ECHO) "  $(GREEN)agent-canvas-frontend$(RESET) Bundle prebuilt agent-canvas frontend assets"
	@$(ECHO) "  $(GREEN)build-server$(RESET)         Build agent-server executable"
	@$(ECHO) "  $(GREEN)test-server-schema$(RESET)   Test server schema"
	@$(ECHO) "  $(GREEN)format$(RESET)               Format code with uv format"
	@$(ECHO) "  $(GREEN)lint$(RESET)                 Lint code with ruff"
	@$(ECHO) "  $(GREEN)pre-commit$(RESET)           Run the pre-commit"
	@$(ECHO) "  $(GREEN)clean$(RESET)                Clean up cache files"
	@$(ECHO) "  $(GREEN)help$(RESET)                 Show this help message"

build-server: check-uv-version
	@$(ECHO) "$(CYAN)Building agent-server executable...$(RESET)"
	@uv run pyinstaller openhands-agent-server/openhands/agent_server/agent-server.spec
	@$(ECHO) "$(GREEN)Build complete! Executable is in dist/agent-server/$(RESET)"

test-server-schema: check-uv-version
	set -euo pipefail;
	# Generate OpenAPI JSON inline (no file left in repo)
	uv run python -c 'import os,json; from openhands.agent_server.api import api; open("openapi.json","w").write(json.dumps(api.openapi(), indent=2))'
	npx --yes @apidevtools/swagger-cli@^4 validate openapi.json
	# Clean up temp schema
	rm -f openapi.json
	rm -rf .client


.PHONY: set-package-version
set-package-version: check-uv-version
	@if [ -z "$(version)" ]; then \
		$(ECHO) "$(RED)Error: missing version. Use: make set-package-version version=1.2.3$(RESET)"; \
		exit 1; \
	fi
	@$(ECHO) "$(CYAN)Setting version to $(version) for: $(PKGS)$(RESET)"
	@for PKG in $(PKGS); do \
		$(ECHO) "$(YELLOW)bumping $$PKG -> $(version)$(RESET)"; \
		uv version --package $$PKG $(version); \
	done
	@$(ECHO) "$(GREEN)Version updated in all selected packages.$(RESET)"
