// 鲸落工坊 · Unity 播放器
//
// 设计要点（为什么这样写）：
//   1. **所有界面用代码创建**，不依赖场景里摆好的 prefab —— 这样能纯命令行
//      （-batchmode -executeMethod）构建，不需要有人在编辑器里手动拖控件。
//   2. 用 `RuntimeInitializeOnLoadMethod` 自启动，连场景都不需要。
//   3. 全屏无边框：`Screen.fullScreenMode = FullScreenWindow`，
//      这就是社区标准做法（Unity 官方 API，和 Ren'Py 的 borderless fullscreen 等价）。
//   4. 中文字体用 `Font.CreateDynamicFontFromOSFont("Microsoft YaHei")`，
//      不把字体文件塞进游戏（省体积也避免授权问题）。
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.Networking;
using UnityEngine.UI;

public class GalgameFlow : MonoBehaviour
{
    GraphData graph;
    Dictionary<string, NodeData> byId = new Dictionary<string, NodeData>();
    NodeData node;
    int index;

    RawImage background;
    Dictionary<string, RawImage> poseSlots = new Dictionary<string, RawImage>();
    Dictionary<string, AspectRatioFitter> poseFitters = new Dictionary<string, AspectRatioFitter>();
    Text titleText;
    Text lineText;
    RectTransform choiceRoot;
    AudioSource voice;
    Font font;

    // 用 AfterSceneLoad 而不是 BeforeSceneLoad：
    // 场景还没加载时 DontDestroyOnLoad 不生效，对象会被随后的场景加载清掉。
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Bootstrap()
    {
        var go = new GameObject("GalgameFlow");
        DontDestroyOnLoad(go);
        go.AddComponent<GalgameFlow>();
    }

    void Awake()
    {
        // ⚠ 不要写 Screen.fullScreen = true —— 它会把刚刚设好的
        //   FullScreenWindow 覆盖成独占全屏（ExclusiveFullScreen）。
        //   独占模式下 DWM 让位，窗口内容不进桌面合成层，
        //   外部截图抓不到，显示也只剩残影。只设 fullScreenMode 就够了。
        Screen.fullScreenMode = FullScreenMode.FullScreenWindow;
        Screen.sleepTimeout = SleepTimeout.NeverSleep;
        Application.runInBackground = true;

        // 显式对齐屏幕原生分辨率。只设 fullScreenMode 而渲染尺寸还是默认值时，
        // 窗口会把画面拉伸并留出上下黑边（实测约 40px）。
        Resolution native = Screen.currentResolution;
        if (Screen.width != native.width || Screen.height != native.height)
        {
            Screen.SetResolution(native.width, native.height, FullScreenMode.FullScreenWindow);
        }

        Debug.Log("[鲸] Awake 屏幕=" + Screen.width + "x" + Screen.height +
                  " 模式=" + Screen.fullScreenMode);

        font = Font.CreateDynamicFontFromOSFont(
            new[] { "Microsoft YaHei", "SimHei", "Arial" }, 32);
        Debug.Log("[鲸] 字体=" + (font == null ? "NULL" : font.name));

        BuildUI();
        Debug.Log("[鲸] BuildUI 完成");
    }

    void BuildUI()
    {
        var canvasGo = new GameObject("Canvas");
        canvasGo.transform.SetParent(transform, false);
        var canvas = canvasGo.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = 0;

        // 按钮要能点，必须两样东西同时存在：
        //   1. Canvas 上的 GraphicRaycaster（把鼠标位置转成 UI 命中）
        //   2. 场景里的 EventSystem（分发点击事件）
        // 少任何一个，按钮都收不到点击（实测点了完全没反应）。
        canvasGo.AddComponent<GraphicRaycaster>();
        if (EventSystem.current == null)
        {
            var eventGo = new GameObject("EventSystem",
                typeof(EventSystem), typeof(StandaloneInputModule));
            DontDestroyOnLoad(eventGo);
            Debug.Log("[鲸] 已创建 EventSystem");
        }
        var scaler = canvasGo.AddComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920, 1080);
        scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
        scaler.matchWidthOrHeight = 0.5f;

        // 背景铺满
        background = NewImage("Background", canvasGo.transform, Color.white);
        Stretch(background.rectTransform, 0, 0, 0, 0);

