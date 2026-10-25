"""Publish numeric cleaning decisions and a conservative association-safe manifest."""
import csv,json,hashlib
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"outputs/data_preparation"
OUT=BASE/"evidence_review"
def read(p):
    with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,rows):
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    summary=json.loads((OUT/"review_summary.json").read_text(encoding="utf-8"))
    workbooks=json.loads((OUT/"workbook_evidence.json").read_text(encoding="utf-8"))
    decisions=read(OUT/"record_decisions.csv")
    manifest=read(BASE/"split_manifest.csv")
    assert sha(BASE/"split_manifest.csv")==summary["manifest_sha256"]
    lookup={r["sample_id"]:r for r in decisions}
    assert len(lookup)==len(manifest)
    output=[]
    for m in manifest:
        d=lookup[m["sample_id"]]
        assert all(m[k]==d[k] for k in ("group","source_file","source_row","p_system","p_ocean","split"))
        output.append({**m,"original_split":m["split"],"original_split_group_id":m["split_group_id"],
            **{k:d[k] for k in ("numerical_status","evidence_status","convergence_status","action","issues","result_workbook")},
            "association_status":"no_identical_node_block_detected"})
    by_id={r["sample_id"]:r for r in output}
    changes=[]
    for association in summary["identical_node_associations"]:
        ids=association["sample_ids"]
        # Include any already-linked records transitively, preserving existing associations.
        linked_groups={by_id[sid]["split_group_id"] for sid in ids}
        linked=[r for r in output if r["split_group_id"] in linked_groups]
        counts=Counter(r["split"] for r in linked)
        destination=min(("train","validation","test"),key=lambda s:(-counts[s],("train","validation","test").index(s)))
        group_id=min(r["sample_id"] for r in linked)
        for r in linked:
            if r["split"]!=destination:
                changes.append(dict(sample_id=r["sample_id"],group=r["group"],p_system=r["p_system"],
                    p_ocean=r["p_ocean"],old_split=r["split"],new_split=destination,
                    reason="conservative_grouping_of_identical_node_block"))
            r["split"]=destination
            r["split_group_id"]=group_id
            r["association_status"]="identical_node_block_grouped"
    exclusions=json.loads((ROOT/"scripts/data_exclusions.json").read_text(encoding="utf-8"))
    excluded_ids={item["sample_id"] for item in exclusions["records"]}
    assert len(excluded_ids)==len(exclusions["records"])
    for r in output:
        r["exclusion_reason"]=""
        r["exclusion_temporary"]=""
    for item in exclusions["records"]:
        r=by_id[item["sample_id"]]
        assert r["group"]==item["group"] and r["split"]==item["split"]
        assert float(r["p_system"])==item["p_system"] and float(r["p_ocean"])==item["p_ocean"]
        r["eligibility"]="excluded"
        r["action"]="temporarily_excluded_by_user"
        r["exclusion_reason"]=exclusions["reason"]
        r["exclusion_temporary"]=str(exclusions["temporary"]).lower()
    boundary_plan={30:"train",40:"train",50:"train",55:"train",70:"train",
                   35:"validation",45:"validation",60:"test",65:"test"}
    boundary_moved=0
    for r in output:
        if r["group"]!="negative" or r["eligibility"]=="excluded":
            continue
        if float(r["p_ocean"])!=0.0:
            continue
        p_sys=float(r["p_system"])
        assert p_sys in boundary_plan, f"negative p_ocean=0 边界记录无方案乙分配: p_system={r['p_system']}"
        new_split=boundary_plan[p_sys]
        if r["split"]!=new_split:
            changes.append(dict(sample_id=r["sample_id"],group=r["group"],p_system=r["p_system"],
                p_ocean=r["p_ocean"],old_split=r["split"],new_split=new_split,
                reason="boundary_p_ocean_zero_rebalance_plan_b"))
            boundary_moved+=1
        r["split"]=new_split
    retained_counts={g:dict(Counter(r["split"] for r in output if r["group"]==g and r["eligibility"]!="excluded"))
                     for g in ("positive","negative")}
    write(OUT/"cleaning_manifest.csv",output)
    write(OUT/"split_manifest_grouped.csv",[{k:r[k] for k in list(manifest[0])+["exclusion_reason","exclusion_temporary"]} for r in output])
    if changes:write(OUT/"split_changes.csv",changes)
    geometry={}; seen=defaultdict(set)
    for group in ("positive","negative"):
        rows=read(BASE/f"{group}_all.csv")
        x=np.array([[float(r[f"x{i}"]) for i in range(1,222)] for r in rows])
        y=np.array([[float(r[f"y{i}"]) for i in range(1,222)] for r in rows])
        d=np.sqrt(np.diff(x,axis=1)**2+np.diff(y,axis=1)**2)
        geometry[group]={"rows":len(rows),"strictly_decreasing_y_rows":int((np.diff(y,axis=1)<0).all(axis=1).sum()),
                         "zero_adjacent_pairs":int((d==0).sum()),"adjacent_distance_min":float(d.min()),"adjacent_distance_max":float(d.max())}
        for r in rows:
            m=by_id[r["sample_id"]]
            nodes=tuple(float(r[f"{c}{i}"]) for c in ("x","y","cpress") for i in range(1,222))
            for key in (("nodes",group,*nodes),("condition",group,float(r["p_system"]),float(r["p_ocean"])),
                        ("component",m["split_group_id"])):
                seen[key].add(m["split"])
    assert all(len(s)==1 for s in seen.values())
    complete=[w for w in workbooks if "result" in w and len(w["distributions"])==4]
    length_matches=sum(all(d.get("rows")==float(w["result"]["num"]) for d in w["distributions"].values()) for w in complete)
    splits={g:dict(Counter(r["split"] for r in output if r["group"]==g)) for g in ("positive","negative")}
    verification=dict(original_manifest_unchanged=sha(BASE/"split_manifest.csv")==summary["manifest_sha256"],
        all_source_hashes_unchanged=summary["source_hashes_unchanged"],row_count=len(output),
        no_node_block_condition_or_component_crosses_partitions=True,
        split_counts=splits,moved_records=len(changes),
        retained_split_counts=retained_counts,excluded_records=len(excluded_ids),
        retained_records=len(output)-len(excluded_ids),
        workbooks_with_complete_result_and_distributions=len(complete),distribution_rows_equal_num=length_matches,
        empty_or_incomplete_workbooks=[w["path"] for w in workbooks if w not in complete],
        node_order_checks=geometry)
    (OUT/"cleaning_verification.json").write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding="utf-8")
    by_path={w["path"]:w for w in workbooks}
    lines=["# 数据清洗证据复核","","## 清洗决定","",
        f"读取 {len(workbooks)} 个 XLSX：{len(complete)} 个含 Result 和四张分布表，另 1 个为空白工作簿；核对全部 {len(output)} 条 training 记录。",
        "清洗结果：删除 0 条、修改原始数值 0 处、补齐工况 0 条。2091 条未检测到数值或源表对照错误；2 条 ff 高风险异常保留并标记。",
        "另外发现 negative 的 11 条记录共享完全相同的 663 节点数值，已采用保守关联分组修正版清单。它们不是完整输入重复，因为 p_ocean 不同。",
        "这已经是本次基于文件证据作出的清洗处置，不需要把整个清洗任务停在等待用户解释所有物理变量上。",
        "数值检查通过不等于求解器收敛已获证明；没有把无法核实的物理事实标为已确认。未启动模型训练。",
        "", "## 两条高风险 ff", "",
        "| 工况 | ff（XLSX） | mn（未定义） | hdot 唯一值数 | pcdot 唯一值数 |",
        "|---|---:|---:|---:|---:|"]
    for d in decisions:
        if "known_ff_review" in d["issues"]:
            w=by_path[d["result_workbook"]]
            lines.append(f"| {d['group']} ({d['p_system']},{d['p_ocean']}) | {w['result']['ff']} | {w['result']['mn']} | {w['distributions']['hdot']['unique_values']} | {w['distributions']['pcdot']['unique_values']} |")
    lines+=["","两条记录的目标与原始 XLSX/配套 CSV 一致，异常不是本次合并造成。ff 与邻近工况相差显著，mn 也明显较大，但 mn 含义未知，不能直接当作迭代次数或失败状态。",
        "完整 hdot/pcdot 并非整段常数；只看开头重复值会误判。存在形状差异不足以证实不收敛，故不硬删除、不替换、不取绝对值。",
        "详细邻近工况见 anomaly_neighbors.csv，完整分布统计见 workbook_evidence.json。未从验证/测试数据拟合异常阈值。",
        "", "## 收敛结论", "",
        f"在工作簿文本中搜索收敛、残差、容差、迭代、失败等标记，命中 {len(summary['status_text_hits'])} 个工作簿。",
        f"{length_matches}/{len(complete)} 个完整工作簿的 num 与四张分布表的数据行数一致，所以 num=5500 不能被当作达到迭代上限。",
        "项目仅有 CSV/XLSX，未发现求解日志；现有文件没有可核实的残差历史、容差或成功终止标记。因此收敛状态为 not_verifiable_from_export。",
        "不能确认全部已收敛，也不能据此宣布全部未收敛。对两条异常的判断是高风险、保留证据，而非已证实无效。",
        "", "## 关联检查与划分修正", "",
        "negative，p_system=65，p_ocean=60、60.5、…、65 共 11 条记录的 x/y/cpress 完全相同，ff/f/q 随压力改变。",
        "可能是节点输入重复使用、有限精度下相同状态或导出机制所致；文件不能区分这些原因。因此不按重复行删除，也不取平均目标。",
        "原清单中该组为 train 7 / validation 1 / test 3。为防止共享节点状态跨分区，修正版将 11 条归为一个组放入 train。",
        f"只移动该关联组的 {len(changes)} 条记录，使用原分区多数归属以最小化移动，不重抽其他记录、不为比例补样本。这是新关联证据导致的划分修正，不是删除异常后调整比例。",
        "旧版 split_manifest.csv 保持原样。后续应使用 evidence_review/split_manifest_grouped.csv；逐行移动依据见 split_changes.csv。",
        f"修正后计数：{json.dumps(splits,ensure_ascii=False)}。",
        "已再次检查相同节点块、同工况和关联组不跨分区。此分组只处理已发现的共享输入，不能排除未记录的其他来源联系。",
        "全体 2093 条记录的 y1…y221 严格递减、相邻节点无重合，支持数值排列一致；缺少节点 ID 对照，不能证明跨变形状态是同一物质点。",
        "", "## 未纳入 training 的结果", "",
        "- negative (70,0.5)：配套表/XLSX ff=-16380273515.853527，T≈16504091.85 ℃，为明显可疑结果；原本就没有进入 training 池，本次没有补回。",
        "- negative (70,64)：XLSX 的 6 个工作表都没有非空数据，且未纳入配套 CSV 或 training；不补齐。",
        "", "## 文件与验收", "",
        "- cleaning_manifest.csv：每条记录的原分区、新分区、数值检查、异常、关联及收敛证据状态。",
        "- split_manifest_grouped.csv：供后续采用的完整修正版划分，三条用户指定记录为 excluded，其余资格仍是 pending_review。",
        "- record_decisions.csv：数值与源表对照的原始逐记录检查；review_summary.json 与 workbook_evidence.json 为全量依据。",
        "- cleaning_verification.json：源哈希、旧 manifest、行数守恒、新关联无跨分区及节点排列检查。",
        "所有原始 CSV/XLSX 哈希前后不变；所有 2093 个 sample_id 保留；原值合并表不改。",
        "", "## 重跑", "",
        "python scripts/review_data_evidence.py",
        "",
        "python scripts/finalize_cleaning_review.py",
        "",
        "前者扫描来源生成证据，后者按固定规则生成清洗清单与分组修正，更新 Markdown。"]
    lines += ["", "## 用户指定暂时排除", "",
        "negative (15,0) 原属 validation、(20,0) 原属 train、(25,0) 原属 validation，当前 eligibility=excluded，原因：用户指定暂时排除，不认定永久无效。",
        "原始文件和合并表仍保留三条原值，清单保留原分区和关联组；本次排除不重抽样、不移动其他记录。",
        f"未排除记录分区计数：{json.dumps(retained_counts,ensure_ascii=False)}；negative 共 1041 条，positive 仍为 1049 条。",
        "未排除不等于物理审核通过，其余 eligibility 保持原状态。排除规则保存于 scripts/data_exclusions.json，重跑会继续应用。",
        "如后续恢复，只撤销相应排除规则并重新生成清单，恢复原分区；是否具备训练资格单独处理。"]
    lines += ["", "## p_ocean=0 边界重分层（方案乙）", "",
        "negative 在 p_ocean=0 处 ff 明显高于 p_ocean>0，从 0.5 到 0 存在跳变；9 条非排除边界记录原分布为 train 2 / validation 5 / test 2，训练集边界样本不足。",
        "为保证验证/测试能评估边界规律，采用方案乙定向重分层：train 5（30,40,50,55,70）/ validation 2（35,45）/ test 2（60,65）。",
        f"边界重分层移动 {boundary_moved} 条（50、55、70 从 validation 到 train），明细见 split_changes.csv；其余记录分区不变。"]
    (OUT/"cleaning_review.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    p=ROOT/"AGENTS.md";text=p.read_text(encoding="utf-8")
    marker="\n## 2026-10-01 清洗任务范围纠正与证据复核\n"
    if marker in text:
        prefix,old_tail=text.split(marker,1)
        next_heading=old_tail.find("\n## ")
        preserved_tail=old_tail[next_heading:] if next_heading>=0 else ""
    else:
        prefix,preserved_tail=text,""
    text=prefix+marker+"""
用户明确当前任务是清洗数据，要求 Agent 自行检查有效性、收敛与关联。当前停止扩展模型，不运行训练。此前模型代码保留。

- 已读取全部 2095 个 XLSX，核对 2093 条 training：2091 条未检测到数值/源表错误，2 条 ff 高风险异常保留并标记。删除 0、数值修改 0。
- 两条异常并非整段分布常数；缺少残差/容差/求解终止证据，不能确诊未收敛，也不能宣布全部收敛。num 与分布长度一致，不能当作迭代上限。
- 新发现 negative (65,60…65) 的 11 条记录共享完整 663 节点值。保守归为一组，修正版全部放 train，仅移动原 validation 1 条和 test 3 条，其他记录不动。
- 新版划分为 outputs/data_preparation/evidence_review/split_manifest_grouped.csv。旧 manifest 保留。后续采用新版，不再沿用旧版“节点关联已充分检查”的结论。
- positive 划分 738/158/153；negative 新版 741/154/149。以上为排除前分区计数；三条用户指定记录现为 excluded，其余仍 pending_review，排除后计数见本节补充。
- 池外 negative (70,0.5) 存在极端 ff/温度；(70,64) 的 XLSX 是空白表。二者都不补入 training。
- 原始 CSV/XLSX 哈希不变。清洗处置、证据和验收见 outputs/data_preparation/evidence_review/cleaning_review.md 与 cleaning_verification.json。
- 重跑 scripts/review_data_evidence.py，再运行 scripts/finalize_cleaning_review.py；不从数值猜测物理定义，不因用户推测而声称收敛。
"""
    text += "\n- 用户已指定暂时排除 negative (15,0)、(20,0) 与 (25,0)，eligibility=excluded，标注暂时排除而非永久无效。原始数据与其他记录原值保持不变。"
    text += "\n- negative 在 p_ocean=0 处存在边界跳变，9 条非排除边界记录按方案乙定向重分层为 train 5 / validation 2 / test 2（50、55、70 从 validation 移到 train），以保证验证/测试可评估边界规律。"
    text += "\n- 排除并重分层后 negative 为 train 740 / validation 152 / test 149，共 1041 条；positive 仍为 1049 条。其他记录的 pending_review 状态不在本次授权中修改。"
    text += "\n- 排除规则为 scripts/data_exclusions.json，边界重分层规则在 scripts/finalize_cleaning_review.py；清单生成脚本重跑仍应用。\n"
    text += preserved_tail
    p.write_text(text,encoding="utf-8")
    p=BASE/"data_audit.md";text=p.read_text(encoding="utf-8")
    marker="\n## 后续证据复核\n"
    text=text.split(marker)[0]+marker+"\n已完成 [XLSX 全量清洗复核](evidence_review/cleaning_review.md)。新发现 11 条共享节点输入关联记录，修正版划分见 evidence_review/split_manifest_grouped.csv；旧版保留追溯。早期未全面读取 XLSX、未发现关联的描述属于历史检查范围。原始数值未改。用户现已暂时排除 negative (15,0)、(20,0)、(25,0)，并对 9 条 p_ocean=0 边界记录按方案乙定向重分层（train 5 / validation 2 / test 2）；negative 未排除记录为 740/152/149，共 1041 条；positive 不变。\n"
    p.write_text(text,encoding="utf-8")
    print(json.dumps(verification,ensure_ascii=True))
if __name__=="__main__":main()
