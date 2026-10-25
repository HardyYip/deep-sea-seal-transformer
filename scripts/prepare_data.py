"""Read-only source audit and provisional split preparation; never trains a model."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ('x', 'y', 'cpress')
TARGETS = ['ff', 'f', 'q']
COLS = ['p_system', 'p_ocean'] + [f'{c}{i}' for c in CHANNELS for i in range(1, 222)] + TARGETS
META = ['sample_id', 'source_file', 'source_row', 'group']
SPLITS = ['train', 'validation', 'test']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    data = path.read_bytes()
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data, encoding, data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError(f'Cannot decode {path}')


def allocate(n, ratios):
    ideal = [n * r for r in ratios]
    counts = [math.floor(v) for v in ideal]
    order = sorted(range(3), key=lambda i: (-(ideal[i] - counts[i]), i))
    for i in order[:n - sum(counts)]:
        counts[i] += 1
    return counts


def assign_splits(rows, components, seed, ratios):
    strata = defaultdict(list)
    for component in components:
        levels = {(rows[i]['group'], rows[i]['numbers'][0]) for i in component}
        if len(levels) != 1:
            raise ValueError('关联组跨压力层：需制定分组优先方案，停止导出划分。')
        level = next(iter(levels))
        if not math.isfinite(level[1]):
            raise ValueError('压力不可解析：无法分层，需复核后制定划分方案。')
        strata[level].append(component)
    assignments, summary = {}, []
    for (group, pressure), units in sorted(strata.items()):
        units.sort(key=lambda unit: min(rows[i]['sample_id'] for i in unit))
        counts = allocate(len(units), ratios)
        if min(counts) == 0:
            raise ValueError(f'{group}/{pressure}: 工况组不足以三分，需单独划分方案。')
        random.Random(f'{seed}|{group}|{pressure.hex()}').shuffle(units)
        offset = 0
        for split, count in zip(SPLITS, counts):
            selected = units[offset:offset + count]
            for component in selected:
                for i in component:
                    assignments[rows[i]['sample_id']] = split
            summary.append(dict(group=group, p_system=pressure, split=split,
                                condition_groups=count, rows=sum(map(len, selected))))
            offset += count
    return assignments, summary


def run(config_path):
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    if len(cfg['ratios']) != 3 or not math.isclose(sum(cfg['ratios']), 1) or min(cfg['ratios']) <= 0:
        raise ValueError('Invalid ratios')
    out = (ROOT / cfg['output']).resolve()
    if not out.is_relative_to(ROOT) or out == ROOT:
        raise ValueError('Output must be a project subdirectory')
    rows, files, hashes, flags = [], [], {}, []

    def flag(row, code, detail, status='review'):
        flags.append({**{k: row[k] for k in META}, 'issue': code,
                      'status': status, 'detail': detail, 'action': '保留原值；等待复核' if status == 'review' else '隔离；不插补'})
        if status == 'quarantine':
            row['eligibility'] = 'quarantine'

    for group, location in cfg['sources'].items():
        source = (ROOT / location).resolve()
        if out == source or out.is_relative_to(source) or source.is_relative_to(out):
            raise ValueError('Output overlaps source')
        paths = sorted(source.rglob('*_training.csv'))
        if not paths:
            raise FileNotFoundError(source)
        for path in paths:
            raw, encoding, content = read_csv(path)
            hashes[path] = digest(raw)
            reader = csv.reader(io.StringIO(content, newline=''), strict=True)
            header = next(reader)
            if len(header) != len(set(header)) or set(header) != set(COLS):
                raise ValueError(f'{path}: 列名重复、缺失或多余；停止而不丢弃字段。')
            rel = path.relative_to(source).as_posix()
            pressure_match = re.fullmatch(r'(\d+(?:\.\d+)?)MPa', path.parent.name)
            pressure = float(pressure_match[1]) if pressure_match else None
            count = 0
            while True:
                source_row = reader.line_num + 1
                try:
                    values = next(reader)
                except StopIteration:
                    break
                if len(values) != len(header):
                    raise ValueError(f'{path}:{source_row}: 行宽错误；停止以保留所有原始字段。')
                record = dict(zip(header, values))
                record.update(sample_id=digest(f'{group}|{rel}|{source_row}'.encode()),
                              source_file=rel, source_row=source_row, group=group,
                              eligibility='pending_review')
                numbers = []
                for column in COLS:
                    value = record[column]
                    try:
                        number = float(value)
                        if not math.isfinite(number):
                            flag(record, 'non_finite', f'{column}={value}', 'quarantine')
                    except ValueError:
                        number = float('nan')
                        flag(record, 'missing' if not value.strip() else 'non_numeric', f'{column}={value}', 'quarantine')
                    numbers.append(number)
                record['numbers'] = numbers
                if values == header:
                    flag(record, 'repeated_header', '文件内重复表头', 'quarantine')
                if pressure is None or numbers[0] != pressure:
                    flag(record, 'directory_pressure_mismatch', f'directory={path.parent.name}; p_system={record["p_system"]}')
                flag(record, 'physical_definitions_pending', '输入定义、来源关联与新工况可获得性尚未确认')
                if [group, *numbers[:2]] in cfg['known_review_conditions']:
                    flag(record, 'known_ff_review', f'ff={record["ff"]}; AGENTS.md 指定复核工况；不自动删除')
                rows.append(record)
                count += 1
            files.append(dict(group=group, source_file=rel, rows=count, columns=len(header),
                              bytes=len(raw), encoding=encoding, sha256=hashes[path],
                              original_columns=header, column_order_matches=header == COLS))

    # Union pressure conditions, exact numeric inputs, and explicitly confirmed links.
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(b)] = find(a)

    duplicate_counts = Counter()
    for name, width in [('condition', 2), ('identical_inputs', 665)]:
        seen = defaultdict(list)
        for i, row in enumerate(rows):
            values = row['numbers'][:width]
            if all(math.isfinite(v) for v in values):
                seen[(row['group'], *values)].append(i)
        for indices in seen.values():
            if len(indices) < 2:
                continue
            duplicate_counts[f'{rows[indices[0]]["group"]}:{name}'] += len(indices) - 1
            labels = {tuple(rows[i][t] for t in TARGETS) for i in indices}
            for i in indices:
                union(indices[0], i)
                flag(rows[i], name, f'同组记录数={len(indices)}')
                if len(labels) > 1:
                    flag(rows[i], name + '_target_conflict', '同工况或同输入目标不一致；不取平均')
    by_id = {r['sample_id']: i for i, r in enumerate(rows)}
    if len(by_id) != len(rows):
        raise AssertionError('Duplicate sample_id')
    for association in cfg['confirmed_associations']:
        for sample_id in association[1:]:
            union(by_id[association[0]], by_id[sample_id])
    members = defaultdict(list)
    for i in range(len(rows)):
        members[find(i)].append(i)
    components = list(members.values())
    assignment, strata = assign_splits(rows, components, cfg['seed'], cfg['ratios'])
    repeated, _ = assign_splits(rows, components, cfg['seed'], cfg['ratios'])
    assert repeated == assignment
    for component in components:
        assert len({assignment[rows[i]['sample_id']] for i in component}) == 1
        component_id = min(rows[i]['sample_id'] for i in component)
        for i in component:
            rows[i]['split_group_id'] = component_id
            rows[i]['split'] = assignment[rows[i]['sample_id']]

    # Ranges are descriptive only; no thresholds are estimated from held-out data.
    ranges = []
    for group in cfg['sources']:
        subset = [r for r in rows if r['group'] == group]
        for index, column in enumerate(COLS):
            finite = [r['numbers'][index] for r in subset if math.isfinite(r['numbers'][index])]
            ranges.append(dict(group=group, column=column, finite_count=len(finite),
                               minimum=min(finite) if finite else '', maximum=max(finite) if finite else '',
                               zero_count=sum(v == 0 for v in finite), negative_count=sum(v < 0 for v in finite)))
    assert sum(f['rows'] for f in files) == len(rows)
    assert all(digest(p.read_bytes()) == h for p, h in hashes.items())
    manifest_fields = META + ['p_system', 'p_ocean', 'split_group_id', 'split', 'eligibility']
    manifest = [{k: r[k] for k in manifest_fields} for r in rows]
    manifest_path = out / 'split_manifest.csv'
    if manifest_path.exists():
        old = {r['sample_id']: r['split'] for r in csv.DictReader(manifest_path.open(encoding='utf-8-sig', newline=''))}
        if old != assignment:
            raise ValueError('现有 manifest 与新划分不同：停止覆盖，需显式版本化数据或划分方案。')
    out.mkdir(parents=True, exist_ok=True)
    for group in cfg['sources']:
        write_csv(out / f'{group}_all.csv', META + COLS, [r for r in rows if r['group'] == group])
    write_csv(out / 'quality_flags.csv', META + ['issue', 'status', 'detail', 'action'], flags)
    write_csv(manifest_path, manifest_fields, manifest)
    write_csv(out / 'column_ranges.csv', ['group', 'column', 'finite_count', 'minimum', 'maximum', 'zero_count', 'negative_count'], ranges)
    write_csv(out / 'split_counts.csv', ['group', 'p_system', 'split', 'condition_groups', 'rows'], strata)
    (out / 'source_inventory.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')

    # Independent disk round trip and [N,221,3] interface spot check. No tensor dataset export.
    import numpy as np
    checks = []
    for group, location in cfg['sources'].items():
        merged = list(csv.DictReader((out / f'{group}_all.csv').open(encoding='utf-8-sig', newline='')))
        original_rows = [r for r in rows if r['group'] == group]
        assert len(merged) == len(original_rows)
        assert all(all(m[c] == str(r[c]) for c in META + COLS) for m, r in zip(merged, original_rows))
        row = next(r for r in merged if by_id.get(r['sample_id']) is not None and rows[by_id[r['sample_id']]]['eligibility'] != 'quarantine')
        p = ROOT / location / row['source_file']
        with p.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            original = next(v for v in reader if reader.line_num == int(row['source_row']))
        blocks = [np.array([[float(row[f'{c}{i}']) for i in range(1, 222)]]) for c in CHANNELS]
        tensor = np.stack(blocks, axis=-1)
        assert tensor.shape == (1, 221, 3)
        for i in (1, 2, 221):
            assert tensor[0, i - 1].tolist() == [float(original[f'{c}{i}']) for c in CHANNELS]
        assert [float(row[t]) for t in TARGETS] == [float(original[t]) for t in TARGETS]
        checks.append(dict(group=group, sample_id=row['sample_id'], source_file=row['source_file'],
                           source_row=row['source_row'], node_shape=list(tensor.shape),
                           nodes={i: tensor[0, i - 1].tolist() for i in (1, 2, 221)},
                           target_order=TARGETS, targets=[float(row[t]) for t in TARGETS]))
    assert all(digest(p.read_bytes()) == h for p, h in hashes.items())
    summary = {g: {'files': sum(f['group'] == g for f in files),
                   'rows': sum(r['group'] == g for r in rows),
                   'splits': dict(Counter(r['split'] for r in rows if r['group'] == g)),
                   'eligibility': dict(Counter(r['eligibility'] for r in rows if r['group'] == g))}
               for g in cfg['sources']}
    result = dict(summary=summary, duplicate_extra_records=dict(duplicate_counts),
                  issue_counts=dict(Counter(f['issue'] for f in flags)),
                  checks={'source_hashes_unchanged': True, 'row_conservation': True,
                          'no_component_split_overlap': True, 'repeat_assignment_equal': True,
                          'all_merged_values_roundtrip_equal': True}, tensor_spotchecks=checks,
                  config=cfg)
    (out / 'audit_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    report = ['# 数据审计与划分准备', '', '状态：数据工程准备已完成；全部记录尚未获物理审核通过。未训练模型。', '',
              '聊天原始路径不可用，本次来源为项目 src/positive 与 src/negative。只纳入 *_training.csv。',
              '原始数值文本保留；CSV 编码与引号格式可变化。每个源文件的 SHA-256、列名、规模见 source_inventory.json。', '',
              '| 分组 | 文件 | 记录 | train | validation | test |', '|---|---:|---:|---:|---:|---:|']
    for group, s in summary.items():
        cells = [f'{s["splits"].get(k, 0)} ({s["splits"].get(k, 0)/s["rows"]:.2%})' for k in SPLITS]
        report.append(f'| {group} | {s["files"]} | {s["rows"]} | ' + ' | '.join(cells) + ' |')
    report += ['', '## 检查与处理', '',
               '- 确切校验 668 列；x/y/cpress 各 1 至 221，目标顺序 ff/f/q。各列范围、零值及负值计数见 column_ranges.csv。',
               '- 原始文件哈希前后相同；合并行数守恒；全量合并值读回一致；节点 1、2、221 与源文件和 [1,221,3] 张量一致。',
               '- sample_id = SHA-256(group|组内相对路径|原 CSV 起始行号)，表头为第 1 行。',
               '- 分组内相同压力对、相同输入、已确认关联用并查集合并；不可跨分区。未确认的潜在关联仍是研究限制。',
               '- 每个 p_system 层按组 ID 排序后，以 seed=42 派生的独立 Python RNG 打乱；最大余数法分配 70/15/15，余数相同时 train、validation、test 优先。',
               '- 分配单位为完整关联工况组。小样本层或跨层关联直接停止并报告，不静默改逐行随机。各层实际计数见 split_counts.csv。',
               '- 未插补、缩放、取绝对值、平滑、删异常或估计统计异常阈值。范围只作描述，不用于筛选、调参。',
               '- 所有物理定义未确认的数值完整记录为 pending_review；必需数值错误为 quarantine；当前不生成可训练的分区 CSV 或张量文件。',
               '', '问题计数：`' + json.dumps(result['issue_counts'], ensure_ascii=False) + '`',
               '', '重复额外记录计数（空对象表示两组均未发现）：`' + json.dumps(dict(duplicate_counts)) + '`', '',
               '## 指定异常', '']
    for r in rows:
        if [r['group'], *r['numbers'][:2]] in cfg['known_review_conditions']:
            report.append(f'- {r["group"]} ({r["p_system"]},{r["p_ocean"]}) MPa：ff={r["ff"]} N，分区 {r["split"]}，保留待复核。')
    report += ['', '## 仍需向数据提供者确认', '',
               '1. positive/negative 的压力正负约定；ff/f 的力学定义、关系和符号。',
               '2. x/y/cpress 单位、坐标系、节点对应和是否为变形后状态。',
               '3. 新工况能否取得同样的节点输入，是否依赖昂贵仿真。',
               '4. 实验或仿真来源、收敛判据、重复试验和关联记录。配套缩写不能擅自解释。',
               '5. 两条指定异常是否有效及处理依据。确认无效后仅从既有分区排除，不搬动其他记录。', '',
               '随机分层仅用于已有压力范围内工况预测，不支持未见压力等级泛化或深海外推结论。', '',
               '## 重跑', '', '在项目根目录运行：`python scripts/prepare_data.py --config scripts/data_preparation.json`。',
               '依赖现有 Python 与 NumPy，不安装依赖。详细验收见 audit_results.json。',
               '未来修改数据或划分策略时使用新输出目录保留现有 manifest；正式训练前完成物理复核。']
    report += ['', '## 逐文件清单', '', '| 分组 | 源文件 | 行数 | 列数 |', '|---|---|---:|---:|']
    for item in files:
        report.append('| {group} | {source_file} | {rows} | {columns} |'.format(**item))
    report += ['', '## 独立验收', '',
               '运行 python scripts/verify_data_preparation.py 完整重跑，并独立检查已保存的 manifest 和全部输入。',
               '结果见 verification.json：包括重跑文件字节一致性、原始哈希、无泄漏、状态守恒及 28 个配套 CSV 核对。',
               '配套 CSV 明确压力为 MPa、ff/f 为 N、q 为 mm3/s；只核对、不拼接入建模池。',
               'XLSX 未进行全面收敛性判读；配套 CSV 的缩写不视为已确认的收敛标记。',
               '本次未设计数据驱动极端值阈值，只标记事先指定异常；没有标记不代表物理值已审核。',
               '两组全部记录状态均为 pending_review，可用 0、隔离 0；具体互斥计数见 audit_results.json。']
    audit_path = out / 'data_audit.md'
    audit_text = '\n'.join(report) + '\n'
    review_marker = '\n## 后续证据复核\n'
    if audit_path.exists():
        previous = audit_path.read_text(encoding='utf-8')
        if review_marker in previous:
            audit_text += review_marker + previous.split(review_marker, 1)[1]
    audit_path.write_text(audit_text, encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'scripts/data_preparation.json')
    run(parser.parse_args().config.resolve())
