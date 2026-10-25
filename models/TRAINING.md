# 环境、训练与推理

公开仓库不含原始数据或真实权重。无需数据的入口是 `python scripts/check_training_pipeline.py --device cpu`；以下真实数据命令需要本地已有经过复核的 v2 实验包。数据准备步骤见 [docs/DATA.md](../docs/DATA.md)。

## 1. 环境与合成验收

在 Python 3.11 的独立环境中安装：

```bash
python -m pip install -r requirements.txt
python scripts/check_models.py
python scripts/check_training_pipeline.py --device cpu
```

GPU 需要驱动、硬件与 PyTorch CUDA wheel 兼容；可按 [PyTorch 官方安装入口](https://pytorch.org/get-started/locally/) 选择安装命令，再安装本项目依赖。`requirements-gpu.txt` 是 CUDA 13.0 索引的可选配置，不是跨硬件通用锁文件。不要为了使用本项目覆盖已有可用环境。

冻结实验实际使用 Python 3.11、NumPy 2.4.6、PyTorch 2.13.0+cu130、RTX 5070 Laptop GPU。安装依赖时解析到的版本可能不同，合成验收用于确认当前环境的运行能力，不保证跨硬件训练结果逐位一致。

```bash
python scripts/check_environment.py --device cuda
python scripts/check_training_pipeline.py --device cuda
```

预检会执行实际 Transformer CUDA 前向与反向，失败直接报错。CPU 模式显式指定 `--device cpu`。VS Code 中先选择当前机器的 Python 解释器；共享启动项不写死解释器路径。

## 2. 数据预检与两组训练

```bash
python scripts/train_models.py --device cuda --data outputs/training_data/v2 --check-data
python scripts/check_training_pipeline.py --device cpu --check-private-data
```

第一条不拟合缩放、不训练、不评价 test；第二条先做合成验收，再只读核对本地 v2 的文件哈希、计数、排除和关联组。两条命令都不会重建或覆盖数据包。

使用两套配置分别训练：

```bash
python scripts/train_models.py --device cuda --data outputs/training_data/v2 --config scripts/training_positive.json --group positive --output outputs/training/my_run
python scripts/train_models.py --device cuda --data outputs/training_data/v2 --config scripts/training_negative.json --group negative --output outputs/training/my_run
```

默认数据路径为 v2。positive 学习率 0.0003，negative 为 0.001；两组均为 seed=42、最多 100 epochs、batch size=32、patience=15、AdamW weight decay=1e-4。优化标准化三目标平均 MSE，梯度范数上限为 1。validation 损失最低时保存 checkpoint。

`--group both` 对两组使用同一份 `--config`，适合明确希望共用超参数的实验；重现 run_004 时使用上述两个单组命令。`--batch-size` 和 `--epochs` 可以覆盖配置。已有分组输出目录会被拒绝，应使用新的运行目录。本版不提供断点续训。

## 3. 训练产物与评价隔离

每组独立保存：

- `best.pt`：validation 标准化 MSE 最低时的权重。
- `metadata.json`：配置、列顺序、train 拟合 ID、缩放参数、数据与 checkpoint 指纹、训练状态、验证指标。
- `history.csv`：逐轮训练和验证标准化 MSE。
- `validation_predictions.csv`：验证集原尺度真实值及预测值。

节点按三通道在训练样本与节点维度上统计，全局压力与目标逐列缩放。仅 train 拟合；validation/test 只进行变换。MAE、RMSE、R² 按 `ff/f/q` 分别报告，常量目标的 R² 为 null。对照基线始终使用训练集目标均值。

训练命令不自动评价 test。方案冻结后，对一个新实验的每组分别执行一次：

```bash
python scripts/train_models.py --device cuda --data outputs/training_data/v2 --evaluate-test outputs/training/my_run/positive
python scripts/train_models.py --device cuda --data outputs/training_data/v2 --evaluate-test outputs/training/my_run/negative
```

测试入口检查数据/权重指纹、训练完成状态及缩放拟合 ID，保存 `test_metrics.json`、`test_predictions.csv`，并标记 `test_evaluated=true`。已有测试结果时拒绝覆盖。这是程序层面的防误用约束，不能替代实验者对测试隔离的遵守。

`run_004` 已完成测试并冻结，公开结果见 [汇总 JSON](../docs/results/run_004_summary.json)。新建运行目录不会把已经看过的测试集变成新的盲测集；后续若根据这些结果修改方案，应另设未使用的最终评价数据。

## 4. 无目标列推理

新 CSV 必须包含 `p_system,p_ocean,x1…x221,y1…y221,cpress1…cpress221`。无需目标列；可选 `group` 列若存在，必须与模型分组一致。

```bash
python scripts/predict.py --device cuda --run outputs/training/my_run/positive --input new_positive.csv --output outputs/predictions/positive.csv
```

输出原尺度 `ff_prediction,f_prediction,q_prediction`，以及逆变换后相加得到的 `friction_prediction`。输入或已有输出不会被覆盖，checkpoint 指纹变化会被拒绝。预测需要完整节点输入，当前模型不支持只给两个压力的推理。

## 5. 重建公开图表

仅依赖仓库中的汇总数据：

```bash
python -m pip install -r requirements-viz.txt
python scripts/plot_public_results.py
```

持有原始冻结产物时，可先执行 `python scripts/export_public_results.py`。导出只复制明确允许的汇总字段与逐轮损失，核对已有文件指纹，不训练、不推理、不重新评价 test。生成研究报告的旧脚本需要本地研究数据及额外的 `matplotlib` / `python-docx`；不属于无数据快速运行入口。
