# -*- coding: utf-8 -*-
"""集中配置：路径、密钥、模型、检索参数。

密钥只从环境变量 / 同目录 .env 读取，永不写入代码或仓库。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"

# ---------- 应用标识 ----------
APP_NAME = "飞盘规则助手"
APP_VERSION = "0.4.0"
APP_DESC = "基于 WFDF 团队飞盘规则的中文规则问答系统"


def _load_dotenv() -> None:
    """极简 .env 读取（不引入额外依赖）。已存在的环境变量优先。"""
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

# ---------- 服务 ----------
# 先加载 .env 再读取监听配置；进程环境变量仍优先。
HOST = os.environ.get("HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("PORT", "8000"))
PUBLIC_ACCESS_PASSWORD = os.environ.get("PUBLIC_ACCESS_PASSWORD", "").strip()

# ---------- 密钥 ----------
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()

# ---------- 写操作令牌（公网部署的必需防线）----------
# 疑问库（/api/curated 的 action=add|delete）是本项目**唯一**能绕过整个
# 检索 + 生成层、直接把一段文本当作「人工核定」权威答案返回的入口。
# 历史版本曾监听 0.0.0.0 且没有写鉴权——
# 2026-10-07 实测：任何人都能 POST 一条裁定，随后提问即可拿到那条注入的答案，
# 且界面上标着「人工核定」。那等于把产品的存在理由（依据可核查）交给陌生人。
#
# 修法（不引第三方依赖，零部署成本）：
#   · 未设置 CURATED_TOKEN → 写接口**一律拒绝**（fail-closed，不是 fail-open）。
#     本机自测可用 `CURATED_TOKEN=dev` 起服务；线上必须设。
#   · 请求带 `X-Owner-Token` 头比对，用 hmac.compare_digest 防时序侧信道。
#   · 前端 Owner 面板在用户填过 token 后存 localStorage，读取和写入自动带上。
# GET /api/curated 含完整人工裁定，也需要 Owner 令牌。
CURATED_TOKEN = os.environ.get("CURATED_TOKEN", "").strip()

# 探针免写日志的**独立**凭据（2026-10-24 DS-SEC-QA-02 R1）。
# ⚠️ 不能复用 "客户端地址是回环"：生产 Caddy 反代到 127.0.0.1:8000，
#    所有真实远程请求在 Python 看来都是回环。也不能复用 CURATED_TOKEN
#    （那是 Owner 写权限，health cron 不应持有）。未配置独立凭据时
#    探针不免写（fail-closed）。
PROBE_TOKEN = os.environ.get("PROBE_TOKEN", "").strip()

# ---------- 模型 ----------
EMBED_MODEL = os.environ.get("EMBED_MODEL", "embedding-3")
EMBED_DIMS = int(os.environ.get("EMBED_DIMS", "2048"))

# 生成层：按顺序尝试，前一个失败（欠费 / 超时 / 报错）自动换下一个。
# 换模型只改这里，不用动代码。
LLM_PROVIDER_CHAIN = os.environ.get("LLM_PROVIDER_CHAIN", "deepseek,zhipu")
LLM_MODEL_DEEPSEEK = os.environ.get("LLM_MODEL_DEEPSEEK", "deepseek-chat")
LLM_MODEL_ZHIPU = os.environ.get("LLM_MODEL_ZHIPU", "glm-4-flash")

# ---------- 检索 ----------
# 候选条数。取 8 而不是 5：向量对"泛条款"（第 1 章飞盘精神那种）天然偏高，
# 正确条款常被挤到 6~8 位；多给几条交给生成层自己挑，比严卡 top5 更稳。
TOP_K = int(os.environ.get("TOP_K", "8"))

# ---- 入参边界（2026-10-07 补；此前 top_k 完全不校验，实测三种坏结果）----
# `int(top_k)` 直接把用户输入喂进去，实测：
#   "abc"  → ValueError 冒到顶层，返回 **500**（"invalid literal for int()"）
#   -5     → 负数切片 scored[:-5] 生效，**返回 408 条**（全库倒着给）
#   100000 → 一次性把 413 条全下发，单响应 242KB
# 三种都不是"给用户一个错误提示"，而是把内部实现漏出去了。
# 用户输入的 top_k 只在 [1, MAX_TOP_K] 内取整，超出即夹到边界而不是报错。
MIN_TOP_K = 1
MAX_TOP_K = 20        # 检索面板调试用 top_k=20；再大没有诊断价值，只会撑爆响应
# 相关性闸门：最高分低于它 -> 判定"规则里没有"，不进生成层
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.30"))
# 词法通道权重（远低于语义，只作补充）
BIGRAM_WEIGHT = float(os.environ.get("BIGRAM_WEIGHT", "0.08"))
CLAUSE_HINT_BONUS = float(os.environ.get("CLAUSE_HINT_BONUS", "0.35"))
TERM_HIT_BONUS = float(os.environ.get("TERM_HIT_BONUS", "0.04"))
# 类目必检命中时的加分（见 app/routes.py）。
# 这是"保召回"的：字面线索认定这题属于某类情形，就该把该条款族拉进候选，
# 不再听凭余弦排名——实测余弦前 5% 全挤在 0.07 宽的带子里，排名近乎噪声。
#
# 【已否决 · 勿重开】2026-10-03 试过"把族改成候选池边界"（族不给分，只决定谁
# 有资格进池，池内纯余弦排序）——听起来直指尺度冲突根因，实测**更差**：
#   A 现状(常数加分) FAQ@5 95.1% / 62集@8 100%
#   B 池边界(硬)                  86.4% /  88.9%
#   C 池边界+极小常数             80.8% /  77.8%
#   D 分层池(按倍数硬分层)         83.9% /  92.6%
# 原因：池是**所有命中族的并集**（最大 65 条），池内纯余弦排序时"大而泛的族"
# （如"攻防转换" 40+ 条）靠人多势众挤掉"小而准的族"的真解。
# 见 docs/retrieval-ceiling.md 第七节。
#
# 【承重性】这个 0.30 不是拍脑袋的：正解常常裸余弦很低（如 17.2.1 只有 0.740，
# 而同题的通用接触原则 12.7.3 高达 0.990）。是 +0.30 把正解抬到能与通用原则
# 同台竞争的位置。调小它 → 场景题立刻被通用原则淹没（RB=0.24 就掉 1 条 T4）。
ROUTE_BONUS = float(os.environ.get("ROUTE_BONUS", "0.30"))
# 辅助条款（族里那些"相关但答非所问"的条款，如第 12 章的通用接触原则）的加分。
# 必须**明显小于** ROUTE_BONUS：实测主辅同分时，辅助条款因为措辞更贴近
# 用户口语反而排到主条款前面（"我拿着盘被人撞了一下" 12.6.1 压过 17.6.1.1）。
# 保持 >0 是为了在余弦排序塌掉时仍能兜住召回。
ROUTE_BONUS_AUX = float(os.environ.get("ROUTE_BONUS_AUX", "0.10"))

# ---------- 族内"平局打破"（tie-break）----------
# **问题**（447 条 FAQ 实测，124 条未命中全部是"正解连前 50 都进不去"）：
# 命中一个族时，族内**所有**条款吃同样的 +0.30。族一大（读秒 8 条、
# 开盘 11 条），全族被整片抬到同一水平，族内原有的余弦大小关系被抹平，
# 于是"读秒怎么数"给出 9.5/9.6 而不是 9.1——**四条都在族里，谁排前面近乎随机**。
#
# **原做法**：把 +0.30 拆成"基础分 + 族内余弦相对位次的小增量"（0~0.05 的位次增量）。
#
# ⚠️⚠️ **但这个参数从未接入线上**（2026-10-09 全量审计查明）：
# `grep -rn ROUTE_TIEBREAK` 全仓只有这一行定义 + retriever.py:223 一句
# 「已否决」的注释，**代码零引用**。上面那段"做法"是**未落地的设计方案**，
# 不是现役行为 —— 别照着它去理解线上族内定序，也别以为改它能影响什么。
# 线上族内实际就是"同吃 +ROUTE_BONUS、余弦原序"。
ROUTE_TIEBREAK = float(os.environ.get("ROUTE_TIEBREAK", "0.05"))

# 【已否决 · 勿重开】"族内焦点加权"（族内余弦名次递减加分）实测为净负收益：
# Hit@8 94.4% → 88.9%（衰减 0.60~0.85 全档更差）。原因是等额加分虽然抹平了
# 族内区分度，但同时把整族**一起抬进前 8**，兜住了大量"正解余弦排第 12、
# 只是碰巧在族里"的长尾题；改成衰减后族尾被压回原位，长尾召回全丢。
# 优化方向应该是补线索词 / 拆族，不是调这个。
# ⚠️ 同上，这个参数**也是零引用**，保留只为记录那次否决（2026-10-09 核实）。
ROUTE_FOCUS_DECAY = float(os.environ.get("ROUTE_FOCUS_DECAY", "1.0"))

# ---------- 多路融合（RRF）----------
# 为什么需要：线上有"原问题"和"原问题+改写术语"两路向量。
# 全量实测发现改写**整体是负收益**（Hit@5 66.0% vs 基线 69.6%）——
# 改写把查询向量摊宽了（域内余弦中位 0.488 → 0.602，区分度下降），
# 还会把个别问题带偏。但改写对"字面零重叠"的问题（"拿着盘被人撞" → 17.6）
# 又是唯一解。所以两路都要。
#
# ⚠️⚠️ **但 RRF 已被实测否决，线上不是按名次融合**（2026-10-09 全量审计修正
# 这条注释）。原文写的是「**按名次融合**而不是拼成一个查询串」——
# **与代码不符**：`retriever.py:201` 实际是 `cos = [max(pair) for pair in zip(*paths)]`
# 即**逐维取最大值**，retriever.py:221-229 已记录 RRF 被否决的过程。
# `RRF_K` / `FUSE_DEPTH` 只被离线对照脚本（app/accept.py、scripts/ab_test.py）
# 引用，线上 `search()` 从不调用 `rrf_order`。
# 保留仅为让离线脚本还能跑；判断线上融合方式请直接读 retriever.py:201。
RRF_K = int(os.environ.get("RRF_K", "60"))
FUSE_DEPTH = int(os.environ.get("FUSE_DEPTH", "20"))   # 每路参与融合的候选数（仅离线脚本用）

# ---------- 引子型父条的子条补齐 ----------
# **问题**（2026-10-06 从 7 条"引错条款"里挖出来的）：语料里有 52 条规则的正文
# 只有一句引子、内容全在子条里，例如
#     9.6「所谓"从最大 n 恢复读秒"…，含义如下：」
#     8.1「以下情况比赛处于"死盘"状态，此时不可能发生攻防转换：」
#     19.2「技术暂停（Technical Stoppage）：」
# 而生成层 `build_context()` 只补**父条**、从不补**子条** ⇒ 命中 9.6 时模型
# 只看到「含义如下：」，只能答"这条被截断了 / 规则没规定"。
# 实测 447 条真实问法里 **278 条（62%）** 的 top8 里都有这种"空壳父条"，
# 其中 **76 条（17%）的金标就是这个引子父条**——也就是说答案正文压根没进上下文。
# 7 条「引错条款」里有 1 条正是 19.2（问"有伤口能继续打吗"，金标 19.2.1）。
#
# **做法**：命中的若是引子型父条，把它的子条一并放进上下文。
# ⚠️ 这是**纯上下文增补**：排序、闸门、加分一律不动 ⇒ 没有抢位风险。
# 上限取 6：实测每题中位数只需补 2 条，cap≥6 时覆盖面已经吃满（不再增长）。
PARENT_CHILD_MAX = int(os.environ.get("PARENT_CHILD_MAX", "6"))

# ---- 问题长度上限（2026-10-07 补）----
# 实测 10 万字的问题会被原样送去 embedding 与改写 API：
# 智谱直接返回 400（"向量化失败"），用户看到的是一句 500；
# 而短到 2000 字时请求又**能过** —— 也就是说超长输入白白消耗一次调用，
# 还可能把改写缓存 rewrite_cache.jsonl 灌成一条几十万字的垃圾行。
# 规则问题实际都在 100 字以内，500 是极宽松的上限。
MAX_QUERY_CHARS = int(os.environ.get("MAX_QUERY_CHARS", "500"))

# ---- 对话历史条数上限（2026-10-07 补）----
# history 原本无上限，直接拼进提示词（只取最近 4 条，但数组本身要全量
# 解析、传输、序列化）。500 条 × 1000 字会变成半 MB 的请求体。
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "20"))

# ---------- 语料约定 ----------
SOURCE_NAME = "WFDF 团队飞盘规则 2025-2028 中文版"
# 智谱建议：检索类任务只给 query 加前缀，文档端不加（非对称）
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# ---------- 容量边界（2026-10-22）----------
# 每个请求在 ThreadingHTTPServer 里占一个线程；外部模型调用是同步阻塞的。
# 不设上限时，30/100 并发会把线程和连接堆到进程扛不住。
# 这组参数先做**请求级**闸门：超过上限的请求要么排队，要么 429/503。
# ⚠️ 进程内限流；将来多 worker 时要换成跨进程限流（见 docs/adr/0007）。
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MAX_CONCURRENT_REQUESTS", "12"))
QUEUE_TIMEOUT_S = float(os.environ.get("QUEUE_TIMEOUT_S", "8"))
REQUEST_BUDGET_S = float(os.environ.get("REQUEST_BUDGET_S", "60"))

# HTTP 接入层线程硬上限（2026-10-23 GPT 复核后加）
# 请求级闸门在拿到线程后才生效；线程总数必须在这里有界，满了直接 429。
# 默认 24 = 12 个在答题 + 12 个最多排队；超过就快速拒绝。
MAX_HTTP_THREADS = int(os.environ.get("MAX_HTTP_THREADS", "24"))

# 请求体总读取时限（2026-10-24 R4）。
# Handler.timeout 只限制"一次 socket 等待"；滴流客户端每次在超时前送一个字节
# 仍可无限占线程。这里给整个 body 阶段一个**总 deadline**；
# 实际取 min(本值, Handler.timeout)，便于测试用更短 timeout 验证。
BODY_READ_TIMEOUT_S = float(os.environ.get("BODY_READ_TIMEOUT_S", "10"))

# 请求头阶段总时限（2026-10-24 R4）。Handler.timeout 只限制单次等待；
# 头部逐字节滴流同样可能占住线程。Caddy 侧对应 read_header 5s。
HEADER_READ_TIMEOUT_S = float(os.environ.get("HEADER_READ_TIMEOUT_S", "10"))

# ---------- 每日上游付费调用总预算（2026-10-24 DS-SEC-QA-01）----------
# 现有 Gate 只限制"同时"多少请求；不限制一天累计调用多少。
# 一个人持续串行请求、或换用 /api/search、/api/branches 等入口，
# 仍能无限消耗上游 embedding / LLM 费用。
# ⇒ 在**真实上游调用点**计数（embedder._call / generator._chat /
#    generator._chat_stream），而不是只数 HTTP 请求：
#    一次回答可能包含改写 + embedding + 生成 + 兜底重试，必须都算。
# 0 = 不限（只建议离线/测试用）；默认 2000 次/日，约为线上历史均值 10 倍。
# 通过 data/usage.sqlite3 持久计数，重启和同机多进程共享；仍须设置服务商消费上限。
DAILY_PAID_CALL_LIMIT = int(os.environ.get("DAILY_PAID_CALL_LIMIT", "2000"))

# 诊断上报文件上限（字节）。超过后只保留一份 .1 备份，防止无限灌盘。
UI_DIAG_MAX_BYTES = int(os.environ.get("UI_DIAG_MAX_BYTES", str(4 * 1024 * 1024)))
# 诊断/反馈的进程内写入速率上限（条/分钟），不按 IP、不存身份。
UI_DIAG_PER_MIN = int(os.environ.get("UI_DIAG_PER_MIN", "120"))
FEEDBACK_PER_MIN = int(os.environ.get("FEEDBACK_PER_MIN", "60"))
