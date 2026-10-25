"""Explicit device checks: CUDA availability alone does not prove kernel compatibility."""
import sys
import torch
from .seal_transformer import SealTransformer

def check_device(device):
    info = {"python": sys.executable, "torch": str(torch.__version__), "device": device}
    if device == "cpu":
        return info
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment. See models/TRAINING.md.")
    info.update(gpu=torch.cuda.get_device_name(0), cuda_build=torch.version.cuda,
                capability=list(torch.cuda.get_device_capability(0)), compiled_arches=torch.cuda.get_arch_list())
    try:
        # Check both basic kernels and the actual Transformer forward/backward path.
        model = SealTransformer(d_model=8, nhead=2, num_layers=1, dim_feedforward=16, dropout=0).to(device)
        prediction = model(torch.randn(2,221,3,device=device), torch.randn(2,2,device=device))
        prediction.square().mean().backward()
        torch.cuda.synchronize()
        if not torch.isfinite(prediction).all():
            raise RuntimeError("Non-finite CUDA output")
    except RuntimeError as exc:
        raise RuntimeError("GPU execution failed. Current PyTorch may not support this GPU. "
                           "Use the CUDA 13.0 environment instructions in models/TRAINING.md. "
                           f"Original error: {exc}") from exc
    return info
