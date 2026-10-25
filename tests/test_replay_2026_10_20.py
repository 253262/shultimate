"""复盘页（2026-10-20）的守卫。

owner 这一轮加入复盘场景；2026-10-23 当前总目标见 docs/product.md：
  · 复盘是重要场景，但不是规则问答产品的唯一入口；
  · 第一屏不问候、不猜你想问，用户带着问题来；
  · 点「摆到场上」才把卡展开成场（惊喜由用户触发，不是产品塞的）；
  · 卡片上的条款必须透传 citations，**不能在这里另写一条链路**。

这些是"用户能看见的契约"，不是实现细节 —— 所以用源码守卫钉住。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


class TestReplayPage2026_10_20(unittest.TestCase):
    def test_replay_fallback_and_actual_chat_landing_are_explicit(self):
        m = re.search(r"function parseHash\(\) \{(.*?)\n\}", APP, re.S)
        self.assertIsNotNone(m, "找不到 parseHash")
        self.assertRegex(m.group(1), r'location\.hash \|\| "#/replay"',
                         "解析层的复盘兜底丢失")
        boot = APP.split("function bootUI()", 1)[1].split("\n}", 1)[0]
        self.assertIn('if (!location.hash) location.hash = "#/chat";', boot,
                      "正常空 hash 启动落点已变化，须同步产品入口决策与 ADR")

    def test_route_and_view_exist(self):
        view = re.search(r"const view = \{(.*?)\}\[route\.main\]", APP, re.S).group(1)
        self.assertIn("replay: viewReplayPage", view, "视图表缺 replay")
        self.assertIn("function viewReplayPage(", APP)
        meta = re.search(r"const PAGE_META = \{(.*?)\};", APP, re.S).group(1)
        self.assertIn("replay:", meta, "PAGE_META 缺 replay → 顶栏标题是 —")

    def test_first_screen_has_no_greeting(self):
        body = APP.split("function rpAskHTML(", 1)[1].split("\n}", 1)[0]
        for bad in ("今天", "有什么不明白", "我可以帮你", "猜你想问", "你好"):
            self.assertNotIn(bad, body, f"第一屏出现了问候/引导：{bad}")

    def test_chat_empty_state_has_intro_and_safe_examples(self):
        """聊天页空态要说明这是什么 + 每天换一组入口示例。

        owner 2026-10-22 推翻 2026-10-20 的"全删"：空态是大白板，太难看了。
        ⚠️ 但 2026-10-20 的两条约束仍然有效：
          ① 情绪那条（"今天被打爆了"）不许回来 —— 那是替用户决定情绪；
          ② 示例点了只填入输入框、不直接发送 —— 那是替他决定问什么。
        """
        self.assertIn("const HERO_SETS = [", APP)
        self.assertIn("function heroSetFor(", APP)
        self.assertIn("HERO_EPOCH", APP)
        self.assertIn("heroSetFor(new Date())", APP)
        sets = re.search(r"const HERO_SETS = \[(.*?)\n\];", APP, re.S)
        self.assertIsNotNone(sets, "找不到 HERO_SETS")
        self.assertEqual(sets.group(1).count("\n  ["), 8,
                         "首页题库不是 8 组 —— 组数要和「周末不重样」的设计对上")
        self.assertIn('"hero-h"', APP)
        # 2026-10-24 R1.1：示例按钮的类名从 v2 的 hero-eg 改为参考稿语义的
        # `question`；"只填入、不发送"的行为断言仍在下面两条。
        self.assertIn('el("button", "question")', APP, "首页示例按钮的类名/构造丢了")
        self.assertNotIn("今天被打爆了", APP)
        hero = APP.split("function heroNode()", 1)[1].split("\n}", 1)[0]
        self.assertIn("ta.value = q", hero, "示例没有填进输入框")
        self.assertNotIn("ask(", hero, "示例直接发送了 —— 应该只填入、不发送")
        # ⚠️ 只查聊天页：`#/settings/test`（召回测试）是开发者页，
        #    那里保留一个“读秒”示例是合理的，不能拿它当用户首屏。
        chat = APP.split("function viewChat(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("读秒读太快了", chat,
                         "聊天页输入框还把“读秒”示例当占位符")
        self.assertIn("用大白话说场上的情况", chat,
                      "聊天页输入框的占位符被删空了")

    def test_review_book_has_export_import(self):
        """复盘本必须能管理：搜索 / 导出 / 导入 / 删除。"""
        for s in ('id="rpBook"', "function rpOpenBook(", "function rpExportBook(",
                  "function rpImportBook(", "function rpDeleteBookItem(",
                  "review-book", "FileReader", "Blob"):
            self.assertIn(s, APP, f"复盘本缺：{s}")

    def test_card_unfolds_into_field(self):
        self.assertIn('id="rpField"', APP, "卡片上没有「摆到场上」")
        self.assertIn("function rpEnterField(", APP)
        self.assertIn('id="rpFieldSvg"', APP, "场上模式没有球场 SVG")
        self.assertIn('id="rpDot"', APP)
        self.assertIn("rpZoneAt", APP, "场上模式没有位置标签")

    def test_no_rule_is_a_designed_state(self):
        self.assertIn("规则没写。", APP)
        self.assertIn("不是我们不知道，是规则真的没写。", APP)
        self.assertIn(".rp-card.is-empty", CSS, "『没写』没有独立的视觉状态")

    def test_card_api_is_a_thin_wrapper(self):
        body = SERVER.split("def handle_card(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("handle_ask_sync(body)", body,
                      "handle_card 没有走唯一控制流 —— 它可能在另写一条链路")
        self.assertIn('payload.get("citations")', body, "citations 没有透传")
        self.assertIn('route == "/api/card"', SERVER, "/api/card 没有接进路由")
        # ⚠️ `is_judgment_question` 是纯启发式（不调模型），不算"另写链路"；
        #    这里禁的是**生成层调用**。2026-10-20 加「如果当时」时补的 kind 字段
        #    会用到它，所以不能笼统禁 `gen_mod.`。
        for bad in ("gen_mod.answer", "gen_mod.chat", "gen_mod.converse",
                    "gen_mod.branches", "Retriever(", "generate("):
            self.assertNotIn(bad, body, f"handle_card 里出现了 {bad} —— 它在另写链路")

    def test_card_next_step_is_extracted_not_generated(self):
        body = SERVER.split("def _card_next_step(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("return line[:120]", body)
        self.assertIn('return ""', body, "摘不到时必须空着，不能补一句")


if __name__ == "__main__":
    unittest.main()
