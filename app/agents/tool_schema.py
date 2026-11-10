"""内置 Agent 的工具集定义（OpenAI function-calling 格式）。

设计原则
--------
1. **参数比最终契约简单**：契约里的 `lines` 是 `[{who,text,voice,dur}]`，
   工具里只要 `lines: ["台词1", "台词2"]` —— `dur` 由后端按字长算，
   `who` 缺省取节点级 `who`。少一层嵌套，模型少犯错、少烧 token。
2. **先固定拓扑，再分批填充**：骨架一次提交完整图。台词每次最多填充
   `MAX_NODES_PER_CALL` 个节点，以控制长篇的单次输出长度。
3. **ref 而不是 id**：写节点时用本章临时键 `ref`（如 `c03-s04`），
   连接与分支用 ref 引用，后端统一分配正式 id。模型不必猜 id 是否冲突。
4. 工具名即 `agent_name.tool_name`，与 `registry.AGENTS` 里的白名单一一对应。
"""

MAX_NODES_PER_CALL = 20        # 单次 write_nodes 硬上限
MAX_LINES_PER_NODE = 6         # 单节点台词上限（长篇允许连续独白）

# 精简节点：工具入参结构（落盘时由后端展开成完整 Node）
NODE_INPUT_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['ref', 'type', 'title'],
    'properties': {
        'ref': {
            'type': 'string',
            'description': '本章临时键，如 c03-s04。只在本章内唯一，用于连边和分支引用。',
        },
        'type': {
            'type': 'string',
            'enum': ['scene', 'choice', 'ending'],
            'description': 'scene=普通叙事；choice=给玩家选项；ending=结局。起点节点由系统自动建，不要写。',
        },
        'title': {
            'type': 'string',
            'description': '节点标题，8 字以内，用于画布显示（如「天台·雨」）。',
        },
        'who': {
            'type': 'string',
            'description': '说话人角色 id（取自 list_characters）；旁白留空字符串。',
        },
        'lines': {
            'type': 'array',
            'items': {'type': 'string'},
            'maxItems': MAX_LINES_PER_NODE,
            'description': '台词数组，一句一个元素。单句 8~40 字，不写句号。',
        },
        'bg_prompt': {
            'type': 'string',
            'description': '背景画面描述，给生图用。写清地点、时间、天气、光线、氛围，不要写画风。',
        },
        'bg_key': {
            'type': 'string',
            'description': '背景复用键。同一场景连续多节点填同一个键（如 tenshou_rain），可省掉重复生图。',
        },
        'chars': {
            'type': 'array',
            'description': '本节点在场角色与姿态，最多 3 个。',
            'maxItems': 3,
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['id'],
                'properties': {
                    'id': {'type': 'string', 'description': '角色 id'},
                    'pose': {'type': 'string', 'description': '姿态（站/坐/背身/低头…）'},
                    'pos': {'type': 'string', 'enum': ['left', 'center', 'right']},
                    'mood': {'type': 'string', 'description': '情绪（平静/紧张/勉强笑…）'},
                },
            },
        },
        'choices': {
            'type': 'array',
            'description': 'type=choice 时必填，2~4 项；其余类型不要填。',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['label', 'to'],
                'properties': {
                    'label': {'type': 'string', 'description': '选项文字，16 字以内，不写句号'},
                    'to': {'type': 'string', 'description': '目标节点的 ref'},
                    'condition': {'type': 'string', 'description': '可选条件表达式，一般不填'},
                },
            },
        },
        'end_kind': {
            'type': 'string',
            'enum': ['true', 'good', 'bad', 'alt'],
            'description': 'type=ending 时必填：true=真结局（最难达成、信息量最大），'
                           'good=好结局（两全的走法），bad=坏结局（某种关系不可挽回），'
                           'alt=分歧结局（另一种合理的生活，谈不上好坏）。',
        },
        'branch_kind': {
            'type': 'string',
            'enum': ['key', 'flavor'],
            'description': 'type=choice 时填：key=必要分支（直接决定走向哪个结局，一个章节 1~2 个就够），'
                           'flavor=非必要分支（对话/氛围不同，最后汇合回主线，不影响结局，'
                           '可以多铺来给玩家互动感）。不填按 key 处理。',
        },
    },
}


