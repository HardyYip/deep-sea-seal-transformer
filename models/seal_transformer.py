"""Separate regressors for positive/negative; outputs always ff, f, q."""
import torch
from torch import nn

TARGET_ORDER = ("ff", "f", "q")


class SealTransformer(nn.Module):
    """Node attention followed by pressure fusion; no assumed time/node positions.

    Inputs must eventually be standardized with training-only statistics.
    This module does not fit scalers or infer units.
    """
    def __init__(self, d_model=64, nhead=4, num_layers=2,
                 dim_feedforward=128, dropout=0.1):
        super().__init__()
        self.node_projection = nn.Linear(3, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_feedforward,
            dropout=dropout, activation="gelu", batch_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers,
                                             enable_nested_tensor=False)
        # Explicit independent initialization avoids identical cloned encoder layers.
        for block in self.encoder.layers:
            nn.init.xavier_uniform_(block.self_attn.in_proj_weight)
            nn.init.zeros_(block.self_attn.in_proj_bias)
            for module in block.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)
        self.pressure_projection = nn.Sequential(nn.Linear(2, d_model), nn.GELU())
        self.head = nn.Sequential(
            nn.Linear(2 * d_model, d_model), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(d_model, 3))

    def forward(self, node_features, global_features):
        if node_features.ndim != 3 or tuple(node_features.shape[1:]) != (221, 3):
            raise ValueError("node_features must have shape [N, 221, 3]")
        if global_features.ndim != 2 or tuple(global_features.shape) != (node_features.shape[0], 2):
            raise ValueError("global_features must have shape [N, 2]")
        encoded = self.encoder(self.node_projection(node_features)).mean(dim=1)
        pressure = self.pressure_projection(global_features)
        # Linear output permits positive, negative, and zero predictions.
        return self.head(torch.cat((encoded, pressure), dim=-1))


def build_models(**kwargs):
    """Two independently parameterized instances; no shared weights."""
    return nn.ModuleDict({group: SealTransformer(**kwargs)
                          for group in ("positive", "negative")})
