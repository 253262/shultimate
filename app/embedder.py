# -*- coding: utf-8 -*-
"""向量化：智谱 embedding-3。

**只依赖 Python 标准库**（urllib）——本项目刻意不引入任何第三方包，
这样任何人 clone 下来不需要 pip install 就能跑。

设计取舍：
  · 2048 维是唯一返回**单位向量**的档位；512/1024 维模长不足 1，必须先归一化。
  · query 侧加检索前缀、文档侧不加（非对称），实测是提升最大的单项配置。
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request

from . import capacity
from . import config

ZHIPU_EMBED_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
_BATCH = 32
_TIMEOUT = 60.0
_RETRY = 2


class EmbedError(RuntimeError):
    pass


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if not n:
        return [0.0] * len(vec)
    return [x / n for x in vec]


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise EmbedError(f"HTTP {exc.code}: {detail}") from exc


def _call(texts: list[str]) -> list[list[float]]:
    if not config.ZHIPU_API_KEY:
        raise EmbedError("未配置 ZHIPU_API_KEY")
    payload = {
        "model": config.EMBED_MODEL,
        "input": texts,
        "dimensions": config.EMBED_DIMS,
    }
    headers = {
        "Authorization": f"Bearer {config.ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    last: Exception | None = None
    for attempt in range(_RETRY + 1):
        try:
            capacity.check()                 # 总预算用尽 ⇒ 不再重试
            capacity.charge("embedding")     # 每日付费调用总预算（2026-10-24）
            body = _post(ZHIPU_EMBED_URL, payload, headers,
                         capacity.timeout(_TIMEOUT))
            data = body.get("data") or []
            if len(data) != len(texts):
                raise EmbedError(f"返回条数不符：期望 {len(texts)} 实得 {len(data)}")
            data.sort(key=lambda d: d.get("index", 0))
            return [_normalize(d["embedding"]) for d in data]
        except capacity.Overloaded:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < _RETRY:
                time.sleep(1.2 * (attempt + 1))
    raise EmbedError(f"向量化失败：{last}")


def embed_documents(texts: list[str], progress: bool = True) -> list[list[float]]:
    """文档端：不加前缀。"""
    out: list[list[float]] = []
    total = len(texts)
    for i in range(0, total, _BATCH):
        out.extend(_call(texts[i:i + _BATCH]))
        if progress:
            print(f"    已向量化 {min(i + _BATCH, total)}/{total}", flush=True)
    return out


def embed_query(text: str) -> list[float]:
    """查询端：加检索前缀（非对称检索的关键）。"""
    return _call([config.QUERY_PREFIX + text])[0]


def embed_queries(texts: list[str]) -> list[list[float]]:
    """批量查询端（评测用，省掉逐条往返）。"""
    out: list[list[float]] = []
    prefixed = [config.QUERY_PREFIX + t for t in texts]
    for i in range(0, len(prefixed), _BATCH):
        out.extend(_call(prefixed[i:i + _BATCH]))
    return out
