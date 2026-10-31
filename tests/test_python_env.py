"""Environment creation with the real uv / venv, fully offline (packages from a local wheel)."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from toolmanager.core.python_env import (
    EnvError,
    EnvManager,
    PythonRuntime,
    UvBackend,
    VenvPipBackend,
    venv_python,
)
from toolmanager.i18n import set_language

UV = shutil.which("uv")


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


def make_wheel(folder: Path, name: str = "tmdemo", version: str = "1.0") -> Path:
    """A minimal valid pure-Python wheel, so no network is needed."""
    folder.mkdir(parents=True, exist_ok=True)
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"VALUE = '{name} {version}'\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
                         "Tag: py3-none-any\n",
    }
    record = []
    for path, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content.encode()).digest())
        record.append(f"{path},sha256={digest.rstrip(b'=').decode()},{len(content.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\n".join(record) + "\n"
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return wheel


def tool_with_wheel(tmp_path: Path) -> Path:
    tool_dir = tmp_path / "tools" / "demo" / "1.0.0"
    make_wheel(tool_dir / "wheels")
    (tool_dir / "requirements.txt").write_text("tmdemo==1.0\n", "utf-8")
    return tool_dir


def run_in(venv: Path, code: str) -> str:
    return subprocess.run([str(venv_python(venv)), "-c", code], capture_output=True,
                          text=True, check=True).stdout.strip()


RUNTIME = PythonRuntime(python=Path(sys.executable), uv=Path(UV) if UV else None)


@pytest.mark.parametrize("backend_name", ["uv", "venv"])
def test_offline_env_from_bundled_wheels(tmp_path: Path, backend_name: str) -> None:
    if backend_name == "uv" and UV is None:
        pytest.skip("uv not installed")
    backend = (UvBackend(RUNTIME, cache_dir=tmp_path / "uv-cache") if backend_name == "uv"
               else VenvPipBackend(RUNTIME))
    manager = EnvManager(tmp_path / "envs", backend)
    tool_dir = tool_with_wheel(tmp_path)
    output: list[str] = []

    venv = manager.ensure("demo", "1.0.0", tool_dir, ">=3.11", output.append)
    assert run_in(venv, "import tmdemo; print(tmdemo.VALUE)") == "tmdemo 1.0"
    assert any("--no-index" in line for line in output)  # offline: wheels, not the network
    assert manager.is_ready("demo", "1.0.0", tool_dir)

    # Unchanged inputs: reused, nothing re-run.
    output.clear()
    assert manager.ensure("demo", "1.0.0", tool_dir, "", output.append) == venv
    assert output == []


@pytest.mark.skipif(UV is None, reason="uv not installed")
def test_changed_requirements_rebuild_the_env(tmp_path: Path) -> None:
    manager = EnvManager(tmp_path / "envs", UvBackend(RUNTIME, cache_dir=tmp_path / "c"))
    tool_dir = tool_with_wheel(tmp_path)
    manager.ensure("demo", "1.0.0", tool_dir, "", lambda _: None)

    make_wheel(tool_dir / "wheels", version="2.0")
    (tool_dir / "requirements.txt").write_text("tmdemo==2.0\n", "utf-8")
    assert not manager.is_ready("demo", "1.0.0", tool_dir)
    venv = manager.ensure("demo", "1.0.0", tool_dir, "", lambda _: None)
    assert run_in(venv, "import tmdemo; print(tmdemo.VALUE)") == "tmdemo 2.0"


@pytest.mark.skipif(UV is None, reason="uv not installed")
def test_failed_install_leaves_no_env(tmp_path: Path) -> None:
    manager = EnvManager(tmp_path / "envs", UvBackend(RUNTIME, cache_dir=tmp_path / "c"))
    tool_dir = tool_with_wheel(tmp_path)
    (tool_dir / "requirements.txt").write_text("not-a-real-package-xyz==9\n", "utf-8")
    with pytest.raises(EnvError, match="failed"):
        manager.ensure("demo", "1.0.0", tool_dir, "", lambda _: None)
    assert not (tmp_path / "envs" / "demo" / "1.0.0").exists()


def test_python_requirement_is_checked(tmp_path: Path) -> None:
    manager = EnvManager(tmp_path / "envs", VenvPipBackend(RUNTIME))
    with pytest.raises(EnvError, match="needs Python >=99"):
        manager.ensure("demo", "1.0.0", tmp_path, ">=99", lambda _: None)
    with pytest.raises(EnvError, match="Invalid Python requirement"):
        manager.check_python("not a spec")


def test_no_requirements_means_plain_env(tmp_path: Path) -> None:
    manager = EnvManager(tmp_path / "envs", VenvPipBackend(RUNTIME))
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    venv = manager.ensure("plain", "0.1.0", tool_dir, "", lambda _: None)
    assert venv_python(venv).is_file()


def test_prune_removes_only_owned_envs_of_removed_versions(tmp_path: Path) -> None:
    manager = EnvManager(tmp_path / "envs", VenvPipBackend(RUNTIME))
    for tool, version in [("a", "1.0.0"), ("a", "2.0.0"), ("b", "1.0.0")]:
        d = manager.env_dir(tool, version)
        d.mkdir(parents=True)
        (d / ".tm-owned").touch()
    foreign = manager.env_dir("0-first", "1.0.0")  # sorts first: must not stop the pruning
    foreign.mkdir(parents=True)  # no marker: never touched
    manager.prune({("a", "2.0.0"), ("b", "1.0.0")})
    assert not manager.env_dir("a", "1.0.0").exists()
    assert manager.env_dir("a", "2.0.0").exists() and manager.env_dir("b", "1.0.0").exists()
    assert foreign.exists()


def test_uv_backend_never_downloads_interpreters(tmp_path: Path, monkeypatch) -> None:
    if UV is None:
        pytest.skip("uv not installed")
    seen: dict = {}

    def fake_stream(cmd, env, output):
        seen.setdefault("envs", []).append(env)
        seen.setdefault("cmds", []).append(cmd)

    monkeypatch.setattr("toolmanager.core.python_env._stream", fake_stream)
    backend = UvBackend(RUNTIME, index_url="https://pypi.example.com/simple")
    backend.create(tmp_path / "venv", tmp_path / "req.txt", None, lambda _: None)
    assert all(env["UV_PYTHON_DOWNLOADS"] == "never" for env in seen["envs"])
    assert seen["cmds"][0][:3] == [UV, "venv", "--python"]
    assert seen["cmds"][1][-2:] == ["--index-url", "https://pypi.example.com/simple"]
