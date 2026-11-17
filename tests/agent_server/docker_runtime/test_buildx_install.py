"""Exercise the Dockerfile's Buildx install shell without touching the host."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


DOCKERFILE = (
    Path(__file__).resolve().parents[3]
    / "openhands-agent-server/openhands/agent_server/docker/Dockerfile"
)


class BuildxInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.plugin = self.root / "docker-buildx"
        self.plugin.write_text("old apt plugin")
        text = DOCKERFILE.read_text()
        self.args = dict(re.findall(r"^ARG (BUILDX_\w+)=(\S+)$", text, re.M))
        block = text.split("# Replace the apt-provided plugin in place", 1)[1]
        self.script = block.split("RUN ", 1)[1].split("\n\n", 1)[0]
        self.script = self.script.replace(
            "/usr/libexec/docker/cli-plugins/docker-buildx", str(self.plugin)
        ).replace("/tmp/docker-buildx", str(self.root / "download"))
        self.payload = self.root / "payload"
        self.payload.write_text("#!/bin/sh\necho pinned-buildx\n")
        self.stub("dpkg", 'echo "$TEST_ARCH"')
        self.stub(
            "curl",
            'printf "%s\\n" "$@" > "$TEST_ROOT/curl-args"; '
            'while [ "$1" != "-o" ]; do shift; done; '
            'cp "$TEST_ROOT/payload" "$2"',
        )
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        self.env = {
            **os.environ,
            **self.args,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "INSTALL_CAPABILITIES": "vscode,browser,docker",
            "BUILDX_AMD64_SHA256": digest,
            "BUILDX_ARM64_SHA256": digest,
            "TEST_ROOT": str(self.root),
            "TEST_ARCH": "amd64",
        }

    def stub(self, name, script):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\nset -eu\n{script}\n")
        path.chmod(0o755)

    def run_install(self, **env):
        return subprocess.run(
            ["sh", "-c", self.script],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_installs_both_architectures_in_place(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch):
                result = self.run_install(TEST_ARCH=arch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.plugin.read_bytes(), self.payload.read_bytes())
                self.assertTrue(os.access(self.plugin, os.X_OK))
                self.assertFalse((self.root / "download").exists())
                self.assertIn(
                    f"/v{self.args['BUILDX_VERSION']}/"
                    f"buildx-v{self.args['BUILDX_VERSION']}.linux-{arch}",
                    (self.root / "curl-args").read_text(),
                )

    def test_bad_checksum_preserves_apt_plugin(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch):
                result = self.run_install(
                    TEST_ARCH=arch, **{f"BUILDX_{arch.upper()}_SHA256": "0" * 64}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.plugin.read_text(), "old apt plugin")

    def test_docker_excluded_skips_download(self):
        result = self.run_install(INSTALL_CAPABILITIES="vscode,browser")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "curl-args").exists())
        self.assertEqual(self.plugin.read_text(), "old apt plugin")

    def test_unknown_architecture_fails_before_download(self):
        result = self.run_install(TEST_ARCH="riscv64")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "curl-args").exists())


if __name__ == "__main__":
    unittest.main()
