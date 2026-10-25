"""Verify saved outputs independently and rerun the complete preparation."""
import csv, hashlib, json, subprocess, sys
from collections import defaultdict, Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/data_preparation"
def read(p):
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    sources = list((ROOT / "src").rglob("*_training.csv"))
    source_hashes = {str(p): sha(p) for p in sources}
    snapshots = {p.name: sha(p) for p in OUT.iterdir() if p.is_file() and p.name != "verification.json"}
    subprocess.run([sys.executable, str(ROOT / "scripts/prepare_data.py")], check=True, stdout=subprocess.DEVNULL)
    assert snapshots == {name: sha(OUT / name) for name in snapshots}
    assert source_hashes == {str(p): sha(p) for p in sources}
    manifest = read(OUT / "split_manifest.csv")
    lookup = {r["sample_id"]: r for r in manifest}
    assert len(lookup) == len(manifest)
    buckets = [defaultdict(set) for _ in range(3)]
    companions = []
    for group in ("positive", "negative"):
        merged = read(OUT / f"{group}_all.csv")
        assert len(merged) == sum(r["group"] == group for r in manifest)
        for r in merged:
            m = lookup[r["sample_id"]]
            inputs = tuple(float(r[c]) for c in ["p_system", "p_ocean"] + [f"{c}{i}" for c in ("x","y","cpress") for i in range(1,222)])
            for bucket, key in zip(buckets, [(group,*inputs[:2]), (group,*inputs), m["split_group_id"]]):
                bucket[key].add(m["split"])
        for source in sorted((ROOT / "src" / group).rglob("*_training.csv")):
            companion = source.with_name(source.name.replace("_training.csv", ".csv"))
            raw = read(companion)
            expected = ["p_system(MPa)","p_ocean(MPa)","ff(N)","f(N)","q(mm3/s)"]
            assert raw and all(c in raw[0] for c in expected)
            mapping = defaultdict(list)
            for r in raw:
                mapping[(float(r[expected[0]]),float(r[expected[1]]))].append(r)
            matched = 0
            for r in read(source):
                matches = mapping[(float(r["p_system"]),float(r["p_ocean"]))]
                assert len(matches) == 1
                assert all(float(r[t]) == float(matches[0][u]) for t,u in zip(("ff","f","q"),expected[2:]))
                matched += 1
            companions.append(dict(path=companion.relative_to(ROOT).as_posix(), rows=len(raw), matched=matched,
                                   sha256=sha(companion), units=expected, velocities=sorted({r.get("u(m/s)","") for r in raw})))
    assert all(len(v) == 1 for b in buckets for v in b.values())
    totals = Counter(r["eligibility"] for r in manifest)
    assert sum(totals.values()) == len(manifest)
    result = dict(full_rerun_byte_identical=True, source_hashes_unchanged=True, independent_no_leakage=True,
                  total_rows=len(manifest), eligibility=dict(totals), companion_checks=companions)
    (OUT / "verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "companion_checks"},ensure_ascii=False))
    print("Companion files checked:",len(companions))
if __name__ == "__main__":
    main()
