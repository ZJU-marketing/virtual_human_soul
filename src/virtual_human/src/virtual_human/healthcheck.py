"""自检 (health check) 框架: 接口 + 注册表 + 执行器.

各模块实现 HealthCheck 接口 (见 checks.py 中的示例实现), app 启动时注册:

    from virtual_human.healthcheck import register_check, run_checks
    register_check(MyCheck())        # 启动时注册实现类
    results = await run_checks()     # 并发执行, 结果写 vh.health 日志

约定:
    - check() 正常返回说明文字 => 通过; 抛异常 => 失败 (异常文本作为 detail)
    - 单项超时 5 秒, 避免依赖不可达时拖死启动
    - 自检不阻断启动 (失败仅告警), 供日志/接口观测
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("vh.health")

CHECK_TIMEOUT = 5.0  # 单项自检超时 (秒)


@dataclass
class CheckResult:
    """单项自检结果."""

    name: str
    ok: bool
    detail: str = ""
    elapsed_ms: float = 0.0


class HealthCheck(Protocol):
    """自检接口: 各模块实现 name 与异步 check()."""

    name: str

    async def check(self) -> str: ...  # 返回说明文字; 抛异常 = 失败


_REGISTRY: dict[str, HealthCheck] = {}


def register_check(impl: HealthCheck) -> None:
    """注册自检实现 (同名覆盖, 幂等; app 启动时调用)."""
    if impl.name not in _REGISTRY:
        log.info("已注册自检: %s (%s)", impl.name, type(impl).__name__)
    _REGISTRY[impl.name] = impl


def registered_checks() -> list[str]:
    """当前已注册的自检项名称."""
    return list(_REGISTRY)


async def _run_one(impl: HealthCheck) -> CheckResult:
    start = time.perf_counter()
    try:
        detail = await asyncio.wait_for(impl.check(), timeout=CHECK_TIMEOUT)
        ok = True
    except asyncio.TimeoutError:
        ok, detail = False, f"超时 (>{CHECK_TIMEOUT:g}s)"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    return CheckResult(
        name=impl.name,
        ok=ok,
        detail=detail,
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


async def run_checks() -> list[CheckResult]:
    """并发执行全部已注册自检, 结果按注册顺序返回并写日志."""
    impls = list(_REGISTRY.values())
    results = await asyncio.gather(*(_run_one(i) for i in impls))

    for r in results:
        if r.ok:
            log.info("[自检] %-12s OK   %6.0fms  %s", r.name, r.elapsed_ms, r.detail)
        else:
            log.warning("[自检] %-12s FAIL %6.0fms  %s", r.name, r.elapsed_ms, r.detail)

    bad = [r.name for r in results if not r.ok]
    if bad:
        log.warning("自检结束: %d/%d 通过, 失败项: %s", len(results) - len(bad), len(results), ", ".join(bad))
    else:
        log.info("自检结束: %d/%d 全部通过", len(results), len(results))
    return results
