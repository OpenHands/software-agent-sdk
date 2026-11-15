"""Standalone stdlib-only credential delivery adapters."""

import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def resolve(config: dict, binding_id: str) -> str | None:
    req = Request(
        config["url"],
        data=json.dumps({"binding": binding_id}).encode(),
        headers={"Authorization": "Bearer " + config["capability"]},
    )
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response).get("value")


def git_helper(config: dict, consumer: str, args: list[str]) -> int:
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
    for binding_id, binding in config["bindings"].items():
        if (
            binding["consumer"] == consumer
            and binding["adapter"] == "git_helper"
            and binding["host"] == fields.get("host", "").lower()
            and fields.get("username", binding["username"]) == binding["username"]
        ):
            value = resolve(config, binding_id)
            if value and not any(char in value for char in "\r\n\0"):
                print("username=" + binding["username"])
                print("password=" + value)
            break
    return 0


def run(config_path: str, mode: str, consumer: str, args: list[str]) -> int:
    config = json.loads(Path(config_path).read_text())
    if mode == "git-helper":
        return git_helper(config, consumer, args)
    if mode != "exec":
        raise ValueError("unknown credential adapter mode")
    target = config["executables"].get(consumer)
    if not target:
        raise ValueError("bound consumer executable is unavailable")
    env = dict(os.environ)
    for binding_id, binding in config["bindings"].items():
        if binding["consumer"] != consumer:
            continue
        if binding["adapter"] == "environment":
            value = resolve(config, binding_id)
            if value is None:
                raise ValueError("bound secret is unavailable")
            env[binding["env_var"]] = value
        elif binding["adapter"] == "git_helper":
            count = int(env.get("GIT_CONFIG_COUNT", "0"))
            if count < 0:
                raise ValueError("invalid GIT_CONFIG_COUNT")
            helper = "!" + shlex.join(
                config["launcher"] + [config_path, "git-helper", consumer]
            )
            env[f"GIT_CONFIG_KEY_{count}"] = (
                f"credential.https://{binding['host']}.helper"
            )
            env[f"GIT_CONFIG_VALUE_{count}"] = helper
            env["GIT_CONFIG_COUNT"] = str(count + 1)
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
        return run(args[0], args[1], args[2], args[3:])
    except Exception:
        # Resolution errors may contain secret-bearing URLs or response bodies.
        print(
            "Terminal credential adapter: credential delivery failed", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