# 骨架和填充的 schema 分开，模型在填充阶段看不到可修改连线的字段。
SKELETON_NODE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['ref', 'type', 'title', 'to'],
    'properties': {
        'ref': {'type': 'string', 'description': '全图唯一的临时节点编号'},
        'type': {'type': 'string', 'enum': ['start', 'scene', 'choice', 'ending']},
        'title': {'type': 'string', 'description': '画布标题，最多 8 字'},
        'act': {'type': 'integer', 'description': '可选展示分组，从 1 开始'},
        'chapter': {'type': 'string', 'description': '可选展示分组内的标签'},
        'to': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4,
               'description': '后继 ref，按选项顺序排列。结局传空数组'},
        'labels': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4,
                   'description': '分支选项文案，和 to 按顺序对应'},
        'exit_kinds': {'type': 'array', 'maxItems': 4,
                       'items': {'type': 'string',
                                 'enum': ['chapter', 'true', 'good', 'bad', 'alt']},
                       'description': '每个出口的去向，和 to 按顺序一一对应：'
                                      'chapter=进下一章，true/good/bad/alt=切对应结局。'
                                      '留空则按目标节点自动推断'},
        'branch_kind': {'type': 'string', 'enum': ['key', 'flavor']},
        'end_kind': {'type': 'string', 'enum': ['true', 'good', 'bad', 'alt']},
        'summary': {'type': 'string', 'description': '给填充阶段看的节点梗概，20–40 字'},
        'effects': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4,
                    'description': '和 to 按顺序对应：选这个出口时置 True 的旗标名，空串表示不改'},
    },
}

SKELETON_PROPERTIES = {
    'nodes': {'type': 'array', 'minItems': 2, 'items': SKELETON_NODE_SCHEMA},
    'logline': {'type': 'string', 'description': '一句话故事内核'},
    'acts': {
        'type': 'array',
        'items': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['id', 'title'],
            'properties': {
                'id': {'type': 'string'},
                'title': {'type': 'string'},
                'chapters': {'type': 'array', 'items': {'type': 'string'}},
            },
        },
    },
    'meta': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            **{field: {'type': 'integer'} for field in ('target_nodes', 'depth', 'endings')},
            # §4.3 生成器参数，validate_skeleton 按它们强制 I9–I11（template_version ≥ 2）。
            **{field: {'type': 'integer'} for field in ('template_version', 'rejoin_max', 'depth_max')},
            'choice_density': {'type': 'number'},
            'branch_len': {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 2},
            'source': {'type': 'string', 'enum': ['model', 'mock']},
            'degraded_reason': {'type': 'string'},
            'structure_warnings': {'type': 'array', 'items': {'type': 'string'}},
            'characters': {
                'type': 'array',
                'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['id', 'name'],
                    'properties': {
                        **{field: {'type': 'string'}
                           for field in ('id', 'name', 'look', 'color', 'voice_id', 'note')},
                        'gender': {'type': 'string', 'enum': ['male', 'female', 'other']},
                    },
                },
            },
        },
    },
}

FILL_NODE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['id', 'lines'],
    'properties': {
        'id': {'type': 'string', 'description': '骨架已分配的正式节点编号，如 n18'},
        'lines': {
            'type': 'array', 'minItems': 1, 'maxItems': MAX_LINES_PER_NODE,
            'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['who', 'text'],
                'properties': {
                    'who': {'type': 'string', 'description': '已登记的说话人 id，旁白留空'},
                    'text': {'type': 'string', 'description': '单句 8–40 字，无句号'},
                },
            },
        },
        'bg_prompt': {'type': 'string', 'description': '可选背景画面描述，同一布景复用相同描述'},
        'chars': {
            'type': 'array',
            'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['id'],
                'properties': {
                    'id': {'type': 'string', 'description': '当前节点中已经在场的角色 id'},
                    'mood': {'type': 'string'},
                    'stance': {'type': 'string', 'enum': ['stand', 'sit', 'lean', 'lie', 'walk']},
                },
            },
        },
    },
}


