"""BadCase 采集与归档。

闭环的入口：把"失败样本"从各层（工具报错、参数非法、本体越界、审批拒绝/超时、
用户负反馈、LLM 降级、限流）统一收敛到一个**可查询、可导出**的集合，
作为下一轮 Prompt / 规则 / 词表迭代的输入。

原实现只有 :class:`observability.models.Feedback` 这一个数据结构，
没有任何生产者——本模块把它变成真正的闭环入口。

存储默认落 :class:`persistence.base.DocumentStore`，也可额外导出 JSONL 供离线分析。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from observability.models import BadCase, BadCaseCategory, Feedback
from persistence.base import DocumentStore

COLLECTION = "badcases"


class BadCaseCollector:
    """BadCase 写入 + 查询 + 分类汇总。"""

    def __init__(self, store: DocumentStore | None = None, export_path: str | None = None) -> None:
        self._store = store
        self._export_path = export_path
        self._memory: list[BadCase] = []
        self._lock = RLock()

    # ------------------------------------------------------------------ 写入

    def capture(
        self,
        category: BadCaseCategory | str,
        source: str,
        message: str,
        *,
        workflow_id: str | None = None,
        trace_id: str | None = None,
        detail: dict | None = None,
    ) -> BadCase:
        """记录一条失败样本。任何层都可以调用，不会向上抛异常。"""
        case = BadCase(
            category=BadCaseCategory(category),
            source=source,
            message=str(message)[:500],
            workflow_id=workflow_id,
            trace_id=trace_id,
            detail=detail or {},
        )
        self._persist(case)
        return case

    def from_feedback(self, feedback: Feedback) -> BadCase | None:
        """把用户反馈升级为 BadCase（rating <= 2 或显式标记）。"""
        if not feedback.is_negative:
            return None
        return self.capture(
            BadCaseCategory.NEGATIVE_FEEDBACK,
            source=feedback.submitted_by,
            message=feedback.comment or f"rating={feedback.rating}",
            workflow_id=feedback.workflow_id,
            trace_id=feedback.trace_id,
            detail={"rating": feedback.rating, "bad_case": feedback.bad_case},
        )

    def resolve(self, case_id: str) -> bool:
        record = self._store.load(COLLECTION, case_id) if self._store is not None else None
        if record is None:
            with self._lock:
                for case in self._memory:
                    if case.id == case_id:
                        case.resolved = True
                        return True
            return False
        record["resolved"] = True
        self._store.save(COLLECTION, case_id, record)
        return True

    # ------------------------------------------------------------------ 读取

    def list(
        self,
        *,
        limit: int | None = None,
        category: BadCaseCategory | str | None = None,
        unresolved_only: bool = False,
    ) -> list[BadCase]:
        cases = self._all()
        if category is not None:
            wanted = BadCaseCategory(category)
            cases = [case for case in cases if case.category is wanted]
        if unresolved_only:
            cases = [case for case in cases if not case.resolved]
        cases.sort(key=lambda case: case.created_at, reverse=True)
        return cases[:limit] if limit is not None else cases

    def count(self) -> int:
        if self._store is not None:
            return self._store.count(COLLECTION)
        with self._lock:
            return len(self._memory)

    def summary(self) -> dict[str, object]:
        """按分类聚合，供 ``/badcases`` 端点与看板消费。"""
        cases = self._all()
        by_category: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for case in cases:
            by_category[case.category.value] = by_category.get(case.category.value, 0) + 1
            by_source[case.source] = by_source.get(case.source, 0) + 1
        return {
            "total": len(cases),
            "unresolved": sum(1 for case in cases if not case.resolved),
            "by_category": dict(sorted(by_category.items())),
            "by_source": dict(sorted(by_source.items(), key=lambda item: -item[1])),
        }

    # ---------------------------------------------------------------- 内部实现

    def _all(self) -> list[BadCase]:
        if self._store is not None:
            return [BadCase.model_validate(record) for record in self._store.query(COLLECTION)]
        with self._lock:
            return list(self._memory)

    def _persist(self, case: BadCase) -> None:
        payload = case.model_dump(mode="json")
        if self._store is not None:
            self._store.save(COLLECTION, case.id, payload)
        else:
            with self._lock:
                self._memory.append(case)
        if self._export_path:
            path = Path(self._export_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


__all__ = ["COLLECTION", "BadCaseCollector"]
