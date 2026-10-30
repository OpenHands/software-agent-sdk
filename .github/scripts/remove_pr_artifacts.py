#!/usr/bin/env python3
"""Stage removal of the tracked repository-root `.pr/` review artifacts."""

import subprocess


def git(*args: str, cwd: str | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True)


def main() -> None:
    root = git("rev-parse", "--show-toplevel").strip()
    if not git("ls-files", "--", ".pr", cwd=root):
        print("No tracked .pr/ artifacts to remove.")
        return
    # -f also removes .pr/ entries an in-progress merge staged or left conflicted.
    subprocess.run(["git", "rm", "-r", "-f", "--", ".pr"], cwd=root, check=True)


if __name__ == "__main__":
    main()