def _fn(name, desc, props=None, required=None):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': desc,
            'parameters': {
                'type': 'object',
                'additionalProperties': False,
                'properties': props or {},
                'required': required or [],
            },
        },
    }


# ---------------- 编剧 ----------------
WRITER_TOOLS = [
    _fn('project_brief',
        '读项目需求、生成参数和当前进度。每次开工第一件事就调它，不要凭记忆猜需求。'),
    _fn('write_skeleton',
        '提交整张剧情图的骨架。全图校验通过后一次提交并冻结节点和出口，后续只填内容。',
        SKELETON_PROPERTIES, ['nodes']),
    _fn('fill_segment',
        '给骨架中已有的一批节点填台词。用 node_ids 或 chapter_id 选择待写节点，'
        '一次最多 20 个。可提供 nodes 内容，省略则由编剧根据梗概生成。',
        {
            'chapter_id': {'type': 'string', 'description': '可选展示分组，自动取组内下一批待填节点'},
            'node_ids': {'type': 'array', 'items': {'type': 'string'},
                         'minItems': 1, 'maxItems': MAX_NODES_PER_CALL},
            'nodes': {'type': 'array', 'items': FILL_NODE_SCHEMA,
                      'minItems': 1, 'maxItems': MAX_NODES_PER_CALL},
        },
    ),
    _fn('plan_structure',
        '提交幕/章的总体结构。全书只调一次（续写时先调 get_structure 看有没有）。'
        '每章要给出目标节点数和一句话梗概。',
        {
            'logline': {'type': 'string', 'description': '一句话故事内核，40 字以内'},
            'target_minutes': {'type': 'integer', 'description': '目标游玩时长（分钟）'},
            'acts': {
                'type': 'array',
                'description': '幕列表，3~6 幕',
                'items': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['title', 'chapters'],
                    'properties': {
                        'title': {'type': 'string'},
                        'goal': {'type': 'string', 'description': '这一幕的戏剧任务，一句话'},
                        'chapters': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'additionalProperties': False,
                                'required': ['title', 'beats'],
                                'properties': {
                                    'title': {'type': 'string'},
                                    'beats': {'type': 'string', 'description': '本章要点，2~3 句'},
                                    'node_budget': {'type': 'integer', 'description': '本章计划节点数'},
                                },
                            },
                        },
                    },
                },
            },
        },
        ['logline', 'target_minutes', 'acts'],
    ),
    _fn('get_structure', '读已有的结构与各章完成状态，用来续写或避免重复规划。'),
    _fn('list_chapters',
        '列出所有章节及其状态（pending/writing/done）和已写节点数，用来决定下一步写哪章。',
        {'status': {'type': 'string', 'enum': ['pending', 'writing', 'done', 'all'],
                    'description': '按状态过滤，默认 all'}}),
    _fn('get_chapter',
        '读某一章的详细信息：梗概、已写节点（含 ref 与台词前 20 字）、已用角色、已连边。'
        '写新节点前必须先调，避免剧情断裂和 ref 冲突。',
        {'chapter_id': {'type': 'string', 'description': '章节 id'}},
        ['chapter_id'],
    ),
    _fn('write_nodes',
        '仅旧模式可用。把一批节点写进某一章。'
        f'单次最多 {MAX_NODES_PER_CALL} 个节点；建议 8~15 个。'
        '写完立刻返回本批 ref → 正式 id 的映射。不要试图一次写完一整章以上的内容。',
        {
            'chapter_id': {'type': 'string'},
            'nodes': {'type': 'array', 'items': NODE_INPUT_SCHEMA,
                      'minItems': 1, 'maxItems': MAX_NODES_PER_CALL},
        },
        ['chapter_id', 'nodes'],
    ),
    _fn('connect_nodes',
        '仅旧模式可用。给节点连边（普通剧情推进用）。choice 节点的分支用 choices 表达，不要用这个连。'
        '改已有出口要传 replace=true，并确保原本可达的节点仍可达。',
        {
            'from': {'type': 'string', 'description': '出口节点的 ref 或 id'},
            'to': {'type': 'string', 'description': '入口节点的 ref 或 id'},
            'replace': {'type': 'boolean',
                        'description': '来源节点已有出口时，改指向 to。插入一段时，先把段尾接回原后继，'
                                       '再改入口。会切断可达路线的修改会被拒绝。默认 false'},
        },
        ['from', 'to'],
    ),
    _fn('list_characters', '读角色表：id、名字、配色、是否已有立绘、音色。写台词前先看有哪些人。'),
    _fn('upsert_character',
        '新增或修改角色。取名要克制，一部 1~2 小时的作品主要角色 3~6 人，不要超过 8 人。',
        {
            'id': {'type': 'string', 'description': '角色 id（英文小写，如 heroine）'},
            'name': {'type': 'string', 'description': '显示名'},
            'color': {'type': 'string', 'description': '名字配色，十六进制如 #7c3aed'},
            'voice_id': {'type': 'string', 'description': '音色 id，留空则用默认'},
            'note': {'type': 'string', 'description': '这个人的说话方式、口头禅、禁忌'},
            'look': {'type': 'string',
                     'description': '外形锚点：发色发型、衣着、标志性特征，一句话。'
                                    '会逐字复用到每一张立绘 —— 留空这个角色每次都会变样'},
            'gender': {'type': 'string', 'enum': ['male', 'female', 'other'],
                       'description': '性别。写台词时用它决定「他/她」。'
                                      '参考图预建的角色会自动带上，别覆盖成 other'},
        },
        ['id', 'name'],
    ),
    _fn('validate_graph',
        '校验图结构：断边、孤儿节点、死路、choice 无分支、ending 缺类型、from ref 不存在。'
        '每章写完调一次；全书完成后调 scope=all。',
        {'scope': {'type': 'string', 'enum': ['chapter', 'all'],
                   'description': 'chapter 需要配 chapter_id'}},
         ['scope'],
    ),
    _fn('finish_chapter',
        '标记本章写完，并写一段 3~5 句的前情摘要。后面章节只能看到摘要，看不到全文 —— '
        '所以伏笔、人物关系变化、已用过的桥段必须写进摘要。',
        {
            'chapter_id': {'type': 'string'},
            'summary': {'type': 'string', 'description': '前情摘要，3~5 句，200 字以内'},
        },
        ['chapter_id', 'summary'],
    ),
    # ---- 断点续写：先看证据，再自己下判断 ----
    # 这两步是给「崩过一次、接着写」准备的。为什么不写死规则：
    # 「哪一章算写完」没有放之四海皆准的标准 —— 有的章 finish 了但节点只有 3 个，
    # 有的章没 finish 却已经写得很实。变化率这么大，交给看过证据的编剧自己判。
    _fn('resume_evidence',
        '续写前先读这份证据：上次崩在哪、每章真实写了多少、盘上的图和检查点对不对得上、'
        '有没有顺序断裂。它只给事实，不替你下结论 —— 判完用 set_resume_plan 落你的决定。'),
    _fn('set_resume_plan',
        '把「从哪章续、哪几章要接着补」的决定写进图的状态，后面就按它执行。'
        '被列进 redo 的章节会从「已完成」退回「写作中」继续补，不会清空已有节点。',
        {
            'resume_from': {'type': 'string', 'description': '从哪一章开始写（章节 id）'},
            'redo': {'type': 'array', 'items': {'type': 'string'},
                     'description': '要退回继续补的章节 id，可以空。只在证据显示它没写实时才列'},
            'reason': {'type': 'string', 'description': '为什么这么判，一两句，会写进运行日志'},
        },
        ['resume_from', 'reason'],
    ),
]

