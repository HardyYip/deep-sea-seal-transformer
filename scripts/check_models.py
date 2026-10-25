"""Synthetic forward/backward checks only: no dataset loading or weight updates."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from models import build_models

def main():
    torch.manual_seed(42)
    models = build_models()
    nodes, pressure = torch.randn(2, 221, 3), torch.randn(2, 2)
    for group, model in models.items():
        model.eval()
        prediction = model(nodes, pressure)
        assert prediction.shape == (2, 3) and torch.isfinite(prediction).all()
        prediction.square().mean().backward()
        assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
        # Without positional assumptions, permuting whole node triplets preserves output.
        with torch.no_grad():
            permuted = model(nodes[:, torch.randperm(221)], pressure)
            torch.testing.assert_close(prediction, permuted, atol=1e-6, rtol=1e-5)
        try:
            model(nodes[:, :220], pressure)
            raise AssertionError("Invalid shape accepted")
        except ValueError:
            pass
        print(group, "PASS", sum(p.numel() for p in model.parameters()), "parameters")
    a, b = models["positive"], models["negative"]
    assert {p.data_ptr() for p in a.parameters()}.isdisjoint({p.data_ptr() for p in b.parameters()})
    before = [p.detach().clone() for p in b.parameters()]
    with torch.no_grad():
        next(a.parameters()).add_(1)
    assert all(torch.equal(x, y) for x, y in zip(before, b.parameters()))
    print("Independent weights: PASS. No training performed.")

if __name__ == "__main__":
    main()
