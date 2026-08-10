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


# ───── Wave12-T34:ens_review/l3_repair 复用父 agentType 的归因(原「已知局限」已转正）─────

# 两 role effort 分开调参的 echo —— 这是本组测试的全部意义:配置值相同的时候
# 老实现"蒙混"得过去,只有分开调参才照得出归因到底对不对。
ECHO_SPLIT = {"agents": {"l4_card": {"effort": "max"}, "ens_review": {"effort": "medium"}}}


def _l4card_row(effort):
    return {"role": "subagent", "agent": "l4-card", "model": "claude-opus-5",
            "effort": effort, "status": "SUCCEEDED"}


def test_ens_review_compliant_row_no_longer_misjudged_as_l4_card(tmp_path):
    """[Wave12-T34] 原 `test_known_gap_ens_review_row_misjudged_against_l4_card_expectation`
    的**转正版**(按那条测试自己写的处置意见改写,不是删除)。

    场景:主卡 1 次(max,合规)+ ens_review 复核 1 次(medium,同样合规)。
    census 说了"ens_review 派了 1 次",所以那一行归 ens_review,按 medium 判 → 不再有
    mismatch。老实现会把它当成 l4_card 的违规(假阳)。
    """
    r = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("max"), _l4card_row("medium")],
                           date="2026-08-06", census={"ens_review": 1})
    assert r["mismatches"] == [], "ens_review 的合规行不该再被冤枉成 l4_card mismatch"


def test_ens_review_real_deviation_reported_on_its_own_row(tmp_path):
    """[Wave12-T34 验收] 造 ens_review 与 l4_card effort 不同的 echo,reconcile 必须**分行报**。

    场景:census 说主卡 1 次 + 复核 1 次,但两行**都**是 max —— 即复核没按 medium 跑。
    要求:恰好 1 条 mismatch,且 `role == "ens_review"`(不是 l4_card)。
    这正是老实现的**假阴**:两行都 max,l4_card 期望也是 max → 老实现 0 mismatch,
    ens_review 的真实偏差被完全掩盖。
    """
    r = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("max"), _l4card_row("max")],
                           date="2026-08-06", census={"ens_review": 1})
    assert not r["ok"]
    bad = [m for m in r["mismatches"] if m["field"] == "effort"]
    assert len(bad) == 1, f"应恰好 1 条 effort mismatch,实际 {r['mismatches']}"
    assert bad[0]["role"] == "ens_review", "必须归到 ens_review 这一行,不能算在 l4_card 头上"
    assert bad[0]["expected"] == "medium" and bad[0]["actual"] == "max"


def test_old_behaviour_would_have_been_silent_here_mutation_probe(tmp_path):
    """变异探针:把归因**关掉**(census 缺)→ 上一条测试的 mismatch 必须消失或变形。

    这条是上一条的对手方:如果把 T34 的修复反向(不传 census),同一份输入下
    `role == "ens_review"` 的精确定位就没了。它不红,说明上一条根本没在测归因。
    """
    r = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("max"), _l4card_row("max")],
                           date="2026-08-06", census={})
    ens_rows = [m for m in r["mismatches"] if m.get("role") == "ens_review"]
    assert not ens_rows, "census 缺时不可能精确定位到 ens_review —— 若这里还能定位,说明归因是假的"


def test_missing_census_falls_back_to_union_set_assertion(tmp_path):
    """[Wave12-T34 兜底] census 缺、但两 role 期望不同 → 退回并集集合断言(role=None)。

    合规行(max ∈ 并集、medium ∈ 并集)一律放过——**不再**拿主 role 的期望冤枉次级 role;
    真正落在并集外的(low)才报,且如实标 `role=None`(分不清就不装分得清)。
    """
    ok = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("max"), _l4card_row("medium")],
                            date="2026-08-06", census={})
    assert ok["mismatches"] == [], "并集内的行不该报"

    bad = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("low")], date="2026-08-06", census={})
    assert len(bad["mismatches"]) == 1
    assert bad["mismatches"][0]["role"] is None
    assert bad["mismatches"][0]["field"] == "model+effort"


