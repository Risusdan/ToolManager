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
important = false                  # 「重要更新」標記
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

- ToolManager 隨附 **Python 3.12** 直譯器（MVP 僅此一版）。
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

### 5.3 執行模式

| 條件 | 行為 |
|---|---|
| `release.toml` 宣告 `args` | 自動產生參數表單，於內建 log 面板即時顯示 stdout / stderr |
| 未宣告 `args` | 直接以 console 啟動 |
| `gui = true` | 以 `pythonw` 啟動，不顯示 console |

## 6. 更新策略

### 6.1 工具更新

- **預設只通知**：清單顯示「有新版」標記 + 系統通知，使用者閱讀 changelog 後手動更新。
- 使用者可針對個別工具開啟**自動更新**。
- 作者可將版本標記為 `important = true`，以更醒目方式提醒（仍不強制）。
- **保留前一版本**，支援一鍵回退。
- 檢查頻率：啟動時一次；常駐系統匣時每 4 小時一次（可設定）。

### 6.2 ToolManager 自我更新

```
%LOCALAPPDATA%\ToolManager\
  ToolManager.exe        ← C++ launcher，邏輯極簡、幾乎不更新
  app-1.2.0\             ← 實際程式（onedir）
  app-1.3.0\
```

- launcher 只負責「找出最新且完整的版本資料夾並啟動」。
- 更新時於背景下載並解壓至新資料夾，完成後寫入完整性標記；下次啟動生效。
- 回退：刪除新版資料夾即可。
- `catalog.toml` / `release.toml` 的 `min_client_version` 用於要求過舊的 client 更新。
- ToolManager 本身也作為 catalog 中的一個工具上架。

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

送出前於 GUI 檢查一次，合併前再檢查一次。

## 9. 身分驗證

- 每位使用者使用**個人 HTTP Access Token**。
- 首次啟動時顯示設定精靈，提供「開啟 Bitbucket token 產生頁面」按鈕，貼上 token 後驗證可用性。
- token 以 `keyring` 儲存於 **Windows 認證管理員**，不存明碼。
- token 過期或失效時提示重新設定。

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
| 我的最愛 / 最近使用 | 儲存於本機 |
| 回報問題 | 開啟 `issue_url`，或寄信給 owners，自動帶入版本、環境資訊與最近 log |
| 設定 | token、PyPI mirror 網址、Bitbucket 網址、自動更新、常駐系統匣、開機啟動 |
| 管理員 | 待審核新工具數量與 PR 連結 |

### 10.2 桌面整合

- 系統匣：`pystray`
- 系統通知：`win11toast`
- 單一實例：重複開啟時喚醒既有視窗

### 10.3 NiceGUI 注意事項

- 本機 server 僅綁定 `127.0.0.1`，port 自動選取可用者
- `reload=False`、`multiprocessing.freeze_support()`
- 使用 `nicegui-pack` 以 **onedir** 模式打包（避免 onefile 解壓延遲）

### 10.4 多語系

- 預設**繁體中文**。
- 所有 UI 字串自第一天起透過翻譯函式（如 `_("Install")`）處理。
- 翻譯檔使用簡單的 TOML / JSON 字典，未來新增英文等語系僅需新增翻譯檔。

## 11. 安裝與部署

- 使用 **Inno Setup** 製作 per-user 安裝程式（不需管理員權限）：
  - 安裝至 `%LOCALAPPDATA%\ToolManager\`
  - 建立開始功能表與桌面捷徑
  - 可選開機自動啟動（常駐系統匣）
  - 註冊至「設定 → 應用程式」，可正常移除
- 安裝程式放置於內部網路磁碟或 Bitbucket，以公告信發布連結。
- MVP 不做程式碼簽章；公告中說明 SmartScreen「其他資訊 → 仍要執行」操作。

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
