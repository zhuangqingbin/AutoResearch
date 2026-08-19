"""卡头 📰 新闻导航行(Wave9 B-4)—— T0/24h 增量条数上浮到卡头,指向文末情报附录。

背景:情报稿全文只附在 detail 卡**尾部**,用户投诉"detail 为什么没看到新闻" ——
其实是没翻到那节。本行在卡**头部**注入一句摘要,先让人知道"今天有没有新料"。
"""

from autoresearch.scan import publisher
from pathlib import Path  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

DRAFT = """# 活体情报 — 000651 @ 2026-07-29
| 日期 | 时效窗 | 事件 | 源 | 净分 |
|---|---|---|---|---|
| 2026-07-29 | T0 | 盘后 R290 | http://a | 0.0 |
| 2026-07-29 | 24h | 铜价 | http://b | -0.5 |
| 2026-07-25 | 背景 | 排产 | http://c | -0.5 |
"""


def test_counts_t0_and_24h(tmp_path):
    p = tmp_path / "_l4_intel_000651.md"
    p.write_text(DRAFT, encoding="utf-8")
    line = publisher._news_headline(p)
    assert "T0 增量:1 条" in line
    # 口径:`24h N 条` 只数 时效窗==24h 的行(不含 T0)。DRAFT 里 T0 一行、24h 一行、
    # 背景一行 —— 正确值是 1,不是 T0+24h 的 2(brief 原 `or` 弱断言在此收紧为单一值)。
    assert "24h 1 条" in line


def test_zero_t0_says_no_increment(tmp_path):
    p = tmp_path / "_l4_intel_x.md"
    p.write_text(DRAFT.replace("| T0 |", "| 背景 |"), encoding="utf-8")
    assert "盘后无增量" in publisher._news_headline(p)


def test_absent_intel(tmp_path):
    assert "情报缺席" in publisher._news_headline(tmp_path / "nope.md")


# ── 头行插入点(现场核验后新增,非 brief 原始步骤)──────────────────────────────
#
# 真实已发布卡(`reports/scan/20260729_2105/details/*.md`)现场核验发现:标题行
# (`# 决策卡 — ...`)后紧跟一行契约版本戳 `〔卡契约 v3·超短 1~2 日〕` —— 若像 brief
# 原始 Step 3 那样只在"第一行"后插入,会把头行硬生生插进"标题+契约戳"这一对中间。
# 另外 ♻️ 复用卡(TTL 复用已于 W9-B1a 退役,但历史 staging 卡如 601211 仍带这层壳)
# 标题根本不在 line 0,前面还有复用横幅 + 分隔线。两种现场形状都必须正确处理。

def test_inject_after_title_and_contract_tag_not_between():
    body = (
        "# 决策卡 — 000651 格力电器 @ 2026-07-29\n"
        "〔卡契约 v3·超短 1~2 日〕\n"
        "\n"
        "## 决策仪表盘\n"
    )
    out = publisher._inject_news_headline(body, "📰 TEST")
    lines = out.split("\n")
    assert lines[0].startswith("# 决策卡")
    assert lines[1] == "〔卡契约 v3·超短 1~2 日〕"          # 标题+契约戳仍相邻,未被拆散
    assert lines.index("📰 TEST") > lines.index("〔卡契约 v3·超短 1~2 日〕")


def test_inject_finds_title_when_not_first_line():
    """♻️ 复用卡壳:标题行不在 line 0,头行仍要落在真正的标题+契约戳之后。"""
    body = (
        "♻️ **复用卡**(源 2026-07-27,TTL 2d)\n"
        "> 当日未重研\n"
        "\n"
        "---\n"
        "\n"
        "# 决策卡 — 601211 国泰海通 @ 2026-07-27\n"
        "〔卡契约 v3·超短 1~2 日〕\n"
        "\n"
        "## 决策仪表盘\n"
    )
    out = publisher._inject_news_headline(body, "📰 TEST")
    lines = out.split("\n")
    title_idx = next(i for i, ln in enumerate(lines) if ln.startswith("# 决策卡"))
    assert lines[title_idx + 1] == "〔卡契约 v3·超短 1~2 日〕"
    assert lines[title_idx + 2] == ""
    assert lines[title_idx + 3] == "📰 TEST"


def test_inject_noop_when_no_title_line():
    """异常卡形(找不到标题行):原样返回,不猜测插入点,不破坏卡片。"""
    body = "no title here\njust text\n"
    assert publisher._inject_news_headline(body, "📰 TEST") == body
