"""共享测试夹具。

约定：**任何修改环境变量的测试都必须用 ``monkeypatch``**，
并在结束时让配置缓存失效，避免污染同进程内的其他测试
（``api.app`` 在导入时就构建了一份模块级 runtime）。
"""

from __future__ import annotations

import pytest

from config import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolate_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
