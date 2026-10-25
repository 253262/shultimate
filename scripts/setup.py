#!/usr/bin/env python3
"""Guided local setup for Windows, macOS, and Linux (standard library only)."""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _configure_output() -> None:
    """Use UTF-8 for console output.

    GitHub Actions runs Python 3.13 on an en-US Windows image whose redirected
    stdout is cp1252.  Printing the Chinese setup messages then raises
    UnicodeEncodeError before any installation work starts.  Reconfiguring the
    streams here keeps the guided installer usable from cmd, PowerShell and CI.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


_configure_output()
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def _configured_key(path: Path) -> bool:
    if os.environ.get("ZHIPU_API_KEY", "").strip():
        return True
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("ZHIPU_API_KEY=") and line.split("=", 1)[1].strip():
            return True
    return False


def _save_key(path: Path, template: Path, key: str) -> None:
    """Write the user's key to the ignored local .env without printing it."""
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("Key 不能为空，也不能包含空格或换行。")
    source = path if path.exists() else template
    lines = source.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith("ZHIPU_API_KEY="):
            if not found:
                output.append("ZHIPU_API_KEY=" + key)
                found = True
            continue
        output.append(line)
    if not found:
        output.append("ZHIPU_API_KEY=" + key)
    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(output) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _ensure_env_file() -> None:
    if ENV_FILE.exists():
        return
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(ENV_EXAMPLE.read_text(encoding="utf-8"))


def _ensure_key() -> bool:
    if "ZHIPU_API_KEY" in os.environ and not os.environ["ZHIPU_API_KEY"].strip():
        os.environ.pop("ZHIPU_API_KEY")
    if _configured_key(ENV_FILE):
        print("[配置] 已找到智谱 Key（不会显示内容）。")
        return True
    try:
        _ensure_env_file()
    except OSError as exc:
        print(f"无法准备本机 .env：{exc}", file=sys.stderr)
        return False
    print("先在 https://open.bigmodel.cn/ 申请你自己的智谱 API Key。")
    print("Key 只保存在本机 .env；不要贴到聊天、Issue 或提交到 Git。")
    if not sys.stdin.isatty():
        print("当前终端无法安全地交互输入。请自行填写 .env 中的 ZHIPU_API_KEY 后重试。")
        return False
    try:
        key = getpass.getpass("请粘贴智谱 API Key（输入不显示）：").strip()
        _save_key(ENV_FILE, ENV_EXAMPLE, key)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消；没有写入 Key。")
        return False
    except (OSError, ValueError) as exc:
        print(f"无法保存 Key：{exc}", file=sys.stderr)
        return False
    print("[配置] Key 已保存到本机 .env。")
    return True


def _run_step(script: str) -> bool:
    print(f"\n[运行] {script}", flush=True)
    result = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT, check=False)
    return result.returncode == 0


def _run_offline_tests() -> bool:
    print("\n[运行] 离线单元测试", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def _index_ready() -> bool:
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_index.py"), "--check"],
                            cwd=ROOT, check=False, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.returncode == 0


def _ensure_pdf_reader() -> bool:
    if importlib.util.find_spec("pypdf") is not None:
        return True
    if sys.prefix == sys.base_prefix:
        print("请先建立虚拟环境，或使用 setup.cmd / setup.ps1 / setup.sh。", file=sys.stderr)
        return False
    print("[依赖] 在项目虚拟环境中安装 PDF 解析库。", flush=True)
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                             str(ROOT / "requirements.txt")], cwd=ROOT, check=False)
    return result.returncode == 0


def _setting(name: str, default: str) -> str:
    if name in os.environ:
        return os.environ[name].strip()
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith(name + "="):
                return raw.split("=", 1)[1].strip()
    return default


def _confirm_index_cost(approved: bool = False) -> bool:
    print("\n建索引会调用你自己的智谱账号并产生费用；只在首次建库或重建时需要。")
    if approved:
        print("已收到明确的建索引费用确认。")
        return True
    if not sys.stdin.isatty():
        print("请在交互终端运行安装脚本，亲自确认这一步。")
        return False
    try:
        answer = input("现在建索引吗？输入 y 继续：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消建索引。")
        return False
    return answer in ("y", "yes")


def _wait_for_server(process: subprocess.Popen[bytes], url: str) -> bool:
    local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        if process.poll() is not None:
            return False
        try:
            with local_opener.open(url + "/api/health", timeout=0.5) as response:
                if response.status == 200 and process.poll() is None:
                    return True
        except urllib.error.HTTPError as exc:
            # Public password enabled: the browser will display its login prompt.
            if exc.code == 401 and process.poll() is None:
                return True
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="配置 Key、建索引并启动飞盘规则助手")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="只安装 PDF 依赖、构建本地语料并运行单测；不读取 Key、不调用模型、不启动服务",
    )
    parser.add_argument("--approve-index-cost", action="store_true",
                        help="已由用户明确同意本次建索引所需的模型调用费用")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本。", file=sys.stderr)
        return 1
    os.chdir(ROOT)
    if not _ensure_pdf_reader():
        return 1
    if args.offline_check:
        if not _run_step("scripts/build_corpus.py"):
            return 1
        return 0 if _run_offline_tests() else 1
    if not _ensure_key():
        return 1
    if not _run_step("scripts/build_corpus.py"):
        return 1
    if not _index_ready():
        if not _confirm_index_cost(args.approve_index_cost):
            print("已完成本地准备。稍后再次运行本脚本即可继续。")
            return 0
        if not _run_step("scripts/build_index.py"):
            return 1
    if not _index_ready():
        print("索引文件未生成，请检查上面的建索引输出。", file=sys.stderr)
        return 1
    port = _setting("PORT", "8000")
    url = f"http://127.0.0.1:{port}"
    print(f"\n[启动] {url}；关闭此终端或按 Ctrl+C 可停止服务。", flush=True)
    process = subprocess.Popen([sys.executable, str(ROOT / "app/server.py")], cwd=ROOT)
    if not _wait_for_server(process, url):
        if process.poll() is None:
            process.terminate()
        print("服务未能启动。请查看终端中的错误信息。", file=sys.stderr)
        return 1
    if not args.no_browser:
        webbrowser.open(url)
    try:
        return process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
