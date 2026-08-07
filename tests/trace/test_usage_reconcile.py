"""Wave11 B4:配置期望 × usage_harvest 实测逐 role 对账 —— 「配置生效」从口头变成可断言。

变异验证是本文件的灵魂(见 task-9 brief):`test_effort_mutation_caught` 把某 role 的
实测 effort 改错,断言 `reconcile` 必须报出差异;`test_wire_break_detected` 把 config
写了但当日无对应实测行的 role 断言进 `wire_breaks`——这两个不逮住,这个对账工具本身
就是下一个「生产者没接线」的笑话。
"""
import copy
import json
from pathlib import Path

import pytest

from autoresearch.trace import usage_reconcile as ur

ECHO = {"agents": {"l4_card": {"effort": "max"}, "l4_intel": {"effort": "max"},
                   "gp_shell": {"model": "sonnet", "effort": "low"},
                   "gp_shell_json": {"model": "sonnet", "effort": "low"}}}
# brief 原 Step 1 只给 2 行(l4-card + general-purpose),但 ECHO 同时配了 l4_intel——
# `_EXPECT_PRESENT` 把 l4_intel 也纳入「全扫日应在场」候选,只给 2 行会让 wire_breaks
# 命中 l4_intel,连累 test_clean_run_ok 断言 r["ok"] 失败(不是逻辑 bug,是原 fixture 对自己
# 配的 ECHO 不完整——`test_clean_run_ok` 的原始 2 行版本经实测确会失败,见 task-9-report.md)。
# 补一行 l4-intel(与 frontmatter+echo 期望一致、不引入新 mismatch)让"干净跑"名副其实。
ROWS = [
    {"role": "subagent", "agent": "l4-card", "model": "claude-opus-5", "effort": "max", "status": "SUCCEEDED"},
    {"role": "subagent", "agent": "general-purpose", "model": "claude-sonnet-5", "effort": "low", "status": "SUCCEEDED"},
    {"role": "subagent", "agent": "l4-intel", "model": "claude-sonnet-5", "effort": "max", "status": "SUCCEEDED"},
]


def _run(tmp_path, echo, rows, date="2026-08-06"):
    d = tmp_path / "context/scan" / date
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(echo))
    (d / "_token_usage.json").write_text(json.dumps({"rows": rows}))
    return ur.reconcile(date, root=tmp_path)


# ───────────────────────── brief Step 1(逐字保留)─────────────────────────


def test_clean_run_ok(tmp_path):
    r = _run(tmp_path, ECHO, ROWS)
    assert r["ok"] and not r["mismatches"]


def test_effort_mutation_caught(tmp_path):
    rows = [dict(ROWS[0], effort="low")] + ROWS[1:]        # 变异:l4_card 实测 low
    r = _run(tmp_path, ECHO, rows)
    assert not r["ok"]
    assert any(m["agent"] == "l4-card" and m["field"] == "effort" for m in r["mismatches"])


def test_wire_break_detected(tmp_path):
    r = _run(tmp_path, ECHO, ROWS[1:])                     # config 写了 l4_card,当日无实测行
    assert "l4_card" in r["wire_breaks"]


# ───────────────────────── 补充:model mismatch / role 覆盖链 ─────────────────────────


def test_model_mismatch_caught(tmp_path):
    """model 字段同样受检——不是只查 effort(frontmatter model=opus,实测混入 sonnet)。"""
    rows = [dict(ROWS[0], model="claude-sonnet-5")] + ROWS[1:]
    r = _run(tmp_path, ECHO, rows)
    assert not r["ok"]
    hit = [m for m in r["mismatches"] if m["agent"] == "l4-card" and m["field"] == "model"]
    assert hit and hit[0]["expected"] == "opus" and hit[0]["actual"] == "sonnet"


def test_echo_override_wins_over_frontmatter_default(tmp_path):
    """回退链 = config > frontmatter:l4-card frontmatter effort=xhigh,echo 覆盖成 max,
    实测 max → 应该判合规(不能仍拿 frontmatter 的 xhigh 去比,那样会对着正常配置报假警)。
    """
    r = _run(tmp_path, ECHO, ROWS)          # ECHO 里 l4_card.effort=max,ROWS[0] 实测也是 max
    assert not any(m["agent"] == "l4-card" for m in r["mismatches"])