        // 三个立绘槽位：左 / 中 / 右
        foreach (var side in new[] { "left", "center", "right" })
        {
            var img = NewImage("Pose_" + side, canvasGo.transform, new Color(1, 1, 1, 0));
            var rt = img.rectTransform;
            float cx = side == "left" ? 0.22f : (side == "right" ? 0.78f : 0.5f);
            rt.anchorMin = new Vector2(cx, 0f);
            rt.anchorMax = new Vector2(cx, 1f);
            rt.pivot = new Vector2(0.5f, 0f);
            rt.sizeDelta = new Vector2(700, 0);
            rt.anchoredPosition = Vector2.zero;
            // RawImage 没有 preserveAspect（那是 Image 的属性），
            // 用 AspectRatioFitter 按原图比例控宽，避免立绘被拉伸变形。
            var fitter = img.gameObject.AddComponent<AspectRatioFitter>();
            fitter.aspectMode = AspectRatioFitter.AspectMode.HeightControlsWidth;
            fitter.aspectRatio = 0.5f;
            poseSlots[side] = img;
            poseFitters[side] = fitter;
        }

        // 台词条
        var bar = NewImage("LineBar", canvasGo.transform, new Color(0f, 0f, 0f, 0.62f));
        var barRt = bar.rectTransform;
        barRt.anchorMin = new Vector2(0.06f, 0.05f);
        barRt.anchorMax = new Vector2(0.94f, 0.30f);
        barRt.offsetMin = Vector2.zero;
        barRt.offsetMax = Vector2.zero;

        titleText = NewText("Title", bar.transform, 26, new Color(1f, 1f, 1f, 0.55f));
        var titleRt = titleText.rectTransform;
        titleRt.anchorMin = new Vector2(0f, 0.74f);
        titleRt.anchorMax = new Vector2(1f, 1f);
        titleRt.offsetMin = new Vector2(32, 0);
        titleRt.offsetMax = new Vector2(-32, -8);

        lineText = NewText("Line", bar.transform, 34, Color.white);
        var lineRt = lineText.rectTransform;
        lineRt.anchorMin = new Vector2(0f, 0f);
        lineRt.anchorMax = new Vector2(1f, 0.74f);
        lineRt.offsetMin = new Vector2(32, 16);
        lineRt.offsetMax = new Vector2(-32, 0);

        // 分支按钮容器
        var rootGo = new GameObject("Choices", typeof(RectTransform));
        rootGo.transform.SetParent(canvasGo.transform, false);
        choiceRoot = rootGo.GetComponent<RectTransform>();
        choiceRoot.anchorMin = new Vector2(0.5f, 0.34f);
        choiceRoot.anchorMax = new Vector2(0.5f, 0.34f);
        choiceRoot.pivot = new Vector2(0.5f, 0f);
        choiceRoot.sizeDelta = new Vector2(760, 0);
        var layout = rootGo.AddComponent<VerticalLayoutGroup>();
        layout.spacing = 14;
        layout.childAlignment = TextAnchor.MiddleCenter;
        layout.childControlHeight = false;
        layout.childForceExpandHeight = false;

        voice = gameObject.AddComponent<AudioSource>();
        voice.playOnAwake = false;

