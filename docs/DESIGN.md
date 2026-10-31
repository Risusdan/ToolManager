# ToolManager 設計文件

> 狀態：設計定稿（v0.1）
> 本文件為通用設計，不包含任何特定組織的內部資訊。所有環境相關的值（Bitbucket 網址、PyPI mirror、管理員名單等）皆透過設定檔或 catalog repo 提供。

## 1. 背景與目標

部門內同仁開發了許多軟體工具，但常遇到以下困擾：

- 不知道有什麼工具、工具放在哪裡
- 不知道哪裡有最新版本
- 不知道新版本支援什麼功能

ToolManager 是一套 Windows 桌面 GUI 工具，概念類似 VS Code Extension Marketplace，提供工具的**瀏覽、搜尋、一鍵安裝/更新/回退、執行與發布**。

### 1.1 範圍

| 項目 | 說明 |
|---|---|
| 產品範圍 | 目錄 + 一鍵安裝/更新（不含依賴管理、使用統計、評分留言） |
| 使用對象 | 部門內部約 50 人；架構預留未來對外（客戶）擴充空間 |
| 平台 | Windows 11（主要） |
| 開發人力 | 一人開發，無硬性 deadline，採漸進式上線 |

### 1.2 非目標（MVP 不做）

- 下載/使用次數統計、評分與留言（需集中式 server，待後續階段）
- 工具之間的依賴關係管理
- 標籤（tags）功能
- 程式碼簽章（待確認組織內是否有 code signing 憑證）

## 2. 系統架構總覽

```
┌──────────────────────── 使用者電腦 (Windows 11) ────────────────────────┐
│                                                                        │
│  ToolManager.exe (C++ launcher)                                        │
│        │ 啟動最新且完整的版本                                            │
│        ▼                                                               │
│  app-x.y.z\  (Python 3.12 + NiceGUI native / onedir)                   │
│   ├─ ui/        NiceGUI 介面（WebView2）                                │
│   ├─ core/      catalog、安裝、環境、執行、發布（不依賴 UI）             │
│   ├─ backends/  Repository 實作（Bitbucket Server）                     │
│   └─ 內建 Python 3.12 直譯器 + uv.exe（供腳本型工具使用）               │
│                                                                        │
│  %LOCALAPPDATA%\ToolManager\                                           │
│   ├─ cache\catalog\   catalog 快取（含 README 與圖片）                 │
│   ├─ tools\<id>\<version>\   已安裝工具（一版一資料夾）                 │
│   ├─ envs\<id>\<version>\    Python 腳本專屬 venv                       │
│   └─ logs\                                                             │
└───────────────┬────────────────────────────────────────────────────────┘
                │ HTTPS + 個人 HTTP Access Token
                ▼
┌──────────── 內部 Bitbucket Server ────────────┐     ┌──── 內部 PyPI mirror ────┐
│  catalog repo（manifest / README / 圖片）      │     │  Python 套件安裝來源      │
│  Git LFS（工具 zip）                           │     └──────────────────────────┘
│  PR = 發布 / 審核流程                          │
└───────────────────────────────────────────────┘
```

### 2.1 關鍵設計原則

1. **使用者電腦不需要安裝 git**：catalog 以 archive API 取得、LFS 檔案以 LFS batch API 下載、發布以 REST API 直接建立 commit。
2. **業務邏輯與 UI 分層**：`core/` 不依賴 NiceGUI，可被 GUI 與未來的 CLI 共用，也方便單元測試。
3. **抽象層保留擴充彈性**：
   - `Repository`：catalog 與檔案存取（目前實作：Bitbucket Server；未來可換自架 server）
   - `EnvBackend`：Python 環境建立（目前實作：uv；備案：venv + pip）
4. **一版一資料夾**：工具與 ToolManager 本身皆採此結構，支援多版本並存與一鍵回退。

## 3. Catalog Repo

### 3.1 目錄結構

```
catalog/
├─ catalog.toml                  # 全域設定
├─ CONTRIBUTING.md               # 上架規範與審核 checklist
└─ tools/
   └─ <tool-id>/                 # 小寫 + 連字號，建立後不可更改
      ├─ tool.toml               # 工具層級資訊（較少變動）
      ├─ README.md               # 詳情頁內容，可引用 assets/ 內圖片
      ├─ assets/                 # README 圖片（單張 ≤ 2 MB）
      └─ releases/
         └─ <x.y.z>/
            ├─ release.toml      # 版本層級資訊
            └─ <tool-id>-<x.y.z>.zip   (Git LFS)
```

- 每次發布只會**新增** `releases/<x.y.z>/`，不修改他人檔案，多人同時發布不會衝突。
- 不維護總索引檔，manifest 本身即唯一資料來源。

### 3.2 `catalog.toml`

