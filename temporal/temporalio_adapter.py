"""Production Temporal adapter boundary.

The domain workflow is intentionally independent from Temporal SDK imports. This module
contains the integration seam so deterministic workflow code can be implemented/deployed
when `temporalio` is installed and a Temporal cluster is configured.
"""
from __future__ import annotations


def temporalio_available() -> bool:
    try:
        import temporalio  # noqa: F401
        return True
    except ImportError:
        return False