def test_census_shortfall_is_reported_not_swallowed(tmp_path):
    """census 说派了 2 次复核,实测行只有 1 条 → 少的那次必须留痕(不是 0 mismatch)。"""
    r = ur._reconcile_core(ECHO_SPLIT, [_l4card_row("medium")],
                           date="2026-08-06", census={"ens_review": 2})
    short = [m for m in r["mismatches"] if m["field"] == "dispatch_count"]
    assert len(short) == 1 and short[0]["role"] == "ens_review"


def test_l3_repair_split_from_l3_rank(tmp_path):
    """同族第二例:`l3_repair` 复用 `agentType: 'l3-rank'`,census 给 1 次 → 分行报。"""
    echo = {"agents": {"l3_rank": {"effort": "max"}, "l3_repair": {"effort": "medium"}}}

    def _rows(*efforts):
        return [{"role": "subagent", "agent": "l3-rank", "model": "claude-opus-5",
                 "effort": e, "status": "SUCCEEDED"} for e in efforts]

    # 各按各的档跑 → 干净(老实现会把 medium 那行当成 l3_rank 违规)
    clean = ur._reconcile_core(echo, _rows("max", "medium"), date="2026-08-06",
                               census={"l3_repair": 1})
    assert clean["mismatches"] == []

    # 自修没按 medium 跑(两行都 max)→ 恰好 1 条,归 l3_repair(老实现 0 条,假阴)
    bad = ur._reconcile_core(echo, _rows("max", "max"), date="2026-08-06",
                             census={"l3_repair": 1})
    hits = [m for m in bad["mismatches"] if m["field"] == "effort"]
    assert len(hits) == 1 and hits[0]["role"] == "l3_repair" and hits[0]["actual"] == "max"


def test_unconfigured_secondary_role_does_not_blunt_primary_detection(tmp_path):
    """回归锁:次级 role **没被配置**时不得参与并集 —— 否则父 role 的 mismatch 会被放过。

    实测教训(本 task 首版):`live` 门没加时,`ECHO`(只配 l4_card)下 ens_review 拿
    frontmatter 的 xhigh 撑起并集,`test_effort_mutation_caught` 等 4 条既有探针集体失明。
    """
    r = ur._reconcile_core({"agents": {"l4_card": {"effort": "max"}}},
                           [_l4card_row("low")], date="2026-08-06", census={})
    bad = [m for m in r["mismatches"] if m["field"] == "effort"]
    assert len(bad) == 1 and bad[0]["role"] == "l4_card" and bad[0]["actual"] == "low"


def test_same_effort_config_still_clean_parity(tmp_path):
    """parity:生产当前 `ens_review.effort == l4_card.effort == max` → 有无 census 都干净。

    T34 不该改变"今天这份配置"的对账结论 —— 它只改变"分开调参之后"的结论。
    """
    echo = {"agents": {"l4_card": {"effort": "max"}, "ens_review": {"effort": "max"}}}
    rows = [_l4card_row("max"), _l4card_row("max")]
    assert ur._reconcile_core(echo, rows, date="2026-08-06", census={"ens_review": 1})["mismatches"] == []
    assert ur._reconcile_core(echo, rows, date="2026-08-06", census={})["mismatches"] == []


# ───────────────────────── dispatch_census:产物普查 ─────────────────────────


