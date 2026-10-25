#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收运行器：对人工题库跑 A 口径（命中率），按难度档 / 章节分别统计。

    python app/accept.py              # 纯检索基线
    python app/accept.py --rewrite    # 走线上流程（改写路 + 原问题路，逐维取余弦最大）
    python app/accept.py --detail     # 打印每题的实际名次

A 口径定义：expect 里的任一条款出现在候选前 N 名，即算命中。
  Hit@1  —— 排第 1
  Hit@3  —— 前 3 名内
  Hit@5  —— 前 5 名内
  Hit@8  —— 前 8 名内（= 线上 TOP_K，这是"能不能进生成层"的硬线）

T6（离题）不计命中率，单独看闸门拦截率。
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from operator import mul
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402
from app.retriever import Retriever, rrf_order  # noqa: E402

CASE_FILE = config.DATA_DIR / "acceptance.md"
LINE_PREFIX = re.compile(r"^(T\d)\s")


def load_cases() -> list[dict]:
    """解析 acceptance.md：T 开头的行是分档标题，{ 开头的行是 JSON 用例。"""
    cases: list[dict] = []
    for raw in CASE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if line.startswith("{"):
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[!] 跳过无法解析的行：{line[:60]}… ({exc})", file=sys.stderr)
    return cases


def rank_of(expect: list[str], ranked_ids: list[str]) -> int | None:
    """expect 里任一条款在 ranked_ids 中的最好名次（1 起）。"""
    if not expect:
        return None
    best = None
    for e in expect:
        if e in ranked_ids:
            r = ranked_ids.index(e) + 1
            best = r if best is None else min(best, r)
    return best


def audit_labels(cases: list[dict], ids: set[str]) -> list[str]:
    """题库自检：expect 里的条号必须真实存在于语料，否则这题永远不可能命中。

    这是血泪教训——「什么是轴心点」曾把 expect 标成 `DEF:轴心点`，
    而术语表里根本没有这个词条（官方名是「转身/轴心」「轴心位置」），
    于是 T1 档被一道**永远不可能命中**的题拉低了一整档的分数，
    追查了半轮才发现是标注错误而不是检索错误。
    标注错比检索错更隐蔽：它伪装成"检索能力不足"。
    """
    problems = []
    for c in cases:
        missing = [e for e in c.get("expect", []) if e not in ids]
        if missing:
            problems.append(f"[{c['tier']}] 「{c['q'][:30]}」expect 不存在于语料：{missing}")
    return problems


