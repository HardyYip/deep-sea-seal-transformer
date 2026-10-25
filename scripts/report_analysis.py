"""Read frozen run_004 outputs and create report figures; never fit or score a model."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/final_report'
FIG = OUT / 'figures'
FIG.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False,
                     'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                     'savefig.dpi': 240, 'axes.titleweight': 'bold'})
COLORS = {'positive': '#216896', 'negative': '#BF6D32'}
TARGETS = ('ff', 'f', 'q')
UNITS = ('N', 'N', 'mm³/s')


def read(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(fig, name):
    fig.savefig(FIG / f'{name}.png', bbox_inches='tight', facecolor='white')
    fig.savefig(FIG / f'{name}.svg', bbox_inches='tight', facecolor='white')
    plt.close(fig)


def score(a, p):
    e = p-a
    return dict(MAE=float(np.abs(e).mean()), RMSE=float(np.sqrt(np.mean(e**2))),
                R2=float(1-(e**2).sum()/((a-a.mean())**2).sum()))


manifest = read(ROOT / 'outputs/training_data/v2/split_manifest.csv')
lookup = {r['sample_id']: r for r in manifest}
source_paths = list((ROOT/'outputs/training/run_004').rglob('*'))
source_paths = [p for p in source_paths if p.is_file()]
source_paths += [ROOT/'outputs/training_data/v2'/n for n in
                 ('split_manifest.csv', 'dataset_metadata.json', 'positive_all.csv', 'negative_all.csv')]
source_paths += list((ROOT/'models').glob('*.py'))
source_paths += [ROOT/'scripts'/n for n in ('training_positive.json','training_negative.json',
                                          'train_models.py','predict.py','prepare_training_bundle.py')]
before = {p.relative_to(ROOT).as_posix(): sha(p) for p in source_paths}
summary = {'source_sha256': before, 'groups': {}, 'development_runs': []}
bundle = json.loads((ROOT/'outputs/training_data/v2/dataset_metadata.json').read_text())
assert all(sha(ROOT/'outputs/training_data/v2'/n) == h for n,h in bundle['files'].items())

for group in COLORS:
    run = ROOT/'outputs/training/run_004'/group
    meta = json.loads((run/'metadata.json').read_text())
    scores = json.loads((run/'test_metrics.json').read_text())
    assert meta['test_evaluated'] and meta['run_status']=='complete'
    assert sha(run/'best.pt') == meta['checkpoint_sha256']
    assert all(sha(ROOT/'outputs/training_data/v2'/n)==h for n,h in meta['fingerprint'].items())
    fit_ids = meta['scaler']['fit_sample_ids']
    expected = [r['sample_id'] for r in manifest if r['group']==group and
                r['split']=='train' and r['eligibility'] in ('eligible','provisional')]
    assert fit_ids == expected
    records = read(run/'test_predictions.csv')
    val_records = read(run/'validation_predictions.csv')
    for split,rows in [('test',records),('validation',val_records)]:
        assert {r['sample_id'] for r in rows} == {r['sample_id'] for r in manifest
               if r['group']==group and r['split']==split and r['eligibility']=='provisional'}
        for t in TARGETS:
            a=np.array([float(r[t+'_actual']) for r in rows])
            p=np.array([float(r[t+'_prediction']) for r in rows])
            expected_score=scores['transformer'][t] if split=='test' else meta['validation_metrics'][t]
            for key,value in score(a,p).items():
                assert np.isclose(value,expected_score[key],atol=1e-10,rtol=1e-9)
    d = {'counts':bundle['counts'][group], 'test':scores, 'validation':meta['validation_metrics'],
         'best_epoch':meta['best_epoch'], 'epochs':len(meta['history']),
         'elapsed_seconds':meta['elapsed_seconds'], 'config':meta['config'],
         'checkpoint_sha256':meta['checkpoint_sha256'],
         'best_validation_mse':min(h['validation_standardized_mse'] for h in meta['history']),
         'test_mae_reduction_pct':{}, 'test_error_distribution':{}, 'subgroups':{}, 'worst':[]}
    for t in TARGETS:
        d['test_mae_reduction_pct'][t]=(1-scores['transformer'][t]['MAE']/scores['training_mean_baseline'][t]['MAE'])*100
        err=np.array([float(r[t+'_prediction'])-float(r[t+'_actual']) for r in records])
        d['test_error_distribution'][t]={'bias':float(err.mean()), 'median_abs':float(np.median(np.abs(err))),
             'p95_abs':float(np.quantile(np.abs(err),.95)), 'max_abs':float(np.max(np.abs(err)))}
        for split,rows in [('validation',val_records),('test',records)]:
            for r in sorted(rows,key=lambda r:abs(float(r[t+'_prediction'])-float(r[t+'_actual'])),reverse=True)[:2]:
                d['worst'].append({'split':split,'target':t,**{k:lookup[r['sample_id']][k] for k in ('sample_id','p_system','p_ocean')},
                                  'actual':float(r[t+'_actual']),'prediction':float(r[t+'_prediction']),
                                  'abs_error':abs(float(r[t+'_prediction'])-float(r[t+'_actual']))})
    for split,rows in [('validation',val_records),('test',records)]:
        for label,predicate in [('p_ocean_zero',lambda r:float(lookup[r['sample_id']]['p_ocean'])==0),
                                ('p_ocean_positive',lambda r:float(lookup[r['sample_id']]['p_ocean'])>0)]:
            selected=[r for r in rows if predicate(r)]
            d['subgroups'][split+'_'+label]={'n':len(selected), 'MAE':{t:float(np.mean([abs(float(r[t+'_prediction'])-float(r[t+'_actual'])) for r in selected])) for t in TARGETS}}
    summary['groups'][group]=d

# The old runs are compared only within their identical fingerprint cohort.
for p in sorted((ROOT/'outputs/training').glob('run_00[123]/*/metadata.json')):
    m=json.loads(p.read_text())
    summary['development_runs'].append({'run':p.parent.parent.name,'group':m['group'],
        'config':m['config'],'fingerprint':m['fingerprint'],'validation':m['validation_metrics'],
        'best_validation_mse':min(h['validation_standardized_mse'] for h in m['history'])})
cohorts={tuple(sorted(d['fingerprint'].items())) for d in summary['development_runs'] if d['group']=='negative'}
assert len(cohorts)==1

# Model architecture with tensor dimensions.
fig,ax=plt.subplots(figsize=(10.2,5.2)); ax.set(xlim=(0,10),ylim=(0,5)); ax.axis('off')
def box(x,y,w,h,label,color='#EAF1F6'):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.08,rounding_size=0.08',
                              facecolor=color,edgecolor='#4A6576',linewidth=1))
    ax.text(x+w/2,y+h/2,label,ha='center',va='center',fontsize=10)
def arrow(x,y,x2,y2):
    ax.add_patch(FancyArrowPatch((x,y),(x2,y2),arrowstyle='-|>',mutation_scale=13,color='#4A6576'))
box(.15,3.65,1.7,.9,'节点三通道\n[B, 221, 3]')
box(2.25,3.65,1.65,.9,'共享线性映射\n3 → 64')
box(4.3,3.65,2.1,.9,'2 层 Transformer\n4 头  FFN 128')
box(6.85,3.65,1.9,.9,'节点均值池化\n[B, 64]')
for x1,x2 in [(1.9,2.15),(3.95,4.2),(6.45,6.75)]:arrow(x1,4.1,x2,4.1)
box(.15,1.7,1.7,.9,'全局压力\n[B, 2]','#F6EDE5')
box(2.25,1.7,1.65,.9,'线性映射 GELU\n2 → 64','#F6EDE5')
arrow(1.9,2.15,2.15,2.15)
box(4.55,1.6,1.65,1.1,'拼接\n[B, 128]')
arrow(3.95,2.15,4.45,2.15); arrow(7.8,3.55,6.25,2.65)
box(6.85,1.6,2.4,1.1,'回归头\n128 → 64 → 3')
arrow(6.25,2.15,6.75,2.15)
ax.text(5,.75,'输出  [ff, f, q]     逆标准化后计算摩擦力 ff + f',ha='center',fontsize=12)
ax.text(5,.2,'positive 与 negative 使用同结构独立权重    无位置编码    固定 221 个节点',ha='center',fontsize=9,color='#555555')
save(fig,'01_architecture')

fig,axes=plt.subplots(1,2,figsize=(10.2,3.9),layout='constrained')
for ax,group in zip(axes,COLORS):
    m=json.loads((ROOT/'outputs/training/run_004'/group/'metadata.json').read_text()); h=m['history']
    ax.semilogy([v['epoch'] for v in h],[v['train_standardized_mse'] for v in h],label='训练 MSE',color=COLORS[group])
    ax.semilogy([v['epoch'] for v in h],[v['validation_standardized_mse'] for v in h],label='验证 MSE',color='#35434D',alpha=.8)
    ax.axvline(m['best_epoch'],linestyle='--',color='#888888',label=f"最佳轮次 {m['best_epoch']}")
    ax.set(title=group,xlabel='Epoch',ylabel='标准化三目标平均 MSE'); ax.grid(alpha=.15); ax.legend(fontsize=9)
save(fig,'02_learning_curves')

fig,axes=plt.subplots(2,3,figsize=(10.2,6.4),layout='constrained')
for row,group in enumerate(COLORS):
    rows=read(ROOT/'outputs/training/run_004'/group/'test_predictions.csv')
    ocean=np.array([float(lookup[r['sample_id']]['p_ocean']) for r in rows])
    for col,(t,unit) in enumerate(zip(TARGETS,UNITS)):
        ax=axes[row,col]; a=np.array([float(r[t+'_actual']) for r in rows]); p=np.array([float(r[t+'_prediction']) for r in rows])
        ax.scatter(a[ocean>0],p[ocean>0],s=12,alpha=.62,color=COLORS[group],label='p_ocean > 0')
        ax.scatter(a[ocean==0],p[ocean==0],s=30,facecolors='none',edgecolors='#AC2833',linewidth=1.1,label='p_ocean = 0')
        lo,hi=min(a.min(),p.min()),max(a.max(),p.max()); gap=(hi-lo)*.06
        ax.plot([lo-gap,hi+gap],[lo-gap,hi+gap],'--',color='#666666',linewidth=.8)
        ax.set(xlim=(lo-gap,hi+gap),ylim=(lo-gap,hi+gap),title=f"{group}  {t}   R²={score(a,p)['R2']:.4f}",
               xlabel=f'真实值 ({unit})',ylabel=f'预测值 ({unit})'); ax.grid(alpha=.13)
        if row==0 and col==0:ax.legend(fontsize=7,loc='lower right')
save(fig,'03_test_parity')

fig,axes=plt.subplots(2,3,figsize=(10.2,6.2),layout='constrained')
for row,group in enumerate(COLORS):
    rows=read(ROOT/'outputs/training/run_004'/group/'test_predictions.csv')
    ocean=np.array([float(lookup[r['sample_id']]['p_ocean']) for r in rows])
    for col,(t,unit) in enumerate(zip(TARGETS,UNITS)):
        ax=axes[row,col]; e=np.array([float(r[t+'_prediction'])-float(r[t+'_actual']) for r in rows])
        ax.scatter(ocean[ocean>0],e[ocean>0],s=12,alpha=.62,color=COLORS[group])
        ax.scatter(ocean[ocean==0],e[ocean==0],s=30,facecolors='none',edgecolors='#AC2833',linewidth=1.1)
        ax.axhline(0,color='#666666',linestyle='--',linewidth=.8)
        ax.set(title=f'{group}  {t}',xlabel='p_ocean (MPa)',ylabel=f'预测值 − 真实值 ({unit})'); ax.grid(alpha=.13)
save(fig,'04_test_residuals')

fig,axes=plt.subplots(1,3,figsize=(10.2,3.7),layout='constrained')
for ax,(t,unit) in zip(axes,zip(TARGETS,UNITS)):
    for i,group in enumerate(COLORS):
        d=summary['groups'][group]['test']
        ax.bar(i-.18,d['training_mean_baseline'][t]['MAE'],width=.35,color='#CCD4DA',label='训练均值基线' if i==0 else None)
        ax.bar(i+.18,d['transformer'][t]['MAE'],width=.35,color=COLORS[group],label='Transformer' if i==0 else None)
    ax.set_yscale('log'); ax.set_xticks([0,1],['positive','negative']); ax.set(title=t,ylabel=f'MAE ({unit})  对数坐标');ax.grid(axis='y',alpha=.13)
    ax.legend(fontsize=8)
save(fig,'05_baseline_comparison')

fig,ax=plt.subplots(figsize=(10.2,3.3),layout='constrained')
for i,group in enumerate(COLORS):
    values=[summary['groups'][group]['counts'][s] for s in ('train','validation','test')]; left=0
    for n,c,label in zip(values,['#216896','#75A7B4','#D9A165'],['train','validation','test']):
        pct=n/sum(values)*100
        ax.barh(i,pct,left=left,color=c,label=label if i==0 else None,height=.5)
        ax.text(left+pct/2,i,f'{n}\n{pct:.2f}%',ha='center',va='center',color='white' if label=='train' else '#172C3C',fontsize=10)
        left+=pct
ax.set(yticks=[0,1],yticklabels=['positive','negative'],xlabel='分组内样本比例 (%)',xlim=(0,100)); ax.invert_yaxis();ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=3,frameon=False)
save(fig,'06_split_counts')

assert before == {p.relative_to(ROOT).as_posix():sha(p) for p in source_paths}
summary['checks']={'frozen_sources_unchanged':True,'bundle_hashes_match':True,
                   'checkpoints_match':True,'scaler_ids_match_train':True,
                   'saved_predictions_reproduce_frozen_metrics':True,
                   'model_execution':False,'training':False,'test_entry_called':False}
(OUT/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({g:{'counts':v['counts'],'MAE_reduction_pct':v['test_mae_reduction_pct'],'subgroups':v['subgroups']} for g,v in summary['groups'].items()},ensure_ascii=False,indent=2))
print('Figures and verified descriptive statistics saved to',OUT)
