"""intel 超硬顶从"整稿拒"改"按时效裁"(Wave9 W9-B2)。

**沿革**:硬顶 30(W8-13)超顶原本整稿拒(改名 `.rejected.md`),card 侧 presence-gate
找不到 intel 就回退卡内网查。问题是整稿拒把 **T0 增量**(收盘后到跑报这段时间的新
信息 = 明天开盘唯一还没被定价的东西)一并扔了 —— 2026-07-29 实测 002546/603893
两票中招,新闻面因此变薄。

本任务把"整稿拒"改成"按时效窗裁剪":硬顶的牙还在(超顶内容照样丢),只是丢弃顺序
按时效价值排(`T0 → 24h → 催化挂 → 背景 → >1周`,T0/24h 永远最后被砍)。
`REJECTED` 收窄为只留给**事件段一行都解析不出**的稿(结构不可信,裁无可裁);
能解析的超顶稿一律 `TRIMMED`。
"""

from __future__ import annotations

from autoresearch.scan.l4 import intel_guard as ig

DRAFT = """# 活体情报 — 000651 格力 @ 2026-07-29

## 事件段
| 日期 | 时效窗 | 事件 | 源 | 净分 |
|---|---|---|---|---|
| 2026-07-29 | T0 | 盘后推出 R290 | http://a | 0.0 |
| 2026-07-29 | 24h | 铜价新高 | http://b | -0.5 |
| 2026-07-25 | 背景 | 排产走弱 | http://c | -0.5 |
| 2026-06-04 | >1周 | 明骏减持完成 | http://d | 0.0 |
| 2026-08-27 | 催化挂 | 中报披露 | http://e | 0.0 |

## 题材段
归属:白电

## 声明行
网查 36 条 ｜ T0面=有增量
"""


def test_trim_keeps_t0_24h_catalyst_drops_background():
    out, cut = ig.trim_by_recency(DRAFT, keep=3)
    assert "R290" in out and "铜价新高" in out and "中报披露" in out
    assert "排产走弱" not in out and "明骏减持" not in out
    assert cut == 2


def test_trim_preserves_non_event_sections():
    out, _ = ig.trim_by_recency(DRAFT, keep=3)
    assert "## 题材段" in out and "归属:白电" in out and "## 声明行" in out


def test_trim_noop_when_within_keep():
    out, cut = ig.trim_by_recency(DRAFT, keep=10)
    assert cut == 0 and out == DRAFT


def test_guard_trims_instead_of_rejecting(tmp_path):
    (tmp_path / "_l4_intel_000651.md").write_text(DRAFT, encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000651", hard_cap=30)
    assert r["action"] == "TRIMMED"
    assert (tmp_path / "_l4_intel_000651.md").exists()
    assert not (tmp_path / "_l4_intel_000651.rejected.md").exists()
    body = (tmp_path / "_l4_intel_000651.md").read_text(encoding="utf-8")
    assert "已裁剪" in body and "R290" in body


def test_guard_rejects_unparseable_draft(tmp_path):
    (tmp_path / "_l4_intel_000333.md").write_text(
        "# 活体情报\n\n网查 40 条\n(事件段表损坏)\n", encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000333", hard_cap=30)
    assert r["action"] == "REJECTED"
    assert (tmp_path / "_l4_intel_000333.rejected.md").exists()
