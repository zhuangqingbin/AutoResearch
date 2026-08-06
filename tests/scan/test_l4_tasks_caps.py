"""Wave11 T1:派发帽与资源帽解耦 —— effective_cap 只由 l4_stock 决定,批次一次全派。"""
from autoresearch.scan import l4_tasks


def _init(tmp_path, n=10, caps=None):
    codes = [f"{600000+i}" for i in range(n)]
    return l4_tasks.initialize("2026-08-06", codes, root=tmp_path,
                               context_root=tmp_path / "ctx", caps=caps)


def test_dispatch_cap_is_l4_stock_only(tmp_path):
    r = _init(tmp_path, n=10)          # DEFAULT_CAPS: l4_stock=64
    assert r["effective_cap"] == 64
    b = l4_tasks.dispatch_batches(tmp_path / "2026-08-06" / "_l4_tasks.json")
    assert len(b["batches"]) == 1 and len(b["batches"][0]) == 10   # 一次全派


def test_rate_limit_failures_no_longer_shrinks_dispatch(tmp_path):
    _init(tmp_path, n=6)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    payload = json.loads(book.read_text())
    payload["rate_limit_failures"] = 3
    book.write_text(json.dumps(payload))
    b = l4_tasks.dispatch_batches(book)
    assert b["effective_cap"] == 64            # 派发帽不缩;限频收窄的是 T2 的 tushare 槽
    assert len(b["batches"][0]) == 6
