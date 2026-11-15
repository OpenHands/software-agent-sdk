"""Exercise real Git and CLI adapter subprocesses with synthetic credentials."""

import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from typing import Literal

import pytest

from openhands.sdk import LLM, Agent, LocalConversation
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.secret.terminal_credentials import TerminalCredentialBinding
from openhands.tools.terminal import TerminalAction
from openhands.tools.terminal.git_credentials import TerminalCredentialRuntime
from openhands.tools.terminal.impl import TerminalExecutor


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX protocol fixtures")


def binding(
    consumer: Literal["git", "gh", "glab"] = "git",
    host="example.test",
    secret_ref="custom",
):
    return TerminalCredentialBinding(
        consumer=consumer,
        host=host,
        secret_ref=secret_ref,
        username="test-user" if consumer == "git" else None,
    )


@pytest.fixture
def runtime(tmp_path):
    registry = SecretRegistry()
    registry.update_secrets(
        {"custom": "synthetic-credential-one", "second": "synthetic-credential-two"}
    )
    runtimes = []

    def create(*bindings, executable_path=None):
        instance = TerminalCredentialRuntime(
            tuple(bindings), registry, executable_path=executable_path
        )
        runtimes.append(instance)
        env = dict(
            os.environ, PATH=str(instance.directory) + os.pathsep + os.environ["PATH"]
        )
        env.update(
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
            HOME=str(tmp_path),
        )
        return instance, env, registry

    yield create
    for instance in runtimes:
        instance.close()


def fill(env, host="example.test", protocol="https"):
    return subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol={protocol}\nhost={host}\n\n",
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_git_delivery_and_rotation(runtime):
    instance, env, registry = runtime(binding())
    result = fill(env)
    assert result.returncode == 0, result.stderr
    assert "username=test-user" in result.stdout
    assert "password=synthetic-credential-one" in result.stdout
    assert "synthetic-credential-one" not in registry.mask_secrets_in_output(
        result.stdout
    )
    registry.update_secrets({"custom": "rotated-credential"})
    assert "password=rotated-credential" in fill(env).stdout
    assert all(
        "synthetic-credential" not in p.read_text()
        for p in instance.directory.iterdir()
    )
    assert "custom" not in env


@pytest.mark.parametrize(
    "host,protocol",
    [
        ("other.test", "https"),
        ("example.test.evil", "https"),
        ("example.test:8443", "https"),
        ("example.test", "http"),
    ],
)
def test_git_rejects_wrong_host_or_protocol(runtime, host, protocol):
    _, env, _ = runtime(binding())
    result = fill(env, host, protocol)
    assert result.returncode != 0
    assert "synthetic-credential" not in result.stdout + result.stderr


def test_multiple_hosts_and_consumer_separation(runtime):
    _, env, _ = runtime(
        binding(),
        binding(host="other.test:8443", secret_ref="second"),
        binding("gh", "github.com"),
    )
    assert "synthetic-credential-two" in fill(env, "other.test:8443").stdout
    assert fill(env, "github.com").returncode != 0


def test_existing_helpers_and_config_count_win(runtime):
    _, env, _ = runtime(binding())
    env.update(
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="test.keep",
        GIT_CONFIG_VALUE_0="existing",
        GIT_CONFIG_KEY_1="credential.helper",
        GIT_CONFIG_VALUE_1=(
            "!f() { echo username=existing; echo password=existing-password; }; f"
        ),
    )
    result = fill(env)
    assert "password=existing-password" in result.stdout
    result = subprocess.run(
        ["git", "config", "test.keep"], env=env, text=True, capture_output=True
    )
    assert result.stdout.strip() == "existing"


@pytest.fixture
def fake_cli(tmp_path):
    # Protocol fixtures, not mocks: executable children report their actual env/argv.
    for name in ("gh", "glab"):
        executable = tmp_path / name
        executable.write_text(
            f"#!{sys.executable}\nimport os,sys,json\n"
            "print(json.dumps({'args':sys.argv[1:], 'tokens':"
            "{k:v for k,v in os.environ.items() if k in "
            "['GH_TOKEN','GH_ENTERPRISE_TOKEN','GITLAB_TOKEN']}}))\n"
        )
        executable.chmod(0o700)
    return tmp_path


