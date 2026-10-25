# -*- coding: utf-8 -*-
"""守卫：疑问库 / 误区库**不许出现同分并列**，且自己的探针必须命中自己。

**为什么这条守卫比"覆盖测试"更该先有**
`app/curated.py::lookup()` 是**取最高分**那一条：
```python
for item in _load():
    s = _score(item, qn, q)
    if s > best_s:      # ← 严格的 > ，同分时**前者获胜**
        best, best_s = item, s
```
⇒ **两条同分时，谁赢取决于文件里的先后顺序** —— 等于"静默地随机选一条"。
而疑问库命中会**绕过整个检索+生成层**，用户拿到的是**答非所问的权威答案**，
是本项目**最坏**的一种失败（比拒答坏得多）。

⚠️ 这不是假想：2026-10-12 实测抓到过一对 ——
   `我跑着接住盘，能直接传吗` → m015（跑着接）与 m016（倒地/躺着）**都是 0.5**，
   而 m015 排在后面 ⇒ **实际给出的是"躺着传"的答案**。两条答案各自都对，
   但**场景不同**，用户会困惑。已给两条各补"本情形独有"的关键词修掉。

⚠️ 假绿的教训（本次踩到）：第一版测试只判"过没过 0.50 阈值"就把
   「两条都 >= 0.50」报成互抢 —— **那是误报**，因为高分本就该压过低分。
   只有**同分**才真的靠顺序决胜。本守卫按"同分"判，不按"过阈值"判。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import curated as curated_mod  # noqa: E402

# 待测的两份库。⚠️ 误区库若还没上线（未并入 curated.jsonl）就单独查它。
LIBS = [
    ("curated.jsonl", ROOT / "data" / "curated.jsonl"),
    ("misconceptions.jsonl", ROOT / "data" / "misconceptions.jsonl"),
]


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _hits(q: str, items: list[dict]) -> list[tuple[str, float]]:
    qn = curated_mod._norm(q)
    out = [(it.get("id") or "?", round(curated_mod._score(it, qn, q), 4))
           for it in items]
    return sorted([h for h in out if h[1] >= 0.50], key=lambda x: -x[1])


class TestNoScoreTies(unittest.TestCase):
    """① 同分并列 = 静默随机 ⇒ 必须为零。"""

    def test_no_ties_on_own_probes(self):
        for name, path in LIBS:
            items = _load(path)
            if not items:
                continue
            for it in items:
                me = it.get("id")
                probes = [it.get("question") or ""] + list(it.get("keywords") or [])
                for q in probes:
                    if not q.strip():
                        continue
                    with self.subTest(lib=name, item=me, probe=q):
                        hits = _hits(q, items)
                        if not hits:
                            self.fail(f"[{name}/{me}]「{q}」一条都没命中 —— "
                                      f"这条探针形同虚设（或阈值把它筛掉了）")
                        top = hits[0][1]
                        tied = [h for h, s in hits if s == top]
                        self.assertEqual(
                            len(tied), 1,
                            f"[{name}]「{q}」**同分并列** {tied} = {top}\n"
                            f"  ⇒ lookup() 会用文件顺序决胜 = 静默随机选一条。\n"
                            f"  ⇒ 给相关条目补**本情形独有**的关键词，把分差拉开。")


class TestNoCrossTies(unittest.TestCase):
    """①b **交叉**探针：A 的探针不许和 B 同分（这才是真实用户会遇到的情况）。

    ⚠️ 为什么必须有这一条（2026-10-12 补）：
    只测"自己的探针"会**漏掉交叉同分**。实测抓到过：
        m025（暂停/换人/读秒）的探针「暂停之后读秒还剩几」
        与 m009（叫了犯规读秒接着数还是重数）**同分 0.5**
    —— 真实用户问"暂停之后读秒还剩几"时，谁赢靠文件顺序 = 静默随机。
    """

    def test_no_cross_entry_ties(self):
        for name, path in LIBS:
            items = _load(path)
            if len(items) < 2:
                continue
            ties = []
            for it in items:
                me = it.get("id")
                probes = [it.get("question") or ""] + list(it.get("keywords") or [])
                for q in probes:
                    if not q.strip():
                        continue
                    hits = _hits(q, items)
                    if len(hits) < 2:
                        continue
                    top = hits[0][1]
                    tied = sorted(h for h, s in hits if s == top)
                    if len(tied) > 1:
                        ties.append(f"[{me}]「{q}」与 {tied} 同分 {top}")
            self.assertEqual(
                sorted(set(ties)), [],
                f"[{name}] 交叉同分并列（lookup 靠文件顺序决胜 = 静默随机）：\n  "
                + "\n  ".join(sorted(set(ties))[:10])
                + "\n⇒ 给相关条目补**本情形独有**的关键词拉开分差。")


class TestNoTiesOnRealVariants(unittest.TestCase):
    """①c **真实变体**上：用户拿到的答案必须是"并列最高分之一"，否则宁可没有。

    ⚠️⚠️ 这条测试的判据改过两次，过程本身就是教训（2026-10-12）：

    ① 第一版测"自己的 question + keywords 的字面串" ⇒ **全绿，但是假绿**。
       真实同分存在（「暂停之后读秒还剩几」→ m009/m025 同分），
       而那两句**既不是任何 question、也不是任何 keyword**，所以测不到。
       ⇒ 探针**必须包含变体集**（那是真实用户会打的字）。

    ② 第二版把"同分"直接判失败 ⇒ 也**不对**。因为加了 fail-closed 之后
       （`lookup()` 遇到并列最高分直接返回 None），同分**不再产生错答**，
       只会**掉回正常检索流程** —— 那是**设计上可接受**的（漏了无害，答错才最坏）。
       ⇒ 正确判据不是"有没有同分"，而是：
         **如果返回了答案，它必须是"并列最高分"之一**；
         如果返回 None，那没关系。

    这个改动同时把"覆盖"和"安全"两个目标分开表达了：
      覆盖看 `curated_stress.py --variants`，安全看这条守卫。
    """

    VARIANTS = ROOT / "data" / "_mis_variants.json"

    def test_returned_answer_is_always_a_top_tied_candidate(self):
        """⚠️ 必须测**真正在线的库**（`curated.jsonl`），因为 `lookup()` 读的就是它。

        踩过的坑（2026-10-12）：上一版拿 `misconceptions.jsonl` 当候选库，
        却调用 `lookup()`（读 `curated.jsonl`）⇒ **拿 A 库的候选去比 B 库的返回**，
        于是必然报"返回的不在并列最高里"。**探针库与候选库必须是同一个。**
        （当时误区库还没并入 curated；现在已并入、online 了。）
        """
        if not self.VARIANTS.is_file():
            self.skipTest("没有变体集 data/_mis_variants.json（可选文件）")
        variants = json.loads(self.VARIANTS.read_text(encoding="utf-8"))
        items = _load(ROOT / "data" / "curated.jsonl")   # ← 与 lookup() 同源
        bad = []
        for mid, qs in variants.items():
            for q in qs:
                cands = _hits(q, items)
                got = curated_mod.lookup(q)
                if got is None:
                    continue                     # 没答案是允许的（fail-closed）
                top = cands[0][1] if cands else None
                tied = {h for h, s in cands if s == top}
                if got.get("id") not in tied:
                    bad.append(f"「{q}」返回 {got.get('id')}，"
                               f"但并列最高是 {sorted(tied)} = {top}")
        self.assertEqual(
            sorted(set(bad)), [],
            "返回的答案不是并列最高分之一（= 给用户答非所问的权威答案）：\n  "
            + "\n  ".join(sorted(set(bad))[:10]))


class TestOwnProbeWins(unittest.TestCase):
    """② 用自己的问题/关键词当探针时，必须**是自己**得分最高。"""

    def test_own_probe_is_top(self):
        for name, path in LIBS:
            items = _load(path)
            if not items:
                continue
            for it in items:
                me = it.get("id")
                for q in [it.get("question") or ""] + list(it.get("keywords") or []):
                    if not q.strip():
                        continue
                    with self.subTest(lib=name, item=me, probe=q):
                        hits = _hits(q, items)
                        self.assertTrue(hits, f"[{name}/{me}]「{q}」无命中")
                        self.assertEqual(
                            hits[0][0], me,
                            f"[{name}/{me}]「{q}」被 {hits[0]} 压过 ⇒ "
                            f"自己的关键词指向了别人的场景。")


class TestLibsParseAndCite(unittest.TestCase):
    """③ 两份库都必须可解析、条号必须真实存在。"""

    def test_parse_and_clauses(self):
        rules = set()
        for line in (ROOT / "data" / "rules.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rules.add(json.loads(line)["id"])
        for name, path in LIBS:
            items = _load(path)          # 解析失败会在这里抛
            for it in items:
                bad = [c for c in (it.get("clauses") or []) if c not in rules]
                with self.subTest(lib=name, item=it.get("id")):
                    self.assertEqual(bad, [],
                                     f"[{name}/{it.get('id')}] clauses 有不存在的条号 {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
