"""Deterministic, varied dialogue for the built-in writer."""
from .tool_schema import MAX_NODES_PER_CALL


PROPS = (
    '折皱的车票', '没封口的信', '褪色的照片', '用旧的钥匙', '缺角的地图',
    '绣花的手帕', '没上弦的表', '磨花的相框', '半融的蜡烛', '擦旧的徽章',
    '泛黄的书页', '空了的信封', '冰冷的硬币', '忘带的雨伞',
)

SOUNDS = (
    '雨水打着窗玻璃', '楼道传来脚步声', '屋檐落下几滴雨', '钟声绕过了长街',
    '风吹响了旧门铃', '列车掠过站台边', '木窗轻轻地摇晃', '远处汽笛响起来',
    '暖气管低低作响', '楼下店门合上了', '街上的灯熄灭了', '水从石阶流下去',
    '风把窗帘吹起来',
)

SCENE_ACTIONS = (
    '她把{prop}放回原处，却仍望着门外的雨',
    '她把{prop}递到我手边，转头去关那扇窗',
    '她把{prop}压在灯下，借光看清背面的字',
    '她把{prop}藏进外套，又悄悄回头看了一眼',
)
SCENE_REACTIONS = (
    '{sound}，我还是把想问的话咽了回去',
    '{sound}，我等那点动静过去才敢说话',
    '{sound}，她说有些事情迟早得讲清楚',
    '{sound}，我在这时才发现天已经黑了',
    '{sound}，窗外的水光慢慢漫过了台阶',
)
KEY_MOMENTS = {
    'choice': ('她把{prop}递到我面前，等我自己做决定',
               '{sound}，现在开口就再也收不回去了'),
    'bad': ('她把{prop}留在桌上，独自走进夜里的雨幕',
            '{sound}，我终于明白她为什么没回头'),
    'alt': ('她把{prop}带出了门，走到长街尽头才停',
            '{sound}，这一次我们终于各自选了方向'),
    'true': ('她把{prop}放进我手里，默默陪我等到天亮',
             '{sound}，那句迟到很久的话终于说完了'),
}


def mock_lines(ordinal, index, *, node_type, end_kind=None):
    # Each chapter owns a stable range of indices, including after resume.
    variant = (ordinal - 1) * MAX_NODES_PER_CALL + index
    variant, prop = divmod(variant, len(PROPS))
    variant, sound = divmod(variant, len(SOUNDS))
    key = end_kind if node_type == 'ending' else node_type
    if key in KEY_MOMENTS:
        action, reaction = KEY_MOMENTS[key]
    else:
        variant, action_index = divmod(variant, len(SCENE_ACTIONS))
        action = SCENE_ACTIONS[action_index]
        reaction = SCENE_REACTIONS[variant % len(SCENE_REACTIONS)]
    return [action.format(prop=PROPS[prop]), reaction.format(sound=SOUNDS[sound])]
