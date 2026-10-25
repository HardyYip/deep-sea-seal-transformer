"""Train the selected experimental dataset; test scoring is an explicit separate command."""
import argparse
import hashlib
import time
import csv
import json
import math
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset
from models.seal_transformer import SealTransformer
from models.runtime import check_device
from models.data import load_group, fit_scaler, transform, inverse_targets, metrics, data_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def loader(values, batch_size, shuffle=False):
    dataset = TensorDataset(*(torch.from_numpy(values[k]) for k in ("nodes","pressures","targets")))
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=0)


def predict(model, values, batch_size, device):
    model.eval()
    with torch.no_grad():
        return np.concatenate([model(n.to(device), p.to(device)).cpu().numpy()
                               for n,p,_ in loader(values,batch_size)])


def save_predictions(path, partition, prediction):
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_id","ff_actual","f_actual","q_actual","ff_prediction","f_prediction","q_prediction"])
        writer.writerows([sid,*actual,*pred] for sid,actual,pred in zip(partition["sample_ids"],partition["targets"],prediction))


def train_group(partitions, output, config, fingerprint, group, device):
    if output.exists():
        raise ValueError(f"Output already exists: {output}; use a new run directory")
    torch.manual_seed(config["seed"])
    np.random.seed(config["seed"])
    random.seed(config["seed"])
    if device == "cpu":
        torch.set_num_threads(min(4,torch.get_num_threads()))
    scaler = fit_scaler(partitions["train"])
    values = {s:transform(partitions[s],scaler) for s in ("train","validation")}
    model = SealTransformer(**config["model"]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(),lr=config["learning_rate"],weight_decay=config["weight_decay"])
    loss_fn = torch.nn.MSELoss()
    best, stale, history = math.inf, 0, []
    output.mkdir(parents=True)
    started = time.perf_counter()
    print(f"{group}: train={len(values['train']['nodes'])}, validation={len(values['validation']['nodes'])}, device={device}", flush=True)
    baseline = np.broadcast_to(partitions["train"]["targets"].mean(axis=0), partitions["validation"]["targets"].shape)
    metadata = {"group":group,"config":config,"scaler":scaler,"fingerprint":fingerprint,
                "validation_mean_baseline":metrics(partitions["validation"]["targets"],baseline),
                "torch_version":str(torch.__version__),"device":device,"test_evaluated":False,
                "run_status":"running","target_order":["ff","f","q"],
                "data_use":"user_authorized_experiment" if "dataset_metadata.json" in fingerprint else "eligible_dataset",
                "physical_review_status":"not_fully_verified" if "dataset_metadata.json" in fingerprint else "unspecified"}
    (output/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    for epoch in range(1,config["epochs"]+1):
        model.train()
        total = 0.0
        for nodes, pressures, targets in loader(values["train"],config["batch_size"],True):
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(nodes.to(device),pressures.to(device)),targets.to(device))
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.0,error_if_nonfinite=True)
            optimizer.step()
            total += loss.item()*len(nodes)
        prediction = predict(model,values["validation"],config["batch_size"],device)
        val_loss = float(np.square(prediction-values["validation"]["targets"]).mean())
        if not math.isfinite(val_loss):
            raise ValueError("Non-finite validation loss")
        history.append({"epoch":epoch,"train_standardized_mse":total/len(values["train"]["nodes"]),
                        "validation_standardized_mse":val_loss})
        if val_loss < best:
            best, stale = val_loss, 0
            metadata["best_epoch"] = epoch
            torch.save(model.state_dict(),output/"best.pt")
        else:
            stale += 1
        metadata["history"] = history
        metadata["elapsed_seconds"] = time.perf_counter() - started
        (output/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
        print(f"{group} epoch {epoch}/{config['epochs']} "
              f"train_mse={history[-1]['train_standardized_mse']:.6f} "
              f"val_mse={val_loss:.6f} best_epoch={metadata['best_epoch']} "
              f"elapsed={metadata['elapsed_seconds']:.1f}s", flush=True)
        if stale >= config["patience"]:
            print(f"{group}: early stopping", flush=True)
            break
    model.load_state_dict(torch.load(output/"best.pt",map_location=device,weights_only=True))
    prediction = inverse_targets(predict(model,values["validation"],config["batch_size"],device),scaler)
    metadata["validation_metrics"] = metrics(partitions["validation"]["targets"],prediction)
    metadata["history"] = history
    metadata["run_status"] = "complete"
    metadata["checkpoint_sha256"] = hashlib.sha256((output/"best.pt").read_bytes()).hexdigest()
    (output/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    save_predictions(output/"validation_predictions.csv",partitions["validation"],prediction)
    with (output/"history.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer = csv.DictWriter(f,fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    return metadata


def evaluate_test(directory, run, device):
    metadata = json.loads((run/"metadata.json").read_text(encoding="utf-8"))
    group = metadata["group"]
    if metadata.get("run_status") != "complete":
        raise ValueError("Training run is incomplete; do not evaluate test")
    if hashlib.sha256((run/"best.pt").read_bytes()).hexdigest() != metadata["checkpoint_sha256"]:
        raise ValueError("Checkpoint changed since training")
    if data_fingerprint(directory,group) != metadata["fingerprint"]:
        raise ValueError("Dataset or manifest changed since training")
    if (run/"test_metrics.json").exists():
        raise ValueError("Test already evaluated; preserve the existing final result")
    partitions = load_group(directory,group)
    if metadata["scaler"]["fit_sample_ids"] != partitions["train"]["sample_ids"]:
        raise ValueError("Scaler training IDs do not match manifest")
    model = SealTransformer(**metadata["config"]["model"]).to(device)
    model.load_state_dict(torch.load(run/"best.pt",map_location=device,weights_only=True))
    values = transform(partitions["test"],metadata["scaler"])
    prediction = inverse_targets(predict(model,values,metadata["config"]["batch_size"],device),metadata["scaler"])
    scores = metrics(partitions["test"]["targets"],prediction)
    baseline = np.broadcast_to(np.array(metadata["scaler"]["targets"]["mean"]),prediction.shape)
    result = {"transformer":scores,"training_mean_baseline":metrics(partitions["test"]["targets"],baseline)}
    (run/"test_metrics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    save_predictions(run/"test_predictions.csv",partitions["test"],prediction)
    metadata["test_evaluated"] = True
    (run/"metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=ROOT/"scripts/training.json")
    parser.add_argument("--data",type=Path,default=ROOT/"outputs/training_data/v2")
    parser.add_argument("--output",type=Path,default=ROOT/"outputs/training/run_001")
    parser.add_argument("--group",choices=("positive","negative","both"),default="both")
    parser.add_argument("--device",choices=("cpu","cuda"),default="cuda")
    parser.add_argument("--evaluate-test",type=Path,help="Explicit final evaluation of a frozen single-group run")
    parser.add_argument("--check-data",action="store_true",help="Validate device and dataset without fitting or training")
    parser.add_argument("--batch-size",type=int)
    parser.add_argument("--epochs",type=int)
    args = parser.parse_args()
    if args.check_data and args.evaluate_test:
        parser.error("--check-data and --evaluate-test are mutually exclusive")
    print(json.dumps(check_device(args.device),indent=2),flush=True)
    if args.evaluate_test:
        print(json.dumps(evaluate_test(args.data,args.evaluate_test,args.device),indent=2))
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.batch_size is not None:
        config["batch_size"] = args.batch_size
    if args.epochs is not None:
        config["epochs"] = args.epochs
    if any(config[k] <= 0 for k in ("epochs","batch_size","patience","learning_rate")):
        raise ValueError("Invalid training settings")
    groups = ("positive","negative") if args.group == "both" else (args.group,)
    # Validate both groups before writing any run files or training either model.
    partitions = {g:load_group(args.data,g) for g in groups}
    if args.check_data:
        print(json.dumps({g:{s:len(p["sample_ids"]) for s,p in parts.items()} for g,parts in partitions.items()},indent=2))
        print("Data check passed; no scaler fitting, training or test evaluation performed.")
        return
    if any((args.output/g).exists() for g in groups):
        raise ValueError("A group output already exists; select a new run directory")
    print("Using the selected experimental dataset; three temporary exclusions are preserved.",flush=True)
    for group in groups:
        result = train_group(partitions[group],args.output/group,config,data_fingerprint(args.data,group),group,args.device)
        print(group,json.dumps(result["validation_metrics"]))

if __name__ == "__main__":
    main()
