#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 data/sample_questions.jsonl 跑 5 个固定用例，打印可否验收。

用法：
    python scripts/verify_cases.py                    # 跑真实服务
    python scripts/verify_cases.py --base http://127.0.0.1:8001
    python scripts/verify_cases.py --selftest         # 不联网，用假响应自检判定逻辑

设计意图：**判条款号和边界类别，不判文字**。
但"判条款号"只认**结构化 citations**，不从回答正文里猜 —— 正文提到"7.12"
不等于系统真的把 7.12 作为依据返回了，那样会放过"编了条款号但没引用"的答案。

判定类型：
    must_cite      citations 必须包含 expect_any 之一
    out_of_scope   citations 必须为空，且回答明确表态"不在规则范围"，
                   且正文不得出现任何条款号（编造也算失败）
    no_number      citations 必须含 expect_any，回答说明正文未给数字，
                   且全文不得出现具体米/码数字

退出码：0 = 全部通过；1 = 有未通过；2 = 连不上服务或配置错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "data" / "sample_questions.jsonl"

# GBK/CP936 控制台下 `[×]`、`❌` 等符号会抛 UnicodeEncodeError 并以非零码退出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# 「第 17.6.1 条」「7.12 条」「见 9.1」这类正文里的条款号表述
CLAUSE_IN_TEXT = re.compile(r"(?:第\s*)?(\d{1,2}(?:\.\d{1,2}){1,4})\s*条")

# 明确表态"不在规则范围"
REFUSE_HINTS = ("没有规定", "没规定", "未规定", "不在规则", "不属于规则",
                "规则没有", "规则里没有", "无法回答", "超出", "不在本规则",
                "规则正文没有", "没有涉及", "不涉及", "不在讨论范围",
                "与规则无关", "不属于本规则", "帮不上", "只懂规则",
                "不在行", "不在我的")
# 明确说明"正文没给数字"
NONUMBER_HINTS = ("没有给出", "未给出", "没写", "没有具体", "未具体",
                  "正文没有", "图 1", "图1", "没有数字", "未标明", "未说明")
SIZE_RE = re.compile(r"\d+\s*(?:米|m\b|码|feet|ft\b)")


def load_cases() -> list[dict]:
    if not CASES.exists():
        print(f"[×] 找不到用例文件：{CASES}", file=sys.stderr)
        sys.exit(2)
    return [json.loads(ln) for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip()]


def post(base: str, path: str, payload: dict, timeout: float = 90) -> tuple[int, dict]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    # 显式空代理：本机若设了 HTTP_PROXY，访问 127.0.0.1 会被拦截
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:300]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def citation_ids(resp: dict) -> list[str]:
    """**只取结构化 citations**。不从 answer 正文里提取——那会放过没有真实引用的答案。"""
    out: list[str] = []
    for c in resp.get("citations") or []:
        if isinstance(c, dict) and c.get("id"):
            out.append(str(c["id"]))
    return out


def judge(case: dict, resp: dict) -> tuple[bool, str]:
    """返回 (通过?, 说明)。"""
    kind = case["judge"]
    ids = citation_ids(resp)
    answer = resp.get("answer") or ""
    want = case.get("expect_any") or []
    mentioned = CLAUSE_IN_TEXT.findall(answer)

    if kind == "must_cite":
        if not want:
            return False, "用例配置错误：must_cite 需要 expect_any"
        hit = [w for w in want if w in ids]
        if hit:
            return True, f"citations 命中 {hit[0]}"
        extra = f"；正文提到 {sorted(set(mentioned))[:3]} 但未被结构化引用" if mentioned else ""
        return False, f"citations 未命中 {want}；实际 {ids[:8] or '空'}{extra}"

    if kind == "out_of_scope":
        # 域外问题不该引用任何规则条款；正文里也不该出现条款号（编造）
        if ids:
            return False, f"域外问题却返回了引用 {ids[:5]}（不得强行引用）"
        if mentioned:
            return False, f"域外问题的回答里出现条款号 {sorted(set(mentioned))[:3]}（编造嫌疑）"
        if any(h in answer for h in REFUSE_HINTS):
            return True, "无引用、正文无条款号，且明确表态不在规则范围"
        return False, "无引用，但也没明确说不在规则范围"

    if kind == "no_number":
        hit = [w for w in want if w in ids]
        if not hit:
            return False, f"citations 未命中 {want}；实际 {ids[:8] or '空'}"
        size = SIZE_RE.search(answer)
        if size:
            return False, f"回答里出现具体尺寸「{size.group(0)}」——规则正文没有数字，属编造"
        if not any(h in answer for h in NONUMBER_HINTS):
            return False, f"命中 {hit[0]}，但没说明正文未给出尺寸"
        return True, f"citations 命中 {hit[0]}，且说明正文无尺寸"

    return False, f"未知 judge 类型：{kind}"