def test_role_without_echo_override_falls_back_to_frontmatter(tmp_path):
    """role 完全没在 echo 里配(既没有 l4_intel 键)→ 期望值全部落回 frontmatter 缺省。"""
    echo = {"agents": {"l4_card": {"effort": "max"}}}       # 不含 l4_intel
    rows = [ROWS[0],
            {"role": "subagent", "agent": "l4-intel", "model": "claude-sonnet-5",
             "effort": "medium", "status": "SUCCEEDED"}]    # frontmatter 缺省 effort=max,实测 medium
    r = _run(tmp_path, echo, rows)
    hit = [m for m in r["mismatches"] if m["agent"] == "l4-intel" and m["field"] == "effort"]
    assert hit and hit[0]["expected"] == "max"               # 来自 l4-intel.md frontmatter,非 echo


# ───────────────────────── 壳类(general-purpose)集合断言 ─────────────────────────


def test_gp_shell_third_pattern_flagged(tmp_path):
    """gp_shell/gp_shell_json 两个允许 spec 之外的第三种实测组合(haiku·无 effort)—— 不能被
    「反正是壳类就放过」悄悄吃掉,必须报出来(这正是 2026-08-05 真实数据里出现的模式)。
    """
    rows = ROWS[:1] + [{"role": "subagent", "agent": "general-purpose",
                        "model": "claude-haiku-4-5-20251001", "effort": "—",
                        "status": "SUCCEEDED"}]
    r = _run(tmp_path, ECHO, rows)
    hit = [m for m in r["mismatches"] if m["agent"] == "general-purpose"]
    assert hit and hit[0]["field"] == "model+effort"
    assert hit[0]["actual"] == ["haiku", "—"]


def test_gp_shell_defaults_to_sonnet_low_when_unconfigured(tmp_path):
    """echo 完全没写 gp_shell/gp_shell_json 时,允许集合缺省回落 {sonnet, low}
    (与 workflow AGENT_DEFAULTS 的意图一致)——不是"没配就放行一切"。
    """
    echo = {"agents": {}}
    ok_rows = [{"role": "subagent", "agent": "general-purpose",
               "model": "claude-sonnet-5", "effort": "low", "status": "SUCCEEDED"}]
    r_ok = _run(tmp_path, echo, ok_rows, date="2026-08-01")
    assert not any(m["agent"] == "general-purpose" for m in r_ok["mismatches"])

    bad_rows = [{"role": "subagent", "agent": "general-purpose",
                "model": "claude-opus-5", "effort": "max", "status": "SUCCEEDED"}]
    r_bad = _run(tmp_path, echo, bad_rows, date="2026-08-02")
    assert any(m["agent"] == "general-purpose" for m in r_bad["mismatches"])


# ───────────────────────── unknown_agent_types(2026-08-06 review Minor 2) ─────────────────────────


def test_unknown_agent_type_reported_not_silently_dropped(tmp_path):
    """既不在 `AGENTTYPE_TO_ROLE`、也不是 `general-purpose` 的 agentType——此前会静默跳过
    (不判也不报,mismatches 里看不出任何痕迹);现在必须出现在 `unknown_agent_types` 里,
    并且让 `ok` 翻假(与 `wire_breaks` 的"配置写了没人接"对称:这边是"来了个不认识的")。
    """
    rows = ROWS + [{"role": "subagent", "agent": "some-new-agent-type",
                    "model": "claude-opus-5", "effort": "max", "status": "SUCCEEDED"}]
    r = _run(tmp_path, ECHO, rows)
    assert r["unknown_agent_types"] == ["some-new-agent-type"]
    assert r["ok"] is False
    # 不该被塞进 mismatches——unknown 是"判不了",不是"判出了具体哪个字段不符"
    assert not any(m["agent"] == "some-new-agent-type" for m in r["mismatches"])


def test_unknown_agent_types_deduped_across_rows(tmp_path):
    """同一个未知 agentType 出现多行,只报一次(与 `wire_breaks` 按 role 去重的粒度一致)。"""
    rows = [{"role": "subagent", "agent": "mystery-agent", "model": "claude-opus-5",
            "effort": "max", "status": "SUCCEEDED"} for _ in range(3)]
    r = _run(tmp_path, ECHO, rows)
    assert r["unknown_agent_types"] == ["mystery-agent"]
    assert r["checked"] == 3                    # 三行都真的被"看过"(checked 计数不受影响)


