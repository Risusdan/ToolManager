from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from toolmanager.core.models import ArgSpec, ArgType
from toolmanager.core.runner import ArgError, ScriptRun, build_args
from toolmanager.i18n import set_language

SPECS = (
    ArgSpec("input", ArgType.FILE, "輸入檔", required=True),
    ArgSpec("column", ArgType.STRING, "欄位"),
    ArgSpec("width", ArgType.INT, "寬度", default=40),
    ArgSpec("scale", ArgType.FLOAT, "倍率"),
    ArgSpec("mode", ArgType.CHOICE, "模式", choices=("fast", "full"), flag="-m"),
    ArgSpec("sort", ArgType.BOOL, "排序"),
    ArgSpec("target", ArgType.STRING, "目標", positional=True),
)


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


def test_build_args_maps_like_argparse() -> None:
    argv = build_args(SPECS, {"input": "C:\\data\\a b.csv", "column": "", "width": "12",
                              "scale": "1.5", "mode": "full", "sort": True, "target": "x"})
    assert argv == ["--input", "C:\\data\\a b.csv", "--width", "12", "--scale", "1.5",
                    "-m", "full", "--sort", "x"]


def test_defaults_apply_and_empty_optionals_are_left_out() -> None:
    argv = build_args(SPECS, {"input": "a.csv", "target": "t"})
    assert argv == ["--input", "a.csv", "--width", "40", "t"]


def test_int_accepts_whole_floats_only() -> None:
    assert build_args(SPECS, {"input": "a", "width": 7.0, "target": "t"})[3] == "7"
    with pytest.raises(ArgError, match="寬度"):
        build_args(SPECS, {"input": "a", "width": "7.5", "target": "t"})


@pytest.mark.parametrize(
    ("values", "label"),
    [
        ({"target": "t"}, "輸入檔"),               # required missing
        ({"input": "a"}, "目標"),                  # positional missing
        ({"input": "a", "target": "t", "scale": "abc"}, "倍率"),
        ({"input": "a", "target": "t", "mode": "other"}, "模式"),
    ],
)
def test_invalid_values(values: dict, label: str) -> None:
    with pytest.raises(ArgError, match=label):
        build_args(SPECS, values)


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "s.py"
    path.write_text(body, "utf-8")
    return [sys.executable, str(path)]


def test_run_captures_output_exit_code_and_log(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "import sys\nprint('第一行')\nprint('err', file=sys.stderr)\n"
                            "sys.exit(3)\n")
    run = ScriptRun(cmd, tmp_path, tmp_path / "logs" / "run.log")
    assert run.wait(10) == 3
    assert run.read_new() == ["第一行", "err"]
    text = (tmp_path / "logs" / "run.log").read_text("utf-8")
    assert "第一行" in text and "[exit code 3]" in text


def test_stop_ends_a_running_script(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
    run = ScriptRun(cmd, tmp_path)
    deadline = time.time() + 10
    while not run.read_new(1) and time.time() < deadline:
        time.sleep(0.05)
    run.stop()
    assert run.wait(10) is not None
    assert run.stopped and not run.running


def test_scripts_do_not_write_bytecode_caches(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text("X = 1\n", "utf-8")
    cmd = _script(tmp_path, "import helper\nprint(helper.X)\n")
    assert ScriptRun(cmd, tmp_path).wait(10) == 0
    assert not (tmp_path / "__pycache__").exists()
