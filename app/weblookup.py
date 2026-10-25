# -*- coding: utf-8 -*-
"""联网兜底：本地语料答不出来时，去 **WFDF 官方 PDF** 抓一手规则原文。

**为什么只抓官方 PDF，不做通用搜索（2026-10-04 实测结论）**
    维护者要求"可以上网搜"。我实测了三条路，结论是**通用搜索在飞盘这个垂类上无效**：

    | 方案 | 实测结果 |
    |---|---|
    | Bing 搜「有伤口能继续打吗」 | 返回《剑桥词典 injury 的释义》+ ScienceDirect 期刊页，**一条规则都没有** |
    | Bing 搜「飞盘比赛最长几分钟」 | 返回「length 在剑桥英语词典中的解释」——搜索引擎对垂类口语问法没有理解能力 |
    | rules.wfdf.sport（官方在线规则） | 86KB HTML 里只有 1.6KB 文本，**规则正文是 JS 动态加载的**，纯 HTTP 抓不到 |
    | wfdf.sport（官方首页） | 抓到的是 "Complete courses, watch webinars…" 这类导航文字 |

    唯一稳定的一手源是官方 PDF：
        https://rules.wfdf.sport/wp-content/uploads/2024/12/WFDF-Rules-of-Ultimate-2025-2028.pdf
    它是静态文件、可纯 zlib 解压出全文、且**就是规则的权威版本**。
    比任何二手博客都可靠。

    所以本模块的做法是：抓官方 PDF → 解压出全文 → 用 2-gram 定位关键词附近的**文本窗口**。
    **不做通用搜索**。宁可告诉用户"没找到"，也不给词典释义冒充规则。

    ⚠️ 关于"抽英文条款"（2026-10-09 澄清）：本模块里 `_clauses_of()` / `_pick()`
    两个函数能切出"看起来像条款号"的片段，但**它们零调用点、是死代码**，
    `lookup()` 实际走的是 `_windows()`，返回结构里**根本没有条号**。
    原因在 `_clauses_of` 自己的 docstring 里已写明：联网抓的 PDF 条号不可靠，
    "绝不能当作准确条号回给用户" —— 所以那条路被放弃了，代码留在原地。
    下一个人别以为它在跑，也别基于"条号不可靠"这个前提做决策。

**⚠️ 硬约束（不要改）：联网结果绝不能进 citations**
    citations 在前端是"规则原文"的可信呈现（带条款号、可点击溯源）。
    网络内容是二手的，混进去 = 把二手信息伪装成官方原文。
    所以只走 `web` 字段，前端单独一块区域渲染，顶部标注"⚠️ 来自网络"。

**零第三方依赖**：urllib + zlib，与项目其余部分一致。
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
}

TIMEOUT = 25
# 2026-10-24：官方 PDF 约 1MB，但上游/CDN 异常时不能无限读进内存。
# 超过这个上限直接放弃（返回空文本，由 lookup 的异常/空结果兜底）。
MAX_PDF_BYTES = 5 * 1024 * 1024

# WFDF 官方一手源。只收 rules.wfdf.sport（WFDF 自己的域名），
# usaultimate.org 是另一个组织（美国极限飞盘协会），规则版本不一定同步，故不收。
OFFICIAL_PDF = ("https://rules.wfdf.sport/wp-content/uploads/2024/12/"
                "WFDF-Rules-of-Ultimate-2025-2028.pdf")

# 官方站点首页：用于回答"规则里有没有提到这件事"这类存在性问题
OFFICIAL_SITE = "https://wfdf.sport/"

# PDF 抓取缓存（内存）。抓一次 ~1MB，够用一整天。
_PDF_CACHE: dict[str, str] = {}


# ---------------------------------------------------------------- PDF 解析

def _pdf_text(url: str, timeout: int = TIMEOUT) -> str:
    """下载并抽取 PDF 纯文本（纯 zlib，不依赖任何 PDF 库）。

    ⚠️ **WFDF 这份 PDF 是 LaTeX 生成的，字形级切分，两个坑**：
      ① 每个字形/词片段都是独立的 PDF 字符串，抽取出来是
         `Spir it  of  t he  G am e` —— **词中间有空格**，
         直接搜 "Scoring" 一条都搜不到（实测 count=0）。
         必须先把"字母之间的多余空白"压掉。
      ② 每段都夹着 `en-GB` / `en-US` 语言标记，是 PDF 的实际文本，
         混在正文里当噪音（实测每段前缀都有）。

    办法：解压每个内容流 → 抽 `(...)` 里的字符串 → 拼接 →
    用 `stitch` 规则把被打散的字母缝回整词。
    """
    if url in _PDF_CACHE:
        return _PDF_CACHE[url]
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_PDF_BYTES + 1)
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError("官方 PDF 超过大小上限")

    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n", raw):
        start = m.end()
        end = raw.find(b"endstream", start)
        if end < 0:
            continue
        try:
            data = zlib.decompress(raw[start:end])
        except zlib.error:
            continue
        if b"Tj" not in data and b"TJ" not in data:
            continue
        for sm in re.finditer(rb"\((?:\\.|[^\\()])*\)", data):
            s = sm.group(0)[1:-1]
            s = re.sub(rb"\\([()\\])", rb"\1", s)
            try:
                chunks.append(s.decode("latin-1"))
            except UnicodeDecodeError:
                pass
        chunks.append("\n")

    text = " ".join(chunks)
    for a, b in (("\\050", "("), ("\\051", ")"), ("\\/", "/"), ("\\&", "&")):
        text = text.replace(a, b)
    text = re.sub(r"\\\d{3}", "", text)

    # ① 去掉语言标记（PDF 里夹带的实际文本，纯噪音）
    text = re.sub(r"\b(?:en-US|en-GB|en-AU|en-CA)\b", " ", text)
    # ② 缝回被打散的字母。
    #    LaTeX 生成 PDF 时按 kerning 切字形，实测会切在半途：
    #      `Spir i t  o f  t he  G am e`、`Ultim ate`、`WF DF`
    #    规律：**长度 1 的片段（及首尾被切下的单字母）必须与邻居粘连**。
    #    反复迭代收敛 —— 一轮只能吃掉一部分（`Spir i t` 要两轮才成 `Spirit`）。
    for _ in range(6):
        # 单字母片段两侧都不留空格
        text = re.sub(r"(?<=\S)\s+([A-Za-z])\s+(?=[A-Za-z])", r"\1", text)
        # 单字母片段紧贴前词（左粘）
        text = re.sub(r"([A-Za-z]{2,})\s+([A-Za-z])\b", r"\1\2", text)
        # 单字母片段紧贴后词（右粘）
        text = re.sub(r"\b([A-Za-z])\s+([A-Za-z]{2,})", r"\1\2", text)
    # ③ 压掉多余空白
    text = re.sub(r"[ \t\xa0]+", " ", text)
    _PDF_CACHE[url] = text
    return text


# ---------------------------------------------------------------- 片段定位

def _clauses_of(text: str) -> list[tuple[str, str]]:
    """从英文规则全文里切出 (条号, 正文)。

    官方条号形如 `14.2`、`10.1.2`，最多 5 段。
    ⚠️ 深度必须放到实际最大深度（5 段）—— 早前正则只吃 4 段，
    把 10 条 5 段条款截断成父条款名，凭空造出 10 个"重复"，
    害我误判成"官方 358 条"。这个教训写在这里，别再踩。

    ⚠️ 但对**这份联网抓的 PDF 不要指望条号完整**（2026-10-04 实测）：
    纯 zlib 字形提取会丢字（`Ca lling Fo uls`、`WF DF`），
    数字条号尤其容易缺（实测 `14.2` 在提取文本里 0 次命中）。
    所以这里切出来的条款**只用于"官方文档里大概写在这附近"**，
    绝不能当作准确条号回给用户。
    真正的兜底价值是：**证明/否证"官方文档里确实谈了这件事"**。
    """
    if not text:
        return []
    pat = re.compile(r"(?<![0-9A-Za-z.])(\d{1,2}(?:\.\d{1,2}){1,4})\.(?=\s)")
    found: list[tuple[int, str]] = []
    for m in pat.finditer(text):
        found.append((m.start(), m.group(1)))
    out: list[tuple[str, str]] = []
    for i, (pos, cid) in enumerate(found):
        nxt = found[i + 1][0] if i + 1 < len(found) else len(text)
        body = re.sub(r"\s+", " ", text[pos + len(cid) + 1:nxt]).strip()
        if body:
            out.append((cid, body[:400]))
    return out


def _windows(text: str, query: str, limit: int = 4) -> list[str]:
    """按 2-gram 命中找原文片段（不做条号切分）。

    这是联网兜底的主力：官方 PDF 全文 → 找最相关的几段原话。
    比切条号可靠，因为不依赖条号完整，只依赖正文词形。
    """
    grams = {query[i:i + 2].lower() for i in range(len(query) - 1)
             if query[i].isalpha()}
    if not grams:
        return []
    out: list[tuple[float, str]] = []
    for sent in re.split(r"(?<=[\n.!?])\s+", text):
        s = re.sub(r"\s+", " ", sent).strip()
        if len(s) < 30 or len(s) > 400:
            continue
        # 目录行/页码行：全是点和数字，直接跳过
        if s.count(".") > 12 or re.fullmatch(r"[\d\s.]+", s):
            continue
        b = s.lower()
        hits = sum(1 for g in grams if g in b)
        if hits >= 2:
            out.append((hits / len(grams) - len(s) / 20000.0, s))
    out.sort(reverse=True)
    return [_readable(s) for _, s in out[:limit]]


# 官方 PDF 里的常见断字（LaTeX kerning 在词中间插空格）。
# 只列高频且无歧义的，宁可漏修也不要改错词。
#
# ⚠️ 2026-10-09 清掉了 4 条 **a → a 的自映射**，它们原来是从候选词表复制进来
# 时忘了剔除的无效项，每次联网兜底都白跑 4 次字符串替换：
#     ("to ", "to ")  ("of the ", "of the ")  ("is are", "is are")  ("occurs", "occurs")
# 更麻烦的是它们**伪装成"已处理的断字"** —— 让人以为 to / of the / occurs
# 的断字问题已解决，而实际上这些词会原样出现在输出里。
_FIXES: list[tuple[str, str]] = [
    ("in to ", "into "),
    ("du ring", "during"), ("th at", "that"), ("w ith", "with"),
    ("af ter", "after"), ("fo r", "for"),
    ("a nd", "and"), ("n ot", "not"),
    ("res pect", "respect"), ("sa fety", "safety"),
    ("obstructi on", "obstruction"), ("si ze", "size"),
    ("di sc", "disc"), ("pl ay", "play"), ("ru les", "rules"),
]


def _readable(s: str) -> str:
    """把断字缝回正常英文。仅做展示用，不影响检索。"""
    for a, b in _FIXES:
        s = s.replace(a, b)
    # 兜底：把 `xx yy` 这种"两段都是 1~3 字母碎片"的情况粘回去
    s = re.sub(r"\b([a-z]{1,3}) ([a-z]{1,3})\b(?=[a-z])", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


def _pick(clauses: list[tuple[str, str]], query: str, limit: int = 4) -> list[dict]:
    """按 2-gram 命中率给条款排序，返回最相关的几条。"""
    grams = {query[i:i + 2].lower() for i in range(len(query) - 1)
             if query[i].isalpha()}
    if not grams:
        return []
    scored: list[tuple[float, str, str]] = []
    for cid, body in clauses:
        b = body.lower()
        hits = sum(1 for g in grams if g in b)
        if not hits:
            continue
        # 命中率为主，长度做轻微惩罚（避免整条超长条款靠体量取胜）
        scored.append((hits / len(grams) - len(body) / 20000.0, cid, body))
    scored.sort(reverse=True)
    return [{"clause": cid, "text": body, "score": round(s, 3)}
            for s, cid, body in scored[:limit] if s > 0.12]


# ---------------------------------------------------------------- 口语→英文

# 口语 → 检索用英文词。只做常见一批，**宁少勿滥**：
# 猜错词会让搜索完全跑偏，比不搜更糟。
_EN_TERMS: list[tuple[tuple[str, ...], str]] = [
    (("得分", "进球", "goal", "scoring", "score", "point"), "goal scoring call goal"),
    (("停", "停止", "stop", "stopped"), "play stops stoppage"),
    (("犯规", "foul"), "foul called"),
    (("违规", "violation"), "violation called"),
    (("走步", "travel"), "travel violation"),
    (("掩护", "屏幕", "pick"), "pick violation"),
    (("读秒", "stall"), "stall count"),
    (("接盘", "接住", "catch"), "catch receiving"),
    (("出界", "界外", "out of bounds"), "out of bounds"),
    (("换人", "替补"), "substitution"),
    (("暂停", "timeout", "time-out"), "time-out"),
    (("受伤", "伤停", "伤口", "流血", "injury"), "injury safety stoppage"),
    (("盘权", "possession"), "possession"),
    (("发盘", "开盘", "pull"), "pull"),
    (("轴心", "pivot", "转身"), "pivot"),
    (("精神", "spirit"), "spirit of the game"),
    (("判罚", "判错", "误判", "叫错"), "call retracted incorrect"),
    (("界内", "inside line"), "line"),
    (("验盘", "check"), "check"),
    (("撞", "碰到", "身体接触", "接触"), "contact foul"),
    (("场地", "场多大", "尺寸", "多大", "多长", "多宽", "边线", "底线"),
     "field dimensions end zone"),
    (("多长时间", "几分钟", "时长", "打多久"), "length of game time"),
    (("几个人", "人数", "上场人数"), "number of players"),
    (("队长",), "captain"),
    (("计分", "比分", "记分"), "score"),
    (("天气", "下雨", "湿滑", "打雷"), "weather rain"),
    (("进攻", "防守", "攻守"), "offense defense"),
    (("传盘", "传球", "传递", "出手"), "throw pass"),
    (("半场", "中场", "换边"), "half time interval"),
    (("抗议", "不同意", "不听判罚"), "dispute contest"),
    (("精神原则",), "spirit"),
]


def _to_english(q: str) -> str:
    parts: list[str] = []
    for keys, en in _EN_TERMS:
        if any(k in q for k in keys):
            parts.append(en)
    return " ".join(dict.fromkeys(parts))


# ---------------------------------------------------------------- 主入口

def lookup(query: str, limit: int = 4) -> dict | None:
    """联网兜底主入口。抓官方 PDF 找最相关原文段落。失败返回 None。

    返回结构里**没有 citations 字段** —— 见模块 docstring 的硬约束。
    也不返回条号 —— 纯 zlib 提取的条号不可靠（见 `_clauses_of` 的警告）。
    """
    q = (query or "").strip()
    if not q:
        return None

    en = _to_english(q)
    if not en:
        # 抽不出英文词就不硬搜。中文关键词在英文 PDF 里必然零命中，
        # 跑了也是浪费一次 1MB 下载。
        return None

    try:
        text = _pdf_text(OFFICIAL_PDF)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, zlib.error):
        return None
    if len(text) < 5000:
        return None

    snips = _windows(text, en, limit)
    if not snips:
        return None

    return {
        "kind": "official_pdf",
        "source": "WFDF Rules of Ultimate 2025-2028（官方 PDF 原文）",
        "url": OFFICIAL_PDF,
        "site": OFFICIAL_SITE,
        "snippets": snips,
        "disclaimer": ("⚠️ 抓自 WFDF 官网 PDF 的**英文原文**（机器提取，可能有断字），"
                       "且未与中文语料逐条核对；本项目中文语料未收录的细节才走这条路。"
                       "**不要把它当成中文条款号引用**，请点链接去官网确认。"),
    }
