"""Content-addressed blob store built on the local agent home.

Digest paths are constructed solely by the server from SHA-256; no user input
is interpolated into the storage path. Text blobs are stored with optional
zlib compression metadata; the raw digest always identifies the uncompressed
content.
"""

from __future__ import annotations

import hashlib
import os
import uuid
import zlib
from pathlib import Path

MAX_ARTIFACT_BYTES = 1024 * 1024
_COMPRESSED_MAGIC = b"CAZ1"


class ArtifactTooLargeError(ValueError):
    pass


class ArtifactCorruptError(OSError):
    pass


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        enable_compression: bool = True,
        max_blob_bytes: int = MAX_ARTIFACT_BYTES,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._enable_compression = enable_compression
        self._max_blob_bytes = max_blob_bytes

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put(self, data: bytes) -> str:
        if len(data) > self._max_blob_bytes:
            raise ArtifactTooLargeError(
                f"artifact exceeds {self._max_blob_bytes} byte limit"
            )
        digest = self.digest(data)
        target = self._path_for(digest)
        if target.exists():
            # Validate existing CAS content. A corrupt blob must not be
            # silently trusted merely because its digest path exists.
            self.read(digest)
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        use_zlib = self._enable_compression and len(data) >= 64
        payload = _COMPRESSED_MAGIC + zlib.compress(data) if use_zlib else data
        try:
            with open(tmp, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return digest

    def put_text(self, text: str) -> str:
        return self.put(text.encode("utf-8"))

    def read(self, digest: str) -> bytes:
        target = self._path_for(digest)
        if not target.is_file():
            raise FileNotFoundError(digest)
        raw = target.read_bytes()
        if self.digest(raw) == digest:
            return raw
        try:
            if raw.startswith(_COMPRESSED_MAGIC):
                data = zlib.decompress(raw[len(_COMPRESSED_MAGIC) :])
            else:
                # Compatibility with task_004's first development build,
                # which wrote headerless zlib payloads.
                data = zlib.decompress(raw)
        except zlib.error as exc:
            raise ArtifactCorruptError(digest) from exc
        if self.digest(data) != digest:
            raise ArtifactCorruptError(digest)
        return data

    def read_text(self, digest: str) -> str:
        return self.read(digest).decode("utf-8")

    def exists(self, digest: str) -> bool:
        return self._path_for(digest).is_file()

    def delete(self, digest: str) -> None:
        target = self._path_for(digest)
        try:
            target.unlink(missing_ok=True)
            target.parent.rmdir()
        except OSError:
            pass

    def list_digests(self) -> set[str]:
        """Return well-formed CAS objects for startup reconciliation."""

        root = self._root / "sha256"
        if not root.is_dir():
            return set()
        result: set[str] = set()
        try:
            for prefix in root.iterdir():
                if not prefix.is_dir():
                    continue
                for target in prefix.iterdir():
                    name = target.name
                    if (
                        target.is_file()
                        and len(name) == 64
                        and name.startswith(prefix.name)
                        and all(ch in "0123456789abcdef" for ch in name)
                    ):
                        result.add(name)
        except OSError:
            return result
        return result

    def _path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid sha256 digest")
        return self._root / "sha256" / digest[:2] / digest
