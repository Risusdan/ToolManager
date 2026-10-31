# ToolManager

部門內部工具的 Marketplace：瀏覽、搜尋、一鍵安裝 / 更新 / 回退、執行與發布工具。

- 設計文件：[docs/DESIGN.md](docs/DESIGN.md)
- P0 技術驗證清單：[docs/P0.md](docs/P0.md)
- 客戶簡化版：[docs/CUSTOMER.md](docs/CUSTOMER.md)
- 使用手冊：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- 作者手冊（上架工具）：[docs/AUTHOR_GUIDE.md](docs/AUTHOR_GUIDE.md)

## 開發

需求：Python 3.12、[uv](https://github.com/astral-sh/uv)

```bash
uv sync
uv run pytest -q
uv run ruff check .

# 以範例 catalog 啟動（Windows 為原生視窗；--browser 改用瀏覽器）
uv run toolmanager --catalog examples/catalog

# 修改範例 release.toml 後，重新產生範例 zip
uv run python scripts/make_example_artifacts.py
```

## 專案結構

```
src/toolmanager/
  core/       業務邏輯（不依賴 UI）：models、manifest、catalog_cache、installer、repository、env …
  backends/   Repository 實作：local（開發用）、bitbucket_server
  ui/         NiceGUI 介面：state（狀態與動作）、theme（字型、版面框架）、widgets、pages、setup_pages
  i18n/       翻譯檔
launcher/           C++ launcher（ToolManager.exe），見 launcher/README.md
installer/          Inno Setup 安裝程式腳本，見 installer/README.md
examples/catalog/   範例 catalog
scripts/            打包與 P0 驗證腳本
tests/              pytest
```

組織相關的設定（Bitbucket 網址、PyPI mirror 等）只存在於使用者的 `settings.toml`，不進入版本控制。
