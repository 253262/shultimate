# ADR-0007：容量边界用请求级闸门，暂不替换 HTTP transport

- 状态：已采纳（第一步）
- 日期：2026-10-22
- 相关：`app/capacity.py`、`app/server.py`、`docs/ops.md`、Task 3

## 背景

`app/server.py` 使用 `ThreadingHTTPServer`：**每个请求一个线程**，而生成层/向量层
都是同步阻塞调用（一次 3–10 秒）。当前没有并发上限、有限队列、429 策略。

真实故障模式不是"答得慢"，而是：30/100 并发时线程与连接一起涨 →
进程内存触顶 → pm2 `max_memory_restart` 重启 → **正在答题的人全部丢失**。

## 决策

1. **HTTP 接入层**先做线程硬上限：`BoundedThreadingHTTPServer` +
   `MAX_HTTP_THREADS=24`。满了直接 **429**，**不创建线程**。
2. **请求级**再做问答并发闸门：`BoundedSemaphore(MAX_CONCURRENT_REQUESTS=12)` +
   `QUEUE_TIMEOUT_S=8`；拿不到名额 → 429 + `Retry-After`、`mode=overloaded`。
3. 保留 `ThreadingHTTPServer`，**不替换 transport**。
4. `REQUEST_BUDGET_S=60` 传入 `_chat` / `_chat_stream` / `embedder._call`：
   调用前 `check()`，timeout 取 `min(原 timeout, remaining())`；流式超时发
   `error` + `done(mode=overloaded)`。
5. 暂不拆"生成 / 向量"两个上游闸门，也不做熔断；等压测证明请求级够粗再加。

## 为什么

- **只做请求级不够**（2026-10-23 GPT 复核实测）：信号量在 Handler 线程里才拿，
  `ThreadingHTTPServer` 会先为每个连接开线程；排队者仍各占一个线程。
  线程总量必须在接入层有界，否则"12 个名额"不等于"12 个线程"。
- 接入层拒绝 + 请求级排队，两层各自简单、可独立回滚；不碰生成器内部 6 个重试循环
  的结构，只加预算检查。
- 换 transport（asyncio / gunicorn / 线程池）是更大的变更，当前**没有实测证据**
  证明它是瓶颈；按项目纪律，先加最小防线，再让压测决定下一步。
- 项目零运行时第三方依赖；`BoundedSemaphore`/`ThreadingHTTPServer` 都是标准库。

## 后果

- ✅ 超过容量时出现**可预测的受控拒绝**，进程保持存活
- ✅ 线程总数有界（默认 24）；问答并发有界（默认 12）
- ✅ 总预算超时不再"拿到名额后无限跑"；流式有 error+done
- ⚠️ 进程内限流：pm2 多 worker 时要换成跨进程限流（本机单 worker）
- ⚠️ 请求预算超时会中断已开始的生成（这是有意的；用户拿到可重试提示）
- ⚠️ 接入层 429 是裸 HTTP 响应，不经过 Handler/埋点；不记录用户问题

## 回滚

删除 `app/capacity.py`、去掉 `handle_ask_sync` / `_ask_stream` 的两处调用、
删掉 `config.py` 的三个参数即可；单 commit 可回退。

## 以后再看

- 压测 30/100 并发，记录 p50/p95、429 比例、线程数、内存、是否被 pm2 重启
- 若请求级闸门仍让上游被打爆，再拆 `GENERATION` / `EMBEDDING` 两个上游闸门
  + 连续失败熔断（半开探测）
- 若 `ThreadingHTTPServer` 在 100 并发下仍不可控，另开 ADR 讨论 transport