# ---------------- 美术 ----------------
ART_TOOLS = [
    _fn('list_pending_assets',
        '列出还缺图的素材，按缺图数量从多到少排。'
        '返回两块：`bases` 是要先画的**基底立绘**（每角色一张，透明底，长相锚点）；'
        '`backgrounds` 是按 bg_key 归并后的背景。'
        '`poses` 是**差分清单**：按「角色 × 姿态 × 表情」聚合，一条 = 一张图，覆盖一组节点。'
        '**顺序不能反**：先画完 bases，再画 poses —— 差分要拿基底当参考图，'
        '基底还没出时 poses 只会是空的。'),
    _fn('generate_image',
        '生成一张图并挂到目标上。背景按 bg_key 批量挂到所有同 key 节点；立绘挂到角色。'
        '同一 bg_key 只生成一次，重复调用会跳过。'
        '立绘默认出竖版且自动抠成透明底，文件名是「序号-中文短语」，用户能直接看懂。'
        '立绘分两种 key：裸角色 id 出**基底**（一个角色一张）；'
        '`char:<id>:<姿态>:<表情>` 出**差分**（只改这一组节点，自动以基底为参考图做图生图，'
        '长相和透明底都继承基底）。差分 key 请从 list_pending_assets 的 poses 里原样取。',
        {
            'target': {'type': 'string', 'enum': ['background', 'pose']},
            'key': {'type': 'string',
                    'description': 'target=background 填 bg_key；target=pose 填角色 id（基底）'
                                   '或 char:<id>:<姿态>:<表情>（差分，如 char:heroine:sit:smile）'},
            'prompt': {'type': 'string', 'description': '画面描述。景色写地点/光/天气；立绘写角色外形特征'},
            'negative': {'type': 'string', 'description': '不要出现的内容'},
            'size': {'type': 'string',
                     'description': '如 1920*1080 或 768*1344。不填：背景用项目分辨率，'
                                    '立绘按项目分辨率长边自适应换算成 4:7 竖版'},
            'transparent': {'type': 'boolean',
                            'description': 'true = 这张图要无背景（透明底），主要用于立绘。'
                                           '立绘本来就会抠图；显式传 true 表示「交付必须带透明通道」，'
                                           '抠不出透明区域会直接报错而不是交出白底图'},
            'label': {'type': 'string',
                      'description': '给这张图起一个人能读懂的中文短名（8 字以内，如「雨夜天台」'
                                     '「她摊开手」）。会变成文件名 `01-雨夜天台.png`。'
                                     '不填就按地点/角色名+姿态自动命名'},
            'force': {'type': 'boolean',
                      'description': 'true = 跳过所有复用（项目内已有 / 素材库命中），强制重新生成。'
                                     '单节点「重新生成」用这个'},
        },
        ['target', 'key', 'prompt'],
    ),
    _fn('check_consistency',
        '检查角色一致性：同一角色在各节点的立绘来源是否相同、配色是否统一。'
        '返回有问题的角色列表，没有则返回空。'),
    _fn('set_style',
        '定下全片唯一的风格圣经（配色 / 画法 / 光影 / 字体 / 负面词）。'
        '一部作品只定一次；之后所有出图都必须先读它。'
        '不定就出图，立绘、背景、界面会变成三套画风 —— 这是最常见的崩坏。',
        {
            'palette': {'type': 'array', 'items': {'type': 'string'},
                        'description': '色板，按 主色 / 辅色 / 强调色 / 中性色 顺序，如 #1a1a2e'},
            'medium': {'type': 'string', 'description': '画法：赛璐璐 / 厚涂 / 水墨 / 描线 / 平涂'},
            'light': {'type': 'string', 'description': '光影基调：色温、对比度、主光方向'},
            'typeface': {'type': 'string', 'description': '字体方案：标题 / 正文 / UI 各用什么'},
            'negative': {'type': 'array', 'items': {'type': 'string'},
                         'description': '全局负面词，每张图都带'},
            'refs': {'type': 'array', 'items': {'type': 'string'},
                     'description': '参考图路径（素材库里已有的），只给生图模型看'},
            'overwrite': {'type': 'boolean', 'description': '已定过时是否覆盖，默认 false'},
        },
        ['palette', 'medium']),
    _fn('draft_prompt',
        '按风格圣经生成一条可直接用的生图提示词，不要自己随手编。'
        '返回 prompt 与 negative，原样交给 generate_image。'
        '风格统一靠这一步，跳过它就会出现三套画风。',
        {
            'target': {'type': 'string', 'enum': ['background', 'character', 'ui', 'typography', 'fx']},
            'key': {'type': 'string', 'description': 'background 填 bg_key；character 填角色 id；ui 填界面名'},
            'brief': {'type': 'string',
                      'description': '这次具体画什么：地点/光线/天气，或角色的姿态与情绪'},
        },
        ['target', 'brief']),
    _fn('generate_ui',
        '生成界面与视觉资产（启动页 / 对话框 / 存档 / 设置 / 标题栏 / 选项 / 字体参考 / 特效），'
        '按风格圣经统一。返回文件路径，同时登记进 agent_state.ui。',
        {
            'surface': {'type': 'string',
                        'enum': ['title', 'dialog', 'save', 'settings', 'hud', 'choice',
                                 'typography', 'fx']},
            'prompt': {'type': 'string', 'description': '界面描述；一般先用 draft_prompt(target=ui) 生成'},
            'size': {'type': 'string', 'description': '如 2560*1600，留空用项目分辨率'},
            'negative': {'type': 'string', 'description': '不要出现的内容'},
        },
        ['surface', 'prompt']),
    _fn('check_style',
        '验收关卡：查风格圣经是否齐全、角色有没有外形锚点、已出的背景是否套用了圣经。'
        '只查元数据（有没有按规范做），不查画面好不好看。收尾必调。'),
]

