import hashlib
import json
import os
import tempfile
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class ArtifactStore:
    def __init__(self, settings):
        self.settings = settings
        self.s3 = None
        if settings.storage_backend == "s3":
            self.s3 = boto3.client("s3", region_name=settings.aws_region, config=Config(signature_version="s3v4"))

    def _path(self, key):
        base = self.settings.local_artifact_dir.resolve()
        path = (base / key).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, body: bytes, content_type="application/octet-stream"):
        if self.s3:
            # Candidate keys are unique per attempt. Writes are immutable.
            try:
                self.s3.put_object(Bucket=self.settings.artifact_bucket, Key=key, Body=body,
                                   ContentType=content_type, IfNoneMatch="*",
                                   Metadata={"sha256": digest(body)})
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "PreconditionFailed":
                    raise
                existing = self.s3.head_object(Bucket=self.settings.artifact_bucket, Key=key)
                if existing.get("Metadata", {}).get("sha256") != digest(body):
                    raise ValueError("An immutable artifact cannot be replaced") from exc
        else:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != body:
                    raise ValueError("An immutable artifact cannot be replaced")
                return
            # Never expose a partially written local object if the process stops mid-write.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False) as f:
                    temporary = f.name
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.read_bytes() != body:
                        raise ValueError("An immutable artifact cannot be replaced")
            finally:
                if temporary:
                    os.unlink(temporary)

    def get(self, key: str) -> bytes:
        if self.s3:
            return self.s3.get_object(Bucket=self.settings.artifact_bucket, Key=key)["Body"].read()
        return self._path(key).read_bytes()

    def get_limited(self, key: str, max_bytes: int) -> bytes:
        """Bound reads before allocating an entire object, including corrupt metadata."""
        if self.s3:
            response = self.s3.get_object(Bucket=self.settings.artifact_bucket, Key=key)
            stream = response['Body']
            try:
                if response.get('ContentLength', 0) > max_bytes:
                    raise ValueError('Stored artifact exceeds its read ceiling')
                content = stream.read(max_bytes + 1)
            finally:
                stream.close()
        else:
            with self._path(key).open('rb') as stream:
                content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError('Stored artifact exceeds its read ceiling')
        return content

    def delete(self, key: str):
        if self.s3:
            self.s3.delete_object(Bucket=self.settings.artifact_bucket, Key=key)
        else:
            self._path(key).unlink(missing_ok=True)

    def download_url(self, key: str, expires=300):
        if not self.s3:
            return None
        return self.s3.generate_presigned_url("get_object", Params={
            "Bucket": self.settings.artifact_bucket, "Key": key,
            "ResponseContentDisposition": "attachment",
        }, ExpiresIn=expires)

    def put_json(self, key, value):
        self.put(key, json.dumps(value, sort_keys=True, default=str).encode(), "application/json")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def artifact_key(tenant, project, attempt, name, content):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not path.name:
        raise ValueError("Invalid artifact path")
    return f"artifacts/{tenant}/{project}/{attempt}/{digest(content)}/{path.as_posix()}"
