"""intel 限频从 advisory 升格为有牙齿(Wave8 W8-13);超顶后果改判(Wave9 W9-B2)。

**沿革**:`pr_20260714_007`「限频形同虚设」挂了 15 天没人裁 —— 因为它一直只是 warn,
而 cap 定在 15 又明显低于稿件实际需要,于是**天天报警、天天无视**,典型的狼来了。

累计三个超限 run 已到(07-14 首跑 / 07-27 两稿 18,16 / 07-28 十一稿全体 16–29,
中位 ~18),Wave7 §4.3 预设的升格触发条件命中。双腿:

- **cap 15 → 20**:对齐实测中位,消掉常态化警报(改配置,不在本文件);
- **硬顶 30 = 有牙齿**:超硬顶必有后果(本文件锁的是这条,不是"必须整稿拒")。

**Wave9 W9-B2 改判**:超硬顶的后果从"整稿拒"改成"按时效窗裁剪"—— 整稿拒会把
T0 增量一并扔掉(2026-07-29 实测 002546/603893 两票中招)。`REJECTED` 收窄为只留给
**事件段一行都解析不出**的稿(结构不可信);能解析的超顶稿一律 `TRIMMED`,原文件名
不变,card 侧 presence-gate 照常读到(裁剪后的)它。本文件里两个原本断言 `REJECTED`
的用例(`test_over_hard_cap_is_rejected` / `test_cli_emits_single_json_line`)其
fixture 用的是无事件表的散文正文,新契约下这类稿仍然落在"不可解析→REJECTED"分支,
断言原样成立,未改动;`TRIMMED` 路径的行为契约见 `tests/scan/test_intel_trim.py`
与本文件新增的 `test_over_hard_cap_with_parseable_draft_is_trimmed_not_rejected`。

红线:**只拒稿不拒票** —— 情报是辅助面,不得反噬决策主链。自报缺失照旧只 warn
(无法对账 ≠ 违规,弱证据不当强证据用)。
"""

from __future__ import annotations

import json

import pytest  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

from autoresearch.scan.l4.intel_guard import guard_intel


def _write(scan_dir, code: str, claimed: int | None, body: str = "事件段…") -> None:
    line = f"\n## 声明行\n网查 {claimed} 条 ｜ as-of ≤ 2026-07-28\n" if claimed is not None else "\n## 声明行\n as-of ≤ 2026-07-28\n"
    (scan_dir / f"_l4_intel_{code}.md").write_text(f"# 活体情报 — {code}\n{body}{line}", encoding="utf-8")


def test_within_hard_cap_is_kept(tmp_path):
    _write(tmp_path, "601288", 18)
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["ok"] is True and out["claimed"] == 18 and out["action"] == "KEPT"
    assert (tmp_path / "_l4_intel_601288.md").exists()


def test_over_hard_cap_is_rejected(tmp_path):
    """超硬顶 + 事件段不可解析 → 稿件改名;原名必须消失(否则 card 仍会读到它)。

    Wave9 W9-B2 后 REJECTED 收窄为"事件段一行都解析不出"才触发 —— 本用例的
    `_write` 默认 body="事件段…" 是无表格散文,天然落在这条分支,断言未改动。
    "超顶但可解析→裁剪保留"的新路径见 `test_over_hard_cap_with_parseable_draft_is_trimmed_not_rejected`。
    """
    _write(tmp_path, "601288", 31)
    out = guard_intel(tmp_path, "601288", hard_cap=30)

    assert out["ok"] is False and out["action"] == "REJECTED" and out["claimed"] == 31
    assert not (tmp_path / "_l4_intel_601288.md").exists(), "原名还在 = card 照样会读到超限稿"
    assert (tmp_path / "_l4_intel_601288.rejected.md").exists(), "证据必须留档,不是删除"


