# -*- coding: utf-8 -*-
"""守卫：离题/空指代分支的**两种文案不许混**（2026-10-12 踩过）。

## 背景：`not_found` 其实是**两种完全不同的失败**
闸门拦下的问句里混着两类，而它们**需要的回复相反**：

| 类型 | 例子 | 该回什么 |
|---|---|---|
| **空指代**（是飞盘问题，只是没说清） | 「这种情况算吗」 | **邀请他补细节** |
| **真离题** | 「帮我订张机票」 | **老实说不在范围内** |

⚠️ 根因在闸门（它分不开这两类，两者都是 `domain_word=None`）——
   本守卫管不了根因，只管**别把两种文案混成一句**。

## 踩过的坑（两次，都记在 `generator.not_found_text` 的注释里）
1. **原文案**「我只回答飞盘规则的问题。你这个我帮不上」——
   对「这种情况算吗」**在事实上是错的**（那**是**飞盘问题），而且"我帮不上"是终点。
2. **改成一律邀请** ⇒ **实测打脸**：
   「帮我订张机票」「怎么学 Python」「推荐个餐厅」全被回成
   「得知道具体是什么情况：谁做了什么、**当时盘在谁手里**」
   —— **对着订机票的人问盘的归属**。比简短拒答**更让人困惑**。
   ⇒ 教训：**一句话服务不了两种失败模式。**

## 现在
用**问句的说法本身**当信号（`_looks_like_ruling_question`）：
讨判定的说法（…算吗/行不行/怎么判…）⇒ 邀请；其余 ⇒ 老实说不在范围内。

⚠️ 这是**启发式**，不保证准。所以本守卫钉的不是"判得准"（那做不到），
   而是三条**能钉死**的性质：
     · 讨判定的问句**不能**拿到"不在飞盘规则范围内"（那是错的事实陈述）；
     · 真离题的问句**不能**拿到"当时盘在谁手里"（那是荒谬回复）；
     · 两支都不许出现铁律 4 的禁词。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import generator as gen  # noqa: E402

# 讨判定的问句：**必须**被认出来（否则用户拿到"不在飞盘规则范围内"这个错误陈述）
RULING = [
    "这种情况算吗", "他那样做可以吗", "刚才那个算不算", "我该叫什么",
    "这算谁的", "这样行不行", "刚才那个有效吗", "那是犯规吗", "这球算不算数",
]

# 真离题：**必须不**被当成讨判定（否则会问出"当时盘在谁手里"这种荒谬话）
OFFTOPIC = [
    "帮我订张机票", "怎么学 Python", "推荐个餐厅", "今天天气怎么样",
    "帮我写一份简历", "北京到上海多远", "我想买个飞盘",
]

BANNED = ["裁判", "判罚", "处罚", "罚球"]

# 荒谬回复的标志：对着离题问题问"盘在谁手里"
ABSURD_MARK = "盘在谁手里"
# 错误陈述的标志：对着飞盘问题说"不在飞盘规则的范围里"
WRONG_CLAIM = "不在飞盘规则的范围里"


class TestTwoBranchesDoNotMix(unittest.TestCase):
    def test_ruling_questions_are_recognized(self):
        for q in RULING:
            with self.subTest(q=q):
                self.assertTrue(
                    gen._looks_like_ruling_question(q),
                    f"「{q}」是**在讨一个判定**的飞盘问题，却没被认出来 ⇒ "
                    f"用户会拿到「{WRONG_CLAIM}」这个**事实上错误**的回复。")

    def test_offtopic_is_not_treated_as_ruling(self):
        for q in OFFTOPIC:
            with self.subTest(q=q):
                self.assertFalse(
                    gen._looks_like_ruling_question(q),
                    f"「{q}」明显离题，却被当成讨判定 ⇒ 会回它"
                    f"「{ABSURD_MARK}」，那是**荒谬回复**（实测踩过）。")

    def test_ruling_branch_text(self):
        txt = gen.not_found_text([], in_domain=False, text="这种情况算吗")
        self.assertNotIn(WRONG_CLAIM, txt,
                         "空指代的飞盘问题不该被告知「不在飞盘规则范围内」")
        self.assertIn("场上", txt, "邀请里要说清「描述场上发生了什么」")

    def test_offtopic_branch_text(self):
        txt = gen.not_found_text([], in_domain=False, text="帮我订张机票")
        self.assertNotIn(ABSURD_MARK, txt,
                         "对着离题问题问「盘在谁手里」会让人觉得系统不懂自己在跟谁说话")
        self.assertTrue(len(txt) < 60,
                        f"离题分支要**一句话打住**（这一支不调大模型），实际 {len(txt)} 字")

    def test_no_banned_words_in_either_branch(self):
        for q in RULING + OFFTOPIC:
            for in_dom in (True, False):
                with self.subTest(q=q, in_domain=in_dom):
                    txt = gen.not_found_text([], in_domain=in_dom, text=q)
                    bad = [w for w in BANNED if w in txt]
                    self.assertEqual(bad, [], f"出现禁词 {bad}：{txt[:60]}")

    def test_default_signature_still_works(self):
        """`text` 必须是**可选**参数 —— 有 5 个调用点不传它。"""
        try:
            gen.not_found_text([])
            gen.not_found_text([], True)
        except TypeError as e:  # noqa: BLE001
            self.fail(f"not_found_text 的调用签名被改坏了（旧调用点会全崩）：{e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
