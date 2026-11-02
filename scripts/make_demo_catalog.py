"""Generate a demo catalog that looks like ToolManager after a while in use: about twenty
tools across every category and type, several versions each, some marked "recommended".

For screenshots and demos only. examples/catalog (which the tests rely on) stays small;
its two tools are copied in so the Python example still runs.

    uv run python scripts/make_demo_catalog.py <output folder>
    uv run toolmanager --catalog <output folder>
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import zipfile
from pathlib import Path

import tomli_w

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "catalog"

# id, name, summary, category, owners, type, entry, features, versions
# versions: (version, date, changelog, important note or "") oldest first
TOOLS = [
    ("i2c-debugger", "I2C Debugger", "透過 USB-I2C 轉接器讀寫晶片暫存器，支援批次腳本",
     "debug", ["user-a", "user-c"], "exe", "I2CDebugger.exe",
     ["單筆與批次讀寫", "暫存器位元欄位解析", "操作紀錄匯出 CSV"],
     [("1.0.0", "2026-03-02", "- 首次發布", ""),
      ("1.1.0", "2026-05-18", "- 新增：批次腳本\n- 修正：連續讀取時偶發逾時", ""),
      ("1.2.0", "2026-09-30", "- 新增：位元欄位解析\n- 新增：深色主題", "")]),
    ("edid-editor", "EDID Editor", "檢視、編輯與驗證 EDID / DisplayID 區塊",
     "debug", ["user-d"], "exe", "EdidEditor.exe",
     ["解析 Base block、CTA-861 與 DisplayID", "Checksum 自動重算", "與規格比對並標示錯誤"],
     [("2.0.0", "2026-02-10", "- 全新介面\n- 支援 DisplayID 2.0", ""),
      ("2.1.0", "2026-06-21", "- 新增：HDR Static Metadata 編輯", ""),
      ("2.1.1", "2026-10-28", "- 修正：存檔後 checksum 錯誤",
       "修正存檔後 checksum 錯誤，舊版存出的 EDID 可能無法被電視辨識")]),
    ("uart-log-viewer", "UART Log Viewer", "即時擷取 UART log，關鍵字標色與過濾",
     "debug", ["user-e"], "exe", "UartLogViewer.exe",
     ["多埠同時擷取", "關鍵字標色、正規表示式過濾", "依時間戳記對齊多份 log"],
     [("0.9.0", "2026-04-11", "- 預覽版", ""),
      ("1.0.0", "2026-07-03", "- 正式版\n- 新增：多埠同時擷取", "")]),
    ("dp-aux-monitor", "DP AUX Monitor", "監看並解碼 DisplayPort AUX 通道的交握過程",
     "debug", ["user-a"], "exe", "AuxMonitor.exe",
     ["Link Training 流程解碼", "DPCD 讀寫紀錄", "異常交握自動標示"],
     [("1.0.0", "2026-08-15", "- 首次發布", "")]),
    ("spi-flash-helper", "燒錄小幫手", "一鍵燒錄 SPI Flash，自動校驗並產生燒錄紀錄",
     "flash", ["user-f", "user-a"], "exe", "FlashHelper.exe",
     ["自動偵測 Flash 型號", "燒錄後校驗", "量產模式：多台連續燒錄"],
     [("3.0.0", "2026-01-20", "- 支援新一代燒錄器", ""),
      ("3.1.0", "2026-04-28", "- 新增：量產模式", ""),
      ("3.2.0", "2026-08-09", "- 新增：燒錄紀錄匯出", ""),
      ("3.2.1", "2026-10-30", "- 修正：特定 Flash 型號校驗誤判",
       "修正特定 Flash 型號在校驗時誤判成功的問題")]),
    ("fw-packer", "Firmware Packer", "合併 bootloader 與主程式，產生帶 CRC 的燒錄檔",
     "flash", ["user-b"], "python", "pack.py",
     ["依設定檔合併多個 binary", "自動加上版本資訊與 CRC", "輸出燒錄用 bin 與 hex"],
     [("1.0.0", "2026-05-06", "- 首次發布", ""),
      ("1.1.0", "2026-09-12", "- 新增：輸出 Intel HEX", "")]),
    ("isp-update-tool", "ISP Update Tool", "透過 ISP 模式更新板端韌體（安裝版）",
     "flash", ["user-c"], "installer", "ISPUpdateSetup.exe",
     ["USB 與 UART 兩種更新路徑", "更新失敗自動復原"],
     [("5.4.0", "2026-03-30", "- 支援新款開發板", ""),
      ("5.5.0", "2026-09-01", "- 更新速度提升約 30%", "")]),
    ("gamma-tuner", "Gamma Tuner", "調整並匯出面板 Gamma 曲線，即時預覽",
     "image", ["user-d", "user-e"], "exe", "GammaTuner.exe",
     ["拖曳控制點調整曲線", "R / G / B 分開調整", "匯出暫存器設定檔"],
     [("1.0.0", "2026-02-25", "- 首次發布", ""),
      ("1.1.0", "2026-07-19", "- 新增：曲線樣板庫", "")]),
    ("color-matrix", "色彩矩陣計算器", "由量測色座標計算 3x3 色彩校正矩陣",
     "image", ["user-e"], "python", "main.py",
     ["支援 sRGB / DCI-P3 / BT.2020 目標色域", "輸出定點數格式係數"],
     [("0.2.0", "2026-06-02", "- 新增：BT.2020", ""),
      ("0.3.0", "2026-10-05", "- 新增：定點數輸出", "")]),
    ("pattern-generator", "Test Pattern Generator", "產生常用測試圖樣：色條、灰階、棋盤格",
     "image", ["user-f"], "exe", "PatternGen.exe",
     ["30 種以上內建圖樣", "自訂解析度與色深", "批次輸出 PNG / BMP"],
     [("2.3.0", "2026-04-22", "- 新增：10-bit 灰階", ""),
      ("2.4.0", "2026-08-27", "- 新增：批次輸出", "")]),
    ("regmap-codegen", "RegMap Codegen", "由暫存器定義表產生 C 標頭檔與說明文件",
     "build", ["user-a", "user-b"], "python", "codegen.py",
     ["讀取 Excel 暫存器定義表", "產生 C header 與 Markdown 文件", "欄位重疊自動檢查"],
     [("1.0.0", "2026-01-15", "- 首次發布", ""),
      ("1.1.0", "2026-03-27", "- 新增：Markdown 文件輸出", ""),
      ("1.2.0", "2026-07-08", "- 新增：欄位重疊檢查", ""),
      ("1.3.0", "2026-10-21", "- 新增：支援多個暫存器區塊", "")]),
    ("timing-calculator", "Timing Calculator", "計算 CVT / CEA 時序參數與 pixel clock",
     "build", ["user-d"], "python", "main.py",
     ["CVT、CVT-RB、CEA-861 標準時序", "輸出暫存器設定值"],
     [("1.0.0", "2026-05-30", "- 首次發布", "")]),
    ("build-helper", "Build Helper", "一鍵切換工具鏈版本並建置韌體專案",
     "build", ["user-b"], "exe", "BuildHelper.exe",
     ["工具鏈版本管理", "常用建置設定一鍵套用", "建置錯誤摘要"],
     [("0.8.0", "2026-06-14", "- 預覽版", ""),
      ("0.9.0", "2026-09-25", "- 新增：建置錯誤摘要", "")]),
    ("regression-runner", "Regression Runner", "批次執行回歸測試並產生 HTML 報告",
     "test", ["user-c", "user-f"], "python", "run.py",
     ["依測試清單批次執行", "失敗項目自動重試", "HTML 報告與趨勢圖"],
     [("2.0.0", "2026-02-18", "- 改用新測試框架", ""),
      ("2.1.0", "2026-06-30", "- 新增：失敗自動重試", ""),
      ("2.2.0", "2026-10-14", "- 新增：趨勢圖", "")]),
    ("frame-compare", "Frame Compare", "逐像素比對兩張擷取畫面，標示差異區域",
     "test", ["user-e"], "exe", "FrameCompare.exe",
     ["容許誤差設定", "差異熱度圖", "批次比對資料夾"],
     [("1.0.0", "2026-07-22", "- 首次發布", "")]),
    ("spec-diff", "Spec Diff", "比較兩版規格書 PDF 的文字差異",
     "doc", ["user-a"], "exe", "SpecDiff.exe",
     ["依章節對齊比對", "匯出差異報告"],
     [("1.0.0", "2026-04-03", "- 首次發布", ""),
      ("1.0.1", "2026-05-29", "- 修正：表格內容比對錯位", "")]),
    ("release-note-gen", "Release Note 產生器", "由 commit 訊息整理出版本說明",
     "doc", ["user-b"], "python", "main.py",
     ["依類型分組（新增、修正、其他）", "輸出 Markdown 與 Word"],
     [("1.0.0", "2026-08-20", "- 首次發布", "")]),
    ("crc-calculator", "CRC Calculator", "計算檔案或十六進位字串的 CRC / Checksum",
     "misc", ["user-f"], "exe", "CrcCalc.exe",
     ["CRC-8 / 16 / 32 與自訂多項式", "拖放檔案計算"],
     [("1.2.0", "2026-03-11", "- 新增：自訂多項式", "")]),
    ("hex-viewer", "Hex Viewer", "大檔案十六進位檢視、搜尋與比對",
     "misc", ["user-c"], "exe", "HexViewer.exe",
     ["GB 級檔案即時開啟", "兩檔差異比對", "結構樣板解析"],
     [("4.0.0", "2026-05-12", "- 全新引擎，大檔開啟速度提升", ""),
      ("4.1.0", "2026-09-18", "- 新增：結構樣板", "")]),
]


def readme(name: str, summary: str, features: list[str], tool_type: str) -> str:
    usage = {
        "exe": "1. 在 ToolManager 按「安裝」。\n2. 按「開啟」啟動程式。",
        "python": "1. 在 ToolManager 按「安裝」。\n2. 按「執行」，填寫表單後執行。",
        "installer": "1. 在 ToolManager 按「下載並開啟安裝程式」。\n2. 依安裝精靈完成安裝。",
    }[tool_type]
    items = "\n".join(f"- {f}" for f in features)
    return f"# {name}\n\n{summary}。\n\n## 功能\n\n{items}\n\n## 使用方式\n\n{usage}\n"


def write_tool(root: Path, tool: tuple) -> None:
    tool_id, name, summary, category, owners, tool_type, entry, features, versions = tool
    folder = root / "tools" / tool_id
    folder.mkdir(parents=True)
    (folder / "tool.toml").write_text(tomli_w.dumps({
        "id": tool_id, "name": name, "summary": summary, "category": category,
        "owners": owners, "type": tool_type}), "utf-8")
    (folder / "README.md").write_text(readme(name, summary, features, tool_type), "utf-8")
    for version, date, changelog, note in versions:
        rel = folder / "releases" / version
        rel.mkdir(parents=True)
        data: dict = {"version": version, "date": dt.date.fromisoformat(date)}
        if note:
            data |= {"important": True, "important_note": note}
        data |= {"entry": entry, "changelog": changelog + "\n"}
        (rel / "release.toml").write_text(tomli_w.dumps(data, multiline_strings=True), "utf-8")
        with zipfile.ZipFile(rel / f"{tool_id}-{version}.zip", "w") as zf:
            body = (f"print('{name} {version}')\n" if tool_type == "python"
                    else f"demo placeholder for {name} {version}\n")
            zf.writestr(entry, body)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0])
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(EXAMPLES, out)  # catalog.toml, register-viewer, csv-plotter
    for tool in TOOLS:
        write_tool(out, tool)
    print(f"{out}: {len(TOOLS) + 2} tools")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