def test_over_hard_cap_with_parseable_draft_is_trimmed_not_rejected(tmp_path):
    """扩(Wave9 W9-B2):超顶但事件段可解析 → TRIMMED,不整稿拒。

    与上一个用例对照:同样超顶(claimed=31 > hard_cap=30),但本用例的稿子有 12 行
    合法事件表(T0/24h/催化挂各 1 行 + 背景 9 行),真的会发生裁剪(cut=2,不是
    test_intel_trim.py 里 cut=0 的"精简稿"场景)—— 验证 `guard_intel` 端到端(读→
    裁→写回原文件名→返回 dropped_rows)而不只是 `trim_by_recency` 本身。
    """
    body = (
        "## 事件段\n"
        "| 日期 | 时效窗 | 事件 | 源 | 净分 |\n"
        "|---|---|---|---|---|\n"
        "| 2026-07-28 | T0 | 盘后重大合同签署 | http://t0 | 1.0 |\n"
        "| 2026-07-28 | 24h | 行业政策利好 | http://h24 | 0.5 |\n"
        "| 2026-09-01 | 催化挂 | 三季报预告 | http://cat | 0.0 |\n"
        "| 2026-07-01 | 背景 | 背景事件一 | http://b1 | 0.0 |\n"
        "| 2026-06-30 | 背景 | 背景事件二 | http://b2 | 0.0 |\n"
        "| 2026-06-29 | 背景 | 背景事件三 | http://b3 | 0.0 |\n"
        "| 2026-06-28 | 背景 | 背景事件四 | http://b4 | 0.0 |\n"
        "| 2026-06-27 | 背景 | 背景事件五 | http://b5 | 0.0 |\n"
        "| 2026-06-26 | 背景 | 背景事件六 | http://b6 | 0.0 |\n"
        "| 2026-06-25 | 背景 | 背景事件七 | http://b7 | 0.0 |\n"
        "| 2026-06-24 | 背景 | 超额背景事件八(应被砍) | http://b8 | 0.0 |\n"
        "| 2026-06-23 | 背景 | 超额背景事件九(应被砍) | http://b9 | 0.0 |\n"
        "\n"
    )
    _write(tmp_path, "601288", 31, body=body)
    out = guard_intel(tmp_path, "601288", hard_cap=30)

    assert out["ok"] is True
    assert out["action"] == "TRIMMED"
    assert out["dropped_rows"] == 2
    assert (tmp_path / "_l4_intel_601288.md").exists(), "TRIMMED 不改名,card 照常读到"
    assert not (tmp_path / "_l4_intel_601288.rejected.md").exists()

    kept = (tmp_path / "_l4_intel_601288.md").read_text(encoding="utf-8")
    assert "已裁剪" in kept and "砍 2 行" in kept
    assert "盘后重大合同签署" in kept   # T0 必须保住
    assert "行业政策利好" in kept       # 24h 必须保住
    assert "三季报预告" in kept         # 催化挂必须保住
    assert "背景事件七" in kept         # 优先级排在被砍的两条之前,应保留
    assert "应被砍" not in kept, "最低优先级的两条背景行应已被砍掉"


def test_exactly_hard_cap_is_kept(tmp_path):
    """边界:等于硬顶不拒(硬顶是"超过才拒")。"""
    _write(tmp_path, "601288", 30)
    assert guard_intel(tmp_path, "601288", hard_cap=30)["action"] == "KEPT"


def test_unreported_count_only_warns(tmp_path):
    """自报缺失 → warn,不拒稿(无法对账 ≠ 违规)。"""
    _write(tmp_path, "601288", None)
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["ok"] is True and out["action"] == "KEPT"
    assert out["claimed"] is None and out["warn"] == "unreported"


def test_missing_file_is_not_an_error(tmp_path):
    """intel 关闭或本票 intel 失败 → 无文件,guard 必须安静通过(presence-gated)。"""
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["ok"] is True and out["action"] == "ABSENT"


def test_cli_emits_single_json_line(tmp_path, capsys):
    """workflow 的 gp 壳只把最后一行 JSON 带回 —— 输出必须是单行可解析 JSON。"""
    from autoresearch.scan.l4.intel_guard import main
    _write(tmp_path, "601288", 31)

    rc = main(["2026-07-28", "601288", "--scan-dir", str(tmp_path), "--hard-cap", "30"])

    assert rc == 0, "拒稿不是进程失败 —— 只拒稿不拒票,退出码必须 0"
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["action"] == "REJECTED" and payload["code"] == "601288"


# ───────────── B4 影子(2026-09-07 Q-B ③):本票事件抽取侧车 ─────────────

def _event_doc(rows):
    head = "## 事件段\n| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
    return head + "".join(rows)


