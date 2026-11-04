"""Record exact source-row reconciliation without mistaking a catalog for an implementation."""
from pathlib import Path
import json,hashlib
p=Path(__file__).parent;a=p/'assets';inv=json.loads((a/'inventory.json').read_text());homes=json.loads((a/'full-control-homes.json').read_text()).get('homes',{})
compact=json.loads((a/'compact-coverage.json').read_text()) if (a/'compact-coverage.json').exists() else {}
cm=compact.get('rows',{})
rows=[]
for r in inv['rows']:
 ident=r['identifier'];life=r['lifecycle'];scope=r['scope'];label=r.get('exact_label') or ident
 rows.append({'identifier':ident,'label':label,'scope':scope,'lifecycle':life,'source':r['source'],'wire':r.get('wire'),'inventory_section':r.get('proposed_menu_section'),'full_catalog_home':homes.get(ident),'compact_path_contract':cm.get(ident),'compact_contract_matches':all(cm.get(ident,{}).get(k)==r.get(k) for k in ['scope','lifecycle']),'disposition': 'external shell maintenance, not session configuration' if ident in ['controller_restart','plugin_restart','close_external'] else 'external before Start' if life=='startup-only' else 'external recovery / consent guard' if life in ['recovery-only','confirmation-only'] else 'preserve original gesture and document options destination' if r.get('exact_label') is None else 'runtime XR entry / child editor; validate revised prototype path','status':'catalog routes reconciled; native behavior remains future work' if ident in cm and ident in homes else 'revised artifact validation pending'})
assert len({r['identifier'] for r in rows})==len(inv['rows'])
result={'rows':rows,'count':len(rows),'full_catalog_missing':[r['identifier'] for r in rows if not r['full_catalog_home']], 'snapshots':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in a.iterdir() if f.suffix in ['.html','.json','.md']}}
(p/'reconciliation.json').write_text(json.dumps(result,indent=2));print('Reconciled source rows:',len(rows),'missing full catalog IDs:',result['full_catalog_missing']);print('Compact path contracts:',sum(r['compact_contract_matches'] for r in rows),'/',len(rows));assert len(cm)==len(rows);assert all(r['compact_contract_matches'] for r in rows);assert compact.get('inventory_sha256')==hashlib.sha256((a/'inventory.json').read_bytes()).hexdigest()
