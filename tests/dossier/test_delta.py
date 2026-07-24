"""delta.py 契约:节切片/幂等追加/机算刷新/frontmatter 回写(Wave3 Task 1)。"""
from autoresearch.dossier import builder, delta, schema


def _mk_dossier(code="300857", today="2026-07-23", initiated=True):
    """真 builder 骨架 + 手工置 initiated(模拟已首覆档案)。"""
    out = builder.build_skeleton(code, today, name="协创数据", sector="消费电子")
    p = out["path"]
    if initiated:
        text = delta.set_frontmatter_key(p.read_text(encoding="utf-8"),
                                         "initiated", today)
        p.write_text(text, encoding="utf-8")
    return p


def test_section_span_and_replace_roundtrip():
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    body = delta.section_body(text, 7)
    assert "建档" in body
    new = delta.replace_section(text, 7, "- X\n")
    assert delta.section_body(new, 7) == "- X\n"
    for s in schema.SECTIONS:            # 八节锚一个不丢
        assert s in new


def test_append_delta_line_idempotent_and_rolling():
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    t1 = delta.append_delta_line(text, "2026-07-24", "入围:评级 Hold(conv 60)", key="入围")
    t2 = delta.append_delta_line(t1, "2026-07-24", "入围:评级 Underweight(conv 55)", key="入围")
    body = delta.section_body(t2, 7)
    assert body.count("- 2026-07-24 入围") == 1          # 同日同 key 整行替换,不重复
    assert "Underweight" in body and "Hold(conv 60)" not in body
    for i in range(30):                                   # 滚动窗:只留近 20 条
        t2 = delta.append_delta_line(t2, f"2026-08-{i + 1:02d}", "入围:评级 Hold", key="入围")
    assert len([ln for ln in delta.section_body(t2, 7).splitlines() if ln.strip()]) == 20


def test_record_scan_delta_full_pipeline(monkeypatch):
    p = _mk_dossier()
    monkeypatch.setattr(builder, "_load_prefetch", lambda c: {
        "val_band": {"pe_p25": 10.0, "pe_p50": 20.0, "pe_p75": 30.0, "pe_now": 25.0},
        "fwd_eps": {"asof": "2026-07-24", "fwd_eps_2026": 5.0}})
    res = delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=60)
    assert res["updated"] and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    assert "- 2026-07-24 入围:评级 Hold(conv 60)" in delta.section_body(text, 7)
    assert "P50~P75" in delta.section_body(text, 2)       # §3 由 prefetch 重算
    assert "- 快照 2026-07-24:一致预期 fwd-EPS:2026=5.00" in delta.section_body(text, 1)
    assert schema.parse_frontmatter(text)["last_delta"] == "2026-07-24"
    assert "- 带位: 当前 PE=25.0" in text                  # 摘要机算行同步
    # 幂等:同日重跑不膨胀
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=60)
    t3 = p.read_text(encoding="utf-8")
    assert t3.count("- 快照 2026-07-24") == 1
    assert delta.section_body(t3, 7).count("- 2026-07-24 入围") == 1


def test_record_scan_delta_presence_gated():
    assert delta.record_scan_delta("999999", "2026-07-24", rating="Hold")["skipped"] == "no_dossier"
    _mk_dossier(code="600000", initiated=False)           # 骨架未首覆
    assert delta.record_scan_delta("600000", "2026-07-24",
                                   rating="Hold")["skipped"] == "not_initiated"


def test_nan_conviction_not_rendered():
    p = _mk_dossier()
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=float("nan"))
    assert "nan" not in delta.section_body(p.read_text(encoding="utf-8"), 7)


def test_record_scan_delta_band_summary_guarded_when_prefetch_degrades(monkeypatch):
    """review I-1:摘要「带位:」刷新须与 §3(_refresh_band)同守卫 —— val_band 缺时跳过,
    不用「数据缺(待预取)」覆盖摘要里原本的好值(与 §3 对称,要么都更新要么都保留)。"""
    p = _mk_dossier()
    monkeypatch.setattr(builder, "_load_prefetch", lambda c: {
        "val_band": {"pe_p25": 10.0, "pe_p50": 20.0, "pe_p75": 30.0, "pe_now": 25.0}})
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=60)
    text = p.read_text(encoding="utf-8")
    assert "- 带位: 当前 PE=25.0" in text
    assert "P50~P75" in delta.section_body(text, 2)

    monkeypatch.setattr(builder, "_load_prefetch", lambda c: {"val_band": None})
    delta.record_scan_delta("300857", "2026-07-25", rating="Hold", conviction=55)
    text2 = p.read_text(encoding="utf-8")
    assert "- 带位: 当前 PE=25.0" in text2            # 摘要仍是第一次的好值
    assert "数据缺" not in delta.section_body(text2, 2)
    assert "P50~P75" in delta.section_body(text2, 2)  # §3 也仍是好值(_refresh_band 自身守卫)


