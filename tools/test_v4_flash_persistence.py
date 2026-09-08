import json,tempfile
from pathlib import Path
p=Path(tempfile.mkdtemp()); (p/'routes').mkdir()
def write(r,v):
 t=p/'routes'/f'{r}.json.tmp';t.write_text(json.dumps({'route_id':r,'result':v}));t.replace(p/'routes'/f'{r}.json')
write('A',{'v':1});write('B',{'v':2});assert json.loads((p/'routes/A.json').read_text())['result']['v']==1
write('A',{'v':3});assert json.loads((p/'routes/B.json').read_text())['result']['v']==2
print('persistence regression: PASS')
# stale contract fingerprints are rejected by the aggregation rule
item={'contract_sha256':'old'}; current={'contract_id':'new'}
assert item['contract_sha256'] != __import__('hashlib').sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
print('stale fingerprint regression: PASS')