# ---------------- 配音 ----------------
VOICE_TOOLS = [
    _fn('list_pending_lines',
        '列出还没有语音的台词（按章节分页）。每条给出 line 引用、文本、建议音色。',
        {
            'chapter_id': {'type': 'string', 'description': '只看某一章，留空看全部'},
            'limit': {'type': 'integer', 'description': '最多返回多少条，默认 40'},
        }),
    _fn('synthesize_line',
        '合成一条台词的语音，并把真实时长写回该句的 dur。'
        '时长写回后节点节奏才准 —— 不要跳过这一步。',
        {
            'chapter_id': {'type': 'string'},
            'node_ref': {'type': 'string', 'description': '节点 ref 或 id'},
            'line_index': {'type': 'integer', 'description': '第几句，从 0 开始'},
            'voice_id': {'type': 'string', 'description': '音色，留空用角色默认'},
        },
        ['chapter_id', 'node_ref', 'line_index'],
    ),
    _fn('batch_synthesize',
        '批量合成一章里所有缺语音的台词。比逐条调快，但失败时整批可重试。',
        {'chapter_id': {'type': 'string'}, 'overwrite': {'type': 'boolean', 'description': '是否重做已有的'}},
        ['chapter_id'],
    ),
]

# ---------------- 装配 ----------------
BUILD_TOOLS = [
    _fn('validate_graph',
        '全书结构校验。装配前必须调，返回 error 列表；有 error 不许导出。',
        {'scope': {'type': 'string', 'enum': ['all']}},
        ['scope']),
    _fn('check_assets',
        '检查素材完整性：每个节点是否有背景、立绘、语音；报告缺什么，按节点列出。'),
    _fn('run_export',
        '导出可玩包。renpy=生成 Ren\'Py 工程；web=生成网页版。'
        '导出前内部会再校验一次，有阻塞性错误直接拒绝并返回原因。',
        {'format': {'type': 'string', 'enum': ['renpy', 'web']}},
        ['format']),
]