def test_append_delta_line_key_prefix_no_collision():
    """review M-1:同日不同 key 若共享前缀(入围/入围候补),不应互相误删。"""
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    t1 = delta.append_delta_line(text, "2026-07-24", "入围候补:关注", key="入围候补")
    t2 = delta.append_delta_line(t1, "2026-07-24", "入围:评级 Hold", key="入围")
    body = delta.section_body(t2, 7)
    assert "- 2026-07-24 入围候补:关注" in body
    assert "- 2026-07-24 入围:评级 Hold" in body
    # 幂等:再写一次「入围候补」只替换自己那一行,不动「入围」
    t3 = delta.append_delta_line(t2, "2026-07-24", "入围候补:持续关注", key="入围候补")
    body3 = delta.section_body(t3, 7)
    assert body3.count("- 2026-07-24 入围候补") == 1
    assert "持续关注" in body3 and "关注" in body3
    assert "- 2026-07-24 入围:评级 Hold" in body3


def test_refresh_summary_line_preserves_blank_line_and_trailing_newline():
    """review M-2/M-3:块切片须保留摘要块与下一节间的空行、以及摘要为文件尾块时的结尾换行。"""
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    new = delta.refresh_summary_line(text, "判例:", "近 10 扫描日入围 3 次")
    assert "\n\n## 1. 业务模型" in new              # 摘要块与 §1 之间空行保留
    assert "- 判例: 近 10 扫描日入围 3 次" in new

    # 摘要为文件尾块(退化输入,§8 之外的边界情形)
    tail_text = schema.SUMMARY_HEAD + "\n- 判例: OLD\n"
    refreshed = delta.refresh_summary_line(tail_text, "判例:", "NEW")
    assert refreshed == schema.SUMMARY_HEAD + "\n- 判例: NEW\n"
    assert refreshed.endswith("\n")                  # 文件仍以 \n 收尾


