"""「如果当时」推演（2026-10-20）的守卫。

这个功能最像"编规则"的一步，所以钉四件事：
  ① 只对**判定题**开放（流程题/情绪题不推演，避免变成豆包式关心）；
  ② 条款原文必须由服务端按 id 从语料取，**不信任客户端传来的文本**；
  ③ 每条岔路必须绑定给定条款，绑不上就丢弃；
  ④ 入口在卡片上，且是**用户点一下才出现**，不自动生成。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import generator as gen_mod  # noqa: E402

SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
GEN = (ROOT / "app" / "generator.py").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


class TestJudgmentGate2026_10_20(unittest.TestCase):
    """入口判据：只有"算不算"类问题才配「如果当时」。"""

    def test_true_examples(self):
        yes = [
            "接盘的时候脚踩线了，算出界吗",
            "我在得分区里接盘，落地时脚踩在得分区外面，算得分吗",
            "盘在界外，我站在界内把它救回来，算不算出界",
            "我叫了暂停，但队友还没站稳，这算暂停成立吗",
            "读秒到8的时候我出手了，但盘还没离手就被拍掉了，怎么算",
        ]
        for q in yes:
            self.assertTrue(gen_mod.is_judgment_question(q), f"判定题被漏掉：{q}")

    def test_false_examples(self):
        no = [
            "开盘的时候，盘没飞过中线，我可以在哪里捡起来",
            "换人的时候，替补队员还没进场，场上少一个人，怎么办",
            "今天被打爆了，心情不好，规则上我该怎么调整",
        ]
        for q in no:
            self.assertFalse(gen_mod.is_judgment_question(q),
                             f"非判定题被放进来（会推出精神建议/相关规则）：{q}")


class TestBranchContract2026_10_20(unittest.TestCase):
    def test_prompt_hard_rules(self):
        p = gen_mod.BRANCH_PROMPT
        for s in ("只能使用", "最多 3 条", "不许引入", "同一个瞬间",
                  "不要硬推", "不许出现"):
            self.assertIn(s, p, f"推演提示词缺硬约束：{s}")

    def test_server_fetches_official_clauses(self):
        body = SERVER.split("def handle_branches(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("r.search(cid, top_k=1)", body,
                      "没有按 id 从语料取条款")
        self.assertIn("clauses.append(_cite(h))", body,
                      "没有把官方条款交给推演")
        self.assertNotIn('body.get("citations")', body,
                         "信任了客户端传来的条款原文 —— 可以伪造规则喂给模型")
        self.assertIn("is_judgment_question(q)", body,
                      "接口层没有判定题闸门")

    def test_branch_validation_drops_unknown_clause(self):
        body = GEN.split("def branches(", 1)[1]
        self.assertIn("if cid not in allowed", body, "没有校验 clause_id")
        self.assertIn("continue", body, "绑不上条款的岔路没有被丢弃")
        self.assertIn("这一步没有可推演的岔路", body,
                      "合法空结果没有专门文案")
        self.assertIn("timeout=20.0", body,
                      "推演超时不是 20s —— 实测 60s 会让人等 13s+")

    def test_card_has_kind(self):
        body = SERVER.split("def handle_card(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"kind"', body, "卡片没有 kind 字段")
        self.assertIn("is_judgment_question", body, "卡片没有用判定题判据")

    def test_json_parser_is_strict(self):
        self.assertEqual(gen_mod._json_obj('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(gen_mod._json_obj('前言 {"a": 1} 后语'), {"a": 1})
        self.assertIsNone(gen_mod._json_obj("不是 JSON"))


class TestBranchUI2026_10_20(unittest.TestCase):
    def test_button_is_user_triggered_and_gated(self):
        self.assertIn('id="rpBranches"', APP, "卡片上没有「如果当时」")
        self.assertIn('c.kind !== "procedure"', APP,
                      "流程题也会显示「如果当时」—— 会推出精神建议")
        self.assertIn('id="rpBr"', APP, "没有推演面板的挂载点")
        self.assertIn("function rpBranches(", APP)
        self.assertIn("function rpRenderBranches(", APP)
        self.assertIn("推演，不是判罚", APP, "缺少免责标签")

    def test_css_has_branch_styles(self):
        for s in (".rp-br{", ".rp-br-i{", ".rp-br-tag{", ".rp-br-need"):
            self.assertIn(s, CSS, f"推演样式缺：{s}")


if __name__ == "__main__":
    unittest.main()