def test_self_stock_event_claims_land_in_a_sidecar_without_touching_the_verdict(tmp_path):
    body = _event_doc([
        "| 2026-07-28 | T0 | 公司公告已完成回购 10 亿元 | http://a | 1.0 |\n",
        "| 2026-07-27 | 24h | 600001 涨停 | http://b | 0.5 |\n",          # 他票且无谓语词
        "| 2026-07-27 | 24h | 600001 完成回购 5 亿元 | http://b2 | 0.5 |\n", # 他票 + 谓语词:必须被他票那一腿挡掉
        "| 2026-07-26 | 背景 | 行业政策利好 | http://c | 0.0 |\n",         # 无谓语词
    ])
    _write(tmp_path, "601288", 18, body)
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["action"] == "KEPT" and out["ok"] is True
    assert out["claim_events"]["n"] == 1
    sidecar = tmp_path / out["claim_events"]["sidecar"]
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    (event,) = payload["events"]
    assert event["bundle"]["event"]["predicate"] == "回购"
    assert event["verdict"] == "UNKNOWN" and event["reason"] == "SOURCE_NOT_BOUND"
    assert payload["binding"] == "none"


def test_no_event_claims_means_no_sidecar(tmp_path):
    _write(tmp_path, "601288", 18, _event_doc(["| 2026-07-28 | T0 | 行业政策利好 | http://c | 0.0 |\n"]))
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["claim_events"] == {"n": 0, "sidecar": None}
    assert not list(tmp_path.glob("_l4_claims_*.json"))


def test_invalid_shadow_date_cannot_fail_intel_guard(tmp_path):
    body = _event_doc([
        "| 2026-09-31 | T0 | 公司于 2026-09-31 完成回购 1 亿元 | http://a | 1.0 |\n",
    ])
    _write(tmp_path, "601288", 18, body)
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["ok"] is True and out["action"] == "KEPT"
    payload = json.loads((tmp_path / out["claim_events"]["sidecar"]).read_text(encoding="utf-8"))
    assert payload["events"][0]["extraction_notes"] == ["invalid_date:2026-09-31"]


def test_sidecar_write_failure_is_a_shadow_diagnostic(tmp_path, monkeypatch):
    from pathlib import Path
    from autoresearch.scan.l4 import intel_guard as ig

    body = _event_doc([
        "| 2026-09-01 | T0 | 公司已完成回购 1 亿元 | http://a | 1.0 |\n",
    ])
    _write(tmp_path, "601288", 18, body)
    original = Path.write_text

    def fail_sidecar(path, *args, **kwargs):
        if path.name.startswith("_l4_claims_"):
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_sidecar)
    out = ig.guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["ok"] is True and out["action"] == "KEPT"
    assert out["claim_events"]["errors"] == [{"reason": "SIDECAR_WRITE_FAILED",
                                                "detail": "disk full"}]


def test_sidecar_does_not_alter_the_draft_or_claims_lint(tmp_path):
    body = _event_doc(["| 2026-07-28 | T0 | 控股股东拟增持不超过 2 亿元 | http://a | 1.0 |\n"])
    _write(tmp_path, "601288", 18, body)
    before = (tmp_path / "_l4_intel_601288.md").read_text(encoding="utf-8")
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert (tmp_path / "_l4_intel_601288.md").read_text(encoding="utf-8") == before
    assert out["claims_lint"] == {"no_url": 0, "mismatch": 0, "orig_as": None}


def test_sidecar_is_written_from_the_trimmed_text_not_the_pretrim(tmp_path):
    rows = ["| 2026-07-28 | T0 | 公司公告已完成回购 10 亿元 | http://t0 | 1.0 |\n"]
    rows += [f"| 2026-06-{30 - i:02d} | 背景 | 拟增持背景事件{i} | http://b{i} | 0.0 |\n" for i in range(11)]
    _write(tmp_path, "601288", 40, _event_doc(rows))
    out = guard_intel(tmp_path, "601288", hard_cap=30)
    assert out["action"] == "TRIMMED" and out["dropped_rows"] > 0
    payload = json.loads((tmp_path / out["claim_events"]["sidecar"]).read_text(encoding="utf-8"))
    assert out["claim_events"]["n"] == len(payload["events"]) < 12