```toml
schema_version = 1
min_client_version = "1.0.0"
admins = ["admin-a", "admin-b"]

[[categories]]
id = "debug"
name = "除錯 / 暫存器存取"

[[categories]]
id = "flash"
name = "燒錄 / 韌體更新"

[[categories]]
id = "image"
name = "影像 / 色彩分析"

[[categories]]
id = "build"
name = "建置 / 程式碼產生"

[[categories]]
id = "test"
name = "自動化測試 / 驗證"

[[categories]]
id = "doc"
name = "文件 / 報表產生"

[[categories]]
id = "misc"
name = "其他"
```

### 3.3 `tool.toml`

```toml
id = "i2c-debugger"
name = "I2C Debugger"
summary = "透過 USB-I2C dongle 讀寫暫存器"
category = "debug"                 # 必須為 catalog.toml 中定義的分類
owners = ["user-a", "user-b"]      # Bitbucket 帳號，僅 owners 可發布新版本
type = "exe"                       # exe | installer | python
issue_url = "https://..."          # 選填；未填則「回報問題」改為寄信給 owners
```

### 3.4 `release.toml`

```toml
version = "1.3.0"                  # 必須符合 SemVer 且大於既有版本
date = 2026-10-30
important = false                  # 「重要更新」標記（介面顯示為「建議盡快更新」）
important_note = "修正讀取 0xFF 位址會當機的問題"  # 選填；一句話說明為何建議更新
min_client_version = "1.0.0"       # 選填
changelog = '''
- 新增：支援 400kHz
- 修正：讀取 0xFF 位址會當機
'''

# --- type = "exe" ---
entry = "I2CDebugger.exe"

# --- type = "installer" ---
# entry = "setup.exe"
# installer_args = "/S"

# --- type = "python" ---
# entry = "main.py"
# python = ">=3.11"
# gui = false
#
# [[args]]
# name = "input"
# type = "file"          # string | int | float | bool | file | folder | choice
# label = "輸入檔"
#
# [[args]]
# name = "baudrate"
# type = "int"
# default = 115200
```

格式讀取使用 Python 內建 `tomllib`，寫入使用 `tomli-w`。

## 4. 工具型態

| 型態 | 打包方式 | 安裝 | 版本偵測 / 更新 / 回退 | 執行 |
|---|---|---|---|---|
| `exe`（可攜式） | zip，解壓即可執行 | 解壓至 `tools\<id>\<version>\`，建立捷徑 | ✅ 完整支援 | 直接啟動 `entry` |
| `installer` | zip 內含 msi / exe 安裝程式 | 下載並開啟安裝程式 | ❌ 不支援（僅提示使用者手動完成） | 由使用者自行執行 |
| `python` | zip 內含 `.py`、`requirements.txt`、選填 `wheels/` | 解壓 + 建立專屬 venv | ✅ 完整支援 | 見 §5 |

新工具一律鼓勵做成可攜式 zip，不需管理員權限。

## 5. Python 腳本支援

### 5.1 直譯器

- ToolManager 隨附 **Python 3.12** 直譯器（MVP 僅此一版）與 `uv.exe`，位於 app 資料夾的 `runtime\python\`、`runtime\uv.exe`（由 `build_windows.ps1` 放入：預設使用 uv 管理的可搬移 CPython，或以 `-PythonDir` 指定）；同事電腦不會下載直譯器。開發模式則使用目前的 Python 與 PATH 上的 uv。
- `release.toml` 的 `python` 欄位僅用於相容性檢查；不相容時拒絕安裝並提示。
- 未來若有需求，可將其他版本直譯器作為 catalog 中的「工具」按需下載（直譯器路徑已參數化）。

### 5.2 依賴安裝（`EnvBackend`）

- 預設實作 **uv**（`uv.exe` 隨 ToolManager 發布，不需另外下載）：
  - `UV_PYTHON_DOWNLOADS=never`，以 `--python <內建直譯器路徑>` 建立 venv
  - `UV_INDEX_URL=<內部 PyPI mirror>`（設定項）
  - 不呼叫 `uv self update`
  - 結果：**不會連線至 GitHub**
- 安裝策略：zip 內有 `wheels/` → 離線安裝；否則經 mirror 安裝 `requirements.txt`。
- 備案實作：完整版 CPython + `python -m venv` + `pip --index-url <mirror>`。

- 環境位於 `envs\<tool-id>\<版本>\`，**第一次執行時建立**（不在安裝時建立，mirror 暫時連不上也不影響安裝）；`requirements.txt`、`wheels\`、直譯器、後端或 mirror 有變動時自動重建。環境不含使用者資料，可隨時重建，因此直接刪除（仍需擁有權標記）；已不存在的版本的環境於啟動時清理。

### 5.3 執行模式

| 條件 | 行為 |
|---|---|
| `release.toml` 宣告 `args` | 自動產生參數表單，於內建 log 面板即時顯示 stdout / stderr |
| 未宣告 `args` | 直接以 console 啟動（新視窗，執行完不關閉，可使用 `input()`） |
| `gui = true` | 以 `pythonw` 啟動，不顯示 console |

- 參數對應命令列（與 argparse 慣例一致）：預設 `--<name> <值>`；`flag` 可自訂旗標；bool 勾選時只送出旗標；`positional = true` 為位置參數。另可設定 `required`、`help`（顯示於欄位下方）。留空的選填欄位不送出，讓腳本自己的預設值生效。
- 腳本的工作目錄為該工具的版本資料夾（輸出檔若寫在旁邊，會受 §6.3 使用者檔案偵測保護）。
- 執行頁面：參數表單（檔案/資料夾欄位在桌面視窗中有「瀏覽」）、執行／停止（Windows 以 taskkill /T 結束整個程序樹）、即時 log；每次執行的 log 存於 `logs\runs\<tool-id>\`，表單會記住上次的值。

## 6. 更新策略

### 6.1 工具更新

- **預設只通知**：清單顯示「有新版」標記 + 系統通知，使用者閱讀 changelog 後手動更新。
- 使用者可針對個別工具開啟**自動更新**。
- 作者可將版本標記為 `important = true`，以更醒目方式提醒（仍不強制）。介面顯示為「⚠ 建議盡快更新」（琥珀色），並可用 `important_note` 一句話說明原因，直接顯示在提示中；未填時提示使用者查看版本說明。
- **保留前一版本**，支援一鍵回退。
- 檢查頻率：啟動時一次；常駐系統匣時每 4 小時一次（可設定）。

### 6.2 ToolManager 自我更新

```
%LOCALAPPDATA%\ToolManager\
  ToolManager.exe        ← C++ launcher，邏輯極簡、幾乎不更新
  app-1.2.0\             ← 實際程式（onedir，ToolManagerApp.exe）
  app-1.3.0\
