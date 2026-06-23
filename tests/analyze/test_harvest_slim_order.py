"""slim 渐进读盘:表面块在前、深核块(P4)在后 + 分界标记。NO network(合成 parts)。"""
from __future__ import annotations

from autoresearch.analyze.harvest import _P4_MARKER, _reorder_slim_for_progressive


def _parts():
    return [
        "# Data context — 000933\n",                                # 头(表面)
        "\n## Verified market snapshot (source of truth)\n\n…\n",    # 表面
        "\n## Income statement (quarterly)\n\n…长表…\n",             # 深核
        "\n## 量价形态/吸筹·多日资金流 (UZI·复用L1)\n\n…\n",         # 表面
        "\n## Earnings quality / forensics (v3)\n\n…\n",            # 深核
        "\n## Solvency & refinancing (v4)\n\n…\n",                  # 深核
        "\n## A股卖方一致预期 EPS / fwd-PE (同花顺·keyless)\n\n…\n",  # 表面(fwd PE)
    ]


def test_reorder_puts_deep_after_marker():
    out = _reorder_slim_for_progressive(_parts())
    joined = "".join(out)
    assert _P4_MARKER in joined
    mi = joined.index(_P4_MARKER)
    # 表面块在标记前
    assert joined.index("Verified market snapshot") < mi
    assert joined.index("量价形态") < mi
    assert joined.index("fwd-PE") < mi
    # 深核块在标记后
    assert joined.index("Income statement") > mi
    assert joined.index("Earnings quality") > mi
    assert joined.index("Solvency") > mi


def test_reorder_preserves_surface_order():
    out = _reorder_slim_for_progressive(_parts())
    joined = "".join(out)
    assert joined.index("market snapshot") < joined.index("量价形态") < joined.index("fwd-PE")


def test_reorder_noop_when_no_deep_blocks():
    surface = ["# head\n", "\n## Verified market snapshot\n\n…\n", "\n## Tradeability\n\n…\n"]
    assert _reorder_slim_for_progressive(surface) == surface       # 无深核 → 原样,不插标记
