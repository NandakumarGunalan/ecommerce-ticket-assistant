"""Batch inference CLI for the DistilBERT priority classifier.

Runs as a Cloud Run Job entrypoint (see ``inference/Dockerfile``). Streams
unscored tickets from the Cloud SQL ``tickets`` table, scores them with the
model resolved from Vertex AI Model Registry, and writes the results back to
the ``predictions`` table in the same DB. Emits one structured JSON log per
scored ticket plus a single ``batch_run_summary`` event at end-of-run.

See ``inference/PLAN.md`` section "Batch CLI Spec" for the full flag surface,
selection modes, and failure semantics. Function decomposition is intentional:
``run_batch`` takes injectable dependencies (``BatchDeps``) so unit tests can
drive the whole flow with a mocked DB and a mocked predict function. ``main``
just parses argv and wires the production deps.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from inference import config
from inference.db import DBClient, PredictionRow, TicketRow
from inference.logging_utils import (
    get_logger,
    log_batch_run_summary,
    log_prediction_event,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dependency injection container
# ---------------------------------------------------------------------------


# Default factories/fns that hit real infra. Tests swap these via BatchDeps.
def _default_db_client_factory() -> DBClient:
    return DBClient()


def _default_load_model():
    # Imported lazily so tests that swap this out never pay the import cost
    # (transformers/torch) or need GCP creds to resolve a Vertex Registry entry.
    from inference.model_loader import load_model

    return load_model()


def _default_predict_fn(texts, ids=None, *, batch_size: int = 32):
    from inference.predictor import predict

    return predict(texts, ids=ids, batch_size=batch_size)


@dataclass
class BatchDeps:
    """Injectable dependencies for ``run_batch``.

    Production call path uses the module-level defaults (``main()`` constructs
    a ``BatchDeps()`` with no overrides). Unit tests pass mocks/fakes for
    every field to exercise the full flow without loading a model or hitting
    a DB.
    """

    db_client_factory: Callable[[], DBClient] = field(default=_default_db_client_factory)
    load_model_fn: Callable[[], Any] = field(default=_default_load_model)
    predict_fn: Callable[..., List[Any]] = field(default=_default_predict_fn)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI args per the PLAN's "Batch CLI Spec" section.

    ``--since`` and ``--ticket-ids`` are mutually exclusive. All other flags
    have safe defaults matching the documented behavior.
    """
    parser = argparse.ArgumentParser(
        prog="python -m inference.batch_predict",
        description=(
            "Score tickets from the Cloud SQL tickets table with the current "
            "DistilBERT priority model and write results to the predictions table."
        ),
    )

    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--since",
        type=str,
        default=None,
        help=(
            "Only score tickets with created_at >= YYYY-MM-DD (minus already-"
            "scored rows for the current model version). Mutually exclusive "
            "with --ticket-ids."
        ),
    )
    selection.add_argument(
        "--ticket-ids",
        type=str,
        default=None,
        help=(
            "Comma-separated ticket ids to re-score (e.g. T-001,T-002). "
            "Re-scores even if already present. Mutually exclusive with --since."
        ),
    )

    parser.add_argument(
        "--model-version",
        type=str,
        default=None,
        help=(
            "Pin to a specific registered Vertex Model version (sets "
            "MODEL_VERSION env before loading). Defaults to the current "
            "registry default."
        ),
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Override the auto-generated batch_run_id (default: pred-<utc_timestamp>).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Inference batch size for each forward pass (default: 32).",
    )
    parser.add_argument(
        "--db-fetch-size",
        type=int,
        default=1000,
        help="DB cursor stream size (default: 1000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score, but skip DB writes.",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_batch_run_id(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"pred-{ts}"


def _parse_ticket_ids(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    # Split on comma; trim whitespace; drop empty entries.
    ids = [tok.strip() for tok in raw.split(",")]
    ids = [tok for tok in ids if tok]
    return ids


def _iso_utc(dt: datetime) -> str:
    """UTC ISO-8601 string with trailing Z (matches the PLAN's summary sample)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_predictions(
    ticket_rows: Iterable[TicketRow],
    *,
    batch_size: int,
    batch_run_id: str,
    model_version: str,
    model_run_id: str,
    predict_fn: Callable[..., List[Any]],
    log: Any = None,
) -> Iterator[Tuple[List[PredictionRow], int]]:
    """Generator yielding ``(prediction_rows, skipped_count)`` per batch.

    Streams ``ticket_rows`` (typically a DB cursor iterator) into fixed-size
    batches, filters out malformed rows (empty/null ``ticket_text``), calls
    ``predict_fn`` once per non-empty batch, and converts the returned
    ``Prediction`` objects into ``PredictionRow`` objects ready to hand to
    ``DBClient.insert_predictions``.

    Skip handling: rows with missing text are logged as a warning, excluded
    from the predict call, and counted in the per-batch skipped total. An
    all-skipped batch yields ``([], skipped)`` so the caller still accounts
    for the skipped rows in totals.
    """
    log = log if log is not None else logger

    buffer_ids: List[str] = []
    buffer_texts: List[str] = []
    skipped_in_buffer: int = 0

    def _flush() -> Tuple[List[PredictionRow], int]:
        if not buffer_texts:
            return [], skipped_in_buffer
        preds = predict_fn(buffer_texts, ids=buffer_ids, batch_size=batch_size)
        rows: List[PredictionRow] = []
        for p in preds:
            rows.append(
                PredictionRow(
                    ticket_id=p.id,
                    model_version=model_version,
                    model_run_id=model_run_id,
                    predicted_priority=p.predicted_priority,
                    confidence=float(p.confidence),
                    all_scores=dict(p.all_scores),
                    batch_run_id=batch_run_id,
                )
            )
        return rows, skipped_in_buffer

    for row in ticket_rows:
        text = row.ticket_text
        if text is None or (isinstance(text, str) and text.strip() == ""):
            log.warning(f"skip ticket_id={row.ticket_id} reason=empty_text")
            skipped_in_buffer += 1
        else:
            buffer_ids.append(row.ticket_id)
            buffer_texts.append(text)

        # When non-skipped texts reach batch_size, flush.
        if len(buffer_texts) >= batch_size:
            yield _flush()
            buffer_ids = []
            buffer_texts = []
            skipped_in_buffer = 0

    # Tail flush: any leftover texts OR pending skips.
    if buffer_texts or skipped_in_buffer:
        yield _flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_batch(args: argparse.Namespace, deps: Optional[BatchDeps] = None) -> Dict[str, Any]:
    """Execute the full batch run.

    Returns a summary dict identical in shape to the ``batch_run_summary``
    JSON event. Always emits one summary log at the end (including on empty
    selection). Re-raises any unexpected exception after logging the summary
    and closing the DB client, so the process exits non-zero.
    """
    deps = deps or BatchDeps()

    # Pin the model version via env BEFORE loading the model so model_loader
    # picks up the override. Done here (not in main()) so tests that drive
    # run_batch directly see the same behavior.
    if args.model_version is not None:
        os.environ[config.MODEL_VERSION_ENV] = str(args.model_version)

    batch_run_id = _generate_batch_run_id(args.run_id)
    ticket_ids = _parse_ticket_ids(args.ticket_ids)

    if ticket_ids is not None:
        selection_mode = "explicit-ids"
    elif args.since is not None:
        selection_mode = "since"
    else:
        selection_mode = "default"
    if args.dry_run:
        # Overlay dry-run on whatever selection mode was chosen so the summary
        # makes it obvious nothing was written.
        selection_mode = "dry-run"

    started_at_dt = datetime.now(timezone.utc)
    started_at_mono = time.monotonic()

    # Totals for the summary.
    row_count = 0
    row_count_scored = 0
    row_count_skipped = 0
    class_distribution: Dict[str, int] = {label: 0 for label in config.LABELS}
    confidence_sum: float = 0.0

    model_version_str: str = str(args.model_version) if args.model_version else ""
    model_run_id: str = ""
    db_client: Optional[DBClient] = None
    error: Optional[BaseException] = None

    try:
        loaded = deps.load_model_fn()
        model_version_str = str(loaded.model_version)
        model_run_id = str(loaded.model_run_id)

        db_client = deps.db_client_factory()

        ticket_iter = db_client.fetch_unscored_tickets(
            model_version_str,
            since=args.since,
            ticket_ids=ticket_ids,
            batch_fetch_size=args.db_fetch_size,
        )

        # Count tickets as we pull them through the build_predictions stream.
        # build_predictions' input is already an iterator; wrap to count.
        def _counting_iter(src):
            nonlocal row_count
            for r in src:
                row_count += 1
                yield r

        overwrite = ticket_ids is not None  # explicit-ids mode re-scores

        for pred_rows, skipped in build_predictions(
            _counting_iter(ticket_iter),
            batch_size=args.batch_size,
            batch_run_id=batch_run_id,
            model_version=model_version_str,
            model_run_id=model_run_id,
            predict_fn=deps.predict_fn,
            log=logger,
        ):
            row_count_skipped += skipped

            if not pred_rows:
                continue

            batch_latency_ms = 0.0  # no per-ticket latency tracking in v1
            for pr in pred_rows:
                row_count_scored += 1
                class_distribution[pr.predicted_priority] = (
                    class_distribution.get(pr.predicted_priority, 0) + 1
                )
                confidence_sum += pr.confidence
                log_prediction_event(
                    logger,
                    run_id=batch_run_id,
                    model_version=model_version_str,
                    model_run_id=model_run_id,
                    ticket_id=pr.ticket_id,
                    # We don't keep the original text around here; log an
                    # empty preview. Per-ticket text preview is primarily
                    # useful for PII debugging, which we can revisit later.
                    input_text="",
                    predicted_priority=pr.predicted_priority,
                    confidence=pr.confidence,
                    latency_ms=batch_latency_ms,
                )

            if not args.dry_run:
                db_client.insert_predictions(pred_rows, overwrite=overwrite)

    except BaseException as exc:  # noqa: BLE001 — re-raised after summary
        error = exc
        logger.error(
            "batch_predict.error",
            extra={
                "_json_payload": {
                    "event": "batch_predict.error",
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
            },
        )
    finally:
        finished_at_dt = datetime.now(timezone.utc)
        runtime_sec = time.monotonic() - started_at_mono
        avg_confidence = (
            confidence_sum / row_count_scored if row_count_scored > 0 else 0.0
        )

        summary: Dict[str, Any] = {
            "event": "batch_run_summary",
            "run_id": batch_run_id,
            "model_version": model_version_str,
            "model_run_id": model_run_id,
            "selection_mode": selection_mode,
            "row_count": row_count,
            "row_count_scored": row_count_scored,
            "row_count_skipped": row_count_skipped,
            "class_distribution": dict(class_distribution),
            "avg_confidence": avg_confidence,
            "runtime_sec": runtime_sec,
            "started_at": _iso_utc(started_at_dt),
            "finished_at": _iso_utc(finished_at_dt),
        }

        # Always emit the summary, even on failure or empty selection.
        log_batch_run_summary(
            logger,
            run_id=summary["run_id"],
            model_version=summary["model_version"],
            model_run_id=summary["model_run_id"],
            selection_mode=summary["selection_mode"],
            row_count=summary["row_count"],
            row_count_scored=summary["row_count_scored"],
            row_count_skipped=summary["row_count_skipped"],
            class_distribution=summary["class_distribution"],
            avg_confidence=summary["avg_confidence"],
            runtime_sec=summary["runtime_sec"],
            started_at=summary["started_at"],
            finished_at=summary["finished_at"],
        )

        if db_client is not None:
            try:
                db_client.close()
            except Exception:  # noqa: BLE001 — best-effort
                pass

    if error is not None:
        raise error

    return summary


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        run_batch(args)
    except BaseException:  # noqa: BLE001 — exit code surface
        # run_batch already logged the error + summary. Non-zero exit so the
        # Cloud Run Job is marked failed.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