# ---------------------------------------------------------------- 自检
# 正例证明判据能过；负例证明判据能抓住错。二者缺一，自检就没意义。
SELFTEST = [
    # (说明, 用例, 假响应, 期望通过?)
    ("R1 citations 含 7.12", {"judge": "must_cite", "expect_any": ["7.12"]},
     {"answer": "应在砖点标记处确立轴心点。", "citations": [{"id": "7.12"}]}, True),
    ("R1 只给 7.9", {"judge": "must_cite", "expect_any": ["7.12"]},
     {"answer": "见 7.9。", "citations": [{"id": "7.9"}]}, False),
    # 负例：正文提到但没引用 —— 首轮实现会误判为通过
    ("R1 正文提 7.12 但 citations 为空", {"judge": "must_cite", "expect_any": ["7.12"]},
     {"answer": "按 7.12 条，你可以在砖点确立轴心点。", "citations": []}, False),
    # 负例：编了个不存在的条款号
    ("R1 citations 是编的 7.99", {"judge": "must_cite", "expect_any": ["7.12"]},
     {"answer": "见 7.99 条。", "citations": [{"id": "7.99"}]}, False),
    ("R4 明确拒答且无引用", {"judge": "out_of_scope", "expect_any": []},
     {"answer": "这不在规则范围内，规则没有涉及价格。", "citations": []}, True),
    # 负例：拒答词 + 编造条款
    ("R4 说没规定却引 99.9", {"judge": "out_of_scope", "expect_any": []},
     {"answer": "规则里没有规定。根据第 99.9 条……", "citations": [{"id": "99.9"}]}, False),
    # 负例：citations 空但正文编条款号
    ("R4 citations 空但正文编 99.9 条", {"judge": "out_of_scope", "expect_any": []},
     {"answer": "规则里没有规定，但第 99.9 条提到过。", "citations": []}, False),
    # 负例：只拒答不表态也不行
    ("R4 无引用也不表态", {"judge": "out_of_scope", "expect_any": []},
     {"answer": "这个问题比较有意思。", "citations": []}, False),
    ("R5 命中 2.1 且说明无尺寸", {"judge": "no_number", "expect_any": ["2.1"]},
     {"answer": "2.1 只说尺寸和分区见图 1，规则正文没有给出具体数字。",
      "citations": [{"id": "2.1"}]}, True),
    ("R5 编了米数", {"judge": "no_number", "expect_any": ["2.1"]},
     {"answer": "场地长 100 米、宽 37 米。", "citations": [{"id": "2.1"}]}, False),
    ("R5 命中但没说没数字", {"judge": "no_number", "expect_any": ["2.1"]},
     {"answer": "场地是长方形，详见 2.1。", "citations": [{"id": "2.1"}]}, False),
    ("R5 没命中 2.1", {"judge": "no_number", "expect_any": ["2.1"]},
     {"answer": "规则正文没有给出具体数字。", "citations": []}, False),
]


def run_selftest() -> int:
    bad = 0
    for label, case, resp, want in SELFTEST:
        got, why = judge(case, resp)
        ok = got == want
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else '❌ '}] {label:36s} 期望={want} 实得={got}  {why}")
    total = len(SELFTEST)
    negatives = sum(1 for *_x, w in SELFTEST if not w)
    print(f"\n{total - bad}/{total} 符合预期（其中负例 {negatives} 组，用于证明判据能抓错）")
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--selftest", action="store_true",
                    help="不联网，用内置假响应验证判定逻辑")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()

    cases = load_cases()

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(args.base.rstrip("/") + "/api/health", timeout=10) as r:
            health = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[×] 连不上 {args.base}：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("    服务起了吗？索引建好了吗？Key 配了吗？", file=sys.stderr)
        return 2

    print(f"服务 {args.base}  ready={health.get('ready')}  chunks={health.get('chunks')}  "
          f"llm_configured={health.get('llm_configured')}")
    if not health.get("llm_configured") and not health.get("ready"):
        print("[!] 服务报告未就绪，用例大概率全失败", file=sys.stderr)
    print()

    passed = 0
    for c in cases:
        status, resp = post(args.base, "/api/ask", {"q": c["question"], "probe": True})
        if status != 200:
            print(f"[未通过] {c['id']}  {c['question']}")
            print(f"         HTTP {status}: {resp.get('detail')}")
            print()
            continue
        ok, why = judge(c, resp)
        passed += 1 if ok else 0
        print(f"[{'通过' if ok else '未通过'}] {c['id']}  {c['question']}")
        print(f"         {why}")
        if not ok:
            print(f"         判据：{c.get('note', '')}")
            print(f"         回答前 160 字：{(resp.get('answer') or '')[:160]}")
        print()

    print("=" * 60)
    print(f"结果：{passed}/{len(cases)} 通过")
    print("提示：回答文字允许变化，验收只看结构化 citations 与边界判定。")
    print("      条款原文是否真的支持该回答，需人工抽查（见 docs/REPRODUCE.md §5）。")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
