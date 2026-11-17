"""Object storage buckets on Central + optional boto3 S3 copy.


DEPRECATED: thin shim over fcctl._sync.objects (generated from fcctl._async.objects); new code should use the async core / facade.
"""
from __future__ import annotations

from .guard import guarded
from ._sync import objects as _impl

M = _impl.M
OPS = _impl.OPS
get_object_storage = _impl.get_object_storage
create_bucket = _impl.create_bucket
list_objects = _impl.list_objects
get_bucket_usage = _impl.get_bucket_usage
get_upload_url = _impl.get_upload_url
get_object_url = _impl.get_object_url
delete_objects = _impl.delete_objects
rotate_credentials = _impl.rotate_credentials
set_bucket_quota = _impl.set_bucket_quota
upload_file = _impl.upload_file
presign_put = _impl.presign_put
presign_get = _impl.presign_get
put_object = _impl.put_object
get_object = _impl.get_object
s3_copy = _impl.s3_copy
list_buckets = _impl.list_buckets
bucket_usage = _impl.bucket_usage
set_quota = _impl.set_quota

delete_bucket = guarded("delete_bucket", lambda name: name)(_impl.delete_bucket)
