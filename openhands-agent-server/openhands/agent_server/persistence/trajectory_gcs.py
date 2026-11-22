from google.api_core.exceptions import GoogleAPIError
from google.cloud import storage

from openhands.agent_server.persistence.trajectory_archive import (
    Head,
    ObjectRef,
    _check_key,
)
from openhands.sdk.observability.trajectory_journal import digest


class GCSStore:
    def __init__(self, location: str) -> None:
        bucket, _, prefix = location.removeprefix("gs://").partition("/")
        if not location.startswith("gs://") or not bucket:
            raise ValueError("Expected a GCS bucket or prefix")
        self._prefix = prefix.strip("/")
        self._bucket = storage.Client().bucket(bucket)

    def _name(self, key: str) -> str:
        _check_key(key)
        return f"{self._prefix}/{key}" if self._prefix else key

    def put(self, key: str, data: bytes) -> ObjectRef:
        name = self._name(key)
        blob = self._bucket.blob(name)
        try:
            blob.upload_from_string(data, if_generation_match=0)
        except (GoogleAPIError, OSError) as error:
            # A create can commit before its acknowledgment reaches the client.
            live = self._bucket.get_blob(name)
            if live is None:
                raise OSError(f"GCS create failed for {key}") from error
            blob = live
        if blob.generation is None:
            raise OSError(f"Missing GCS generation for {key}")
        return ObjectRef(
            key=key,
            generation=str(blob.generation),
            sha256=digest(data),
            size=len(data),
        )

    def get(self, ref: ObjectRef) -> bytes:
        if ref.generation is None or not ref.generation.isdecimal():
            raise ValueError("Expected a pinned GCS generation")
        return self._read(self._name(ref.key), int(ref.generation))

    def _read(self, name: str, generation: int) -> bytes:
        try:
            return self._bucket.blob(name, generation=generation).download_as_bytes(
                raw_download=True, if_generation_match=generation
            )
        except GoogleAPIError as error:
            raise OSError(f"GCS read failed for generation {generation}") from error

    def head(self, key: str) -> Head | None:
        name = self._name(key)
        blob = self._bucket.get_blob(name)
        if blob is None:
            return None
        if blob.generation is None:
            raise OSError(f"Missing GCS head generation for {key}")
        return Head(str(blob.generation), self._read(name, blob.generation))

    def advance(self, key: str, previous: Head | None, data: bytes) -> None:
        generation = int(previous.revision) if previous is not None else 0
        try:
            self._bucket.blob(self._name(key)).upload_from_string(
                data, if_generation_match=generation
            )
        except (GoogleAPIError, OSError) as error:
            current = self.head(key)
            if current is None or current.data != data:
                raise OSError(f"GCS head update failed for {key}") from error