def main() -> int:
    argv = sys.argv[1:]
    with_rewrite = "--rewrite" in argv
    show_detail = "--detail" in argv

    cases = load_cases()
    if not cases:
        print(f"[×] 题库为空或不存在：{CASE_FILE}", file=sys.stderr)
        return 1

    r = Retriever()
    ids = [it["id"] for it in r.items]

    # 先自检题库，避免"标注错"伪装成"检索差"
    problems = audit_labels(cases, set(ids))
    if problems:
        print("[!] 题库标注有问题（这些题永远不可能命中）：", file=sys.stderr)
        for p in problems:
            print("   ", p, file=sys.stderr)
        print("    -> 请先修 data/acceptance.md 再跑验收\n", file=sys.stderr)

    # ---- 可选：改写 ----
    rewritten: dict[int, str] = {}
    if with_rewrite:
        from app import generator as gen_mod
        gen = gen_mod.Generator()
        if not gen.available:
            print("[!] 未配置 Key，无法改写", file=sys.stderr)
            return 1
        for i, c in enumerate(cases, 1):
            if c["tier"] == "T6":
                continue
            rewritten[i] = gen.rewrite_query(c["q"])
            if i % 10 == 0:
                print(f"    改写 {i}/{len(cases)}", flush=True)

    # ---- 逐题检索 ----
    rows = []
    t0 = time.perf_counter()
    for i, c in enumerate(cases):
        q = c["q"]
        rw = rewritten.get(i, "")
        # 走线上同一条 search()，保证测的就是线上行为
        res = r.search(q, top_k=len(ids), extra=rw)
        ranked = [h["id"] for h in res["hits"]]
        rows.append({**c, "ranked": ranked, "best_cos": res["best_cos"],
                     "rw": rw, "routes": res["routes"],
                     "structured": res.get("structured", False),
                     "confident": res.get("confident", False),
                     "domain_word": res.get("domain_word")})
    elapsed = time.perf_counter() - t0

    # ---- 统计 ----
    scored = [x for x in rows if x["tier"] != "T6"]
    ks = (1, 3, 5, 8)

    def summarize(subset: list[dict]) -> dict:
        out = {k: 0 for k in ks}
        for x in subset:
            rk = rank_of(x["expect"], x["ranked"])
            for k in ks:
                if rk and rk <= k:
                    out[k] += 1
        return out

    total = summarize(scored)
    n = len(scored)
    print("=" * 78)
    print(f"飞盘 RAG · 检索层验收（A 口径）")
    print(f"  题库 {len(rows)} 条（计分 {n} 条，离题 {len(rows) - n} 条）"
          f" · {'线上流程（改写+RRF）' if with_rewrite else '纯检索基线'}")
    print("=" * 78)
    print(f"  {'Hit@1':<8}{total[1] / n * 100:6.1f}%  ({total[1]}/{n})")
    print(f"  {'Hit@3':<8}{total[3] / n * 100:6.1f}%  ({total[3]}/{n})")
    print(f"  {'Hit@5':<8}{total[5] / n * 100:6.1f}%  ({total[5]}/{n})")
    print(f"  {'Hit@8':<8}{total[8] / n * 100:6.1f}%  ({total[8]}/{n})   ← 线上 TOP_K 硬线")

    print("\n--- 按难度档 ---")
    for tier in ("T1", "T2", "T3", "T4", "T5"):
        sub = [x for x in scored if x["tier"] == tier]
        if not sub:
            continue
        s = summarize(sub)
        label = {"T1": "术语直问", "T2": "书面转述", "T3": "口语转述",
                 "T4": "场景描述", "T5": "口语隐含"}[tier]
        print(f"  {tier} {label:<8} n={len(sub):<3} "
              f"Hit@1 {s[1] / len(sub) * 100:5.1f}%  "
              f"Hit@3 {s[3] / len(sub) * 100:5.1f}%  "
              f"Hit@5 {s[5] / len(sub) * 100:5.1f}%  "
              f"Hit@8 {s[8] / len(sub) * 100:5.1f}%")

    print("\n--- 按章节 ---")
    chapters = sorted({x["ch"] for x in scored}, key=lambda c: (c == "-", c))
    for ch in chapters:
        sub = [x for x in scored if x["ch"] == ch]
        s = summarize(sub)
        print(f"  第{ch:<3}章 n={len(sub):<3} Hit@8 {s[8] / len(sub) * 100:5.1f}%")

    # ---- 离题 ----
    # ⚠️ 2026-10-05 重新定义了这里的指标。原先量的是"**闸门有没有拦**"，
    #    而闸门判据已从「命中规则类目」改成「命中类目 **或** 命中域内基础词」
    #    （见 app/routes.py::DOMAIN_WORDS）。含"飞盘"字样的离题题
    #    （"飞盘多少钱一个"）现在**会被放行** —— 这是**有意的**：
    #      · 误拦的代价 = 用户问真规则问题，我们答"没有规定" → 实质答错
    #      · 漏放的代价 = 白调一次大模型，铁律下模型说"规则里没有" → 无害
    #    所以正确的问题不是"拦没拦"，而是"**不含任何飞盘词的问题拦没拦**"
    #    和"**被放行的离题题会不会被生成层兜住**"。后者要真调模型，
    #    由 tools/answer_eval.py 和 tools/verify_remote.py 负责。
    off = [x for x in rows if x["tier"] == "T6"]
    if off:
        def _has_domain(x):
            return bool(x.get("domain_word"))
        must_block = [x for x in off if not _has_domain(x)]
        allowed = [x for x in off if _has_domain(x)]
        blocked = sum(1 for x in must_block if not x["confident"])
        print("\n--- 离题题（两档，判据=是否含飞盘域内词）---")
        print(f"  ① 不含任何域内词 → 必须拦：{blocked}/{len(must_block)} 拦住")
        for x in must_block:
            flag = "拦住" if not x["confident"] else "⚠️漏放"
            print(f"    [{flag}] cos={x['best_cos']:.3f} routes={x['routes']} 「{x['q']}」")
        if allowed:
            print(f"  ② 含域内词（如「飞盘」）→ **按设计放行**给生成层复核：{len(allowed)} 条")
            for x in allowed:
                print(f"    [放行·设计如此] 命中词={x.get('domain_word')} 「{x['q']}」")

    # ---- 未命中 ----
    miss = [x for x in scored if rank_of(x["expect"], x["ranked"]) is None
            or rank_of(x["expect"], x["ranked"]) > 8]
    if miss:
        print(f"\n--- 未进 TOP8（共 {len(miss)} 条，列前 20）---")
        for x in miss[:20]:
            top3 = ", ".join(x["ranked"][:3])
            print(f"  [{x['tier']}] 「{x['q'][:34]}」")
            print(f"        期望 {x['expect']}  实得 {top3}")
            if x["rw"]:
                print(f"        改写: {x['rw']}")

    if show_detail:
        print("\n--- 逐题名次 ---")
        for x in rows:
            if x["tier"] == "T6":
                continue
            rk = rank_of(x["expect"], x["ranked"])
            mark = "OK " if rk and rk <= 8 else "MISS"
            print(f"  {mark} #{rk if rk else '-':<4} {x['q'][:40]}")

    print(f"\n总耗时 {elapsed:.1f}s（{elapsed / len(rows) * 1000:.0f} ms/题）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
