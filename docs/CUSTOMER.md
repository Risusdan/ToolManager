# 客戶簡化版

客戶拿到的是**同一個 ToolManager**，只是連到另一個只放「可給客戶的工具」的 catalog repo。
內部工具的任何資料（名稱、說明、檔案）都不會出現在客戶的 repo，因此客戶無從得知它們的存在。

| | 內部完整版 | 客戶簡化版 |
|---|---|---|
| catalog repo | 內部 catalog repo | 客戶 catalog repo（co-git 對外空間，只開放給客戶帳號） |
| 交付方式 | 安裝程式 `ToolManagerSetup-<版本>.exe` | 安裝程式 `ToolManagerSetup-<版本>-customer.exe` |
| 安裝位置 | `%LOCALAPPDATA%\ToolManager\` | `%LOCALAPPDATA%\ToolManager-Customer\` |
| 資料位置 | 同安裝位置 | 安裝資料夾內的 `data\`（可攜模式） |
| 回報問題（Jira） | 有 | 無（客戶 catalog 不設定 `[jira]`，自動隱藏） |
| Python 工具 | 經內部 PyPI mirror 安裝套件 | **只能**從工具附帶的 `wheels\` 離線安裝 |

## 一、建立客戶 catalog repo（一次性）

1. 在 co-git 對外空間建立 repo（例如 `CUST/customer-catalog`），**權限只給客戶的帳號群組與負責同仁**。
2. 啟用 Git LFS，追蹤 zip：`git lfs track "tools/*/releases/*/*.zip"`。
3. 建立 `catalog.toml`：

   ```toml
   # 頂層設定必須寫在檔案開頭、第一個 [ ] 之前
   schema_version = 1
   min_client_version = "0.1.0"
   admins = ["負責同仁帳號"]
   python_offline_only = true   # 客戶端連不到內部 PyPI mirror

   [[categories]]
   id = "flash"
   name = "燒錄 / 韌體更新"
   # 只列出客戶會用到的分類
   ```

   - **不要**加入 `[jira]`。
   - `main` 分支設定為只能透過 PR 合併。

## 二、把工具放進客戶 repo（每次出貨）

由客戶專案負責人決定要給哪些工具與版本：

1. 從內部 catalog repo 複製 `tools\<工具 id>\` 到客戶 repo，**只保留要給客戶的版本**（刪除其他 `releases\<版本>\`）。
2. 檢視並視需要改寫 `README.md`、`tool.toml` 的摘要、各版本的 `changelog`，移除內部資訊（內部網址、專案代號、內部工具名稱）。`issue_url` 若指向內部系統請刪除。
3. **Python 工具**：zip 內要有 `wheels\`（含 `requirements.txt` 所需的全部套件）。作者可用：

   ```powershell
   pip download -r requirements.txt -d wheels --only-binary=:all: --platform win_amd64 --python-version 3.12
   ```

   再重新打包 zip。
4. ToolManager 本身要讓客戶自動更新的話，同樣把 `tools\toolmanager\` 與要給客戶的版本放進來。
5. 執行檢查腳本（先 `git lfs pull`，讓腳本能檢查 zip 內容）：

   ```powershell
   uv run python scripts\check_customer_catalog.py <客戶 repo 資料夾> --deny <內部關鍵字清單>
   ```

   內部關鍵字清單是一個文字檔，每行一個正規表示式（`#` 開頭為註解），例如內部主機名稱、專案代號。
   **建議由部門統一維護一份**，不要放進客戶 repo。檢查項目：

   | 檢查 | 結果 |
   |---|---|
   | 設定檔格式 | 錯誤 |
   | `catalog.toml` 含 `[jira]` | 錯誤 |
   | 未設定 `python_offline_only = true`（含寫錯位置） | 錯誤 |
   | Python 工具有 `requirements.txt` 但 zip 內沒有 `wheels\*.whl` | 錯誤 |
   | 名稱、摘要、README、更新說明、參數說明、zip 內檔名含內部關鍵字 | 錯誤 |
   | 設有 `issue_url` | 注意（確認客戶能開啟） |
   | zip 只有 LFS 指標檔 | 注意（無法檢查內容） |

6. 沒有錯誤後再發 PR、合併。客戶的 ToolManager 下次同步就會看到。

## 三、產生客戶版安裝程式

客戶版與內部版用法一致，都是安裝程式；但它是**獨立的一套安裝**，可以和內部版裝在同一台電腦（方便同仁自己測試客戶版）：

| | 內部版 | 客戶版 |
|---|---|---|
| 應用程式清單 / 解除安裝 | 自己的項目 | 另一個獨立項目（不同 AppId），互不影響 |
| 安裝位置 | `%LOCALAPPDATA%\ToolManager\` | `%LOCALAPPDATA%\ToolManager-Customer\` |
| 開機自動啟動項目 | `ToolManager` | `ToolManager (ToolManager-Customer)` |
| 「請先關閉 ToolManager」偵測 | 只看內部版 | 只看客戶版 |

1. 複製 `installer\customer-defaults.example.toml`，填入客戶 repo（**不要放進 git**）。檔案必須包含 `portable = true`。
   - `accent = "#2a4a6e"`：客戶版的主題色。畫面（頂部色帶、按鈕、選取項目）與圖示（捷徑、工作列、系統匣、應用程式清單）都換成這個顏色，和內部版的青綠一眼可分。建議用和 `#2a6e6a` 同飽和度、同明暗的顏色，圖示外框才會剛好是這個顏色。
2. 打包（同時產生內部版與客戶版安裝程式）：

   ```powershell
   .\scripts\build_windows.ps1 -DefaultsFile D:\dept\defaults.toml -CustomerDefaultsFile D:\dept\customer-defaults.toml
   ```

   產生 `dist\ToolManagerSetup-<版本>-customer.exe`。
   - 捷徑與「應用程式」清單的名稱預設為 `ToolManager`（給客戶的版本用這個）。
   - 同仁要在自己電腦上和內部版一起測試時，加上 `-CustomerDisplayName "ToolManager（客戶版）"` 另外打包一份，避免兩個捷徑同名。
3. 交付給客戶。客戶的使用方式：
   - 執行安裝程式（不需要系統管理員權限），完成後從開始功能表開啟。
   - 依設定精靈貼上自己的 co-git HTTP access token。
   - 之後的 ToolManager 更新：和內部版一樣由 ToolManager 自我更新（客戶 repo 需有 `tools\toolmanager\`，見上一節第 4 點）。
   - 移除：「設定 → 應用程式」解除安裝。已安裝的工具與設定（`data\`）會保留，完成時會顯示位置。

## 已知限制

- 目前所有客戶共用同一個客戶 repo。若之後不同客戶需要看到不同工具，需改為每個客戶一個 repo（見 DESIGN.md 的後續規劃）。
- 工具從內部複製到客戶 repo 是人工作業；需求明確後可改為匯出腳本自動產生。
- 客戶可在工具詳情頁看到 `owners`（內部 Bitbucket 帳號）。
