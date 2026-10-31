# ToolManager launcher

`ToolManager.exe` 是使用者實際點擊的程式：一個很小的 C++ 程式，只負責「挑出最新且完整的版本並啟動它」。
真正的 ToolManager（Python）放在旁邊的版本資料夾中，因此更新時永遠不需要覆寫執行中的檔案。

## 安裝目錄結構

```
%LOCALAPPDATA%\ToolManager\
  ToolManager.exe          ← 本 launcher
  defaults.toml            ← 部門預設值（選用，見 docs/DESIGN.md §9.1）
  app-1.2.0\
    ToolManagerApp.exe
    .complete              ← 最後寫入；沒有它的資料夾視為未完成，略過
  app-1.3.0\
    .disabled              ← 選用；標記為有問題的版本，略過
  logs\launcher.log
```

## 行為

1. 掃描與自己同一層的 `app-MAJOR.MINOR.PATCH` 資料夾，依版本**數值**排序（1.10.0 > 1.9.0）。
2. 略過：沒有 `.complete`、有 `.disabled`、缺少 `ToolManagerApp.exe` 的資料夾；名稱不是合法版本號的資料夾一律忽略。
3. 由新到舊嘗試啟動；若最新版無法啟動（例如 exe 損毀），自動改用下一個可用版本。
4. 原封不動轉傳命令列參數，並**沿用呼叫端的工作目錄**（相對路徑參數保持原意）。
5. 透過環境變數告訴 app：`TOOLMANAGER_LAUNCHER`（launcher 路徑，用來找 `defaults.toml`、放置新版本）、`TOOLMANAGER_APP_DIR`（目前版本資料夾）。
6. 沒有任何可用版本時，顯示錯誤訊息並結束。
7. 每次啟動寫入 `logs\launcher.log`（超過 256 KB 自動重寫）。

**launcher 從不刪除或修改任何檔案。** 舊版本資料夾的清理由 app 透過回收區機制處理（docs/DESIGN.md §6.3）。

## 建置與測試

```powershell
# Windows（Visual Studio Build Tools 或 MinGW）
cmake -S launcher -B build\launcher
cmake --build build\launcher --config Release
ctest --test-dir build\launcher -C Release --output-on-failure
```

```bash
# Linux：只建置並測試平台無關的核心邏輯
cmake -S launcher -B build/launcher && cmake --build build/launcher && build/launcher/launcher_tests

# Linux 交叉編譯 Windows 版（需 g++-mingw-w64-x86-64）
cmake -S launcher -B build/launcher-win -DCMAKE_TOOLCHAIN_FILE=mingw-toolchain.cmake \
      -DCMAKE_BUILD_TYPE=Release && cmake --build build/launcher-win
```

靜態連結 C++ runtime，目標電腦不需要安裝 VC++ 可轉散發套件。

完整打包（launcher + app）請用 `scripts\build_windows.ps1`。
