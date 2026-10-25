# -*- coding: utf-8 -*-
"""守卫：「幽灵线索词」不得再单独把问句拉进错误的族。

**背景（2026-10-12，DSH 会话打线上实机抓到的）**

探针 `tools/newbie_scenario_probe.py` 问线上：
    我第一次打比赛，盘传过来我没接住掉地上了，然后呢
线上返回 `routes=['接盘犯规']` —— 这是**新手问"盘落地的后果"**
（正解 13.1.1 攻防转换），却被「接盘犯规」族接走，top8 被 17.2.x 占满，
真解只靠余弦勉强排第 7，**换一次措辞就可能答错**。

**病因**：`app/routes.py` 的「接盘犯规」族主档线索里收了
`"没接住"` / `"接不住"` —— 这两个短语**不含任何犯规/接触语义**，
纯粹是在描述"没接到盘"这个动作。

**量级核实**（`tools/_diag_phantom.py`，815 条真实问法 = 447 FAQ + 368 审计）：
全库只有 **2 条**问法靠这两个词命中该族，而它们的期望答案分别是
`7.8` / `14.3` —— **本来就不该由「接盘犯规」族来答**。
⇒ 这两个词**不承载任何正确职责**，只贡献误触发。

**修法**：`_CONDITIONAL_HINTS` —— 线索词只有在句中出现
接触/犯规语境词（`_CONTACT_CONTEXT`）时才触发。
    · 「跳起来接不住**被撞**了算谁的」→ 有接触词 ⇒ 照旧触发（保住场景题）
    · 「我**没接住**掉地上了然后呢」  → 无接触词 ⇒ 不触发（修掉误报）

⚠️ **本文件的两半必须同时成立**，少一半就是错的方向：
    只钉"不触发" → 有人会把线索词直接删掉，害掉真实的场景题；
    只钉"触发"   → 有人会把条件去掉，退回原来的误报。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import routes  # noqa: E402

FAMILY = "接盘犯规"


class TestPhantomHintMustNotFireAlone(unittest.TestCase):
    """① 幽灵词**单独出现**时，不许触发「接盘犯规」族。"""

    def test_newbie_down_question_does_not_hit_catch_foul(self):
        """新手问「没接住掉地上了，然后呢」——这是问规则后果，不是问犯规。

        ⚠️ 这条是**本次修复的直接触发点**，改坏了必须报红。
        """
        q = "我第一次打比赛，盘传过来我没接住掉地上了，然后呢"
        self.assertNotIn(FAMILY, routes.match_routes(q))

    def test_bare_phantom_words_do_not_hit(self):
        """两个幽灵词各自单独出现，都不许触发该族。"""
        for q in [
            "我没接住",
            "盘传过来接不住怎么办",
            "队友传的盘接不住，算谁的失误",
            "接不住会怎么样",
        ]:
            with self.subTest(q=q):
                self.assertNotIn(FAMILY, routes.match_routes(q),
                                 f"「{q}」不含任何接触/犯规语境，不该进「{FAMILY}」族")

    def test_phantom_words_are_still_registered(self):
        """⚠️ 幽灵词**必须留在表里**（只是被条件约束），不许直接删。

        直接删会害掉下面 `TestCatchFoulStillFires` 那类真实场景题 ——
        它们是真实口语，是族线索的正当来源。
        """
        self.assertIn("没接住", routes._CONDITIONAL_HINTS,
                      "「没接住」应当作为**条件线索**保留，而不是从族里删掉")
        self.assertIn("接不住", routes._CONDITIONAL_HINTS)
        # 反向：它们仍须留在「接盘犯规」族的线索表里（条件约束 ≠ 删除）
        hints = next(h for n, h, _, _, _ in routes.ROUTES if n == FAMILY)
        self.assertIn("没接住", hints)
        self.assertIn("接不住", hints)


class TestCatchFoulStillFires(unittest.TestCase):
    """② 带接触语境的真实场景题，该族**必须照旧触发**（这是修法的另一半）。"""

    CASES = [
        "我跳起来接不住被撞了算谁的犯规",
        "接不住是因为他撞我",
        "抢盘的时候没接住，被他推了一下",
        "我接不住掉地上，对方还打了我一下",
        "跳起来接不住，跟防守的撞在一起",
        "没接住，他把我撞倒了",
        "接不住是因为对方拍了我手",
    ]

    def test_contact_context_still_hits(self):
        for q in self.CASES:
            with self.subTest(q=q):
                self.assertIn(FAMILY, routes.match_routes(q),
                              f"「{q}」含接触语境，必须仍能触发「{FAMILY}」族")

    def test_reversed_order_also_fires(self):
        """接触词出现在幽灵词**之后**也要生效（判据是"句中有没有"，不是顺序）。"""
        q = "我被撞了，没接住"
        self.assertIn(FAMILY, routes.match_routes(q))


class TestConditionMechanismIntegrity(unittest.TestCase):
    """③ 机制本身不许被绕过。"""

    def test_match_routes_is_the_only_entry(self):
        """条件判据只能有一份实现 —— 别在别处重写一份过滤逻辑。

        同 AGENTS.md §13「域内/术语判定只能有一份实现」的道理：
        两处各写一份必然分叉（谁先跑谁赢，纯看调用顺序）。
        """
        src = (ROOT / "app" / "routes.py").read_text(encoding="utf-8")
        # 条件判据的语境词表只许出现在 _CONDITIONAL_HINTS 的定义处，
        # 不许在 match_routes 里再内联一份。
        self.assertIn("_CONDITIONAL_HINTS", src)
        self.assertIn("def _cond_ok", src)
        # 反向：match_routes 里必须真的调用了判据（钉"使用点"而不是"声明点"）
        body_start = src.index("def match_routes(")
        body_end = src.index("def _cond_ok(")
        body = src[body_start:body_end]
        self.assertIn("_CONDITIONAL_HINTS", body,
                      "match_routes 必须真的检查条件线索，不能只是声明了却不用")
        self.assertIn("_cond_ok", body,
                      "match_routes 必须调用 _cond_ok（钉使用点，不是声明点）")

    def test_context_words_are_not_phantom_entities(self):
        """语境词里不许混进"飞盘实体名词"。

        ⚠️ 理由见 AGENTS.md §8「加线索词粒度」：
        语境词是用来**收窄**触发面的，混进「盘」「场地」这类泛词等于没约束
        —— 任何飞盘问题都含「盘」，条件恒真，机制失效。
        """
        banned = ("盘", "场地", "比赛", "队员", "规则")
        for w in routes._CONTACT_CONTEXT:
            with self.subTest(w=w):
                self.assertNotIn(w, banned,
                                 f"语境词「{w}」太泛，会让条件恒真、机制失效")

    def test_conditional_hints_keys_are_normalized(self):
        """`_CONDITIONAL_HINTS` 的键必须是 `_norm` 之后的形式。

        匹配时比的是 `_norm(hint)`（见 `_build`），键没归一化就**永不生效**
        —— 而且不报错，属于静默失效。
        """
        for k in routes._CONDITIONAL_HINTS:
            with self.subTest(k=k):
                self.assertEqual(k, routes._norm(k),
                                 f"条件线索键「{k}」没归一化，会静默失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