def test_l4_stock_workflow_writes_ens_review_dispatch_subrecord():
    """生产者接线锁(FN-1 家族):`dispatch_census` 读的 `role`/`n_dispatch` 必须真的有人写。

    普查器写得再好,`l4-stock.js` 不往 `_ensemble_<code>.json` 里写这两个键,ens_review
    就永远退回并集兜底 —— 「消费者读没人生产的产物」正是本仓 FN-1 的原形。
    另锁 `n_dispatch` 取的是**派发数**(sameTier ? 1 : 2)而不是成功数:失败的复核 run
    一样烧 token、一样在 harvest 留一行,数成功数会让 census 系统性少数。
    """
    src = (Path(__file__).resolve().parents[2]
           / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    assert "role: 'ens_review'" in src, "l4-stock.js 的 ens-dump 未写 role 子记录"
    assert "n_dispatch: ensDispatched" in src, "l4-stock.js 未写 n_dispatch"
    assert "const ensDispatched = sameTier ? 1 : 2" in src, (
        "n_dispatch 必须是派发数(r2 恒派 + 分歧时加派 r3),不是 reruns.length 那种成功数")


def test_scan_market_workflow_documents_l3_repair_census_contract():
    """`l3_repair` 的普查靠 `_l3_repair_prompt.md` 在场 —— 这条隐式契约必须写在改动现场。

    契约没写在代码旁边,下一个改 repair-pack 的人不会知道自己动的是归因的地基。
    """
    src = (Path(__file__).resolve().parents[2]
           / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    assert "dispatch_census" in src and "_l3_repair_prompt.md" in src


def test_dispatch_census_reads_explicit_ens_record(tmp_path):
    (tmp_path / "_ensemble_000651.json").write_text(json.dumps(
        {"code": "000651", "n_runs": 3, "role": "ens_review", "n_dispatch": 2}))
    assert ur.dispatch_census(tmp_path) == {"ens_review": 2}


def test_dispatch_census_infers_from_n_runs_for_legacy_records(tmp_path):
    """老产物没有 role/n_dispatch → 由 `n_runs - 1` 推断(主卡 1 次 + 复核 n-1 次)。"""
    (tmp_path / "_ensemble_000651.json").write_text(json.dumps({"code": "000651", "n_runs": 2}))
    (tmp_path / "_ensemble_600000.json").write_text(json.dumps({"code": "600000", "n_runs": 3}))
    assert ur.dispatch_census(tmp_path) == {"ens_review": 3}


def test_dispatch_census_counts_l3_repair_from_prompt_artifact(tmp_path):
    """`l3_repair` 靠 repair prompt 判定 —— **agent 死掉也数得到**(07-27 教训)。

    ⚠️ 修复轮 1(M1)收紧了判据:原版写个 `"x"` 就算一次派发,而 `build_repair_pack`
    是**无条件**写这个文件的(`l3/validation.py:291`),空 codes 的陈旧 prompt 会被记成
    派发 → 假 mismatch → `ok=false`。现在要求 prompt 真的列出 ≥1 个 code。
    本条保留原意(不读 agent 自己写的 patch,所以 agent 死了照样数得到),只是喂一份
    **真实形状**的 prompt。
    """
    assert "l3_repair" not in ur.dispatch_census(tmp_path)
    (tmp_path / "_l3_repair_prompt.md").write_text(
        '# L3 thesis 局部修复包\n```json\n{"codes": ["000651"], "rows": []}\n```\n',
        encoding="utf-8")
    assert ur.dispatch_census(tmp_path)["l3_repair"] == 1
    # agent 没写 patch(死了)也照样算数 —— 这才是本条存在的理由
    assert not (tmp_path / "_l3_repair_patch.json").exists()


def test_dispatch_census_survives_corrupt_artifacts(tmp_path):
    """坏产物不抛异常(本模块是报表,不毙人)。"""
    (tmp_path / "_ensemble_bad.json").write_text("{not json")
    (tmp_path / "_ensemble_list.json").write_text("[1,2,3]")
    assert ur.dispatch_census(tmp_path) == {}
    assert ur.dispatch_census(tmp_path / "does-not-exist") == {}


def test_reconcile_wires_census_from_scan_dir(tmp_path):
    """接线验收:`reconcile()` 必须真的把 census 读进来(不是只有 `_reconcile_core` 支持)。

    ⚠️ 本测试的**第一版没有鉴别力**(实测):它用「两行分别是 max/medium」断言
    `mismatches == []` —— 可那两行在**无 census** 的并集兜底下同样全过,把 `reconcile()`
    里的 census 实参删掉,测试照样绿。这正是 FN-1 家族(生产者没接线)最爱藏身的形状,
    也是"绿灯不等于有灯"的教科书例子。

    改用**只有接了线才会出现**的读数:两行都是 max,census 说其中一次是 ens_review
    (期望 medium)→ 接了线 = 1 条 role=ens_review 的 mismatch;没接线 = 走并集,
    (opus,max) 落在 l4_card 的期望里 → 0 条。两种结局不同,测试才真的在测接线。
    """
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(ECHO_SPLIT))
    (d / "_token_usage.json").write_text(json.dumps(
        {"rows": [_l4card_row("max"), _l4card_row("max")]}))
    (d / "_ensemble_000651.json").write_text(json.dumps(
        {"code": "000651", "n_runs": 2, "role": "ens_review", "n_dispatch": 1}))
    r = ur.reconcile("2026-08-06", root=tmp_path)
    hits = [m for m in r["mismatches"] if m.get("role") == "ens_review"]
    assert len(hits) == 1, f"reconcile() 没把 dispatch_census 接进来:{r['mismatches']}"
    assert hits[0]["expected"] == "medium" and hits[0]["actual"] == "max"


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


# ═══════════ I-1(2026-08-10 终审必修):limit-killed 行不得被判成 model/effort mismatch ═══════════
#
# 背景:C4-1(d4f91e0)给 usage_harvest 加了 meta.json agentType 兜底,把 limit-killed
# transcript 的 `agent` 从 "(未标注)" 改成真实 agentType(如 l4-card/general-purpose)。
# 但这类行的 `model`/`effort` 两个字段本来就是 "—"(transcript 死得太早,从没等到一条带
# usage 的消息,连"发出的请求带什么参数"都没记下)。真实 agentType 一旦落地,这些行就不再
# 走 unknown_agent_types 分支,而是流进 `by_type`/`gp_allowed` 判定——判的不是"配置有没有
# 生效",而是"这份 transcript 死没死"。2026-08-07 真实数据(39 行,31 general-purpose +
# 8 l4-card)证实:不加这道口子会凭空造出 47 条假 mismatch(见 final-review.md I-1)。


def test_unmeasured_l4_card_row_produces_no_mismatch(tmp_path):
    """评审指定探针:喂一行 (agent='l4-card', model='—', effort='—') 不得产生 mismatch。"""
    rows = [{"role": "subagent", "agent": "l4-card", "model": "—", "effort": "—",
            "status": "FAILED"}] + ROWS[1:]
    r = _run(tmp_path, ECHO, rows)
    assert not any(m["agent"] == "l4-card" for m in r["mismatches"])
    assert r["unmeasured"] == 1


def test_unmeasured_general_purpose_row_produces_no_mismatch(tmp_path):
    """真实数据里占多数的那一类(31/39)——壳类兜底也不能被这些死行误判。"""
    rows = ROWS + [{"role": "subagent", "agent": "general-purpose", "model": "—",
                    "effort": "—", "status": "FAILED"}]
    r = _run(tmp_path, ECHO, rows)
    assert r["mismatches"] == []
    assert r["unmeasured"] == 1


def test_unmeasured_row_not_silently_dropped(tmp_path):
    """铁律「降级不留痕才是真病」:这类行必须在返回体里可数,不能只是从 mismatches 里消失。"""
    rows = [{"role": "subagent", "agent": "l4-card", "model": "—", "effort": "—",
            "status": "FAILED"},
            {"role": "subagent", "agent": "general-purpose", "model": None, "effort": "",
             "status": "FAILED"}] + ROWS[1:]
    r = _run(tmp_path, ECHO, rows)
    assert r["unmeasured"] == 2
    assert r["checked"] == len(rows)          # 仍然是"被看过"的行,只是判不了


def test_partially_unmeasured_row_still_judged(tmp_path):
    """精度边界:只有 model **和** effort 都是 unmeasured 才豁免——只缺一个字段的行仍是
    真实信号(比如 effort 请求参数确实没打上),不能被这个兜底连带放过。
    """
    rows = [{"role": "subagent", "agent": "l4-card", "model": "claude-opus-5",
            "effort": "—", "status": "SUCCEEDED"}] + ROWS[1:]
    r = _run(tmp_path, ECHO, rows)
    hit = [m for m in r["mismatches"] if m["agent"] == "l4-card" and m["field"] == "effort"]
    assert hit, "只缺 effort 一个字段的行被误当成 unmeasured 放过了"
    assert r["unmeasured"] == 0


def test_unmeasured_row_still_counts_as_seen_for_wire_break(tmp_path):
    """即使当日唯一一行是 unmeasured,也不该被判"这个 role 今天没人接线"——agent 确实
    跑过,只是没能留下 model/effort 参数,这两件事不能混为一谈。
    """
    rows = [{"role": "subagent", "agent": "l4-card", "model": "—", "effort": "—",
            "status": "FAILED"}]
    r = _run(tmp_path, ECHO, rows)
    assert "l4_card" not in r["wire_breaks"]


def test_unmeasured_only_run_is_still_ok(tmp_path):
    """全天只跑出限速夭折的行,`ok` 不该翻假——unmeasured 是"记账"信号,不是失败信号
    (与 wire_break/unknown_agent_type/missing_resolved_role 那几个真失败信号不同)。
    """
    d = tmp_path / "context/scan/2026-08-06"
    d.mkdir(parents=True)
    echo = {"agents": {"l4_card": {"effort": "max"}}}
    rows = [{"role": "subagent", "agent": "l4-card", "model": "—", "effort": "—",
            "status": "FAILED"}]
    (d / "user_config_echo.json").write_text(json.dumps(echo))
    (d / "_token_usage.json").write_text(json.dumps({"rows": rows}))
    r = ur.reconcile("2026-08-06", root=tmp_path)
    assert r["ok"] is True
    assert r["unmeasured"] == 1


def test_render_shows_unmeasured_count(tmp_path):
    """render() 也要把它摆出来——不是只有 JSON 里有,人读的 markdown 里看不见同样是白记账。"""
    rows = [{"role": "subagent", "agent": "l4-card", "model": "—", "effort": "—",
            "status": "FAILED"}] + ROWS[1:]
    r = _run(tmp_path, ECHO, rows)
    md = ur.render(r)
    assert "unmeasured 1 行" in md


_REAL_0807_DIR = Path("context/scan/2026-08-07")
_real_0807_present = (_REAL_0807_DIR / "user_config_echo.json").exists() and \
    (_REAL_0807_DIR / "_token_usage.json").exists()


@pytest.mark.skipif(not _real_0807_present, reason="真实 2026-08-07 产物不在场(gitignored,新 checkout 正常缺失)")
def test_real_20260807_data_has_no_unknown_type_from_dash_rows():
    """对真实事故日数据的轻量回归:39 行 limit-killed(model=effort='—')此前会被判成
    `unknown_agent_types` 里的 `(未标注)`(修复前的磁盘快照就是这个状态,见 final-review.md
    I-1 引用的 BEFORE 读数)——现在这类行在 model/effort 都缺失时直接被 unmeasured 分支
    截住,不再流到 unknown_types 判定,不依赖 `agent` 字段究竟写的是"(未标注)"还是真实
    agentType(两者本条测试跑的磁盘现状都覆盖到)。
    """
    echo = json.loads((_REAL_0807_DIR / "user_config_echo.json").read_text())
    rows = json.loads((_REAL_0807_DIR / "_token_usage.json").read_text())["rows"]
    dash_rows = [r for r in rows if r.get("model") == "—" and r.get("effort") == "—"]
    assert len(dash_rows) >= 1, "真实数据的前提(limit-killed 行)不在场,本测试没测到东西"
    result = ur._reconcile_core(echo, rows, date="2026-08-07")
    assert "(未标注)" not in (result.get("unknown_agent_types") or [])
    assert result["unmeasured"] >= len(dash_rows)


# ═══════════ Wave12-T33:对 resolved 对账 + 缺派发 role 直接 ok=false ═══════════

_RESOLVED = {"l4_card": {"effort": "max"}, "l4_intel": {"effort": "max"},
             "l3_rank": {"effort": "max"}, "strategist": {"effort": "high"},
             "sector_brief": {"effort": "high"},
             "gp_shell": {"model": "sonnet", "effort": "low"},
             "gp_shell_json": {"model": "sonnet", "effort": "low"}}


def test_resolved_is_preferred_over_raw_agents_block():
    """resolved 在场时,期望取 resolved —— 它才是 workflow 真正吃的那一份。

    造一个"raw agents 说 low、resolved 说 max"的分歧局面:实测 max 时必须判过
    (跟着 resolved 走),否则说明还在读 raw。
    """
    echo = {"agents": {"l4_card": {"effort": "low"}},
            "resolved_agents": {"l4_card": {"effort": "max"}}}
    r = ur._reconcile_core(echo, [_l4card_row("max")], date="2026-08-09")
    assert [m for m in r["mismatches"] if m["field"] == "effort"] == []


def test_missing_resolved_role_forces_ok_false():
    """今天真派过这个 role,resolved 里却没有它 → ok=false(不能被 0 mismatch 冲平)。"""
    # 只留 l4_card:其余 `_EXPECT_PRESENT` role 若也在 resolved 里、当日又无实测行,
    # 会额外触发 wire_breaks,把本条要看的信号混进去。
    resolved = {"l4_card": {"effort": "max"}}
    rows = [_l4card_row("max"),
            {"role": "subagent", "agent": "l4-intel", "model": "claude-sonnet-5",
             "effort": "max", "status": "SUCCEEDED"}]
    r = ur._reconcile_core({"resolved_agents": resolved}, rows, date="2026-08-09")
    assert r["mismatches"] == [] and not r["wire_breaks"]
    assert r["missing_resolved_roles"] == ["l4_intel"]
    assert r["ok"] is False, "0 mismatch 也不该算过 —— 有一整个 role 无从对账"


def test_missing_resolved_role_covers_shell_roles():
    """`general-purpose` 分不清是哪个壳 → 两个壳 role 都必须在 resolved 里。"""
    resolved = {"gp_shell": {"model": "sonnet", "effort": "low"}}
    rows = [{"role": "subagent", "agent": "general-purpose", "model": "claude-sonnet-5",
             "effort": "low", "status": "SUCCEEDED"}]
    r = ur._reconcile_core({"resolved_agents": resolved}, rows, date="2026-08-09")
    assert r["missing_resolved_roles"] == ["gp_shell_json"] and r["ok"] is False


def test_no_resolved_artifact_does_not_report_missing_roles():
    """presence-gated:压根没有 resolved(老 run)→ 不报 missing,那是"还没上线"不是"漏了"。"""
    r = ur._reconcile_core(ECHO, ROWS, date="2026-08-06")
    assert r["missing_resolved_roles"] == []
    assert r["ok"] is True


def test_reconcile_reads_resolved_artifact_file(tmp_path):
    """接线锁:`reconcile()` 必须读 `_resolved_agent_config.json`,不是只认 echo 里的键。

    鉴别力检查:echo 的 raw agents 故意写 low、resolved 文件写 max,实测 max ——
    读了文件 = 0 mismatch;没读 = 报一条 l4_card effort mismatch。
    """
    d = tmp_path / "context/scan/2026-08-09"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps({"agents": {"l4_card": {"effort": "low"}}}))
    (d / "_token_usage.json").write_text(json.dumps({"rows": [_l4card_row("max")]}))
    (d / "_resolved_agent_config.json").write_text(json.dumps(
        {"schema_version": 1, "date": "2026-08-09", "roles": _RESOLVED}))
    r = ur.reconcile("2026-08-09", root=tmp_path)
    assert [m for m in r["mismatches"] if m["field"] == "effort"] == [], (
        f"reconcile() 没读 _resolved_agent_config.json:{r['mismatches']}")


