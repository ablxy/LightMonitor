"""MinIO/S3 object storage adapter with a small testable interface."""

from __future__ import annotations

from datetime import timedelta
from typing import Any


class ObjectStorageUnavailableError(RuntimeError):
    """Raised when the MinIO SDK is not installed."""


class ObjectStorage:
    def __init__(self, config: Any, client: Any = None):
        self.config = config
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from minio import Minio  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise ObjectStorageUnavailableError("minio package is not installed") from exc
            self._client = Minio(
                self.config.endpoint,
                access_key=self.config.access_key,
                secret_key=self.config.secret_key,
                secure=self.config.secure,
            )
        return self._client

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.config.bucket):
            self.client.make_bucket(self.config.bucket)

    def put_bytes(self, object_name: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        from io import BytesIO

        self.ensure_bucket()
        self.client.put_object(
            self.config.bucket,
            object_name,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        return object_name

    def presigned_get_url(self, object_name: str) -> str:
        return self.client.presigned_get_object(
            self.config.bucket,
            object_name,
            expires=timedelta(seconds=self.config.presigned_url_expire_seconds),
        )