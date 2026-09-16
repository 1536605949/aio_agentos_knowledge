"""持久化测试：内存与 SQLite 两种后端的一致性，以及跨"进程重启"可读性。"""

from __future__ import annotations

from persistence import InMemoryDocumentStore, SqliteDocumentStore, build_store, stamp_new
from persistence.base import META_CREATED, META_KEY, META_UPDATED


def test_stamp_new_sets_metadata():
    record = stamp_new({"a": 1}, "k")
    assert record[META_KEY] == "k"
    assert META_CREATED in record
    assert META_UPDATED in record


def test_in_memory_store_roundtrip():
    store = InMemoryDocumentStore()
    store.save("workflows", "w1", {"status": "running"})
    store.save("workflows", "w2", {"status": "completed"})

    loaded = store.load("workflows", "w1")
    assert loaded is not None
    assert loaded["status"] == "running"
    assert loaded[META_KEY] == "w1"

    assert store.count("workflows") == 2
    assert store.collections() == ["workflows"]

    newest = store.query("workflows", limit=1)
    assert len(newest) == 1
    assert newest[0][META_KEY] == "w2"

    assert store.delete("workflows", "w1") is True
    assert store.delete("workflows", "w1") is False
    assert store.count("workflows") == 1


def test_in_memory_store_preserves_created_at_on_update():
    store = InMemoryDocumentStore()
    first = store.save("c", "k", {"v": 1})
    second = store.save("c", "k", {"v": 2})
    assert second[META_CREATED] == first[META_CREATED]
    assert second["v"] == 2


def test_sqlite_store_survives_reopen(tmp_path):
    path = str(tmp_path / "state.db")

    store = SqliteDocumentStore(path)
    store.save("workflows", "w1", {"status": "waiting_approval", "nested": {"a": [1, 2]}})
    store.close()

    # 重新打开 = 模拟进程重启
    reopened = SqliteDocumentStore(path)
    loaded = reopened.load("workflows", "w1")
    assert loaded is not None
    assert loaded["status"] == "waiting_approval"
    assert loaded["nested"] == {"a": [1, 2]}
    assert reopened.count("workflows") == 1

    # UPSERT 保留 created_at
    created_before = loaded[META_CREATED]
    reopened.save("workflows", "w1", {"status": "completed"})
    assert reopened.load("workflows", "w1")[META_CREATED] == created_before
    reopened.close()


def test_sqlite_store_query_ordering_and_delete(tmp_path):
    store = SqliteDocumentStore(str(tmp_path / "q.db"))
    store.save("badcases", "a", {"n": 1})
    store.save("badcases", "b", {"n": 2})
    store.save("badcases", "c", {"n": 3})

    newest = store.query("badcases", limit=2)
    assert len(newest) == 2
    oldest_first = store.query("badcases", newest_first=False)
    assert [record["n"] for record in oldest_first] == [1, 2, 3]

    assert store.delete("badcases", "b") is True
    assert store.count("badcases") == 2
    assert store.collections() == ["badcases"]
    store.close()


def test_build_store_selects_backend(monkeypatch, tmp_path):
    from config import Settings, reset_settings_cache

    memory_store = build_store(Settings(store_backend="memory"))
    assert memory_store.backend == "memory"

    sqlite_store = build_store(Settings(store_backend="sqlite", sqlite_path=str(tmp_path / "x.db")))
    assert sqlite_store.backend == "sqlite"
    sqlite_store.close()

    try:
        build_store(Settings(store_backend="nope"))
    except ValueError as exc:
        assert "unsupported store backend" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")

    reset_settings_cache()
