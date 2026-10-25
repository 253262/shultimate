# -*- coding: utf-8 -*-
"""「引子型父条 → 带上子条」的守卫。

⚠️ 存在的理由（2026-10-06 挖出来的）
语料里有 52 条规则正文只有一句引子、内容全在子条，例如
    9.6「所谓"从最大 n 恢复读秒"…，含义如下：」
    8.1「以下情况比赛处于"死盘"状态，此时不可能发生攻防转换：」
而生成层 `build_context()` 只补**父条**、从不补**子条** ⇒ 命中 9.6 时模型
只看到「含义如下：」，只能答"这条被截断了 / 规则没规定"。
实测 447 条真实问法里 62% 的 top8 含这种"空壳父条"；端到端 154 题里
45 题（29%）的金标条款本身就是引子父条，其中 22 题这次才第一次把正文补进上下文。

守卫分两类：
  ① 行为级：直接调 `_attach_leadin_children()`（**不调 search()，零 API**）
  ② 源码级：钉死"这个能力被接到 search() 上"且上限走 config，不是写死的数字

⚠️ 其中 `test_original_hits_are_never_dropped` 守的是一个**真实踩过的 bug**：
   早期实现写成 `if added >= MAX: return out`，于是补满上限时**后面还没遍历到的
   原文命中被整条丢掉**（cap=0 时只剩 1 条命中）。它不报错、只是上下文悄悄变少。
   本文件里连"故意写坏"的版本一起测，用来证明这条守卫真的会红。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config                        # noqa: E402
from app.retriever import Retriever           # noqa: E402

RETRIEVER_PY = ROOT / "app" / "retriever.py"
RUN_PAID_TESTS = os.environ.get("RUN_PAID_TESTS") == "1"


def strip_py_comments(s: str) -> str:
    """去掉 Python 注释与文档字符串。

    ⚠️ 必须先剥再断言：注释里提到某个写法（比如"不要用 return 提前退出"）
       会让源码级守卫误判。剥的顺序也重要 —— 先三引号、再行注释。
    """
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s.startswith('"""', i) or s.startswith("'''", i):
            q = s[i:i + 3]
            j = s.find(q, i + 3)
            i = n if j < 0 else j + 3
            continue
        if s[i] == "#":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