def test_delta_refreshes_section7_with_track_block(tmp_path, monkeypatch):
    import json

    from autoresearch.dossier import delta, ledger
    p = _mk_dossier()
    lp = tmp_path / "t1.jsonl"
    lp.write_text(json.dumps({"t": "2026-07-14", "code": "300857", "rating": "Underweight",
                              "verdict": "准", "excess_ind": -0.03, "sealed": False},
                             ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(ledger, "_T1_LEDGER", lp)
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold")
    text = p.read_text(encoding="utf-8")
    assert "t1 快环战绩" in delta.section_body(text, 6)   # §7 尾战绩块
    assert "t1 方向 1 笔 准1/不准0" in text               # 摘要判例行升级


def test_record_scan_deltas_batch(tmp_path, monkeypatch):
    import json

    from autoresearch.dossier import delta
    p = _mk_dossier()                                     # 300857 已首覆
    _mk_dossier(code="600000", initiated=False)           # 骨架票:应 skip
    sd = tmp_path / "2026-07-24"
    sd.mkdir()
    (sd / "finalists.csv").write_text(
        "code,name,conviction\n300857,协创数据,58\n600000,浦发银行,50\n000001,平安银行,60\n",
        encoding="utf-8")
    (sd / "_final_ratings.json").write_text(
        json.dumps({"300857": "Underweight", "600000": "Hold"}), encoding="utf-8")
    res = delta.record_scan_deltas(sd, "2026-07-24")
    # 返回 dict(I-4):int 版本会把 record_scan_delta 的 lint issues 静默吞掉
    assert res["updated"] == 1                            # 只有已首覆的 300857 落 δ
    assert res["issues"] == {}                            # 健康档案 = 无 issues
    body = delta.section_body(p.read_text(encoding="utf-8"), 7)
    assert "- 2026-07-24 入围:评级 Underweight(conv 58)" in body


def test_record_scan_deltas_missing_inputs(tmp_path):
    from autoresearch.dossier import delta
    assert delta.record_scan_deltas(tmp_path / "nope", "2026-07-24") == {
        "updated": 0, "issues": {}, "sections_skipped": {}}   # 无 finalists
    sd = tmp_path / "d"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name\n300857,协创数据\n", encoding="utf-8")
    assert delta.record_scan_deltas(sd, "2026-07-24") == {
        "updated": 0, "issues": {}, "sections_skipped": {}}   # 无 _final_ratings.json → 不记


def test_record_scan_deltas_surfaces_lint_issues(tmp_path):
    """写坏的档案:issues 必须回传给调用方(I-4「降级留痕…不空写不吞」)。

    坏法用真实故障形态:摘要被写爆 3k 帽 → `injectable_summary` 从此返回 ""、注入
    无声停摆,而 lint 门与注入门同源 —— 旧版 int 返回值下**连假警都不会有**。
    """
    import json

    from autoresearch.dossier import delta, schema
    p = _mk_dossier(code="300857")
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace(schema.SUMMARY_HEAD + "\n",
                              schema.SUMMARY_HEAD + "\n- 溢出: " + "填" * 5000 + "\n", 1),
                 encoding="utf-8")
    sd = tmp_path / "2026-07-24"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name,conviction\n300857,协创数据,58\n",
                                      encoding="utf-8")
    (sd / "_final_ratings.json").write_text(json.dumps({"300857": "Hold"}), encoding="utf-8")
    res = delta.record_scan_deltas(sd, "2026-07-24")
    assert res["updated"] == 1
    assert "300857" in res["issues"]
    assert any("summary>cap" in s for s in res["issues"]["300857"])
    assert schema.injectable_summary("300857") == ""      # 注入确实停摆 = 该警必须可见


def _mk_staging(root, day, code="300857", *, pledge=True, calendar=True):
    """造一天的 staging:pledge.csv(§4 料)+ calendar.csv(§6 料)。

    calendar.csv 列名按真实契约 `autoresearch.scan.calendar._CAL_COLS`
    (code,kind,event_date,detail,ratio;非泛化的 ann_date/event)——
    `calendar_flags` 按这五列读,列名不对会在 itertuples 属性访问上直接抛
    AttributeError(非降级空,已实测验证),不是"数据缺"的降级路径。
    """
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    if pledge:
        (d / "pledge.csv").write_text(
            f"code,pledge_ratio,end_date\n{code},41.5,2026-07-20\n", encoding="utf-8")
    if calendar:
        (d / "calendar.csv").write_text(
            f"code,kind,event_date,detail,ratio\n{code},disclosure,20260828,中报预约披露,\n",
            encoding="utf-8")
    return d


def test_delta_refreshes_section4_6_from_today_staging(tmp_path):
    """review I-1 点 3:旧版只断言 §4,循环删到只剩 §4 也会全绿——这里补 §6 断言。

    §6 用 `"中报预约披露"`(fixture 独有措辞)而非只查日期 `"20260828"`:真实仓库
    `context/scan/2026-07-21/calendar.csv` 恰好也有 300857 的 20260828 披露事件
    (措辞不同,"预约披露(期 20260630)"),只查日期数字撞上真实数据也会假绿。
    """
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-24")
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    text = p.read_text(encoding="utf-8")
    assert "41.5" in delta.section_body(text, 3)          # §4 拿到当日质押率
    assert "2026-07-24" in delta.section_body(text, 3)     # 标注素材来自哪个扫描日
    assert "中报预约披露" in delta.section_body(text, 5)    # §6 也真被刷新(非旧真值残留)


def test_delta_section4_6_missing_material_keeps_old(tmp_path):
    """对称守卫:当日无 staging → 保留旧 §4/§6,不得写成 [数据缺,…]。"""
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-24")
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    before4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in before4
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    delta.record_scan_delta("300857", "2026-07-25", rating="Hold", scan_root=empty_root)
    after4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert after4 == before4                               # 旧真值原样保留
    assert "数据缺" not in after4