@pytest.mark.parametrize(
    "consumer,host,var",
    [
        ("gh", "github.com", "GH_TOKEN"),
        ("gh", "enterprise.test:8443", "GH_ENTERPRISE_TOKEN"),
        ("glab", "custom.test", "GITLAB_TOKEN"),
    ],
)
def test_cli_child_only_env(runtime, fake_cli, consumer, host, var):
    _, env, _ = runtime(
        binding(consumer, host),
        executable_path=str(fake_cli) + os.pathsep + os.environ["PATH"],
    )
    env["PATH"] = (
        env["PATH"].split(os.pathsep)[0]
        + os.pathsep
        + str(fake_cli)
        + os.pathsep
        + os.environ["PATH"]
    )
    command = f"{consumer} issue list --repo {host}/owner/repo; env"
    result = subprocess.run(
        ["sh", "-c", command], cwd=fake_cli, env=env, text=True, capture_output=True
    )
    first, rest = result.stdout.split("\n", 1)
    payload = json.loads(first)
    assert payload["tokens"] == {var: "synthetic-credential-one"}
    assert payload["args"] == ["issue", "list", "--repo", f"{host}/owner/repo"]
    assert "synthetic-credential" not in rest
    result = subprocess.run(["env"], env=env, text=True, capture_output=True)
    assert "synthetic-credential" not in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["issue", "list", "--repo", "other.test/o/r"],
        ["api", "https://other.test/api"],
        ["api", "--hostname=other.test", "user"],
    ],
)
def test_cli_wrong_host_never_receives_bound_token(runtime, fake_cli, args):
    _, env, _ = runtime(
        binding("gh", "github.com"),
        executable_path=str(fake_cli) + os.pathsep + os.environ["PATH"],
    )
    env["PATH"] = (
        env["PATH"].split(os.pathsep)[0]
        + os.pathsep
        + str(fake_cli)
        + os.pathsep
        + os.environ["PATH"]
    )
    result = subprocess.run(
        ["gh", *args], cwd=fake_cli, env=env, text=True, capture_output=True
    )
    assert "synthetic-credential" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["extension", "exec", "foo"],
        ["api", "--hostname=github.com", "https://other.test/api"],
        ["issue", "list", "--repo"],
    ],
)
def test_cli_ambiguous_or_unsupported_fails_closed(runtime, fake_cli, args):
    _, env, _ = runtime(
        binding("gh", "github.com"),
        executable_path=str(fake_cli) + os.pathsep + os.environ["PATH"],
    )
    env["PATH"] = (
        env["PATH"].split(os.pathsep)[0]
        + os.pathsep
        + str(fake_cli)
        + os.pathsep
        + os.environ["PATH"]
    )
    result = subprocess.run(
        ["gh", *args], cwd=fake_cli, env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "synthetic-credential" not in result.stdout + result.stderr


def test_no_binding_no_adapter(runtime):
    instance, env, _ = runtime()
    assert not (instance.directory / "git").exists()
    assert shutil.which("git", path=env["PATH"]) == shutil.which("git")
    assert fill(env).returncode != 0


def test_close_revokes_and_cleans_up():
    registry = SecretRegistry()
    instance = TerminalCredentialRuntime((binding(),), registry)
    directory = instance.directory
    instance.close()
    assert not directory.exists()


def test_terminal_binding_reset_and_session_isolation(tmp_path):
    with (
        closing(
            LocalConversation(
                agent=Agent(llm=LLM(model="test-model")),
                workspace=tmp_path,
                persistence_dir=tmp_path / "state",
                visualizer=None,
                secrets={"custom": "synthetic-credential-one"},
                terminal_credential_bindings=[binding()],
            )
        ) as conversation,
        closing(
            TerminalExecutor(
                working_dir=str(tmp_path),
                terminal_type="subprocess",
                env={
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                    "HOME": str(tmp_path),
                },
            )
        ) as terminal,
    ):
        command = (
            "printf 'protocol=https\\nhost=example.test\\n\\n' | "
            "git credential fill; env"
        )
        result = terminal(TerminalAction(command=command), conversation)
        assert result.exit_code == 0
        assert "password=<secret-hidden>" in result.text
        assert "synthetic-credential" not in result.text
        result = terminal(TerminalAction(command="env"), conversation)
        assert "custom=" not in result.text
        assert "GIT_CONFIG_COUNT=" not in result.text
        result = terminal(TerminalAction(command=command, reset=True), conversation)
        assert "password=<secret-hidden>" in result.text


def test_missing_secret_does_not_fall_back_to_other_registry_entry(runtime):
    _, env, _ = runtime(binding(secret_ref="missing"))
    assert fill(env).returncode != 0


def test_bound_executable_is_not_reselected_from_mutated_path(
    runtime, fake_cli, tmp_path
):
    _, env, _ = runtime(
        binding("gh", "github.com"),
        executable_path=str(fake_cli) + os.pathsep + os.environ["PATH"],
    )
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    executable = replacement / "gh"
    executable.write_text("#!/bin/sh\necho WRONG_EXECUTABLE\n")
    executable.chmod(0o700)
    env["PATH"] = env["PATH"].split(os.pathsep)[0] + os.pathsep + str(replacement)
    result = subprocess.run(
        ["gh", "api", "user"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "WRONG_EXECUTABLE" not in result.stdout
    assert json.loads(result.stdout)["tokens"] == {
        "GH_TOKEN": "synthetic-credential-one"
    }


@pytest.mark.parametrize("consumer", ["gh", "glab"])
def test_cli_conflicting_remotes_fail_closed(runtime, fake_cli, tmp_path, consumer):
    _, env, _ = runtime(
        binding(consumer, "example.test"),
        executable_path=str(fake_cli) + os.pathsep + os.environ["PATH"],
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    for name, host in [("origin", "example.test"), ("upstream", "other.test")]:
        subprocess.run(
            ["git", "remote", "add", name, f"https://{host}/owner/repo"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    result = subprocess.run(
        [consumer, "issue", "list"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "synthetic-credential" not in result.stdout + result.stderr


def test_git_credential_store_operations_do_not_export(runtime):
    instance, env, _ = runtime(binding())
    for operation in ("approve", "reject"):
        result = subprocess.run(
            ["git", "credential", operation],
            input=(
                "protocol=https\nhost=example.test\nusername=test-user\n"
                "password=synthetic-credential-one\n\n"
            ),
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert not result.stdout
    assert all(
        "synthetic-credential" not in p.read_text()
        for p in instance.directory.iterdir()
    )
