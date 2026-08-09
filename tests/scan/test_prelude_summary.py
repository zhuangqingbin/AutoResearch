"""prelude 汇总屏双写:12 步 ✓/✗ 屏必须完整落盘(scan-market.js 末15行截断的解药)。"""
from __future__ import annotations

from autoresearch.scan import prelude


def _results():
    return [{"step": "universe", "ok": True, "note": "L0 4100 → L1 1000 → L2 200"},
            {"step": "menu", "ok": False, "note": "RuntimeError: staging 缺"}]


def test_render_summary_has_every_step_with_mark():
    out = prelude.render_summary("2026-07-25", _results())
    assert "✓ universe" in out
    assert "✗ menu" in out
    assert "L0 4100 → L1 1000 → L2 200" in out
    assert "prelude 汇总" in out


def test_render_summary_includes_prewarm_state(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "预热" in out
    assert "✗" in out                      # 无 _prewarm.json → 明说没跑


def test_prewarm_line_detects_artifact(tmp_path):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    assert "✗" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)
    (d / "_prewarm.json").write_text('{"started_at": 1, "ended_at": 2}', encoding="utf-8")
    assert "✓" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)


# ══════════ Wave12 T3:热度快照断采可见(prelude 汇总屏)══════════
#
# `prewarm.py`(夜间 19:30)与 `prelude.py`(次日开扫)是**两个独立进程**——
# `contracts._DEGRADED` 是进程内模块级列表,穿不透进程边界。唯一穿透介质是落盘的
# `_prewarm.json`;`prewarm_line()` 读它的 `steps[]`,把 hot_rank_snapshot 步骤的
# 断采 note 追加成告警片段(T1 报告"移交决策"⑦已把这条精确化写清)。


def _prewarm_json(tmp_path, note, ok=True):
    import json

    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True, exist_ok=True)
    (d / "_prewarm.json").write_text(json.dumps({
        "started_at": 1, "ended_at": 2,
        "steps": [{"step": "hot_rank_snapshot", "ok": ok, "note": note}],
    }), encoding="utf-8")
    return prelude.prewarm_line("2026-07-25", scan_root=tmp_path)


def test_prewarm_line_surfaces_hot_rank_failure(tmp_path):
    line = _prewarm_json(
        tmp_path, "eastmoney_hot_rank✓(100行) · stock_hot_follow_xq✗(RuntimeError)")
    assert "✓" in line                          # 主行仍是"已跑"(prewarm 整体没失败)
    assert "热度快照" in line and "✗" in line     # 但追加了断采告警片段


def test_prewarm_line_surfaces_whole_step_exception(tmp_path):
    """I4:整步抛异常时 `_step()` 写的 note 是 `f"{type(e).__name__}: {e}"` —— **不含 ✗**。
    只认装饰字符的旧判法会让"预热 ✓ 已跑"照常显示、热度告警片段完全不出现。"""
    line = _prewarm_json(tmp_path, "SnapshotDateError: as-of 键 20260807 ≠ 今天", ok=False)
    assert "热度快照" in line and "整步异常" in line


def test_prewarm_line_surfaces_half_return_that_looks_successful(tmp_path):
    """I4/C2:半截返回在旧实现里写成 `✓(3000行)`,ok=True、note 无 ✗ —— 两条旧判据全瞎。
    行数是结构化事实,拿契约下限一比就现原形(prelude 独立于 prewarm 的第二道判据)。"""
    line = _prewarm_json(
        tmp_path, "eastmoney_hot_rank✓(100行) · stock_hot_follow_xq✓(3000行)")
    assert "热度快照" in line and "半截/空" in line and "3000" in line


def test_prewarm_line_surfaces_zero_row_snapshot(tmp_path):
    line = _prewarm_json(tmp_path, "eastmoney_hot_rank✓(0行) · stock_hot_follow_xq✓(5619行)")
    assert "热度快照" in line and "半截/空" in line


def test_prewarm_line_silent_when_hot_rank_ok(tmp_path):
    line = _prewarm_json(
        tmp_path,
        "eastmoney_hot_rank✓(100行) · stock_hot_follow_xq✓(5619行) · "
        "stock_hot_follow_xq[本周新增]✓(5619行) · 观测日 20260809")
    assert "热度快照" not in line                # 全源足量✓,不多贴告警