def test_no_unknown_agent_types_key_stays_empty_on_clean_run(tmp_path):
    """全部 agentType 都认识时,`unknown_agent_types` 是空列表而不是缺键——消费者可以
    无条件 `result["unknown_agent_types"]`,不用先 `.get(..., [])` 防 KeyError。
    """
    r = _run(tmp_path, ECHO, ROWS)
    assert r["unknown_agent_types"] == []
    assert r["ok"] is True


def test_main_role_rows_ignored(tmp_path):
    """role=main(主会话自身)不受 agentType→role 规则约束,不该被拿去跟任何 role 期望比对;
    `checked` 只数真正进了比对循环的行(2026-08-06 review Minor 1)—— role=main 那行虽然
    在 rows 里,但从未被比对过,不该被算进"实测行 N 条"。
    """
    rows = [{"role": "main", "agent": "(主会话)", "model": "claude-opus-5",
            "effort": "max", "status": "FAILED"}]
    r = _run(tmp_path, ECHO, rows)
    assert r["checked"] == 0
    assert r["mismatches"] == []


# ───────────────────────── 已知局限:ens_review/l3_repair 复用父 role 的 agentType ─────────────────────────


def test_known_gap_ens_review_row_misjudged_against_l4_card_expectation(tmp_path):
    """记录一个已知局限(不是本 task 要修的范围,但必须有测试证明"风险是真的"而不是
    臆测):`ens_review`(l4-card 双复核 run2/3)复用 `agentType: 'l4-card'` 派发(见
    `.claude/workflows/l4-stock.js` `rerun()`),harvest 分不出一行到底是主卡还是复核。

    只要生产配置里 `ens_review.effort` 与 `l4_card.effort` 恰好相同,这条 gap 不会显影;
    一旦分开调参(这正是 Task 8 把 ens_review 独立成 role 的目的),本表就会对着
    ens_review 的真实行为误判——本测试构造这个场景,证明误判确实发生,而不是空口描述。
    """
    echo = {"agents": {"l4_card": {"effort": "max"}, "ens_review": {"effort": "medium"}}}
    rows = [
        {"role": "subagent", "agent": "l4-card", "model": "claude-opus-5",
         "effort": "max", "status": "SUCCEEDED"},             # 真实是主卡调用,配置合规
        {"role": "subagent", "agent": "l4-card", "model": "claude-opus-5",
         "effort": "medium", "status": "SUCCEEDED"},           # 真实是 ens_review 复核,配置也合规(medium)
    ]
    r = ur._reconcile_core(echo, rows, date="2026-08-06")
    # 现状:第二行被误判成"l4_card mismatch"(expected max,actual medium)——它其实是
    # ens_review 的合规行为,本表目前分不清,把它当成了 l4_card 的违规。这就是已知局限。
    bad = [m for m in r["mismatches"] if m["agent"] == "l4-card" and m["field"] == "effort"]
    assert len(bad) == 1 and bad[0]["actual"] == "medium", (
        "本测试锁定已知局限的现状表现;若未来实现了 ens_review 并集豁免(仿 gp_shell),"
        "这条断言应该改写成'不再误判',而不是被删除——删除会让这个局限重新变回没人知道")


# ───────────────────────── presence-gated:产物缺失 ─────────────────────────


def test_missing_echo_raises(tmp_path):
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    (d / "_token_usage.json").write_text(json.dumps({"rows": ROWS}))
    with pytest.raises(FileNotFoundError):
        ur.reconcile("2026-08-06", root=tmp_path)


def test_missing_token_usage_raises(tmp_path):
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(ECHO))
    with pytest.raises(FileNotFoundError):
        ur.reconcile("2026-08-06", root=tmp_path)


def test_empty_rows_checked_zero_but_wire_breaks_fire(tmp_path):
    """_token_usage.json 存在但 rows 空(如全废弃)→ 0 条可查、无 mismatch(不是"没查"就报错),
    但 wire_breaks 仍会命中——config 配了 l4_card/l4_intel,一行实测都没有,这不该被 0
    mismatches 掩盖成"一切正常"。
    """
    r = _run(tmp_path, ECHO, [])
    assert r["checked"] == 0
    assert r["mismatches"] == []
    assert r["ok"] is False
    assert set(r["wire_breaks"]) >= {"l4_card", "l4_intel"}


# ───────────────────────── _frontmatter / _norm_model 单元 ─────────────────────────