def test_render_lists_missing_resolved_roles():
    r = ur._reconcile_core({"resolved_agents": {"l4_card": {"effort": "max"}}},
                           [_l4card_row("max"),
                            {"role": "subagent", "agent": "l4-intel",
                             "model": "claude-sonnet-5", "effort": "max"}],
                           date="2026-08-09")
    md = ur.render(r)
    assert "missing_resolved_roles" in md and "l4_intel" in md


# ── 修复轮 1(M1):l3_repair 普查判据收紧 —— prompt 在场 ≠ 派过 ──


def test_l3_repair_counted_only_when_prompt_lists_codes(tmp_path):
    """`build_repair_pack` 无条件写 prompt(`l3/validation.py:291`),但只有
    `repair.n > 0` 才真派 agent。空 codes 的陈旧 prompt 不得记成一次派发。
    """
    (tmp_path / "_l3_repair_prompt.md").write_text(
        '# L3 thesis 局部修复包\n```json\n{"codes": [], "rows": []}\n```\n', encoding="utf-8")
    assert "l3_repair" not in ur.dispatch_census(tmp_path)

    (tmp_path / "_l3_repair_prompt.md").write_text(
        '# L3 thesis 局部修复包\n```json\n{"codes": ["000651", "600000"], "rows": []}\n```\n',
        encoding="utf-8")
    assert ur.dispatch_census(tmp_path)["l3_repair"] == 1


