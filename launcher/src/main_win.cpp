// Windows entry point: pick the newest complete app-x.y.z folder and start it.
// Built as a GUI-subsystem program so no console window flashes.
#include <windows.h>

#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <string>

#include "launcher_core.hpp"

namespace fs = std::filesystem;
using namespace launcher;

namespace {

constexpr std::uintmax_t kMaxLogBytes = 256 * 1024;
// A version that exits with an error this soon after starting is treated as broken for this
// launch, and the next older version is started instead. A normal close exits with code 0.
constexpr DWORD kHealthWindowMs = 15 * 1000;

class Log {
public:
    explicit Log(const fs::path& root) {
        std::error_code ec;
        fs::path dir = root / "logs";
        fs::create_directories(dir, ec);
        fs::path file = dir / "launcher.log";
        // Keep the log small: start over once it grows past the limit.
        bool reset = fs::exists(file, ec) && fs::file_size(file, ec) > kMaxLogBytes;
        out_.open(file, reset ? std::ios::trunc : std::ios::app);
    }
    void line(const std::string& text) {
        if (!out_) return;
        std::time_t now = std::time(nullptr);
        char stamp[32];
        std::strftime(stamp, sizeof stamp, "%Y-%m-%d %H:%M:%S", std::localtime(&now));
        out_ << stamp << " " << text << "\n";
        out_.flush();
    }

private:
    std::ofstream out_;
};

fs::path module_path() {
    std::wstring buf(MAX_PATH, L'\0');
    for (;;) {
        DWORD n = GetModuleFileNameW(nullptr, buf.data(), static_cast<DWORD>(buf.size()));
        if (n == 0) return {};
        if (n < buf.size()) {
            buf.resize(n);
            return fs::path(buf);
        }
        buf.resize(buf.size() * 2);  // long path: retry with a bigger buffer
    }
}

std::string utf8(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0,
                                nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), n, nullptr,
                        nullptr);
    return s;
}

bool start(const Candidate& c, const fs::path& launcher_exe, const std::wstring& args, Log& log,
           HANDLE& process) {
    fs::path exe = c.dir / kAppExe;
    std::wstring command = quote_arg(exe.wstring());
    if (!args.empty()) command += L" " + args;

    // Tell the app where it was launched from (used by self-update to place new versions).
    SetEnvironmentVariableW(L"TOOLMANAGER_LAUNCHER", launcher_exe.c_str());
    SetEnvironmentVariableW(L"TOOLMANAGER_APP_DIR", c.dir.c_str());

    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    // Inherit the caller's working directory so relative arguments keep their meaning.
    if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr,
                        nullptr, &si, &pi)) {
        DWORD error = GetLastError();
        log.line("failed to start " + utf8(exe.wstring()) + " (error " + std::to_string(error) +
                 ")");
        return false;
    }
    AllowSetForegroundWindow(pi.dwProcessId);
    CloseHandle(pi.hThread);
    process = pi.hProcess;
    log.line("started " + c.version.str());
    return true;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    const fs::path launcher_exe = module_path();
    const fs::path root = launcher_exe.parent_path();
    Log log(root);

    const auto candidates = scan(root);
    for (const auto& c : candidates) {
        log.line("found " + c.version.str() +
                 (c.skip_reason.empty() ? "" : " - skipped: " + c.skip_reason));
    }

    const std::wstring args = args_after_program(GetCommandLineW());
    // Newest usable first. If a version cannot be started (e.g. a damaged exe) or crashes right
    // away, try the next older one and tell it which version failed so it can offer to disable it.
    for (const auto& c : candidates) {
        if (!c.skip_reason.empty()) continue;
        HANDLE process = nullptr;
        if (!start(c, launcher_exe, args, log, process)) continue;
        DWORD code = 0;
        bool exited = WaitForSingleObject(process, kHealthWindowMs) == WAIT_OBJECT_0;
        bool crashed = exited && GetExitCodeProcess(process, &code) && code != 0;
        CloseHandle(process);
        if (!crashed) return 0;
        log.line(c.version.str() + " exited with code " + std::to_string(code) +
                 " right after starting; trying an older version");
        const std::string version = c.version.str();  // ASCII digits and dots only
        const std::wstring failed(version.begin(), version.end());
        SetEnvironmentVariableW(L"TOOLMANAGER_FAILED_VERSION", failed.c_str());
    }

    log.line("no usable version");
    MessageBoxW(nullptr,
                L"找不到可用的 ToolManager 版本。\n\n請重新執行 ToolManager 安裝程式。\n"
                L"詳細資訊請見 logs\\launcher.log。",
                L"ToolManager", MB_ICONERROR | MB_OK);
    return 1;
}
