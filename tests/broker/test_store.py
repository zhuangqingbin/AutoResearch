"""store:raw/<src>.csv 按 row_hash 幂等、日志只认 ok 的哈希、根路径走 workspace。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.broker import schema, store
from autoresearch.common import workspace as ws

AT = "2026-08-27T10:00:00"


def _norm(df, kind="gtht"):
    return schema.normalize(df, source_kind=kind, source_file="a.xlsx", ingested_at=AT)


def test_upsert_raw_is_idempotent_by_row_hash(tmp_path, raw):
    df = _norm(raw.rows({}, {"trade_time": "09:32:00"}))
    assert store.upsert_raw(df, tmp_path) == (2, 0)
    assert store.upsert_raw(df, tmp_path) == (0, 2)
    back = store.read_raw(store.raw_path(tmp_path, "gtht"))
    assert len(back) == 2 and list(back.columns) == list(schema.RAW_STORE_COLUMNS)
    assert back.price.dtype == float and back.seq.tolist() == [0, 1]   # seq 组键不含 trade_time
    assert pd.isna(back.other_fee).all()


def test_upsert_appends_only_new_rows(tmp_path, raw):
    store.upsert_raw(_norm(raw()), tmp_path)
    assert store.upsert_raw(_norm(raw.rows({}, {"price": "12.35", "amount": "1235"})), tmp_path) == (1, 1)
    assert len(store.read_raw(store.raw_path(tmp_path, "gtht"))) == 2


def test_raw_file_is_stable_when_nothing_new(tmp_path, raw):
    df = _norm(raw())
    store.upsert_raw(df, tmp_path)
    p = store.raw_path(tmp_path, "gtht")
    before = p.read_bytes()
    store.upsert_raw(df, tmp_path)
    assert p.read_bytes() == before


def test_log_roundtrip_only_ok_counts_as_ingested(tmp_path):
    store.append_log({"sha256": "abc", "status": "ok"}, tmp_path)
    store.append_log({"sha256": "def", "status": "rejected"}, tmp_path)
    assert store.ingested_shas(tmp_path) == {"abc"}
    assert store.ingested_shas(tmp_path / "nowhere") == set()


def test_sha256_of(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"abc")
    assert store.sha256_of(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_roots_follow_workspace_engine(monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    assert store.raw_path(None, "gtht") == Path("context_codex/broker/raw/gtht.csv")
    assert store.trades_path(None) == Path("context_codex/broker/trades.csv")
    assert store.log_path(None) == Path("context_codex/broker/ingest_log.jsonl")
