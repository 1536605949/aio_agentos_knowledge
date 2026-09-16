"""指标采集。

进程内累加，零外部依赖。通过 ``GET /metrics`` 暴露，可被 Prometheus 侧车抓取
（键名已按 ``aio_agentos_`` 前缀规范化）。生产环境可替换为 OTel Meter 实现同一接口。
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from observability.models import MetricSample

PREFIX = "aio_agentos"


class MetricsCollector:
    """计数器 + 仪表 + 时长分布。"""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._durations: dict[str, list[float]] = {}
        self._lock = RLock()

    # -------------------------------------------------------------- 写入接口

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def gauge(self, name: str, value: float, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = _key(name, labels)
        with self._lock:
            bucket = self._durations.setdefault(key, [])
            bucket.append(value)
            if len(bucket) > 2048:
                del bucket[: len(bucket) - 2048]

    # -------------------------------------------------------------- 读取接口

    def counter(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get(_key(name, labels), 0.0)

    def percentile(self, name: str, quantile: float, **labels: str) -> float:
        with self._lock:
            values = sorted(self._durations.get(_key(name, labels), []))
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(round(quantile * (len(values) - 1)))))
        return round(values[index], 3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            durations = {name: list(values) for name, values in self._durations.items()}

        latency: dict[str, dict[str, float]] = {}
        for name, values in durations.items():
            ordered = sorted(values)
            latency[name] = {
                "count": len(ordered),
                "p50": round(ordered[len(ordered) // 2], 3) if ordered else 0.0,
                "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3) if ordered else 0.0,
                "max": round(ordered[-1], 3) if ordered else 0.0,
                "avg": round(sum(ordered) / len(ordered), 3) if ordered else 0.0,
            }
        return {"counters": counters, "gauges": gauges, "latency_ms": latency}

    def samples(self) -> list[MetricSample]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            durations = {name: list(values) for name, values in self._durations.items()}

        samples = [MetricSample(name=name, value=value) for name, value in sorted(counters.items())]
        samples += [MetricSample(name=name, value=value, unit="gauge") for name, value in sorted(gauges.items())]
        for name, values in sorted(durations.items()):
            if values:
                ordered = sorted(values)
                samples.append(
                    MetricSample(
                        name=f"{name}.p95",
                        value=round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3),
                        unit="ms",
                    )
                )
        return samples

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._durations.clear()


def _key(name: str, labels: dict[str, str]) -> str:
    full = name if name.startswith(PREFIX) else f"{PREFIX}_{name}"
    if not labels:
        return full
    suffix = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
    return f"{full}{{{suffix}}}"
