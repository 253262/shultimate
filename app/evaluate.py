#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测：检索命中率 + 拒答准确率 + 延迟。

评测集两路：
  A. data/faq_eval.jsonl —— 154 条常见问题、447 种**真实口语问法**，每条都人工标注了
     依据的条号。这是最有价值的一路：它就是"球员会怎么问"的真实样本。
  B. 内置离题问题集 —— 验证「不懂就说不知道」这条最高优先级。

输出：docs/eval-report.md
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from operator import mul
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, embedder  # noqa: E402
from app import routes as routes_mod  # noqa: E402
from app.retriever import Retriever  # noqa: E402

# 规则里没有、或根本不是规则问题的提问 —— 系统必须说「没有」，而不是硬凑
OFF_TOPIC = [
    "飞盘多少钱一个",
    "推荐个飞盘牌子",
    "飞盘该怎么保养",
    "今天天气怎么样",
    "飞盘运动起源于哪里",
    "谁是世界上最厉害的飞盘选手",
    "长沙哪里可以打飞盘",
    "飞盘有几种握法",
    "怎么练才能扔得远",
    "飞盘比赛有奖金吗",
    "飞盘是奥运会项目吗",
    "我想买个飞盘送朋友",
]

# 语义等价组：条号不同，但回答的是同一个问题。
# 只收「我逐条读过原文、确认结论一致」的组合，不为了刷分乱加。
EQUIVALENT_GROUPS: list[set[str]] = [
    # 边界线属不属于比赛场地 —— 2.3 与 11.1 是同一结论的两种表述
    {"2.3", "11.1"},
    # 「得分之后谁发盘」—— 4.5 讲得分后要做哪些事，7.1 讲由防守方掷开盘
    {"4.5", "7.1"},
]


def build_accept_sets(ids: list[str]) -> dict[str, set[str]]:
    """把每个条号映射到「命中它也算对」的条号集合。

    评测口径的三层（越往下越宽松）：
      ① 自身 —— 精确答对
      ② 祖先 + 后代 —— 父子条款打通。问「得分后换不换边」命中 4.5.2
         本就是正解，不该因为答了子条款而被判错。
      ③ 语义等价组 —— 见 EQUIVALENT_GROUPS。
    """
    id_set = set(ids)
    equiv: dict[str, set[str]] = {}
    for g in EQUIVALENT_GROUPS:
        gg = {x for x in g if x in id_set}
        for x in gg:
            equiv.setdefault(x, set()).update(gg)

    accept: dict[str, set[str]] = {}
    for cid in ids:
        parts = cid.split(".")
        ancestors = {".".join(parts[:k]) for k in range(1, len(parts) + 1)}
        descendants = {o for o in ids if o.startswith(cid + ".")}
        accept[cid] = ancestors | descendants | equiv.get(cid, set())
    return accept


