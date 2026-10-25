"""Build a versioned experimental dataset from the latest cleaning decisions."""
import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/data_preparation"
REVIEW = BASE / "evidence_review"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")

def build(output):
    manifest = read(REVIEW / "split_manifest_grouped.csv")
    decisions = {r["sample_id"]: r for r in read(REVIEW / "cleaning_manifest.csv")}
    rules_path = ROOT / "scripts/data_exclusions.json"
    exclusions = json.loads(rules_path.read_text(encoding="utf-8"))
    excluded_ids = {r["sample_id"] for r in exclusions["records"]}
    if {r["sample_id"] for r in manifest if r["eligibility"] == "excluded"} != excluded_ids:
        raise ValueError("Cleaning manifest does not match current exclusion rules")
    sources = [REVIEW / "split_manifest_grouped.csv", REVIEW / "cleaning_manifest.csv", rules_path]
    sources += [BASE / f"{g}_all.csv" for g in ("positive", "negative")]
    fingerprints = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    contents, counts = {}, {}
    for row in manifest:
        decision = decisions[row["sample_id"]]
        if row["sample_id"] not in excluded_ids:
            if decision["numerical_status"] != "pass" or decision["evidence_status"] != "no_detected_data_error":
                raise ValueError(f"New unresolved data error: {row['sample_id']}")
            if row["eligibility"] != "pending_review":
                raise ValueError("Unexpected source eligibility")
            row["source_eligibility"] = row["eligibility"]
            row["eligibility"] = "provisional"
        else:
            row["source_eligibility"] = row["eligibility"]
    contents["split_manifest.csv"] = csv_bytes(manifest)
    for group in ("positive", "negative"):
        records = read(BASE / f"{group}_all.csv")
        lookup = {r["sample_id"]: r for r in records}
        chosen = [r for r in manifest if r["group"] == group]
        if len(lookup) != len(records) or set(lookup) != {r["sample_id"] for r in chosen}:
            raise ValueError("Manifest and source IDs differ")
        contents[f"{group}_all.csv"] = (BASE / f"{group}_all.csv").read_bytes()
        counts[group] = {}
        for split in ("train", "validation", "test"):
            selected = [lookup[r["sample_id"]] for r in chosen if r["split"] == split and r["eligibility"] == "provisional"]
            if not selected:
                raise ValueError("Empty partition")
            counts[group][split] = len(selected)
            contents[f"{group}/{split}.csv"] = csv_bytes(selected)
    metadata = {
        "usage": "user_authorized_experiment",
        "basis": "User requested GPU training code after evidence review and temporary exclusion of three records.",
        "physical_review_status": "not_fully_verified",
        "target_order": ["ff", "f", "q"],
        "counts": counts,
        "excluded_sample_ids": sorted(excluded_ids),
        "source_fingerprints": fingerprints,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        "notes": "Provisional means selected for the requested experiment, not certified solver convergence."
    }
    contents["dataset_metadata.json"] = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = output.resolve()
    if not output.is_relative_to(ROOT / "outputs/training_data"):
        raise ValueError("Bundle output must be inside outputs/training_data")
    if output.exists():
        if any(not (output/name).is_file() or (output/name).read_bytes() != data for name, data in contents.items()):
            raise ValueError("Bundle differs; choose a new version directory instead of overwriting")
    else:
        output.mkdir(parents=True)
        for name, data in contents.items():
            dest = output/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    if fingerprints != {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}:
        raise ValueError("Source data changed during export")
    print(json.dumps({"output": str(output), "counts": counts, "excluded": len(excluded_ids)}, ensure_ascii=True))
    return metadata

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"outputs/training_data/v2")
    build(parser.parse_args().output)
