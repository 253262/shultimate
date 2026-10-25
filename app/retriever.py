# -*- coding: utf-8 -*-
"""混合检索：**语义为主，字词法为辅**，末尾带一道相关性闸门。

为什么这么设计（重要，改动前先读）：
  上一版（小程序 rulesAsk）翻车的原因，是把意图词表的权重给到了 0.40，
  直接盖过了向量本身 —— 消融实验显示，关掉词表加权后纯语义只有 34.5% 命中，
  换句话说那 99% 的高分是"老词表"挣的，不是"理解"挣的。
  本版把关系倒过来：语义是主判据，字词法只补两种语义天生吃亏的情况：
    ① 用户直接报条号（"18.1.1.5 是什么"）
    ② 专有名词 / 英文别名（"Double Team" "brick"）
  并且**闸门判据是"有没有触发规则类目线索"**（routes / 强制族 / 术语别名），
  不是余弦阈值——实测余弦在域内外完全重叠（域内最低 0.344 vs 离题最低 0.350），
  拿它当闸门等于没闸门。这样词法加成也抬不动离题问题进生成层。

零第三方依赖：全量点积用纯 Python，四百多条语料约 70ms。
"""
from __future__ import annotations

import array
import json
import re
import sys
import time
from operator import mul

from . import appendix as appendix_mod, config, embedder, routes as routes_mod
from . import competition as competition_mod

# 用户可能直接报的条号，如 18.1.1.5 / 第 17.6 条
CLAUSE_IN_QUERY = re.compile(r"(\d{1,2}(?:\.\d{1,2}){1,4})")


