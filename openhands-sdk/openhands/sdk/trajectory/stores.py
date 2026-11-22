import fcntl
import os
import subprocess
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from openhands.sdk.trajectory.archive import ArchiveGap, Head, ObjectRef
from openhands.sdk.trajectory.journal import digest


def _check_key(key: str) -> None:
    path = PurePosixPath(key)
    if (
        not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or any(char in key for char in "#*?[]\\")
    ):
        raise ValueError("Invalid archive object key")


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DirectoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, data: bytes) -> ObjectRef:
        _check_key(key)
        dest = self.root / key
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=dest.parent) as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            try:
                os.link(file.name, dest)
            except FileExistsError:
                pass
        _sync_directory(dest.parent)
        return ObjectRef(key=key, sha256=digest(data), size=len(data))

    def get(self, ref: ObjectRef) -> bytes:
        _check_key(ref.key)
        if ref.generation is not None:
            raise ValueError("A directory archive has no object generations")
        return (self.root / ref.key).read_bytes()

    def head(self, key: str) -> Head | None:
        _check_key(key)
        try:
            data = (self.root / key).read_bytes()
        except FileNotFoundError:
            return None
        return Head(digest(data), data)

    def advance(self, key: str, previous: Head | None, data: bytes) -> None:
        _check_key(key)
        dest = self.root / key
        with dest.with_suffix(".lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.head(key) != previous:
                raise ArchiveGap(f"Concurrent archive head update for {key}")
            with NamedTemporaryFile(dir=dest.parent, delete_on_close=False) as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
                os.replace(file.name, dest)
            _sync_directory(dest.parent)


class _MissingObject(OSError):
    pass


class GCSStore:
    def __init__(self, prefix: str) -> None:
        if not prefix.startswith("gs://") or prefix == "gs://":
            raise ValueError("Expected a GCS bucket or prefix")
        self.prefix = prefix.rstrip("/")

    def _url(self, key: str) -> str:
        _check_key(key)
        return f"{self.prefix}/{key}"

    @staticmethod
    def _gcloud(*args: str, data: bytes | None = None) -> bytes:
        try:
            completed = subprocess.run(
                ("gcloud", "--quiet", "storage", *args),
                input=data,
                capture_output=True,
                check=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as error:
            if (
                args[:2] == ("objects", "describe")
                and b"not found: 404" in error.stderr
            ):
                raise _MissingObject("GCS object is missing") from error
            raise OSError(f"GCS {args[0]} failed") from error
        except subprocess.TimeoutExpired as error:
            raise OSError(f"GCS {args[0]} timed out") from error
        return completed.stdout

    def _generation(self, url: str) -> str:
        generation = (
            self._gcloud("objects", "describe", url, "--format=value(generation)")
            .decode()
            .strip()
        )
        if not generation.isdecimal():
            raise OSError("Missing GCS object generation")
        return generation

    def put(self, key: str, data: bytes) -> ObjectRef:
        url = self._url(key)
        try:
            self._gcloud("cp", "--if-generation-match=0", "-", url, data=data)
        except OSError:
            # The object can exist when the create succeeded but its reply was lost.
            pass
        return ObjectRef(
            key=key,
            generation=self._generation(url),
            sha256=digest(data),
            size=len(data),
        )

    def get(self, ref: ObjectRef) -> bytes:
        if ref.generation is None or not ref.generation.isdecimal():
            raise ValueError("Expected a pinned GCS generation")
        return self._gcloud("cat", f"{self._url(ref.key)}#{ref.generation}")

    def head(self, key: str) -> Head | None:
        url = self._url(key)
        try:
            generation = self._generation(url)
        except _MissingObject:
            return None
        return Head(generation, self._gcloud("cat", f"{url}#{generation}"))

    def advance(self, key: str, previous: Head | None, data: bytes) -> None:
        generation = previous.revision if previous is not None else "0"
        try:
            self._gcloud(
                "cp",
                f"--if-generation-match={generation}",
                "-",
                self._url(key),
                data=data,
            )
        except OSError:
            current = self.head(key)
            if current is None or current.data != data:
                raise
