# SealTransformer 模型接口

`positive` 与 `negative` 各使用一个 `SealTransformer`，结构相同、权重独立。冻结实验结果见[根 README](../README.md)，运行命令见[训练说明](TRAINING.md)。

| 张量 | 形状 | 顺序 |
|---|---|---|
| `node_features` | `[N, 221, 3]` | 节点三通道 `[xi, yi, cpressi]` |
| `global_features` | `[N, 2]` | `[p_system, p_ocean]` |
| 输出 | `[N, 3]` | `[ff, f, q]` |

默认模型：节点投影 3→64；2 层 Transformer Encoder；4 个注意力头；前馈维度 128；GELU；dropout=0.1。节点均值池化后，与压力分支 2→64 的表示拼接，经 128→64→3 的回归头输出。每个模型的参数量为 75,843。

输出层为线性层，保留正负号；`ff+f` 在目标逆变换后计算，不作为额外监督目标。来源字段、划分字段及三个目标均不进入特征。

不使用时间位置编码或节点编号嵌入。推理模式下，整体置换节点三元组不改变输出；模型没有显式编码节点邻接关系。

`models/data.py` 按整数编号逐块读取 x/y/cpress，再沿最后一维堆叠。每组仅用 train 拟合三个节点通道、两个压力和三个目标的标准化参数，常量尺度设为 1；validation/test 只进行 transform，指标在逆变换后计算。

```python
import torch
from models import build_models

models = build_models()
nodes = torch.randn(2, 221, 3)
pressures = torch.randn(2, 2)
predictions = models["positive"](nodes, pressures)  # [2, 3]
```

该示例使用随机权重，只验证接口。真实推理使用 `scripts/predict.py` 加载已训练的权重及配套元数据。`python scripts/check_models.py` 会检查形状、有限梯度、参数独立性和节点重排一致性。
