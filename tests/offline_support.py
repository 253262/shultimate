#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DS-SEC-QA-02 离线测试公共设施（R5）。

本文件只做两件事：
  1. `block_external_network()`：测试进程里任何非回环连接立即失败，
     **不允许静默跳过**；
  2. `install_lexical_cosine()`：给 7 个原本调用真实智谱 embedding 的
     检索模块一个确定性的本地 test double。它**只验证代码路径**，
     不代表真实 embedding 检索质量；真实质量看 `tools/fast_regress.py`
     的缓存指标 / 单独的真实 API 评测。

⚠️ 不要把这个替身说成"真实检索全绿"。
"""
from __future__ import annotations

import socket

_ORIG_CONNECT = socket.socket.connect
_BLOCKED = False
_LEXICAL = False


def block_external_network() -> None:
    global _BLOCKED, _ORIG_CONNECT
    if _BLOCKED:
        return
    _ORIG_CONNECT = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) and address else None
        if host not in ("127.0.0.1", "::1", "localhost", None):
            raise RuntimeError(
                "offline test gate: external network is blocked, tried %r" % (address,))
        return _ORIG_CONNECT(sock, address)

    socket.socket.connect = guarded
    _BLOCKED = True


def unblock_external_network() -> None:
    global _BLOCKED
    if _BLOCKED:
        socket.socket.connect = _ORIG_CONNECT
        _BLOCKED = False


def install_lexical_cosine() -> None:
    from app import retriever as rmod

    global _LEXICAL
    if _LEXICAL:
        return

    def fake_cos(self, text):
        q = rmod._bigrams(text)
        best_i, best = 0, -1.0
        for i, db in enumerate(self._doc_bg):
            v = rmod._dice(q, db)
            if v > best:
                best_i, best = i, v
        v = [0.0] * self.dims
        v[best_i] = 1.0
        return v

    rmod.Retriever._cos = fake_cos
    _LEXICAL = True
