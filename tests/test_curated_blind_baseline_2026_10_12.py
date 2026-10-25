# -*- coding: utf-8 -*-
"""回归守卫：**盲测指标不许退化**（2026-10-12 起钉住）。

## 为什么钉这个数，而不是钉"自测变体 100%"
自测变体是**词与变体同源**的产物 —— 新手那条报 14/14 = 100%，
而**真盲**（`data/_newbie_blind.json`，生成时禁止看 keywords）只有 29%（诚实 26%）。
⇒ 钉自测变体等于钉一个**虚高的数**，它变红了也说明不了用户会怎样。

## 为什么"被抢率"比"命中率"更该钉
疑问库命中**绕过整个生成层**（见 `app/curated.py` 头部）。
- **未命中** → 掉回正常检索+生成层 ⇒ **无害**；
- **被抢**（该答 A，实际返回 B）→ **答非所问的权威答案** ⇒ **最坏**。

⇒ 所以本文件钉**两个下界/上界**：
  · 诚实命中率 **不低于** 基线（掉下来 = 系统认得更少了）
  · 被抢数 **不高于** 基线（涨上去 = 错答变多了）

⚠️ **数字是 2026-10-12 第 17 轮实测的真实值**（收窄两处泛关键词 + 一处跑题修掉之后）。
   改基线必须**同时**给出"为什么这次退化是可接受的" ——
   不许为了让守卫变绿而调松它（这与 `test_curated_keyword_hygiene` 里那条同源纪律）。

⚠️ 连带验证：本文件同时确认那两处**已定位的泛关键词没有回来**
  （它们曾让「场上不懂的能直接问吗」和「防守的时候能不能一直贴着人」
    被完全无关的条目抢走）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import curated as curated_mod  # noqa: E402
from app.curated import _norm  # noqa: E402

BLIND = ROOT / "data" / "_newbie_blind.json"
TARGET = "新手第一次打比赛"

# ---- ⚠️ 基线改过一次（2026-10-13），改的理由必须写清 ----
# 原基线：诚实命中 7、被抢 5（对应 `_WEAK_DICE = 0.50`）
# 现基线：诚实命中 1、被抢 4（对应 `_WEAK_DICE = 0.65`）
#
# **为什么接受命中数下降**：0.50 那个阈值下，**curated 的命中有三分之二是错的**
#   （两套独立评测集实测精确率 **32.5%**，251/372 命错）。抬到 0.65 后精确率
#   **65.9%**，代价是命中数减半、盲测诚实命中 7 → 1。
#   ⇒ 判据是本项目一贯的「两边代价不对称」：
#     **漏了无害**（掉回检索+生成，规则真没写时它会拒答）；
#     **错了最坏**（curated 绕过生成层 ⇒ 用户拿到**标着「人工核定」的答非所问**）。
#     实测受害样例：「比赛打几分」被答成"第一次打比赛，不用背规则…"。
#   ⚠️ 完整数据与推翻过程见 `app/curated.py` 里 `_WEAK_DICE` 上方那段注释。
#   ⚠️ 这个数**下调过**：不允许为了让守卫变绿而调松；要动必须给同样的证据。
BASE_HONEST_HITS = 1
BASE_STOLEN = 4

# ⚠️ 已知**必须保持"无命中"**的探针：它们曾被完全无关的条目抢走。
#    出现在这里 = "这条问法当前答不了，但**绝不能**给出一个跑题的权威答案"。
MUST_NOT_MATCH = [
    "场上不懂的能直接问吗",       # 曾被 m015（跑着接住能直接传吗）抢走
    "防守的时候能不能一直贴着人",  # 曾被 m025（开盘前能叫暂停吗）抢走
    # ⚠️ 2026-10-13 补：这四条在部署后**线上实测答错**（curated 误配），
    #    抬到 0.65 后不再命中。它们是"精确率"这个问题的具体受害样例，
    #    **必须留在名单里** —— 一旦回归，用户会再次拿到答非所问的权威答案。
    "得分区从哪里开始算",         # 曾被答成"从 1 数到 10……读秒"（该讲场地 2.4）
    "可以戴手表打球吗",           # 曾被答成"第一次打比赛，不用背规则…"（该讲装备 3.4）
    "比赛打几分",                 # 曾被答成"第一次打比赛，不用背规则…"（该讲赛制 4.2）
    "随便什么盘都能用吗",         # 曾被答成"规则里没写，不等于可以做…"（该讲用哪种盘 3.1）
]


def _target() -> dict | None:
    for it in curated_mod.all_items():
        if (it.get("question") or "").startswith(TARGET):
            return it
    return None


class TestBlindMetricsDoNotRegress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BLIND.is_file():
            raise unittest.SkipTest(f"没有盲测集 {BLIND.name}")
        cls.variants = json.loads(BLIND.read_text(encoding="utf-8"))
        cls.tgt = _target()
        if cls.tgt is None:
            raise unittest.SkipTest(f"疑问库里没有「{TARGET}」条目")

    def _score(self):
        tid = self.tgt.get("id")
        kws = [k for k in (_norm(k) for k in (self.tgt.get("keywords") or [])) if k]
        hits = stolen = 0
        ind_hits = ind_tot = 0
        for q in self.variants:
            qn = _norm(q)
            triv = any(k in qn and k != qn for k in kws)
            got = curated_mod.lookup(q)
            gid = (got or {}).get("id")
            if gid == tid:
                hits += 1
                if not triv:
                    ind_hits += 1
            elif gid:
                stolen += 1
            if not triv:
                ind_tot += 1
        return hits, ind_hits, ind_tot, stolen

    def test_honest_hit_rate_not_lower(self):
        hits, ind_hits, ind_tot, stolen = self._score()
        self.assertGreaterEqual(
            ind_hits, BASE_HONEST_HITS,
            f"诚实命中数从 {BASE_HONEST_HITS} 掉到 {ind_hits}（共 {ind_tot} 条独立变体）"
            f" ⇒ 系统**认得更少了**。\n"
            f"  ⚠️ 如果你改了 `app/curated.py` 的匹配逻辑，请先看 "
            f"`tools/curated_stress.py`（误区库那一侧的代价）。\n"
            f"  ⚠️ 如果你改了这条的关键词，请确认不是「收窄过头」。")

    def test_stolen_not_higher(self):
        hits, ind_hits, ind_tot, stolen = self._score()
        self.assertLessEqual(
            stolen, BASE_STOLEN,
            f"被别的条目抢走的条数从 {BASE_STOLEN} 涨到 {stolen} ⇒ "
            f"**答非所问的权威答案变多了**（比未命中坏得多，因为疑问库绕过生成层）。\n"
            f"  用 `python tools/curated_blind.py` 看被抢明细，"
            f"再收窄那条**具体**的关键词（不要抬全局阈值）。")

    def test_known_offtopic_steals_stay_gone(self):
        """那两处完全跑题的抢答不许回来。"""
        back = []
        for q in MUST_NOT_MATCH:
            got = curated_mod.lookup(q)
            if got:
                back.append(f"「{q}」→ [{got.get('id')}] {got.get('question')}")
        self.assertEqual(
            back, [],
            "以下问法又被抢走了（它们问的**不是**返回那条，属于完全跑题）：\n  "
            + "\n  ".join(back)
            + "\n⇒ 去收窄那条条目的关键词（曾用过 "
              "「能不能直接出手」「暂停的时候能换人吗」这类极泛词，别再放回来）。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