# ---------------- 界面 ----------------
UI_TOOLS = [
    _fn('list_ui_presets',
        '列出全部可选的界面风格预设：id、名字、气质一句话、适合什么题材、菜单排列方式。'
        '开工第一件事先调它 —— 界面必须从预设里挑一套，不许从零自由发挥，'
        '否则启动页、对话框、存档页会变成三种视觉语言。'),
    _fn('match_ui_preset',
        '按关键词给预设打分排序，返回每套的 score 与命中的词。用户给的是氛围词'
        '（「水墨 江南 雨夜」「校园 清新 治愈」「科幻 孤独」）时用它。'
        '**返回空列表 = 没有一个预设接得住这些词** —— 要如实说明并给最接近的候选，'
        '不要硬套一个气质完全不对的。',
        {'keywords': {'type': 'string',
                      'description': '用户给的关键词或一句氛围描述，原样传入（不要自己改写）'}},
        ['keywords']),
    _fn('render_ui',
        '用指定预设渲染一块界面并落盘，返回 HTML 路径（可直接打开）。'
        'title=启动页；dialog/choice/save/settings/hud 是游戏内界面。'
        '同一套预设渲染多块界面时视觉语言自动保持一致。'
        '渲染完必须自己截图核对，不能只看返回 ok。',
        {
            'preset': {'type': 'string', 'description': '预设 id（取自 list_ui_presets / match_ui_preset）'},
            'screen': {'type': 'string',
                       'enum': ['title', 'dialog', 'choice', 'save', 'settings', 'hud'],
                       'description': '要渲染哪块界面，默认 title'},
            'title': {'type': 'string', 'description': '作品标题，显示在启动页'},
            'subtitle': {'type': 'string', 'description': '英文副标题，默认 WHALE FALL WORKSHOP'},
            'menu': {'type': 'array', 'items': {'type': 'string'},
                     'description': '菜单项，默认 开始游戏/继续游戏/读取存档/设置；4 项最稳，最多 6 项'},
            'version': {'type': 'string', 'description': '版本号文案'},
            'bg': {'type': 'string', 'description': '背景图路径（不带背景的预设会忽略它）'},
            'char_left': {'type': 'string', 'description': '左侧立绘路径（只有水墨预设用）'},
            'char_right': {'type': 'string', 'description': '右侧立绘路径（只有水墨预设用）'},
        },
        ['preset']),
    _fn('tweak_ui_preset',
        '不换预设，只改少量参数（主色/强调色/标题字号/字距/菜单排列），生成变体。'
        '**优先换预设而不是改配色** —— 只允许在「预设对了但一笔不对」时用它。'
        '改完后续 render_ui 会自动套用这些覆盖。',
        {
            'preset': {'type': 'string', 'description': '要微调的预设 id'},
            'accent': {'type': 'string', 'description': '强调色 #RRGGBB'},
            'bg_color': {'type': 'string', 'description': '底色 #RRGGBB'},
            'fg_color': {'type': 'string', 'description': '文字色 #RRGGBB'},
            'title_size': {'type': 'integer', 'description': '标题字号 px，40~140'},
            'letter_spacing': {'type': 'string', 'description': '字距，如 0.10em'},
            'menu': {'type': 'string', 'enum': ['vertical', 'horizontal', 'numbered'],
                     'description': '菜单排列方式'},
            'reset': {'type': 'boolean', 'description': 'true = 清掉这一套的所有覆盖，回到原预设'},
        },
        ['preset']),
    _fn('check_ui',
        '界面验收关卡：预设是否存在、HTML 是否落盘、菜单项够不够、标题是否为空、'
        '字号是否过小、图片引用是否真存在、有没有残留占位符。收尾必调；'
        '有 issues 必须修完再回报，不许跳过。'),
    _fn('design_ui',
        '按用户的「文字要求 + 参考图」设计一套新的界面风格，并渲染主界面 / 对话界面。'
        '这是「从要求生成预设」的唯一入口：先分析气质与参考图配色，'
        '再沿最贴近的内置骨架生成一套新预设，最后用同一套渲染器出图 —— '
        '六块界面仍是同一套视觉语言，不会散架。'
        '参考图只用于取色和判断明暗，原图不落盘、不进成品。'
        '菜单排列（竖排/横排/编号）四套骨架目前是写死的，这一项不会生效：'
        '返回里的 notes 会明确写「没做」，不要跟用户说已经改了。',
        {
            'requirement': {
                'type': 'string',
                'description': '用户给的风格要求，原样传入（可以多行），不要自己改写或总结。'
                               '留空也可以：会用项目题材挑最贴近的一套',
            },
            'reference_images': {
                'type': 'array', 'items': {'type': 'string'},
                'description': '参考图的 dataURL，最多 6 张；只用来取色，不落盘',
            },
            'target': {
                'type': 'string', 'enum': ['title', 'dialog', 'both'],
                'description': '设计哪块界面：title=主界面（标题画面），dialog=对话界面，默认 both',
            },
            'base': {
                'type': 'string',
                'description': '强制从哪套骨架出发（取自 list_ui_presets）；'
                               '留空就按要求和参考图自动挑',
            },
        }),
]