        var canvasRt = canvasGo.GetComponent<RectTransform>();
        Debug.Log("[鲸] Canvas rect=" + canvasRt.rect + " scale=" + canvasRt.localScale +
                  " 子对象=" + canvasGo.transform.childCount);
        Debug.Log("[鲸] Background rect=" + background.rectTransform.rect +
                  " color=" + background.color);
        DumpCanvas(canvasGo.transform);
    }

    // 排查用：把画布下每个元素的真实 rect / 颜色 / 字体都打出来，
    // 免得靠猜"为什么看不见"。
    void DumpCanvas(Transform root)
    {
        for (int i = 0; i < root.childCount; i++)
        {
            var child = root.GetChild(i);
            var rt = child.GetComponent<RectTransform>();
            string info = "[鲸] 子[" + i + "] " + child.name + " active=" + child.gameObject.activeSelf;
            if (rt != null) info += " rect=" + rt.rect + " anchor=" + rt.anchorMin + "~" + rt.anchorMax;
            var img = child.GetComponent<RawImage>();
            if (img != null)
                info += " color=" + img.color +
                        " tex=" + (img.texture == null ? "null" : img.texture.width + "x" + img.texture.height);
            Debug.Log(info);

            for (int j = 0; j < child.childCount; j++)
            {
                var gc = child.GetChild(j);
                var grt = gc.GetComponent<RectTransform>();
                var gtxt = gc.GetComponent<Text>();
                string g = "[鲸]   └[" + i + "." + j + "] " + gc.name;
                if (grt != null) g += " rect=" + grt.rect;
                if (gtxt != null)
                    g += " font=" + (gtxt.font == null ? "NULL" : gtxt.font.name) +
                         " size=" + gtxt.fontSize + " 色=" + gtxt.color +
                         " 文本=[" + gtxt.text + "]";
                Debug.Log(g);
            }
        }
    }

    static RawImage NewImage(string name, Transform parent, Color color)
    {
        var go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        var img = go.AddComponent<RawImage>();
        img.color = color;
        return img;
    }

    static void Stretch(RectTransform rt, float left, float bottom, float right, float top)
    {
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = new Vector2(left, bottom);
        rt.offsetMax = new Vector2(-right, -top);
    }

    Text NewText(string name, Transform parent, int size, Color color)
    {
        var go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        var text = go.AddComponent<Text>();
        text.font = font;
        text.fontSize = size;
        text.color = color;
        text.alignment = TextAnchor.UpperLeft;
        text.horizontalOverflow = HorizontalWrapMode.Wrap;
        text.verticalOverflow = VerticalWrapMode.Truncate;
        return text;
    }

    IEnumerator Start()
    {
        string path = Path.Combine(Application.streamingAssetsPath, "graph.json");
        string url = path.Contains("://") ? path : "file://" + path.Replace("\\", "/");
        Debug.Log("[鲸] 读 graph: " + path + "  存在=" + File.Exists(path));
        using (var request = UnityWebRequest.Get(url))
        {
            yield return request.SendWebRequest();
            Debug.Log("[鲸] 请求结果=" + request.result + " 错误=" + request.error +
                      " 字节=" + (request.downloadHandler == null ? -1 : request.downloadHandler.data.Length));
            if (request.result != UnityWebRequest.Result.Success)
            {
                lineText.text = "读不到 graph.json：" + request.error + "\n" + url;
                Debug.LogError("[鲸] 读不到 graph.json: " + request.error);
                yield break;
            }
            graph = JsonUtility.FromJson<GraphData>(request.downloadHandler.text);
        }

        if (graph == null || graph.nodes == null || graph.nodes.Length == 0)
        {
            lineText.text = "graph.json 里没有节点";
            Debug.LogError("[鲸] graph.json 里没有节点");
            yield break;
        }
        Debug.Log("[鲸] 节点数=" + graph.nodes.Length);
        foreach (var item in graph.nodes) byId[item.id] = item;

        string startId = graph.nodes[0].id;
        foreach (var item in graph.nodes)
        {
            if (item.type == "start") { startId = item.id; break; }
        }
        Debug.Log("[鲸] 起始节点=" + startId);
        Show(startId);
    }

    // 让游戏自己截一张渲染结果 —— 桌面 CopyFromScreen 在 DPI 缩放下
    // 抓 D3D 画面不可靠，ScreenCapture 走引擎内部，抓到的就是真实画面。
    // 玩家按 F12 触发，平时不落盘。
    IEnumerator AutoShot()
    {
        yield return null;
        string dir = @"D:\软件\GalgameStudio\tools\shots";
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir,
            "shot_" + System.DateTime.Now.ToString("HHmmssfff") + ".png");
        ScreenCapture.CaptureScreenshot(path);
        Debug.Log("[鲸] F12 截图 → " + path);
    }

    void Show(string id)
    {
        if (!byId.TryGetValue(id, out node))
        {
            lineText.text = "（这个分支还没写内容：" + id + "）";
            return;
        }
        index = 0;
        ApplyBackground(node.bg != null ? node.bg.asset : null);
        ApplyPoses(node.chars);
        Render();
    }

    // StreamingAssets 在 Windows 桌面版就是**普通目录**，直接 File.ReadAllBytes 读就行。
    // 用协程 + UnityWebRequest 反而要多绕一层（而且迭代器里不能嵌套 yield）。
    Texture2D LoadTextureSync(string relative)
    {
        if (string.IsNullOrEmpty(relative)) return null;
        string full = Path.Combine(Application.streamingAssetsPath,
                                   relative.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(full)) return null;
        try
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!texture.LoadImage(File.ReadAllBytes(full))) return null;
            return texture;
        }
        catch (System.Exception error)
        {
            Debug.LogWarning("读图失败 " + relative + "：" + error.Message);
            return null;
        }
    }

    void ApplyBackground(string relative)
    {
        background.texture = null;
        background.color = new Color(0.09f, 0.13f, 0.20f, 1f);
        var texture = LoadTextureSync(relative);
        if (texture != null)
        {
            background.texture = texture;
            background.color = Color.white;
        }
        Debug.Log("[鲸] 背景 path=[" + relative + "] 贴图=" +
                  (texture == null ? "NULL" : texture.width + "x" + texture.height));
    }

    void ApplyPoses(PoseData[] poses)
    {
        foreach (var slot in poseSlots.Values)
        {
            slot.texture = null;
            slot.color = new Color(1, 1, 1, 0);
        }
        if (poses == null) return;
        foreach (var pose in poses)
        {
            string side = string.IsNullOrEmpty(pose.pos) ? "center" : pose.pos;
            if (!poseSlots.ContainsKey(side)) side = "center";
            var texture = LoadTextureSync(pose.pose);
            if (texture != null)
            {
                poseSlots[side].texture = texture;
                poseSlots[side].color = Color.white;
                if (poseFitters.ContainsKey(side) && texture.height > 0)
                {
                    poseFitters[side].aspectRatio = (float)texture.width / texture.height;
                }
            }
        }
    }

    void Render()
    {
        titleText.text = node.title;

        foreach (Transform child in choiceRoot) Destroy(child.gameObject);

        if (node.lines == null || node.lines.Length == 0)
        {
            lineText.text = "";
        }
        else
        {
            var line = node.lines[Mathf.Clamp(index, 0, node.lines.Length - 1)];
            lineText.text = line.text;
            PlayVoice(line.voice);
        }

        bool lastLine = node.lines == null || index >= node.lines.Length - 1;
        Debug.Log("[鲸] Render 节点=" + node.id + " 标题=[" + titleText.text + "]" +
                  " 台词=[" + lineText.text + "] 行数=" +
                  (node.lines == null ? 0 : node.lines.Length) + " 索引=" + index);
        if (!lastLine) return;

        if (node.type == "ending")
        {
            AddButton("重新开始", () => Show(graph.nodes[0].id));
            return;
        }
        if (node.choices != null && node.choices.Length > 0)
        {
            foreach (var choice in node.choices)
            {
                string target = choice.to;
                AddButton(choice.label, () => Show(target));
            }
            return;
        }
        AddButton("重新开始", () => Show(graph.nodes[0].id));
    }

    void PlayVoice(string relative)
    {
        voice.Stop();
        if (string.IsNullOrEmpty(relative)) return;
        StartCoroutine(LoadVoice(relative));
    }

    IEnumerator LoadVoice(string relative)
    {
        string full = Path.Combine(Application.streamingAssetsPath, relative);
        string url = full.Contains("://") ? full : "file://" + full.Replace("\\", "/");
        using (var request = UnityWebRequestMultimedia.GetAudioClip(url, AudioType.WAV))
        {
            yield return request.SendWebRequest();
            if (request.result == UnityWebRequest.Result.Success)
            {
                var clip = DownloadHandlerAudioClip.GetContent(request);
                if (clip != null) voice.PlayOneShot(clip);
            }
        }
    }

    void AddButton(string label, System.Action action)
    {
        var go = new GameObject("Choice", typeof(RectTransform));
        go.transform.SetParent(choiceRoot, false);
        var img = go.AddComponent<Image>();
        img.color = new Color(0.16f, 0.23f, 0.34f, 0.94f);
        var rt = go.GetComponent<RectTransform>();
        rt.sizeDelta = new Vector2(720, 62);

        var textGo = new GameObject("Text", typeof(RectTransform));
        textGo.transform.SetParent(go.transform, false);
        var text = textGo.AddComponent<Text>();
        text.font = font;
        text.fontSize = 28;
        text.color = Color.white;
        text.alignment = TextAnchor.MiddleCenter;
        text.text = label;
        Stretch(text.rectTransform, 0, 0, 0, 0);

        var button = go.AddComponent<Button>();
        button.targetGraphic = img;
        button.onClick.AddListener(() => action());

        // 分支出现时把台词条压暗一点，视觉上把注意力给按钮
        lineText.color = new Color(1, 1, 1, 0.85f);
    }

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.F12))
        {
            StartCoroutine(AutoShot());
            return;
        }

        // 空格 / 回车 / 左键推进台词；有分支时左键不推进
        bool advance = Input.GetKeyDown(KeyCode.Space) ||
                       Input.GetKeyDown(KeyCode.Return) ||
                       Input.GetMouseButtonDown(0);
        if (!advance) return;
        if (node == null || node.lines == null) return;
        if (index < node.lines.Length - 1)
        {
            index++;
            Render();
        }
    }
}
