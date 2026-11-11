from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import NotFoundError, ProviderError
from app.providers.base import BucketInfo, StorageProvider, register_provider


@register_provider("storage", "local")
class LocalStorageProvider(StorageProvider):
    """Bucket == directory under ``data/storage``; object == file."""

    name = "local"

    def _root(self) -> Path:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        return settings.storage_dir

    def _bucket_dir(self, bucket: str) -> Path:
        path = (self._root() / bucket).resolve()
        if not str(path).startswith(str(self._root())):
            raise ProviderError("非法的 bucket 名称")
        return path

    def create_bucket(self, bucket: str, *, project_id: str) -> BucketInfo:
        path = self._bucket_dir(bucket)
        path.mkdir(parents=True, exist_ok=True)
        (path / ".project").write_text(project_id, encoding="utf-8")
        return BucketInfo(
            name=bucket,
            provider=self.name,
            endpoint=f"file://{path.as_posix()}",
            region="local",
            meta={"path": path.as_posix()},
        )

    def delete_bucket(self, bucket: str) -> None:
        path = self._bucket_dir(bucket)
        if path.exists():
            shutil.rmtree(path)

    def put_object(
        self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> dict[str, Any]:
        path = self._bucket_dir(bucket) / key.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"key": key, "size": len(data), "content_type": content_type, "etag": f"local-{len(data)}"}

    def get_object(self, bucket: str, key: str) -> bytes:
        path = self._bucket_dir(bucket) / key.lstrip("/")
        if not path.exists():
            raise NotFoundError(f"对象不存在: {bucket}/{key}")
        return path.read_bytes()

    def list_objects(self, bucket: str, *, prefix: str = "") -> list[dict[str, Any]]:
        root = self._bucket_dir(bucket)
        if not root.exists():
            return []
        items: list[dict[str, Any]] = []
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.name == ".project":
                continue
            key = file.relative_to(root).as_posix()
            if prefix and not key.startswith(prefix):
                continue
            items.append({"key": key, "size": file.stat().st_size})
        return items

    def presign(self, bucket: str, key: str, *, expires_in: int = 3600) -> str:
        return f"file://{(self._bucket_dir(bucket) / key).as_posix()}?ttl={expires_in}"
