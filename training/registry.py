"""Vertex AI Model Registry upload for the DistilBERT priority classifier.

The registry holds a curated, versioned index pointing at the raw artifacts in
`gs://.../models/distilbert-priority/runs/{run_id}/model/`. We attach metrics
and data-version metadata via labels + description so the Registry UI is
self-describing.
"""
from __future__ import annotations

from typing import Dict

from google.cloud import aiplatform

MODEL_DISPLAY_NAME = "distilbert-priority"

# Serving container placeholder. PLAN.md puts actual serving in a separate
# branch (Cloud Run endpoint), so we just need a container that satisfies the
# Vertex Registry upload API without failing artifact-format validation.
# Prebuilt Vertex prediction containers (pytorch/sklearn/xgboost/tensorflow)
# all validate artifact dir contents at upload time (looking for model.mar /
# model.joblib / model.bst / saved_model.pb respectively) and reject our HF
# safetensors layout.
#
# The workaround: point at our own training container. Registration then only
# validates "image exists in Artifact Registry," and we keep the artifact_uri
# pointing at the real GCS model dir. When the serving branch lands, each
# deployment will override this with a real HF-serving container.
SERVING_CONTAINER_IMAGE_URI = (
    "us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/"
    "distilbert-priority:latest"
)


def _sanitize_label(value: str) -> str:
    """Vertex labels: lowercase letters, digits, dashes, underscores; <=63 chars."""
    out = []
    for ch in value.lower():
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("-")
    return "".join(out)[:63]


def register_model(
    project: str,
    region: str,
    run_id: str,
    artifact_uri: str,
    metrics: Dict,
    data_version: str,
) -> aiplatform.Model:
    """Register the trained model in Vertex AI Model Registry.

    Versioning: all runs register under the same `model_id` (display name),
    producing incrementing version numbers (v1, v2, ...) in the Registry UI.
    """
    aiplatform.init(project=project, location=region)

    accuracy = float(metrics["accuracy"])
    macro_f1 = float(metrics["macro_f1"])

    description = (
        f"run_id={run_id} | data_version={data_version} | "
        f"accuracy={accuracy:.4f} | macro_f1={macro_f1:.4f}"
    )

    labels = {
        "run-id": _sanitize_label(run_id),
        "data-version": _sanitize_label(data_version),
        "accuracy": _sanitize_label(f"{accuracy:.4f}"),
        "macro-f1": _sanitize_label(f"{macro_f1:.4f}"),
    }

    existing = aiplatform.Model.list(
        filter=f'display_name="{MODEL_DISPLAY_NAME}"',
        order_by="create_time desc",
    )
    parent_model = existing[0].resource_name if existing else None

    print(f"[registry] display_name={MODEL_DISPLAY_NAME}")
    print(f"[registry] artifact_uri={artifact_uri}")
    print(f"[registry] parent_model={parent_model or '<new>'}")
    print(f"[registry] description={description}")

    model = aiplatform.Model.upload(
        display_name=MODEL_DISPLAY_NAME,
        artifact_uri=artifact_uri,
        serving_container_image_uri=SERVING_CONTAINER_IMAGE_URI,
        serving_container_predict_route="/predict",
        serving_container_health_route="/health",
        serving_container_ports=[8080],
        description=description,
        labels=labels,
        parent_model=parent_model,
        is_default_version=True,
        version_aliases=[_sanitize_label(run_id)],
    )

    print(f"[registry] registered: {model.resource_name}")
    print(f"[registry] version: {model.version_id}")
    return model
