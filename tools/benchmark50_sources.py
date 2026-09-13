"""Curated official-document acquisition for an opt-in, reproducible KB benchmark.

Downloaded documents stay in ignored output, separate from redistributable code.
This is a fixed-URL research tool, not the production arbitrary-URL importer.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'benchmark50'

# key, domain, URL, CSS selection. A narrow function/section retains its heading.
SOURCES = []
for name in ['units', 'boundary', 'lattice', 'pair_style', 'neighbor', 'timestep', 'minimize', 'fix_nh', 'fix_deform', 'compute_stress_atom']:
    SOURCES.append(('MAT-'+name, '材料仿真', 'https://docs.lammps.org/'+name+'.html', 'section'))
for group, name in [('Timer','TON'),('Timer','TOF'),('Timer','TP'),('Trigger','R_TRIG'),('Trigger','F_TRIG'),('Counter','CTU'),('Counter','CTD'),('Counter','CTUD'),('Bistable-Function-Blocks','RS'),('Bistable-Function-Blocks','SR')]:
    SOURCES.append(('PLC-'+name, 'PLC自动化', f'https://content.helpme-codesys.com/en/libs/Standard/Current/{group}/{name}.html', 'section'))
for name, anchor in [('transaction-iso','XACT-READ-COMMITTED'),('transaction-iso','XACT-REPEATABLE-READ'),('transaction-iso','XACT-SERIALIZABLE'),('ddl-constraints','DDL-CONSTRAINTS-CHECK-CONSTRAINTS'),('ddl-constraints','DDL-CONSTRAINTS-NOT-NULL'),('ddl-constraints','DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS'),('ddl-constraints','DDL-CONSTRAINTS-PRIMARY-KEYS'),('ddl-constraints','DDL-CONSTRAINTS-FK'),('indexes-partial',''),('indexes-multicolumn','')]:
    SOURCES.append(('DB-'+(anchor or name), '数据库', f'https://www.postgresql.org/docs/16/{name}.html'+('#'+anchor if anchor else ''), '#'+anchor if anchor else '.sect1'))
for module, name in [('json','dump'),('json','load'),('csv','reader'),('csv','writer'),('csv','DictReader'),('csv','DictWriter'),('statistics','mean'),('statistics','median'),('statistics','stdev'),('statistics','pstdev')]:
    SOURCES.append(('PY-'+name,'数据处理',f'https://docs.python.org/3.12/library/{module}.html#{module}.{name}', '[id="'+module+'.'+name+'"]'))
for name in ['StandardScaler','MinMaxScaler','RobustScaler','OneHotEncoder','OrdinalEncoder']:
    SOURCES.append(('ML-'+name,'统计建模',f'https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.{name}.html', 'article'))
for module, name in [('impute','SimpleImputer'),('impute','KNNImputer'),('model_selection','train_test_split'),('model_selection','KFold'),('model_selection','StratifiedKFold')]:
    SOURCES.append(('ML-'+name,'统计建模',f'https://scikit-learn.org/stable/modules/generated/sklearn.{module}.{name}.html', 'article'))

def acquire(entry):
    key, domain, url, selector = entry
    destination = OUT/'sources'/f'{key}.txt'
    receipt = destination.with_suffix('.json')
    if receipt.exists() and destination.exists():
        return json.loads(receipt.read_text('utf-8'))
    r = requests.get(url, timeout=60, headers={'User-Agent':'KnowledgePipelineEvaluation/0.1'}, allow_redirects=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, 'html.parser')
    node = soup.select_one(selector)
    if node is None:
        raise ValueError(f'{key}: source selector missing {selector}')
    if node.name == 'dt':
        description = node.find_next_sibling('dd')
        text = node.get_text(' ',strip=True)+'\n'+description.get_text('\n',strip=True)
    elif node.name == 'a':
        node = node.find_parent(class_=['sect2','sect3']) or node.parent.parent
        text = node.get_text('\n',strip=True)
    else:
        for junk in node.select('script,style,nav,.headerlink,.sphinxsidebar'):
            junk.decompose()
        text = node.get_text('\n',strip=True)
    # Preserve the full selected text locally. Per-card scoped excerpts are frozen separately.
    if len(text)<100:
        raise ValueError(f'{key}: extracted source too short')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(text,encoding='utf-8')
    record={'key':key,'domain':domain,'url':url,'selector':selector,'characters':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest(),'collected_at':datetime.now(timezone.utc).isoformat(),'file':str(destination.relative_to(OUT))}
    receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record

if __name__=='__main__':
    def safe(entry):
        try:
            r=acquire(entry)
            print(r['key'],r['characters'],flush=True)
            return r
        except Exception as exc:
            print(entry[0],type(exc).__name__,str(exc),flush=True)
            return {'key':entry[0],'error':str(exc)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        result=list(pool.map(safe,SOURCES))
    (OUT/'source-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
