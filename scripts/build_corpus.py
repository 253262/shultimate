#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a local Chinese corpus from the rights holder's official PDF.

The PDF and extracted Chinese text stay on the user's computer. Neither is
distributed with this source repository. Requires pypdf (see requirements.txt).
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PDF = DATA / "source" / "official-zh.pdf"
URL = "https://www.sport.gov.cn/stzx/n5434/c28603165/part/28611213.pdf"
PDF_SHA256 = "c421b7e79ab598465db9341338892207d1724bf4487fb476d01fd1a3e0f6aad5"
SOURCE = "世界飞盘联合会团队飞盘规则（2025-2028）中文版 · 体育总局社体中心"
CLAUSE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})\.?\s*(?![、,，.\d])(\S.*)$")
CHAPTER = re.compile(r"^(\d{1,2})\.\s*([^\d].+)$")
PAGE = re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页$")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download_pdf() -> bytes:
    """Download the official PDF, preserving the site's anti-bot cookie.

    Some CDN edges answer the first request with a redirect that sets
    ``HMF_CI`` and keep redirecting until the cookie is sent back.  urllib's
    default redirect handler does not keep cookies, which produced an
    "infinite redirect" error on a macOS CI runner.  A cookie jar makes the
    download behave like a browser; retries cover transient edge failures.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 Shultimate/0.4"),
        ("Accept", "application/pdf,*/*"),
        ("Referer", "https://www.sport.gov.cn/stzx/n5434/c28603165/content.html"),
    ]
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(URL, timeout=30) as response:
                return response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if last_error is None:
        raise OSError("官网 PDF 下载失败，原因未知。")
    raise last_error


def _source_pdf() -> Path:
    PDF.parent.mkdir(parents=True, exist_ok=True)
    if PDF.is_file():
        if _sha(PDF) != PDF_SHA256:
            raise ValueError("本机 PDF 与已验证的官网版本不一致；请删除 data/source/official-zh.pdf 后重试。")
        return PDF
    print("从体育总局官网下载中文版 PDF 到本机；该文件不会进入公开仓库。", flush=True)
    raw = _download_pdf()
    if len(raw) > 8 * 1024 * 1024 or not raw.startswith(b"%PDF"):
        raise ValueError("官网下载结果不是预期的 PDF。")
    if hashlib.sha256(raw).hexdigest() != PDF_SHA256:
        raise ValueError("官网 PDF 的版本与已验证版本不一致；已停止构建，请核查新版本。")
    temporary = PDF.with_suffix(".pdf.tmp")
    temporary.write_bytes(raw)
    temporary.replace(PDF)
    return PDF


def _join(left: str, right: str) -> str:
    right = re.sub(r"\s+", " ", right).strip()
    if not left:
        return right
    spacer = " " if left[-1].isascii() and left[-1].isalnum() and right[:1].isascii() and right[:1].isalnum() else ""
    return left + spacer + right


def _rule_lines(reader) -> list[str]:
    lines: list[str] = []
    started = False
    for page in reader.pages:
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if CHAPTER.match(line) and CHAPTER.match(line).group(1) == "1":
                started = True
            if not started:
                continue
            if line == "名词定义":
                return lines
            if line and not PAGE.match(line):
                lines.append(line)
    raise ValueError("官网 PDF 缺少名词定义章节，无法确定规则正文边界。")


def _rules(reader) -> list[dict]:
    lines = _rule_lines(reader)
    # A few continuation lines begin with a clause reference. Use only the
    # last occurrence of each ID, which is its actual paragraph in this PDF.
    last: dict[str, int] = {}
    for i, line in enumerate(lines):
        match = CLAUSE.match(line)
        if match:
            last[match.group(1)] = i
    rows: list[dict] = []
    chapter_title = ""
    current: dict | None = None
    for i, line in enumerate(lines):
        heading = CHAPTER.match(line)
        if heading and 1 <= int(heading.group(1)) <= 20:
            chapter_title = heading.group(2).strip()
            current = None
            continue
        match = CLAUSE.match(line)
        if match and last.get(match.group(1)) == i:
            cid, body = match.groups()
            parts = cid.split(".")
            current = {
                "id": cid, "type": "rule", "chapter": parts[0],
                "chapter_title": chapter_title, "text": body.strip(),
                "parent": ".".join(parts[:-1]), "depth": len(parts),
                "source": SOURCE,
            }
            rows.append(current)
        elif current is not None:
            current["text"] = _join(current["text"], line)
    for row in rows:
        row["search_text"] = f"【{row['chapter_title']}】第{row['id']}条 {row['text']}"
    if len(rows) != 368 or len({row["id"] for row in rows}) != 368:
        raise ValueError(f"规则切分异常：得到 {len(rows)} 条，预期 368 条。")
    if len({row["chapter"] for row in rows}) != 20:
        raise ValueError("规则切分异常：未找到完整的 20 章。")
    return rows


def _glossary_pages(reader) -> list:
    for i, page in enumerate(reader.pages):
        if "名词定义" in (page.extract_text() or "").splitlines():
            return list(reader.pages[i:])
    raise ValueError("官网 PDF 缺少名词定义。")


def _definition_terms(pages: list) -> list[str]:
    candidates: list[tuple[int, float, float, str]] = []
    for page_no, page in enumerate(pages):
        def collect(text, cm, tm, font, size):
            word = text.strip()
            if 50 <= tm[4] < 140 and 100 < tm[5] < 790 and word and len(word) < 16:
                candidates.append((page_no, round(tm[5], 1), round(tm[4], 1), word))
        page.extract_text(visitor_text=collect)
    grouped: dict[tuple[int, float], list[tuple[float, str]]] = {}
    for page_no, y, x, word in candidates:
        grouped.setdefault((page_no, y), []).append((x, word))
    terms = ["".join(word for x, word in sorted(grouped[key]))
             for key in sorted(grouped)]
    if len(terms) != 40:
        raise ValueError(f"名词定义切分异常：识别到 {len(terms)} 个术语，预期 40 个。")
    return terms


def _definitions(reader) -> list[dict]:
    pages = _glossary_pages(reader)
    terms = _definition_terms(pages)
    lines = [line.strip() for page in pages
             for line in (page.extract_text() or "").splitlines()
             if line.strip() and not PAGE.match(line.strip()) and line.strip() != "名词定义"]
    rows: list[dict] = []
    next_term = 0
    current: dict | None = None
    for line in lines:
        term = terms[next_term] if next_term < len(terms) else None
        marker = re.match(r"^" + re.escape(term) + r"(?:\s+|$)(.*)$", line) if term else None
        if marker:
            current = {
                "id": f"DEF:{term}", "type": "definition", "term": term,
                "alias": None, "chapter": "0", "chapter_title": "名词定义",
                "text": marker.group(1).strip(), "parent": None, "depth": 1,
                "source": f"{SOURCE} · 名词定义",
            }
            rows.append(current)
            next_term += 1
        elif current is not None:
            current["text"] = _join(current["text"], line)
    if len(rows) != 40 or any(not row["text"] for row in rows):
        raise ValueError(f"名词定义切分异常：得到 {len(rows)} 条，预期 40 条。")
    for row in rows:
        row["search_text"] = f"【名词定义】术语「{row['term']}」{row['text']}"
    return rows


def _preface(reader) -> list[dict]:
    for page in reader.pages:
        lines = [line.strip() for line in (page.extract_text() or "").splitlines()]
        if "简介" in lines:
            start = lines.index("简介") + 1
            body = ""
            for line in lines[start:]:
                if line and not PAGE.match(line):
                    body = _join(body, line)
            return [{
                "id": "PRE:1", "type": "preface", "chapter": "0",
                "chapter_title": "简介", "text": body, "parent": None,
                "depth": 1, "search_text": f"【简介】{body}", "source": SOURCE,
            }]
    raise ValueError("官网 PDF 缺少简介。")


def main() -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("请先运行 python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    try:
        reader = PdfReader(_source_pdf())
        rules, defs, preface = _rules(reader), _definitions(reader), _preface(reader)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"语料构建失败：{exc}", file=sys.stderr)
        print(f"官网下载页面：https://www.sport.gov.cn/stzx/n5434/c28603165/content.html", file=sys.stderr)
        return 2
    DATA.mkdir(parents=True, exist_ok=True)
    for name, rows in (("rules.jsonl", rules), ("definitions.jsonl", defs), ("preface.jsonl", preface)):
        (DATA / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        print(f"{name}: {len(rows)} 条")
    summary = {
        "source": SOURCE, "source_sha256": PDF_SHA256,
        "counts": {"rules": len(rules), "definitions": len(defs), "preface": len(preface)},
        "by_chapter": dict(Counter(row["chapter"] for row in rules)),
        "duplicate_ids": [], "empty_text": [],
    }
    (DATA / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("校验结果: 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