def test_delta_prefers_today_staging_over_latest(tmp_path):
    """当日目录存在即用当日,不回退到"最近有素材的日"(防拿旧快照冒充今天)。

    review I-1 点 1:原版 δ 日恰好就是 root 里最新的日期,naive 实现
    (`builder._latest_staging_dir`,只看"最近有素材的日")与正确实现
    (`_staging_dir_for`,当日优先)在那个场景下**返回同一个目录**,零鉴别力
    (运行时把 `_staging_dir_for` 换成 `_latest_staging_dir`,原版三测试 657 用例照样全绿)。
    这里反过来:δ 日 = 07-20,root 里存在**更晚**的 07-24 目录(未来素材)——
    naive 会选中 07-24(99.9),正确实现必须选 07-20(41.5)。
    """
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-20")                         # δ 日当日:41.5
    later = _mk_staging(root, "2026-07-24")                 # 更晚的目录(不该被选中)
    (later / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,99.9,2026-07-01\n",
                                      encoding="utf-8")
    delta.record_scan_delta("300857", "2026-07-20", rating="Hold", scan_root=root)
    body4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in body4 and "99.9" not in body4


def test_staging_dir_for_pit_safe_fallback_never_picks_future_dir(tmp_path):
    """review M-1:当日无素材、回退"最近有素材的日"时不得选到**晚于** δ 日的目录
    (PIT 安全;重跑历史日 δ 不该被未来素材污染)。"""
    from autoresearch.dossier.delta import _staging_dir_for
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-21")             # 唯一有素材的目录,晚于下面查询的 δ 日
    assert _staging_dir_for(root, "2026-07-09") is None


def test_delta_guard_executes_when_staging_present_but_no_row_for_code(tmp_path):
    """review I-1 点 2:对称守卫真正被执行的路径必须是 staging **存在**、该票**无行**
    (生产常态——今天没有这只票的质押变化/没上龙虎榜/无日历事件),不是 `empty_root`
    触发的 `_staging_dir_for` 早返回(那条路径下 `body == miss` 那一行从未被执行过)。

    第一天(07-21)用一次真实 δ 调用把 §4/§6 钉成已知值(不依赖 `_mk_dossier()` 建档时
    读到的任何真实仓库残留);第二天(07-25)造一个**存在**、三份 csv 都在、但都不含
    300857 的目录 → 两腿 + §6 应保留第一天的值,且返回值如实记下三项全跳过。
    """
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    d1 = _mk_staging(root, "2026-07-21")
    (d1 / "seats.csv").write_text(
        "code,n_appear,inst_net_wan,retail_net_wan\n300857,1,-50924,-44204\n",
        encoding="utf-8")
    delta.record_scan_delta("300857", "2026-07-21", rating="Hold", scan_root=root)
    text1 = p.read_text(encoding="utf-8")
    before4, before6 = delta.section_body(text1, 3), delta.section_body(text1, 5)
    assert "41.5" in before4 and "龙虎榜席位" in before4 and "-50924" in before4
    assert "中报预约披露" in before6

    d2 = root / "2026-07-25"                   # 存在的目录,三份 csv 都在,但都不含 300857
    d2.mkdir()
    (d2 / "pledge.csv").write_text("code,pledge_ratio,end_date\n999999,10.0,2026-07-20\n",
                                   encoding="utf-8")
    (d2 / "seats.csv").write_text(
        "code,n_appear,inst_net_wan,retail_net_wan\n999999,2,10,20\n", encoding="utf-8")
    (d2 / "calendar.csv").write_text(
        "code,kind,event_date,detail,ratio\n999999,disclosure,20260901,x,\n", encoding="utf-8")
    res = delta.record_scan_delta("300857", "2026-07-25", rating="Hold", scan_root=root)
    text2 = p.read_text(encoding="utf-8")
    after4, after6 = delta.section_body(text2, 3), delta.section_body(text2, 5)
    assert after4 == before4 and "数据缺" not in after4
    assert after6 == before6 and "数据缺" not in after6
    assert res["sections_skipped"] == ["§4.pledge", "§4.seats", "§6"]


