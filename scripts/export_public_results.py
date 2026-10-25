"""Export an allowlist of aggregate frozen results; never run model evaluation.

Requires the private local run_004/v2 artifacts. Published summaries contain no
sample IDs, per-sample predictions, fitted scalers, source paths or model weights.
"""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export():
    bundle = ROOT / "outputs/training_data/v2"
    dataset = json.loads((bundle / "dataset_metadata.json").read_text(encoding="utf-8"))
    destination = ROOT / "docs/results"
    destination.mkdir(parents=True, exist_ok=True)
    summary = {
        "run": "run_004",
        "dataset_version": "v2",
        "data_use": "user_authorized_experiment",
        "physical_review_status": "not_fully_verified",
        "target_order": ["ff", "f", "q"],
        "units": {"ff": "N", "f": "N", "q": "mm^3/s"},
        "counts": dataset["counts"],
        "temporary_exclusions": 3,
        "export_method": "Aggregate fields copied from existing frozen files; no model fitting, inference or test rescoring.",
        "groups": {},
    }
    for group in ("positive", "negative"):
        run = ROOT / "outputs/training/run_004" / group
        metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
        scores = json.loads((run / "test_metrics.json").read_text(encoding="utf-8"))
        config = json.loads((ROOT / f"scripts/training_{group}.json").read_text(encoding="utf-8"))
        assert metadata["run_status"] == "complete" and metadata["test_evaluated"]
        assert metadata["target_order"] == summary["target_order"]
        assert metadata["config"] == config
        assert sha(run / "best.pt") == metadata["checkpoint_sha256"]
        for name, digest in metadata["fingerprint"].items():
            assert sha(bundle / name) == digest
        with (run / "history.csv").open(encoding="utf-8-sig", newline="") as stream:
            history = list(csv.DictReader(stream))
        columns = ["epoch", "train_standardized_mse", "validation_standardized_mse"]
        assert set(history[0]) == set(columns)
        with (destination / f"{group}_history.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(history)
        summary["groups"][group] = {
            "config": config,
            "best_epoch": metadata["best_epoch"],
            "completed_epochs": len(history),
            "torch_version": metadata["torch_version"],
            "device": metadata["device"],
            "validation_metrics": metadata["validation_metrics"],
            "validation_training_mean_baseline": metadata["validation_mean_baseline"],
            "test_metrics": scores,
            "checkpoint_sha256": metadata["checkpoint_sha256"],
            "dataset_fingerprints": metadata["fingerprint"],
            "source_file_sha256": {name: sha(run / name) for name in ("metadata.json", "test_metrics.json", "history.csv")},
        }
    (destination / "run_004_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Exported frozen aggregate results to docs/results; no test evaluation performed.")


if __name__ == "__main__":
    export()
