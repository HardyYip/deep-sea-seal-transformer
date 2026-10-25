"""Check user exclusions, source immutability, partition preservation and reruns."""
import csv,hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"outputs/data_preparation/evidence_review"
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def main():
    rules=json.loads((ROOT/"scripts/data_exclusions.json").read_text(encoding="utf-8"))
    ids={r["sample_id"] for r in rules["records"]}
    assert len(ids)==3
    originals=[p for p in (ROOT/"src").rglob("*") if p.is_file()]
    originals += [ROOT/"outputs/data_preparation"/n for n in ("positive_all.csv","negative_all.csv","split_manifest.csv")]
    before_hashes={str(p):sha(p) for p in originals}
    baseline={r["sample_id"]:r for r in read(OUT/"split_manifest_grouped.csv")}
    subprocess.run([sys.executable,str(ROOT/"scripts/finalize_cleaning_review.py")],check=True,stdout=subprocess.DEVNULL)
    current={r["sample_id"]:r for r in read(OUT/"split_manifest_grouped.csv")}
    assert set(current)==set(baseline)
    assert {sid for sid,r in current.items() if r["eligibility"]=="excluded"}==ids
    for sid,old in baseline.items():
        new=current[sid]
        for column,value in old.items():
            if sid in ids and column in ("eligibility","exclusion_reason","exclusion_temporary"):continue
            assert new[column]==value,(sid,column)
    for sid in ids:
        assert current[sid]["exclusion_reason"]==rules["reason"]
        assert current[sid]["exclusion_temporary"]=="true"
    cleaning={r["sample_id"]:r for r in read(OUT/"cleaning_manifest.csv")}
    assert set(cleaning)==set(current)
    assert {sid for sid,r in cleaning.items() if r["eligibility"]=="excluded"}==ids
    for sid in current:
        assert all(cleaning[sid][k]==v for k,v in current[sid].items())
    verification=json.loads((OUT/"cleaning_verification.json").read_text(encoding="utf-8"))
    assert verification["retained_split_counts"]=={"positive":{"train":738,"validation":158,"test":153},
                                                 "negative":{"train":740,"validation":152,"test":149}}
    assert verification["retained_records"]==2090 and verification["excluded_records"]==3
    tracked=[OUT/n for n in ("split_manifest_grouped.csv","cleaning_manifest.csv","cleaning_verification.json","cleaning_review.md")]
    tracked += [ROOT/"outputs/data_preparation/data_audit.md"]
    first={str(p):sha(p) for p in tracked}
    subprocess.run([sys.executable,str(ROOT/"scripts/finalize_cleaning_review.py")],check=True,stdout=subprocess.DEVNULL)
    assert first=={str(p):sha(p) for p in tracked},"Rerun changed output"
    assert before_hashes=={str(p):sha(p) for p in originals},"Original data changed"
    result={"excluded_records":3,"remaining_negative":1041,"remaining_positive":1049,
            "only_requested_eligibility_changes":True,"all_partition_assignments_unchanged":True,
            "original_files_and_merged_data_unchanged":True,"protected_file_count":len(originals),
            "rerun_byte_identical":True,
            "retained_split_counts":verification["retained_split_counts"]}
    (OUT/"exclusion_verification.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
if __name__=="__main__":main()