def test_frontmatter_reads_real_l4_card_agent_def():
    """对真实 `.claude/agents/l4-card.md` 的 sanity check——这份 frontmatter 是
    `test_clean_run_ok` 之所以能通过的隐性前提(model=opus 未被 echo 覆盖,effort 被覆盖)。
    """
    fm = ur._frontmatter("l4-card")
    assert fm.get("model") == "opus"
    assert fm.get("effort") == "xhigh"


def test_frontmatter_missing_agent_type_returns_empty():
    assert ur._frontmatter("no-such-agent-type") == {}


@pytest.mark.parametrize("raw,expect", [
    ("claude-opus-5", "opus"),
    ("claude-sonnet-5", "sonnet"),
    ("claude-haiku-4-5-20251001", "haiku"),
    (None, "?"),
    ("", "?"),
    ("gpt-4", "gpt-4"),
])
def test_norm_model(raw, expect):
    assert ur._norm_model(raw) == expect


# ───────────────────────── render():报表头三条边界必须在场 ─────────────────────────


def test_render_header_states_three_boundaries(tmp_path):
    r = _run(tmp_path, ECHO, ROWS)
    md = ur.render(r)
    assert "时序" in md and "GATE4" in md
    assert "壳类" in md and "gp_shell" in md
    assert "effort 是请求参数" in md


def test_render_lists_mismatches_and_wire_breaks(tmp_path):
    r = _run(tmp_path, ECHO, ROWS[1:])                      # 缺 l4-card 行 → l4_card wire_break
    md = ur.render(r)
    assert "l4_card" in md
    assert "wire_breaks" in md


def test_render_lists_unknown_agent_types(tmp_path):
    rows = ROWS + [{"role": "subagent", "agent": "mystery-agent", "model": "claude-opus-5",
                    "effort": "max", "status": "SUCCEEDED"}]
    r = _run(tmp_path, ECHO, rows)
    md = ur.render(r)
    assert "unknown_agent_types" in md
    assert "mystery-agent" in md


# ───────────────────────── CLI:main() ─────────────────────────


