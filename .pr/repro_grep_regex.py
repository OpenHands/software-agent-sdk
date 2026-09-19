"""From the checkout, run: .venv/bin/python .pr/repro_grep_regex.py."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from openhands.tools.grep import GrepAction, GrepExecutor


def main() -> int:
    grep_binary = shutil.which("grep")
    if grep_binary is None:
        raise SystemExit("This reproduction requires an installed system grep.")

    original_path = os.environ.get("PATH", "")
    failures = 0
    with tempfile.TemporaryDirectory(prefix="grep-regex-repro-") as directory:
        root = Path(directory)
        binary_directory = root / "bin"
        binary_directory.mkdir()
        (binary_directory / "grep").symlink_to(grep_binary)
        os.environ["PATH"] = str(binary_directory)
        try:
            print(
                json.dumps(
                    {
                        "grep_available": shutil.which("grep") is not None,
                        "rg": shutil.which("rg"),
                    }
                )
            )
            cases = [
                ("extended", r"^(foo|bar)+[0-9]{2}$", "FooBAR12\n", "foo12 extra\n"),
                ("escaped", r"^foo\(bar\)\+$", "foo(bar)+\n", "foobar\n"),
            ]
            for name, pattern, matching_content, other_content in cases:
                search_directory = root / name
                search_directory.mkdir()
                (search_directory / "matching.txt").write_text(matching_content)
                (search_directory / "other.txt").write_text(other_content)
                observation = GrepExecutor(working_dir=str(search_directory))(
                    GrepAction(pattern=pattern)
                )
                actual = [Path(path).name for path in observation.matches]
                passed = not observation.is_error and actual == ["matching.txt"]
                failures += not passed
                print(
                    json.dumps(
                        {
                            "case": name,
                            "pattern": pattern,
                            "expected": ["matching.txt"],
                            "actual": actual,
                            "is_error": observation.is_error,
                            "passed": passed,
                        }
                    )
                )
        finally:
            os.environ["PATH"] = original_path
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
