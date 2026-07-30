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


# ── 复核回归修复(W9-B2-fix):原地覆写真删行,lint 会永久失明 → 裁前留档 ──────────
#
# `intel_future_dates_lint`/`intel_recency_lint`(autoresearch/learning/self_review.py)
# 靠裸读 `_l4_intel_*.md` 审计事件段;旧 REJECTED 靠改名保留整稿全文,这两条 lint
# 一直能看到完整原稿。TRIMMED 原地覆写会真删掉被砍行,且被砍的(背景/>1周)恰是这
# 两条 lint 最想抓的对象——审计覆盖率下降方向与探针敏感度负相关。

_OVER_KEEP_BODY = (
    "# 活体情报 — 000777 @ 2026-07-29\n\n## 事件段\n"
    "| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
    "| 2026-07-29 | T0 | 盘后重大合同签署 | http://t0 | 1.0 |\n"
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
    "\n## 声明行\n网查 33 条 ｜ T0面=有增量\n"
)


def test_guard_trims_archives_pretrim_when_rows_dropped(tmp_path):
    """真的丢行时(cut>0)——裁前原文必须留档到 `<code>.pretrim`,内容含被砍的行。"""
    (tmp_path / "_l4_intel_000777.md").write_text(_OVER_KEEP_BODY, encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000777", hard_cap=30)

    assert r["action"] == "TRIMMED" and r["dropped_rows"] == 2
    archive = tmp_path / "_l4_intel_000777.pretrim"
    assert r["pretrim_as"] == archive.name == "_l4_intel_000777.pretrim"
    assert archive.exists()
    assert archive.read_text(encoding="utf-8") == _OVER_KEEP_BODY   # 逐字节保留裁前原文

    trimmed_body = (tmp_path / "_l4_intel_000777.md").read_text(encoding="utf-8")
    assert "应被砍" not in trimmed_body        # canonical 里两条最低优先级背景行已消失
    assert "应被砍" in archive.read_text(encoding="utf-8")  # 但留档里还在


def test_guard_trims_no_archive_when_nothing_dropped(tmp_path):
    """cut==0(没什么可裁)不留档 —— 裁前裁后内容逐字节相同,留档没有审计价值。"""
    (tmp_path / "_l4_intel_000651.md").write_text(DRAFT, encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000651", hard_cap=30)

    assert r["action"] == "TRIMMED" and r["dropped_rows"] == 0
    assert r["pretrim_as"] is None
    assert not (tmp_path / "_l4_intel_000651.pretrim").exists()


def test_pretrim_archive_name_invisible_to_intel_md_glob(tmp_path):
    """留档命名故意不带 `.md`——不会被任何 `glob('_l4_intel_*.md')` 消费方顺带命中。"""
    (tmp_path / "_l4_intel_000777.md").write_text(_OVER_KEEP_BODY, encoding="utf-8")
    ig.guard_intel(tmp_path, "000777", hard_cap=30)
    matches = sorted(p.name for p in tmp_path.glob("_l4_intel_*.md"))
    assert matches == ["_l4_intel_000777.md"], (
        "留档 _l4_intel_000777.pretrim 不该出现在 _l4_intel_*.md 的匹配结果里")
