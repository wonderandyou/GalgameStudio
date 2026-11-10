import json, sys, re
from pathlib import Path
S = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(S))
from app.engine.export_mock import export_web, _pick_preset
from app.ui_presets import all_presets
root = S / 'projects' / 'p_63b2b758e0ab46d1bf4201629468eb36'
g = json.loads((root / 'graph.json').read_text(encoding='utf-8'))
res = {}
for p in [None] + [x['id'] for x in all_presets()]:
    o = export_web(root, g, preset=p)
    h = (o / 'index.html').read_text('utf-8')
    res[str(p)] = dict(marker=re.findall(r'ui_preset: ([\w-]+)', h)[:1], len=len(h),
                       play=(o / 'play.html').is_file(), href_play='play.html' in h)
res['default_pick'] = _pick_preset(g)['id']
print(json.dumps(res, ensure_ascii=False))
