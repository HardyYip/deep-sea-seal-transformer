# 数据格式、证据复核与公开范围

## 公开与本地文件

研究数据的公开授权尚未确认，因此仓库不含 `src/`、`outputs/`、原始 CSV/XLSX、逐样本预测、真实权重或拟合缩放参数。代码、实验配置、排除规则和汇总结果公开。`docs/results/` 的逐轮损失是聚合统计，不含逐样本记录。

合成检查在临时目录自动构造相同列结构的数据，不需要下载研究数据，也不会代替真实实验结果。原始数据目前不提供公开下载链接。

## 输入格式

```text
p_system,p_ocean,
x1,...,x221,
y1,...,y221,
cpress1,...,cpress221,
ff,f,q
```

实际 CSV 为单行表头，上述换行只为展示列块。221 个节点属于同一工况，不拆为独立样本。`ff/f/q` 为三个目标；`y1…y221` 是节点输入。

训练来源仅匹配 `*_training.csv`。原始每组含 5、10、…、70 MPa 共 14 个压力目录，配置使用项目相对路径：

```text
src/
  positive/<原始压力目录名>/*_training.csv
  negative/<原始压力目录名>/*_training.csv
```

应保留提供者原始目录名、文件名和行顺序，不重新编造文件名。稳定 `sample_id` 依赖分组、来源相对路径及原 CSV 行号（表头为第 1 行）。改变这些信息会改变 ID，历史排除规则也可能无法匹配。

普通 CSV 和 `Results_*.xlsx` 保持原始位置及命名，用于核对单位、目标和来源证据，不与 training 重复拼接。工作簿分布表不是可直接逐行对接的 221 个节点数据。`scripts/data_preparation.json` 给出来源和输出配置。

## 审计与清洗决定

- 逐文件校验确切列名及重复列、节点编号 1–221、数值解析、NaN/Inf、压力与目录对应、重复工况和完全相同输入。
- 记录 SHA-256、规模、范围、溯源字段和质量标记；原始目录只读，不覆盖、不移动、不重命名。
- 不因零或负值自动删除，不对全字段套用异常阈值，不取绝对值或自动插值。确定的数值错误隔离，可疑物理值保留证据。
- negative `(15,0)`、`(20,0)`、`(25,0)` 按项目决定暂时排除，见 `scripts/data_exclusions.json`；并非已证实永久无效。
- negative `(65,60…65)` 的 11 条记录共享全部 663 个节点数值，目标和海压存在差别；按关联组隔离，不平均目标或按重复行删除。

原始池 2,093 条，最终实验纳入 2,090 条。记录仍保留原始值和历史资格；单独版本化实验包允许所选数据以 `provisional` 状态进入授权实验，不将其改成物理审核通过。

## 从授权原始资料重建

下列命令需要完整原始资料。工作簿证据复核针对本项目数据格式；不能把任意新数据直接套入历史排除和边界分配规则。

```bash
python scripts/prepare_data.py
python scripts/verify_data_preparation.py
python scripts/review_data_evidence.py
python scripts/finalize_cleaning_review.py
python scripts/check_exclusions.py
python scripts/prepare_training_bundle.py
```

`verify_data_preparation.py` 会重跑初始整理并比较字节一致性；`check_exclusions.py` 会重跑派生清单验证幂等性。它们不改原始文件，但会写入派生审计输出。工作目录下若有需要保留的其他实验版本，应使用独立副本执行重建。

最终训练使用 `outputs/training_data/v2/split_manifest.csv`，由修正后的 `outputs/data_preparation/evidence_review/split_manifest_grouped.csv` 生成，不直接使用初版划分。v2 已存在但内容不一致时，导出器拒绝覆盖，应使用新的版本目录并记录变更。

## 划分与防泄漏

初版按 p_system 分层、以完整工况及关联组划分，seed=42。发现共享节点状态后，将 11 条关联记录全部放入 train；零海压边界按记录的方案重分配。排除后的实际计数为：

| 分组 | train | validation | test |
|---|---:|---:|---:|
| positive | 738（70.35%） | 158（15.06%） | 153（14.59%） |
| negative | 740（71.09%） | 152（14.60%） | 149（14.31%） |

manifest 保存 sample_id、来源、工况、关联组、分区及资格。数据加载时再次检查同工况、完整输入和节点块的分区隔离。标准化仅用 train 拟合，validation 用于选 checkpoint，test 单独最终评价。

这些约束隔离了已知关联，不能证明所有未记录的来源关系均不存在。随机分层也不支持未见压力等级泛化或外推结论。

## 物理信息仍有待补充

已知 `ff+f` 为摩擦力；ff/f 原表为 N，q 为 mm³/s，压力为 MPa。ff/f 各自更细的定义、节点变量单位和物理对应、输入获取成本及完整求解收敛证据仍未全部确认。

提供者说明过节点间收敛，但导出文件没有完整残差、容差或成功终止证据。因此实验成绩应与数据物理审核状态一起引用，不解释为全部工况物理有效或工程部署验收。
