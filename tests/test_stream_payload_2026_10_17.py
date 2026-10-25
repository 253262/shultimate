# -*- coding: utf-8 -*-
"""守卫：**流式的 `done` 帧必须带齐"检索上下文"**（2026-10-17 加）。

## 这条守卫对应的真实事故（不是预防性设计）
owner 报：「提问之后点条款号（8.1 / 9.1 / 1.1 这种），本来应该在最右边
弹出那条规则原文，现在不显示」。

实测（本地 + 一个自托管的在线实例上双向复现）：
```
GET /api/ask        → citations: 8   ← 非流式，一切正常
/api/ask/stream done→ citations: 0   ← 线上真实用户在走的这条
done 帧的键只有: answer / glossary / mode / provider / usage
```
⇒ 前端 `citesRow()` 拿不到 `citations`：答案下面**没有条款芯片**；
`paintSources()` 拿不到 `citations`：右栏**永远**是
「这次回答没有引用条款原文」；点答案里的条款号 ⇒ 右栏打开了、**里面是空的**。

## 根因
`generator.answer_stream()` 只产出 5 个键（它只该管生成，不知道检索）。
2026-10-13 把流式分支接进 `handle_ask` 时，`done` 帧**直接透传**了那 5 个键，
没有合并服务端那半边（非流式出口手写了 20 个键，流式出口一个都没写）。

## 为什么原来的测试没发现
`tests/test_handle_ask_branches_2026_10_13.py` 的注释里写着
「另外流式那一路由 `tests/test_stream_parity_2026_10_13.py` 覆盖」——
**那个文件根本不存在**。于是 570+ 条单测全绿，而线上所有 LLM 答案的右栏都是空的。
⚠️ 教训：**注释里声称的覆盖，不等于覆盖。** 本文件就是补上那个洞。

## 守什么
1. 流式 `done` 帧里 `citations` 必须是**检索真的命中的那几条**（含 id）；
2. 流式 `done` 帧的**键集合**必须与非流式完全一致（同一个 `handle_ask`，
   只是出口形态不同 —— 少一个键就是一种"界面塌掉"的可能）；
3. 生成层产的键（answer / mode / provider / usage）不能被上下文覆盖；
4. 前端 `askStream` 仍然把 `meta` 帧的字段兜底并进 payload（第二道防线）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import server  # noqa: E402

# 流式答案的**真实形状**：生成层只产这 5 个键。
# ⚠️ 测试里刻意用"薄 payload"，而不是照着服务端写全 —— 这样只要服务端
#    不再合并上下文，本文件立刻报红（守的正是那个缺口）。
_THIN_DONE = {"answer": "（流式正文）", "mode": "llm", "provider": "stub-model",
              "glossary": "", "usage": {"prompt": 1, "completion": 2}}


class _StubGen:
    """够用的生成器替身：**故意让 `answer_stream` 只产薄 payload**。"""

    resolved: list = []
    available = False

    def chat(self, q, history=None, style_hint=""):
        return {"answer": "（闲聊替身）", "mode": "chat", "provider": None}

    def converse(self, q, purpose="", history=None, competition=None, style_hint=""):
        return {"answer": "（对话替身）", "mode": "conversation", "provider": None}

    def answer(self, q, hits, history=None, style_hint="", intent="rule", competition=None):
        return {"answer": "（非流式正文）", "mode": "llm", "provider": "stub-model"}

    def answer_stream(self, q, hits, history=None, style_hint="", intent="rule",
                      competition=None):
        yield "token", "（流"
        yield "token", "式正文）"
        yield "done", dict(_THIN_DONE)

    def rewrite_query(self, q):
        return ""

    def not_found_text(self, nearby, in_domain=True, text=""):
        return "（没找到）"


class _StubRetriever:
    """返回固定命中 —— 让"条款原文"真的有东西可发。"""

    def __init__(self, hits=None):
        self._hits = hits or []

    def search(self, q, top_k=None, extra=""):
        return {
            "hits": list(self._hits), "confident": True, "domain_word": "跨线防守",
            "best_cos": 0.63, "threshold": None, "elapsed_ms": 7,
            "nearby": [{"id": "18.1.1.2", "chapter_title": "违例与违规", "text": "（附近条款）"}],
            "routes": ["防守"], "paths": 2, "forced_count": 1,
            "competition": [],
        }


_HITS = [
    {"id": "8.1", "text": "8.1 开始比赛。", "chapter_title": "开始与重新开始",
     "type": "rule", "parent": None},
    {"id": "9.1", "text": "9.1 读秒。", "chapter_title": "读秒",
     "type": "rule", "parent": None},
    {"id": "1.1", "text": "1.1 飞盘精神。", "chapter_title": "飞盘精神",
     "type": "rule", "parent": None},
]

# ⚠️ 必须挑**不命中人工裁定库 / 闲聊 / 范围闸门**的问句，
#    否则走不到规则主路，这条守卫又变成"测了别的路"。
_Q = "跨线防守是怎么规定的"


class _QuerylogIsolated:
    """把埋点重定向到临时目录 —— 测试问题绝不能写进真实用户数据。"""

    def __enter__(self):
        from app import querylog as Q
        self._saved = Q.LOG_FILE
        self._td = tempfile.TemporaryDirectory(prefix="qlog_stream_")
        Q.LOG_FILE = str(Path(self._td.name) / "queries.jsonl")
        return self

    def __exit__(self, *exc):
        from app import querylog as Q
        Q.LOG_FILE = self._saved
        self._td.cleanup()
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = dict(server.STATE)
        server.STATE["generator"] = _StubGen()
        server.STATE["retriever"] = _StubRetriever(_HITS)
        self._iso = _QuerylogIsolated()
        self._iso.__enter__()

    def tearDown(self):
        self._iso.__exit__()
        server.STATE.clear()
        server.STATE.update(self._saved)

    def _stream_done(self, q=_Q):
        frames = list(server.handle_ask({"q": q}, stream=True))
        done = [d for k, d in frames if k == "done"]
        self.assertEqual(len(done), 1, "流式必须恰好产出一帧 done")
        return done[0]

    def _sync_payload(self, q=_Q):
        payload, code = server.handle_ask_sync({"q": q})
        self.assertEqual(code, 200)
        return payload


class TestStreamDoneCarriesRetrievalContext(_Base):
    def test_done_has_citations(self):
        """⚠️ 核心：线上那个 bug —— `done` 帧必须带条款原文。"""
        done = self._stream_done()
        self.assertIn("citations", done,
                      "流式 done 帧没有 citations ⇒ 答案下面的条款芯片会消失、"
                      "右栏永远是「没有引用条款原文」（2026-10-17 线上事故）")
        self.assertEqual([c["id"] for c in done["citations"]],
                         ["8.1", "9.1", "1.1"],
                         "citations 必须是检索真实命中的那几条，且顺序一致")
        # 右栏渲染要用到这几个字段，缺一个就会出现"卡片半空"
        first = done["citations"][0]
        for k in ("id", "text", "chapter_title", "source"):
            self.assertIn(k, first, f"citations 条目缺 {k}，右栏那张卡会渲染不全")

    def test_done_keys_identical_to_sync(self):
        """流式与非流式是**同一个 handle_ask 的两个出口** ⇒ 键集合必须相同。"""
        sync = self._sync_payload()
        done = self._stream_done()
        missing = sorted(set(sync) - set(done))
        extra = sorted(set(done) - set(sync))
        self.assertEqual(missing, [],
                         f"流式 done 帧少了这些键：{missing} —— 每一个都对应界面上的一块")
        self.assertEqual(extra, [], f"流式 done 帧多了这些键：{extra}")

    def test_context_fields_present(self):
        """逐个点名（键集合比对挂了时，这里能直接指出是哪一块塌了）。"""
        done = self._stream_done()
        for k in ("assistant", "intent", "rewritten", "citations", "nearby",
                  "confident", "best_cos", "threshold", "elapsed_ms", "routes",
                  "paths", "forced_count", "glossary", "web", "scope",
                  "llm_configured"):
            self.assertIn(k, done, f"流式 done 帧缺 {k}")
        self.assertEqual(done["scope"]["route"], "rule")
        self.assertTrue(done["nearby"], "nearby 也该带（前端用来显示「找过没找到」）")

    def test_generator_fields_win(self):
        """生成层产的 answer/mode/provider/usage 必须**优先**（它才知道）。"""
        done = self._stream_done()
        self.assertEqual(done["answer"], "（流式正文）")
        self.assertEqual(done["mode"], "llm")
        self.assertEqual(done["provider"], "stub-model")
        self.assertEqual(done["usage"], {"prompt": 1, "completion": 2})


class TestFrontendHasSecondLineOfDefence(unittest.TestCase):
    """前端要把 `meta` 帧的字段兜底并进 payload。

    ⚠️ 这不是重复劳动：服务端与前端是**两份产物**，历史上就出现过
      「后端改了、static/ 没传」。多这一层，字段再漏一次也不会变成空右栏。
    """

    def setUp(self):
        self.js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_meta_frame_is_merged(self):
        self.assertIn('k === "meta"', self.js,
                      "askStream 没有处理 meta 帧 ⇒ 少了一道兜底")
        self.assertIn("Object.assign({}, ctx, payload)", self.js,
                      "askStream 没有把 meta 帧的字段并进 done payload")

    def test_desktop_copy_in_sync(self):
        """桌面端打包的是 `desktop/static/` 那份副本，落后就等于没修。"""
        dst = ROOT / "desktop" / "static" / "app.js"
        if not dst.is_file():
            self.skipTest("desktop/static 不存在（未跑过 sync），跳过")
        self.assertEqual(dst.read_bytes(), (ROOT / "static" / "app.js").read_bytes(),
                         "desktop/static/app.js 与 static/app.js 不一致 —— "
                         "打包进去的仍是旧界面")


if __name__ == "__main__":
    unittest.main(verbosity=2)
