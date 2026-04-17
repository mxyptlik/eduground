from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class StoredObjectMetadata:
    storage_key: str
    byte_size: int
    etag: str | None
    content_type: str | None = None


class ObjectStorage(ABC):
    @abstractmethod
    def create_signed_upload_url(self, storage_key: str, mime_type: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def create_signed_download_url(self, storage_key: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def head_object(self, storage_key: str) -> StoredObjectMetadata | None:
        raise NotImplementedError

    @abstractmethod
    def get_object_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError
