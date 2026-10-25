# API 契约（v1）

> 这份文档是**前后端之间的合同**，不是使用手册。
> 判据：前端只依赖这里写明的字段；后端改字段之前先改这里 + 加契约测试。
> 最后更新：2026-10-20

## 0. 通用约定

- 基地址：`http://127.0.0.1:8000`（你自托管时按自己的 `HOST` / `PORT` 调整）
- 编码：UTF-8；请求/响应都是 JSON（流式接口除外）
- 读接口**不需要登录**；写人工库需要 `X-Owner-Token`（fail-closed）
- 错误统一为 `{"detail": "人话原因"}`，HTTP 状态码表达类别
- **不收集身份**：没有用户 ID / 会话 ID / Cookie；`/api/ui-diag` 会显式剥掉 `q`
- **版本策略**：只允许**加字段**；删除/改名/改语义 = 破坏性变更，
  必须新开路径（如 `/api/card/v2`）并保留旧路径一个发布周期

### 状态码

| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 请求体/参数不合法（如 `q` 为空、`clause_ids` 不是数组） |
| 403 | 写接口缺少/错误的 `X-Owner-Token` |
| 404 | 路径或条款 id 不存在 |
| 413 | 请求体过大 |
| 429 | 容量已满/排队超时；带 `Retry-After`，payload `mode=overloaded` |
| 500 | 服务内部错误（**不返回异常原文**） |
| 503 | 索引/生成层未就绪 |

---

## 1. 问答核心

### `POST /api/ask`

一次问答的**唯一**同步入口。内部走 `handle_ask`（范围闸门、检索、生成都在里面）。

请求：

```json
{
  "q": "接盘的时候脚踩线了，算出界吗",
  "top_k": 8,
  "history": [{"role": "user", "content": "..."}],
  "assistant": "standard",
  "web": true,
  "probe": false
}
```

- `q` 必填，长度受 `MAX_QUERY_CHARS` 限制
- `history` 只取最近若干条；服务端会清洗 role/content
- `probe: true` 只影响**埋点**（巡检不记成用户提问），不影响回答

响应（200）：

```json
{
  "answer": "算。踩在边线上就是站在界外……",
  "mode": "curated",
  "provider": null,
  "assistant": "standard",
  "intent": null,
  "rewritten": null,
  "citations": [],
  "nearby": [],
  "confident": true,
  "best_cos": null,
  "threshold": null,
  "elapsed_ms": 0,
  "retrieval_ms": 0,
  "generation_ms": 0,
  "total_ms": 12,
  "routes": ["疑问库"],
  "paths": null,
  "forced_count": 0,
  "web": null,
  "usage": null,
  "scope": {"route": "rule", "why": "...", "need_clarify": false},
  "curated": {"id": "...", "question": "...", "source": "...", "by": "...", "tag": "人工核定"},
  "llm_configured": true
}
```

字段词典见 §6。**`citations` 只放官方语料**；联网结果走 `web`，绝不进 `citations`。

⚠️ 三个耗时（2026-10-22 加）：

| 字段 | 含义 |
|---|---|
| `elapsed_ms` | **历史字段，只代表检索**（retriever 自己报的那一段） |
| `retrieval_ms` | 服务端从收到请求到检索结束（含改写 / 闸门） |
| `generation_ms` | 模型生成那一段；curated / 拒答等不生成时为 0 |
| `total_ms` | 用户从发问到拿到结果的端到端 |

判「用户觉得慢」看 `total_ms`；定位再看 `retrieval_ms` / `generation_ms`。
**不要再用 `elapsed_ms` 判断慢请求**——它是检索，不是用户感知。

### `POST /api/ask/stream`

同样的请求体，返回 `text/event-stream`。帧序契约：

```
data: {"k":"meta","d":{...}}      # 恰好 1 帧；检索刚跑完就发
data: {"k":"token","d":"一小段"}  # 0..N 帧；只有规则主路有
data: {"k":"done","d":{...}}      # 恰好 1 帧；payload 与 /api/ask 相同
data: {"k":"error","d":{...}}     # 出错时可能出现在 done 之前
```

- **`done` 必须存在**，即使生成失败也要有一个空 `done`；否则前端会一直转圈
- `meta` 不能重复；重复会让依据区闪两次
- 客户端按 `\n\n` 切帧，不按行
- 耗时字段：`done` 帧的 `d` 与 `/api/ask` 一样带
  `retrieval_ms` / `generation_ms` / `total_ms`；`meta` 只有 `elapsed_ms`
  （检索刚结束，生成还没开始）
- 流式断开 / 生成出错也会写一条 querylog（`mode=client_abort` / `stream_error`），
  **不再静默丢**

### `POST /api/card`

把一次问答包装成一张「判定卡」；内部**只调** `handle_ask_sync`，不另写链路。

请求：`{"q": "..."}`

响应：

```json
{
  "question": "接盘的时候脚踩线了，算出界吗",
  "kind": "judgment",
  "conclusion": "算。踩在边线上就是站在界外……",
  "detail": "很多人以为……",
  "answer": "算。……<!--more-->很多人以为……",
  "next_step": "",
  "citations": [],
  "confident": true,
  "mode": "curated",
  "provider": null,
  "curated": {"...": "..."},
  "scope": {"route": "rule"},
  "elapsed_ms": 0
}
```