# ---------------- 质检 ----------------
# 独立于 writer/art/voice/build 的第 5 个 agent：只检查，不改东西。
REVIEW_TOOLS = [
    _fn('inspect_graph',
        '跑一遍成品检查，返回发现的问题列表。scope 可以只查一类，也可以 all 全查。'
        'structure=起点/出边/可达性/分支死路；text=空台词/占位符/超长句/重复句；'
        'assets=背景/立绘/语音/文件是否存在；consistency=角色立绘漂移/背景 key 冲突；'
        'scale=节点数和目标时长偏差。'
        '返回里带 checked（真正跑过的项）—— 没跑的不要算进去。',
        {'scope': {'type': 'string',
                   'enum': ['structure', 'text', 'assets', 'consistency', 'scale', 'all'],
                   'description': '查哪一类，默认 all'}},
        ['scope']),
    _fn('file_report',
        '把这次检查的结论写进项目，用户会在完成面板里看到。'
        'verdict：pass=没发现 error 且 warn 不多；warn=有将就的地方但能玩；'
        'fail=有 error，不能交付。score 100 起，每条 error −15、每条 warn −4，最低 0。'
        'summary 是一句人话结论（60 字以内）。items 逐条列问题。'
        '**没真跑过检查不许调用它**。',
        {
            'verdict': {'type': 'string', 'enum': ['pass', 'warn', 'fail']},
            'score': {'type': 'integer', 'description': '0-100'},
            'summary': {'type': 'string', 'description': '一句话结论，≤60 字'},
            'items': {
                'type': 'array',
                'description': '问题清单',
                'items': {
                    'type': 'object',
                    'properties': {
                        'level': {'type': 'string', 'enum': ['error', 'warn']},
                        'code': {'type': 'string'},
                        'node_id': {'type': 'string'},
                        'where': {'type': 'string'},
                        'message': {'type': 'string'},
                    },
                    'required': ['level', 'code', 'message'],
                },
            },
            'checked': {'type': 'array', 'items': {'type': 'string'},
                        'description': '真正跑过的检查项'},
        },
        ['verdict', 'score', 'summary']),
]


TOOLS = {
    'writer': WRITER_TOOLS,
    'art': ART_TOOLS,
    'voice': VOICE_TOOLS,
    'build': BUILD_TOOLS,
    'ui': UI_TOOLS,
    'review': REVIEW_TOOLS,
}


def tool_names(agent):
    return [t['function']['name'] for t in TOOLS.get(agent, [])]