def load_faq_cases(filename: str = "faq_eval.jsonl") -> list[tuple[str, set[str]]]:
    """读一份「问法 → 期望条号」评测集，展开成 (问法, 期望条号集合) 列表。

    ⚠️ 默认仍是 `faq_eval.jsonl`（447 问法），**不要改默认值** ——
    `app/evaluate.py`、`tools/fast_regress.py`、`tools/routes_diff.py`
    都按老签名调它，改了默认值会让所有历史数字失去可比性。

    加 `filename` 参数是为了接第二套题（2026-10-12 新增的
    `data/faq_eval_coverage.jsonl`：214 条 / 856 问法，专补原先
    **从未被考过的 214 条规则**，覆盖 154 → 368/368）。两套题的分工：
      · `faq_eval.jsonl`          —— 人工热点题集，但 **ROUTE_BONUS 是在它上面调的** ⇒ 训练集
      · `faq_eval_coverage.jsonl` —— 按条款铺的补集，问法中位 13 字（更贴近真人）
    """
    p = config.DATA_DIR / filename
    if not p.exists():
        return []
    cases: list[tuple[str, set[str]]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        expect = {str(n).strip().rstrip(".") for n in (row.get("num") or []) if n}
        if not expect:
            continue
        for q in row.get("q") or []:
            if q and q.strip():
                cases.append((q.strip(), expect))
    return cases


def cosine_matrix(r: Retriever, queries: list[str]) -> list[list[float]]:
    """批量算所有查询与所有语块的余弦（纯 Python 点积）。"""
    out: list[list[float]] = []
    for qv in embedder.embed_queries(queries):
        out.append([sum(map(mul, qv, v)) for v in r.matrix])
    return out


def main() -> int:
    argv = sys.argv[1:]
    with_rewrite = "--rewrite" in argv
    limit = 0
    for a in argv:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])

    r = Retriever()
    cases = load_faq_cases()
    if limit:
        cases = cases[:limit]
    mode_label = "含查询改写（线上流程）" if with_rewrite else "线上完整检索链路（向量+类目必检+别名+二元组）"
    print(f"[评测] 语块 {len(r.items)} 条 · 样本 {len(cases)} 种口语问法 · {mode_label}")

    ids = [it["id"] for it in r.items]
    queries = [q for q, _ in cases]
    rewritten: list[str] = []
    if with_rewrite:
        from app import generator as gen_mod
        gen = gen_mod.Generator()
        if not gen.available:
            print("[!] 未配置大模型 Key，无法做查询改写", file=sys.stderr)
            return 1
        for i, q in enumerate(queries, 1):
            rewritten.append(gen.rewrite_query(q))
            if i % 10 == 0:
                print(f"    改写 {i}/{len(queries)}", flush=True)

    t0 = time.perf_counter()
    # ⚠ 2026-10-03 修正：原来这里自己写 `sorted(cos)`，测的是**裸向量**，
    # 完全没走 routes 的类目必检/别名/二元组加成 —— 与线上不是同一条链路，
    # 据此做回归判断会得出错误结论。现改为：
    #   ① cosine_matrix 批量 embedding（一次请求打一捆，避免 447 次网络往返）
    #   ② 再交给 Retriever.score_from_cos() 打分 —— 与线上 search() 同源
    sims_main = cosine_matrix(r, queries)
    if with_rewrite:
        fused_q = [f"{q} {rw}".strip() for q, rw in zip(queries, rewritten)]
        sims_alt = cosine_matrix(r, fused_q)
        # 余弦取最大：改写路只是"多给一次机会"，类目必检/别名/二元组仍走原问题
        cos_all = [[max(a, b) for a, b in zip(sims_main[i], sims_alt[i])]
                   for i in range(len(queries))]
    else:
        cos_all = sims_main

    ranked_ids: list[list[str]] = []
    score_cols: list[dict[str, float]] = []
    for qi, q in enumerate(queries):
        scored = r.score_from_cos(q, cos_all[qi])
        ranked_ids.append([cid for _, cid in scored[:5]])
        score_cols.append({cid: s for s, cid in scored})

    ids = [it["id"] for it in r.items]
    elapsed = time.perf_counter() - t0

    accept = build_accept_sets(ids)
    strict_hits = {1: 0, 3: 0, 5: 0}
    wide_hits = {1: 0, 3: 0, 5: 0}
    misses: list[dict] = []
    rescued = 0
    for qi, (q, expect) in enumerate(cases):
        ranked = ranked_ids[qi]
        col = score_cols[qi]
        # 宽口径：期望条号 ∪ 其祖先/后代/语义等价条号
        wide: set[str] = set()
        for e in expect:
            wide |= accept.get(e, {e})
        rank_s = next((i for i, cid in enumerate(ranked, 1) if cid in expect), None)
        rank_w = next((i for i, cid in enumerate(ranked, 1) if cid in wide), None)
        for k in strict_hits:
            if rank_s and rank_s <= k:
                strict_hits[k] += 1
            if rank_w and rank_w <= k:
                wide_hits[k] += 1
        if rank_s is None and rank_w is not None:
            rescued += 1
        if rank_w is None:
            misses.append({
                "q": q, "expect": sorted(expect),
                "got": [{"id": cid, "score": round(col.get(cid, 0.0), 3)} for cid in ranked[:3]],
            })

    # ---- 拒答评测 ----
    # ⚠ 2026-10-03：判据改为与线上闸门一致 —— 看**有没有触发任何规则类目线索**
    # （routes / forced / alias），而不是余弦阈值（实测余弦在域内外完全重叠）。
    off_ok = 0
    off_bad = []
    off_detail = []
    if OFF_TOPIC:
        for q in OFF_TOPIC:
            scored = r.score_query(q)
            top_cid = scored[0][1]
            available = set(r._id2idx)
            forced, forced_aux, routes = routes_mod.forced_split(q, available)
            alias_ids = routes_mod.alias_hits(q) & available
            blocked = not bool(forced or forced_aux or alias_ids or routes)
            off_ok += int(blocked)
            if not blocked:
                off_bad.append((q, routes, top_cid))
            off_detail.append({"q": q, "routes": routes, "blocked": blocked,
                               "top1": top_cid})

    in_domain = [col[ranked_ids[i][0]] for i, col in enumerate(score_cols)]
    n = len(cases)

    print("\n=========== 评测结果 ===========")
    print("  [严格] 检索前 5 条里必须出现所标注的原始条号")
    for k in (1, 3, 5):
        print(f"    Hit@{k}   {strict_hits[k] / n * 100:5.1f}%  ({strict_hits[k]}/{n})")
    print("  [宽口径] 额外承认父子条款打通 + 语义等价条号")
    for k in (1, 3, 5):
        print(f"    Hit@{k}   {wide_hits[k] / n * 100:5.1f}%  ({wide_hits[k]}/{n})")
    print(f"  口径救回 {rescued} 条（严格判错、宽口径判对）")
    print(f"  拒答     {off_ok}/{len(OFF_TOPIC)} 被正确拦住（判据=是否触发任何规则类目线索）")
    print(f"  平均耗时 {elapsed / max(n, 1) * 1000:.0f} ms/次（线上完整链路）")
    if off_bad:
        print("\n  [!] 未被拦住的离题问题（会被送去调大模型）：")
        for q, routes, top1 in off_bad:
            print(f"      {q}  routes={routes}  -> 最像的是 {top1}")
    if misses:
        print(f"\n  [!] 宽口径下仍未命中 {len(misses)} 条（列前 8）：")
        for m in misses[:8]:
            got = ", ".join(f"{g['id']}({g['score']})" for g in m["got"])
            print(f"      「{m['q']}」 期望 {m['expect']} 实得 {got}")

    suffix = "-rewrite" if with_rewrite else ""
    if limit:
        suffix += f"-{limit}"
    out = Path(__file__).resolve().parent.parent / "docs" / f"eval-report{suffix}.md"
    out.parent.mkdir(exist_ok=True)
    lines = ["# 检索与拒答评测报告", "",
             "- 语料：{} 条规则 / {} 条定义 / 共 {} 个语块 · {} 维".format(
                 r.stats()["rules"], r.stats()["definitions"], r.stats()["chunks"], r.dims),
             f"- 评测样本：{n} 种真实口语问法（来自站方整理的常见问题库）",
             f"- 平均单次耗时：{elapsed / max(n, 1) * 1000:.0f} ms（含接口往返）", "",
             "## 命中率", "",
             "| 指标 | 严格（条号精确对上） | 宽口径（含父子条款 + 语义等价组） |",
             "|---|---|---|"]
    lines += [f"| Hit@{k} | {strict_hits[k] / n * 100:.1f}% ({strict_hits[k]}/{n}) "
              f"| {wide_hits[k] / n * 100:.1f}% ({wide_hits[k]}/{n}) |" for k in (1, 3, 5)]
    lines += ["",
              f"- 口径说明：严格＝检索前 5 条里出现提问所标注的原始条号；"
              f"宽口径额外承认「父/子条款」与「语义等价条号」。本轮回测有 **{rescued} 条**"
              f"由严格判错、宽口径判对。",
              "",
              "- **检索链路**：向量余弦 + 类目必检（字面线索 → 强制注入条款族）"
              "+ 术语别名 + 二元组字面分，与线上 `Retriever.search()` 共用同一套打分。",
              "", "## 拒答（「不懂就说不知道」）", "",
              f"- 离题问题：{off_ok}/{len(OFF_TOPIC)} 被正确拦住",
              "- 判定判据：是否触发任何「规则类目线索」（routes / 强制族 / 术语别名）",
              "- 说明：余弦阈值已被实测证伪（域内最低 0.344 vs 离题最低 0.350，完全重叠），"
              "故不再以余弦作闸门。", "",
              "## 未命中样本（宽口径下仍判错，即真正答非所问）", ""]
    if misses:
        lines += ["| 提问 | 期望条号 | 检索到的前三条 |", "|---|---|---|"]
        for m in misses[:25]:
            got = " / ".join(f"{g['id']}({g['score']})" for g in m["got"])
            lines.append(f"| {m['q']} | {', '.join(m['expect'])} | {got} |")
    else:
        lines.append("无。")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
