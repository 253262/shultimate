# -*- coding: utf-8 -*-
"""守卫：⭕ **范围与意图闸门**（`app/scope.py`，2026-10-13 owner 定的底层逻辑）。

## 这条守卫在守什么
owner 把产品定位说清了：

> 「一开始我只想做飞盘规则，后面我觉得除了飞盘规则之外，还要做一些非飞盘规则
>  但和飞盘相关的事情，然后又觉得要接触他情绪。」

> 「**什么时候拒答？** 当他**诱导我们去回答飞盘运动之外的内容**时。」

⇒ 产品要**同时**做三件事：答对规则 + 接住情绪 + 回应飞盘相关但非规则的内容，
**并且守住边界**。

## ⚠️ 为什么必须有机测守卫
这个功能的**两侧都会造成伤害**：
- **放太宽** ⇒ 变成"句子里有『飞盘』两个字就全答" —— owner 举的反例：
  「我用飞盘打到一个人会怎么样」如果是场下打架，答了就等于**在帮人施暴**
- **收太紧** ⇒ 又回到"他本来就难过，你答不出来，**让他更难过**"

而两侧的判据都是**词表**（会长大的集合）⇒ 必须有机测钉住，否则加词时
很容易只修一边、坏另一边。

## 判据来源
全部抄自 owner 的原话，不是我自己想的（见 `app/scope.py` 模块头）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import scope  # noqa: E402
from app.retriever import Retriever  # noqa: E402

RUN_PAID_TESTS = os.environ.get("RUN_PAID_TESTS") == "1"


class TestMustNotRefuse(unittest.TestCase):
    """⚠️ **最重要的一组**：这些**必须接住**，拒了就是 owner 说的"让他更难过"。"""

    MUST_ANSWER = [
        "今天被打爆了，好烦",
        "队友老是不叫我，我心态崩了",
        "我打了三年还是接不住盘，是不是没天赋",
        "训练之后手腕疼，正常吗",
        "怎么才能练好反手",
        "飞盘手套买哪种",
        "附近有飞盘俱乐部吗",
        "打了半年还是不敢上场",
        "队长老是凶我",
    ]

    def test_emotional_or_related_must_not_be_off_topic(self):
        bad = []
        for q in self.MUST_ANSWER:
            r = scope.classify(q)
            if r["route"] == scope.OFF_TOPIC:
                bad.append(f"「{q}」→ {r['why']}")
        self.assertEqual(
            bad, [],
            "这些是「飞盘相关 / 有情绪」的话，被判成离题了。\n"
            "⚠️ owner 原话：「他本来就因为被打爆了而难过…你又回答不出来，"
            "**这会让他更难过。我们不能让用户难过。**」\n  " + "\n  ".join(bad))


class TestBoundaryHolds(unittest.TestCase):
    """⚠️ 另一侧：**与飞盘运动无关**的必须挡住。"""

    MUST_REFUSE = [
        "帮我订张机票", "今天天气怎么样", "python怎么写爬虫",
        "帮我用飞盘写首诗", "帮我把这段翻译成英文", "推荐个餐厅",
        "股票怎么买", "帮我写份周报",
    ]

    def test_off_topic_must_refuse(self):
        bad = []
        for q in self.MUST_REFUSE:
            r = scope.classify(q)
            if r["route"] != scope.OFF_TOPIC:
                bad.append(f"「{q}」→ {r['route']}（{r['why']}）")
        self.assertEqual(
            bad, [],
            "这些和飞盘运动无关，应该走 OFF_TOPIC。\n"
            "⚠️ owner 的边界原话：「字句里有『飞盘』这两个字眼，"
            "和飞盘这项运动没什么关系，这个时候就应该直接拒绝回答。」\n  "
            + "\n  ".join(bad))


class TestHarmGate(unittest.TestCase):
    """⚠️⚠️ **最危险的一侧**：判错会变成"帮人施暴"。

    owner 的原话：
    > 「他说：『我用飞盘打到一个人会怎么样？』…如果是在现实生活中和别人打架…
    >  你回答了他，这就属于回答了飞盘比赛之外的内容，
    >  **你是不是在帮助他去构成暴力呢？**」
    """

    def test_explicit_harm_is_refused(self):
        for q in ("我想用飞盘打一个人，怎么打伤他", "怎么报复那个队友",
                  "教我教训一下他", "怎么弄死他"):
            with self.subTest(q=q):
                self.assertEqual(scope.classify(q)["route"], scope.HARM,
                                 f"「{q}」明显在求帮助伤害他人，必须判 HARM")

    def test_field_questions_are_never_harm(self):
        """⚠️ **反向守卫**：规则问题里全是"撞人/犯规"这类词，绝不能被误判成 HARM。

        这是最要紧的一条 —— 误判会让**真的在场上被撞的人**拿不到答案。
        """
        for q in ("接盘时候被撞算犯规吗", "冲撞算犯规吗", "他撞了我一下算不算犯规",
                  "比赛里我用飞盘打到人了会怎么样", "场上打人算不算犯规",
                  "危险动作怎么判", "有人故意撞我怎么办"):
            with self.subTest(q=q):
                self.assertNotEqual(scope.classify(q)["route"], scope.HARM,
                                    f"「{q}」是规则问题，绝不能判 HARM")

    def test_ambiguous_harm_asks_instead_of_refusing(self):
        """⚠️ 歧义**不硬拒，而是问一句**（owner 那个例子的正解）。

        理由：它可能是场上冲撞（该答）也可能是场下打架（不该答）。
        硬拒会让**真的在场上被撞的人**更难过 —— 正是 owner 要避免的。
        """
        r = scope.classify("用飞盘打到一个人会怎么样")
        self.assertEqual(r["route"], scope.RELATED, "歧义场景该走对话链路（问一句）")
        self.assertTrue(r["need_clarify"], "歧义场景必须标记 need_clarify")


class TestRulePathUntouched(unittest.TestCase):
    """⚠️ 规则路径**不许被抢** —— 有条款候选时必须走 RULE。"""

    def test_rule_candidate_always_wins(self):
        for q in ("读秒从几开始", "这种情况算吗", "他那样做可以吗", "比赛打几分"):
            with self.subTest(q=q):
                r = scope.classify(q, has_rule_candidate=True)
                self.assertEqual(r["route"], scope.RULE,
                                 "有条款候选时必须走规则链路，不许被范围闸门抢走")

    def test_pronoun_without_candidate_asks(self):
        """没条款候选时，带指代的该**问一句**而不是拒答。"""
        for q in ("这种情况算吗", "他一直那样我怎么办", "刚才那个算不算"):
            with self.subTest(q=q):
                r = scope.classify(q, has_rule_candidate=False)
                self.assertNotEqual(r["route"], scope.OFF_TOPIC,
                                    "带指代不该判离题")
                self.assertTrue(r["need_clarify"], "带指代该标记 need_clarify")


class TestOffDomainTasks(unittest.TestCase):
    """「提到了飞盘，但要的不是飞盘的事」⇒ 拒（owner 那条边界的核心）。"""

    def test_task_requests_refused_even_with_frisbee_word(self):
        for q in ("帮我用飞盘写首诗", "用飞盘写个故事", "帮我翻译这段飞盘文案",
                  "给我画个飞盘"):
            with self.subTest(q=q):
                self.assertEqual(
                    scope.classify(q)["route"], scope.OFF_TOPIC,
                    f"「{q}」是任务请求，不是飞盘问题 —— 提到了飞盘也不算")


class TestWordListsGrow(unittest.TestCase):
    """⚠️ 词表是**会长大的集合** —— 加词时最容易只修一边、坏另一边。"""

    def test_single_char_pan_does_not_misfire(self):
        """⚠️ 单字「盘」太泛（键盘/盘算/盘问）—— 必须挡住。"""
        for q in ("键盘坏了怎么办", "盘算一下这个月花了多少", "他盘问了我半天"):
            with self.subTest(q=q):
                self.assertEqual(
                    scope.classify(q)["route"], scope.OFF_TOPIC,
                    f"「{q}」里的「盘」不是飞盘的盘")

    def test_stats_are_nonempty(self):
        s = scope.stats()
        for k, v in s.items():
            with self.subTest(key=k):
                self.assertGreater(v, 0, f"{k} 是空的 —— 词表被清空了？")

    def test_emotion_words_cover_real_phrasings(self):
        """⚠️⚠️ 情绪词表要覆盖**球员真会怎么说**，不是照着词典收。

        【为什么单独立这条】2026-10-15 实测踩到一个真实误伤：
        ```
          `打得不好，有点不想练了` → 被判"与飞盘运动无关" →
          收到「这个我帮不上 —— 我只懂规则和场上怎么判。」
        ```
        **那是产品定义里最不能出的错**：一个已经"不想练了"的人被挡在门外。
        根因：表里有「不想**打**」，却**没有「不想练」** ——
        ⚠️ **漏一个词 = 漏一类人。**

        ⇒ 这条守卫钉住的不是某个具体词，而是**这个类别**：
          凡是"表达不想继续/难受/自责"的日常说法，都必须被认成情绪。
        """
        must_be_emotion = [
            "打得不好，有点不想练了", "不想练了", "不想打了",
            "最近很挫败", "有点不想去训练", "觉得自己拖后腿",
            "打不下去了", "很自责", "对不起队友",
            # 已有的那批也要继续认（别在补词时把旧的弄丢）
            "今天被打爆了，心情有点差", "被队友骂了，很郁闷", "输了，很难受",
        ]
        missing = [q for q in must_be_emotion if not scope.has_emotion(q)]
        self.assertEqual(
            missing[:5], [],
            f"这些情绪表达没被认出来（漏一个词 = 漏一类人）：{missing[:5]}")

    def test_emotion_words_do_not_swallow_rule_questions(self):
        """⚠️ 反向守卫：补情绪词**不许**把真规则题也吞进去。

        情绪类会被送到对话链路（接住 + 聊），规则题必须走严谨链路（引条款）。
        **吞错了的后果**：规则题不再引条款 ⇒ 用户拿不到可核查的依据。
        """
        must_not_be_emotion = [
            "读秒从几开始", "走步是怎么规定的", "盘出界了从哪里掷进来",
            "掩护犯规怎么判", "得分之后能换人吗", "什么情况下可以叫暂停",
            "两个人同时接住盘算谁的", "验盘是什么",
        ]
        wrong = [q for q in must_not_be_emotion if scope.has_emotion(q)]
        self.assertEqual(wrong[:5], [],
                         f"规则题被误判成情绪了：{wrong[:5]}")


@unittest.skipUnless(RUN_PAID_TESTS, "真实 embedding 集成测试；设置 RUN_PAID_TESTS=1 才跑")
class TestBeyondRules(unittest.TestCase):
    """⭕ **第五个出口**：跟飞盘有关，但我真的答不了（2026-10-15 加）。

    ## 为什么加这个守卫
    实测（真实控制流）13 条"跟飞盘有关但非规则"的提问里 **12 条**走了严谨规则链路
    硬答。owner 的产品定义是「我们不能让用户难过」——
    而把"我不知道"**包装成一段解释**，比直说不知道更让人难受。

    ## ⚠️ 这条守卫两侧都要守
    - 放太宽 ⇒ **有答案却说答不了**（本项目最严重的一类错）⇒ 所以"必答集"一个都不许中
    - 收太紧 ⇒ 域外题继续硬答 ⇒ 等于没修
    """

    @classmethod
    def setUpClass(cls):
        cls.r = Retriever()

    def _dw(self, q: str) -> str:
        """拿到检索时实际用的 `domain_word`。"""
        res = self.r.search(q, None, extra=None)
        return str(res.get("domain_word") or "")

    def test_must_answer_never_trips_it(self):
        """⭐ 真规则题**一个都不许**被判成"答不了"。

        ⚠️ 这里刻意包含**场地尺寸题**（`中心区多长` / `边线多长`…）——
           第一版判据（"命中全是泛章"）把它们**误拦**了，
           而它们是真有答案的（附录 A1.2、`verify_remote.py` 里就有这条检查）。
           把第 2 章当"泛章"是**分类错误**：场地尺寸是真正的规则内容。
        """
        must_answer = [
            "读秒从几开始", "走步是怎么规定的", "盘出界了从哪里掷进来",
            "接盘时踩线算出界吗", "掩护犯规怎么判", "得分之后能换人吗",
            "比赛中断之后怎么恢复", "什么情况下可以叫暂停",
            "数到十被判计满，但我觉得他数太快了", "走步", "读秒", "出界",
            "犯规", "暂停", "换人", "什么叫轴心脚", "捡盘要多久", "验盘是什么",
            # ⚠️ 场地尺寸题 —— 第一版判据的**受害者**，必须一直守在这里
            "中心区多长", "飞盘场地有多大", "场地多长", "边线多长", "得分区多深",
        ]
        for q in must_answer:
            with self.subTest(q=q):
                res = self.r.search(q, None, extra=None)
                ok, why = scope.is_beyond_rules(res.get("hits"),
                                                res.get("domain_word"), q)
                self.assertFalse(ok, f"「{q}」是规则题，却会被判成答不了 —— {why}")

    def test_off_scope_questions_are_caught(self):
        """⭐ 跟飞盘有关但非规则的，要能被认出来。

        ⚠️ 这份清单只用**原句**判，不经过 LLM 改写 —— 单测要确定性。
           实测差异（不是 bug，是已知边界）：
           `国内有哪些飞盘赛事` 用原句判 `domain_word = 飞盘赛事`（具体词）⇒ 判不出来；
           走真实链路时 LLM 会改写成更泛的说法 ⇒ 能判出来。
           ⇒ 判据的输入是"**检索实际用的那个查询**"，这句话在 `is_beyond_rules`
             的 docstring 里写清楚了；单测只覆盖原句那一路。
        """
        should_catch = [
            "办一场飞盘比赛要交多少报名费", "飞盘多少钱一个", "推荐个飞盘牌子",
            "飞盘运动是哪一年发明的", "怎么加入飞盘社团",
            "飞盘比赛有奖金吗", "飞盘怎么练反手",
        ]
        missed = []
        for q in should_catch:
            res = self.r.search(q, None, extra=None)
            if not scope.is_beyond_rules(res.get("hits"),
                                         res.get("domain_word"))[0]:
                missed.append(q)
        self.assertEqual(missed[:3], [],
                         f"这些域外题没被认出来：{missed}")

    def test_known_miss_is_documented(self):
        """⚠️ 记下**已知的漏**，免得以后有人以为它坏了。

        `打飞盘要穿什么鞋` 命中 `domain_word = 穿什么鞋`（具体词）⇒ 不会被拦。
        **但它其实不算错**：装备确实是规则第 3 章管的事，答它不违反边界。
        ⇒ 这条测试的作用是**把"已知漏"变成可执行的记录**：
          如果哪天它突然被拦了，说明判据收紧了，要回来看是不是误伤。
        """
        res = self.r.search("打飞盘要穿什么鞋", None, extra=None)
        ok, _ = scope.is_beyond_rules(res.get("hits"), res.get("domain_word"))
        self.assertFalse(ok, "这条以前是漏的，现在被拦了 —— 判据收紧了，回来看有没有误伤")


if __name__ == "__main__":
    unittest.main(verbosity=2)
