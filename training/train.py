"""Fine-tune DistilBERT on ticket priority. Phase 1: local + smoke-test."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
from transformers import (
    AutoModelForSequenceClassification,
    Trainer,
    TrainingArguments,
    set_seed,
)

from training.config import (
    ARTIFACTS_DIR,
    ID2LABEL,
    LABEL2ID,
    LOCAL_DATA_PATH,
    SmokeTestConfig,
    TrainConfig,
)
from training.data_loader import load_and_tokenize
from training.evaluate import (
    compute_confusion_matrix,
    compute_metrics,
    hf_compute_metrics,
    sample_misclassifications,
    save_eval_artifacts,
)


def _make_run_id(smoke: bool) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"smoke-{ts}" if smoke else f"run-{ts}"


def train(
    data_path: str,
    config: TrainConfig,
    output_dir: Path,
    max_rows: Optional[int] = None,
) -> dict:
    """Run the fine-tuning pipeline locally and write artifacts to `output_dir`."""
    set_seed(config.seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Data
    splits = load_and_tokenize(data_path, config, max_rows=max_rows)

    # 2. Model
    model = AutoModelForSequenceClassification.from_pretrained(
        config.base_model,
        num_labels=config.num_labels,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )

    # 3. TrainingArguments — keep it portable across transformers versions.
    trainer_output = output_dir / "trainer"
    args = TrainingArguments(
        output_dir=str(trainer_output),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        weight_decay=config.weight_decay,
        logging_steps=10,
        save_strategy="no",
        report_to=[],
        seed=config.seed,
        disable_tqdm=False,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=splits.train,
        eval_dataset=splits.val,
        tokenizer=splits.tokenizer,
        compute_metrics=hf_compute_metrics,
    )

    # 4. Train
    train_result = trainer.train()

    # 5. Evaluate on held-out test set.
    preds_output = trainer.predict(splits.test)
    logits = preds_output.predictions
    y_true = preds_output.label_ids
    y_pred = np.argmax(logits, axis=-1)

    metrics = compute_metrics(y_true, y_pred)
    cm = compute_confusion_matrix(y_true, y_pred)

    # Recover original texts from the tokenized test set (we kept them in the
    # in-memory DataFrame; re-decode via the tokenizer to avoid extra plumbing).
    test_texts = splits.tokenizer.batch_decode(
        splits.test["input_ids"], skip_special_tokens=True
    )
    misclassified = sample_misclassifications(test_texts, y_true, y_pred, n=20)

    # 6. Save model + tokenizer + artifacts.
    model_dir = output_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(model_dir))
    splits.tokenizer.save_pretrained(str(model_dir))

    save_eval_artifacts(output_dir, metrics, cm, misclassified)

    with open(output_dir / "training_args.json", "w") as f:
        # Serialize our own dataclass config (HF TrainingArguments has
        # non-JSON-safe fields we don't need here).
        json.dump(asdict(config), f, indent=2)

    with open(output_dir / "train_summary.json", "w") as f:
        json.dump(
            {
                "train_runtime_sec": float(train_result.metrics.get("train_runtime", 0.0)),
                "train_loss": float(train_result.metrics.get("train_loss", 0.0)),
                "num_train_samples": len(splits.train),
                "num_val_samples": len(splits.val),
                "num_test_samples": len(splits.test),
            },
            f,
            indent=2,
        )

    return {"metrics": metrics, "confusion_matrix": cm.tolist(), "output_dir": str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Tiny local run on CPU: 100 rows, 1 epoch, bs=4, artifacts to training/artifacts/.",
    )
    parser.add_argument(
        "--data-path",
        type=str,
        default=str(LOCAL_DATA_PATH),
        help="Path or GCS URI to tickets.csv (Phase 1 uses local path).",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional run id; defaults to timestamp-based.",
    )
    parser.add_argument(
        "--output-root",
        type=str,
        default=str(ARTIFACTS_DIR),
        help="Root directory for local run outputs.",
    )
    args = parser.parse_args()

    if args.smoke_test:
        config = SmokeTestConfig()
        max_rows = config.max_rows
    else:
        config = TrainConfig()
        max_rows = None

    run_id = args.run_id or _make_run_id(smoke=args.smoke_test)
    output_dir = Path(args.output_root) / run_id

    print(f"[train] run_id={run_id}")
    print(f"[train] data_path={args.data_path}")
    print(f"[train] output_dir={output_dir}")
    print(f"[train] smoke_test={args.smoke_test}")

    result = train(args.data_path, config, output_dir, max_rows=max_rows)

    print("\n[train] === Final metrics ===")
    print(f"  accuracy : {result['metrics']['accuracy']:.4f}")
    print(f"  macro_f1 : {result['metrics']['macro_f1']:.4f}")
    for cls, d in result["metrics"]["per_class"].items():
        print(
            f"  {cls:>7}: precision={d['precision']:.3f} recall={d['recall']:.3f} "
            f"f1={d['f1']:.3f} support={d['support']}"
        )
    print(f"\n[train] artifacts written to: {result['output_dir']}")


if __name__ == "__main__":
    main()
