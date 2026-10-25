"""Manifest-aligned arrays and training-only standardization."""
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

TARGETS = ("ff", "f", "q")


def read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_group(directory, group):
    if group not in ("positive", "negative"):
        raise ValueError("Unknown group")
    directory = Path(directory)
    all_rows = read_rows(directory / f"{group}_all.csv")
    manifest = [r for r in read_rows(directory / "split_manifest.csv") if r["group"] == group]
    lookup = {r["sample_id"]: r for r in all_rows}
    if len(lookup) != len(all_rows) or len({r["sample_id"] for r in manifest}) != len(manifest):
        raise ValueError("Duplicate sample_id")
    if set(lookup) != {r["sample_id"] for r in manifest}:
        raise ValueError("Manifest and merged data differ")
    if not manifest:
        raise ValueError("Empty group")
    if any(r["eligibility"] == "provisional" for r in manifest):
        metadata = json.loads((directory / "dataset_metadata.json").read_text(encoding="utf-8"))
        if metadata["usage"] != "user_authorized_experiment":
            raise ValueError("Missing experimental dataset provenance")
        for name in ("split_manifest.csv", f"{group}_all.csv"):
            if hashlib.sha256((directory/name).read_bytes()).hexdigest() != metadata["files"][name]:
                raise ValueError("Experimental dataset fingerprint mismatch")
    accepted = ("eligible", "provisional")
    pending = [r for r in manifest if r["eligibility"] not in (*accepted, "excluded", "quarantine")]
    if pending:
        raise ValueError(f"{group}: {len(pending)} records awaiting review; training is blocked")
    partitions = {}
    for split in ("train", "validation", "test"):
        chosen = [r for r in manifest if r["split"] == split and r["eligibility"] in accepted]
        if not chosen:
            raise ValueError(f"Empty eligible partition: {split}")
        records = [lookup[r["sample_id"]] for r in chosen]
        for m, r in zip(chosen, records):
            if r["group"] != group or any(m[c] != r[c] for c in ("source_file", "source_row", "p_system", "p_ocean")):
                raise ValueError("Manifest provenance/condition mismatch")
        nodes = np.stack([np.array([[float(r[f"{c}{i}"]) for i in range(1,222)] for r in records])
                          for c in ("x", "y", "cpress")], axis=-1)
        pressures = np.array([[float(r[c]) for c in ("p_system","p_ocean")] for r in records])
        targets = np.array([[float(r[c]) for c in TARGETS] for r in records])
        if not all(np.isfinite(a).all() for a in (nodes, pressures, targets)):
            raise ValueError("Non-finite model data")
        partitions[split] = dict(nodes=nodes, pressures=pressures, targets=targets,
                                 sample_ids=[r["sample_id"] for r in chosen], split=split)
    # Revalidate association, pressure condition, and identical-input isolation.
    seen = {}
    for r in manifest:
        if r["split"] not in partitions:
            raise ValueError("Unknown split")
        original = lookup[r["sample_id"]]
        keys = [("component", r["split_group_id"]),
                ("condition", float(r["p_system"]), float(r["p_ocean"]))]
        if r["eligibility"] in accepted:
            columns = ["p_system","p_ocean"] + [f"{c}{i}" for c in ("x","y","cpress") for i in range(1,222)]
            keys.append(("inputs", *(float(original[c]) for c in columns)))
            keys.append(("nodes", *(float(original[c]) for c in columns[2:])))
        for key in keys:
            if key in seen and seen[key] != r["split"]:
                raise ValueError("Manifest leaks related records across partitions")
            seen[key] = r["split"]
    return partitions


def fit_scaler(train):
    if train["split"] != "train":
        raise ValueError("Scaler must be fitted on train")
    result = {"fit_sample_ids": train["sample_ids"], "target_order": list(TARGETS),
              "node_channels": ["x","y","cpress"], "pressure_order": ["p_system","p_ocean"]}
    for key, axis in (("nodes",(0,1)),("pressures",0),("targets",0)):
        mean = train[key].mean(axis=axis)
        scale = train[key].std(axis=axis)
        scale = np.where(scale == 0, 1, scale)
        result[key] = {"mean": mean.tolist(), "scale": scale.tolist()}
    return result


def transform(partition, scaler):
    return {key: ((partition[key] - np.array(scaler[key]["mean"])) /
                  np.array(scaler[key]["scale"])).astype(np.float32)
            for key in ("nodes","pressures","targets")}


def inverse_targets(values, scaler):
    return np.asarray(values) * np.array(scaler["targets"]["scale"]) + np.array(scaler["targets"]["mean"])


def metrics(actual, prediction):
    result = {}
    for i, target in enumerate(TARGETS):
        delta = prediction[:,i] - actual[:,i]
        denominator = np.square(actual[:,i] - actual[:,i].mean()).sum()
        result[target] = {"MAE": float(np.abs(delta).mean()),
                          "RMSE": float(np.sqrt(np.square(delta).mean())),
                          "R2": float(1 - np.square(delta).sum()/denominator) if denominator > 0 else None}
    return result


def data_fingerprint(directory, group):
    directory = Path(directory)
    names = ["split_manifest.csv", f"{group}_all.csv"]
    if (directory/"dataset_metadata.json").exists():
        names.append("dataset_metadata.json")
    return {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}