@unittest.skipUnless(RUN_PAID_TESTS, "真实 embedding 集成测试；设置 RUN_PAID_TESTS=1 才跑")
class TestLeadinChildren(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.r = Retriever()

    def _hit(self, cid: str) -> dict:
        it = dict(self.r.items[self.r._id2idx[cid]])
        it["_score"] = 0.5
        it["_cos"] = 0.4
        return it

    def _expand(self, ids: list[str], cap: int | None = None) -> list[dict]:
        old = config.PARENT_CHILD_MAX
        if cap is not None:
            config.PARENT_CHILD_MAX = cap
        try:
            return self.r._attach_leadin_children([self._hit(c) for c in ids])
        finally:
            config.PARENT_CHILD_MAX = old

    # ---------- 语料前提 ----------

    def test_corpus_has_leadin_parents(self):
        """语料里确实有一批"正文只有引子、内容全在子条"的条款。"""
        leadins = list(self.r._leadin)
        self.assertGreaterEqual(len(leadins), 30, "引子型父条不该这么少，判据是不是被改坏了")
        for i in leadins:
            it = self.r.items[i]
            self.assertTrue(it.get("text", "").strip().endswith(("：", ":")),
                            f"{it['id']} 正文不以冒号结尾，不该被判为引子")
            self.assertTrue(self.r._children.get(it["id"]),
                            f"{it['id']} 没有子条，不该被判为引子")

    # ---------- 行为级 ----------

    def test_children_of_leadin_are_added(self):
        out = self._expand(["9.6", "9.5.5.2"])
        ids = [h["id"] for h in out]
        self.assertIn("9.6.1", ids, "9.6 的正文全在 9.6.1 里，必须补进来")
        i = ids.index("9.6")
        self.assertEqual("9.6.1", ids[i + 1], "子条要紧跟在父条后面")
        self.assertEqual("9.6", out[i + 1].get("_via_parent"),
                         "补进来的子条必须标记来源，便于排查")

    def test_original_hits_are_never_dropped(self):
        """⭐ 守真实踩过的 bug：补子条绝不能吞掉任何一条原文命中。"""
        ids = ["9.6", "9.5.5.2", "9.5", "9.1", "9.2", "9.4", "9.3", "9.5.1"]
        for cap in (0, 1, 2, 6, 99):
            out = [h["id"] for h in self._expand(ids, cap=cap)]
            for cid in ids:
                self.assertIn(cid, out, f"cap={cap} 时原文命中 {cid} 被吞了")
            keep = [c for c in out if c in ids]
            self.assertEqual(ids, keep, f"cap={cap} 时原文命中的相对顺序被打乱")

    def test_cap_is_respected(self):
        ids = ["9.6", "9.5", "9.3", "8.1"]     # 前三条都是引子父条，子条很多
        for cap in (0, 1, 3, 6):
            out = self._expand(ids, cap=cap)
            added = [h for h in out if h.get("_via_parent")]
            self.assertLessEqual(len(added), cap, f"cap={cap} 被突破")
            self.assertLessEqual(len(out), len(ids) + cap, f"cap={cap} 时总数超了")

    def test_no_duplicates(self):
        # 9.5 是引子父条，9.5.1 又是它的子条且已在命中里
        out = self._expand(["9.5", "9.5.1", "9.6", "9.6.1"])
        ids = [h["id"] for h in out]
        self.assertEqual(len(ids), len(set(ids)), f"命中列表里出现重复：{ids}")

    def test_non_leadin_parent_is_not_expanded(self):
        """正文不是引子的父条不补 —— 它的正文本身就是完整答案，补子条只会灌水。"""
        self.assertNotIn(self.r._id2idx["8.5"], self.r._leadin)
        out = self._expand(["8.5"])
        self.assertEqual(["8.5"], [h["id"] for h in out])

    def test_added_children_carry_parent_score(self):
        out = self._expand(["9.6"])
        child = next(h for h in out if h["id"] == "9.6.1")
        self.assertEqual(0.5, child["_score"], "子条要继承父条分，否则会打乱展示顺序")

    # ---------- 红队：证明这条守卫有牙 ----------

    def test_buggy_early_return_would_be_caught(self):
        """把踩过的 bug 原样写一遍，断言它**真的会被上面那条守卫抓住**。

        没有这一步，"守卫全绿"只证明它没抛异常 —— 不证明它能报错。
        """
        def buggy(hits, cap):
            out = []
            added = 0
            for h in hits:                      # ← 旧版：命中还没 append 就先 return
                out.append(h)
                if added >= cap:
                    return out
                idx = self.r._id2idx.get(h["id"])
                if idx not in self.r._leadin:
                    continue
                for ci in self.r._children.get(h["id"], []):
                    if added >= cap:
                        return out
                    out.append(dict(self.r.items[ci]))
                    added += 1
            return out

        hits = [self._hit(c) for c in ["9.6", "9.5.5.2", "9.1", "9.2"]]
        bad = [h["id"] for h in buggy(hits, 0)]
        self.assertNotIn("9.2", bad, "红队失效：写坏的版本居然没吞掉原文命中")
        self.assertLess(len(bad), 4, "红队失效：写坏的版本居然保住了全部命中")

    # ---------- 源码级 ----------

    def test_search_actually_calls_expansion(self):
        src = strip_py_comments(RETRIEVER_PY.read_text(encoding="utf-8"))
        self.assertIn("_attach_leadin_children(hits)", src,
                      "子条补齐没接在 search() 上，等于没做")

    def test_cap_comes_from_config(self):
        src = strip_py_comments(RETRIEVER_PY.read_text(encoding="utf-8"))
        self.assertIn("config.PARENT_CHILD_MAX", src, "上限必须来自 config，不能写死数字")
        self.assertGreater(config.PARENT_CHILD_MAX, 0)
        self.assertLessEqual(config.PARENT_CHILD_MAX, 12,
                             "上限超过 12 条会让上下文膨胀到 20 条以上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
