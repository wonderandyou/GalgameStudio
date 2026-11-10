// 奇迹工坊 · 桌面启动器
// 找到项目的 launcher\galapp.py，用本机 pythonw 拉起来（无黑框）。
// 编译：见同目录 build_launcher.ps1
#include <windows.h>
#include <shlwapi.h>
#include <cstdio>
#include <string>

static const wchar_t* kAppDir = L"D:\\\x8f6f\x4ef6\\GalgameStudio";   // D:\软件\GalgameStudio
static const wchar_t* kPython =
    L"C:\\Users\\q3153\\AppData\\Local\\Programs\\Python\\Python311\\pythonw.exe";

// 日志：优先 %LOCALAPPDATA%，退 %TEMP%，再退 exe 旁边。桌面 exe 写不了 LocalAppData 是踩过的坑。
static void log_line(const wchar_t* fmt, ...) {
    wchar_t msg[2048];
    va_list ap;
    va_start(ap, fmt);
    vswprintf_s(msg, 2048, fmt, ap);
    va_end(ap);

    // 日志写 exe 旁边最可靠（桌面 exe 写 %LOCALAPPDATA% 会静默失败，踩过）。
    // 退路：%TEMP%。文件名用中文，主人一眼能找到。
    wchar_t path[MAX_PATH] = {0};
    GetModuleFileNameW(nullptr, path, MAX_PATH);
    wchar_t* slash = wcsrchr(path, L'\\');
    if (slash) {
        *(slash + 1) = 0;
        wcscat_s(path, MAX_PATH, L"奇迹工坊-启动器日志.txt");
    }
    FILE* probe = nullptr;
    if (_wfopen_s(&probe, path, L"a") != 0 || !probe) {
        // exe 所在目录不可写（只读介质 / 权限受限）→ 退到 %TEMP%
        if (GetTempPathW(MAX_PATH, path)) {
            wcscat_s(path, MAX_PATH, L"奇迹工坊-启动器日志.txt");
        }
    } else {
        fclose(probe);
    }

    FILE* fp = nullptr;
    // 不能用 "a, ccs=UTF-8"：某些环境（尤其带 msys 运行时的）会直接打开失败、日志静默丢失。
    // 统一走「宽字符 → UTF-8 字节 → fwrite」，最稳。
    if (_wfopen_s(&fp, path, L"a") == 0 && fp) {
        SYSTEMTIME st;
        GetLocalTime(&st);
        wchar_t line[2300];
        swprintf_s(line, 2300, L"[%04d-%02d-%02d %02d:%02d:%02d] %ls\r\n",
                   st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, msg);
        char utf8[9216] = {0};
        int n = WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, sizeof(utf8) - 1, nullptr, nullptr);
        if (n > 0) fwrite(utf8, 1, (size_t)(n - 1), fp);
        fclose(fp);
    }
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    log_line(L"启动器启动");

    std::wstring script = std::wstring(kAppDir) + L"\\launcher\\galapp.py";
    if (!PathFileExistsW(script.c_str())) {
        log_line(L"找不到启动脚本：%ls", script.c_str());
        return 1;
    }
    if (!PathFileExistsW(kPython)) {
        log_line(L"找不到 pythonw：%ls", kPython);
        return 2;
    }

    // 命令行：pythonw.exe "galapp.py"
    std::wstring cmd = L"\"" + std::wstring(kPython) + L"\" \"" + script + L"\"";
    log_line(L"拉起：%ls", cmd.c_str());

    STARTUPINFOW si = {0};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi = {0};

    std::wstring mutable_cmd = cmd;                 // CreateProcessW 要可写缓冲
    if (!CreateProcessW(nullptr, mutable_cmd.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, kAppDir, &si, &pi)) {
        log_line(L"CreateProcess 失败，GetLastError=%lu", GetLastError());
        return 3;
    }

    log_line(L"已拉起，PID=%lu", pi.dwProcessId);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
