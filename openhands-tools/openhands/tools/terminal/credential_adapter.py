"""Standalone stdlib-only credential adapter (also a frozen-server entrypoint)."""

import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


TOKEN_VARIABLES = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITLAB_TOKEN",
    "GITLAB_ACCESS_TOKEN",
    "OAUTH_TOKEN",
)


def request(config: dict, consumer: str, host: str) -> dict:
    payload = json.dumps({"consumer": consumer, "host": host}).encode()
    req = Request(
        config["url"],
        data=payload,
        headers={"Authorization": "Bearer " + config["capability"]},
    )
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response)


def host_from_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("credential targets must be HTTPS without user information")
    return url.hostname.lower() + (f":{url.port}" if url.port else "")


def repo_host(value: str, default: str) -> str:
    if "://" in value:
        return host_from_url(value)
    parts = value.split("/")
    if len(parts) == 2 and all(parts):
        return default
    if len(parts) == 3 and all(parts):
        return parts[0].lower()
    raise ValueError("ambiguous repository target; use an explicit HTTPS repository")


def cli_host(consumer: str, args: list[str], git: str | None) -> str:
    default = "github.com" if consumer == "gh" else "gitlab.com"
    host_var = "GH_HOST" if consumer == "gh" else "GITLAB_HOST"
    default = os.environ.get(host_var, default).lower()
    if consumer == "glab" and host_var not in os.environ:
        default = os.environ.get(
            "GITLAB_URI", os.environ.get("GL_HOST", default)
        ).lower()
    if "://" in default:
        default = host_from_url(default)
    hosts: list[str] = []
    repos: list[str] = []
    positionals: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        key, sep, value = arg.partition("=")
        if key in ("--hostname", "--host", "--repo", "-R"):
            if not sep:
                index += 1
                if index == len(args):
                    raise ValueError("missing host/repository argument")
                value = args[index]
            if key in ("--hostname", "--host"):
                hosts.append(host_from_url(value) if "://" in value else value.lower())
            else:
                repos.append(value)
        elif arg.startswith("-R") and len(arg) > 2:
            repos.append(arg[2:])
        elif arg.startswith("--host") or arg.startswith("--repo"):
            raise ValueError("ambiguous host/repository option")
        elif not arg.startswith("-"):
            positionals.append(arg)
        index += 1
    if not positionals or positionals[0] not in {
        "api",
        "pr",
        "mr",
        "issue",
        "repo",
        "release",
        "workflow",
        "run",
        "search",
    }:
        raise ValueError("this CLI command is not supported for credential delivery")
    if hosts:
        default = hosts[0]
    env_repo = os.environ.get("GH_REPO" if consumer == "gh" else "GITLAB_REPO")
    if env_repo:
        repos.append(env_repo)
    hosts.extend(repo_host(repo, default) for repo in repos)
    for arg in positionals:
        if "://" in arg:
            hosts.append(host_from_url(arg))
    if len(positionals) > 2 and positionals[:2] in (
        ["repo", "clone"],
        ["repo", "view"],
    ):
        hosts.append(repo_host(positionals[2], default))
    repository_commands = {"pr", "mr", "issue", "release", "workflow", "run"}
    needs_repository = positionals[0] in repository_commands or positionals[:2] == [
        "repo",
        "view",
    ]
    if not hosts and git and needs_repository:
        result = subprocess.run(
            [git, "config", "--get-regexp", r"^remote\..*\.url$"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            _, _, remote = line.partition(" ")
            if remote.startswith("git@") and ":" in remote:
                hosts.append(remote[4:].split(":", 1)[0].lower())
            else:
                hosts.append(host_from_url(remote))
    host = hosts[0] if hosts else default
    if any(value != host for value in hosts):
        raise ValueError("conflicting credential target hosts")
    return host


def run(config_path: str, consumer: str, args: list[str]) -> int:
    path = Path(config_path)
    config = json.loads(path.read_text())
    if consumer == "git-helper":
        if args != ["get"]:
            return 0
        fields = {}
        for line in sys.stdin:
            if line == "\n":
                break
            key, sep, value = line.rstrip("\n").partition("=")
            if not sep or key in fields:
                return 0
            fields[key] = value
        if fields.get("protocol") != "https":
            return 0
        credential = request(config, "git", fields.get("host", "").lower())
        if (
            credential
            and fields.get("username", credential["username"]) == credential["username"]
        ):
            print("username=" + credential["username"])
            print("password=" + credential["token"])
        return 0
    target = config["executables"].get(consumer)
    if not target:
        raise ValueError("bound consumer executable is unavailable")
    env = dict(os.environ)
    if consumer == "git":
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        if count < 0:
            raise ValueError("invalid GIT_CONFIG_COUNT")
        helper = "!" + shlex.join(config["launcher"] + [config_path, "git-helper"])
        for host in config["git_hosts"]:
            env[f"GIT_CONFIG_KEY_{count}"] = f"credential.https://{host}.helper"
            env[f"GIT_CONFIG_VALUE_{count}"] = helper
            count += 1
        env["GIT_CONFIG_COUNT"] = str(count)
    else:
        git = config["executables"].get("git")
        if not args or args in (["--help"], ["--version"], ["help"], ["version"]):
            host = ""
        else:
            host = cli_host(consumer, args, git)
        credential = request(config, consumer, host) if host else {}
        # Never carry a token for another host into the selected CLI child.
        for variable in TOKEN_VARIABLES:
            env.pop(variable, None)
        if credential:
            if consumer == "glab" and any(
                arg in {"--head", "--target-project"}
                or arg.startswith(("--head=", "--target-project="))
                for arg in args
            ):
                raise ValueError(
                    "cross-project targeting requires explicit authentication"
                )
            if consumer == "glab" and os.environ.get("GITLAB_HEAD_REPO"):
                raise ValueError(
                    "cross-project targeting requires explicit authentication"
                )
            if consumer == "gh":
                variable = (
                    "GH_TOKEN"
                    if host == "github.com" or host.endswith(".ghe.com")
                    else "GH_ENTERPRISE_TOKEN"
                )
                env["GH_HOST"] = host
            else:
                variable = "GITLAB_TOKEN"
                env["GITLAB_HOST"] = host
                env["GITLAB_API_HOST"] = host
                env["GLAB_API_PROTOCOL"] = "https"
                env["API_PROTOCOL"] = "https"
                env["GLAB_ENABLE_CI_AUTOLOGIN"] = "false"
            env[variable] = credential["token"]
    if os.name == "posix":
        os.execve(target, [target, *args], env)
    child = subprocess.Popen([target, *args], env=env)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.send_signal(signal.CTRL_BREAK_EVENT)
        return child.wait()


def main(args: list[str] | None = None) -> int:
    args = sys.argv[1:] if args is None else args
    try:
        return run(args[0], args[1], args[2:])
    except Exception:
        # Lookup failures may carry secret-bearing URLs/messages from dependencies.
        print(
            "Terminal credential adapter: request failed or target is unsupported",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
