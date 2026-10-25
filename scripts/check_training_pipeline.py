"""Synthetic end-to-end checks; optional private bundle checks are read-only."""
import copy
import csv
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from models.data import load_group,fit_scaler,transform,inverse_targets,metrics,data_fingerprint
from train_models import train_group,evaluate_test
from predict import predict_csv

ROOT=Path(__file__).resolve().parents[1]

def fixture(directory, group):
    records, manifest = [], []
    for split_index,split in enumerate(("train","validation","test")):
        for j in range(3):
            index=split_index*3+j
            row=dict(sample_id=f"{group}-{index}",source_file="synthetic_training.csv",source_row=index+2,
                     group=group,p_system=index+1,p_ocean=0,ff=-index-1,f=index+3,q=-0.1*index)
            for c,offset in (("x",0),("y",10),("cpress",20)):
                for node in range(1,222):
                    row[f"{c}{node}"]=node+offset+index
            records.append(row)
            manifest.append({k:row[k] for k in ("sample_id","source_file","source_row","group","p_system","p_ocean")})
            manifest[-1].update(split=split,split_group_id=row["sample_id"],eligibility="eligible")
    for name,rows in ((f"{group}_all.csv",records),("split_manifest.csv",manifest)):
        with (directory/name).open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

def main(device='cpu', check_private_data=False):
    from models.runtime import check_device
    results={"environment":check_device(device)}
    with tempfile.TemporaryDirectory(prefix="seal_pipeline_") as temp:
        root=Path(temp)
        for group in ("positive","negative"):
            directory=root/group
            directory.mkdir()
            fixture(directory,group)
            parts=load_group(directory,group)
            assert parts["train"]["nodes"].shape == (3,221,3)
            np.testing.assert_array_equal(parts["train"]["nodes"][0,[0,1,220]],
                                          [[1,11,21],[2,12,22],[221,231,241]])
            np.testing.assert_array_equal(parts["train"]["targets"][0],[-1,3,0])
            scaler=fit_scaler(parts["train"])
            assert scaler["pressures"]["scale"][1] == 1
            np.testing.assert_allclose(scaler["nodes"]["mean"],[112,122,132])
            for split in parts:
                converted=transform(parts[split],scaler)
                np.testing.assert_allclose(inverse_targets(converted["targets"],scaler),
                                           parts[split]["targets"],atol=1e-6)
            changed=copy.deepcopy(parts)
            changed["validation"]["targets"]+=100000
            changed["test"]["nodes"]+=100000
            assert fit_scaler(changed["train"]) == scaler
            try:
                fit_scaler(parts["validation"])
                raise AssertionError("Validation scaler fit allowed")
            except ValueError:
                pass
            constant=metrics(np.ones((3,3)),np.ones((3,3)))
            assert all(v["R2"] is None and v["MAE"]==0 for v in constant.values())
            config=dict(seed=42,epochs=2,batch_size=3,patience=2,learning_rate=0.001,weight_decay=0,
                        model=dict(d_model=8,nhead=2,num_layers=1,dim_feedforward=16,dropout=0))
            run=directory/"run"
            meta=train_group(parts,run,config,data_fingerprint(directory,group),group,device)
            assert meta["scaler"]["fit_sample_ids"] == parts["train"]["sample_ids"]
            assert not (run/"test_metrics.json").exists()
            scores=evaluate_test(directory,run,device)
            assert set(scores["transformer"]) == {"ff","f","q"}
            assert (run/"best.pt").is_file()
            # Inference must not require target columns, and sums forces only after inversion.
            feature_path = directory/"features.csv"
            with (directory/f"{group}_all.csv").open(encoding="utf-8-sig",newline="") as f:
                feature_rows = list(csv.DictReader(f))[3:6]
            for row in feature_rows:
                for target in ("ff","f","q"):
                    del row[target]
            with feature_path.open("w",encoding="utf-8-sig",newline="") as f:
                writer = csv.DictWriter(f,fieldnames=list(feature_rows[0]))
                writer.writeheader()
                writer.writerows(feature_rows)
            inferred = predict_csv(run,feature_path,directory/"predictions.csv",device,3)
            with (run/"validation_predictions.csv").open(encoding="utf-8-sig",newline="") as f:
                saved = list(csv.DictReader(f))
            np.testing.assert_allclose(inferred,[[float(r[t+"_prediction"]) for t in ("ff","f","q")] for r in saved],atol=1e-6)
            with (directory/"predictions.csv").open(encoding="utf-8-sig",newline="") as f:
                saved_inference = list(csv.DictReader(f))
            np.testing.assert_allclose([float(r["friction_prediction"]) for r in saved_inference],inferred[:,0]+inferred[:,1])

            try:
                evaluate_test(directory,run,device)
                raise AssertionError("Repeated final evaluation allowed")
            except ValueError:
                pass
            # An association spanning two splits must be rejected.
            path=directory/"split_manifest.csv"
            original=path.read_text(encoding="utf-8-sig")
            rows=list(csv.DictReader(original.splitlines()))
            rows[3]["split_group_id"]=rows[0]["split_group_id"]
            with path.open("w",encoding="utf-8-sig",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            try:
                load_group(directory,group)
                raise AssertionError("Cross-partition association accepted")
            except ValueError as exc:
                assert "leaks" in str(exc)
            # Exercise the review guard with synthetic records, without private files.
            for row in rows:
                row["eligibility"] = "pending_review"
            with path.open("w",encoding="utf-8-sig",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            try:
                load_group(directory,group)
                raise AssertionError("Pending-review data entered training")
            except ValueError as exc:
                assert "awaiting review" in str(exc)
            results[group]="synthetic training, checkpoint reload, inference, review and leakage checks passed"
    if check_private_data:
        import hashlib
        for group in ("positive","negative"):
            try:
                load_group(ROOT/"outputs/data_preparation",group)
                raise AssertionError("Pending-review data entered training")
            except ValueError as exc:
                assert "awaiting review" in str(exc)
        bundle = ROOT/"outputs/training_data/v2"
        bundle_meta = json.loads((bundle/"dataset_metadata.json").read_text(encoding="utf-8"))
        for name,digest in bundle_meta["files"].items():
            assert hashlib.sha256((bundle/name).read_bytes()).hexdigest() == digest
        excluded = set(bundle_meta["excluded_sample_ids"])
        for group in ("positive","negative"):
            parts = load_group(bundle,group)
            assert {s:len(p["sample_ids"]) for s,p in parts.items()} == bundle_meta["counts"][group]
            assert not excluded.intersection(sid for p in parts.values() for sid in p["sample_ids"])
            with (bundle/"split_manifest.csv").open(encoding="utf-8-sig",newline="") as f:
                selected = [r for r in csv.DictReader(f) if r["group"]==group and r["eligibility"]=="provisional"]
            node_ids = [r for r in selected if group=="negative" and float(r["p_system"])==65 and 60<=float(r["p_ocean"])<=65]
            if node_ids:
                assert len(node_ids)==11 and {r["split"] for r in node_ids}=={"train"}
        results["private_data"]="v2 hashes, counts, exclusions, review guard and grouped nodes passed; read-only"
    else:
        results["private_data"]="not requested; no research data required"
    results["inference"]="target-free CSV predictions equal saved validation predictions; friction = ff + f"
    destination=ROOT/"outputs/model_checks"
    destination.mkdir(parents=True,exist_ok=True)
    (destination/"pipeline_checks.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(json.dumps(results,indent=2))

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--device",choices=("cpu","cuda"),default="cpu")
    parser.add_argument("--check-private-data",action="store_true",help="Also verify the local v2 bundle without rebuilding it")
    args=parser.parse_args()
    main(args.device,args.check_private_data)