- `kind`：`judgment` | `procedure`。只用于**界面入口**（是否显示「如果当时」），不是规则判断
- `conclusion`：回答第一段（`<!--more-->` 之前）；人工裁定没有标记时按第一段/第一句切
- `detail`：其余正文，前端折叠；**只切不改写**
- `next_step`：从回答里摘的一句；**摘不到就是空串**，不补写

### `POST /api/branches`

对**已有判定**做一次受控推演（「如果当时…」）。

请求：

```json
{
  "q": "接盘的时候脚踩线了，算出界吗",
  "answer": "算。……",
  "clause_ids": ["11.4.1", "2.3"]
}
```

- 只收 `clause_ids`，**不收条款原文**；服务端按 id 从语料取，防止伪造规则
- 只对判定题开放（服务端 `is_judgment_question` 再挡一次）

响应：

```json
{
  "branches": [
    {"action": "双脚都落在界内", "consequence": "触盘那刻身体不在界外，接盘成立", "clause_id": "11.4.1"}
  ],
  "need": "",
  "note": null,
  "provider": "deepseek-chat",
  "clauses": [{"id": "11.4.1", "label": null, "text": "……", "chapter_title": "……"}]
}
```

- `branches` 最多 3 条；每条必须绑定给定 `clause_id`，绑不上就丢弃
- `need`：只在缺关键事实时给一句追问；可以为空
- `note`：没有可推演岔路时的人话说明
- 没有 `citations` 字段：推演依据在 `clauses`，来源是官方语料

---

## 2. 检索与规则

### `GET /api/search?q=...&top_k=8`

只跑检索，不生成。响应：

```json
{"q": "...", "hits": [{"id": "11.4.1", "text": "...", "score": 0.82}], "nearby": []}
```

`hits` 的结构同 `citations`（见 §6）。

### `GET /api/rule?id=11.4.1`

按条款号直接取原文（给「点开答案里引用的条款」兜底）。响应 `{"item": {...}}`；不存在 404。

### `GET /api/chapters` / `GET /api/glossary`

- `/api/chapters`：规则章节树，给「查规则」页
- `/api/glossary`：官方名词定义列表

---

## 3. 练习与人工库

### `GET /api/practice` / `POST /api/practice`

- GET 下发题目，**不含答案**
- POST 判分；具体字段见 `app/practice.py`
- 练习成绩只存在当前会话，不落库（已知缺口）

### `GET /api/curated` / `POST /api/curated`

- GET：人工裁定列表 + 统计；必须带 `X-Owner-Token`，否则 403
- POST：新增/删除；必须带 `X-Owner-Token`，否则 403
- 人工裁定的 `source` / `tag` 必须显式标「人工核定」，不得伪装成官方正文

---

## 4. 更新与运维

### `GET /api/update`

更新清单。客户端只读 `launcher` 对象：

```json
{
  "launcher": {
    "version": "0.3.8",
    "name": "Shultimate-0.3.8.exe",
    "size": 7808289,
    "sha256": "04b5...",
    "released_at": "2026-10-20T22:45:03+08:00",
    "notes": "0.3.8：……",
    "force": false
  }
}
```

- 客户端按 `launcher.version > 自己` 判断，**直接下载 `launcher.name`**
- 没有版本链 / 逐版补丁；跨版本一次升到最新
- `force: true` 只给 P0/安全修复，用来绕过「一天最多自动更新一次」

### `GET /dl/<name>`

更新包下载；支持 `Range`（断点续传），返回 200/206。

### `GET /api/health`

```json
{"ready": true, "chunks": 433, "error": "", "llm_configured": true, "version": "0.4.0", "build": "8dcd5cc4cbd9"}
```

`build` 是静态文件指纹；前端靠它判断「服务端换了新版」。

### `GET /api/stats` / `GET /api/settings` / `GET /api/files`

只读的统计/设置/语料清单；字段见前端 `loadData()` 与 `viewOverview()` 的读取处。

### `POST /api/ui-diag`

前端界面诊断；**只收结构化字段**，服务端显式 `pop("q")`，不存问题文本。

### 2026-10-24 DS-SEC-QA-02 回修补充

- `POST /api/ask`（及 health cron 的同类探针）如带 `X-Probe-Token` 且服务端
  独立的 `PROBE_TOKEN` 匹配，才允许 `probe=true` 免写查询日志；
  没有 token 时 `probe` 只是普通请求，**不再凭回环地址授权**。
- `GET /api/practice` 公开字段只有 `id/kind/prompt/options`；`answer/why/explain/evidence`
  等判分字段不下发。
- `GET /api/feedback` 的 `recent`（含 q/comment）只回给带 `X-Owner-Token` 的 Owner；
  匿名只拿聚合计数。
- `POST /api/ui-diag` 只接受允许列表字段（kind/strays/ts/d 的受限子集），
  不再接受任意嵌套对象；有每分钟限速与 4MB 轮转。
- 请求体只支持 `Content-Length`；带 `Transfer-Encoding: chunked` 的请求按设计返回 400
  （官方前端发送 JSON 字符串，会自动带 Content-Length；Caddy 会保留 chunked，不自动归一化）。

---

## 5. 兼容性检查单（改接口前过一遍）

```
□ 只加字段，没删/没改名/没改语义？
□ 新字段有默认值，旧前端不传也能跑？
□ citations 仍然只来自官方语料？
□ 错误仍然是 {"detail": "..."}？
□ 没把用户身份写进请求/响应/日志？
□ /api/ask 与 /api/ask/stream 仍然共用 handle_ask？
□ 契约测试 tests/test_api_contract_2026_10_20.py 跑过？
```