def test_stale_empty_prompt_does_not_manufacture_a_false_mismatch(tmp_path):
    """M1 的**后果**面:陈旧空 prompt 若被记成派发,会从 l3-rank 行里抢一行按
    l3_repair 的档位判 → 假 mismatch → `ok=false`,而 `usage_reconcile_lint` 对
    连续两日 ok=false 升 fail。一个会自己制造报警的探针,比没有探针更糟。
    """
    d = tmp_path / "context/scan/2026-08-09"
    d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(
        {"agents": {"l3_rank": {"effort": "max"}, "l3_repair": {"effort": "medium"}}}))
    (d / "_token_usage.json").write_text(json.dumps({"rows": [
        {"role": "subagent", "agent": "l3-rank", "model": "claude-opus-5",
         "effort": "max", "status": "SUCCEEDED"}]}))
    (d / "_l3_repair_prompt.md").write_text(
        '```json\n{"codes": [], "rows": []}\n```\n', encoding="utf-8")

    r = ur.reconcile("2026-08-09", root=tmp_path)
    assert [m for m in r["mismatches"] if m.get("role") == "l3_repair"] == [], (
        "空 codes 的陈旧 prompt 制造了假 mismatch")


def test_l3_repair_census_survives_unreadable_prompt(tmp_path):
    (tmp_path / "_l3_repair_prompt.md").write_text("没有 json 块", encoding="utf-8")
    assert "l3_repair" not in ur.dispatch_census(tmp_path)


def test_real_repo_repair_prompts_all_list_codes():
    """活体对照:现存 8 个 `_l3_repair_prompt.md` 全都列了 codes(即当日确实派过)。

    收紧判据**不该**改变历史读数 —— 这条证明它没有(造 fixture 能红不代表生产没被误伤)。
    """
    from pathlib import Path as _P
    prompts = sorted(_P("context/scan").glob("*/_l3_repair_prompt.md"))
    if not prompts:
        pytest.skip("本机无历史 scan 目录")
    for p in prompts:
        assert ur._l3_repair_dispatched(p.parent), f"{p} 被新判据误判成'没派过'"
