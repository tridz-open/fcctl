"""Object storage buckets on Central (presigned-URL object access)."""
from __future__ import annotations

from ..region import default_region
from .central import CentralClient

M = "central.services.api.storage."


async def get_object_storage(c: CentralClient):
    return await c.call("GET", M + "get_object_storage")


async def create_bucket(c: CentralClient, bucket_name: str, region: str | None = None):
    """Create a bucket. The secret is returned ONCE: store it."""
    return await c.call("POST", M + "create_bucket", json={"bucket_name": bucket_name, "region": region or default_region()})


async def delete_bucket(c: CentralClient, name: str):
    return await c.call("POST", M + "delete_bucket", json={"name": name})


async def get_bucket_usage(c: CentralClient, name: str):
    return await c.call("GET", M + "get_bucket_usage", params={"name": name})


async def get_upload_url(c: CentralClient, name: str, key: str, content_type: str = "application/octet-stream"):
    """Upload URL (expires 1h; PUT with the same Content-Type)."""
    return await c.call("POST", M + "get_upload_url", json={"name": name, "key": key, "content_type": content_type})


async def get_object_url(c: CentralClient, name: str, key: str):
    return await c.call("GET", M + "get_object_url", params={"name": name, "key": key})


async def set_bucket_quota(c: CentralClient, name: str, **quota):
    return await c.call("POST", M + "set_bucket_quota", json={"name": name, **quota})


async def upload_file(c: CentralClient, name: str, key: str, path: str, content_type="application/octet-stream"):
    """Upload via presigned URL (no boto3 needed)."""
    import httpx
    url = await get_upload_url(c, name, key, content_type)
    url = url["url"] if isinstance(url, dict) else url

    with open(path, "rb") as f:
        data = f.read()
    async with httpx.AsyncClient(timeout=600) as h:
        r = await h.put(url, content=data, headers={"Content-Type": content_type})
    r.raise_for_status()


async def presign_put(c: CentralClient, name: str, key: str, content_type="application/octet-stream") -> str:
    """Presigned PUT URL (1h). PUT with the same Content-Type."""
    r = await get_upload_url(c, name, key, content_type)
    return r["url"] if isinstance(r, dict) else r


async def presign_get(c: CentralClient, name: str, key: str) -> str:
    """Presigned GET URL for an object."""
    r = await get_object_url(c, name, key)
    return r["url"] if isinstance(r, dict) else r


async def put_object(c: CentralClient, name: str, key: str, path: str, content_type="application/octet-stream"):
    """Upload a local file to name/key. Returns {bucket,key,bytes}."""
    import os
    await upload_file(c, name, key, path, content_type)
    return {"bucket": name, "key": key, "bytes": os.path.getsize(path)}


async def get_object(c: CentralClient, name: str, key: str, dest: str):
    """Download name/key to a local path via presigned URL. Returns {bucket,key,path,bytes}."""
    import httpx
    url = await presign_get(c, name, key)
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as h:
        r = await h.get(url)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    return {"bucket": name, "key": key, "path": dest, "bytes": len(r.content)}


# ---- v1 wrapper names (SDK: buckets.list/usage/quota/delete)
OPS = {"list_buckets": "bucket.get", "bucket_usage": "bucket.usage", "set_quota": "bucket.set_quota",
       "delete_bucket": "bucket.delete", "create_bucket": "bucket.create"}


async def list_buckets(c: CentralClient) -> list:
    """The buckets list."""
    r = await get_object_storage(c)
    return (r.get("buckets", []) if isinstance(r, dict) else r) or []


async def bucket_usage(c: CentralClient, name: str):
    return await get_bucket_usage(c, name)


async def set_quota(c: CentralClient, name: str, size_gib: int | None = None, max_objects: int | None = None):
    """Set the bucket quota (0 = unlimited)."""
    q = {k: v for k, v in (("size_gib", size_gib), ("max_objects", max_objects)) if v is not None}
    return await set_bucket_quota(c, name, **q)
