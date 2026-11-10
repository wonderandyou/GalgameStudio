// 鲸落工坊 · Unity 命令行构建器
//
// 用法（Unity 装好后）：
//   Unity.exe -batchmode -quit -projectPath <项目目录> ^
//             -executeMethod Builder.BuildWindows -logFile <日志>
//
// 为什么需要它：Unity 没有"一条命令出 exe"的开关，正式做法就是
// 写一个 static 方法挂在 -executeMethod 上，里面调 BuildPipeline.BuildPlayer。
//
// 窗口设置按社区标准：PlayerSettings.fullScreenMode = FullScreenWindow
//（这就是 Unity 版的 borderless fullscreen，和 Ren'Py 的 preferences.fullscreen 等价）
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class Builder
{
    const string ScenePath = "Assets/Scenes/Main.unity";

    // 成品名：从 StreamingAssets/product.json 读（用户在「打包」页填的游戏名称）。
    // 静态字段是为了让 BuildWindows 和 ApplyPlayerSettings 共用同一个值。
    static string productTitle = "鲸落工坊";

    [System.Serializable]
    class ProductInfo { public string title; public string icon; }

    [MenuItem("鲸落工坊/构建 Windows")]
    public static void BuildWindows()
    {
        PrepareScene();
        ApplyPlayerSettings();
        EnsureUIShaderResource();

        Directory.CreateDirectory("Build");
        // exe 文件名也用成品名：以前写死「鲸落工坊.exe」，用户改名后文件名不跟着变
        string exeName = string.IsNullOrWhiteSpace(productTitle) ? "game" : productTitle;
        foreach (char bad in Path.GetInvalidFileNameChars()) exeName = exeName.Replace(bad, '_');
        var options = new BuildPlayerOptions
        {
            scenes = new[] { ScenePath },
            locationPathName = "Build/" + exeName + ".exe",
            target = BuildTarget.StandaloneWindows64,
            options = BuildOptions.None,
        };

        BuildReport report = BuildPipeline.BuildPlayer(options);
        BuildSummary summary = report.summary;
        if (summary.result == BuildResult.Succeeded)
        {
            Debug.Log("BUILD_OK 输出=" + Path.GetFullPath(options.locationPathName) +
                      " 大小=" + summary.totalSize);
            EditorApplication.Exit(0);
        }
        else
        {
            Debug.LogError("BUILD_FAILED " + summary.result +
                           " 错误数=" + summary.totalErrors);
            EditorApplication.Exit(1);
        }
    }

    // Build 必须有场景。这里用代码生成一个空场景并保存，
    // 界面全靠 GalgameFlow 的 RuntimeInitializeOnLoadMethod 自建，
    // 所以场景本身是空的没关系。
    //
    // ⚠ 但"空场景"连相机都没有 —— 没有相机时 Unity 不清屏，
    //   屏幕上是未初始化的图形缓冲（实测整屏品红 #FF00FF）。
    //   所以这里强行补一个只负责清屏的相机。
    static void PrepareScene()
    {
        Directory.CreateDirectory("Assets/Scenes");
        if (!File.Exists(ScenePath))
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.Refresh();
        }

        // 打开场景，确认有相机；没有就加一个并保存
        var opened = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        bool hasCamera = false;
        foreach (var root in opened.GetRootGameObjects())
        {
            if (root.GetComponentInChildren<Camera>() != null) { hasCamera = true; break; }
        }
        if (!hasCamera)
        {
            var cameraGo = new GameObject("MainCamera");
            cameraGo.tag = "MainCamera";
            var camera = cameraGo.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.05f, 0.06f, 0.09f, 1f);
            camera.orthographic = true;
            camera.cullingMask = 0;          // 只清屏，不渲染任何物体（UI 走 Overlay）
            camera.depth = -100;
            EditorSceneManager.SaveScene(opened, ScenePath);
            Debug.Log("已给场景补上清屏相机");
        }

        // 塞进 Build Settings 的列表里，编辑器里点播放也能跑
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
    }

    // UGUI 的 shader 放在 package 里，项目里没有任何 Resources / 材质引用它，
    // Unity 打包时会把它当"没用到"剔掉 —— 结果是运行时 UI 全变成品红色。
    //
    // ⚠ 别用 GraphicsSettings 的 m_AlwaysIncludedShaders：
    //   实测在 Unity 6.0.85 上加进去会让 BuildPipeline 在写
    //   unity_builtin_extra 时断言失败（'m_LockCount == 0'）并导致构建失败。
    //   改用"Resources 里放一个引用该 shader 的材质"这个官方稳做法：
    //   只要 Resources 里有引用，打包就一定保留 shader，且不动打包管线。
    static void EnsureUIShaderResource()
    {
        Directory.CreateDirectory("Assets/Resources");
        var wanted = new[]
        {
            ("UIShaderKeeper", "UI/Default"),
            ("SpriteShaderKeeper", "Sprites/Default"),
            ("TextShaderKeeper", "GUI/Text Shader"),
            ("TextShaderKeeper2", "TextMeshPro/Distance Field"),
            ("FontShaderKeeper", "UI/Default Font"),
        };
        foreach (var (assetName, shaderName) in wanted)
        {
            string path = "Assets/Resources/" + assetName + ".mat";
            if (File.Exists(path)) { Debug.Log("材质已存在，跳过：" + path); continue; }
            var shader = Shader.Find(shaderName);
            if (shader == null) { Debug.LogWarning("找不到 shader：" + shaderName); continue; }
            var material = new Material(shader);
            material.name = assetName;
            AssetDatabase.CreateAsset(material, path);
            Debug.Log("已建保底材质 " + path + " → " + shaderName);
        }
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
    }

    static void ApplyPlayerSettings()
    {
        // 先读成品信息（打包页填的游戏名 + 图标）
        string iconDataUrl = null;
        string infoPath = Path.Combine(Application.streamingAssetsPath, "product.json");
        if (File.Exists(infoPath))
        {
            try
            {
                var info = JsonUtility.FromJson<ProductInfo>(
                    File.ReadAllText(infoPath, System.Text.Encoding.UTF8));
                if (info != null)
                {
                    if (!string.IsNullOrWhiteSpace(info.title)) productTitle = info.title.Trim();
                    if (!string.IsNullOrEmpty(info.icon)) iconDataUrl = info.icon;
                }
            }
            catch (System.Exception e)
            {
                Debug.LogWarning("读 product.json 失败，用默认名字：" + e.Message);
            }
        }
        Debug.Log("成品名 = " + productTitle);

        PlayerSettings.companyName = "Jingluo";
        PlayerSettings.productName = productTitle;
        // 必须用屏幕原生分辨率：写死 1920x1080 再拉全屏，上下会留黑边
        //（实测 2560x1600 屏幕上留了约 40px 的黑条）。
        PlayerSettings.defaultIsNativeResolution = true;
        PlayerSettings.fullScreenMode = FullScreenMode.FullScreenWindow;
        PlayerSettings.resizableWindow = false;
        PlayerSettings.runInBackground = true;
        PlayerSettings.visibleInBackground = true;
        // 关掉 Unity 自己的启动画面，避免一进游戏看到引擎 logo
        PlayerSettings.SplashScreen.show = false;

        ApplyIcon(iconDataUrl);
    }

    // 把打包页选的图标（data:image/png;base64,...）应用到 Windows 播放器图标上
    //
    // ⚠ Unity 要求「槽位数量严格匹配」：Standalone 有 8 个图标位，
    //   只给 1 张就会报 "8 expected while 1 were specified" 然后**静默不生效**
    //   （构建照样成功、exe 里还是默认立方体图标 —— 实测踩到）。
    //   所以先问 Unity 每个槽位要多大，再逐尺寸缩放后一起传。
    static void ApplyIcon(string dataUrl)
    {
        if (string.IsNullOrEmpty(dataUrl) || !dataUrl.StartsWith("data:image/"))
        {
            Debug.Log("没选图标，用 Unity 默认图标");
            return;
        }
        try
        {
            int comma = dataUrl.IndexOf(',');
            if (comma < 0) return;
            byte[] bytes = System.Convert.FromBase64String(dataUrl.Substring(comma + 1));
            var source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!source.LoadImage(bytes))
            {
                Debug.LogWarning("图标解码失败，跳过");
                return;
            }

            int[] sizes = PlayerSettings.GetIconSizesForTargetGroup(BuildTargetGroup.Standalone);
            if (sizes == null || sizes.Length == 0) sizes = new[] { 16, 24, 32, 48, 64, 128, 256, 256 };
            var icons = new Texture2D[sizes.Length];
            for (int i = 0; i < sizes.Length; i++) icons[i] = Resize(source, sizes[i]);
            PlayerSettings.SetIconsForTargetGroup(BuildTargetGroup.Standalone, icons);
            // 必须落盘：batchmode 下 PlayerSettings 改了还在内存里，
            // 构建时读到的是 ProjectSettings.asset 里的旧值（实测不保存等于白设）
            AssetDatabase.SaveAssets();
            Debug.Log("已应用游戏图标（源 " + source.width + "x" + source.height +
                      "，槽位 " + sizes.Length + " 个：" + string.Join(",", sizes) + "）");
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("应用图标失败：" + e.Message);
        }
    }

    // 缩放纹理到指定边长（batchmode 下用 RenderTexture，不依赖编辑器导入设置）
    static Texture2D Resize(Texture2D src, int size)
    {
        var rt = RenderTexture.GetTemporary(size, size, 0, RenderTextureFormat.ARGB32);
        var previous = RenderTexture.active;
        Graphics.Blit(src, rt);
        RenderTexture.active = rt;
        var dst = new Texture2D(size, size, TextureFormat.RGBA32, false);
        dst.ReadPixels(new Rect(0, 0, size, size), 0, 0);
        dst.Apply();
        RenderTexture.active = previous;
        RenderTexture.ReleaseTemporary(rt);
        return dst;
    }
}