def test_prewarm_line_backward_compatible_without_steps_key(tmp_path):
    """旧 `_prewarm.json`(无 `steps` 字段,即 `test_prewarm_line_detects_artifact` 那份
    fixture 的形态)不该因新逻辑报错或变化。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    (d / "_prewarm.json").write_text('{"started_at": 1, "ended_at": 2}', encoding="utf-8")
    line = prelude.prewarm_line("2026-07-25", scan_root=tmp_path)
    assert "✓" in line and "热度快照" not in line


def test_write_summary_file(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    p = prelude.write_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "✓ universe" in text and "✗ menu" in text


def test_summary_shows_macro_state_freshness(tmp_path, monkeypatch):
    """宏观 full 摘要缺/过期必须当天可见(Wave5 ③B:它恒缺了一个月而无人察觉)。"""
    monkeypatch.setattr("autoresearch.macro.state.DEFAULT_ROOT", tmp_path / "nope")
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "宏观 full 摘要" in out
    assert "✗" in out


def test_macro_state_line_reports_fresh(tmp_path, monkeypatch):
    import json
    (tmp_path / "macro_state.json").write_text(json.dumps(
        {"as_of": "2026-07-24", "ttl_days": 7, "regime_at_run": None}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.macro.state.DEFAULT_ROOT", tmp_path)
    line = prelude.macro_state_line("2026-07-25")
    assert line.startswith("宏观 full 摘要:✓")
    assert "新鲜" in line


# ══ Wave9 A-1 复核轮1 Critical:📡 提醒行必须回看历史日,不能读当日 ══════════════════
#
# `L3_news/` 由 L3 阶段 `harvest_l3_news()` 生成,而 render_summary 在 prelude(L0→L2)
# 末尾就跑——读当日 `anns_source_status` 必然撞见目录还不存在(=pending)。原实现直接
# 读当日,若把 pending 当 blind 处理就会天天无条件误报;实盘佐证:2026-07-29
# `L2_gbdt_top200.csv` mtime 与 `L3_news/` mtime 相差 9 分 36 秒,后者严格晚于 prelude
# 收尾。修法:回看最近一个已完成扫描日(跳过仍是 pending 的日子)。

def test_anns_reminder_shows_when_most_recent_completed_day_is_blind(tmp_path):
    """用例①:回看到的最近一个已完成扫描日是 blind(有稿但双源皆空)→ 出 📡 行,标注
    该日期。"""
    prev = tmp_path / "2026-07-24"
    (prev / "L3_news").mkdir(parents=True)
    (prev / "L3_news" / "000001.json").write_text("[]", encoding="utf-8")
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "📡" in out and "2026-07-24" in out


def test_anns_reminder_silent_when_most_recent_completed_day_is_ok(tmp_path):
    """用例②:回看到的最近一个已完成扫描日是 ok(主源有料)→ 不出 📡 行。"""
    import json as _json
    prev = tmp_path / "2026-07-24"
    (prev / "L3_news").mkdir(parents=True)
    (prev / "L3_news" / "000001.json").write_text(
        _json.dumps([{"ann_date": "20260724", "title": "x"}]), encoding="utf-8")
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "📡" not in out


def test_anns_reminder_silent_when_today_pending_and_no_history(tmp_path):
    """用例③(Critical 回归钉子):当日目录只有 L2 产物、无 `L3_news/`(= prelude 跑完
    那一刻的真实现场),且无更早历史日 → 不出 📡 行。这正是复核轮1 抓到的场景本身:
    render_summary 在 prelude 末尾跑,L3 阶段(生成 L3_news/)此时还没开始。

    ⚠️ 判别力说明(见 task-2-report.md 追加报告):本用例的 fixture 里"回看候选"集合
    天生为空(今天被 `p.name < date` 排除、且不存在更早的历史日),循环体在数学上
    压根不会执行——因此把腿2里 `if st == "pending": continue` 删掉**不会**让本用例
    变红(删不删,空集合的 for 循环结果都一样是"什么都不做")。本用例仍是这个 Critical
    的合法回归钉子(钉住"读当日会不会被误判成 blind"这个端到端问题的原始场景),但它
    不是、也不可能是那一行`continue`本身的判别力来源——那一行真正的判别力靠下面
    `test_anns_reminder_skips_past_pending_day_to_older_blind_day` 钉住(变异测试实测
    见 task-2-report.md)。"""
    today = tmp_path / "2026-07-25"
    today.mkdir(parents=True)
    (today / "L2_gbdt_top200.csv").write_text("code\n", encoding="utf-8")
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "📡" not in out


def test_anns_reminder_skips_past_pending_day_to_older_blind_day(tmp_path):
    """补充回归钉子(腿2 `if st == "pending": continue` 唯一有判别力的场景):回看窗口里
    最近一天是 pending(该日 L3 中途夭折,只有 L2 产物没有 `L3_news/`),再往前一天是真
    blind —— 必须跳过 pending 找到更早的 blind 并出 📡,而不是撞到 pending 就地放弃。"""
    older = tmp_path / "2026-07-23"
    (older / "L3_news").mkdir(parents=True)
    (older / "L3_news" / "000001.json").write_text("[]", encoding="utf-8")   # 真 blind
    recent = tmp_path / "2026-07-24"
    recent.mkdir(parents=True)
    (recent / "L2_gbdt_top200.csv").write_text("code\n", encoding="utf-8")   # 该日中途夭折,pending
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "📡" in out and "2026-07-23" in out
