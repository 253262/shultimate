"""范围分路守卫：明确的非规则诉求（2026-10-20）。

质量闸门 g025/g026 暴露：
  · 「队友总是不跑位，我该怎么跟他说」→ 检索给 12.x 候选 ⇒ 被判 rule
  · 「推荐一双飞盘鞋」→ 检索给 1.6/1.7 候选 ⇒ 被判 rule
两者都不是规则问题，模型只能在规则链路里说"这不在规则范围"。
修法：在 `has_rule_candidate` 之前认这类诉求，但让"算不算/怎么判/规则"一票否决。

另钉 g006：`发盘` 必须是域内具体概念，否则「盘要还给发盘的人，大家站哪」
会被 `is_beyond_rules` 判成"只命中裸盘"。
"""
import unittest

from app import routes, scope


class TestScopeRelatedRequest2026_10_20(unittest.TestCase):
    def test_communication_request_is_related(self):
        r = scope.classify("队友总是不跑位，我该怎么跟他说", has_rule_candidate=True)
        self.assertEqual(r["route"], scope.RELATED, r)

    def test_gear_recommendation_is_related(self):
        r = scope.classify("推荐一双飞盘鞋", has_rule_candidate=True)
        self.assertEqual(r["route"], scope.RELATED, r)

    def test_rule_question_still_wins(self):
        r = scope.classify("队友不跑位算不算越位", has_rule_candidate=True)
        self.assertEqual(r["route"], scope.RULE, r)

    def test_fa_pan_is_a_domain_concept(self):
        self.assertIn("发盘", routes.DOMAIN_WORDS)
        beyond, why = scope.is_beyond_rules(
            [{"id": "x"}] * 3, "盘", "盘要还给发盘的人，大家站哪")
        self.assertFalse(beyond, why)


if __name__ == "__main__":
    unittest.main()