def test_delta_section4_leg_guard_keeps_stale_leg_on_partial_refresh(tmp_path):
    """C-1 回归(修 1 的目标场景):旧 §4 质押 + 席位两行俱全,新 staging 只有 pledge
    (seats.csv 在但该票无行——生产常态)→ 席位旧行**原样保留**、质押行已更新为
    新值,不得整节覆盖删掉席位腿的旧真内容(真档案 002371 复现场景的最小化版本)。
    """
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    d1 = root / "2026-07-21"
    d1.mkdir(parents=True)
    (d1 / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,7.3,2026-07-01\n",
                                   encoding="utf-8")
    (d1 / "seats.csv").write_text(
        "code,n_appear,inst_net_wan,retail_net_wan\n300857,1,-50924,-44204\n",
        encoding="utf-8")
    delta.record_scan_delta("300857", "2026-07-21", rating="Hold", scan_root=root)
    old4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "7.3" in old4 and "-50924" in old4 and "龙虎榜席位" in old4

    d2 = root / "2026-07-24"
    d2.mkdir()
    (d2 / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,41.5,2026-07-20\n",
                                   encoding="utf-8")
    (d2 / "seats.csv").write_text(               # 文件在,但没有 300857 这一行
        "code,n_appear,inst_net_wan,retail_net_wan\n999999,2,10,20\n", encoding="utf-8")
    res = delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    new4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in new4 and "7.3" not in new4          # 质押行已更新为新值
    assert "-50924" in new4 and "龙虎榜席位" in new4       # 席位旧行仍在(未被整节覆盖删掉)
    assert "§4.seats" in res["sections_skipped"]          # 席位腿如实记跳(质押腿没跳)
    assert "§4.pledge" not in res["sections_skipped"]


def test_delta_section4_upgrades_from_missing_placeholder(tmp_path):
    """单向守卫不堵升级路:旧 §4 是建档占位 `[数据缺,...]`,新 staging 有真值时必须
    正常整节升级——不是"缺料保旧"被误做成"占位也保、永不升级"。

    用隔离的 `scan_root=tmp_path/"noscan"`(`test_builder.py` 同款惯用法)建档,不用
    默认 `_mk_dossier()`:真实仓库 `context/scan/2026-07-21/pledge.csv` 里 300857
    有真实质押行(7.27%),`_mk_dossier()` 默认建档会读到它,§4 就不是占位了。
    """
    from autoresearch.dossier import builder, delta
    out = builder.build_skeleton("300857", "2026-07-20", name="协创数据",
                                 sector="消费电子", scan_root=tmp_path / "noscan")
    p = out["path"]
    p.write_text(delta.set_frontmatter_key(p.read_text(encoding="utf-8"),
                                           "initiated", "2026-07-20"), encoding="utf-8")
    old4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "数据缺" in old4

    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-24")
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    new4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in new4 and "数据缺" not in new4


def test_record_scan_delta_sections_skipped_observable(tmp_path):
    """review I-2:跳过刷新必须进返回值,不得静默——覆盖"全齐不跳过"与"部分跳过"
    两态,并确认 `record_scan_deltas` 批量层原样透传(不吞)。"""
    import json

    from autoresearch.dossier import delta
    _mk_dossier()
    root = tmp_path / "scan"
    d1 = root / "2026-07-21"
    d1.mkdir(parents=True)
    (d1 / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,7.3,2026-07-01\n",
                                   encoding="utf-8")
    (d1 / "seats.csv").write_text(
        "code,n_appear,inst_net_wan,retail_net_wan\n300857,1,-50924,-44204\n",
        encoding="utf-8")
    (d1 / "calendar.csv").write_text(
        "code,kind,event_date,detail,ratio\n300857,disclosure,20260828,中报预约披露,\n",
        encoding="utf-8")
    res1 = delta.record_scan_delta("300857", "2026-07-21", rating="Hold", scan_root=root)
    assert res1["sections_skipped"] == []                  # 全齐:两腿 + §6 都拿到新素材

    d2 = root / "2026-07-24"
    d2.mkdir()
    (d2 / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,41.5,2026-07-20\n",
                                   encoding="utf-8")        # 只有 pledge,无 seats/calendar
    res2 = delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    assert res2["sections_skipped"] == ["§4.seats", "§6"]

    (d2 / "finalists.csv").write_text("code,name,conviction\n300857,协创数据,58\n",
                                      encoding="utf-8")
    (d2 / "_final_ratings.json").write_text(json.dumps({"300857": "Hold"}), encoding="utf-8")
    batch = delta.record_scan_deltas(d2, "2026-07-24")      # 批量层同款不吞(I-2)
    assert batch["sections_skipped"]["300857"] == ["§4.seats", "§6"]
