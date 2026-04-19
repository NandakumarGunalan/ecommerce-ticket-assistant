"""Submit a Vertex AI Custom Training job for DistilBERT priority fine-tuning.

Assumes the training container has been built and pushed to Artifact Registry:
    us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority:<tag>
"""
from __future__ import annotations

import argparse
import time

from google.cloud import aiplatform

from training.config import (
    DATA_VERSION_DEFAULT,
    GCP_PROJECT,
    GCP_REGION,
    GCS_BUCKET,
)

DEFAULT_IMAGE_URI = (
    f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/ml-repo/distilbert-priority:latest"
)
STAGING_BUCKET = f"gs://{GCS_BUCKET}"


def _make_run_id() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}"


def submit_job(
    image_uri: str,
    run_id: str,
    data_version: str,
    machine_type: str,
    accelerator_type: str,
    accelerator_count: int,
    sync: bool,
) -> None:
    aiplatform.init(
        project=GCP_PROJECT,
        location=GCP_REGION,
        staging_bucket=STAGING_BUCKET,
    )

    display_name = f"distilbert-priority-{run_id}"
    job = aiplatform.CustomContainerTrainingJob(
        display_name=display_name,
        container_uri=image_uri,
    )

    container_args = [
        "--cloud",
        "--run-id", run_id,
        "--data-version", data_version,
        "--output-root", "/tmp/artifacts",
    ]

    print(f"[launch] display_name={display_name}")
    print(f"[launch] image={image_uri}")
    print(f"[launch] machine={machine_type} accelerator={accelerator_type} x{accelerator_count}")
    print(f"[launch] args={container_args}")

    job.run(
        args=container_args,
        replica_count=1,
        machine_type=machine_type,
        accelerator_type=accelerator_type,
        accelerator_count=accelerator_count,
        sync=sync,
    )

    print(f"[launch] job submitted: {job.resource_name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-uri", default=DEFAULT_IMAGE_URI)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--data-version", default=DATA_VERSION_DEFAULT)
    parser.add_argument("--machine-type", default="n1-standard-4")
    parser.add_argument("--accelerator-type", default="NVIDIA_TESLA_T4")
    parser.add_argument("--accelerator-count", type=int, default=1)
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Submit and return immediately (don't stream logs).",
    )
    args = parser.parse_args()

    run_id = args.run_id or _make_run_id()
    submit_job(
        image_uri=args.image_uri,
        run_id=run_id,
        data_version=args.data_version,
        machine_type=args.machine_type,
        accelerator_type=args.accelerator_type,
        accelerator_count=args.accelerator_count,
        sync=not args.no_sync,
    )


if __name__ == "__main__":
    main()
