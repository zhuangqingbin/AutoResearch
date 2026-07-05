"""L4 派发包确定性落稿(write_dispatch_pack):_harvest_list(yfinance 后缀,.SH 绝迹)
+ 每卡 _l4_prompt_<code>.md(共享块+简报+slim 指针)。落稿契约从人肉变确定性:
token 表输入侧从此可计,.SH 空 slim 双跑(07-03 中招 10/30)从源头消灭。合成,无网络。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l4_card import write_dispatch_pack

_DATE = "2026-07-03"


def _mk(root):
    d = root / "context" / "scan" / _DATE
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600584", "name": "长电科技", "sector": "半导体", "conviction": 50},
        {"code": "000001", "name": "平安银行", "sector": "银行", "conviction": 40},
        {"code": "300001", "name": "特锐德", "sector": "电力设备", "conviction": 30},
    ]).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([
        {"code": "600584", "name": "长电科技", "composite": 80, "main_net_ratio": 0.01,
         "main_inflow_yi": 3.6, "pe": 98, "pct_60d": 70.0},
        {"code": "000001", "name": "平安银行", "composite": 60, "main_net_ratio": 0.01,
         "main_inflow_yi": 1.0, "pe": 5, "pct_60d": 2.0},
    ]).to_csv(d / "L1_recall_top1000.csv", index=False)
    (d / "_l4_shared_instructions.md").write_text("共享指令块:渐进深度+早停", encoding="utf-8")
    (d / "details" / "300001.md").write_text("♻️ 复用卡\n**Rating**: Hold\n", encoding="utf-8")
    return d


def test_write_dispatch_pack(tmp_path):
    d = _mk(tmp_path)
    res = write_dispatch_pack(d)
    assert res["n_prompts"] == 2 and res["n_skipped"] == 1

    tickers = (d / "_harvest_list.txt").read_text(encoding="utf-8").split()
    assert "600584.SS" in tickers and "000001.SZ" in tickers     # 上交所必须 .SS(yfinance)
    assert not any(t.endswith(".SH") for t in tickers)
    assert not any(t.startswith("300001") for t in tickers)      # 已有卡(复用)不重拉不派发

    p = (d / "_l4_prompt_600584.md").read_text(encoding="utf-8")
    assert "共享指令块" in p and "漏斗简报" in p
    assert f"600584.SS_{_DATE}_slim.md" in p                     # slim 指针带 .SS
    assert not (d / "_l4_prompt_300001.md").exists()


def test_dispatch_pack_cli(tmp_path, monkeypatch, capsys):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.agents.l4_card import main
    assert main(["prompts", _DATE]) == 0
    assert (tmp_path / "context" / "scan" / _DATE / "_l4_prompt_600584.md").exists()
    assert "2" in capsys.readouterr().out
