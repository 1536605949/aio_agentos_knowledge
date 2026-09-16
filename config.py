"""集中配置。

所有环境变量读取集中在此，避免散落的 ``os.getenv`` 调用。
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings(BaseModel):
    """运行时配置。通过 :meth:`from_env` 构造。"""

    environment: Environment = Environment.DEVELOPMENT

    # --- 鉴权 ---
    api_key: str | None = None
    approver_key: str | None = None
    allow_insecure: bool = False
    """为 True 时允许在未配置任何密钥的情况下启动（仅限本地开发）。"""

    # --- LLM ---
    llm_provider: str = "deterministic"
    llm_model: str = "deterministic-rule-v1"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2

    # --- 编排 ---
    approval_timeout_seconds: float = 24 * 3600
    reasoning_timeout_seconds: float = 120.0
    max_graph_steps: int = 24

    # --- 横切能力 ---
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 30
    cache_ttl_seconds: float = 30.0
    cache_max_entries: int = 512
    resource_lock_timeout_seconds: float = 10.0
    tool_circuit_threshold: int = 3
    tool_circuit_cooldown_seconds: float = 30.0

    # --- 持久化 ---
    store_backend: str = "memory"
    """``memory`` 或 ``sqlite``。"""
    sqlite_path: str = "aio_agentos.db"

    # --- 可观测 ---
    trace_export_path: str | None = None
    badcase_export_path: str | None = None

    # --- 协议 ---
    a2a_public_endpoint: str = "http://localhost:8000"
    model_config = {"extra": "ignore"}

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=Environment(os.getenv("AIO_AGENTOS_ENV", "development").lower()),
            api_key=os.getenv("AIO_AGENTOS_API_KEY") or None,
            approver_key=os.getenv("AIO_AGENTOS_APPROVER_KEY") or None,
            allow_insecure=_env_bool("AIO_AGENTOS_ALLOW_INSECURE", False),
            llm_provider=os.getenv("AIO_AGENTOS_LLM_PROVIDER", "deterministic").lower(),
            llm_model=os.getenv("AIO_AGENTOS_LLM_MODEL", "deterministic-rule-v1"),
            llm_api_key=os.getenv("AIO_AGENTOS_LLM_API_KEY") or None,
            llm_base_url=os.getenv("AIO_AGENTOS_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            llm_timeout_seconds=_env_float("AIO_AGENTOS_LLM_TIMEOUT_SECONDS", 30.0),
            llm_max_retries=_env_int("AIO_AGENTOS_LLM_MAX_RETRIES", 2),
            approval_timeout_seconds=_env_float("AIO_AGENTOS_APPROVAL_TIMEOUT_SECONDS", 24 * 3600),
            reasoning_timeout_seconds=_env_float("AIO_AGENTOS_REASONING_TIMEOUT_SECONDS", 120.0),
            max_graph_steps=_env_int("AIO_AGENTOS_MAX_GRAPH_STEPS", 24),
            rate_limit_per_minute=_env_int("AIO_AGENTOS_RATE_LIMIT_PER_MINUTE", 120),
            rate_limit_burst=_env_int("AIO_AGENTOS_RATE_LIMIT_BURST", 30),
            cache_ttl_seconds=_env_float("AIO_AGENTOS_CACHE_TTL_SECONDS", 30.0),
            cache_max_entries=_env_int("AIO_AGENTOS_CACHE_MAX_ENTRIES", 512),
            resource_lock_timeout_seconds=_env_float("AIO_AGENTOS_RESOURCE_LOCK_TIMEOUT_SECONDS", 10.0),
            tool_circuit_threshold=_env_int("AIO_AGENTOS_TOOL_CIRCUIT_THRESHOLD", 3),
            tool_circuit_cooldown_seconds=_env_float("AIO_AGENTOS_TOOL_CIRCUIT_COOLDOWN_SECONDS", 30.0),
            store_backend=os.getenv("AIO_AGENTOS_STORE_BACKEND", "memory").lower(),
            sqlite_path=os.getenv("AIO_AGENTOS_SQLITE_PATH", "aio_agentos.db"),
            trace_export_path=os.getenv("AIO_AGENTOS_TRACE_EXPORT_PATH") or None,
            badcase_export_path=os.getenv("AIO_AGENTOS_BADCASE_EXPORT_PATH") or None,
            a2a_public_endpoint=os.getenv("AIO_AGENTOS_A2A_ENDPOINT", "http://localhost:8000"),
        )

    @property
    def auth_configured(self) -> bool:
        return bool(self.api_key)

    def validate_security_posture(self) -> list[str]:
        """返回安全配置问题清单；空列表表示配置合格。"""
        problems: list[str] = []
        if not self.api_key:
            problems.append("AIO_AGENTOS_API_KEY 未配置：所有 API 端点将拒绝请求")
        if not self.approver_key:
            problems.append("AIO_AGENTOS_APPROVER_KEY 未配置：审批端点将拒绝请求")
        if self.environment is Environment.PRODUCTION and self.llm_provider == "deterministic":
            problems.append("生产环境仍在使用 deterministic LLM provider")
        if self.environment is Environment.PRODUCTION and self.store_backend == "memory":
            problems.append("生产环境仍在使用 memory store backend（重启即丢失状态）")
        return problems


_cached: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """进程内单例。测试可通过 ``refresh=True`` 重新读取环境变量。"""
    global _cached
    if _cached is None or refresh:
        _cached = Settings.from_env()
    return _cached


def reset_settings_cache() -> None:
    global _cached
    _cached = None
