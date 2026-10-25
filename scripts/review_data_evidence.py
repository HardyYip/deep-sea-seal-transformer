"""Evidence-based cleaning review. Source XLSX/CSV files are strictly read-only."""
import csv, hashlib, json, math, re
import xml.etree.ElementTree as E
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"outputs/data_preparation/evidence_review"
NS={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
TERMS=re.compile(r"converg|residual|tolerance|iteration|solver|fail|error|收敛|残差|容差|迭代|失败",re.I)

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    with p.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def write(p,rows,fields=None):
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]),extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def sheet_rows(data,strings):
    result=[]
    for row in E.fromstring(data).findall("m:sheetData/m:row",NS):
        cells={}
        for c in row:
            v=c.find("m:v",NS); inline=c.find("m:is",NS)
            text=v.text if v is not None else "".join(inline.itertext()) if inline is not None else ""
            if c.get("t")=="s": text=strings[int(text)]
            cells[re.sub(r"[0-9]","",c.get("r"))]=text
        result.append(cells)
    return result

def inspect(p):
    before=sha(p)
    evidence=dict(path=p.relative_to(ROOT).as_posix(),sha256=before)
    with ZipFile(p) as z:
        strings=["".join(e.itertext()) for e in E.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si",NS)] if "xl/sharedStrings.xml" in z.namelist() else []
        rels={r.get("Id"):r.get("Target") for r in E.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets=E.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet",NS)
        evidence["sheet_names"]=[s.get("name") for s in sheets]
        evidence["status_text_hits"]=[s for s in strings if TERMS.search(s)]
        distributions={}
        for s in sheets:
            name=s.get("name"); target=rels[s.get(REL)]
            target=target.lstrip("/") if target.startswith("/") else "xl/"+target
            raw=z.read(target)
            cells=sheet_rows(raw,strings)
            if name=="Result":
                assert len(cells)==2,(p,"Unexpected Result rows")
                evidence["result"]={label:cells[1].get(col,"") for col,label in cells[0].items()}
            elif name in ("hdot","pdot","pcdot","psdot"):
                pairs=[]
                for row in cells[1:]:
                    if not row: continue
                    try: pairs.append([float(row["A"]),float(row["B"])])
                    except (KeyError,ValueError):
                        distributions[name]={"parse_error":True}; break
                else:
                    a=np.asarray(pairs)
                    values=a[:,1]
                    distributions[name]=dict(rows=len(a),finite=bool(np.isfinite(a).all()),
                        minimum=float(values.min()),maximum=float(values.max()),
                        unique_values=len(np.unique(values)),negative_count=int((values<0).sum()),
                        x_strictly_increasing=bool((np.diff(a[:,0])>0).all()),
                        first=float(values[0]),last=float(values[-1]),
                        values_hash=hashlib.sha256(values.tobytes()).hexdigest())
            else:
                evidence.setdefault("extra_nonempty_cells",{})[name]=sum(bool(v.strip()) for row in cells for v in row.values())
            evidence["status_text_hits"] += [v for row in cells for v in row.values() if TERMS.search(v)]
        evidence["distributions"]=distributions
    assert sha(p)==before
    return evidence

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    prepared=ROOT/"outputs/data_preparation"
    manifest=read(prepared/"split_manifest.csv")
    mlookup={r["sample_id"]:r for r in manifest}
    rows=[r for g in ("positive","negative") for r in read(prepared/f"{g}_all.csv")]
    all_source=list((ROOT/"src").rglob("*"))
    source_files=[p for p in all_source if p.is_file()]
    source_before={str(p):sha(p) for p in source_files}
    workbooks=[]
    for index,p in enumerate(sorted((ROOT/"src").rglob("*.xlsx"))):
        workbooks.append(inspect(p))
        if (index+1)%200==0: print("Reviewed workbooks:",index+1,flush=True)
    (OUT/"workbook_evidence.json").write_text(json.dumps(workbooks,ensure_ascii=False,indent=2),encoding="utf-8")
    by_condition={}
    for w in workbooks:
        p=Path(w["path"])
        match=re.fullmatch(r"Results_([\d.]+)MPa_([\d.]+)MPa_(positive|negative).xlsx",p.name)
        if not match: raise ValueError(p)
        key=(match[3],float(match[1]),float(match[2]))
        if key in by_condition: raise ValueError("Duplicate result workbook condition")
        by_condition[key]=w
    decisions=[]; comparisons=[]; used=set(); groups=defaultdict(list)
    for r in rows:
        key=(r["group"],float(r["p_system"]),float(r["p_ocean"]))
        used.add(key)
        w=by_condition.get(key)
        issues=[]
        if w is None: issues.append("missing_result_workbook")
        else:
            result=w["result"]
            # CSV exports use 6 decimal places; compare at that stated precision.
            for target in ("ff","f","q"):
                if not math.isclose(float(r[target]),float(result[target]),rel_tol=0,abs_tol=0.500001e-6):
                    issues.append("result_target_mismatch:"+target)
            if float(result["p_system"])!=key[1]: issues.append("result_pressure_mismatch")
            for name,d in w["distributions"].items():
                if d.get("parse_error") or not d.get("finite"): issues.append("distribution_invalid:"+name)
            if key in (("negative",15.0,0.0),("negative",20.0,0.0)): issues.append("known_ff_review")
        cols=[f"{c}{i}" for c in ("x","y","cpress") for i in range(1,222)]
        nodes=np.array([float(r[c]) for c in cols])
        groups[(r["group"],hashlib.sha256(nodes.tobytes()).hexdigest())].append(r["sample_id"])
        decisions.append(dict(sample_id=r["sample_id"],group=r["group"],source_file=r["source_file"],
            source_row=r["source_row"],p_system=r["p_system"],p_ocean=r["p_ocean"],split=mlookup[r["sample_id"]]["split"],
            numerical_status="pass" if np.isfinite(nodes).all() else "invalid",
            evidence_status="review" if issues else "no_detected_data_error",
            convergence_status="not_verifiable_from_export",action="retain_original",
            issues=";".join(issues),result_workbook=w["path"] if w else ""))
        if key[2] <= 1 and key[1] in (10,15,20,25):
            comparisons.append(dict(group=key[0],p_system=key[1],p_ocean=key[2],split=mlookup[r["sample_id"]]["split"],
                **{k: w["result"].get(k) for k in ("ff","f","q","mn","num","sp","pnum","T")},
                **{f"{name}_{field}":d.get(field) for name,d in w["distributions"].items()
                   for field in ("minimum","maximum","unique_values")}))
    related=[{"group":k[0],"sample_ids":v,"evidence":"identical_663_node_values"} for k,v in groups.items() if len(v)>1]
    summary=dict(workbooks=len(workbooks),training_records=len(rows),
        source_file_types=dict(Counter(p.suffix for p in source_files)),
        status_text_hits=[{"path":w["path"],"hits":w["status_text_hits"]} for w in workbooks if w["status_text_hits"]],
        extra_result_conditions=[list(k) for k in sorted(set(by_condition)-used)],
        evidence_status=dict(Counter(r["evidence_status"] for r in decisions)),
        constant_distributions=[{"path":w["path"],"sheet":name,"value":d["minimum"]}
           for w in workbooks for name,d in w["distributions"].items() if d.get("unique_values")==1],
        identical_node_associations=related,
        source_hashes_unchanged=all(sha(Path(p))==h for p,h in source_before.items()),
        manifest_sha256=sha(prepared/"split_manifest.csv"))
    assert summary["source_hashes_unchanged"]
    write(OUT/"record_decisions.csv",decisions)
    write(OUT/"anomaly_neighbors.csv",comparisons)
    (OUT/"review_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in ("constant_distributions","identical_node_associations","status_text_hits")},ensure_ascii=True),flush=True)
    print("Constant distribution count:",len(summary["constant_distributions"]),flush=True)
    print("Identical node groups:",len(related),"Status hits:",len(summary["status_text_hits"]),flush=True)

if __name__=="__main__": main()