```

- launcher 只負責「找出最新且完整的版本資料夾並啟動」，從不刪除或修改檔案；細節見 `launcher/README.md`。
  - 只採用含 `.complete` 標記、沒有 `.disabled` 標記、且有 `ToolManagerApp.exe` 的 `app-x.y.z\`，依版本數值排序。
  - 最新版無法啟動時自動改用下一個可用版本；沒有可用版本時顯示錯誤訊息。
  - 原封不動轉傳命令列參數、沿用呼叫端工作目錄；以 `TOOLMANAGER_LAUNCHER`、`TOOLMANAGER_APP_DIR` 環境變數告知 app 安裝位置（`defaults.toml` 因此放在 launcher 旁，更新後仍有效）。
- 新版在 15 秒內帶錯誤碼結束時，launcher 自動改用前一版，並告知 app 失敗的版本。
- **App 端自我更新**（`core/self_update.py`）：ToolManager 本身以 tool id `toolmanager` 上架於 catalog（release zip 為 onedir 內容，由 `build_windows.ps1` 產生）。
  - 同步目錄後若有新版，**自動在背景下載**並解壓至 `app-x.y.z\`（staging → 寫入 `.complete` → rename），不影響正在執行的版本；頂部顯示「重新啟動以更新至 x」。
  - 只有經由 launcher 啟動時才啟用（開發模式不自我更新）。`toolmanager` 不出現在工具列表，可從設定頁「ToolManager 更新內容」查看版本說明。
  - 被 launcher 退回啟動時，首頁顯示「ToolManager x 無法正常啟動，已暫時改用前一版」與「不再使用 x」（寫入 `.disabled`，不刪除）；此時不再提示重新啟動到失敗的版本，也不清理舊版本。
  - 舊版本清理：保留目前版本與一個較舊的可用版本（launcher 的退回目標），其餘移到回收區。
  - catalog 的 `min_client_version` 高於目前版本時，首頁提示需要更新 ToolManager。
- 回退：在新版資料夾放 `.disabled` 標記即可（不需刪除）。
- `catalog.toml` / `release.toml` 的 `min_client_version` 用於要求過舊的 client 更新。
- ToolManager 本身也作為 catalog 中的一個工具上架。

### 6.3 刪除與回收區（資料安全）

目標：**任何 bug 都不應造成使用者檔案無法挽回的遺失。**

1. **不就地刪除**：工具資料夾與舊版本一律先「移動」到 ToolManager 回收區（`%LOCALAPPDATA%\ToolManager\trash\`），保留 **7 天**（`trash_retention_days` 可設定）後才永久刪除。回收區頁面可**還原**、**開啟資料夾**、**永久刪除**（需確認）。還原時若原位置已有資料，則不覆蓋。
2. **移動前的防護**（`core/trash.py`）：
   - 路徑必須位於 ToolManager 管理的資料夾（`tools\`、`downloads\`）**之內**，且不得是這些資料夾本身。
   - 不得是 symlink / junction（只判斷連結本身，不跟隨目標）。
   - 資料夾內必須有 ToolManager 的擁有權標記（`.tm-owned` 或 `.complete`）；**沒有標記的資料夾一律不碰**。
   - 回收區清除只處理「回收區根目錄下、含有 `meta.toml`」的項目，其他內容一律略過。
3. **使用者檔案偵測**：安裝時記錄每個檔案的大小與修改時間（`.tm-files`）。工具常把設定檔、log 寫在 exe 旁邊，因此：
   - 自動清理舊版本時，若版本資料夾中有**新增或修改的檔案**，該版本**保留不動**，並在「已安裝」頁提示，由使用者決定「開啟資料夾」或「移到回收區」。
   - 移除工具前的確認視窗會列出使用者檔案數量。
4. **既有資料夾不接管**：安裝時若 `tools\<id>\` 已存在且不是 ToolManager 建立的，拒絕安裝，不覆蓋、不清理。
5. **狀態檔驗證**：`state.toml` 的 `id` 必須符合 tool id 格式且與資料夾名稱一致，否則視為未安裝。
6. **Windows 特性**：
   - 移動（rename）遇到使用中的檔案會整個失敗，不會刪到一半；失敗時提示「檔案使用中」。
   - 防毒軟體鎖住下載暫存檔不會導致安裝失敗（`ignore_cleanup_errors`）。
   - 唯讀檔案於永久刪除時先清除唯讀屬性再刪。
   - 依 Python 3.8+ 文件，`shutil.rmtree` 在 Windows 上不會跟進 junction 刪除其目標內容。
7. **捷徑**：記錄自己建立的 `.lnk` 路徑，只移除自己建立的；同名時改用 `名稱 (tool-id).lnk`。
8. 中斷遺留的 staging 資料夾，需超過 1 小時且有擁有權標記才會清除（避免誤刪另一個執行中實例的安裝）。

> 建議工具作者將使用者資料存放在 `%APPDATA%\<工具名稱>\`（寫入 catalog 的 `CONTRIBUTING.md`），偵測機制則作為保險。

## 7. Catalog 同步

1. 呼叫 Bitbucket REST API 取得 `main` 最新 commit hash。
2. 與本機快取的 hash 相同 → 直接使用快取。
3. 不同 → 以 archive API 下載整個 catalog（LFS 檔案僅為指標檔，體積小），解壓至快取。
4. 啟動時**先顯示快取**，背景同步完成後再刷新畫面。
5. README 圖片隨 catalog 快取於本機，由 NiceGUI 本機 server 提供給 WebView（WebView 無法直接攜帶 token 存取 Bitbucket）。
6. 工具 zip 於安裝時透過 **LFS batch API** 下載。

## 8. 發布與審核

### 8.1 發布流程

發布核心（`core/publish`）為獨立模組，GUI 與 CLI 共用。

1. 作者於**發布精靈**選擇 zip 或腳本資料夾，填寫名稱、版本、changelog 等（自動帶入上一版資料）。
2. 本機自動檢查（見 §8.3）。
3. 透過 LFS batch API 上傳 zip。
4. 透過 REST API 建立 branch 並 commit（manifest、README、assets、LFS 指標檔）。
5. 透過 REST API 開 PR。
6. 依審核規則合併（見 §8.2）。

階段規劃：MVP 實作 GUI 發布精靈；後續加入 CLI（`toolmanager publish ./dist`）以支援 CI 自動發布。

實作（`core/publish.py`、`ui/publish_page.py`）：
- **只給開發人員**：判斷依據是 catalog repo 的寫入權限。同步後以 `GET /rest/api/1.0/repos?permission=REPO_WRITE`（一般使用者即可呼叫）確認一次；沒有寫入權限的帳號看不到任何發布入口，直接開網址也只會看到「發布功能只開放給工具開發人員」。因此 Bitbucket 上 catalog repo 的寫入權限只應給開發人員（建議用一個群組管理）。客戶帳號沒有寫入權限，客戶版自然不會出現發布功能。
- 入口：側欄底部「上架新工具」；工具詳情頁「版本歷史」旁的「發布新版本」（另需為該工具的 owner 或管理員；帳號比對不分大小寫）。
- 精靈為單一頁面四個區塊：程式（zip 或資料夾；資料夾自動打包並略過 `__pycache__`、`.venv` 等）、工具資料、版本、檢查與發布。新版本自動帶入上一版的資料（含 `python` 版本需求與 `min_client_version`），版本號預設為下一個修訂號。
- Python 工具另有「參數（執行頁的表單）」區塊：逐列編輯 `[[args]]`（名稱、標籤、類型、預設值、選項、說明、必填、位置參數、旗標），可上下移動；下方即時預覽執行頁表單（與執行頁同一段程式）與預設值對應的命令列。驗證規則與 catalog 解析相同。新版本沿用上一版的參數，未修改的欄位寫出後與原本完全相同。
- 只提交與已同步 catalog 不同的檔案；`tool.toml`、`README.md` 內容沒變時不重寫。README 引用的圖片若 catalog 中沒有，從程式資料夾複製。
- Bitbucket：LFS batch API 上傳 zip → `POST .../branches` 從**上次同步時的 commit**建立 `publish/<id>-<版本>`（已存在則加 `-2`；從同步時的 commit 開始，期間別人發布的同版本或修改的 `tool.toml` 會成為合併衝突，而不會被覆蓋）→ browse API 逐檔 commit（既有檔案以 `sourceCommitId` 修改）→ 開 PR → 新版本以 `version` 參數合併；合併被擋（需核准等）時保留 PR 並顯示原因。
- 本機 catalog（`--catalog`）直接寫入資料夾，供開發與展示。
- 尚未實作：管理員「待審核：N 個新工具」提示、CLI。

### 8.2 審核規則

| 情境 | 規則 |
|---|---|
| 新工具首次上架 | 需任一位管理員於 Bitbucket 網頁核准 PR |
| 既有工具發布新版本 | owners 自行合併（ToolManager 以 owner 的 token 透過 API 合併） |
| 分類異動 | 由管理員修改 `catalog.toml` |

- 管理員：`catalog.toml` 中的 `admins`（建議 2～3 人，避免單點瓶頸）。
- ToolManager 對管理員顯示「待審核：N 個新工具」，點擊開啟 PR 頁面。
- Bitbucket 設定 `main` 分支禁止直接 push，僅能透過 PR 合併。
- 審核標準 checklist 記載於 catalog repo 的 `CONTRIBUTING.md`：可正常執行、README 說明用途、未與既有工具重複。

> ⚠️ 已知限制：owner 驗證於 client 端進行，具 repo 寫入權限者仍可於網頁手動合併繞過。內部互信環境下可接受；若未來對外，改為 server 端驗證。

### 8.3 自動檢查項目

- `tool.toml` / `release.toml` 格式與必填欄位
- `id` 格式正確；分類存在於 `catalog.toml`
- 版本號符合 SemVer 且大於既有最新版本
- 發布者為該工具的 owner（新工具除外）
- zip 以 LFS 儲存
- README 引用的圖片皆存在，且單張 ≤ 2 MB

送出前於 GUI 檢查一次，合併前再檢查一次。兩者共用 `core/catalog_check.py`；合併前由 `scripts/check_catalog.py` 執行（手動或掛在 Bitbucket build）：

```powershell
uv run python scripts\check_catalog.py <catalog repo 資料夾> --base origin/main --author <發布者帳號>
```

- 不加 `--base`：只檢查檔案本身（格式、套件內有 entry、zip 走 LFS、README 圖片）。
- 加 `--base`：另外檢查變更——已發布的版本（`release.toml` 與 zip）不可修改、新版本號必須大於目前最新版、`--author` 必須是有變動工具的 owner（管理員不受限）、只有管理員能改 `catalog.toml`。新工具、owners 變更、移除工具／版本會列為「說明」或「注意」供審核者參考。
- zip 是否已比對內容以 LFS oid 為準，因此有沒有 `git lfs pull` 結果都一樣。

## 9. 身分驗證

- 每位使用者使用**個人 HTTP Access Token**。
- 首次啟動時顯示設定精靈，提供「開啟 Bitbucket token 產生頁面」按鈕，貼上 token 後驗證可用性。
- token 以 `keyring` 儲存於 **Windows 認證管理員**，不存明碼。
- token 過期或失效時提示重新設定。

### 9.1 首次設定精靈與設定頁

- **部門預設值 `defaults.toml`**：與 `ToolManager.exe` 放在同一資料夾（由打包者填入 Bitbucket 網址、Project、Repo、PyPI mirror），不進版本控制。同事第一次使用時，伺服器欄位已預先填好，只需貼上 token。
- **設定優先順序**：程式內建預設 ← `defaults.toml` ← 使用者的 `settings.toml`。`settings.toml` **只寫入與預設值不同的欄位**，因此部門日後修改 `defaults.toml`（例如伺服器搬家），沒改過該欄位的使用者會自動跟著更新。
- **精靈步驟**：伺服器 → 存取 Token（開啟 Bitbucket 的 token 頁面、貼上、驗證）→ 完成。驗證失敗會依原因顯示中文說明：token 無效/過期、沒有權限、找不到專案/repo、無法連線（提示確認內網/VPN）。
- **設定頁**：連線（含更換 token、測試連線）、更新檢查間隔、回收區保留天數、PyPI mirror、開啟資料/log 資料夾。連線欄位或 token 有變動時，必須先通過連線測試才會儲存；儲存後立即套用，不需重新啟動。

## 10. 使用者介面

技術：**NiceGUI（native 模式，WebView2）**，Win11 內建 WebView2。

### 10.1 MVP 頁面與功能

| 頁面 / 功能 | 內容 |
|---|---|
| 瀏覽 / 搜尋 | 卡片式列表；依分類篩選；搜尋範圍：名稱、摘要、README 內文 |
| 工具詳情 | README 渲染（含圖片）、版本歷史與各版 changelog、owners、安裝 / 更新 / 回退 / 執行 |
| 已安裝 | 本機已安裝工具與版本、「有新版」標記、自動更新開關 |
| 執行 | exe 直接啟動；Python 腳本參數表單 + log 面板 |
| 發布精靈 | 見 §8.1 |
| 我的最愛 / 最近使用 | 卡片與詳情頁的星號加入我的最愛；開啟 exe 工具或執行 Python 工具時記錄最近使用（保留 10 筆）；分類欄最上方兩個項目篩選。存於資料資料夾的 `prefs.json` |
| 回報問題 | 開啟 `issue_url`，或寄信給 owners，自動帶入版本、環境資訊與最近 log |
| 設定 | token、PyPI mirror 網址、Bitbucket 網址、自動更新、常駐系統匣、開機啟動 |
| 管理員 | 待審核新工具數量與 PR 連結 |

### 10.2 桌面整合

- 系統匣（`pystray`，僅 Windows 桌面視窗）：選單「開啟 ToolManager／檢查更新／結束」。**最小化時縮到系統匣**（視窗從工作列移除，第一次會提示仍在執行）；按 X 則完全結束（NiceGUI 無法攔截關閉）。
- 系統通知（透過系統匣圖示）：同步後若有新版本跳出通知，每個「工具＋版本」只通知一次；ToolManager 自身更新準備好時也通知一次。
- 開機自動啟動：設定頁開關（**預設關閉**），寫入 `HKCU\...\Run`，以 `ToolManager.exe --background` 啟動，只在系統匣等候、不跳出視窗；僅在經由 launcher 安裝的 Windows 版本可用。
- 單一實例（`core/single_instance.py`）：資料資料夾中的系統檔案鎖標示執行中的實例（程式當掉時由作業系統自動釋放）；`instance.json` 記錄其埠號與一次性 token。第二次啟動在載入 UI 框架前檢查，呼叫既有實例的 `/_tm/activate` 把視窗叫到最前面後**以結束碼 0 離開**（避免 launcher 誤判為新版當掉）；既有實例無回應時顯示訊息。自我更新的「重新啟動」會先釋放鎖再啟動新實例，啟動失敗則收回鎖。同時避免兩個實例同時清理暫存與回收區。

### 10.3 NiceGUI 注意事項

- 本機 server 僅綁定 `127.0.0.1`，port 自動選取可用者
- `reload=False`、`multiprocessing.freeze_support()`
- 使用 `nicegui-pack` 以 **onedir** 模式打包（避免 onefile 解壓延遲）

### 10.4 版面與視覺原則

- **單一強調色**（圖示的青綠 #2a6e6a；客戶版另設）用於主要動作與「有新版」；狀態用灰字＋小圖示；「建議盡快更新」用琥珀色；紅色只出現在破壞性操作的確認按鈕。
- **一致的頁框**：白底頂部導覽（底線標示目前頁、已安裝旁顯示可更新數量），所有頁面都有 208px 分類側欄（含數量）；同步成功顯示在側欄底部，只有「同步中／失敗」才出現在頂部。
- **首頁卡片固定骨架**：字首方塊＋名稱（過長截斷）、摘要最多兩行、頁尾左分類右狀態；排序為「有更新 → 已安裝 → 其他（依名稱）」；有更新時顯示一條總覽提示。
- **詳情頁**：麵包屑；上方「左：名稱／摘要／中繼資訊，右：主要動作＋⋯ 選單（切換版本、開啟資料夾、移除）＋安裝狀態與自動更新」；警示只出現一次；README 字級低於頁面標題，且移除與工具名稱重複的開頭標題。
- **字型**：Segoe UI ＋ Microsoft JhengHei UI；內文行高 1.6，README 1.75、最寬 68 字元。

### 10.6 回報問題（Jira）

原則：「不會用」先看 README；確定是 Bug 才到 Jira，**先看既有問題再決定是否開新單**。ToolManager 只負責把使用者帶到 Jira 的正確位置，不做對話框或分類。

- 所有工具共用一個 Jira 專案，以 label `tm-<tool-id>` 區分工具（不需 Jira 管理員為每個工具建立 component）。
- 設定放在 catalog repo 的 `catalog.toml`（不進程式碼）：

  ```toml
  [jira]
  base_url = "https://jira.example.com"
  project_key = "TOOLS"
  project_id = 10200      # 數字 ID，預填建單頁需要
  issue_type_id = 1       # 通常為 Bug
  label_prefix = "tm-"    # 選填
  ```

- 入口：詳情頁「⋯」選單與 Python 工具執行頁，各有兩個連結（未設定 `[jira]` 時不顯示）：
  - **查看已知問題**：在瀏覽器開啟該工具未結案的 ticket（JQL 依 label 篩選）。
  - **回報新問題**：開啟 Jira 預填建單頁（`CreateIssueDetails!init.jspa`），帶入 label、標題前綴、「送出前請先確認已知問題」提醒與連結、工具與 ToolManager 版本、系統、最近一次執行的輸出。
- 不需要額外的 Jira token：連結在使用者瀏覽器中開啟，沿用其 Jira 登入。
- 連結長度控制在 6000 字元內（Jira Data Center 的 Tomcat 預設約 8 KB 標頭上限），輸出過長時保留最新的部分。
- 執行 log 只記錄 `python main.py <參數>`，不含安裝路徑。
- 後續：可另寫腳本以 Jira API 定期產生各工具的問題統計報表給作者。
- **匯出診斷資訊**（設定頁「資料夾」區塊）：把摘要（版本、資料夾、catalog 伺服器、同步與自我更新狀態、已安裝工具）、`settings.toml`／`defaults.toml` 與 log（每個檔案最多保留最後 2 MB，含 launcher.log）打包成 `diagnostics\toolmanager-diagnostics-<時間>.zip`，並在檔案總管中選取該檔。token 存在 Windows 認證管理員，不在這些檔案內；看起來像憑證的內容（`Authorization:`、`token=` 等）仍會遮蔽。客戶版沒有 Jira，這是取得客戶 log 的主要方式。

### 10.7 多語系

- 預設**繁體中文**。
- 所有 UI 字串自第一天起透過翻譯函式（如 `_("Install")`）處理。
- 翻譯檔使用簡單的 TOML / JSON 字典，未來新增英文等語系僅需新增翻譯檔。

## 11. 安裝與部署

- 使用 **Inno Setup** 製作 per-user 安裝程式 `ToolManagerSetup-<版本>.exe`（`installer/ToolManager.iss`，細節見 `installer/README.md`）：
  - 不需管理員權限；固定安裝至 `%LOCALAPPDATA%\ToolManager\`（launcher + `app-<版本>\`，`.complete` 最後複製）
  - 開始功能表捷徑；桌面捷徑為選項
  - 註冊至「設定 → 應用程式」，可正常移除；以 `AppMutex` 偵測執行中的 ToolManager
  - 可在打包時帶入部門 `defaults.toml`（`/DDefaultsFile`，不進版本控制）
  - **解除安裝只移除程式**（launcher、所有 `app-*`），保留工具、回收區、設定與 log，並告知使用者資料位置
  - 開機自動啟動由 ToolManager 設定頁控制（§10.2），安裝程式不另外詢問
- 安裝程式只用於**首次安裝**；之後的更新由自我更新處理（§6.2）。
- 安裝程式放置於內部網路磁碟或 Bitbucket，以公告信發布連結。
- MVP 不做程式碼簽章；公告中說明 SmartScreen「其他資訊 → 仍要執行」操作。

## 11.1 客戶簡化版

同一個程式、不同的 catalog repo；細節見 `docs/CUSTOMER.md`。
- 客戶 catalog repo 只放可給客戶的工具（人工複製），以 `scripts/check_customer_catalog.py` 檢查：無 `[jira]`、`python_offline_only = true`、Python 工具附 `wheels/`、可見文字與檔名不含內部關鍵字。
- 以 `build_windows.ps1 -CustomerDefaultsFile` 產生客戶版安裝程式 `ToolManagerSetup-<版本>-customer.exe`（`ToolManager.iss` 的 `/DCustomer=yes`）：獨立的 AppId、安裝到 `%LOCALAPPDATA%\ToolManager-Customer\`，可與內部版並存。內外部的安裝與移除方式一致。
- 每套安裝的開機自動啟動項目與 AppMutex 名稱由安裝資料夾名稱決定（`autostart.value_name`、`paths.app_mutex_name`，安裝腳本用相同規則），兩版互不干擾。
- **主題色與圖示**：`defaults.toml` 的 `accent = "#rrggbb"` 換掉主色（頂部 3px 色帶、標誌、按鈕、選取項目、資訊橫幅都跟著變）；未設定則為圖示原色青綠 `#2a6e6a`。圖示（`src/toolmanager/assets/icon.svg`）也跟著換色：每個色階換成主色的色相、保留原本的飽和度與明暗（`core/icon.py`）。
  - 打包時：`scripts/make_icon.py` 產生內部版與客戶版的 `.ico`，用於 launcher、ToolManagerApp.exe（內部版圖示）、捷徑、應用程式清單與安裝程式本身。
  - 執行時：favicon、頁首標誌、系統匣直接用換色後的圖示；客戶版另以 `WM_SETICON` 換掉視窗與工作列圖示（ToolManagerApp.exe 兩版共用同一個自我更新套件，內嵌的是內部版圖示）。
  - 點陣化只用 Pillow，不需 SVG 函式庫；因此 SVG 限用直線路徑（M/L/H/V/Z）與水平／垂直漸層，用到曲線時會明確報錯。
