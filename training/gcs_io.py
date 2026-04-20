"""Tiny GCS helpers for uploading the local artifact dir to a gs:// prefix."""
from __future__ import annotations

from pathlib import Path

from google.cloud import storage


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a GCS URI: {uri}")
    without_scheme = uri[len("gs://"):]
    bucket, _, prefix = without_scheme.partition("/")
    return bucket, prefix.rstrip("/")


def upload_directory(local_dir: Path, gcs_uri: str) -> None:
    """Recursively upload `local_dir` under `gcs_uri` (gs://bucket/prefix/)."""
    local_dir = Path(local_dir)
    bucket_name, prefix = _parse_gcs_uri(gcs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        blob_name = f"{prefix}/{rel}" if prefix else rel
        bucket.blob(blob_name).upload_from_filename(str(path))
