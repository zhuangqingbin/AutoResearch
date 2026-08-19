"""B5 判据的计量:结构失败落盘、不可测不冒充干净、开发期重建不算失败。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §B5
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import structural_audit as sa
from autoresearch.scan.l4_tasks import initialize, mark_failure, mark_success, preflight
from autoresearch.scan.stage_result import safe_record_stage_result

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def _files(tmp_path, code: str, ticker: str) -> None:
    scan = tmp_path / DATE
    (scan / "details").mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text("# prompt", encoding="utf-8")
    ctx = tmp_path / "context"
    ctx.mkdir(exist_ok=True)
    (ctx / f"{ticker}_{DATE}_slim.md").write_text(
        "\n".join([
            "## Verified market snapshot",
            "### Latest verified OHLCV row",
            "| Close | 12.34 |",
            "## Market context",
            "## Fundamentals overview",
            "x" * 5000,
        ]),
        encoding="utf-8",
    )
    (scan / "details" / f"{code}.md").write_text("# card", encoding="utf-8")


def _book(tmp_path, code="000001"):
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text("# 任务包\n", encoding="utf-8")
    return initialize(DATE, [code], root=tmp_path, context_root=tmp_path / "context",
                      meta={code: {"ticker": f"{code}.SZ", "pinned": False}}, now=NOW)


def _payload(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _events(path, kind=None) -> list:
    log = _payload(path).get(sa.EVENTS_KEY) or []
    return [e for e in log if kind is None or e["kind"] == kind]


# ── 计量本身 ────────────────────────────────────────────────────────────────

def test_fresh_book_carries_empty_log_so_clean_is_distinguishable_from_unmeasured(tmp_path):
    """空表必须显式写:否则「干净的已计量日」和「计量前的日子」在盘上无法区分。"""
    book = _book(tmp_path)
    payload = _payload(book["path"])
    assert payload[sa.EVENTS_KEY] == []

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.measured is True
    assert audit.failure_n == 0
    assert audit.clean is True


def test_legacy_book_is_unmeasured_not_clean(tmp_path):
    """计量上线前的账本:failure_n 必须是 None,**不是 0** —— 「没看」不能冒充「没有」。"""
    book = _book(tmp_path)
    path = tmp_path / DATE / "_l4_tasks.json"
    payload = _payload(path)
    del payload[sa.EVENTS_KEY]                      # 退回计量上线前的形状
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.measured is False
    assert audit.failure_n is None
    assert audit.clean is None
    assert book["ok"]


def test_record_refuses_to_retrofit_the_key_onto_a_legacy_book(tmp_path):
    """补建键 = 给那天伪造「已计量」身份,而它的历史事件早已丢失。"""
    payload = {"tasks": {}}
    assert sa.record(payload, "000001", sa.TERMINAL_RERUN) is None
    assert sa.EVENTS_KEY not in payload


def test_record_rejects_unknown_kind(tmp_path):
    payload = {"tasks": {}, sa.EVENTS_KEY: []}
    with pytest.raises(ValueError, match="unknown structural event kind"):
        sa.record(payload, "000001", "SOMETHING_ELSE")


# ── streak:B5 那根杆的判据 ──────────────────────────────────────────────────

def _audit(date, measured=True, failure_n=0):
    return sa.DayAudit(date, measured, failure_n, 0, {}, "")


def test_streak_counts_consecutive_clean_runs():
    audits = [_audit("2026-07-27"), _audit("2026-07-28"), _audit("2026-07-29")]
    assert sa.streak(audits)["streak"] == 3


def test_unmeasured_day_breaks_the_streak_exactly_like_a_failure():
    """本模块存在的全部理由:不可测 ≠ 干净。"""
    audits = [_audit("2026-07-27"), _audit("2026-07-28", measured=False, failure_n=None),
              _audit("2026-07-29")]
    stats = sa.streak(audits)
    assert stats["streak"] == 1                      # 只数到不可测日为止
    assert "2026-07-28" in stats["streak_broken_by"]
    assert stats["runs_unmeasured"] == 1


def test_failure_day_breaks_the_streak_and_totals_stay_on_measured_subset():
    audits = [_audit("2026-07-27", failure_n=2), _audit("2026-07-28")]
    stats = sa.streak(audits)
    assert stats["streak"] == 1
    assert stats["failure_n_total"] == 2
    assert stats["as_of"] == "2026-07-28"


def test_missing_task_book_is_a_failure_only_when_the_day_should_have_had_one(tmp_path):
    (tmp_path / DATE).mkdir(parents=True)
    assert sa.audit_day(tmp_path / DATE) is None                       # 非流式日,不入账
    audit = sa.audit_day(tmp_path / DATE, expect_book=True)
    assert audit.failure_n == 1
    assert audit.kinds == {sa.TASK_BOOK_MISSING: 1}


# ── 事后重验:prompt 与 card 的地位不对称 ──────────────────────────────────

def test_post_hoc_prompt_rewrite_is_an_observation_not_a_failure(tmp_path):
    """实测每天都发生(开发期重跑 write_dispatch_pack 原地覆写)。

    prompt 是可重建的**输入**;它事后变了不代表那次运行结构不成立。
    """
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / DATE / "_l4_prompt_000001.md").write_text("# 重建过", encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 0
    assert audit.kinds.get(sa.PROMPT_REBUILT) == 1


def test_post_hoc_card_rewrite_is_a_real_failure(tmp_path):
    """card 是**结果**;结果在完成后变了才是真的产物 hash 失配。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / DATE / "details" / "000001.md").write_text("# 被改了", encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 1
    assert audit.kinds.get(sa.ARTIFACT_HASH_MISMATCH) == 1


def _legacy_path_book(tmp_path, monkeypatch, *, body: str = "# card") -> Path:
    """造一份「路径串停留在分根前、文件在现根下」的成功票账本(08-11 及之前的真实形状)。

    路径必须是**相对**的(生产账本记的就是相对根),所以整个用例在 tmp_path 下跑。
    """
    monkeypatch.chdir(tmp_path)
    ctx = Path(ws.context_root())                      # context_<engine>/
    (ctx / "scan" / DATE / "details").mkdir(parents=True, exist_ok=True)
    legacy_root = ws.context_root().name.split("_")[0]
    refs = {}
    for name, rel in (("prompt", f"scan/{DATE}/_l4_prompt_000001.md"),
                      ("slim", f"000001.SZ_{DATE}_slim.md"),
                      ("card", f"scan/{DATE}/details/000001.md")):
        real = ctx / rel
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(body if name == "card" else f"# {name}", encoding="utf-8")
        refs[name] = {                                  # ← 账本记的是**旧根**下的那个串
            "path": f"{legacy_root}/{rel}",
            "content_hash": hashlib.sha256(real.read_bytes()).hexdigest(),
        }
    scan = ctx / "scan" / DATE
    (scan / "_l4_tasks.json").write_text(json.dumps({
        "schema_version": 1, "date": DATE, "order": ["000001"],
        sa.EVENTS_KEY: [],
        "tasks": {"000001": {"code": "000001", "ticker": "000001.SZ",
                             "status": "SUCCEEDED", "artifacts": refs}},
    }, ensure_ascii=False), encoding="utf-8")
    return scan


def test_pre_engine_split_paths_are_an_observation_not_a_failure(tmp_path, monkeypatch):
    """引擎分根(2026-08-11 裁定)把 `<legacy>/…` 搬成 `context_<engine>/…`;路径串失效
    **不是**产物失配 —— 迁移后逐字节同 hash 的,记观察不记失败。

    变异校验:把 `_resolve` 的别名分支删掉,本用例必红(3 条会全判 ARTIFACT_HASH_MISMATCH,
    failure_n 变 3)。实测依据:08-03..08-11 七个可测日 216 条"失败"全属此类,332/339 条
    历史 legacy 路径在迁移位置上 hash 逐条相符。
    """
    scan = _legacy_path_book(tmp_path, monkeypatch)
    audit = sa.audit_day(scan)

    assert audit.failure_n == 0
    assert audit.kinds.get(sa.ARTIFACT_PATH_MIGRATED) == 3
    assert sa.ARTIFACT_HASH_MISMATCH not in audit.kinds
    assert audit.observation_n == 3                     # 观察量必须**可见**,不是静默豁免


def test_migrated_path_with_changed_content_is_still_a_real_failure(tmp_path, monkeypatch):
    """别名只解释迁移,不替内容变更打掩护:card 在迁移后的位置上被改过 → 照旧算失败。"""
    scan = _legacy_path_book(tmp_path, monkeypatch)
    (Path(ws.context_root()) / "scan" / DATE / "details" / "000001.md").write_text(
        "# 被改了", encoding="utf-8")

    audit = sa.audit_day(scan)
    assert audit.failure_n == 1
    assert audit.kinds.get(sa.ARTIFACT_HASH_MISMATCH) == 1
    assert audit.kinds.get(sa.ARTIFACT_PATH_MIGRATED) == 2   # 另两件仍只是迁移


def test_truly_missing_artifact_is_still_a_failure(tmp_path, monkeypatch):
    """两次都找不到 → 老实判失败。别名不得把真正丢失的产物洗白。"""
    scan = _legacy_path_book(tmp_path, monkeypatch)
    (Path(ws.context_root()) / "scan" / DATE / "details" / "000001.md").unlink()

    audit = sa.audit_day(scan)
    assert audit.failure_n == 1
    assert any(f["detail"].endswith("产物已不在盘上")
               for f in sa._rehash_findings(json.loads(
                   (scan / "_l4_tasks.json").read_text(encoding="utf-8"))))


def test_non_terminal_tasks_are_not_rehashed(tmp_path):
    """未终态票的产物本来就允许在变,不该被当失配。"""
    _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    audit = sa.audit_day(tmp_path / DATE)             # 从未 mark_success
    assert audit.failure_n == 0


# ── P1-1(2026-08-19 取证):判官必须看见「卡死在 RUNNING」──────────────────

def _mark_published(tmp_path, *, date=DATE) -> None:
    """模拟 L5 assemble 阶段已落过 StageResult(P1-1『run 已发布』的判据本体)。"""
    safe_record_stage_result(tmp_path / date, stage="assemble", status="SUCCEEDED",
                             artifacts=[], metrics={}, warnings=[], error=None)


def test_stuck_running_task_is_invisible_before_the_run_is_published(tmp_path):
    """未发布(无 `stage_results/assemble.json`)时,非终态票是正常在跑,不算异常
    ——`test_non_terminal_tasks_are_not_rehashed` 已经锁了旧判据的这条边界,这里
    显式点名一次,证明 P1-1 新判据同样尊重「还没跑完 ≠ 卡死」。"""
    _book(tmp_path)                       # 000001 初始 status=PENDING,从未认领
    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 0
    assert sa.TASK_STUCK_RUNNING not in audit.kinds


def test_stuck_running_task_is_a_failure_once_the_run_is_published(tmp_path):
    """P1-1 核心断言:run 已发布(assemble StageResult 在场)而票仍非终态 → 计失败。

    复现 2026-08-12 真实形态的缩小版(9 票全卡 RUNNING、`mark_success` 从未执行、
    E6 contract 门团灭当天全部候选,但旧 `_rehash_findings` 只查 `SUCCEEDED`,把
    那天评成 `failure_n=0`「无事件」)。

    变异校验(task-1.9 report 贴了完整命令与输出):把 `audit_day` 里那次
    `_stuck_running_findings(scan, payload)` 调用摘掉,本测试必红 ——
    `failure_n` 会掉回 0,`clean` 会掉回 `True`,08-12 那天又变回「假干净日」。
    """
    book = _book(tmp_path)
    preflight(book["path"], "000001", now=NOW)      # PENDING → RUNNING(认领后失联,再无终态回写)
    _mark_published(tmp_path)

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 1
    assert audit.kinds == {sa.TASK_STUCK_RUNNING: 1}
    assert audit.clean is False


def test_pending_task_after_publish_is_also_stuck(tmp_path):
    """`PENDING`(从未认领)与 `RUNNING`(认领未回写)同属非终态 —— 票从未被 `preflight`
    碰过、run 却已发布,同样是异常,不能只逮 RUNNING 漏了 PENDING。"""
    _book(tmp_path)                       # 从未 preflight,status 仍是初始 PENDING
    _mark_published(tmp_path)

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 1
    assert audit.kinds == {sa.TASK_STUCK_RUNNING: 1}


def test_blocked_task_after_publish_is_not_stuck_running(tmp_path):
    """`BLOCKED` 是终态(已有明确了结的失败判断),发布后仍是 BLOCKED 不该被
    `TASK_STUCK_RUNNING` 二次计失败 —— 它已经被 `mark_failure` 记过一次理由了,
    这里只验证它不被新判据误伤,不重复验证 `mark_failure` 自身的记账。"""
    book = _book(tmp_path)
    mark_failure(book["path"], "000001", "NON_TRANSIENT_KIND", now=NOW)   # → BLOCKED
    _mark_published(tmp_path)

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.kinds.get(sa.TASK_STUCK_RUNNING) is None
    assert audit.failure_n == 0


def test_succeeded_and_stuck_tasks_are_both_visible_in_the_same_book(tmp_path):
    """混合票本(部分正常收尾、部分卡死)—— 两类判据互不掩盖:SUCCEEDED 票走
    `_rehash_findings` 的干净路径,RUNNING 票被 `TASK_STUCK_RUNNING` 单独逮住,
    `failure_n` 只数后者那一票,不该把前者也牵连进去。"""
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    (scan / "_l4_prompt_000001.md").write_text("# 任务包\n", encoding="utf-8")
    (scan / "_l4_prompt_000002.md").write_text("# 任务包\n", encoding="utf-8")
    book = initialize(DATE, ["000001", "000002"], root=tmp_path,
                      context_root=tmp_path / "context",
                      meta={"000001": {"ticker": "000001.SZ", "pinned": False},
                            "000002": {"ticker": "000002.SZ", "pinned": False}},
                      now=NOW)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    preflight(book["path"], "000002", now=NOW)       # PENDING → RUNNING,再无终态回写
    _mark_published(tmp_path)

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 1
    assert audit.kinds == {sa.TASK_STUCK_RUNNING: 1}


# ── 活体接线:四个检测点真的会落盘 ────────────────────────────────────────

def test_artifact_change_at_preflight_is_recorded(tmp_path):
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / "context" / "000001.SZ_2026-07-28_slim.md").write_text(
        "变了", encoding="utf-8")

    preflight(book["path"], "000001", now=NOW)
    assert len(_events(book["path"], sa.ARTIFACT_HASH_MISMATCH)) == 1