- **可攜模式**：`defaults.toml` 含 `portable = true` 時，所有資料放在 launcher 旁的 `data\`，不與內部版混用。
- `python_offline_only = true` 時，Python 工具缺少 `wheels\` 會直接提示，不嘗試連線安裝。
- 後續（需求明確後）：每個客戶一個 repo、匯出腳本自動產生客戶 catalog。

## 12. 專案結構（原始碼）

```
ToolManager/
├─ docs/
│  └─ DESIGN.md
├─ src/toolmanager/
│  ├─ core/          # catalog、install、env、runner、publish、validate（不依賴 UI）
│  ├─ backends/      # Repository 實作（bitbucket_server）
│  ├─ ui/            # NiceGUI 頁面
│  └─ i18n/          # 翻譯檔
├─ launcher/         # C++ launcher
├─ installer/        # Inno Setup 腳本
├─ tests/            # pytest；以假的 Repository 實作進行單元測試
└─ pyproject.toml    # uv 管理開發依賴，Python 3.12
```

- 原始碼**不包含任何組織內部資訊**；所有環境相關值皆來自設定檔或 catalog repo。
- Log：`%LOCALAPPDATA%\ToolManager\logs\`，保留 7 天。

## 13. 開發階段

| 階段 | 內容 | 粗估 |
|---|---|---|
| **P0 技術驗證** | NiceGUI 打包與啟動速度、本機 port 是否受資安軟體干擾；Bitbucket archive API / LFS batch API / REST 檔案 commit；uv 經 mirror 建立環境 | 1 週 |
| **P1 唯讀版** 🚀 首次上線 | 瀏覽、搜尋、詳情頁；exe 工具安裝 / 更新 / 回退；token 設定；launcher 自我更新；Inno Setup 安裝程式 | 2～3 週 |
| **P2 Python 執行** | `EnvBackend`（uv）、參數表單、log 面板 | 1～2 週 |
| **P3 發布** | 發布精靈、自動檢查、owners 驗證、PR 建立與合併、管理員待審核提示 | 2 週 |
| **P4 打磨** | 系統匣、通知、我的最愛 / 最近使用、回報問題、使用文件 | 1 週 |
| **後續** | CLI 發布、統計 / 評分（需 server）、多版本 Python、對外版本、程式碼簽章 | — |

P1 上線時，catalog 由管理員以手動 PR 維護，先上架數個常用工具。

## 14. 待確認事項

| # | 事項 | 處理時機 |
|---|---|---|
| 1 | 組織對於在個人 GitHub 開發此專案的政策 | **最優先** |
| 2 | 內部 PyPI mirror 網址 | P2 前 |
| 3 | 資安軟體是否干擾本機監聽 port | P0 |
| 4 | Bitbucket Server 版本，以及 archive API、REST 檔案編輯 API、LFS 支援狀況 | P0 |
| 5 | 是否有可用的 code signing 憑證 | 後續 |

## 附錄 A：決策紀錄

| # | 主題 | 決定 | 主要理由 |
|---|---|---|---|
| 1 | 產品範圍 | 目錄 + 一鍵安裝/更新 | 同時解決「在哪裡、最新版、新功能」三個痛點 |
| 2 | 使用對象 | 內部 ~50 人，預留對外 | MVP 快速，保留擴充 |
| 3 | 後端 | Bitbucket Server catalog repo + LFS | 既有基礎設施，PR 即審核流程 |
| 4 | 工具型態 | 可攜式 zip 為主；installer 僅下載開啟；Python 腳本原生支援 | 可靠的版本偵測與多版本並存 |
| 5 | Python 依賴 | uv per-tool venv + mirror；wheels 離線備案 | 依賴隔離、作者免打包 exe |
| 6 | 腳本執行 | 參數表單 / 直接啟動 / pythonw | 使用者免碰指令列 |
| 7 | 發布方式 | GUI 精靈 →（後續）CLI，共用核心 | 降低上架門檻 |
| 8 | 審核 | 新工具需管理員核准；更新由 owners 合併 | 品質把關且不形成瓶頸 |
| 9 | 技術棧 | Python + NiceGUI native + onedir | 純 Python、介面美觀、利於未來網頁化 |
| 10 | 驗證 | 個人 HTTP Access Token + Windows 認證管理員 | 可追溯、可撤銷、適用未來對外 |
| 11 | 工具更新 | 預設通知、可選自動、重要更新標記、可回退 | 兼顧使用者控制與版本一致性 |
| 12 | 自我更新 | C++ launcher + 版本資料夾 | 不覆寫執行中檔案，最安全 |
| 13 | MVP 加分 | 回報問題、我的最愛 / 最近使用、README 圖片渲染 | 低成本高價值 |
| 14 | 時程 | P0 + P1 先上線，漸進迭代 | 早期取得回饋 |
| 15 | 同步 | archive API + commit hash；LFS batch API；REST commit | 使用者免安裝 git，無索引衝突 |
| 16 | 格式 | TOML；工具 / 版本層級分離 | 語法嚴謹，發布不衝突 |
| 17 | 分類 | 固定分類，不做標籤 | 導覽整齊 |
| 18 | 管理員 | 2～3 人，Bitbucket 網頁審核 | 避免單點瓶頸 |
| 19 | 首次安裝 | Inno Setup per-user，暫不簽章 | 標準體驗、免管理員權限 |
| 20 | Python 版本 | 僅 3.12 | 簡單一致，路徑已參數化 |
| 21 | 語系 | 繁中預設，字串一律經翻譯函式 | 低成本保留多語系 |
| 22 | 原始碼位置 | GitHub 通用版，不含組織資訊 | 設計本即可設定化 |
