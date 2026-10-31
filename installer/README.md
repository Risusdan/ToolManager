# ToolManager 安裝程式（Inno Setup）

`ToolManager.iss` 產生給同事**第一次安裝**用的 `ToolManagerSetup-<版本>.exe`。之後的更新由 ToolManager 自己處理（自我更新，見 docs/DESIGN.md §6.2），不需要再發安裝程式。

## 產生方式

一般由 `scripts\build_windows.ps1` 一起完成（偵測到 Inno Setup 6 時）：

```powershell
.\scripts\build_windows.ps1 -DefaultsFile D:\dept\defaults.toml
```

也可以單獨編譯（需先有 `dist\ToolManager\`）：

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 /DDefaultsFile=D:\dept\defaults.toml installer\ToolManager.iss
```

- `defaults.toml` 含部門內部網址，**不要放進 git**；只在打包時用 `/DDefaultsFile` 帶入。
- 想要中文安裝精靈：從 Inno Setup 網站的 Translations 頁面下載 `ChineseTraditional.isl`，放到 Inno Setup 安裝目錄的 `Languages\` 資料夾，重新編譯即可（腳本會自動偵測）。沒有的話，標準精靈畫面為英文，但我們自訂的訊息一律是中文。

## 安裝行為

| 項目 | 行為 |
|---|---|
| 權限 | 不需要系統管理員（per-user 安裝） |
| 位置 | 固定 `%LOCALAPPDATA%\ToolManager\`（與資料資料夾相同，因此不提供選擇） |
| 內容 | `ToolManager.exe`（launcher）、`app-<版本>\`、`defaults.toml`（若有提供） |
| 完成標記 | `app-<版本>\.complete` 最後才複製，launcher 不會使用複製到一半的版本 |
| 捷徑 | 開始功能表；桌面捷徑為選項（預設不勾選） |
| 應用程式清單 | 出現在「設定 → 應用程式」，可正常解除安裝 |
| 執行中 | 偵測 `ToolManagerAppMutex`，請使用者先關閉 ToolManager |
| 升級 | 直接執行新版安裝程式即可；舊版本資料夾由 ToolManager 之後自動清理 |

## 解除安裝

- 移除：launcher、所有 `app-*` 版本資料夾（包含自我更新下載的）、中斷下載的暫存資料夾、捷徑、應用程式清單項目。
- **保留**：已安裝的工具（`tools\`）、回收區、設定（`settings.toml`）、log、快取。完成時會顯示資料位置，需要時使用者可自行刪除。

## 開機自動啟動

目前**不提供**。ToolManager 還沒有系統匣常駐模式，開機自動啟動會直接跳出視窗；等 P4 加入系統匣後再加入此選項。

## 在 Linux 上以 Wine 驗證（選用）

在沒有 Windows 的環境中，可以用 Wine（需 32 位元支援 `wine32:i386`）加上虛擬顯示器 `xvfb` 跑完整的安裝流程：

```bash
wine ISCC.exe /DAppVersion=0.1.0 /DCloseApps=no /DSourceDir=Z:\path\to\dist\ToolManager installer\ToolManager.iss
xvfb-run -a wine dist/ToolManagerSetup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /LOG=install.log
xvfb-run -a wine "$WINEPREFIX/drive_c/users/$USER/AppData/Local/ToolManager/unins000.exe" /VERYSILENT
```

`/DCloseApps=no` **只用於 Wine**：Wine 沒有實作 Windows Restart Manager（`RmStartSession`），開啟 `CloseApplications` 時安裝程式會以「Call not implemented」失敗。正式打包不要加這個參數。
