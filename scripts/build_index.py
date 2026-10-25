#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把语块向量化并落盘（零第三方依赖）。

输入  data/rules.jsonl + definitions.jsonl + preface.jsonl + appendix.jsonl
输出  data/embeddings.bin   float32 紧凑二进制，N x 2048，与 index.json 行序一一对应
      data/index.json       语块元数据

⚠️ appendix.jsonl 是**附录层**（2026-10-18 加）：官方英文附录 v2.0 的中文译文，
   `type="appendix"`、`chapter="APP"`。它**不是正文条款**，前端/提示词都据此标注。

为什么用 array + 二进制而不是 numpy/npz：
  语料只有四百多条，纯 Python 全量点积约 70ms，已经达标；
  为它引入 numpy 会让"clone 下来就能跑"变成"先装依赖"，不值当。
"""
from __future__ import annotations

import array
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, embedder  # noqa: E402

# GBK/CP936 控制台下 `[×]` 等符号会抛 UnicodeEncodeError 并以非零码退出。
# 降级为替换编码，避免"打印失败"被误读成"建索引失败"。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

CORPUS_FILES = ("rules.jsonl", "definitions.jsonl", "preface.jsonl", "appendix.jsonl")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> int:
    force = "--force" in sys.argv
    check_only = "--check" in sys.argv

    items: list[dict] = []
    for name in CORPUS_FILES:
        rows = load_jsonl(config.DATA_DIR / name)
        print(f"  载入 {name:22s} {len(rows):4d} 条")
        items.extend(rows)
    if not items:
        print("[×] 语料为空，请先运行 scripts/build_corpus.py", file=sys.stderr)
        return 3 if check_only else 1

    bin_p = config.DATA_DIR / "embeddings.bin"
    meta_p = config.DATA_DIR / "index.json"

    fingerprint = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    current = False
    if bin_p.exists() and meta_p.exists():
        try:
            old = json.loads(meta_p.read_text(encoding="utf-8"))
            current = (old.get("corpus_sha256") == fingerprint
                       and old.get("model") == config.EMBED_MODEL
                       and old.get("dims") == config.EMBED_DIMS
                       and old.get("query_prefix") == config.QUERY_PREFIX
                       and len(old.get("items", [])) == len(items)
                       and bin_p.stat().st_size == len(items) * config.EMBED_DIMS * 4)
        except (OSError, ValueError, KeyError):
            current = False
    if check_only:
        return 0 if current else 3
    if current and not force:
        print(f"[跳过] 索引与当前语料及模型一致（{len(items)} 条）。")
        return 0

    texts = [it["search_text"] for it in items]
    print(f"\n[向量化] {len(texts)} 条 · 模型 {config.EMBED_MODEL} · {config.EMBED_DIMS} 维")
    t0 = time.time()
    try:
        vecs = embedder.embed_documents(texts)
    except embedder.EmbedError as exc:
        # 不把原始 traceback 甩给用户：这一步失败有明确的原因分类和修法。
        sys.stdout.flush()          # 否则错误会插在"载入…"前面，看起来像另一个问题
        msg = str(exc)
        print(f"\n[×] 向量化失败：{msg}", file=sys.stderr)
        if "未配置 ZHIPU_API_KEY" in msg:
            print("\n    建索引必须调用智谱的 embedding 接口，没有密钥这一步做不了。", file=sys.stderr)
            print("      1) 到 https://open.bigmodel.cn/ 注册，创建一个 API Key", file=sys.stderr)
            print("      2) 打开 .env，填 ZHIPU_API_KEY=你的密钥（没有 .env 就先从 .env.example 复制）", file=sys.stderr)
            print("      3) 重新运行本脚本", file=sys.stderr)
            print(f"\n    注：本次会对 {len(texts)} 条语料各调一次接口，按智谱 embedding "
                  "定价计费，量很小但不为零。", file=sys.stderr)
        else:
            print("\n    请依次检查：", file=sys.stderr)
            print("      · Key 是否复制完整（前后不能有空格）", file=sys.stderr)
            print("      · 智谱账户是否有可用余额 / 是否欠费", file=sys.stderr)
            print("      · 网络能否访问 open.bigmodel.cn（公司代理或防火墙可能拦截）", file=sys.stderr)
            print("      · EMBED_MODEL / EMBED_DIMS 是否为服务端支持的组合", file=sys.stderr)
        return 2
    dt = time.time() - t0

    dims = len(vecs[0]) if vecs else 0
    if any(len(v) != dims for v in vecs):
        print("[×] 向量维度不一致", file=sys.stderr)
        return 1

    flat = array.array("f")
    for v in vecs:
        flat.extend(v)
    bin_p.write_bytes(flat.tobytes())

    meta_p.write_text(json.dumps({
        "model": config.EMBED_MODEL,
        "dims": dims,
        "query_prefix": config.QUERY_PREFIX,
        "corpus_sha256": fingerprint,
        "count": len(items),
        "items": items,
    }, ensure_ascii=False), encoding="utf-8")

    print(f"\n[完成] 耗时 {dt:.1f}s")
    print(f"  {bin_p.name:18s} {bin_p.stat().st_size / 1024 / 1024:6.2f} MB")
    print(f"  {meta_p.name:18s} {meta_p.stat().st_size / 1024:6.1f} KB")
    print(f"  矩阵 {len(vecs)} x {dims} · 首条向量范数 {sum(x * x for x in vecs[0]) ** 0.5:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