def test_cli_writes_json_ledger_and_always_exits_zero(tmp_path, capsys):
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(ECHO))
    rows = [dict(ROWS[0], effort="low")] + ROWS[1:]         # 故意留一个 mismatch
    (d / "_token_usage.json").write_text(json.dumps({"rows": rows}))
    json_out = tmp_path / "context/scan/2026-08-06/_usage_reconcile.json"

    code = ur.main(["2026-08-06", "--root", str(tmp_path), "--json-out", str(json_out)])

    assert code == 0                                        # exit 恒 0,即便 ok=false
    written = json.loads(json_out.read_text())
    assert written["ok"] is False and written["mismatches"]
    ledger_path = tmp_path / "context/learning/usage_reconcile.jsonl"
    lines = [ln for ln in ledger_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["date"] == "2026-08-06"


def test_cli_ledger_appends_across_runs(tmp_path):
    """streak 账本 append-only:两次 CLI 调用 → 两行(self_review 的连续两日判据靠这个)。"""
    for date in ("2026-08-05", "2026-08-06"):
        d = tmp_path / "context/scan" / date
        d.mkdir(parents=True)
        (d / "user_config_echo.json").write_text(json.dumps(ECHO))
        (d / "_token_usage.json").write_text(json.dumps({"rows": ROWS}))
        ur.main([date, "--root", str(tmp_path)])
    ledger_path = tmp_path / "context/learning/usage_reconcile.jsonl"
    lines = [ln for ln in ledger_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert [json.loads(ln)["date"] for ln in lines] == ["2026-08-05", "2026-08-06"]


def test_cli_missing_product_still_exits_zero(tmp_path, capsys):
    """usage_harvest 没跑/产物缺失 → 不能让 `&&` 串联的 post_run 断链,恒 exit 0。"""
    code = ur.main(["2026-08-06", "--root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "缺产物" in out


def test_cli_no_ledger_flag_skips_append(tmp_path):
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(ECHO))
    (d / "_token_usage.json").write_text(json.dumps({"rows": ROWS}))
    ur.main(["2026-08-06", "--root", str(tmp_path), "--no-ledger"])
    assert not (tmp_path / "context/learning/usage_reconcile.jsonl").exists()


# ───────────────────────── 变异验证 · 对真实 2026-08-05 数据 ─────────────────────────
# task-9 brief 明确要求:两个变异都必须在真实 echo/harvest 上跑通,不能只在合成 fixture 上
# 自证。`context/scan/2026-08-05/` 是 gitignored 的真实产物,新 checkout 可能没有——
# 缺失时跳过而不是失败(这两条是"锦上添花"的真数据回归,不是核心契约测试)。

_REAL_DIR = Path("context/scan/2026-08-05")
_real_data_present = (_REAL_DIR / "user_config_echo.json").exists() and \
    (_REAL_DIR / "_token_usage.json").exists()


@pytest.mark.skipif(not _real_data_present, reason="真实 2026-08-05 产物不在场(gitignored,新 checkout 正常缺失)")
class TestRealDataMutations:
    """对 2026-08-05 真实产物做受控变异——用真实数据而非合成 fixture 验证鉴别力。"""

    @staticmethod
    def _load():
        echo = json.loads((_REAL_DIR / "user_config_echo.json").read_text())
        rows = json.loads((_REAL_DIR / "_token_usage.json").read_text())["rows"]
        return echo, rows

    def test_baseline_is_not_trivially_clean(self):
        """基线本身不是空跑 —— 44 条真实 mismatch(l3-rank 分叉 + gp haiku 壳)、0 wire_break、
        0 unknown_agent_type。这条锁住"真实数据长什么样",变异测试改的是这个基线之上的
        一处,不是从零构造。`checked` 只数 role=="subagent" 的行(2026-08-06 review Minor 1
        修正后)——真实数据里有 1 行 role=="main"(主会话自身,状态 FAILED),不进比对循环,
        所以 `checked` 比 `len(rows)` 少 1,不是相等。
        """
        echo, rows = self._load()
        base = ur._reconcile_core(echo, rows, date="2026-08-05")
        n_subagent_rows = sum(1 for r in rows if r.get("role") == "subagent")
        assert base["checked"] == n_subagent_rows
        assert base["checked"] == len(rows) - 1   # 真实数据里恰好 1 行 role=="main"
        assert base["wire_breaks"] == []
        assert base["unknown_agent_types"] == []
        assert len(base["mismatches"]) >= 1     # 至少那条 l3-rank medium/max 真分叉

    def test_mutation_effort_wrong_in_echo_caught_against_real_harvest(self):
        """变异 A:echo 里 l4_card.effort 改错(max→low),真实 harvest 原样不动 ——
        12 条 l4-card 实测行(真实值全是 max)必须**逐行**报出 effort mismatch。
        """
        echo, rows = self._load()
        mutated = copy.deepcopy(echo)
        mutated["agents"]["l4_card"]["effort"] = "low"
        result = ur._reconcile_core(mutated, rows, date="2026-08-05")
        hits = [m for m in result["mismatches"] if m["agent"] == "l4-card" and m["field"] == "effort"]
        n_l4_card_rows = sum(1 for r in rows if r.get("agent") == "l4-card" and r.get("role") == "subagent")
        assert n_l4_card_rows > 0, "真实数据里应该有 l4-card 实测行,否则本测试没测到东西"
        assert len(hits) == n_l4_card_rows
        assert all(h["expected"] == "low" and h["actual"] == "max" for h in hits)

    def test_mutation_role_dropped_from_harvest_flags_wire_break(self):
        """变异 B:从真实 harvest 里摘掉某个已配置 role 的全部实测行(echo 不动)——
        `wire_breaks` 必须报出该 role(用 sector_brief,真实数据里有 8 行可摘)。
        """
        echo, rows = self._load()
        n_before = sum(1 for r in rows if r.get("agent") == "sector-brief")
        assert n_before > 0, "真实数据里应该有 sector-brief 实测行,否则本测试没测到东西"
        rows_dropped = [r for r in rows if r.get("agent") != "sector-brief"]
        result = ur._reconcile_core(echo, rows_dropped, date="2026-08-05")
        assert "sector_brief" in result["wire_breaks"]

    def test_both_mutations_independently_visible(self):
        """负对照:两处变异同时施加,互不掩盖(effort mismatch 不会盖过 wire_break,反之亦然)。"""
        echo, rows = self._load()
        mutated_echo = copy.deepcopy(echo)
        mutated_echo["agents"]["l4_card"]["effort"] = "low"
        rows_dropped = [r for r in rows if r.get("agent") != "sector-brief"]
        result = ur._reconcile_core(mutated_echo, rows_dropped, date="2026-08-05")
        assert any(m["agent"] == "l4-card" and m["field"] == "effort" for m in result["mismatches"])
        assert "sector_brief" in result["wire_breaks"]
