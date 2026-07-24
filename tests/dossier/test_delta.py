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
        "updated": 0, "issues": {}}                          # 无 finalists
    sd = tmp_path / "d"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name\n300857,协创数据\n", encoding="utf-8")
    assert delta.record_scan_deltas(sd, "2026-07-24") == {
        "updated": 0, "issues": {}}                          # 无 _final_ratings.json → 不记


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
