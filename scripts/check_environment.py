"""Small synthetic hardware check; never reads real training data."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.runtime import check_device

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu","cuda"), default="cuda")
    args = parser.parse_args()
    try:
        print(json.dumps(check_device(args.device), indent=2))
        print("Device check passed. No real model training was performed.")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
