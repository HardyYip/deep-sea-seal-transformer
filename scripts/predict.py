"""Predict ff/f/q in original scale using a completed single-group run."""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
from models.data import inverse_targets
from models.runtime import check_device
from models.seal_transformer import SealTransformer

def predict_csv(run, source, output, device, batch_size=32):
    run, source, output = Path(run), Path(source), Path(output)
    if output.exists() or output.resolve() == source.resolve():
        raise ValueError("Choose a new output path; input/existing output will not be overwritten")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    metadata = json.loads((run/"metadata.json").read_text(encoding="utf-8"))
    if metadata.get("run_status") != "complete":
        raise ValueError("Run has not finished")
    if hashlib.sha256((run/"best.pt").read_bytes()).hexdigest() != metadata["checkpoint_sha256"]:
        raise ValueError("Checkpoint fingerprint mismatch")
    with source.open(encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        header=reader.fieldnames or []
        required=["p_system","p_ocean"]+[f"{c}{i}" for c in ("x","y","cpress") for i in range(1,222)]
        if len(header)!=len(set(header)) or not set(required)<=set(header):
            raise ValueError("Input requires two pressures plus x1..221, y1..221, cpress1..221, with unique headers")
        rows=list(reader)
    if not rows:
        raise ValueError("Input CSV is empty")
    if any(r.get("group",metadata["group"])!=metadata["group"] for r in rows):
        raise ValueError("Input group does not match trained model")
    nodes=np.stack([np.array([[float(r[f"{c}{i}"]) for i in range(1,222)] for r in rows])
                    for c in ("x","y","cpress")],axis=-1)
    pressures=np.array([[float(r[c]) for c in ("p_system","p_ocean")] for r in rows])
    if not np.isfinite(nodes).all() or not np.isfinite(pressures).all():
        raise ValueError("Input contains non-finite values")
    scaler=metadata["scaler"]
    nodes=((nodes-np.array(scaler["nodes"]["mean"]))/np.array(scaler["nodes"]["scale"])).astype(np.float32)
    pressures=((pressures-np.array(scaler["pressures"]["mean"]))/np.array(scaler["pressures"]["scale"])).astype(np.float32)
    model=SealTransformer(**metadata["config"]["model"]).to(device)
    model.load_state_dict(torch.load(run/"best.pt",map_location=device,weights_only=True))
    model.eval()
    batches=[]
    with torch.no_grad():
        for start in range(0,len(rows),batch_size):
            batches.append(model(torch.from_numpy(nodes[start:start+batch_size]).to(device),
                                 torch.from_numpy(pressures[start:start+batch_size]).to(device)).cpu().numpy())
    predicted=inverse_targets(np.concatenate(batches),scaler)
    if not np.isfinite(predicted).all():
        raise ValueError("Prediction produced non-finite values")
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.writer(f)
        writer.writerow(["input_record","sample_id","group","ff_prediction","f_prediction","q_prediction","friction_prediction"])
        writer.writerows([i,r.get("sample_id",""),metadata["group"],*p,p[0]+p[1]]
                         for i,(r,p) in enumerate(zip(rows,predicted),1))
    return predicted

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--device",choices=("cpu","cuda"),default="cuda")
    parser.add_argument("--batch-size",type=int,default=32)
    args=parser.parse_args()
    print(json.dumps(check_device(args.device),indent=2))
    values=predict_csv(args.run,args.input,args.output,args.device,args.batch_size)
    print(f"Saved {len(values)} predictions to {args.output}")
