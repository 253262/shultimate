#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reject the rights holder's official Chinese rules files from the public repo.

The project deliberately does not redistribute the official Chinese rules PDF
or the text extracted from it.  Users download the PDF from the rights holder
and build the corpus on their own computer.  This guard runs in CI and can be
run locally before committing:

    python scripts/check_public_tree.py

It checks both the current index and every commit reachable from any ref, so a
translation file cannot be added in a later commit and then quietly removed
from the tip.
"""
from __future__ import annotations

import subprocess
import sys


def _configure_output() -> None:
    """Keep Chinese status messages from crashing on Windows' cp1252 stdout."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


_configure_output()

FORBIDDEN = {
    "data/definitions.jsonl",
    "data/preface.jsonl",
    "data/rules.jsonl",
    "data/source/official-zh.pdf",
    "data/source/rules-source.json",
    "data/summary.json",
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8")


def main() -> int:
    try:
        tracked = set(_git("ls-files", "data").splitlines())
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"无法读取 Git 信息：{exc}。请在项目根目录运行本脚本。", file=sys.stderr)
        return 2
    bad = sorted(tracked & FORBIDDEN)
    if bad:
        print("公开树包含官方中文规则文件：" + ", ".join(bad), file=sys.stderr)
        return 1

    try:
        commits = _git("rev-list", "--all").splitlines()
    except subprocess.CalledProcessError as exc:
        print(f"无法读取 Git 历史：{exc}。", file=sys.stderr)
        return 2
    for commit in commits:
        try:
            names = set(
                _git("ls-tree", "-r", "--name-only", commit, "data").splitlines()
            )
        except subprocess.CalledProcessError:
            continue
        bad = sorted(names & FORBIDDEN)
        if bad:
            print(
                f"历史提交 {commit[:12]} 包含官方中文规则文件：{', '.join(bad)}",
                file=sys.stderr,
            )
            return 1

    print(
        f"公开树检查通过：{len(tracked)} 个 data 文件，"
        f"{len(commits)} 个提交，无官方中文规则文件。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