def test_redispatch_of_verified_success_is_an_observation_not_a_failure(tmp_path):
    """断点续跑本来就会重派;守卫拦住 ≠ 失败,否则 streak 永远绿不了 = 判据废掉。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)

    assert preflight(book["path"], "000001", now=NOW)["action"] == "SKIP"
    assert len(_events(book["path"], sa.TERMINAL_REDISPATCH_BLOCKED)) == 1
    assert sa.audit_day(tmp_path / DATE).failure_n == 0


def test_stale_running_task_is_recorded_as_completion_misjudged(tmp_path):
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    preflight(book["path"], "000001", now=NOW)                 # → RUNNING
    later = NOW + timedelta(seconds=7200)

    preflight(book["path"], "000001", now=later)
    assert len(_events(book["path"], sa.COMPLETION_MISJUDGED)) == 1


def test_mark_success_rejection_survives_the_raise(tmp_path):
    """原先只抛异常:上层一吞,这件事再无痕迹,B5 的判据永远查不到它。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    (tmp_path / DATE / "details" / "000001.md").unlink()

    with pytest.raises(ValueError, match="missing artifacts"):
        mark_success(book["path"], "000001", now=NOW)

    assert len(_events(book["path"], sa.COMPLETION_MISJUDGED)) == 1
    assert sa.audit_day(tmp_path / DATE).failure_n == 1


# ── 渲染:分母与不可测必须同屏 ────────────────────────────────────────────

def test_render_shows_denominator_and_refuses_to_hide_unmeasured():
    audits = [_audit("2026-07-27", measured=False, failure_n=None), _audit("2026-07-28")]
    text = "\n".join(sa.render(audits, required=10))
    assert "1/10" in text and "不得退役兜底" in text
    assert sa.UNMEASURED in text
    assert "不可测 1" in text