def _strip_punct(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def _bigrams(text: str) -> set[str]:
    t = _strip_punct(text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def _dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def rrf_order(cos_lists: list[list[float]]) -> tuple[list[int], list[float]]:
    """多路余弦 → RRF（Reciprocal Rank Fusion）融合。

    **只用名次，不用分数**：改写路的余弦整体偏高（查询被摊宽），
    两路的分数不可比，直接加权会让改写路无脑压过原问题路。
    名次是可比的，这正是 RRF 的用武之地。

    返回 (按下标降序排好的下标列表, 每个下标的融合分)。
    """
    depth = config.FUSE_DEPTH
    m = len(cos_lists[0]) if cos_lists else 0
    scores = [0.0] * m
    for cos in cos_lists:
        order = sorted(range(m), key=lambda i: -cos[i])[:depth]
        for pos, idx in enumerate(order, 1):
            scores[idx] += 1.0 / (config.RRF_K + pos)
    return sorted(range(m), key=lambda i: -scores[i]), scores


class Retriever:
    """载入索引，提供 search()。索引缺失时构造即抛错，由调用方决定降级。"""

    def __init__(self) -> None:
        meta_p = config.DATA_DIR / "index.json"
        bin_p = config.DATA_DIR / "embeddings.bin"
        if not meta_p.exists() or not bin_p.exists():
            raise FileNotFoundError(
                "索引不存在，请先运行：\n"
                "  python scripts/build_corpus.py\n"
                "  python scripts/build_index.py")

        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        self.items: list[dict] = meta["items"]
        self.dims: int = int(meta["dims"])

        if sys.byteorder != "little":
            raise RuntimeError("embeddings.bin 为小端序，当前平台字节序不匹配")
        flat = array.array("f")
        flat.frombytes(bin_p.read_bytes())
        n = len(self.items)
        if len(flat) != n * self.dims:
            raise ValueError(f"索引与语料不一致：向量 {len(flat)} 个 float vs 语料 {n} 条 x {self.dims} 维")
        # 切成 list of list：后续点积用 sum(map(mul, ...)) 最快
        self.matrix: list[list[float]] = [
            flat[i * self.dims:(i + 1) * self.dims].tolist() for i in range(n)
        ]

        # 预计算，避免每次检索重算
        self._doc_bg = [_bigrams(it.get("chapter_title", "") + it.get("text", "")) for it in self.items]
        self._id2idx = {it["id"]: i for i, it in enumerate(self.items)}
        self._terms = {it["term"]: it["id"] for it in self.items
                       if it.get("type") == "definition" and it.get("term")}
        self._aliases = {it["alias"].lower(): it["id"] for it in self.items if it.get("alias")}

        # 「引子型父条」及其子条（见 config.PARENT_CHILD_MAX 的说明）。
        # 判据刻意做得**可数、无歧义**：正文以「：」结尾 **且**语料里确有子条。
        # 语料里 52 条满足，平均 3.5 个子条 —— 不存在"边界模糊"的中间态。
        self._children: dict[str, list[int]] = {}
        for i, it in enumerate(self.items):
            p = it.get("parent")
            if p:
                self._children.setdefault(p, []).append(i)
        self._leadin: set[int] = {
            i for i, it in enumerate(self.items)
            if it.get("type") == "rule"
            and (it.get("text") or "").strip().endswith(("：", ":"))
            and self._children.get(it["id"])
        }

    # ---------- 对外 ----------
    def _cos(self, text: str) -> list[float]:
        """一条查询文本 -> 与全部语块的余弦。"""
        qv = embedder.embed_query(text)
        return [sum(map(mul, qv, v)) for v in self.matrix]

    def score_from_cos(self, query: str, cos: list[float]) -> list[tuple[str, float]]:
        """给定**已算好的余弦向量**，输出 (id, score) 降序 —— 与线上打分同源。

        评测要走批量 embedding（cosine_matrix 一次请求打一捆），所以把
        "算余弦" 与 "打分排序" 拆开：score_query() 是给单条用的便捷包装。
        """
        query = (query or "").strip()
        q_bg = _bigrams(query)
        hinted = set(CLAUSE_IN_QUERY.findall(query))
        q_low = query.lower()
        term_hits = {tid for term, tid in self._terms.items() if term in query}
        term_hits |= {tid for al, tid in self._aliases.items() if al and al in q_low}

        available = set(self._id2idx)
        forced, forced_aux, routes = routes_mod.forced_split(query, available)
        alias_ids = routes_mod.alias_hits(query) & available

        cos_max = max(cos) or 1.0
        scored: list[tuple[float, str]] = []
        for i, it in enumerate(self.items):
            s = cos[i] / cos_max
            cid = it["id"]
            if cid in forced:
                s += config.ROUTE_BONUS * forced[cid]
            elif cid in forced_aux:
                s += config.ROUTE_BONUS_AUX * forced_aux[cid]
            if cid in alias_ids:
                s += config.ROUTE_BONUS
            if hinted and cid in hinted:
                s += config.CLAUSE_HINT_BONUS
            s += config.BIGRAM_WEIGHT * _dice(q_bg, self._doc_bg[i])
            if cid in term_hits:
                s += config.TERM_HIT_BONUS
            scored.append((s, cid))
        scored.sort(key=lambda x: -x[0])
        return scored

    def score_query(self, query: str) -> list[tuple[str, float]]:
        """只做排序、不构造 hits —— 供**评测**批量调用，保证与线上打分**逐字一致**。

        为什么必须抽出来：app/evaluate.py 原来自己写了一遍 `sorted(cos)`，
        等于在测**裸向量**，而不是线上那条"向量 + 类目必检 + 别名 + 二元组"的
        完整链路。结果 447 条 FAQ 的分数长期失真（一路 72.3% 其实是不含路由的
        基线分），拿它做回归判断会得出完全错误的结论（2026-10-03 发现）。
        """
        return self.score_from_cos(query, self._cos(query or ""))

    def search(self, query: str, top_k: int | None = None, extra: str = "",
               subqueries: list[str] | None = None) -> dict:
        """混合检索。

        extra       ：改写出的规则术语串（拼进查询向量，跨"口语↔书面"鸿沟）
        subqueries  ：问题分解出的子问题（各自独立检索后取较优）
                      长场景题会把向量摊平、真问题被细节稀释，分解后再检索能救回来。

        排序策略（2026-10-03 重做）：
          余弦取多路**最大值**（每种问法都给它一次机会），
          而不是 RRF 名次融合——实测 RRF 在 62 题验收集上是负收益
          （Hit@8 68.5% vs 基线 70.4%），因为它让"两路都还行"的文档
          压过"只有一路很强"的文档，而后者往往才是正解。
          再用「类目必检」把该情形的条款族强制拉进候选（见 app/routes.py）。
        """
        t0 = time.perf_counter()
        top_k = top_k or config.TOP_K
        query = (query or "").strip()

        # ---- 多路语义召回 ----
        # ① 原问题（精确性最好）
        # ② 原问题 + 改写术语（跨字面鸿沟）
        # ③…n 分解出的子问题（长场景题专用，避免被细节稀释）
        paths: list[list[float]] = [self._cos(query)]
        if extra and extra.strip():
            paths.append(self._cos(f"{query} {extra}".strip()))
        for sq in (subqueries or []):
            sq = (sq or "").strip()
            if sq and sq != query:
                paths.append(self._cos(sq))

        # 取各条"最像程度"的最大值：一种问法命中就算命中
        cos = [max(pair) for pair in zip(*paths)]

        # ---- 条号线索只认用户原问题 ----
        # 实测改写模型会凭空编条号（"场外有人挡住我" → 它写 18.1.1.4），
        # 一旦给强加分，幻觉条号会被顶到第一位，比不检索还糟。
        q_bg = _bigrams(query)
        hinted = set(CLAUSE_IN_QUERY.findall(query))
        q_low = query.lower()
        term_hits = {tid for term, tid in self._terms.items() if term in query}
        term_hits |= {tid for al, tid in self._aliases.items() if al and al in q_low}

        # ---- 类目必检：用字面线索锁定"这题属于哪类情形"，强制注入条款族 ----
        # 余弦在领域语料上区分度不足（前 5% 挤在 0.07 宽的带子里），
        # 靠排名保证不了召回；规则负责保底，余弦负责排序。
        available = set(self._id2idx)
        forced, forced_aux, routes = routes_mod.forced_split(query, available)
        # 术语别名：球员说的词和术语表里的官方名对不上（"轴心点" vs "转身/轴心"），
        # 用别名字典把定义条目捞回来。
        alias_ids = routes_mod.alias_hits(query) & available

        # 【已否决 · 勿重开】两条"让族内排序服余弦"的路线都实测为负收益：
        #   ① 族内余弦名次**指数衰减** ROUTE_BONUS_*：Hit@8 94.4% → 88.9%
        #   ② 族内余弦名次**小增量**（ROUTE_TIEBREAK 0.02 / 0.05）：
        #      447 条集**一点不涨**（72.3% → 72.3%），62 条集反掉 3.7pp
        #      （100% → 96.3%）。
        # 两次都指向同一个道理：族内"谁排前面"看起来像噪声，但把它掰正
        # 并没有让**正解进前 8**——因为那 124 条未命中的正解根本不在被触发
        # 的那个族里（"砖点是什么"→2.5 属于"场地"族，却只触发了"开盘"族）。
        # 真正的杠杆是**补线索词 / 拆族**，不是调族内权重。
        scored: list[tuple[float, int, float]] = []
        cos_max = max(cos) or 1.0
        for i, it in enumerate(self.items):
            c = cos[i] / cos_max                    # 归一化，与加成同尺度
            s = c
            cid = it["id"]
            if cid in forced:
                s += config.ROUTE_BONUS * forced[cid]          # 主条款：这题就该看它
            elif cid in forced_aux:
                s += config.ROUTE_BONUS_AUX * forced_aux[cid]   # 辅条款：相关但非正解
            if cid in alias_ids:
                s += config.ROUTE_BONUS             # 术语别名命中，同上
            if hinted and cid in hinted:
                s += config.CLAUSE_HINT_BONUS       # 用户点名要这条
            s += config.BIGRAM_WEIGHT * _dice(q_bg, self._doc_bg[i])
            if cid in term_hits:
                s += config.TERM_HIT_BONUS
            scored.append((s, i, cos[i]))

        scored.sort(key=lambda x: -x[0])
        best_cos = max(cos) if cos else 0.0
        hinted_exact = bool(hinted and any(it["id"] in hinted for it in self.items))

        # ---- 相关性闸门（2026-10-05 第三次修订：补域内词表） ----
        # 实测余弦**没有区分度**：域内最低 0.344，离题最低 0.350 —— 完全重叠
        # （"飞盘运动是哪一年发明的"能拿 0.510，比一堆真问题的下限还高）。
        # 所以判据是"有没有任何**规则类目线索**被触发"，而不是余弦阈值。
        #
        # ⚠️ 但只用"族线索"有一个**结构性缺陷**：族是为"需要判断的争议场景"
        #    建的（犯规/违例/争抢/读秒…），**368 条规则里 190 条（52%）
        #    不属于任何族** —— 场地(第2章)、装备(第3章)、队伍(第5章)、
        #    比赛开始(第6章) 这些**事实性章节整章无族**。
        #    后果：这些问题的正解**排第 1 也照样被拦**。
        #       · 「边线和端线是什么」 → 2.2 排第 1 → 拦截
        #       · 「什么衣服不能穿」  → 3.4 排第 1 → 拦截
        #    154 个评测主题里 24 个（16%）的答案键全部无族。
        #
        # ⇒ 判据 = 「命中族」**或**「命中域内基础词」(routes_mod.domain_hit)。
        #   闸门的职责是挡住**和飞盘无关**的问题（价格/品牌/历史/天气/编程），
        #   不是"只放行有争议类目的问题"。
        #   两条误触发（"飞盘场地有多大"→场地域、"推荐个飞盘牌子"→装备域）
        #   由生成层按"依据充分性"兜住：候选是"尺寸见图 1"这类，
        #   与该问题不相干，模型能看出依据不足并明说。
        # ⚠️ domain_hit **不参与打分**，只决定放行 —— 见 app/routes.py 的说明。
        domain_word = routes_mod.domain_hit(query)
        structured = bool(forced or forced_aux or alias_ids or routes)
        confident = structured or hinted_exact or bool(domain_word)

        hits = []
        for s, i, c in scored[:top_k]:
            it = dict(self.items[i])
            it["_score"] = round(float(s), 5)
            it["_cos"] = round(float(c), 4)
            parent = it.get("parent")
            if parent and parent in self._id2idx:
                p = self.items[self._id2idx[parent]]
                it["_parent_text"] = p.get("text")
                it["_parent_id"] = p.get("id")
            hits.append(it)

        # 引子型父条 ⇒ 把子条一并带上（只加上下文，不改排序）
        hits = self._attach_leadin_children(hits)

        # 附录注解 ⇒ 正文把答案外包给图 / 附录时，把官方文档里的事实一并递给模型。
        # ⚠️ 只**加内容**：不动 _score、不动排序、不新增语块（打分与族见红线 8 / 9）。
        #    来源见 app/appendix.py —— 每条都标了出处，且明确写"非正文条款"，
        #    不伪装成条款号（理由见该模块的"来源纪律"）。
        hits = appendix_mod.attach(hits)

        # ⭕「圈外的点」：赛事设置（时长 / 目标分 / 裁判 / 观察员…）。
        # ⚠️ **刻意不混进 hits**（owner 2026-10-13 定的架构）：
        #    · 它**不是规则条款**，混进候选会让模型把赛事规定当规则引用
        #    · 它**不参与打分/排序/闸门** —— 走独立的词面匹配（app/competition.py）
        #    · 回答时**分区呈现**，让用户一眼看出哪句是规则、哪句是这场比赛的规程
        # ⚠️ 文件不存在时返回空 list，整个功能静默降级（它是可选增强）。
        try:
            comp_hits = competition_mod.search(query)
        except Exception:
            comp_hits = []

        return {
            "query": query,
            "rewritten": extra or None,
            "routes": routes,
            "forced_count": len(forced) + len(forced_aux),
            "paths": len(paths),
            "mode": "hybrid",
            "confident": confident,
            "structured": structured,
            "domain_word": domain_word,
            "best_cos": round(best_cos, 4),
            # ⚠️ 原来这里回传的是 config.MIN_SCORE，但 MIN_SCORE **从未参与判定**
            # （真实闸门就是上面那三个 structured/hinted_exact/domain_word）。
            # 这个字段名会让前端和 owner 以为"低于它就拒答"，据此做错误推论。
            # 保留字段是为了不破坏前端契约，但值改成**真实判据的说明**。
            "threshold": None,
            "threshold_note": "判据不是分数阈值，是「类目/线索词/域内词」"
                              "（structured / hinted_exact / domain_word）；"
                              "余弦在本语料上域内与离题完全重叠，不可当阈值用。",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "hits": hits,
            "competition": comp_hits,
            "nearby": [{"id": h["id"], "chapter_title": h.get("chapter_title", ""),
                        "text": h.get("text", "")[:60]} for h in hits[:3]],
        }

    def _attach_leadin_children(self, hits: list[dict]) -> list[dict]:
        """给命中的「引子型父条」补上它的子条（**只加上下文，不动排序**）。

        **为什么必须有这一步**（2026-10-06 从 7 条"引错条款"里挖出来的）
        语料里有 52 条规则正文只有一句引子、内容全在子条：
            9.6「所谓"从最大 n 恢复读秒"…，含义如下：」
            8.1「以下情况比赛处于"死盘"状态，此时不可能发生攻防转换：」
            19.2「技术暂停（Technical Stoppage）：」
        而 `build_context()` 只补**父条**（`_parent_text`）、从不补**子条**，
        所以命中 9.6 时模型只看到「含义如下：」，只能答"这条被截断了 / 规则没规定"。

        实测（`tools/exp_parent_child.py`，零 API）：
          · 447 条真实问法里 **278 条（62%）** 的 top8 含这种"空壳父条"；
          · 其中 **76 条（17%）的金标就是这个引子父条** ⇒ 答案正文压根没进上下文；
          · 按严格口径（金标直接写在子条里）也有 7 条。
        7 条「引错条款」里的 19.2 就是它（问"有伤口能继续打吗"，金标 19.2.1）。

        ⚠️ 与"族线索词"完全不同：这里不碰 `forced_split`、不碰加分、不碰闸门，
           因此**没有抢位风险**——它只是把本该一起读的父条+子条还给模型。
        """
        if not hits:
            return hits
        have = {h["id"] for h in hits}
        out: list[dict] = []
        added = 0
        for h in hits:
            out.append(h)                      # 原文命中先落位（顺序不动）
            # ⚠️ 补满是"停止补充"，**不是** `return` —— 早期版本一 return 就把
            #    还没遍历到的原文命中整条丢掉（cap=0 时只剩 1 条命中）。
            #    这是纯静默失败：不报错、只是上下文悄悄变少。
            if added >= config.PARENT_CHILD_MAX:
                continue
            if self._id2idx.get(h["id"]) not in self._leadin:
                continue
            for ci in self._children.get(h["id"], []):
                if added >= config.PARENT_CHILD_MAX:
                    break
                child_id = self.items[ci]["id"]
                if child_id in have:          # 已在 top8 里就不重复
                    continue
                have.add(child_id)
                child = dict(self.items[ci])
                child["_score"] = h.get("_score")
                child["_cos"] = h.get("_cos")
                child["_via_parent"] = h["id"]   # 标记来源，便于排查
                out.append(child)                # 紧跟父条之后
                added += 1
        return out

    def glossary(self) -> list[dict]:        return [{"term": it["term"], "id": it["id"], "alias": it.get("alias"),
                 "text": it["text"]}
                for it in self.items if it.get("type") == "definition"]

    def stats(self) -> dict:
        from collections import Counter
        kinds = Counter(it.get("type") for it in self.items)
        chapters = Counter(it.get("chapter") for it in self.items if it.get("type") == "rule")
        # 条数口径（2026-10-04 核正）：rule 语块数与官方唯一条号数**同为 368**。
        # 曾误记为 358，是提取条号时正则只允许 4 段编号（`\d{1,2}(?:\.\d{1,2}){1,3}`）
        # 把 10 条 5 段条款（如 `18.1.1.1.1`）截断成父条款名造成的假重复。
        # 索引里 `id` 本就唯一，故 rules 即唯一条款数，无需另算。
        return {
            "chunks": len(self.items),
            "rules": kinds.get("rule", 0),
            "definitions": kinds.get("definition", 0),
            "preface": kinds.get("preface", 0),
            # 附录层（2026-10-18）：**单独计数，绝不并进 rules** ——
            # 并进去会让"368 条正文"这个所有覆盖率指标的分母悄悄变掉。
            "appendix": kinds.get("appendix", 0),
            "chapters": len(chapters),
            "dims": self.dims,
            "embed_model": config.EMBED_MODEL,
            "source": config.SOURCE_NAME,
        }
