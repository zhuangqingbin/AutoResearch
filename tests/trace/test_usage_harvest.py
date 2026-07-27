"""token 真计量(Wave5 ④A)。

守的第一件事:**按 message.id 去重**。流式更新让同一条 usage 重复落多行,直接求和会翻倍
(实测一个 Explore agent:109 行 usage / 49 唯一 id,cache_read 4.81M 被虚报成 9.83M)。
第二件事:覆盖声明必须在场 —— 产物能证明跑过什么,不能证明没跑过什么。
"""
from __future__ import annotations

import json

from autoresearch.trace import usage_harvest as U


def _row(mid, out, cr, cc, inp=0, agent="l4-card", effort="xhigh", model="claude-opus-5"):
    return {"attributionAgent": agent, "effort": effort,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation_input_tokens": cc}}}


def _write(d, name, rows):
    p = d / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def test_dedups_by_message_id(tmp_path):
    """同一 id 的三行流式 usage 只能算一次(取最后一条累计值)。"""
    p = _write(tmp_path, "agent-a.jsonl", [
        _row("m1", 10, 1000, 100), _row("m1", 50, 5000, 100), _row("m1", 99, 9000, 100),
        _row("m2", 20, 2000, 200),
    ])
    got = U.usage_of(p)
    assert got["messages"] == 2
    assert got["output"] == 99 + 20            # 不是 10+50+99+20
    assert got["cache_read"] == 9000 + 2000    # 不是 1000+5000+9000+2000


def test_naive_sum_would_have_been_wrong(tmp_path):
    """把去重去掉会算出什么 —— 显式钉住这个坑,防后人"顺手简化"。"""
    rows = [_row("m1", 10, 1000, 0), _row("m1", 50, 5000, 0)]
    p = _write(tmp_path, "agent-a.jsonl", rows)
    naive = sum(r["message"]["usage"]["cache_read_input_tokens"] for r in rows)
    assert naive == 6000
    assert U.usage_of(p)["cache_read"] == 5000, "去重腿死了(会虚报 20%~100%)"


def test_carries_agent_identity(tmp_path):
    p = _write(tmp_path, "agent-a.jsonl", [_row("m1", 1, 1, 1, agent="l4-intel", effort="max",
                                                model="claude-sonnet-5")])
    got = U.usage_of(p)
    assert got["agent"] == "l4-intel" and got["effort"] == "max"
    assert got["model"] == "claude-sonnet-5"


def test_billed_input_includes_cache(tmp_path):
    """计费输入 = 生输入 + cache 写 + cache 读(只看 input_tokens 会以为几乎不要钱)。"""
    p = _write(tmp_path, "agent-a.jsonl", [_row("m1", 5, 700, 200, inp=100)])
    assert U.usage_of(p)["billed_in"] == 1000


def test_collect_and_render(tmp_path):
    _write(tmp_path, "agent-a.jsonl", [_row("m1", 100, 900_000, 100_000, agent="l3-rank")])
    _write(tmp_path, "agent-b.jsonl", [_row("m2", 50, 90_000, 10_000, agent="l4-card")])
    rows = U.collect(tmp_path)
    assert [r["agent"] for r in rows] == ["l3-rank", "l4-card"]     # 计费输入降序
    md = U.render(rows, sub_dir=str(tmp_path))
    assert "l3-rank" in md and "l4-card" in md
    assert "cache 命中率" in md
    assert "按 agent 类型汇总" in md
    assert "覆盖声明" in md, "缺覆盖声明 = 读者会把这张表当全量账单"


def test_cache_hit_rate_none_when_no_input():
    assert U.cache_hit_rate([]) is None
    assert U.cache_hit_rate([{"cache_read": 0, "cache_create": 0, "input": 0}]) is None


def test_empty_dir_says_so_not_silently_zero(tmp_path):
    md = U.render(U.collect(tmp_path), sub_dir=str(tmp_path))
    assert "无 transcript" in md


def test_corrupt_line_does_not_kill_file(tmp_path):
    p = tmp_path / "agent-a.jsonl"
    p.write_text(json.dumps(_row("m1", 10, 100, 10)) + "\n{半截行\n"
                 + json.dumps(_row("m2", 20, 200, 20)) + "\n", encoding="utf-8")
    assert U.usage_of(p)["messages"] == 2


def test_weighted_input_applies_price_ratios(tmp_path):
    """加权 = 生输入×1 + 5m写×1.25 + 1h写×2 + cache读×0.1(官方 prompt caching 倍率)。

    不加权就会把「贵」排反:cache_read 常占原始输入 90%+,却只按 0.1 倍计价。
    """
    row = _row("m1", 0, 1000, 200, inp=100)
    row["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 200,
                                                "ephemeral_1h_input_tokens": 0}
    p = _write(tmp_path, "agent-a.jsonl", [row])
    got = U.usage_of(p)
    assert got["billed_in"] == 1300                      # 原始:100+200+1000
    assert got["weighted_in"] == round(100 + 200 * 1.25 + 1000 * 0.1)   # = 450


def test_1h_cache_write_costs_more_than_5m(tmp_path):
    """1h TTL 写是 2×、5m 是 1.25× —— transcript 分开记了就不能混算。"""
    r5 = _row("m1", 0, 0, 1000)
    r5["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 1000,
                                                "ephemeral_1h_input_tokens": 0}
    r1h = _row("m1", 0, 0, 1000)
    r1h["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 0,
                                                 "ephemeral_1h_input_tokens": 1000}
    w5 = U.usage_of(_write(tmp_path, "agent-a.jsonl", [r5]))["weighted_in"]
    w1h = U.usage_of(_write(tmp_path, "agent-b.jsonl", [r1h]))["weighted_in"]
    assert w5 == 1250 and w1h == 2000


def test_ranking_uses_weighted_not_raw(tmp_path):
    """排序必须按加权:一个 cache 读大户的原始量更大,但实际更便宜。"""
    heavy_read = _row("m1", 0, 5_000_000, 0, agent="cache-heavy")
    heavy_write = _row("m2", 0, 0, 1_000_000, agent="write-heavy")
    heavy_write["message"]["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 0}
    _write(tmp_path, "agent-a.jsonl", [heavy_read])
    _write(tmp_path, "agent-b.jsonl", [heavy_write])
    rows = U.collect(tmp_path)
    assert rows[0]["agent"] == "write-heavy", "按原始量排会把 cache 读大户排前面(错)"
    assert rows[0]["weighted_in"] == 1_250_000
    assert rows[1]["weighted_in"] == 500_000


# ── Wave6 T8-b:分模型汇总 + 追溯模式 ────────────────────────────────────────


def test_model_family_normalizes_ids():
    """model 桶取家族名,不是带日期的完整 id —— 否则同族跨版本会分裂成多行,汇总失去意义。"""
    assert U.model_family("claude-haiku-4-5-20251001") == "haiku"
    assert U.model_family("claude-opus-5") == "opus"
    assert U.model_family("claude-sonnet-5") == "sonnet"
    assert U.model_family("—") == "(未标注)"
    assert U.model_family(None) == "(未标注)"


def test_rollup_splits_by_model(tmp_path):
    """必须有按模型汇总:加权口径只含 cache 倍率、**不含模型价差**,所以把壳从 opus 降到
    haiku 后加权 token 数几乎不变而真实成本降一个量级 —— 没有这一维,T1 那类降档改动
    在表上完全看不出来(= 无法验收)。"""
    _write(tmp_path, "agent-a.jsonl", [_row("m1", 500, 60000, 1000,
                                            agent="general-purpose", effort="low",
                                            model="claude-haiku-4-5-20251001")])
    _write(tmp_path, "agent-b.jsonl", [_row("m2", 40000, 400000, 50000)])

    md = U.render(U.collect(tmp_path))

    assert "按模型汇总" in md
    assert "| haiku |" in md and "| opus |" in md


def test_transcripts_glob_mode(tmp_path):
    """--transcripts <glob> 追溯模式:计量代码晚于某次 run 落地时,仍能从存活 transcript 补账
    (Wave6 附录 A 的处境 —— 此前只能手写驱动脚本)。"""
    d = tmp_path / "wf_x"
    d.mkdir()
    _write(d, "agent-1.jsonl", [_row("m1", 20, 30, 0)])

    rows = U.collect_glob(str(tmp_path / "*" / "agent-*.jsonl"))

    assert len(rows) == 1 and rows[0]["agent"] == "l4-card"
