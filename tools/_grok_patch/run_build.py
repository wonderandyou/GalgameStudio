import json, sys, time, traceback
from pathlib import Path
S = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(S))
out = Path(__file__).parent
log = open(out / 'run.log', 'w', encoding='utf-8', buffering=1)
def echo(*a): print(time.strftime('%H:%M:%S'), *a, file=log)
from app.engine.export_unity import export_unity_and_build
root = S / 'projects' / 'p_63b2b758e0ab46d1bf4201629468eb36'
g = json.loads((root / 'graph.json').read_text(encoding='utf-8'))
try:
    r = export_unity_and_build(root, g, timeout=1800, echo=echo)
except Exception as e:
    r = {'ok': False, 'type': type(e).__name__, 'error': str(e), 'tb': traceback.format_exc()}
(out / 'result.json').write_text(json.dumps(r, ensure_ascii=False, indent=1), 'utf-8')
echo('DONE', r.get('ok'))
