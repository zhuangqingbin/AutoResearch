"""backfill 契约:skip-existing 幂等 / max_calls 分片 / 异常停不丢缓存。全注入,零网络。"""
import pandas as pd

from autoresearch.research import consensus


def _days(start, end):
    return ["20260701", "20260702", "20260703"]


def test_skip_existing_and_cap(tmp_path):
    root = tmp_path / "report_rc"
    root.mkdir(parents=True)
    pd.to_pickle(pd.DataFrame({"ts_code": ["000001.SZ"]}), root / "20260701.pkl")
    calls = []
    res = consensus.backfill("2026-07-01", "2026-07-03", cache_root=tmp_path,
                             max_calls=1, pull_fn=lambda d, c=None: calls.append(d),
                             days_fn=_days)
    assert res == {"pulled": 1, "skipped": 1, "stopped_by": "max_calls"}
    assert calls == ["2026-07-02"]                 # 已缓存跳过,cap 停在第三天前


def test_error_stops_resumable(tmp_path):
    def boom(d, c=None):
        raise RuntimeError("每小时最多访问该接口1次")
    res = consensus.backfill("2026-07-01", "2026-07-03", cache_root=tmp_path,
                             pull_fn=boom, days_fn=_days)
    assert res["pulled"] == 0
    assert res["stopped_by"].startswith("error")   # 停下可续跑,不抛穿
