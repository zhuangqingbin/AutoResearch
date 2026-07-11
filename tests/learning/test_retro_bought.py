# tests/learning/test_retro_bought.py
from pathlib import Path
import pandas as pd
from autoresearch.learning import retro


def _fake_attr_csv(tmp_path: Path, date: str, rating: str) -> Path:
    d = tmp_path / date / "retro"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "rating": [rating],
                  "fwd_2_oc": [0.01]}).to_csv(d / "attribution.csv", index=False)
    return d / "attribution.csv"


def test_keep_whitelist_contains_bought():
    assert "bought" in retro._KEEP


def test_backfill_bought_idempotent(tmp_path):
    p = _fake_attr_csv(tmp_path, "2026-07-08", "Overweight")
    n1 = retro.backfill_bought(scan_root=tmp_path)
    assert n1 == 1
    df = pd.read_csv(p)
    assert bool(df.loc[0, "bought"]) is True
    n2 = retro.backfill_bought(scan_root=tmp_path)   # 已有列 → 跳过
    assert n2 == 0


def test_backfill_bought_hold_is_false(tmp_path):
    p = _fake_attr_csv(tmp_path, "2026-07-09", "Hold")
    retro.backfill_bought(scan_root=tmp_path)
    assert bool(pd.read_csv(p).loc[0, "bought"]) is False
