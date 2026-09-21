"""Immutable storage for uploaded source documents.

Source files are the evidence behind every published claim, so once a file is
stored it is never modified.  Re-uploading the same document is detected by
hash and reuses the existing object rather than creating a second copy.

Keys are content-addressed (``sha256`` prefix directories), which makes
deduplication automatic and means a key can be verified against its content.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import Settings, get_settings


class StorageError(RuntimeError):
    pass


class SourceStorage(ABC):
    """Where immutable source documents live."""

    @abstractmethod
    def put(self, local_path: Path, *, sha256: str, filename: str) -> str:
        """Store a file and return its storage key."""

    @abstractmethod
    def open(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def local_copy(self, key: str) -> Path:
        """A local path for a stored object, for parsers that need a file."""

    @staticmethod
    def key_for(sha256: str, filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return f"sources/{sha256[:2]}/{sha256[2:4]}/{sha256}{suffix}"


class LocalSourceStorage(SourceStorage):
    """Filesystem storage, for development and single-host deployments."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise StorageError(f"storage key escapes the storage root: {key!r}")
        return path

    def put(self, local_path: Path, *, sha256: str, filename: str) -> str:
        key = self.key_for(sha256, filename)
        destination = self._path(key)
        if destination.exists():
            # Content-addressed: identical hash means identical bytes.
            return key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, destination)
        # Read-only, so a later bug cannot rewrite the evidence.
        destination.chmod(0o444)
        return key

    def open(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_copy(self, key: str) -> Path:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"no stored object for key {key!r}")
        return path


class S3SourceStorage(SourceStorage):
    """S3-compatible object storage for production deployments."""

    def __init__(self, settings: Settings) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise StorageError(
                "S3 storage requires boto3; install it or set TRACKER_STORAGE_BACKEND=local"
            ) from exc
        if not settings.s3_bucket:
            raise StorageError("TRACKER_S3_BUCKET must be set when using S3 storage")
        self.bucket = settings.s3_bucket
        self._cache = Path(settings.storage_local_path) / "_s3cache"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put(self, local_path: Path, *, sha256: str, filename: str) -> str:
        key = self.key_for(sha256, filename)
        if not self.exists(key):
            self._client.upload_file(str(local_path), self.bucket, key)
        return key

    def open(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def local_copy(self, key: str) -> Path:
        path = self._cache / key.replace("/", "_")
        if not path.exists():
            path.write_bytes(self.open(key))
        return path


def get_storage(settings: Settings | None = None) -> SourceStorage:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        return S3SourceStorage(settings)
    return LocalSourceStorage(settings.storage_local_path)
