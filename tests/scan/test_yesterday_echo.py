"""昨卡回声(yesterday_echo,W9-B1b):TTL 复用退役后,评级稳定性靠记忆而非跳过研究——
最近一次已发布卡的 3 行摘要注入当日任务包。防锚定:回声是历史判断,翻覆须给增量证据。
"""
import json

from autoresearch.scan.l4 import prompts


def _mk_run(root, run_id, data_date, name, body):
    d = root / run_id
    (d / "details").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"analysis_date": data_date}),
                                     encoding="utf-8")
    (d / "details" / f"{name}.md").write_text(body, encoding="utf-8")


CARD = """# 决策卡 — 601211 国泰海通 @ 2026-07-27
**Rating**: Hold
**一行多空**: 多 fwd-PE 11.2x ｜ 空 利好已见光死
- [价格线] close < 18.72 → 隔日减仓
"""


def test_echo_extracts_three_lines(tmp_path):
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通", CARD)
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path)
    assert "2026-07-27" in echo
    assert "Hold" in echo
    assert "见光死" in echo
    assert "18.72" in echo
    assert "增量证据" in echo          # 防锚定文案必须在


def test_echo_empty_when_no_history(tmp_path):
    assert prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path) == ""


def test_echo_respects_lookback_window(tmp_path):
    _mk_run(tmp_path, "20260701_2100", "2026-07-01", "国泰海通", CARD)
    assert prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  lookback_days=5, reports_root=tmp_path) == ""


def test_echo_picks_most_recent_run(tmp_path):
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通", CARD)
    _mk_run(tmp_path, "20260728_2100", "2026-07-28", "国泰海通",
            CARD.replace("Hold", "Underweight").replace("07-27", "07-28"))
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path)
    assert "Underweight" in echo and "Hold" not in echo


def _rating_line(echo: str) -> str:
    return next(line for line in echo.splitlines() if line.startswith("- 评级:"))


def test_echo_rating_is_the_tier_word_only(tmp_path):
    """历史卡把模板提示抄进 Rating 行时,回声只取五档词,模板残留不进次日任务包。"""
    body = CARD.replace("**Rating**: Hold",
                        "**Rating**: Underweight ← 必须 = Rubric建议(一致,无偏离)")
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通", body)
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29", reports_root=tmp_path)
    assert _rating_line(echo) == "- 评级:**Underweight**"


def test_echo_rating_strips_bold_and_reports_dash_when_unreadable(tmp_path):
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通",
            CARD.replace("**Rating**: Hold", "**Rating**: **Hold**"))
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29", reports_root=tmp_path)
    assert _rating_line(echo) == "- 评级:**Hold**"
    _mk_run(tmp_path, "20260728_2100", "2026-07-28", "国泰海通",
            CARD.replace("**Rating**: Hold", "**Rating**: 待定"))
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29", reports_root=tmp_path)
    assert _rating_line(echo) == "- 评级:**—**"
