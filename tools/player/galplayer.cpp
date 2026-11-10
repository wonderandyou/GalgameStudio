// 鲸落工坊 · 全屏播放器启动器
//
// 为什么用 g++ 自己编译而不是 PyInstaller：
//   这台机器开着 Smart App Control（强制）。PyInstaller 打的 exe 里那个 bootloader
//   是从 PyPI 下载的预编译二进制，双击会被静默拦掉（没窗口、没日志、没进程）。
//   而本机编译出来的 exe，SAC 直接放行 —— 桌面上那些能双击的 20 KB 启动器就是这么来的。
//
// 窗口模式对标社区做法：
//   Electron 的 { frame: false, fullscreen: true } / { kiosk: true }
//   → 这里用 Edge 的 --kiosk --edge-kiosk-type=fullscreen，效果一样：
//     无标题栏、无地址栏、无任务栏，Alt+F4 退出。
//
// 编译（目录和文件名都用纯英文，中文会让 ld 失败）：
//   D:\w64devkit\bin\g++.exe -std=c++17 -O2 -s -mwindows ^
//       -o tools\player\galplayer.exe tools\player\galplayer.cpp -lshell32
//
// 用法：
//   galplayer.exe                  → 全屏打开同目录的 index.html
//   galplayer.exe <url 或 html>    → 全屏打开指定目标

#include <windows.h>
#include <shellapi.h>
#include <string>

static bool FileExists(const wchar_t* path) {
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static bool IsRemote(const std::wstring& s) {
    return s.rfind(L"http://", 0) == 0 || s.rfind(L"https://", 0) == 0 ||
           s.rfind(L"file://", 0) == 0;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int) {
    wchar_t exePath[MAX_PATH] = {0};
    GetModuleFileNameW(NULL, exePath, MAX_PATH);
    std::wstring dir(exePath);
    dir = dir.substr(0, dir.find_last_of(L'\\') + 1);

    // 目标：命令行给的优先，否则同目录 index.html
    std::wstring target;
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        if (argc > 1) target = argv[1];
        LocalFree(argv);
    }
    if (target.empty()) target = dir + L"index.html";

    if (!IsRemote(target)) {
        // 相对路径按 exe 目录补全
        if (target.size() > 1 && target[1] != L':') target = dir + target;
        if (!FileExists(target.c_str())) {
            std::wstring msg = L"找不到要播放的页面：\n\n" + target +
                               L"\n\n先用「鲸落工坊」导出一次网页版，"
                               L"再把本程序放进导出的文件夹里。";
            MessageBoxW(NULL, msg.c_str(), L"鲸落工坊 · 播放器", MB_ICONWARNING);
            return 1;
        }
        std::wstring local = L"file:///" + target;
        for (wchar_t& c : local) {
            if (c == L'\\') c = L'/';
        }
        target = local;
    }

    const wchar_t* candidates[] = {
        L"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        L"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    };
    const wchar_t* edge = nullptr;
    for (const wchar_t* candidate : candidates) {
        if (FileExists(candidate)) {
            edge = candidate;
            break;
        }
    }
    if (edge == nullptr) {
        MessageBoxW(NULL,
                    L"没找到 Microsoft Edge。\n\n"
                    L"可以考虑改用导出里的 index.html，直接双击用浏览器打开。",
                    L"鲸落工坊 · 播放器", MB_ICONWARNING);
        return 1;
    }

    // ★ 用 --app + --start-fullscreen，**不要**用 --kiosk。
    //   实测（pwsh 里逐条验证）：
    //     --app=<url> --start-fullscreen   → 无边框全屏，标题是页面标题 ✓
    //     --kiosk <url>                    → 退化成普通 Edge 窗口（带标签栏）✗
    //   --app 起来的窗口没有地址栏、没有标签页，加 start-fullscreen 就是满屏，
    //   等价于 Electron 的 { frame: false, fullscreen: true }。
    std::wstring params = L"--app=";
    params += target;
    params += L" --start-fullscreen --window-size=1600,900 --no-first-run "
              L"--disable-features=Translate --autoplay-policy=no-user-gesture-required";

    HINSTANCE result = ShellExecuteW(NULL, L"open", edge, params.c_str(),
                                     dir.c_str(), SW_SHOWNORMAL);
    if (reinterpret_cast<INT_PTR>(result) <= 32) {
        std::wstring msg = L"没能启动播放器（错误码 ";
        msg += std::to_wstring(reinterpret_cast<INT_PTR>(result));
        msg += L"）。\n\n可以直接双击同目录的 index.html 用浏览器打开。";
        MessageBoxW(NULL, msg.c_str(), L"鲸落工坊 · 播放器", MB_ICONWARNING);
        return 1;
    }
    return 0;
}
