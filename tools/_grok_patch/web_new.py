def _pick_preset(graph, preset=None):
    """选界面预设：导参 > graph.style / project.ui_preset / project.style（id 或关键词）> 默认第一套。"""
    from ..ui_presets import all_presets, match
    presets = {p['id']: p for p in all_presets()}
    project = graph.get('project') or {}
    style = graph.get('style')
    if isinstance(style, dict):
        style = style.get('preset') or style.get('id') or style.get('ui_preset')
    for want in (preset, style, project.get('ui_preset'), project.get('style')):
        if not want:
            continue
        want = str(want)
        if want in presets:
            return presets[want]
        hits = match(want, limit=1)
        if hits:
            return presets[hits[0]['id']]
    return all_presets()[0]


def _title_page(graph, preset=None):
    """用 app.ui_presets 的四套预设之一渲染标题页；菜单「开始游戏/继续游戏」→ play.html。"""
    from ..ui_presets import render
    chosen = _pick_preset(graph, preset)
    project = graph.get('project') or {}
    title = str(project.get('name') or '鲸落工坊')[:60]
    nodes = graph.get('nodes') or []
    bg = next(((n.get('bg') or {}).get('asset') for n in nodes if (n.get('bg') or {}).get('asset')), '')
    poses = [c.get('pose') for n in nodes for c in (n.get('chars') or []) if c.get('pose')]
    poses = list(dict.fromkeys(poses))
    html = render(chosen, 'title', title=title,
                  subtitle=str(project.get('subtitle') or 'WHALE FALL WORKSHOP'),
                  bg=bg or '', char_left=poses[0] if poses else '',
                  char_right=poses[1] if len(poses) > 1 else '')
    hook = ('<script>document.querySelectorAll("a").forEach(function(a){'
            'var t=a.textContent;if(/开始|继续|START|Start|^0?1/.test(t)||a===document.querySelector("a"))'
            '{a.href="play.html"}});document.documentElement.dataset.preset=' +
            json.dumps(chosen['id']) + ';</script>')
    marker = f'<!-- ui_preset: {chosen["id"]} -->'
    return (html.replace('</body>', hook + '</body>') if '</body>' in html else html + hook) \
        .replace('<head>', '<head>' + marker, 1) if '<head>' in html else marker + html + hook
