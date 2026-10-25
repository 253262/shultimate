# -*- coding: utf-8 -*-
"""HTTP 服务：把检索内核 + 生成层暴露成接口，并托管前端页面。

**零第三方依赖**（标准库 http.server），因此：
    python app/server.py
就能直接跑起来，不需要 pip install 任何东西。

监听地址由环境变量控制（见 app/config.py）：
    HOST=127.0.0.1  PORT=8000   # 默认，仅本机
    HOST=0.0.0.0    PORT=8000   # 部署到服务器时对外提供

接口：
    POST /api/ask      {q, top_k?, history?, assistant?, web?} -> 回答 + 引用条款
    POST /api/search   {q, top_k?}           -> 只检索（调检索质量用）
    POST /api/feedback {q, rating, reason?, comment?, ...} -> 记录评分反馈
    GET  /api/feedback                        -> 原因选项口径 + 反馈统计
    GET  /api/curated                         -> 疑问库列表 + 统计
    POST /api/curated {action:add|delete, ...} -> Owner 写入/撤掉裁定
    GET  /api/stats                           -> 语料统计 + 模型状态
    GET  /api/chapters                        -> 知识库结构（章节 -> 条款）
    GET  /api/assistants                      -> 助手预设清单
    GET  /api/settings                        -> 运行参数（只读，含用途说明）
    GET  /api/files                           -> 源数据文件清单
    GET  /api/glossary                        -> 术语表
    GET  /api/health                          -> 健康检查
    GET  /api/update                          -> 桌面客户端的更新清单
    GET  /dl/<安装包文件名>                     -> 下载更新包（支持 Range 断点续传）
    GET  /                                    -> 前端页面
    GET  /<静态资源>                           -> static/ 下的 css / js 等

**知识源优先级（别搞反，前面命中就不往后走）**
    ⓪ 轻量对话  寒暄 / 元问题 / 问助手本身（`app/smalltalk.py`）→ 不检索，一两句自然回答
    ① 疑问库   Owner 亲自回答过的  →  直接返回，不检索不生成
    ② 规则语料  368 条 WFDF 规则   →  检索命中才让大模型作答
    ③ 联网兜底  官方 PDF 英文原文  →  只在前两层都救不了时，**单独标注**
第 ③ 层刻意不进 citations：网络内容不能伪装成规则原文（见 app/weblookup.py）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import functools
import json
import socket
import threading
import time
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# GBK/CP936 控制台下部分符号会抛 UnicodeEncodeError，导致进程以非零码退出。
# 降级为替换编码：服务本身的收发仍是 UTF-8，只有终端打印会退化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from app import assistants as assistants_mod  # noqa: E402
from app import capacity  # noqa: E402
from app import config  # noqa: E402
from app import curated as curated_mod  # noqa: E402
from app import generator as gen_mod  # noqa: E402
from app import intent as intent_mod  # noqa: E402
from app import practice as practice_mod  # noqa: E402
from app import querylog as querylog_mod  # noqa: E402
from app import scope as scope_mod  # noqa: E402
from app import smalltalk as smalltalk_mod  # noqa: E402
from app import weblookup as weblookup_mod  # noqa: E402
from app.retriever import Retriever  # noqa: E402


def _capacity_guard(fn):
    """流式入口的容量闸门：拿不到名额就 429，不进入 SSE。

    ⚠️ 放在方法外层而不是方法体里：`_ask_stream` 有一串早退分支，
       逐条加 try/finally 很容易漏释放；装饰器只写一次，所有路径都覆盖。
    """
    @functools.wraps(fn)
    def wrapper(self, body):
        try:
            with capacity.request_budget():
                with capacity.REQUEST.slot():
                    return fn(self, body)
        except capacity.Overloaded as exc:
            self._send_json({"detail": str(exc), "mode": "overloaded",
                             "retry_after": exc.retry_after}, 429)
    return wrapper


def _paid_guard(fn):
    """给**没有自己闸门**的付费入口（/api/search、/api/branches）加闸门。

    ⚠️ 只包这两个：handle_ask_sync 自己已经拿了 REQUEST.slot()，
       再包一层会因 BoundedSemaphore 不可重入而自己排队到超时。
    """
    @functools.wraps(fn)
    def wrapper(body):
        try:
            with capacity.paid_slot():
                return fn(body)
        except capacity.Overloaded as exc:
            return {"detail": str(exc), "mode": "overloaded",
                    "retry_after": exc.retry_after}, 429
    return wrapper

HOST = config.HOST
PORT = config.PORT

# ---------- 桌面客户端的更新通道 ----------
# 更新包和语料完全无关，单独一个目录，且**不放进 git**
# （data/update 在 .gitignore 里，77MB 的 exe 不该进仓库历史）。
UPDATE_DIR = config.DATA_DIR / "update"
MANIFEST = UPDATE_DIR / "latest.json"

STATE: dict = {"retriever": None, "generator": None, "boot_error": "", "started_at": 0.0,
               # 练习器题库：**进程内缓存**（由 rules.jsonl 确定性派生，见 app/practice.py）
               "quiz": None}

# `_static_build_id()` 的缓存：((路径, mtime_ns, size), 哈希)
_BUILD_CACHE: tuple | None = None


def _static_build_id() -> str:
    """静态文件内容的指纹 —— 前端用它判断"服务端是不是换过了"。

    ⚠️ 为什么要它（2026-10-15）：
      owner 问「我换了 UI，用户收不到不就白做了吗」。
      静态文件本身是 `no-cache` 的，**用户刷新确实立刻就能看到**；
      但如果用户**不关窗口**，那个 Chrome --app 窗口里跑的还是
      几分钟前加载的 JS ⇒ 他会一直看旧界面，而**我们这边毫无察觉**。
      ⇒ 前端定期问一次 health，比对 build：
        build 变了 = 服务端换了 UI/JS ⇒ 提示"有新版本，点这里刷新"。

    ⚠️ 为什么用**内容哈希**而不是 `config.APP_VERSION`：
      后者要**人工记得改**。人会忘，忘了用户就永远收不到提示。
      内容哈希是自动的：文件一变它就变。
      （这也是本项目反复学到的：**别依赖"人记得"**。）

    ⚠️ 缓存按 (路径, mtime_ns, size)：没有它的话每次 /api/health 都要
      重读三个文件（约 320KB），而这个接口会被前端**定期轮询**。
    """
    global _BUILD_CACHE
    parts: list[str] = []
    for name in ("app.js", "app.css", "index.html"):
        p = config.STATIC_DIR / name
        try:
            st = p.stat()
            key = (str(p), st.st_mtime_ns, st.st_size)
        except OSError:
            parts.append(name + ":missing")
            continue
        if _BUILD_CACHE and _BUILD_CACHE[0] == key:
            parts.append(_BUILD_CACHE[1])
            continue
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except OSError:
            h = "unreadable"
        _BUILD_CACHE = (key, h)
        parts.append(h)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def load_manifest() -> dict:
    """读更新清单。

    读不到 / 坏掉都返回空 dict —— 更新通道是**附加能力**，
    它坏了不该让整个问答服务 500。客户端拿到空清单的表现是
    "服务器还没发布过更新"，这是对的：确实等于没有可更新的东西。

    ⚠️⚠️ 2026-10-15 修了一个**会让自动更新彻底失效**的 bug：
      原来这里只校验 `installer` 字段：
    ```python
    inst = data.get("installer") or {}
    if not name or not (UPDATE_DIR / name).is_file():
        return {}          # ← 整个清单被丢掉
    ```
      而启动器更新写的是 **`launcher`** 字段、**没有 `installer`** ⇒
      走到这里 `name` 是空的 ⇒ **返回 {}** ⇒ `/api/update` 报 404 ⇒
      **客户端永远收不到更新，而 publish_launcher.py 会一路打印成功。**

      ⇒ 现在**两个字段分别校验**：哪个的文件不在了就摘掉哪个，
        **而不是把整个清单丢掉**（不然一个包的缺失会连带另一个也失效）。
    """
    try:
        if not MANIFEST.is_file():
            return {}
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # ⚠️ 逐字段校验：文件不在的那个字段摘掉，保留另一个。
    #    否则客户端会去下一个 404，弹一句"更新失败"，
    #    比"没有更新"更让人困惑。
    for key in ("installer", "launcher"):
        entry = data.get(key)
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or ""
        if not name or not (UPDATE_DIR / name).is_file():
            data.pop(key, None)
    # 两个包都不在 ⇒ 等于没发布过
    if not data.get("installer") and not data.get("launcher"):
        return {}
    return data


# 请求体上限。反馈正文最长 4000 字、history 最多 20 条，
# 1MB 已是几十倍余量；真正要挡的是"随手一个 Content-Length: 1e9"。
MAX_BODY = 1 << 20

# ⭕ 「圈外」两种拒答的话术（2026-10-13 加，owner 定的边界）。
# ⚠️ 两段话**必须分开**：把它们合成一句会让"求帮助伤害别人"和
#    "问天气"得到同样温和的回应，那等于没设边界。
# ⚠️ HARM 那段**不说教、不追问细节** —— 追问等于在帮他完善那件事。
HARM_TEXT = (
    "这个我不接。\n\n"
    "我只能聊打飞盘这件事 —— 规则、场上怎么处理、队友之间的事都可以问。")
OFF_TOPIC_TEXT = (
    "这个不在我管的范围里 —— 我只懂飞盘这件事。\n\n"
    "场上遇到拿不准的情况、或者和队友、训练、装备有关的事，都可以问我。\n"
    "你问的这个，交给豆包这类通用助手会更合适。")

# ⭕ 第三种：「跟飞盘有关，但我真的答不了」（2026-10-15 加）。
#
# 【为什么必须和上面两段分开】三段的**用户处境完全不同**：
#   · HARM       —— 他在求帮助伤害别人 ⇒ 硬拒，不说教
#   · OFF_TOPIC  —— 他问的东西跟飞盘无关（天气、周报）⇒ 友好拒 + 推荐别的工具
#   · 这一段      —— **他问的是飞盘的事**（报名费多少、哪儿有场地、买什么牌子的盘），
#                   只是那不属于规则 ⇒ **绝不能让他觉得"我问的东西不配被回答"**
# ⚠️ 所以这段话里**不许出现"不在我管的范围"** —— 那正是 OFF_TOPIC 的措辞，
#   用在这里会让他以为"问飞盘的事也被嫌弃了"。
# ⚠️ 也**不许说"规则里没有"** —— 那暗示"规则里本该有，是我们漏了"，
#   而实际上这种事本来就不该由规则管。
#
# 【实测的错法（修之前它就是这么答的）】
#   问"办一场飞盘比赛要交多少报名费"，它答：
#   「报名费不在规则范围内，这些条款里没有规定这一点。规则管的是场上怎么打、
#     怎么判…赛事收多少钱属于主办方的竞赛规程和报名通知，得…」
#   ⇒ **把"我不知道"包装成了一段解释**。用户会觉得它在装傻、或者没听懂他。
#   owner 的原话：「我们不能让用户难过。」这一条正是那种"更难过"。
#
# 【这段话要做什么】三件事，缺一不可：
#   ① **认下这件事本身**（不说"不在我管的范围"，而是"这个我帮不上"）
#   ② **说清我擅长什么**（而且用他的话：场上那些拿不准的事）
#   ③ **给他一个能接着问的方向** —— 不是敷衍的"换种说法"，是**具体的**
BEYOND_RULES_TEXT = (
    "这个我帮不上 —— 我只懂规则和场上怎么判。\n\n"
    "但你既然在打飞盘，可能有几件事我能帮上：\n"
    "· 场上拿不准的判罚（读秒、走步、出界、犯规怎么算）\n"
    "· 跟对手或队友争起来了，规则到底站哪边\n"
    "· 刚打完一场，心里有口气没顺 —— 也可以说说")

# 静态资源的 MIME（只放行前端真正用到的类型，未知类型一律拒绝）
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    # 官方注释的扫描页（2026-10-18）：原图直接展示，不转录 —— 见 _chapters() 的说明。
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff2": "font/woff2",
}


def boot() -> None:
    STATE["generator"] = gen_mod.Generator()
    STATE["started_at"] = time.time()
    try:
        STATE["retriever"] = Retriever()
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 2026-10-24：异常原文（可能含服务器绝对路径/上游细节）只进日志，
        # 公网 /api/health 与 503 响应只给通用话术。
        traceback.print_exc()
        STATE["boot_error"] = "服务未就绪（索引或配置问题）"
    # 练习器题库：**独立于检索索引**，索引坏了它照样能用
    # （它只读 data/rules.jsonl，不碰向量）。所以单独 try，不跟上面共用。
    try:
        STATE["quiz"] = practice_mod.build()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        STATE["quiz"] = []


def quiz_pool() -> dict[str, dict]:
    """题目 id -> 题（含答案）。判分时按 id 查回，**服务端不存状态**。"""
    return {q["id"]: q for q in (STATE.get("quiz") or [])}


def quiz_public(q: dict) -> dict:
    """下发前只保留题面需要的字段 —— **白名单**，不是黑名单。

    2026-10-24：原来只去掉 `answer`，但同一批数据里还有 `why` / `explain` /
    `evidence` / `rule_text`，它们都直接说明正确选项（实测
    `s9_5_5_2.explain` 第一句就是"这句话是错误的"）。
    公开 GET 接口等于把答案一起发出去，练习器"服务端判分"的前提就没了。
    """
    return {k: q[k] for k in ("id", "kind", "prompt", "options") if k in q}


@_paid_guard
def handle_practice(body: dict) -> tuple[dict, int]:
    """判分：`{id, choice}` -> `{ok, correct, explain, clauses}`。

    ⚠️ 为什么判分放服务端、而不是把答案发给前端自己判：
      前端判分 = **答案随题面一起下发**，打开开发者工具就能看到，
      练习就失去意义。代价是每答一题一次请求 —— 可接受（本地/局域网毫秒级）。
    ⚠️ **服务端不存状态**：题 id 是确定性的（见 `practice._qid`），
      所以按 id 在题库里查回即可。多标签页、刷新、重启都不会错位。
    """
    qid = str(body.get("id") or "").strip()
    if not qid:
        return {"detail": "缺少题目 id"}, 400
    q = quiz_pool().get(qid)
    if q is None:
        # 题库是启动时派生的；服务重启后 id 不变，所以"查不到"只可能是
        # 题号被改过、或题库没起来。给明确文案而不是静默判错。
        return {"detail": "没找到这道题（题库可能已重新生成，请刷新再试）"}, 404

    correct = q["answer"]
    choice = str(body.get("choice") or "").strip()
    if not choice:
        return {"detail": "还没有选择"}, 400

    ok = choice == correct
    r = STATE.get("retriever")
    cites = []
    if r is not None:
        for cid in (q.get("clauses") or []):
            for h in (r.search(cid, top_k=1)["hits"] if r else []):
                if h["id"] == cid:
                    cites.append(_cite(h))
                    break
    # 正确答案的**可读形式**（选项文字），让前端不必自己反查
    correct_text = ""
    for o in (q.get("options") or []):
        if o.get("key") == correct:
            correct_text = o.get("text") or ""
            break
    return {
        "ok": ok,
        "correct": correct,
        "correct_text": correct_text,
        "chosen": choice,
        "kind": q.get("kind"),
        "clauses": q.get("clauses") or [],
        "citations": cites,
        # ⚠️ 2026-10-15：场景题必须把「为什么」带回去。
        #    二选一蒙对的概率是 **50%** —— 答对这件事本身说明不了什么，
        #    **真正的价值在答完之后那两句解释**。
        #    前端原来只在**答错时**才展示依据；场景题的 explain/why/evidence
        #    三个字段**对错都要显示**（前端已同步改）。
        "explain": q.get("explain") or "",
        "why": q.get("why") or "",
        "evidence": q.get("evidence") or "",
    }, 200


def clamp_top_k(v) -> int | None:
    """把客户端传来的 top_k 夹进合法区间；None/空表示"用默认值"。

    ⚠️ 为什么不直接 `int(top_k)`（2026-10-07 修）：那样做实测有三种坏结果 ——
      "abc"    → ValueError 冒到顶层，用户看到 **500** 而不是自己的输入有问题；
      -5       → 负数切片生效，**返回 408 条**（全库倒着下发）；
      100000   → 一次性把 413 条全给，单响应 242KB。
    三种都是把内部实现的意外行为漏给用户，不是"友好地报错"。
    夹取而不是拒绝，是因为 top_k 只影响"给模型看多少条候选"，
    夹到边界对用户完全够用 —— 没有理由为它打断一次正常提问。
    """
    if v is None or v == "":
        return None
    try:
        n = int(float(v))          # 容忍 "8" / 8.0 / "8.7"
    except (TypeError, ValueError, OverflowError):
        # "inf" / "1e999" 走的是 OverflowError，不是 ValueError
        # （2026-10-24 实测：漏了它会把 /api/ask 打成 500）。
        return None                # 解析不了就用默认值，不算错
    return max(config.MIN_TOP_K, min(config.MAX_TOP_K, n))


def clean_query(body: dict) -> tuple[str, str | None]:
    """取出并清洗问题与历史。返回 (q, err)。

    长度上限是实测定的：10 万字的问题会被原样送去 embedding，
    智谱直接 400，用户看到一句莫名其妙的 500；而 2000 字又能过
    —— 也就是说超长输入白消耗一次付费调用，还会把改写缓存灌成垃圾行。
    """
    q = str(body.get("q") or "").strip()
    if not q:
        return "", "问题不能为空"
    if len(q) > config.MAX_QUERY_CHARS:
        return "", f"问题太长了（{len(q)} 字）。规则问题用大白话写一句就够了，请精简到 {config.MAX_QUERY_CHARS} 字以内。"
    # ⚠️⚠️ **history 必须在入口净化，而且要"就地生效"**（2026-10-13 审计发现的 500）。
    #
    # 【原来的写法是死代码】
    #   ```
    #   raw_hist = body.get("history")
    #   hist = None
    #   if isinstance(raw_hist, list) and raw_hist:
    #       hist = [h for h in raw_hist[:MAX_HISTORY] if isinstance(h, dict)]
    #   return q, None          # ← `hist` 从来没被 return 出去，也没写回 body
    #   ```
    #   ⇒ 守卫**算了但没用**，调用方拿到的仍是原始值。
    #   「检查了但没生效」比没有检查更坏 —— 它让人以为这里安全了。
    #
    # 【实测的三个 500】（`tools/audit_probe.py`）
    #   `history:"x"`      → 字符串被当列表迭代，字符进生成层 → `h.get` 崩
    #   `history:{"..."}`  → dict 没有切片语义 → 崩
    #   `history:["x"]`    → **字符串通过了 isinstance(h, dict) 那层？不** ——
    #                        是它被原样传下去，生成层逐个 `.get()` 崩
    #   ⇒ 三条都让**整个问答 500**，用户拿不到任何答案。
    #
    # 【为什么静默清理而不是 400】
    #   history 是**辅助上下文**，不是用户的问题本身。
    #   为了一个格式不对的 history 把他真正想问的问题拒掉，是过度严格。
    #   ⇒ 清成可用的形态，照常回答。
    raw_hist = body.get("history")
    clean_hist: list[dict] = []
    if isinstance(raw_hist, list):
        for h in raw_hist[-config.MAX_HISTORY:]:
            if not isinstance(h, dict):
                continue
            role = h.get("role")
            content = h.get("content")
            # ⚠️ role 白名单：只认 user/assistant。别的值进了提示词等于
            #    让外部输入扮演 system —— 那是**提示词注入**的入口。
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            clean_hist.append({"role": role, "content": content[:4000]})
    body["history"] = clean_hist          # ⭐ 就地写回 —— 这一步是关键，别省
    return q, None


def _cite(h: dict) -> dict:
    return {
        "id": h["id"],
        "type": h.get("type", "rule"),
        "chapter": h.get("chapter"),
        "chapter_title": h.get("chapter_title", ""),
        "term": h.get("term"),
        "text": h.get("text", ""),
        "parent_id": h.get("_parent_id"),
        "parent_text": h.get("_parent_text"),
        # 附录注解：正文把答案外包给图/附录时（2.1「尺寸见图 1」），
        # 事实在注解里。右栏必须能看到它，用户才能核查数字不是编的。
        "notes": h.get("_notes") or [],
        "source": h.get("source", config.SOURCE_NAME),
        # 附录层专用：前端据此把芯片/卡片写成「附录 A1.2」，
        # 而不是「第 A1.2 条」——**附录不是正文条款**，两者不能混用同一种说法。
        "label": h.get("label"),
        "section": h.get("section"),
        "section_title": h.get("section_title"),
        "page": h.get("page"),
        "source_url": h.get("source_url"),
        "score": h.get("_score"),
        "cos": h.get("_cos"),
    }


# ── 右栏兜底：按条款号直接取原文（2026-10-17 加）─────────────────────────
#
# ⚠️ 为什么需要它（线上真事：owner 转来的一条用户反馈）
#   答案正文里的条款号是可点的（前端 `selectCite`），但右栏 `paintSources()`
#   **只渲染本次检索召回的 `citations`**。实测那一问：
#     问「我们在区域防守，我是读秒的防盘人，属于什么情况」
#     模型在答案里引 **17.4.1**（我核对过语料：17.4.1 确实是「阻挡犯规」，**引对了**）
#     而这次 13 条 citations 里**没有 17.4.1**（召回的是 9.1 / 18.1.x…）
#   ⇒ 用户点 17.4.1：右栏打开了、列出一堆别的条款，**就是没有 17.4.1** ——
#     在他眼里和"右栏空了"那个 P0 一模一样，于是又来反馈。
#
# ⚠️ 纪律（别破）：
#   ① 这里返回的是**语料原文**，不是模型生成的 ⇒ 不违反"零编造"铁律；
#   ② 它**绝不进 `citations`**（不参与打分、不算"命中几条"）——
#      `citations` 的语义是"这次检索召回了什么"，两者不能混。
_RULE_JSONL_INDEX: dict[str, dict] | None = None
_RULE_LIVE_INDEX: tuple[int, dict[str, dict]] | None = None


def _rules_index() -> dict[str, dict]:
    """条款号 → 条款。优先用**引擎里那份**（与 `citations` 同源，文字逐字一致）；
    引擎没就绪时退回 `data/rules.jsonl` —— 保证这个只读接口不会因为
    "索引还没建好"就 503（用户点了条款号却什么都没有，正是要修的那个体验）。"""
    global _RULE_LIVE_INDEX, _RULE_JSONL_INDEX
    r = STATE.get("retriever")
    if r is not None:
        if _RULE_LIVE_INDEX is None or _RULE_LIVE_INDEX[0] != id(r):
            _RULE_LIVE_INDEX = (id(r), {str(it.get("id")): it for it in r.items})
        return _RULE_LIVE_INDEX[1]
    if _RULE_JSONL_INDEX is None:
        idx: dict[str, dict] = {}
        try:
            # 附录层（2026-10-18）：引擎没就绪时，点开附录条目也要能取到原文，
            # 否则「按条款号取原文」对附录条目会 404。
            for name in ("rules.jsonl", "appendix.jsonl"):
                p = config.DATA_DIR / name
                if not p.exists():
                    continue
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            it = json.loads(line)
                            idx[str(it.get("id"))] = it
        except (OSError, ValueError):
            idx = {}
        _RULE_JSONL_INDEX = idx
    return _RULE_JSONL_INDEX


def _rule_card(cid: str) -> dict | None:
    """按条款号取一条原文，形状与 `_cite()` 完全一致 ⇒ 前端可以复用同一套卡片渲染。"""
    idx = _rules_index()
    it = idx.get(str(cid or "").strip())
    if it is None:
        return None
    h = dict(it)
    parent = h.get("parent")
    if parent:
        par = idx.get(str(parent))
        if par:
            h["_parent_id"] = par.get("id")
            h["_parent_text"] = par.get("text", "")
    card = _cite(h)
    # 兜底卡不是"检索命中的那一条" ⇒ 抹掉相关度，前端据此标明来源
    card["score"] = None
    card["cos"] = None
    card["fallback"] = True
    return card


def _retrieval_context(ast: dict, _scope: dict, res: dict, hits: list[dict],
                       intent: str, rewritten: str | None,
                       *, web=None, glossary: str = "") -> dict:
    """"检索这一侧"的上下文：**生成层不知道、但前端必须收到**的那一半 payload。

    ## ⚠️⚠️ 为什么要有这个函数（2026-10-17 修 P0，线上真事）
    `generator.answer_stream()` 只产出 5 个键：
    `{answer, mode, provider, glossary, usage}` —— 它**不该**知道检索上下文
    （条款、路由、置信度都是服务端的事）。而 2026-10-13 接上流式分支时，
    `done` 帧**只发了生成层那 5 个键**，于是线上真实用户看到的是：

      · 答案下面**没有条款芯片**（前端 `citesRow()` 拿到的 `citations` 是 undefined）
      · 右栏**永远是**「这次回答没有引用条款原文」（`paintSources()` 取不到 citations）
      · 点答案里的条款号 ⇒ 右栏打开了、**里面是空的** ← owner 报的那个 bug
      · 「记进笔记」/ 反馈回传也丢了条号（`payload.citations` 为空）

    ## 为什么是两个出口共用一份，而不是各写一份
    非流式出口（`handle_ask` 末尾那坨 `_fin({...})`）本来就手写着这些字段。
    **手写两遍 = 迟早只改一处** —— 这次的 bug 就是"流式那份漏了"。
    ⇒ 一个函数，两个出口都从它取；新增字段只改这里。

    ⚠️ `web` / `glossary` 走参数：非流式出口里的 `web` 可能是联网兜底的结果
       （**绝不进 citations**，前端单独渲染并标注来源），不能在这里写死 None。
    """
    return {
        "assistant": ast["id"],
        "intent": intent,
        "rewritten": rewritten or None,
        "citations": [_cite(h) for h in hits],
        "nearby": res.get("nearby") or [],
        "confident": res.get("confident"),
        "best_cos": res.get("best_cos"),
        "threshold": res.get("threshold"),
        "elapsed_ms": res.get("elapsed_ms"),
        # 检索可观测性：让前端能展示"命中了哪些规则类目""走了几路召回"，
        # 这是"依据可核查"的一部分——用户能看到系统是怎么找到这条规则的。
        "routes": res.get("routes") or [],
        "paths": res.get("paths"),
        "forced_count": res.get("forced_count"),
        "glossary": glossary,
        "web": web,
        # ⭕ 本次走了哪条路（rule / related / off_topic / harm / chat）。
        # ⚠️ 前端**只需判这一个字段**就能知道该摆什么（要不要摆条款、要不要标注
        #    "这不是规则"）。前端契约见 static/app.js 的渲染分支。
        "scope": {"route": _scope["route"], "why": _scope["why"],
                  "need_clarify": _scope["need_clarify"]},
        "llm_configured": bool(STATE["generator"] and STATE["generator"].available),
    }


def _stream_tap(gen, q: str, _scope: dict, res: dict, hits: list[dict],
                intent: str, extra: dict | None = None, *,
                probe: bool = False, log_fn=None,
                timing: dict | None = None):
    """包住 `answer_stream`，**旁路**做埋点。不改变产出的帧。

    ⚠️ 2026-10-22 修了一个静默 bug：这里原来写 `probe=probe`，但 `probe`
       是 `handle_ask` 的局部变量，**不在这个函数的 scope 里** ⇒ 每次埋点都
       `NameError`，而四处都包在 `except Exception: pass` 里 ⇒
       **流式提问（主界面那条路）从来没被记进 queries.jsonl**。
       ⇒ 现在 `probe` / `log_fn` / `timing` 都显式传进来。

    ⚠️ `extra` = `_retrieval_context()` 那份"生成层不知道、前端必须收到"的字段，
       **在 `done` 帧上补进去**（生成层产出的键优先，它才知道 answer/mode/usage）。
       ⚠️⚠️ 这一步不是可选的润色 —— 漏了它，右栏/条款芯片/笔记条号**全部为空**。
       见 `_retrieval_context()` 的注释（2026-10-17 线上 bug 的根因）。

    ⚠️ 三条边界：
      ① 埋点**不许**影响帧的产出 —— 帧原样 yield 出去，埋点失败只吞掉。
      ② 对端断开（`GeneratorExit`）时也要记账：那说明用户**没等到答案**，
         **这恰恰是最该被记录的一种情况**（原来的埋点完全看不到它）。
      ③ 生成器中途抛错时要补一个 `done` 帧兜底 ——
         否则前端收不到结束信号，又变成"永远转圈"。
    """
    final: dict | None = None
    got = False
    logged = False
    log = log_fn or querylog_mod.log
    g0 = time.perf_counter()

    def _gen_ms() -> int:
        return int(round((time.perf_counter() - g0) * 1000))

    try:
        for kind, data in gen:
            if kind == "token":
                got = True
            elif kind == "done":
                # ⚠️ 先合并上下文，再赋值给 final —— 埋点读的也是合并后的那份
                #    （mode/answer 来自生成层，citations 来自服务端）。
                if extra and isinstance(data, dict):
                    data = {**extra, **data}
                if isinstance(data, dict) and timing is not None:
                    # done 帧也要带三段耗时（前端「显示细节」与报表都用得到）。
                    gen_ms = _gen_ms()
                    timing["generation_ms"] = gen_ms
                    data["retrieval_ms"] = timing.get("retrieval_ms", 0)
                    data["generation_ms"] = gen_ms
                    data["total_ms"] = int(round(
                        (time.perf_counter() - timing.get("t0", time.perf_counter())) * 1000))
                final = data if isinstance(data, dict) else None
            yield kind, data
    except GeneratorExit:
        if timing is not None:
            timing["generation_ms"] = _gen_ms()
        try:
            log(q, route=_scope["route"], mode="client_abort",
                confident=res.get("confident"), citations=len(hits),
                answer_len=0, need_clarify=_scope.get("need_clarify"),
                why="客户端在流式过程中断开", intent=intent,
                elapsed_ms=res.get("elapsed_ms"), probe=probe)
        except Exception:      # noqa: BLE001
            pass
        logged = True
        raise
    except Exception as exc:  # noqa: BLE001
        if timing is not None:
            timing["generation_ms"] = _gen_ms()
        try:
            log(q, route=_scope["route"], mode="stream_error",
                confident=res.get("confident"), citations=len(hits),
                answer_len=0, why=f"流式中断：{type(exc).__name__}",
                intent=intent, elapsed_ms=res.get("elapsed_ms"), probe=probe)
        except Exception:      # noqa: BLE001
            pass
        logged = True
        yield "error", {"message": f"{type(exc).__name__}"}
        yield "done", {"answer": "", "mode": "stream_error", "provider": None}
        return
    finally:
        if timing is not None:
            timing["generation_ms"] = _gen_ms()
        # ⚠️ 埋点只在**真正结束**时记一次。用 finally 是因为三种结局
        #    （正常 / 抛错 / 对端断开）都该留下痕迹，只是 mode 不同。
        #    ⚠️ `logged` 防重复：abort/error 已经在上面记过，别再补一条 nodone。
        if not logged and final is not None:
            try:
                log(q, route=_scope["route"],
                    mode=final.get("mode") or "",
                    confident=res.get("confident"), citations=len(hits),
                    answer_len=len(final.get("answer") or ""),
                    provider=final.get("provider") or "",
                    need_clarify=_scope.get("need_clarify"),
                    why=_scope.get("why", ""), intent=intent,
                    elapsed_ms=res.get("elapsed_ms"), probe=probe)
            except Exception:      # noqa: BLE001
                pass
        elif not logged and got:
            try:
                log(q, route=_scope["route"], mode="stream_nodone",
                    confident=res.get("confident"), citations=len(hits),
                    answer_len=0, why="流式发过 token 但没有 done 帧",
                    intent=intent, elapsed_ms=res.get("elapsed_ms"), probe=probe)
            except Exception:      # noqa: BLE001
                pass


def handle_ask(body: dict, *, stream: bool = False):
    """一次问答的**唯一**入口。`/api/ask` 与 `/api/ask/stream` 都走这里。

    ⚠️⚠️ 返回类型有两种形态（这是刻意的，见下面的理由）：
      · `stream=False`（默认）→ `(payload, code)`，即原来的形态；
      · `stream=True` → **生成器**，产出 `(kind, data)` 帧：
        `("meta", {...})` 规则主路在**检索刚跑完**就先发一帧（用户马上看到依据），
        `("token", "…")` 只有主路才有，最后必有 `("done", payload)`。

    **为什么加开关，而不是另写一个流式函数**（2026-10-13）：
      本函数里的**范围闸门**（第 0.7 层）是 owner 定的"什么时候拒答"，
      是整个产品最承重的判断。**复制一份到流式路径 = 迟早漂移**，
      而漂移那天流式版就成了**绕过闸门的后门**。
      ⇒ 一份控制流、两种出口形态。
      `tests/test_stream_parity_2026_10_13.py` 会断言两个模式经过**同一串判断**。

    ⚠️ 出口一律走 `_fin()` 包装，**不要直接 return** —— 流式下那会让
       前端一个字节都收不到（`_fin` 负责把 payload 变成 `done` 帧）。
    """
    # ⚠️ 「meta 是不是已经发过了」必须是**整个 handle_ask 共享**的一个格子。
    #    ⚠️ 第一版把它放在 `_fin` 里面 ⇒ 每调一次 `_fin` 就重置，
    #       而 `handle_ask` 主体里也要读它 ⇒
    #       `NameError: name '_st' is not defined`（实测，服务端 500）。
    #    用可变容器而不是裸 bool：嵌套函数**读**没问题，
    #    但想在里面**改**就会踩 UnboundLocalError —— 用 dict 一次躲开。
    _st = {"meta_sent": False}

    # ⚠️⚠️ 「这是不是自动巡检探针」—— 决定**要不要记进用户日志**。
    #
    # 【为什么需要它（2026-10-15，一个我造成的污染）】
    #   服务器 cron 每 10 分钟跑 `tools/health_check.py`，它会真问一句
    #   `读秒从几开始` 来验证问答链路。而那句提问**被当成用户提问记了下来**。
    #   实测后果：
    #     · 日志里 `读秒从几开始` 出现 **160 次** —— 全是探针
    #     · 309 条"用户提问"里，能确认是真人问的**只有个位数**
    #     · ⇒ **整份用户行为日志不可用**：算不出真实提问分布，
    #       也没法拿它判断"用户到底在问什么"
    #   ⇒ 探针要的是"链路通不通"，**不该被统计成一个用户**。
    #
    # ⚠️ 为什么读 body 而不是靠 User-Agent：
    #   UA 谁都能改，而且探针和真人走的**必须是同一条代码路径**
    #   （不然测不出真实链路）。用一个显式字段最直白。
    # ⚠️ 这个标记**只影响埋点，不影响回答** —— 探针照样拿到完整答案。
    probe = bool(body.get("probe")) and bool(body.get("_probe_ok"))

    # ── 耗时拆解（2026-10-22）────────────────────────────────────────
    # ⚠️ `res["elapsed_ms"]` 只来自 retriever，是**检索那一段**。
    #    用户感知的"慢"大多在生成，所以这里分开记：
    #    retrieval_ms / generation_ms / total_ms。
    #    ⚠️ `elapsed_ms` 保留作兼容字段（= 检索），别拿它判断"用户觉得慢"。
    _tm = {"t0": time.perf_counter(), "retrieval_ms": 0, "generation_ms": 0}

    def _ms() -> int:
        return int(round((time.perf_counter() - _tm["t0"]) * 1000))

    def _log(q: str, **kw):
        """所有埋点走这里：自动补三段耗时，避免 8 个调用点各写一遍。"""
        kw.setdefault("retrieval_ms", _tm["retrieval_ms"])
        kw.setdefault("generation_ms", _tm["generation_ms"])
        kw.setdefault("total_ms", _ms())
        querylog_mod.log(q, **kw)

    def _timed(fn, *a, **k):
        """跑一次生成调用并记 generation_ms（异常也记，便于定位慢失败）。"""
        s = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            _tm["generation_ms"] = int(round((time.perf_counter() - s) * 1000))

    def _fin(payload: dict, code: int = 200):
        """**唯一**的出口：产出一帧 `("done", payload)`。

        ⚠️ 非 200 的状态码放进 `payload["_err"]`（哨兵），由 `handle_ask_sync`
           取出后删掉 —— 前端不该看到内部字段。

        ⚠️⚠️ 为什么不再搞"两种形态"（2026-10-13 踩过）：
          `yield` 一出现，函数**整个**就变成生成器函数 ——
          连 `return payload, code` 都只写进 `StopIteration.value`，
          调用方 `a, b = handle_ask(...)` 会直接炸。
          ⇒ **只有一种产出形态**，非流式由 `handle_ask_sync` 还原。
        """
        payload = dict(payload or {})
        # 所有出口都带三段耗时（2026-10-22）：早退路径也一样，
        # 否则报表会缺掉"被拒/未命中"那批的 total_ms。
        payload["retrieval_ms"] = _tm["retrieval_ms"]
        payload["generation_ms"] = _tm["generation_ms"]
        payload["total_ms"] = _ms()
        payload.setdefault("elapsed_ms", _tm["retrieval_ms"])
        if code != 200:
            payload["_err"] = code
        # ⚠️⚠️ 早退路径（人工库/闲聊/拒答/对话/not_found）**也要发一帧 meta**。
        #    为什么用空帧而不是各自拼一份依据：
        #      这些路径的"依据"形态各不相同（拒答没有条款、人工库有 6 条、
        #      对话路刻意不摆条款），在 6 个出口各拼一次**必然漏掉某个**。
        #      ⇒ 统一发空帧（只起"阶段结束"的信号作用），
        #        真正的依据由 `done` 帧带全量 payload —— 前端从那里取即可。
        #    ⚠️ 这不是"偷懒"：**帧序契约比帧内容重要** ——
        #       前端要靠 meta 判断"服务端已经开始说话了"，缺了它会一直显示加载态。
        #    ⚠️ 但**已经发过 meta 的路径不许再发**（对话路就是）——
        #       重复的 meta 会让前端把"依据区"重建两遍（闪烁），
        #       而且帧序契约变成"1 或 2 个 meta"，前端没法写判据。
        if not _st["meta_sent"]:
            yield "meta", {}
        yield "done", payload

    r = STATE["retriever"]
    if r is None:
        yield from _fin({"detail": STATE["boot_error"] or "索引未就绪"}, 503)
        return
    q, err = clean_query(body)
    if err:
        yield from _fin({"detail": err}, 400)
        return

    # 助手预设：只影响表达风格与默认召回条数，不影响铁律（见 app/assistants.py）
    ast = assistants_mod.get(body.get("assistant"))
    top_k = clamp_top_k(body.get("top_k")) or ast.get("top_n")
    use_web = body.get("web", True)

    # ---- 第 0 层：疑问库（Owner 亲自回答过，最高优先级） ----
    # 放在最前面是因为它绕过了整个检索不确定性：
    # Owner 说过的答案不需要靠向量找回来，直接用。
    hit_curated = curated_mod.lookup(q)
    if hit_curated:
        # 📊 埋点：人工库命中也要记 —— 否则"哪些问题被人工库接走了"看不出来
        _log(q, route="curated", mode="curated", confident=True,
                         citations=len(curated_mod.clause_ids(hit_curated)),
                         why="命中人工裁定库", probe=probe)
        yield from _fin(_curated_answer(q, hit_curated, ast), 200)
        return

    # ---- 第 0.5 层：轻量对话（寒暄 / 元问题 / 问助手本身） ----
    # ⚠️ 必须在**检索之前**分掉。放后面没用：这句话含「飞盘」「规则」两个
    #    域内词 ⇒ 闸门会放行 ⇒ 检索到 10.1.2 / 1.8 / 1.9 这些"鼓励请教规则"
    #    的条款 ⇒ 生成层拿这些条款硬答，用户拿到一屏答非所问的东西。
    #    owner 2026-10-06 实测报的正是这个（「有点答非所问了…应该收敛一下」）。
    #    这层**不检索、不给条款**，所以提前做还能省一次改写 + 一次向量检索。
    # ⚠️ 判据方向是「宁漏勿错」：不确定就走下面正常的规则流程。
    if smalltalk_mod.is_smalltalk(q):
        out = _timed(STATE["generator"].chat, q, body.get("history"),
                     style_hint=ast.get("style", ""))
        _log(q, route="chat", mode="chat", confident=True,
                         answer_len=len(out["answer"] or ""),
                         provider=out.get("provider") or "", why="寒暄/元问题", probe=probe)
        yield from _fin({
            "answer": out["answer"],
            "mode": "chat",
            "provider": out.get("provider"),
            "assistant": ast["id"],
            "intent": "chat",
            "rewritten": None,
            "citations": [],
            "nearby": [],
            "confident": True,
            "best_cos": None,
            "threshold": None,
            "elapsed_ms": 0,
            "routes": ["日常对话"],
            "paths": None,
            "forced_count": 0,
            "web": None,
            "usage": out.get("usage"),
            # ⚠️ 闲聊也带 scope，保持**前端只需判一个字段**（见 _scope 的说明）
            "scope": {"route": "chat", "why": "寒暄/元问题", "need_clarify": False},
            "llm_configured": bool(STATE["generator"] and STATE["generator"].available),
        }, 200)
        return

    # ---- 第 0.6 层：诉求分路（他在诉苦？只要原文？还是普通提问？） ----
    # owner 2026-10-06 定的产品方向：「要能分析出用户提问的根本目的和核心诉求…
    #   或者是来找你诉苦，需要你一边提供情绪价值，一边把规则给讲透」。
    # ⚠️ 与第 0.5 层的**本质区别**：那层是**换路**（不检索），这层只是**换语气** ——
    #    依据、条款号、"规则没规定"一个字都不动，检索照常跑。
    #    所以它放在"改写/检索"**之前**只是为了算一次意图，不加任何额外开销。
    # ⚠️ 判据方向同样是「宁漏勿错」：判不准就回 intent=rule，走原来那条路。
    intent = intent_mod.classify(q)

    # ---- 第 0.7 层：⭕ **范围与意图闸门**（2026-10-13 加，owner 定的底层逻辑） ----
    # owner 原话：
    #   「用户可能会问到规则之外的问题…他本来就因为被打爆了而难过，结果向你提问
    #    飞盘规则之外的相关内容时，你又回答不出来，**这会让他更难过**。」
    #   「**什么时候拒答？** 当他**诱导我们去回答飞盘运动之外的内容**时。」
    # 判据与设计见 `app/scope.py` 模块头（含 owner 那个"用飞盘打了人"的反例）。
    #
    # ⚠️⚠️ **这里只做"提前分流"，绝不改判规则问题**：
    #    · 有危害意图 / 与飞盘无关的任务请求 ⇒ 提前拒，**省掉检索**（也省一次改写）
    #    · **其余一律照旧往下走** —— 真正的分路在检索之后，
    #      因为"有没有条款候选"是判 RULE 的最硬依据（`has_rule_candidate`）。
    _scope_pre = scope_mod.classify(q)
    # ⚠️⚠️ **提前拒答必须双条件**（2026-10-13 修，线上实测踩过）：
    #    第一版只要 `classify` 判 OFF_TOPIC 就提前返回，结果
    #    「中心区多长」（场地尺寸题）被拒了 —— 因为「中心区」不在飞盘词表里，
    #    而**词表永远补不全**（场地、附录、术语…）。
    #    ⇒ 规则改成：**只有在"明确离题信号"下才允许提前拒**；
    #      没有离题信号时一律**让检索先判** —— 检索找不到才走拒答。
    #    ⚠️ 这与本项目的「宁可偏松，不可偏紧」一致：误拦=真问题被答"规则里没有"，
    #      那是**实质答错**，比白调一次大模型坏得多。
    _clearly_off = (scope_mod.is_off_topic_signal(q)
                    or scope_mod.is_off_domain_task(q))
    if _scope_pre["route"] == scope_mod.HARM or (
            _scope_pre["route"] == scope_mod.OFF_TOPIC and _clearly_off):
        # ⚠️ 提前拒答**必须**清空 citations —— 否则又会复现
        #    「说只回答飞盘问题、下面却摆 14 条条款」那个老问题（见下面那段长注释）。
        # ⚠️ **harm 与 off_topic 用不同的话术**：
        #    · HARM 是最硬的一种拒 —— 但也不必说教，简短、不接话茬。
        #    · OFF_TOPIC 友好引导（owner 同意可以建议去问豆包类工具）。
        if _scope_pre["route"] == scope_mod.HARM:
            answer = HARM_TEXT
        else:
            answer = OFF_TOPIC_TEXT
        _log(q, route=_scope_pre["route"], mode="out_of_scope",
                         confident=False, answer_len=len(answer),
                         why=_scope_pre["why"], intent=intent, probe=probe)
        yield from _fin({
            "answer": answer, "mode": "out_of_scope", "provider": None,
            "assistant": ast["id"], "intent": intent, "rewritten": None,
            "citations": [], "nearby": [], "confident": False,
            "best_cos": 0, "threshold": None, "elapsed_ms": 0,
            "routes": [], "paths": None, "forced_count": None,
            "glossary": "", "web": None, "usage": None,
            "scope": {"route": _scope_pre["route"], "why": _scope_pre["why"],
                      "need_clarify": False},
        }, 200)
        return

    # 先做查询改写（口语 -> 规则术语）。这一步决定检索能不能命中，
    # 例如"我拿着盘被人撞了"要能翻译出"非轻微接触 / 防盘犯规"才找得到 17.6。
    rewritten = STATE["generator"].rewrite_query(q)
    res = r.search(q, top_k if top_k else None, extra=rewritten)
    hits = res["hits"]
    _tm["retrieval_ms"] = _ms()

    # ⚠️⚠️ 流式：**检索一跑完就把「依据」发出去**（2026-10-13 实测驱动）。
    #   为什么必须在这里、而不是等生成器：`rewrite_query` 是**一次模型调用**，
    #   实测约 2 秒。把这一帧留到后面发，用户就要先看 2 秒空白，
    #   "第一眼看到条款"的设计完全落空（实测 meta 迟到 **2.04s**）。
    #   ⚠️ 代价：这一帧里**没有** `_scope`（它要等检索后才知道"有没有条款候选"），
    #      所以只发"已经确定"的部分，`scope` 留给 done 帧。
    #      **宁可少发，也不要为了凑字段去猜一个路由** —— 猜错会把用户引偏。
    if stream:
        _st["meta_sent"] = True          # ⚠️ 告诉 `_fin`：别再发第二帧
        yield "meta", {
            "rewritten": rewritten or None,
            "elapsed_ms": res.get("elapsed_ms"),
            "routes": res.get("routes") or [],
            "nearby": res.get("nearby") or [],
            "citations": [_cite(h) for h in hits],
            "best_cos": res.get("best_cos"),
            "threshold": res.get("threshold"),
            "confident": res.get("confident"),
        }

    # ⚠️ 检索跑完才知道"有没有条款候选" ⇒ **在这里才做最终分路**。
    #    有候选就一定是 RULE（`scope.classify` 里那条最优先的判据）。
    _scope = scope_mod.classify(q, has_rule_candidate=bool(hits) and res["confident"])

    # ⭕⭕ **第五个出口：跟飞盘有关，但我真的答不了**（2026-10-15 加）。
    #
    # 【为什么在这里】判据要用**检索结果**（"命中的条款是不是全在泛章"），
    #   所以必须在检索之后、进对话链路之前。这也是本项目那条教训的又一次应用：
    #   **闸门要放在它真正能判的那个位置上** —— 检索之前的闸门拿不到这个信号。
    #
    # 【它拦的是什么】实测 13 条"跟飞盘有关但非规则"里 **12 条**走了严谨规则链路
    #   硬答（报名费/场地在哪/多少钱一个/推荐牌子/北京到上海多远…）。
    #   owner 的定义是「我们不能让用户难过」——
    #   而把"我不知道"包装成一段解释，**比直说不知道更让人难受**。
    #
    # ⚠️ 只拦**检索不自信**的那些（`not res["confident"]`）：
    #   自信说明确实有条文对得上（`读秒从几开始` 分数 0.57），那是真规则题。
    # ⚠️ 判据见 `app/scope.py:is_beyond_rules` 的注释 —— 是**量出来的**，不是拍的：
    #   泛章占比 ≥ 0.90 ⇒ 误伤 0/27。
    # ⚠️⚠️ **不能挂在 `if not res["confident"]` 下面**（第一版就是这么写的，永远不执行）。
    #    实测：域外题 `办一场飞盘比赛要交多少报名费` 的 **`confident=True`** ——
    #    因为 `DOMAIN_WORDS` 里有裸「飞盘」，`domain_hit()` 一票命中就是 True。
    #    ⇒ `confident` 这件事上**根本不可靠**（它就是被这两个字骗的）。
    #
    # ⚠️⚠️⚠️ 但也**不能只看检索结果** —— 这里踩了本轮最深的一个坑，必须记下来：
    #   判据要挂在 `_scope_pre`（**检索之前**那次判定）的 `RELATED` 上才安全。
    #   为什么？实测 `今天被打爆了，心情有点差`（**产品定义里最不能拦的一类**）：
    #   ```
    #     原句   : 今天被打爆了，心情有点差
    #     改写后 : 比赛失利 情绪 体育精神 自我调节      ← LLM 把情绪**洗掉了**
    #     用原句检索  : 泛章占比 0.75  → 不该拦 ✅
    #     用改写句检索: 泛章占比 1.00  → **会被拦** ❌
    #     scope.classify(原句) = related「有情绪但没有话题线索 ⇒ 问一句」
    #   ```
    #   ⇒ 检索链路上用的是**改写后**的查询，而改写会把情绪洗成"体育精神"这类词，
    #     于是它看起来就像个"没有具体条款对得上"的规则问题。
    #   ⇒ 而**检索之前**那次 `classify(q)` 是拿**原句**判的 —— 它认得情绪。
    #   ⇒ 所以：**情绪类交给 `related` 那条路（问一句），不要让它走到这里来。**
    #     这也是产品定义的要求：「先接住人」优先于「判断这问题归不归我管」。
    #
    # ⚠️ 这个坑的教训与注释里那个阈值坑是**同一类**：
    #   **我拿理想化的输入（原句）去验真实链路（改写后的句子）。**
    #   凡是"链路中间会改输入"的地方，判据都必须用**改完之后**的数据验一遍。
    # ⚠️ 闸门判据：**直接查原句有没有情绪词**，不看 `_scope_pre["why"]` 的措辞。
    #
    # ⚠️ 试过两个更"聪明"的写法，都错了，记下来免得再试：
    #   ① `route != RELATED` —— 不行。实测 `读秒从几开始`（真规则题）
    #      检索前也是 `related / 飞盘相关`，跟域外题**一模一样** ⇒ 分不开。
    #   ② 读 `_scope_pre["why"]` 里有没有"情绪"二字 —— 也不行。
    #      `打得不好，有点不想练了` 的 why 是「与飞盘运动无关」（**没有"情绪"二字**）
    #      ⇒ 漏掉，于是那个用户收到"这个我帮不上"。
    #      **那是产品定义里最不能出的错**（他已经"不想练了"，还被挡在门外）。
    #   ③ ⇒ 现在直接调 `has_emotion(q)`。**判据要盯住事实本身，不要盯住别人对事实的描述。**
    #
    # ⚠️ 而且这次同时补了词表（`app/scope.py:_EMOTION` 加了「不想练」这批）——
    #    因为原来表里有「不想**打**」却没有「不想**练**」，
    #    **漏一个词就等于漏一类人**。
    if scope_mod.has_emotion(q):
        beyond, why_beyond = False, "原句带情绪 ⇒ 交给对话链路接住（产品定义优先）"
    else:
        # ⚠️ 传 `res["domain_word"]` —— 它是**检索时实际用的那个查询**算出来的
        #    （可能是 LLM 改写后的句子）。实测：用原句算和用改写句算结论会不一样，
        #    而**改写后那个才是真实链路**。
        # ⚠️ 三个参数缺一不可：
        #    `hits` / `res["domain_word"]` 来自**检索实际用的那个查询**（可能已被改写），
        #    `q` 是**用户原句** —— 判据的信号 B 恰恰要原句（改写会丢信息）。
        beyond, why_beyond = scope_mod.is_beyond_rules(
            hits, res.get("domain_word"), q)
    if beyond:
        answer = BEYOND_RULES_TEXT
        hits = []          # ⚠️ 同对话链路：这条路上没有能回答他的条款，摆出来是明示
        _log(q, route="beyond_rules", mode="beyond_rules",
                         confident=False, answer_len=len(answer),
                         why=why_beyond, intent=intent,
                         elapsed_ms=res.get("elapsed_ms"), probe=probe)
        yield from _fin({
            "answer": answer, "mode": "beyond_rules", "provider": None,
            "assistant": ast["id"], "intent": intent, "rewritten": rewritten or None,
            # ⚠️ `citations` 必须清空 —— 与离题那条同源：
            #    摆出条款等于告诉用户"这些跟你有关"，而它们其实无关。
            "citations": [], "nearby": res["nearby"], "confident": False,
            "best_cos": res["best_cos"], "threshold": res["threshold"],
            "elapsed_ms": res.get("elapsed_ms"), "routes": res.get("routes") or [],
            "paths": res.get("paths"), "forced_count": res.get("forced_count"),
            "glossary": "", "web": None, "usage": None,
            "scope": {"route": "beyond_rules", "why": why_beyond,
                      "need_clarify": False},
        }, 200)
        return

    web = None

    # ⭕ **对话链路**：最终分路是 RELATED ⇒ 交给模型用"懂飞盘的伙伴"口吻回应。
    #    ⚠️ 2026-10-20：**不再要求"检索不自信"**。
    #    g025/g026 实测：「队友总是不跑位，我该怎么跟他说」「推荐一双飞盘鞋」
    #    会被检索塞进 12.x / 1.6 候选（confident=True），旧代码因此跳过这段、
    #    硬走规则链路，用户拿到一段"这不在规则范围"的绕路回答。
    #    ⇒ 判据挂在**最终分路**上，不挂在检索自信上。
    if _scope["route"] == scope_mod.RELATED:
        out = _timed(
            STATE["generator"].converse,
            q, purpose=_scope.get("why", ""), history=body.get("history"),
            competition=res.get("competition"), style_hint=ast.get("style", ""))
        answer, mode, usage = out["answer"], out["mode"], out.get("usage")
        provider = out.get("provider")
        glossary = ""
        # ⚠️ 对话链路**不摆条款**：这条路上本来就没有能回答他的条款，
        #    摆出来就是在明示"这些条款跟你的问题有关"（与离题那条同源）。
        #    但**保留 nearby** 让前端能显示"系统找过、没找到"，保持可核查。
        hits = []
        _log(q, route=_scope["route"], mode=mode, confident=False,
                         answer_len=len(answer or ""), provider=provider or "",
                         need_clarify=_scope.get("need_clarify"),
                         why=_scope.get("why", ""), intent=intent,
                         elapsed_ms=res.get("elapsed_ms"), probe=probe)
        yield from _fin({
            "answer": answer, "mode": mode, "provider": provider,
            "assistant": ast["id"], "intent": intent, "rewritten": rewritten or None,
            "citations": [], "nearby": res["nearby"], "confident": False,
            "best_cos": res["best_cos"], "threshold": res["threshold"],
            "elapsed_ms": res["elapsed_ms"], "routes": res.get("routes") or [],
            "paths": res.get("paths"), "forced_count": res.get("forced_count"),
            "glossary": glossary, "web": None, "usage": usage,
            "scope": {"route": _scope["route"], "why": _scope["why"],
                      "need_clarify": _scope["need_clarify"]},
        }, 200)
        return

    if not res["confident"]:
        # 闸门拦截：不调大模型，零编造风险
        # ⚠️ `in_domain` 区分闸门拦截的两种情况（2026-10-07）：
        #   完全离题（"帮我写段代码"）⇒ 不许摆条款，否则是在明示
        #   用户"这些条款跟你无关"；域内但没条款 ⇒ 摆"你是不是想问这些"有用。
        answer = gen_mod.not_found_text(res["nearby"], bool(res.get("domain_word")), q)
        mode, usage, provider = "not_found", None, None
        _log(q, route=_scope["route"], mode="not_found", confident=False,
                         answer_len=len(answer or ""), provider="",
                         why=_scope.get("why", ""), intent=intent,
                         elapsed_ms=res.get("elapsed_ms"), probe=probe)
        # 拒答路径不给术语解释：那段话本来就该短，别再往上加东西。
        glossary = ""
        # ⚠️⚠️ 完全离题时 citations 必须置空（2026-10-09 修，线上实测复现）。
        # 上面那段注释早就写明「完全离题 ⇒ 不许摆条款，否则是在明示用户
        # 这些条款跟他无关」，但下面 return 里的 citations 是**无条件**返回 hits 的
        # ⇒ 注释与实现脱节。实测问「附近有什么好吃的」，回答确实是
        # 「我只回答飞盘规则的问题」，下面却齐刷刷摆着 **14 条**飞盘精神条款芯片，
        # 右栏还写着「本次翻出 14 条」—— 用户会认为系统在敷衍他，
        # 或者更糟：以为这 14 条一定和他的问题有关。
        # 域内但查不到条款（domain_word 为真）**照旧摆**，那种情况摆出来是有用的。
        if not res.get("domain_word"):
            hits = []
        # ---- 第 3 层：联网兜底（只在前两层都救不了时） ----
        # ⚠️ 联网结果绝不进 citations（见 app/weblookup.py 模块头），
        #    单独走 web 字段，前端必须显示"⚠️ 来自网络"标注。
        if use_web:
            try:
                web = weblookup_mod.lookup(q)
            except Exception:      # 联网是可选增强，挂了不能影响主流程
                web = None
    else:
        # ⭕ `competition` 是「圈外的点」（赛事设置：时长/目标分/裁判/观察员…）。
        # ⚠️ 它**不在 hits 里**、不参与打分与闸门，走独立通道传给生成层，
        #   提示词里也**单独成段**（见 generator.build_competition_block）。
        #   理由见 app/competition.py 头部与 AGENTS.md §7.9b。
        #
        # ── ⚡ 流式分支（2026-10-13）：只有「规则主路」需要真流式 ──────────
        # 为什么只在这里分：模型逐字写 700 字是**唯一**那段长耗时（实测占 78%），
        #   其它路径（人工库/闲聊/拒答/对话）的正文都是**已经算好的整段**，
        #   没有可流的东西 —— 硬套流式只会多一层壳。
        # ⚠️ 但**前置一行都不改**：闸门/闲聊/人工库/检索都在上面跑完了，
        #   走到这里说明它已经被判定为"该用规则回答"。
        if stream:
            # ⚠️ 「依据」帧**已经在上面发过了**（检索完立刻发的那一帧）。
            #    这里只驱动生成器。**不要**再发一次 meta ——
            #    重复的 meta 帧会让前端把"依据区"重建两遍（闪烁）。
            gen = STATE["generator"].answer_stream(
                q, hits, body.get("history"),
                style_hint=ast.get("style", ""), intent=intent,
                competition=res.get("competition"))
            # ⚠️⚠️ `extra` 必须传：生成层的 `done` 帧只带正文/模式/用量，
            #    条款原文（citations）全靠这份上下文补进去 ——
            #    漏了它 = 右栏空、答案下面没有条款芯片（2026-10-17 线上 bug）。
            yield from _stream_tap(
                gen, q, _scope, res, hits, intent,
                extra=_retrieval_context(ast, _scope, res, hits, intent, rewritten),
                probe=probe, log_fn=_log, timing=_tm)
            return

        out = _timed(STATE["generator"].answer, q, hits, body.get("history"),
                     style_hint=ast.get("style", ""), intent=intent,
                     competition=res.get("competition"))
        answer, mode, usage = out["answer"], out["mode"], out.get("usage")
        provider = out.get("provider")
        # 📊 埋点：**规则链路这一条最常用** —— 它的数据决定"下一版改哪里"。
        _log(q, route=_scope["route"], mode=mode,
                         confident=res.get("confident"),
                         citations=len(hits), answer_len=len(answer or ""),
                         provider=provider or "", need_clarify=_scope.get("need_clarify"),
                         why=_scope.get("why", ""), intent=intent,
                         elapsed_ms=res.get("elapsed_ms"), probe=probe)
        # ⚠️ `glossary` 是「答案里出现、但正文没解释的官方术语」白话解释，
        #   **单独返回、不拼进 answer**：正文一字不动，前端决定默认折叠。
        #   取值全部来自 data/definitions.jsonl，不经大模型（见 app/glossary.py）。
        glossary = out.get("glossary") or ""

    # ⚠️ 这是**规则主路**的正常出口（缩进 4 空格，与函数体同级）。
    #    漏改这一处会让 handle_ask 走 `return` 而不是产帧 ——
    #    而 `handle_ask_sync` 找不到 done 帧就会显式报错（这是故意的）。
    #
    # ⚠️ 字段**不再手写**：与流式出口共用 `_retrieval_context()`
    #    （生成层产出的 answer/mode/provider/usage 由本处覆盖进去）。
    #    两个出口手写两遍 = 迟早只改一处 —— 2026-10-17 的线上 bug 就是这么来的。
    yield from _fin(dict(
        _retrieval_context(ast, _scope, res, hits, intent, rewritten,
                           web=web, glossary=glossary),
        answer=answer, mode=mode, provider=provider, usage=usage,
    ), 200)
    return


def handle_ask_sync(body: dict) -> tuple[dict, int]:
    """`/api/ask` 用的薄包装：把 `handle_ask` 的**帧流**还原成原来的 `(payload, code)`。

    ⚠️ 存在的意义：让 `/api/ask` 的契约**一个字都不变** ——
       它被 576 条单测和线上 `verify_remote.py`（PASS 58）盯着，
       改它的返回形态是在拿最稳的那条路冒险。
    ⚠️ 只取 `done` 帧：主路的 `meta` / `token` 帧对非流式没有意义，
       直接丢弃（HTTP 响应本来就要等全部生成完才有 body）。
    """
    payload: dict | None = None
    try:
        with capacity.request_budget():
            with capacity.REQUEST.slot():
                for kind, data in handle_ask(body):
                    if kind == "done":
                        payload = data if isinstance(data, dict) else {}
    except capacity.Overloaded as exc:
        # 2026-10-22：容量边界。**返回 429 而不是 500** ——
        # 让客户端知道"这是可以重试的过载"，不是服务坏了。
        return {"answer": "", "mode": "overloaded", "provider": None,
                "citations": [], "confident": False,
                "detail": str(exc), "retry_after": exc.retry_after,
                "scope": {"route": "overloaded", "why": "容量已满",
                          "need_clarify": False}}, 429
    if payload is None:
        # ⚠️ 走到这里说明某个出口忘了 `_fin()` —— **显式报错**，
        #    绝不静默返回空 payload（空 answer 看起来就是"卡住了"）。
        raise AssertionError("handle_ask 没有产出 done 帧：某个出口漏了 _fin()")
    code = int(payload.pop("_err", 200) or 200)
    return payload, code


def _card_next_step(answer: str) -> str:
    """从**已经生成好的回答**里摘一句"下次怎么做"。

    ⚠️ 只摘，不生成 —— 摘不到就返回空串。
    这是卡片上的一个展示字段，不是新的规则判断；绝不能在这里补一句话。
    """
    text = str(answer or "")
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*#> ").strip()
        if not line:
            continue
        if any(k in line for k in ("下次", "该怎么做", "你可以", "建议", "场上")):
            # 只取第一句，避免把整段细节搬进卡片
            for sep in ("。", "！", "？", "!", "?"):
                if sep in line:
                    line = line.split(sep, 1)[0] + sep
                    break
            return line[:120]
    return ""


def handle_card(body: dict) -> tuple[dict, int]:
    """把一次问答包装成一张「判定卡」。

    ⚠️⚠️ 只调用 `handle_ask_sync`（唯一控制流），不另写检索 / 生成 / 闸门。
       这个接口只是**换了一种呈现形态**：把 answer 拆成卡片需要的字段。
    ⚠️ 不新增任何规则判断：
       · conclusion = 回答的第一段（`<!--more-->` 之前），不是另写一句；
       · next_step = 从回答里摘一句，摘不到就空着；
       · citations 原样透传，**绝不在这里编条款**。
    """
    payload, code = handle_ask_sync(body)
    if code != 200:
        return payload, code

    answer = str(payload.get("answer") or "")
    direct, _, detail = answer.partition("<!--more-->")
    direct = direct.strip()
    detail = detail.strip()
    # ⚠️ 人工裁定（curated）常常没有 `<!--more-->`，整段都是正文。
    #    卡片的"第一眼"只放第一段/第一句，其余进 detail（由前端折叠）。
    #    **只切，不改写** —— 一个字都不新增。
    if not detail and len(direct) > 220:
        parts = [p.strip() for p in direct.split("\n\n") if p.strip()]
        if len(parts) > 1:
            direct, detail = parts[0], "\n\n".join(parts[1:])
    if len(direct) > 220:
        cut_len = 220
        pos = max(direct.rfind(s, 0, cut_len) for s in ("。", "！", "？"))
        if pos >= 40:
            cut_len = pos + 1
        cut = direct[:cut_len]
        rest = direct[cut_len:].strip()
        if cut_len == 220:
            cut = cut.rstrip() + "…"
        detail = (rest + ("\n\n" + detail if detail else "")).strip()
        direct = cut

    card = {
        "question": str(body.get("q") or "").strip()[:1000],
        # 给前端一个"要不要显示「如果当时」"的入口判据。
        # ⚠️ 这不是规则判断，只是界面入口；判宽了最多多一个按钮。
        "kind": "judgment" if gen_mod.is_judgment_question(str(body.get("q") or "")) else "procedure",
        "conclusion": direct or answer.strip(),
        "detail": detail,
        "answer": answer,
        "next_step": _card_next_step(answer),
        "citations": payload.get("citations") or [],
        "confident": bool(payload.get("confident", True)),
        "mode": payload.get("mode"),
        "provider": payload.get("provider"),
        "curated": payload.get("curated"),
        "scope": payload.get("scope"),
        "elapsed_ms": payload.get("elapsed_ms"),
        # 2026-10-22：卡片是主界面，三段耗时也要透传，否则报表只有 /api/ask。
        "retrieval_ms": payload.get("retrieval_ms"),
        "generation_ms": payload.get("generation_ms"),
        "total_ms": payload.get("total_ms"),
    }
    return card, 200


@_paid_guard
def handle_branches(body: dict) -> tuple[dict, int]:
    """「如果当时」：对**已经有判定**的一次受控推演。

    ⚠️ 客户端只传 clause_ids，**不传条款原文** ——
       原文必须由服务端按 id 从语料取，否则任何人都能伪造一段"规则"
       喂给模型，让它推演出看似有依据的岔路。
    ⚠️ 不调 handle_ask、不重新检索：这是对已有判定的推演，
       不是第二次判罚。条款集合由调用方给出、服务端逐条校验。
    """
    gen = STATE["generator"]
    r = STATE["retriever"]
    if gen is None or r is None:
        return {"branches": [], "note": "服务未就绪。"}, 503
    q = str(body.get("q") or "").strip()[:500]
    if not q:
        return {"detail": "问题不能为空"}, 400
    # ⚠️ 只对**判定题**开放推演。流程题（"我该怎么做"）和情绪题
    #    推出来的往往不是"另一个选择"，而是"另一条相关规则"或精神建议 ——
    #    实测「今天被打爆了…怎么调整」会推出「去跟对方打个招呼」，
    #    那不是这个产品该说的话。
    if not gen_mod.is_judgment_question(q):
        return {"branches": [], "note": "这个问题不是判定题，暂时没有可推演的岔路。"}, 200
    raw_ids = body.get("clause_ids") or []
    if not isinstance(raw_ids, list):
        return {"detail": "clause_ids 必须是数组"}, 400
    ids: list[str] = []
    for x in raw_ids[:8]:
        s = str(x or "").strip()
        if s and s not in ids:
            ids.append(s)

    clauses: list[dict] = []
    for cid in ids:
        for h in (r.search(cid, top_k=1)["hits"] if r else []):
            if h["id"] == cid:
                clauses.append(_cite(h))
                break
    if not clauses:
        return {"branches": [], "note": "没有可用条款，无法推演。"}, 200

    out = gen.branches(q, str(body.get("answer") or "")[:1200], clauses)
    # 契约兜底：不管生成层返回什么，这三个字段必须在（前端按它们分支）。
    out.setdefault("branches", [])
    out.setdefault("need", "")
    out.setdefault("note", "")
    # 前端用这份官方原文显示条款，不信任客户端传来的 citations 文本。
    out["clauses"] = [
        {"id": c.get("id"), "label": c.get("label"),
         "text": c.get("text"), "chapter_title": c.get("chapter_title")}
        for c in clauses
    ]
    return out, 200


def _curated_answer(q: str, item: dict, ast: dict) -> dict:
    """疑问库命中时的回答。

    不调大模型、不做检索 —— Owner 的答案本身就是依据。
    引用条款只取 Owner 自己点名的那些，并**带上"人工裁定"的来源标记**，
    绝不把裁定伪装成官方规则原文。
    """
    r = STATE["retriever"]
    clauses = curated_mod.clause_ids(item)
    cites: list[dict] = []
    for cid in clauses:
        for h in (r.search(cid, top_k=1)["hits"] if r else []):
            if h["id"] == cid:
                cites.append(_cite(h))
                break

    src = item.get("source", curated_mod.SOURCE_OWNER)
    by = item.get("by") or "维护者"
    tag = "人工核定" if src == curated_mod.SOURCE_OWNER else "联网补充（未复核）"
    return {
        "answer": item["answer"],
        "mode": "curated",
        "provider": None,
        "assistant": ast["id"],
        # 疑问库是"查表命中"，压根没走诉求分路 —— 如实写 null，不编一个。
        "intent": None,
        "rewritten": None,
        "citations": cites,
        "nearby": [],
        "confident": True,
        "best_cos": None,
        "threshold": None,
        "elapsed_ms": 0,
        "routes": ["疑问库"],
        "paths": None,
        "forced_count": 0,
        "web": None,
        "curated": {
            "id": item.get("id"),
            "question": item.get("question"),
            "source": src,
            "by": by,
            "tag": tag,
            "match": item.get("_match"),
            "created_at": item.get("created_at"),
        },
        "usage": None,
        "llm_configured": bool(STATE["generator"] and STATE["generator"].available),
    }


@_paid_guard
def handle_search(body: dict) -> tuple[dict, int]:
    r = STATE["retriever"]
    if r is None:
        return {"detail": STATE["boot_error"] or "索引未就绪"}, 503
    q, err = clean_query(body)
    if err:
        return {"detail": err}, 400
    res = r.search(q, clamp_top_k(body.get("top_k")), )
    res["hits"] = [_cite(h) for h in res["hits"]]
    res["nearby"] = res.get("nearby") or []
    return res, 200


# 「没用」的原因选项。前后端共用同一份口径，改这里前端会跟着走（前端通过 /api/feedback 取）。
# 这些选项是照着"病根"设计的，不是"感受"：
#   条款引错 / 明明有却说没有 → 检索层
#   没说清该怎么办 / 太啰嗦 / 术语太多 → 表达层
#   答非所问 / 其他 → 待分类
FEEDBACK_REASONS = [
    {"id": "wrong_clause", "label": "条款引错了"},
    {"id": "missed_clause", "label": "明明有这条却说没有"},
    {"id": "unclear_action", "label": "没说清场上该怎么做"},
    {"id": "too_long", "label": "太啰嗦了"},
    {"id": "too_jargon", "label": "术语太多看不懂"},
    {"id": "off_topic", "label": "答非所问"},
    {"id": "other", "label": "其他"},
]
_REASON_IDS = {r["id"] for r in FEEDBACK_REASONS}


def _feedback_path() -> Path:
    return config.DATA_DIR / "feedback.jsonl"


def _clip(v, n: int) -> str:
    """把用户输入裁到安全长度，避免有人往日志里灌大段文本。"""
    s = str(v or "").strip()
    return s[:n]


def handle_feedback(body: dict, ok_owner: bool = False) -> tuple[dict, int]:
    """记录一次评分反馈，append 到 data/feedback.jsonl。

    **「有用」也记** —— 只有记了分母，才算得出"满意率"；
    只收差评的话，看到 100 条 badcase 也不知道该说好还是坏。

    这里不做去重：同一个人改好几次主意是有效信号（说明这条确实让人纠结）。

    rating="note" = **意见反馈**（不针对某条回答的评分，而是"界面能不能小一点"
    "想要个 XX 功能"这类）。它和评分反馈写进同一个文件 —— 拆成两个池子，
    反馈就散了，以后没人知道用户到底说了什么。
    但统计时**必须跳过**：一句"界面不好用"不是差评，算进满意率会污染这个数。

    ⚠️ 2026-10-24：`recent` 含其他用户的 q/comment，只回给持令牌的 Owner；
    普通调用者只拿聚合计数。写入也加每分钟上限，防止无鉴权灌盘。
    """
    if not _FEEDBACK_RATE.allow():
        return {"detail": "反馈提交太频繁，请稍后再试", "retry_after": 30}, 429
    rating = str(body.get("rating") or "").strip().lower()
    if rating not in ("up", "down", "note"):
        return {"detail": "rating 必须是 up / down / note"}, 400
    q = _clip(body.get("q"), 500)
    if not q:
        return {"detail": "缺少原问题 q"}, 400

    reason = _clip(body.get("reason"), 32)
    if reason and reason not in _REASON_IDS:
        reason = "other"  # 未知原因归到"其他"，不因此拒收整条反馈

    cites = body.get("citations") or []

    # ⚠️⚠️ 2026-10-15：**反馈里的文本也要脱敏**，不能只 `_clip` 截断。
    #
    # 【原来是个隐私缺口】`queries.jsonl` 的提问走 `querylog._scrub()`
    #   （手机号 / 邮箱 / 微信号都会被换成 `[手机号]` 这种标记），
    #   但**这里一个字都没脱敏** —— 而反馈里的文本比提问更长、更容易带隐私：
    #     · `comment` 是自由输入框：「我队友张三 138xxxx1234 当时…」
    #     · `q` 是原问题，可能带「我们队的李四…」
    #     · `answer` 是整段回答
    #   ⇒ 用户看到界面上写着「只记录问题与回答本身，不含身份信息」，
    #     而实际上他写进补充说明的手机号会被原样存下来 —— **说到没做到**。
    #
    # ⚠️ 复用 `querylog._scrub`（幂等），**不另写一份正则** ——
    #   两份脱敏规则迟早会漂移，而漂移的那天没人会发现。
    _scrub = querylog_mod._scrub

    row = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "rating": rating,
        # 意见反馈的分类（answer/clause/missing/ui/feature/other）。
        # 只对 note 有意义，评分反馈不用它 —— 两个字段别混：reason 是"为什么差"，
        # kind 是"这条意见在讲什么"，混成一个字段以后没法分别统计。
        "kind": _clip(body.get("kind"), 24) or None,
        "reason": reason or None,
        "comment": _clip(_scrub(body.get("comment")), 500) or None,
        "q": _clip(_scrub(q), 500),
        "answer": _clip(_scrub(body.get("answer")), 4000),
        "mode": _clip(body.get("mode"), 32) or None,
        "provider": _clip(body.get("provider"), 64) or None,
        "assistant": _clip(body.get("assistant"), 32) or None,
        "citations": [_clip(c, 24) for c in cites][:16] if isinstance(cites, list) else [],
        "best_cos": body.get("best_cos") if isinstance(body.get("best_cos"), (int, float)) else None,
    }
    try:
        with _feedback_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        # ⚠️ 与 do_POST 的兜底 catch 同一个理由（2026-10-09 修）：
        # OSError 原文含**服务器绝对路径**，公网访问者能直接读到目录结构。
        traceback.print_exc()
        return {"detail": "反馈写入失败，请稍后再试"}, 500
    return {"ok": True, "stats": _feedback_stats(include_recent=ok_owner)}, 200


def _feedback_stats(include_recent: bool = False) -> dict:
    """统计反馈：总条数、好评/差评、差评按原因分布。供概览页与工具复用。

    ⚠️ rating="note"（意见反馈）**不进 total/up/down/satisfaction**。
    它不是对某条回答的评价，算进分母会让满意率这个数失去意义 ——
    这个数是用来判断"检索层该不该改"的，不是用来统计用户心情的。
    但它要单独计数、也要出现在 recent 里（意见反馈页要显示"已经收到的"）。
    """
    p = _feedback_path()
    up = down = notes = 0
    by_reason: dict[str, int] = {}
    recent: list[dict] = []
    if p.exists():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            rating = row.get("rating")
            if rating == "up":
                up += 1
            elif rating == "down":
                down += 1
                key = row.get("reason") or "other"
                by_reason[key] = by_reason.get(key, 0) + 1
            elif rating == "note":
                notes += 1
            recent.append({
                "ts": row.get("ts"), "rating": rating, "kind": row.get("kind"),
                "reason": row.get("reason"), "comment": row.get("comment"),
                "q": row.get("q"),
            })
    total = up + down
    return {
        "total": total,
        "up": up,
        "down": down,
        "notes": notes,
        "satisfaction": round(up / total, 4) if total else None,
        "by_reason": by_reason,
        "recent": recent[-10:][::-1] if include_recent else [],
    }


def _write_denied_reason() -> str:
    """403 的说法。**故意区分"没配令牌"和"令牌不对"** ——
    Owner 一个人用，两个原因的可能性差着几个数量级，
    含糊的"无权限"会让他查半天。
    """
    if not config.CURATED_TOKEN:
        return ("服务器没有配置 CURATED_TOKEN，写入已关闭。"
                "本机自测请用 `CURATED_TOKEN=dev python app/server.py` 启动。")
    return "Owner 令牌不对。请在「人工裁定」页面填入正确的令牌。"


def _owner_ok(headers) -> bool:
    """Owner 写操作令牌校验（fail-closed）。

    ⚠️ 为什么这道锁是必需的（2026-10-07 审计实测）：
    疑问库是本项目**唯一**能绕过整个检索 + 生成层、把一段文本直接当
    「人工核定」权威答案吐给用户的通道。服务默认绑 0.0.0.0（部署即公网可达），
    而全项目零鉴权 ⇒ 实测任何人 POST 一条裁定，紧接着提问就能拿到那条注入的
    答案，界面上还明明白白标着"人工核定"。这等于把"依据可核查"这个
    产品存在理由交给陌生人 —— 而且**比不回答坏得多**，因为它看起来很权威。

    三条设计取舍：
    ① **fail-closed**：服务器没配 `CURATED_TOKEN` 就一律拒绝写。
       反过来（没配就放开）等于"忘了配置 = 门户大开"，是最坏的那种默认值。
    ② `hmac.compare_digest` 而不是 `==`：后者按字节短路比较，
       会把"前几位对不对"变成一个可测量的时间信号。
    ③ 只锁**写**，不锁读：Owner 要在面板里浏览已有裁定，那不需要令牌；
       真正的界线在"能不能改数据"。
    """
    want = config.CURATED_TOKEN
    if not want:
        return False
    got = (headers.get("X-Owner-Token") or "").strip()
    if not got:
        return False
    # ⚠️ str 版 hmac.compare_digest 遇到非 ASCII 会抛 TypeError
    #    ⇒ 恶意 X-Owner-Token 能把请求打成 500（2026-10-24 实测）。
    #    统一按 UTF-8 bytes 比较；无法编码的输入直接拒绝。
    try:
        return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))
    except (UnicodeError, TypeError):
        return False


def _probe_token_ok(headers) -> bool:
    """内部巡检探针的免写授权（2026-10-24 R1，fail-closed）。

    ⚠️ 不能用 client_address 判断：Caddy 反代后远程请求也是 127.0.0.1。
    ⚠️ 不能用 X-Forwarded-For / Referer / UA：都可被公网提交。
    ⇒ 独立 `PROBE_TOKEN` 头，只放在服务器 cron 环境；不进 JS/exe/报告。
    """
    want = config.PROBE_TOKEN
    if not want:
        return False
    got = (headers.get("X-Probe-Token") or "").strip()
    if not got:
        return False
    try:
        return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))
    except (UnicodeError, TypeError):
        return False


def handle_curated(body: dict, ok_owner: bool) -> tuple[dict, int]:
    """疑问库增删。

    action=add     写入一条 Owner 裁定（问题 / 答案 / 相关条款 / 同义问法）
    action=delete  按 id 撤掉一条裁定 —— 裁定会过期，必须能删

    ⚠️ 两个动作都是**改数据**，都要 Owner 令牌（见 `_owner_ok` 的理由）。
    """
    action = str(body.get("action") or "add").strip()
    if action == "add":
        if not ok_owner:
            return {"detail": _write_denied_reason()}, 403
        try:
            item = curated_mod.add(
                question=body.get("question"),
                answer=body.get("answer"),
                clauses=body.get("clauses") if isinstance(body.get("clauses"), list) else [],
                keywords=body.get("keywords") if isinstance(body.get("keywords"), list) else [],
                source=str(body.get("source") or curated_mod.SOURCE_OWNER),
                by=str(body.get("by") or "Owner"),
            )
        except ValueError as exc:
            return {"detail": str(exc)}, 400
        except OSError:
            # ⚠️ 同上（2026-10-09 修）：OSError 原文含服务器绝对路径。
            traceback.print_exc()
            return {"detail": "疑问库写入失败，请稍后再试"}, 500
        return {"ok": True, "item": item, "stats": curated_mod.stats()}, 200

    if action == "ping":
        # 令牌自检（2026-10-09 加）。Owner 在面板右上角填完令牌，
        # 此前**没有任何办法知道它对不对** —— 只能靠真正新增一条才发现，
        # 于是变成"填错 → 试一次 → 被拒 → 再猜"的循环。
        # ⚠️ 这里**只校验、不碰任何数据**：连 delete 都不调用，
        # 否则"点一下确认令牌"会变成一条隐形的写操作。
        if not ok_owner:
            return {"detail": _write_denied_reason()}, 403
        return {"ok": True, "token_ok": True}, 200

    if action == "delete":
        if not ok_owner:
            return {"detail": _write_denied_reason()}, 403
        item_id = str(body.get("id") or "").strip()
        if not item_id:
            return {"detail": "缺少 id"}, 400
        if not curated_mod.delete(item_id):
            return {"detail": "没找到这条裁定"}, 404
        return {"ok": True, "stats": curated_mod.stats()}, 200

    return {"detail": f"未知 action：{action}"}, 400


def _chapters() -> list[dict]:
    """知识库结构：章节 -> 条款。供「知识库 · 数据集」页浏览与核查。

    ⚠️ 附录层（2026-10-18 加）：`type="appendix"` 的条目属于 `chapter="APP"`，
    **不是正文条款**。它必须排在最上面、且**不显示章序号**（owner 定的：
    「附录最好加在最上面，也不要给它标序号」）。前端据 `chapter=="APP"`
    单独渲染（见 static/app.js `viewKBDataset`），所以这里把
    `label/section/section_title` 一并下发 —— 少了它们，前端只能显示 `A1`
    这种编号，用户又分不清那是条号还是附录编号。
    """
    r: Retriever = STATE["retriever"]
    order: list[str] = []
    buckets: dict[str, dict] = {}
    for it in r.items:
        key = str(it.get("chapter"))
        if key not in buckets:
            buckets[key] = {"chapter": key, "title": it.get("chapter_title", ""), "items": []}
            order.append(key)
        buckets[key]["items"].append({
            "id": it["id"],
            "type": it.get("type"),
            "depth": it.get("depth"),
            "term": it.get("term"),
            "parent": it.get("parent"),
            "text": it.get("text", ""),
            # 附录层专用（正文没有这些字段，取到 None 也无害）
            "label": it.get("label"),
            "section": it.get("section"),
            "section_title": it.get("section_title"),
            "page": it.get("page"),
            # 附录里的图（场地示意 / 24 个手势，2026-10-18）：前端按图集渲染
            "images": it.get("images"),
        })
    # 官方注释（2021–2024）：**扫描原件**，只读浏览，**不进检索**。
    # ⚠️ 为什么不 OCR 成文字再入库：它是旧版（2021–2024）、且扫描件没有文字层；
    #    OCR 一定有错（实测漏过整行），错一个条号就是误导。直接展示原件最诚实。
    ann_dir = config.STATIC_DIR / "annotations"
    if ann_dir.is_dir():
        pages = sorted(ann_dir.glob("p*.jpg"))
        if pages:
            # OCR 文字稿（2026-10-18）：扫描件在窄屏上字太小，默认给文字；
            # 每页保留「看扫描原件」折叠。⚠️ OCR 必有错，只作阅读辅助，不参与回答/引用。
            ocr: dict[str, str] = {}
            ocr_p = config.DATA_DIR / "annotations_ocr.json"
            if ocr_p.is_file():
                try:
                    ocr = json.loads(ocr_p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    ocr = {}
            buckets["ANN"] = {"chapter": "ANN", "title": "官方注释（2021–2024）", "items": []}
            for p in pages:
                n = int(p.stem[1:])
                buckets["ANN"]["items"].append({
                    "id": f"ANN:p{n}",
                    "type": "annotation_page",
                    "label": f"第 {n} 页",
                    "text": "",
                    "ocr_blocks": ocr.get(str(n)) or [],
                    "image": f"annotations/{p.name}",
                    "page": n,
                    "depth": 1,
                })
            order.append("ANN")
    # 排序：附录置顶 → 官方注释 → 规则章按数字 → 0 号（前言/定义）放最后
    def sort_key(k: str):
        if k == "APP":
            return (0, 0)
        if k == "ANN":
            return (1, 0)
        if k == "0":
            return (3, 0)
        return (2, int(k) if k.isdigit() else 999)
    order.sort(key=sort_key)
    return [buckets[k] for k in order]


def _files() -> list[dict]:
    """源数据文件清单：让"知识库到底由什么构成"可见。"""
    rows: list[dict] = []
    targets: list[tuple[Path, str]] = []
    src_dir = config.DATA_DIR / "source"
    if src_dir.exists():
        for p in sorted(src_dir.glob("*")):
            if p.is_file():
                targets.append((p, "语料源文件"))
    for name, kind, note in (
        ("index.json", "检索索引", "语料切块 + 向量，服务启动时加载"),
        ("summary.json", "切块摘要", "构建索引时的统计产物"),
        ("faq_eval.jsonl", "评测集", "447 条真实口语问法，用于回归验证"),
        ("rewrite_cache.jsonl", "改写缓存", "口语→术语的改写结果缓存"),
        ("feedback.jsonl", "用户反馈", "评分反馈，用于定位该优化的地方"),
        # 附录层（2026-10-18）：让「语料来源」页能看见它，而不是只在检索里默默生效
        ("appendix.jsonl", "附录语料", "官方附录 v2.0 中文译文（非正文条款）"),
    ):
        p = config.DATA_DIR / name
        if p.exists():
            targets.append((p, kind))
    for p, kind in targets:
        try:
            st = p.stat()
            rows.append({
                "name": str(p.relative_to(config.ROOT)).replace("\\", "/"),
                "kind": kind,
                "bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            continue
    return rows


class _RateWindow:
    """进程内滑动窗口限速（不按 IP、不存身份）。

    2026-10-24：给无鉴权的写入接口一个最低成本的滥用闸门。
    ⚠️ 进程重启会重置；多 worker 时各算各的（本项目 pm2 单进程）。
    """

    def __init__(self, per_minute: int) -> None:
        self.limit = max(1, int(per_minute))
        self._lock = threading.Lock()
        self._times: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            self._times = [t for t in self._times if now - t < 60.0]
            if len(self._times) >= self.limit:
                return False
            self._times.append(now)
            return True

    def reset(self) -> None:
        """测试用：清空窗口。"""
        with self._lock:
            self._times = []


_DIAG_RATE = _RateWindow(config.UI_DIAG_PER_MIN)
_FEEDBACK_RATE = _RateWindow(config.FEEDBACK_PER_MIN)
_DIAG_FILE_LOCK = threading.Lock()

# /api/ui-diag 的**字段允许列表**（2026-10-24）：
#   原来只 pop 顶层 q，任意嵌套字段都会原样落盘 ——
#   `{"d":{"q":"..."}}` 能绕过"绝不收问题文本"，`ua_short`/`sid` 也违背
#   "不记录 UA/会话标识"的承诺。这里只保留前端真实使用的结构计数。
_UI_DIAG_FIELDS = {
    "kind": ("str", 40), "url": ("str", 240), "t": ("int", None),
    "strays": ("int", None), "stillHere": ("bool", None), "hasTh": ("bool", None),
    "innerInDoc": ("bool", None), "innerKids": ("int", None),
    "loadings": ("int", None), "hasPayload": ("bool", None),
    "ansLen": ("int", None), "msgs": ("int", None),
    "failed": ("str", 160), "err": ("str", 160),
}


def _sanitize_ui_diag(body: dict) -> dict:
    """按允许列表重建诊断记录；未知字段（含嵌套 q / UA / sid）一律丢弃。"""
    out: dict = {}
    for key, (typ, maxlen) in _UI_DIAG_FIELDS.items():
        if key not in body:
            continue
        val = body.get(key)
        if typ == "bool":
            if isinstance(val, bool):
                out[key] = val
        elif typ == "int":
            if isinstance(val, int) and not isinstance(val, bool):
                out[key] = max(-1, min(val, 10 ** 9))
        else:
            if isinstance(val, str):
                out[key] = val[:maxlen] if maxlen else val
    out.setdefault("kind", "ui-stuck")
    out["ts"] = time.time()
    return out


def _append_ui_diag(obj: dict) -> None:
    """有界追加：超过 UI_DIAG_MAX_BYTES 时只保留一份 .1 备份。

    诊断不是用户资产，可以轮转；反馈/查询日志另做限速，不在这里删。
    """
    p = config.DATA_DIR / "ui_diag.jsonl"
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    if len(line.encode("utf-8")) > 4096:
        return
    with _DIAG_FILE_LOCK:
        try:
            if p.is_file() and p.stat().st_size >= config.UI_DIAG_MAX_BYTES:
                p.replace(p.with_name(p.name + ".1"))
        except OSError:
            pass
        with p.open("a", encoding="utf-8") as f:
            f.write(line)


def _settings() -> dict:
    """运行参数。**只读** —— 见前端"检索参数"页的说明。

    刻意不做成可调开关：ROUTE_BONUS 等参数是经过全量对照扫参定下来的
    （扫过 0.20~0.50，0.30 是保住场景题的值），放开成滑块只会让使用者
    把一个"扫过参才定住"的值随手调坏。这里把它们连同"为什么是这个值"一起摊开。
    """
    r: Retriever = STATE["retriever"]
    return {
        "retrieval": [
            {"key": "TOP_K", "value": config.TOP_K, "name": "召回条数",
             "note": "交给生成层自己挑的候选条款数。取 8 而非 5：泛条款天然得分偏高，正确条款常被挤到 6~8 位。"},
            # ⚠️⚠️ 这一条**曾经是假陈述**（2026-10-09 修）。原文写的是
            # 「最高分低于此值即判『规则里没有』… 这是『不确定就说不确定』的执行点」，
            # 但 MIN_SCORE 全仓只有定义、回传和这一行显示文案，**从未参与任何判定**。
            # 真实闸门在 retriever.py:274：`confident = structured or hinted_exact
            # or domain_word`（类目/线索词/域内词），一个字都没提 MIN_SCORE；
            # 而且 retriever.py:253 已实测证伪余弦有区分度
            # （域内最低 0.344 vs 离题最低 0.350，完全重叠）。
            # 保留这一行是为了 owner 找得到这个历史参数，**但必须说清它没接线** ——
            # 否则 owner 会照着去调 MIN_SCORE，而那对系统行为零影响。
            {"key": "MIN_SCORE", "value": config.MIN_SCORE, "name": "余弦阈值（⚠️ 未接入，仅保留）",
             "note": "⚠️ 遗留参数，**当前不参与任何判定**，调它对系统行为零影响。"
                     "真实闸门是「类目/线索词/域内词」的结构化判据，不看余弦分数 —— "
                     "实测域内最低 0.344 与离题最低 0.350 完全重叠，分不开。"},
            {"key": "ROUTE_BONUS", "value": config.ROUTE_BONUS, "name": "类目命中加分（承重）",
             "note": "⚠️ 承重参数，非可调旋钮。正解裸余弦常低至 0.740，而同题通用原则高达 0.990，是靠这个值把正解抬到能同台竞争。调小即掉场景题。"},
            {"key": "ROUTE_BONUS_AUX", "value": config.ROUTE_BONUS_AUX, "name": "辅助条款加分",
             "note": "必须明显小于主档，否则措辞更贴近口语的辅助条款会压过主条款。"},
            {"key": "BIGRAM_WEIGHT", "value": config.BIGRAM_WEIGHT, "name": "字词法权重",
             "note": "远低于语义通道，只补「用户直接报条款号」和专有名词两种情形。"},
            {"key": "CLAUSE_HINT_BONUS", "value": config.CLAUSE_HINT_BONUS, "name": "条号线索加分",
             "note": "用户直接写条款号时强加分。只认用户原问题，不认改写器输出（防止编造条号被顶到第一位）。"},
        ],
        "embedding": {
            "model": config.EMBED_MODEL,
            "dims": config.EMBED_DIMS,
            "prefix": config.QUERY_PREFIX,
            "chunks": len(r.items) if r else 0,
        },
        "generation": {
            "chain": [s.strip() for s in config.LLM_PROVIDER_CHAIN.split(",") if s.strip()],
            "models": {"deepseek": config.LLM_MODEL_DEEPSEEK, "zhipu": config.LLM_MODEL_ZHIPU},
            "temperature": {"rewrite": 0.1, "answer": 0.2},
            "max_tokens": {"rewrite": 120, "answer": 1600},
            "configured": bool(STATE["generator"] and STATE["generator"].available),
            "describe": STATE["generator"].describe() if STATE["generator"] else None,
        },
        "source": config.SOURCE_NAME,
    }


class Handler(BaseHTTPRequestHandler):
    # 2026-10-24：不再向公网暴露框架/语言版本（旧值会带 Python 3.x）。
    server_version = "Shultimate"
    sys_version = ""
    # 2026-10-24：给慢客户端/半截 body 一个硬上限。
    # BaseHTTPRequestHandler 会在 setup() 里 `connection.settimeout(self.timeout)`；
    # 没有它时 24 个只发一半 body 的连接可以把线程池永久占满。
    timeout = 30

    # ---------- 工具 ----------
    def _security_headers(self) -> None:
        """所有响应共用的最小安全头（不改动任何业务字段）。"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        # script-src 'self' 是 2026-10-24 复盘导入 XSS 的第二道防线：
        # 即使某个渲染点漏了转义，内联 onerror/onclick 也不会执行。
        # style 允许 unsafe-inline：前端模板大量使用 style="..."（42 处），
        # 且这些值来自代码常量，不是用户可控内容。
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; connect-src 'self'; media-src 'self' blob:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'")

    def _access_ok(self) -> bool:
        password = config.PUBLIC_ACCESS_PASSWORD
        if not password:
            return True  # main() refuses a non-loopback bind in this case.
        raw = (self.headers.get("Authorization") or "").strip()
        if not raw.startswith("Basic ") or len(raw) > 2048:
            return False
        try:
            decoded = base64.b64decode(raw[6:], validate=True).decode("utf-8")
            user, supplied = decoded.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        return user == "shultimate" and hmac.compare_digest(supplied, password)

    def _require_access(self) -> bool:
        if self._access_ok():
            return True
        body = b'Authentication required'
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Shultimate", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)
        return False

    def setup(self) -> None:
        super().setup()
        # 2026-10-24 R4：请求头阶段也必须有**总时限**，不能只靠 socket
        # inactivity timeout（逐字节滴流会让每次 recv 都成功）。
        self._header_timer = None
        try:
            budget = float(config.HEADER_READ_TIMEOUT_S)
        except (TypeError, ValueError):
            budget = 10.0
        if budget > 0:
            def _kill_header_slow():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            t = threading.Timer(budget, _kill_header_slow)
            t.daemon = True
            t.start()
            self._header_timer = t

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            t = getattr(self, "_header_timer", None)
            if t is not None:
                t.cancel()
                self._header_timer = None

    def _send_json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if code == 429 and isinstance(obj, dict) and obj.get("retry_after"):
            self.send_header("Retry-After", str(int(obj["retry_after"])))
        # ⚠️⚠️ 2026-10-13：**显式声明关闭连接**。
        #
        # 本服务是 `BaseHTTPRequestHandler` 默认的 **HTTP/1.0**，
        # 而 HTTP/1.0 下**没有** `Connection: keep-alive` 时，消息边界只能靠
        # "服务器关连接"来判定。少写这一行时，某些浏览器/连接复用组合会一直
        # **等连接关闭**才认为响应读完 —— 表现正是
        # 「服务端 200、答案也生成了，前端 `fetch` 却永远不 resolve」。
        # ⚠️ 写 `Connection: close` 是**声明**，不是"关得更早"：
        #    它让客户端立刻知道"读完 Content-Length 就结束"，不必等 socket 关。
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.send_header("Connection", "close")   # 同上：HTTP/1.0 必须显式声明
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, target: Path, ctype: str) -> None:
        """带 Range 支持的文件下发。客户端 77MB 的更新包下载中断后会续传。

        为什么必须自己实现而不是 _send_bytes 一次读完：
        ① read_bytes() 把 77MB 全读进内存 —— 服务器只有 2G，
           并发几个下载就 OOM，而 pm2 旁边还跑着生产服务；
        ② 断点续传需要 206 响应，而 _send_bytes 永远返回 200。

        ⚠️ Range 头只认 `bytes=start-` 和 `bytes=start-end` 两种。
        多段（`bytes=0-100,200-300`）直接拒绝而不是"尽力而为" ——
        客户端只会发单段，接受多段反而会写出错位的文件。
        """
        try:
            size = target.stat().st_size
        except OSError:
            self._send_json({"detail": "文件不存在"}, 404)
            return

        start, end = 0, size - 1
        rng = (self.headers.get("Range") or "").strip()
        if rng:
            spec = rng[len("bytes="):] if rng.lower().startswith("bytes=") else ""
            if "," in spec or not spec:
                self._send_json({"detail": "不支持的 Range"}, 416)
                return
            a, _, b = spec.partition("-")
            # ⚠️⚠️ `bytes=-500` 是 HTTP 规范的**后缀区间**（"最后 500 字节"），
            # 这里 a == "" ⇒ 原代码算出 start=0, end=500 ⇒ **返回文件开头 501 字节**，
            # 而且 Content-Range 是合法的 `bytes 0-500/<size>`，**客户端无法察觉**。
            # 用户会拿到一个"开头 501 字节的安装包"，sha512 会拦住（不至于装坏包），
            # 但看到的是一句莫名其妙的"校验未通过"。
            # 当前客户端用的是 `bytes=<start>-`（desktop/updater.js）碰不到这里，
            # 可任何代理改写 Range、或将来改用后缀区间就会踩中 ⇒ 明确拒绝。
            if not a:
                self._send_json({"detail": "不支持后缀 Range"}, 416)
                return
            try:
                start = int(a)
                end = int(b) if b else size - 1
            except ValueError:
                self._send_json({"detail": "Range 解析失败"}, 416)
                return
            if start < 0 or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            # ⚠️ `bytes=0-99999999999` 这类越界尾值**也必须夹**（2026-10-07 修）。
            # 原来只有 `end = min(end, size - 1)`，那是对的；但负数尾值
            # （`bytes=0--5`）会让 length 变成负数 ⇒ Content-Length 报负、
            # `f.read(min(262144, left))` 拿到负数参数，行为不可预期。
            if end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)

        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        # 更新包必须能续传，所以清单和包都禁缓存；
        # 包还必须禁代理缓存（77MB 压到 CDN 上没意义，且会读到旧版本）。
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        with target.open("rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # 客户端关掉了（用户取消、切换网络）。
                    # 这不是错误，日志里刷红只会掩盖真问题。
                    return
                left -= len(chunk)

    def _send_static(self, rel: str) -> None:
        """从 static/ 安全地取文件。rel 已去掉前导斜杠。

        防目录穿越：解析后必须仍在 STATIC_DIR 之内，否则 404。
        """
        base = config.STATIC_DIR.resolve()
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_json({"detail": "非法路径"}, 400)
            return
        if not target.is_file():
            self._send_json({"detail": "文件不存在"}, 404)
            return
        ctype = MIME.get(target.suffix.lower())
        if not ctype:
            self._send_json({"detail": "不支持的类型"}, 415)
            return
        self._send_bytes(target.read_bytes(), ctype)

    def _send_update(self, raw_name: str) -> None:
        """下发 data/update/ 下的更新包。

        ⚠️ 三道锁，缺一不可：
        ① 解析后必须仍在 UPDATE_DIR 内 —— 否则 GET /dl/../../.env 就把密钥送出去了；
        ② 后缀只认 .exe —— 这个端点没有存在给别的文件下载的理由，
           放开了就成了任意文件读取（配合 ① 只能读到 update/ 里的东西，
           但仍然没必要）；
        ③ 必须是清单里登记过的那个文件名 —— 防止有人拿旧文件名直接命中，
           用户装到的是"看起来最新版但其实是旧包"。

        2026-10-15 扩展：启动器自动更新也走这里。
        原来只认 `installer` 字段（Electron 时代的 80MB 安装包），
        现在**也认 `launcher` 字段**（浏览器启动器，6.9MB）。
        两者的安全约束完全一样（清单里登记过才给下）。
        """
        name = unquote(raw_name)
        if "/" in name or "\\" in name or ".." in name:
            self._send_json({"detail": "非法文件名"}, 400)
            return
        if not name.lower().endswith(".exe"):
            self._send_json({"detail": "只提供 .exe 更新包"}, 415)
            return
        base = UPDATE_DIR.resolve()
        target = (base / name).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_json({"detail": "非法路径"}, 400)
            return
        if not target.is_file():
            self._send_json({"detail": "更新包不存在"}, 404)
            return
        # ⚠️ 安全锁③：文件名必须**是发过的更新包**。
        #
        # 【2026-10-15 改过一次判据 —— 原来那版会造成"用户卡在旧版"】
        #   原来是「名字必须在**当前**清单里登记过」：
        #       allowed = {installer.name, launcher.name}     ← 只有**一个** launcher
        #   后果：
        #     · 发布 0.3.2 的**那一瞬间**，`Shultimate-0.3.1.exe` 立刻 404
        #       （文件明明还在磁盘上，实测确认过：0.3.0/0.3.1 都 404 了）
        #     · 而用户那边可能**刚拿到旧清单**（清单是每次请求现读的，
        #       两个请求之间就是有这个窗口）
        #       ⇒ 它去下清单里写的那个名字 ⇒ **404 ⇒ 静默放弃**
        #       ⇒ 用户停在旧版，而且**没有任何报错**（更新失败是刻意静默的）
        #
        #   ⇒ 判据改成「**在 update 目录里、且叫 Shultimate*.exe** 就放行」。
        #     这样"发布新版导致旧版立刻不可下"这个窗口就消失了。
        #
        #   ⚠️ 安全性没有放松，前面两道锁已经拦住了真正危险的东西：
        #     ① `name` 由 `urlsplit().path` 取出，且必须 `.exe` 结尾（L1385）
        #     ② `(base / name).resolve()` 必须仍在 UPDATE_DIR 内（L1390-1394）
        #        ⇒ 路径穿越（`../../.env`）在第 ② 步就被挡了，跟白名单无关
        #     ③ 现在这条：**必须落在 update 目录里、且是 Shultimate 开头的 exe**
        #        ⇒ 就算目录里被塞了别的文件也下不走
        #   ⇒ 保留"登记过"的语义，但**不再只认最新那一个**。
        m = load_manifest()
        registered = set()
        for key in ("installer", "launcher"):
            entry = m.get(key) or {}
            if entry.get("name"):
                registered.add(str(entry["name"]))
        looks_like_release = (name.startswith("Shultimate")
                              and name.lower().endswith(".exe")
                              and name == target.name)
        if name not in registered and not looks_like_release:
            self._send_json({"detail": "该文件不是本项目的更新包"}, 404)
            return
        self._send_file(target, "application/octet-stream")

    def _read_body(self) -> dict:
        """读请求体。**带体积上限、长度校验和传输编码拒绝**。

        2026-10-24 DS-SEC-QA-01 补：
          · `Content-Length: -1` 原来能绕过 `n > MAX_BODY`，而
            `rfile.read(-1)` 会一直读到 EOF ⇒ 不限长内存 + pm2 400M 重启；
          · 多个 `Content-Length` / `Transfer-Encoding: chunked` 本服务器
            没有实现，继续按 JSON 读会读到错误边界 ⇒ 明确 400。
        """
        # 本服务只实现 HTTP/1.0 风格的定长 JSON body，不支持 chunked。
        if (self.headers.get("Transfer-Encoding") or "").strip():
            self.close_connection = True
            return {"__bad_body__": "不支持 Transfer-Encoding；请用 Content-Length"}
        raw_cls = self.headers.get_all("Content-Length") or []
        if len(raw_cls) > 1:
            self.close_connection = True
            return {"__bad_body__": "Content-Length 重复"}
        try:
            n = int((raw_cls[0] if raw_cls else "0").strip() or "0")
        except (TypeError, ValueError):
            self.close_connection = True
            return {"__bad_body__": "Content-Length 不是合法整数"}
        if n < 0:
            self.close_connection = True
            return {"__bad_body__": "Content-Length 不能为负数"}
        if not n:
            return {}
        if n > MAX_BODY:
            # 不去读它（读了才是真的被拖住），只明确拒绝并关连接。
            self.close_connection = True
            return {"__too_big__": True}
        # 2026-10-24 R4：Handler.timeout 只挡"一次等待没数据"；
        # 滴流客户端每次在超时前送一个字节，仍会无限占住线程。
        # 这里给整个 body 阶段一个总 deadline（取 min(配置, Handler.timeout)）。
        try:
            budget = min(float(config.BODY_READ_TIMEOUT_S),
                         float(getattr(self, "timeout", 0) or 30.0))
        except (TypeError, ValueError):
            budget = 10.0
        deadline = time.monotonic() + max(0.1, budget)
        buf = bytearray()
        remaining = n
        try:
            while remaining > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("body read deadline")
                conn = getattr(self, "connection", None)
                if conn is not None:
                    try:
                        conn.settimeout(max(0.05, min(
                            float(getattr(self, "timeout", 0) or 30.0), left)))
                    except OSError:
                        pass
                # ⚠️ 必须用 read1（返回"当前已到达的字节"），不能用 read(n)：
                #    BufferedIO.read(n) 会一直等到凑满 n 或 EOF；滴流客户端
                #    每次 recv 都返回 1 字节，socket inactivity timeout 永远不触发。
                reader = getattr(self.rfile, "read1", None) or self.rfile.read
                chunk = reader(min(65536, remaining))
                if not chunk:
                    raise ValueError("truncated body")
                buf.extend(chunk)
                remaining -= len(chunk)
        except (TimeoutError, ValueError, OSError):
            self.close_connection = True
            return {"__bad_body__": "请求体读取超时或截断"}
        try:
            body = json.loads(bytes(buf).decode("utf-8"))
            # body 必须是 dict：前端所有接口都发对象。若收到 list/str，
            # 下面的 body.get() 会 AttributeError → 500，属于"用户传错却报服务器错"。
            if not isinstance(body, dict):
                return {"__bad_body__": "请求体必须是 JSON 对象"}
            return body
        except json.JSONDecodeError:
            # ⚠️⚠️ 不能和下面的异常合并（2026-10-09 修）。原来所有异常一律
            # `return {}`，于是「请求体被截断/编码错」会被下游解读成
            # 「问题不能为空」「缺少原问题 q」—— **错误信息指向一个完全无辜的字段**，
            # Owner 会反复检查自己明明没问题的提问文本，而真因在传输层。
            # 分开之后前端能拿到真正的线索（是不是没发完/编码不对）。
            return {"__bad_body__": "请求体不是合法 JSON（可能传输被截断或编码不对）"}
        except UnicodeDecodeError:
            return {"__bad_body__": "请求体编码不是 UTF-8"}
        except Exception:  # noqa: BLE001
            return {}

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        if not self._require_access():
            return
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html"):
                self._send_static("index.html")
                return
            if route == "/api/health":
                r = STATE["retriever"]
                self._send_json({
                    "ready": r is not None,
                    "chunks": len(r.items) if r else 0,
                    "error": STATE["boot_error"],
                    "llm_configured": bool(STATE["generator"] and STATE["generator"].available),
                    "version": config.APP_VERSION,
                    # ⚠️ build = **静态文件内容的指纹**（2026-10-15 加）。
                    #    前端拿它跟"自己加载时的那个值"比：
                    #    不一样 ⇒ 说明服务端换了 UI/JS ⇒ 提示用户刷新。
                    #
                    #    为什么用内容指纹而不是 APP_VERSION：
                    #    APP_VERSION 要**人工记得改**，而人会忘 ——
                    #    忘了就意味着用户永远收不到"请刷新"的提示。
                    #    内容指纹是**自动**的：文件一变它就变。
                    "build": _static_build_id(),
                })
                return
            if route == "/api/stats":
                r = STATE["retriever"]
                if r is None:
                    self._send_json({"detail": STATE["boot_error"] or "索引未就绪"}, 503)
                    return
                s = r.stats()
                s["llm_configured"] = bool(STATE["generator"] and STATE["generator"].available)
                s["llm_chain"] = STATE["generator"].describe() if STATE["generator"] else None
                s["app_name"] = config.APP_NAME
                s["app_version"] = config.APP_VERSION
                s["assistants"] = assistants_mod.listing()
                s["default_assistant"] = assistants_mod.DEFAULT_ID
                self._send_json(s)
                return
            if route == "/api/chapters":
                if STATE["retriever"] is None:
                    self._send_json({"detail": STATE["boot_error"] or "索引未就绪"}, 503)
                    return
                self._send_json({"chapters": _chapters()})
                return
            if route == "/api/rule":
                # 按条款号取**原文**（右栏兜底，2026-10-17）。
                # ⚠️ 只读、不经模型、不进 citations —— 用户点了哪条就给他哪条。
                qs = parse_qs(urlparse(self.path).query)
                cid = (qs.get("id") or [""])[0].strip()
                if not cid:
                    self._send_json(
                        {"detail": "缺少 id（例：/api/rule?id=17.4.1）"}, 400)
                    return
                card = _rule_card(cid)
                if card is None:
                    self._send_json({"detail": f"没有这一条：{cid}", "id": cid}, 404)
                    return
                self._send_json({"item": card})
                return
            if route == "/api/assistants":
                self._send_json({
                    "items": assistants_mod.listing(),
                    "default": assistants_mod.DEFAULT_ID,
                })
                return
            if route == "/api/settings":
                if STATE["retriever"] is None:
                    self._send_json({"detail": STATE["boot_error"] or "索引未就绪"}, 503)
                    return
                st = _settings()
                st["app"] = {
                    "name": config.APP_NAME,
                    "version": config.APP_VERSION,
                    "desc": config.APP_DESC,
                }
                st["runtime"] = {
                    "chunks": len(STATE["retriever"].items),
                    "stats": STATE["retriever"].stats(),
                    "assistants": assistants_mod.listing(),
                    "started_at": STATE["started_at"],
                    "host": HOST,
                    "port": PORT,
                }
                self._send_json(st)
                return
            if route == "/api/files":
                self._send_json({"items": _files()})
                return
            if route == "/api/glossary":
                r = STATE["retriever"]
                if r is None:
                    self._send_json({"detail": STATE["boot_error"] or "索引未就绪"}, 503)
                    return
                self._send_json({"items": r.glossary()})
                return
            if route == "/api/feedback":
                # 只读：原因选项口径 + 当前统计。前端拿它渲染"哪里不对"的选项，
                # 保证前后端选项永远同源，不会改了一边忘另一边。
                self._send_json({"reasons": FEEDBACK_REASONS,
                                 "stats": _feedback_stats(
                                     include_recent=_owner_ok(self.headers))})
                return
            if route == "/api/curated":
                # 裁定可包含私人问题及答案，读取也必须经 Owner 授权。
                if not _owner_ok(self.headers):
                    self._send_json({"detail": "需要 Owner 令牌才能查看人工裁定"}, 403)
                    return
                self._send_json({"items": curated_mod.all_items(), "stats": curated_mod.stats()})
                return
            if route == "/api/practice":
                # 练习器：下发一批题（**已剥掉答案**）。
                # ⚠️ 参数只有"要几道"，没有页码/游标 —— 服务端**不存状态**，
                #   判分靠题 id 查回（见 POST /api/practice）。这样多标签页/重启都安全。
                pool = STATE.get("quiz") or []
                if not pool:
                    self._send_json({"detail": "题库未就绪"}, 503)
                    return
                import random as _rnd
                # ⚠️ 服务端此前**从不解析 query string**（都是 POST body），
                #   所以这里是第一处 —— 用 parse_qs，别自己 split("=")。
                try:
                    raw = (parse_qs(urlparse(self.path).query).get("n") or ["10"])[0]
                    n = max(1, min(50, int(raw)))
                except (TypeError, ValueError):
                    n = 10
                picks = _rnd.sample(pool, min(n, len(pool)))
                self._send_json({
                    "items": [quiz_public(q) for q in picks],
                    "total": len(pool),
                    "kinds": sorted({q["kind"] for q in pool}),
                })
                return
            if route == "/api/update":
                # 桌面客户端的更新清单。没有发布过就返回 404 + 空对象 ——
                # 客户端把它理解成「没有可更新的版本」，这是正常状态不是错误。
                m = load_manifest()
                if not m:
                    self._send_json({"version": None, "note": "尚未发布更新包"}, 404)
                    return
                self._send_json(m)
                return
            if route.startswith("/dl/"):
                self._send_update(route[len("/dl/"):])
                return
            # 其余当作 static/ 下的静态资源
            self._send_static(route.lstrip("/"))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"detail": "服务内部错误"}, 500)

    @_capacity_guard
    def _ask_stream(self, body: dict) -> None:
        """SSE 流式问答：把 `handle_ask` 产出的帧写成 `text/event-stream`。

        ⚠️ 与 `/api/ask` **共用 `handle_ask`**（一份控制流）——
           范围闸门、闲聊、人工库、检索一个都没绕开。这里只负责"把帧写出去"。
        """
        q = str((body or {}).get("q") or "").strip()
        if not q:
            self._send_json({"detail": "问题不能为空"}, 400)
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # ⚠️ 没有 Content-Length（流不知道多长）⇒ **必须**显式声明关闭连接。
            #    少了它，某些浏览器会一直等 socket 关才认为响应读完。
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._security_headers()
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

        def frame(kind: str, data) -> bool:
            """发一帧。返回 False = 对端已断开，调用方应当停止。"""
            try:
                payload = json.dumps({"k": kind, "d": data}, ensure_ascii=False)
                self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                try:
                    self.wfile.flush()          # ⚠️ 不 flush 就不是流式
                except Exception:               # noqa: BLE001
                    pass
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        got_token = False
        got_done = False
        try:
            # ⚠️⚠️ `stream=True` **必须传**。
            #   漏了它 ⇒ `handle_ask` 内部走非流式分支 ⇒
            #   ① 一个 token 帧都不发（"流式"名存实亡）
            #   ② 检索后那帧 meta 也不发（因为 `if stream:` 为假）⇒
            #      用户要一直等到**整段生成完**才收到第一帧。
            #   实测症状：帧统计 `meta=0/token=0/done=1`，总时长与非流式**完全一样**。
            for kind, data in handle_ask(body, stream=True):
                if kind == "token":
                    got_token = True
                elif kind == "done":
                    got_done = True
                    # ⚠️ 状态码哨兵不进前端
                    if isinstance(data, dict):
                        data = {k: v for k, v in data.items() if k != "_err"}
                if not frame(kind, data):
                    return                  # 对端断开 ⇒ 停止生成（生成器会被关闭）
        except capacity.Overloaded as exc:
            # 总预算/队列在流中途用尽：发 error + done，前端不会一直转圈。
            frame("error", {"message": str(exc), "retry_after": exc.retry_after})
            frame("done", {"answer": "", "mode": "overloaded", "provider": None,
                           "retry_after": exc.retry_after})
            got_done = True
        except Exception as exc:            # noqa: BLE001
            traceback.print_exc()
            frame("error", {"message": f"服务内部错误：{type(exc).__name__}"})

        # ⚠️⚠️ 收尾兜底：**无论如何都要有一个 done 帧**。
        #    少了它，前端永远收不到"结束"信号 ⇒ 一直转圈 ——
        #    那正是 owner 报的那个 bug。宁可发一个空的 done，也不要让它转。
        if not got_done:
            frame("done", {"answer": "", "mode": "empty" if not got_token else "partial",
                           "provider": None,
                           "errors": ["生成层没有产出完整结果"]})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_access():
            return
        route = urlparse(self.path).path
        # 2026-10-24：只接受 JSON body。
        # 目的不是"格式洁癖"，而是让浏览器跨站"简单请求"（text/plain、
        # form-urlencoded）无法直接触发写接口/付费接口 —— 这类请求不经过
        # CORS 预检，服务端又不发 ACAO，响应虽读不到，副作用却已经发生。
        _ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        try:
            _has_body = int((self.headers.get("Content-Length") or "0").strip() or "0") > 0
        except (TypeError, ValueError):
            _has_body = True
        if _has_body and _ctype != "application/json":
            self._send_json({"detail": "请求体必须是 application/json"}, 415)
            return
        body = self._read_body()
        if body.get("__too_big__"):
            self._send_json({"detail": "请求体太大"}, 413)
            return
        # ⚠️ 必须在这里拦住（2026-10-09 加）：否则 `__bad_body__` 这个哨兵值会被
        # 当成正常 body 传给 handle_ask/handle_curated，用户看到的是
        # 「问题不能为空」—— 又回到了"错误信息指向无辜字段"的老毛病。
        if "__bad_body__" in body:
            self._send_json({"detail": str(body["__bad_body__"])}, 400)
            return
        # ⚠️ 2026-10-24 R1：`probe` 只能用**独立探针凭据**授权。
        #    不能用回环地址：生产 Caddy 反代后所有远程请求都是回环。
        #    不能用 X-Forwarded-For/UA/Referer：公网可提交。
        body["_probe_ok"] = _probe_token_ok(self.headers)
        # ⚠️⚠️ 2026-10-13：**前端界面诊断上报**（owner 报「一直转圈不出答案」）
        #
        # 为什么单独开一个接口：那个 bug **只在真实浏览器里出现** ——
        # 服务端 200 / answer_len 正常 / JS 控制台无异常，我从外面用 CDP 猜 DOM
        # 猜了**三次全错** ⇒ 只能让前端把它当时的现场自己报回来。
        #
        # ⚠️ 四条纪律：
        #  ① **只收结构化字段**，显式剥掉 `q`（不变成第二条埋点；
        #     隐私声明见 app/querylog.py）
        #  ② 大小上限卡死（前端可被改，不能信它的体积）
        #  ③ 写**独立文件** `data/ui_diag.jsonl`，和用户问答数据分开存
        #  ④ 失败一律静默返回 200 —— 诊断接口绝不能给用户报错
        if route == "/api/ui-diag":
            try:
                # 2026-10-24：允许列表 + 限速 + 有界轮转。
                # 诊断永远返回 200（它的失败不能影响任何用户功能）。
                if _DIAG_RATE.allow():
                    obj = _sanitize_ui_diag(body if isinstance(body, dict) else {})
                    _append_ui_diag(obj)
            except Exception:               # noqa: BLE001 诊断失败不影响任何东西
                pass
            self._send_json({"ok": True})
            return

        try:
            # ⚡ 流式问答。⚠️ 必须放在 `/api/ask` **前面**：
            #   `route == "/api/ask"` 是精确比较，其实顺序无所谓，
            #   但放前面读起来不会让人以为 `/api/ask/stream` 会被上面那条吃掉。
            if route == "/api/ask/stream":
                self._ask_stream(body)
                return
            if route == "/api/ask":
                obj, code = handle_ask_sync(body)
            elif route == "/api/card":
                obj, code = handle_card(body)
            elif route == "/api/branches":
                obj, code = handle_branches(body)
            elif route == "/api/search":
                obj, code = handle_search(body)
            elif route == "/api/feedback":
                obj, code = handle_feedback(body, _owner_ok(self.headers))
            elif route == "/api/curated":
                obj, code = handle_curated(body, _owner_ok(self.headers))
            elif route == "/api/practice":
                obj, code = handle_practice(body)
            else:
                obj, code = {"detail": "未知路径"}, 404
            self._send_json(obj, code)
        except Exception:  # noqa: BLE001
            traceback.print_exc()   # ⚠️ 异常原文只进这里（服务器日志）
            # ⚠️⚠️ 这里**原来**是把 str(exc) 直接回给客户端（2026-10-09 修）。
            # 实测复现：智谱 embedding 欠费时，公网访问者能直接读到
            # 「服务内部错误：EmbedError HTTP 401 insufficient balance」
            # ⇒ 泄露了「用哪家服务 / 账户欠费 / 上游错误码」这类运营信息，
            # 而这个服务监听在公网 8000。**异常原文只进日志，客户端只给通用文案。**
            # （本文件其他错误分支都是精心设计的中文话术，唯独这里裸奔。）
            self._send_json({"detail": "服务暂时不可用，请稍后再试"}, 500)

    def log_message(self, fmt: str, *args) -> None:  # 静音访问日志
        return


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP 接入层线程硬上限：满了直接 429，**不创建线程**。

    2026-10-23 GPT 复核指出的问题：请求级 `capacity.REQUEST` 是在
    Handler 线程里拿信号量的 —— 它限制“同时答题”的数量，但
    `ThreadingHTTPServer` 会为每个连接先开一个线程；排队者仍各占一个线程。
    线程总量必须在这里有界。

    `MAX_HTTP_THREADS` 默认 24 = 12 个在答题 + 12 个最多排队；
    超过 24 的连接在 `process_request` 里直接写 429 并关闭，不进入 Handler。
    """

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._admission = threading.BoundedSemaphore(config.MAX_HTTP_THREADS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._admission.acquire(blocking=False):
            self._reject_overloaded(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._admission.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._admission.release()

    @staticmethod
    def _reject_overloaded(request) -> None:
        body = json.dumps({
            "detail": "现在问的人有点多，请稍后再试",
            "mode": "overloaded",
            "retry_after": 5,
        }, ensure_ascii=False).encode("utf-8")
        head = (
            b"HTTP/1.0 429 Too Many Requests\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Retry-After: 5\r\n"
            b"X-Content-Type-Options: nosniff\r\n"
            b"Referrer-Policy: no-referrer\r\n"
            b"Connection: close\r\n\r\n"
        )
        try:
            request.sendall(head + body)
        except OSError:
            pass
        finally:
            try:
                request.close()
            except OSError:
                pass


def main() -> int:
    if HOST not in ("127.0.0.1", "::1", "localhost") and len(config.PUBLIC_ACCESS_PASSWORD) < 16:
        print("公网/局域网监听需要在 .env 中设置至少 16 位 PUBLIC_ACCESS_PASSWORD，"
              "并通过 HTTPS 反向代理访问。", file=sys.stderr)
        return 2
    boot()
    if STATE["retriever"] is None:
        print(f"[!] 索引未就绪：{STATE['boot_error']}", file=sys.stderr)
        print("    服务在没有索引时不会启动 —— 检索层是核心，半启动只会给出错误答案。", file=sys.stderr)
        print("    请先运行：", file=sys.stderr)
        print("      python scripts/build_corpus.py", file=sys.stderr)
        print("      python scripts/build_index.py    # 这一步需要 .env 里的 ZHIPU_API_KEY", file=sys.stderr)
        print("    若索引已存在却仍报错，上面那行 traceback 会指出是维度不匹配还是文件损坏。", file=sys.stderr)
        return 1
    r: Retriever = STATE["retriever"]
    s = r.stats()
    print("=" * 58)
    print(f"  {config.APP_NAME}  v{config.APP_VERSION}")
    print(f"  语料：{s['rules']} 条规则 / {s['definitions']} 条定义 / 共 {s['chunks']} 个语块")
    print(f"  向量：{s['embed_model']} · {s['dims']} 维")
    print(f"  生成：{STATE['generator'].describe()}")
    print(f"  地址：http://{HOST}:{PORT}")
    print("=" * 58)
    BoundedThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
