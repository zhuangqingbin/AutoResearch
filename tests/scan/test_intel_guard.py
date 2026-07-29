"""intel 限频从 advisory 升格为有牙齿(Wave8 W8-13)。

**沿革**:`pr_20260714_007`「限频形同虚设」挂了 15 天没人裁 —— 因为它一直只是 warn,
而 cap 定在 15 又明显低于稿件实际需要,于是**天天报警、天天无视**,典型的狼来了。

累计三个超限 run 已到(07-14 首跑 / 07-27 两稿 18,16 / 07-28 十一稿全体 16–29,
中位 ~18),Wave7 §4.3 预设的升格触发条件命中。双腿:

- **cap 15 → 20**:对齐实测中位,消掉常态化警报(改配置,不在本文件);
- **硬顶 30 = 拒稿**:超硬顶把稿件改名 `.rejected.md`,card 侧 presence-gate 找不到
  intel 就自动回退卡内网查(现有机制零改动)。

红线:**只拒稿不拒票** —— 情报是辅助面,不得反噬决策主链。自报缺失照旧只 warn
(无法对账 ≠ 违规,弱证据不当强证据用)。
"""

from __future__ import annotations

import json

import pytest

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
    """超硬顶 → 稿件改名;原名必须消失(否则 card 仍会读到它)。"""
    _write(tmp_path, "601288", 31)
    out = guard_intel(tmp_path, "601288", hard_cap=30)

    assert out["ok"] is False and out["action"] == "REJECTED" and out["claimed"] == 31
    assert not (tmp_path / "_l4_intel_601288.md").exists(), "原名还在 = card 照样会读到超限稿"
    assert (tmp_path / "_l4_intel_601288.rejected.md").exists(), "证据必须留档,不是删除"


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
