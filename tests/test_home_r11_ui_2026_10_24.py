# -*- coding: utf-8 -*-
"""首页 R1.1 结构 / 动效 / 字级守卫（DS-HOME-R11，2026-10-24）。

来历：2026-10-23 的 v2 守卫（本文件原内容）锁的是黑场图版、CASE 014、
`heroDiscFlight` 等**旧视觉结构**。本轮 Owner 决定改为 GPT R1.1 的构图与
飞盘动效，那些类名和 keyframes 已从实现中移除。
⇒ 按任务要求，不为了"让旧类名断言变绿"而往 DOM 里塞无效锚点；
  这里改为锁 R1.1 的真实结构 + 真实行为（几何来源、只播一次、清理函数、
  字级下限、容器查询）。旧 v2 视觉结构不再保留。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static" / "app.js"
CSS_PATH = ROOT / "static" / "app.css"


def strip_js_comments(source: str) -> str:
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def strip_css_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


class TestHomeR11Ui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
        cls.css = strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))

    def test_hero_has_r11_hierarchy(self):
        for class_name in (
            "hero-grid",
            "hero-copy",
            "hero-proof",
            "flight-figure",
            "flight-art",
            "flight-path",
            "flight-disc",
            "flight-replay",
            "question",
            "question-number",
            "question-arrow",
        ):
            self.assertIn(f'"{class_name}"', self.js, f"首页缺少 .{class_name}")

    def test_figure_geometry_comes_from_reference_not_redrawn(self):
        # viewBox 与路径 d 是参考稿的几何真源；改图必须同步改这里。
        self.assertIn('viewBox="0 0 500 410"', self.js)
        self.assertIn('d="M42 303C136 294 116 94 350 117"', self.js)
        self.assertIn('matrix(1 -.25 .52 .62 -29 241)', self.js)
        self.assertRegex(self.js, r'flight-art[\s\S]{0,160}aria-hidden="true"')

    def test_figure_carries_no_rule_judgement(self):
        """首页图形只是装饰：不写条款号、CASE 编号或"已核对"。"""
        block = self.js.split("function heroNode()", 1)[1].split("\n}\n\nfunction citesRow", 1)[0]
        for bad in ("CASE", "11.4.2", "已核对", "条款"):
            self.assertNotIn(bad, block, f"首页 hero 里出现了规则判定痕迹：{bad}")

    def test_examples_keep_button_semantics_and_only_fill_input(self):
        hero = self.js.split("function heroNode()", 1)[1].split("\n}\n\nfunction citesRow", 1)[0]
        self.assertIn('b.type = "button"', hero)
        self.assertRegex(hero, r"forEach\(\(q,\s*i\)")
        self.assertIn("padStart(2", hero)
        self.assertIn("ta.value = q", hero, "示例没有填进输入框")
        self.assertNotIn("ask(", hero, "示例直接发送了 —— 应该只填入、不发送")

    def test_intro_only_plays_once_per_browser_session(self):
        self.assertIn("shultimate.heroIntroSeen", self.js)
        self.assertIn("sessionStorage.getItem", self.js)
        self.assertIn("sessionStorage.setItem", self.js)
        self.assertIn('"is-intro"', self.js)
        self.assertIn('"is-settled"', self.js)

    def test_flight_samples_the_path_and_uses_waapi(self):
        fn = self.js.split("function bindHeroFlight(", 1)[1].split("\n}\n\nfunction heroNode", 1)[0]
        self.assertIn("getPointAtLength", fn)
        self.assertIn("getTotalLength", fn)
        self.assertIn("disc.animate(", fn, "飞盘不是 WAAPI 帧动画")
        self.assertIn("duration: 1200", fn)
        self.assertIn("delay: 100", fn)
        self.assertIn("cubic-bezier(.23,1,.32,1)", fn)
        self.assertIn("delay: 160", fn, "路径描边必须比飞盘晚 160ms")
        self.assertNotIn("setInterval", fn, "不许用逐帧 JS 重写飞行")

    def test_lifecycle_cleanup_is_wired(self):
        self.assertIn("function disposeHero(", self.js)
        self.assertIn("return function cleanup()", self.js)
        self.assertIn('removeEventListener("click"', self.js)
        self.assertIn("removeEventListener(\"change\"", self.js)
        # 切路由 / 重画对话 / 首次提问，这三条路径都必须拆旧动画。
        self.assertGreaterEqual(self.js.count("disposeHero()"), 4,
                                "卸载路径没接全：路由 / paintChat / ask 至少要清理")

    def test_reduced_motion_stops_at_path_end(self):
        fn = self.js.split("function bindHeroFlight(", 1)[1].split("\n}\n\nfunction heroNode", 1)[0]
        self.assertIn("reduceQ.matches", fn)
        self.assertIn("settle()", fn)
        self.assertIn("duration: 160", fn)
        reduced = re.search(
            r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{([\s\S]*?)\n\}",
            self.css,
        )
        self.assertIsNotNone(reduced)
        self.assertIn(".hero.is-intro", reduced.group(1))
        self.assertIn("160ms", reduced.group(1))

    def test_pointer_hover_and_keyboard_focus_are_explicit(self):
        self.assertRegex(
            self.css,
            r"@media\s*\(hover\s*:\s*hover\)\s*and\s*\(pointer\s*:\s*fine\)"
            r"[\s\S]*?\.hero \.question:hover",
        )
        self.assertIn(".hero .question:focus-visible", self.css)
        self.assertIn(".flight-replay:hover", self.css)

    def test_responsive_uses_actual_container_width(self):
        # 生产侧栏 280px：主区宽度 ≠ viewport，所以折叠判据必须是容器宽度。
        self.assertIn("container-type:inline-size", self.css)
        # R11.1：640–760 紧凑两栏，<640 才单列；560 的示例单列保留。
        self.assertRegex(
            self.css,
            r"@container\s*\(min-width:640px\)\s*and\s*\(max-width:760px\)",
        )
        self.assertRegex(self.css, r"@container\s*\(max-width:639\.98px\)")
        self.assertIn("@container (max-width:560px)", self.css)
        self.assertIn("@supports not (container-type:inline-size)", self.css,
                      "老浏览器没有兜底，窄屏会硬挤两栏")

    def test_compact_desktop_keeps_two_columns_and_shrinks_proportionally(self):
        """R11.1 P2：640–760 是紧凑桌面，不是单列；不能靠缩小功能字。"""
        compact = re.search(
            r"@container\s*\(min-width:640px\)\s*and\s*\(max-width:760px\)\s*"
            r"\{([\s\S]*?)\n\}",
            self.css,
        )
        self.assertIsNotNone(compact, "找不到 640–760 紧凑桌面容器查询")
        body = compact.group(1)
        self.assertRegex(body, r"font-size:clamp\(38px,6\.1cqw,46px\)")
        self.assertRegex(body, r"\.flight-art\{height:240px;\}")
        self.assertRegex(body, r"\.hero \.questions\{margin-top:26px;\}")
        self.assertNotIn("grid-template-columns:1fr", body,
                         "紧凑桌面被写成了单列")
        # 720×700 短窗口：26px 上距会让第一条示例差 7px，允许局部收到 18px。
        self.assertRegex(
            self.css,
            r"@media\s*\(max-height:720px\)\s*\{\s*@container\s*"
            r"\(min-width:640px\)\s*and\s*\(max-width:760px\)\s*\{"
            r"[\s\S]{0,120}margin-top:18px",
        )

    def test_scrollbar_is_visible_and_aligned_with_stable_gutter(self):
        """R11.1 P2：溢出时必须有细滚动条，不能再用 display:none / width:0 藏掉。"""
        inner = re.search(r"\.chat\.empty \.stream \.inner\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(inner)
        self.assertNotIn("scrollbar-width:none", inner.group(1))
        self.assertNotRegex(self.css, r"\.chat\.empty \.stream \.inner::-webkit-scrollbar"
                                      r"\s*\{[^}]*width\s*:\s*0")
        self.assertIn("scrollbar-gutter:stable", self.css)
        self.assertRegex(self.css, r"min-width:1001px\)[\s\S]{0,200}padding-right:15px")
        self.assertRegex(self.css, r"max-width:1000px\)[\s\S]{0,200}padding-right:6px")

    def test_readability_floor_is_not_shrunk_on_mobile(self):
        def block(selector):
            m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
            self.assertIsNotNone(m, f"找不到 {selector}")
            return m.group(1)

        self.assertRegex(block(".hero .question"), r"font-size:14px")
        self.assertRegex(block(".composer-label"), r"font-size:12px")
        self.assertRegex(block(".field textarea"), r"font-size:16px")
        self.assertIn("color:#697260", block(".field textarea::placeholder"))
        self.assertRegex(block(".hero-eyebrow"), r"font-size:12px")
        self.assertRegex(block(".hero .question-number"), r"font-size:12px")
        # 手机断点里输入仍是 16px（不许为了塞进首屏压小）
        mobile = re.search(r"@media\s*\(max-width:600px\)\s*\{([\s\S]*?)\n\}", self.css)
        self.assertIsNotNone(mobile)
        self.assertIn(".field textarea{font-size:16px;}", mobile.group(1))

    def test_v2_visual_layer_is_gone(self):
        for bad in ("field-stage", "field-cite", "hero-case", "hero-kicker",
                    "heroDiscFlight", "heroPathReveal", "heroPulse",
                    "is-tracing", "manual-paper", "bindHeroParallax("):
            self.assertNotIn(bad, self.css, f"v2 旧视觉层还在 CSS 里：{bad}")
        for bad in ("bindHeroParallax(", "replayTrace(", "CASE 014"):
            self.assertNotIn(bad, self.js, f"v2 旧逻辑还在 JS 里：{bad}")

    def test_empty_homepage_removes_glass_and_card_shadows(self):
        self.assertRegex(
            self.css,
            r"\.field\s*\{[^}]*box-shadow\s*:\s*none",
        )
        self.assertRegex(
            self.css,
            r"\.field\s*\{[^}]*border-radius\s*:\s*8px",
        )

    def test_no_transition_all_is_added(self):
        hero_blocks = "\n".join(
            block
            for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", self.css)
            if ".hero" in selector
        )
        self.assertNotRegex(hero_blocks, r"transition\s*:\s*all\b")


if __name__ == "__main__":
    unittest.main()
