"""Rebuild portfolio figures from published aggregate results only."""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    results = ROOT / "docs/results"
    output = ROOT / "docs/figures"
    output.mkdir(parents=True, exist_ok=True)
    summary = json.loads((results / "run_004_summary.json").read_text(encoding="utf-8"))
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "seal-transformer-run-004"})
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 3.8), constrained_layout=True)
    for ax, group in zip(axes, ("positive", "negative")):
        with (results / f"{group}_history.csv").open(encoding="utf-8", newline="") as stream:
            history = list(csv.DictReader(stream))
        epochs = [int(r["epoch"]) for r in history]
        for key, label, color in (("train_standardized_mse", "Train", "#2563eb"),
                                  ("validation_standardized_mse", "Validation", "#ea580c")):
            ax.plot(epochs, [float(r[key]) for r in history], label=label, color=color, linewidth=1.6)
        best = summary["groups"][group]["best_epoch"]
        ax.axvline(best, color="#64748b", linestyle="--", linewidth=1, label=f"Selected epoch {best}")
        ax.set(title=group.capitalize(), xlabel="Epoch", ylabel="Standardized MSE", yscale="log")
        ax.grid(alpha=0.18)
        ax.legend(frameon=False, fontsize=8)
    fig.suptitle("Frozen run_004 | checkpoint selection uses validation only", fontsize=12)
    fig.savefig(output / "learning_curves.svg", metadata={"Date": None})
    fig.savefig(output / "learning_curves.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.4, 3.8), constrained_layout=True)
    labels, reductions = [], []
    for group in ("positive", "negative"):
        scores = summary["groups"][group]["test_metrics"]
        for target in summary["target_order"]:
            labels.append(f"{group}\n{target}")
            reductions.append(100 * (1 - scores["transformer"][target]["RMSE"] /
                                      scores["training_mean_baseline"][target]["RMSE"]))
    bars = ax.bar(np.arange(6), reductions, color=["#2563eb"]*3 + ["#0f766e"]*3, width=0.6)
    ax.bar_label(bars, labels=[f"{v:.1f}%" for v in reductions], padding=4)
    ax.set(xticks=np.arange(6), xticklabels=labels, ylim=(0, 108),
           ylabel="RMSE reduction (%)", title="Frozen test | improvement over the training-mean predictor")
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    fig.savefig(output / "test_rmse_reduction.svg", metadata={"Date": None})
    fig.savefig(output / "test_rmse_reduction.png", dpi=180)
    plt.close(fig)
    for svg in output.glob("*.svg"):
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    print("Rebuilt aggregate learning and test comparison figures; no private data or model evaluation used.")


if __name__ == "__main__":
    main()
