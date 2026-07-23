import json

from autoresearch.dossier import builder, schema


def _prefetch_file(tmp_path, code="300857"):
    d = tmp_path / "_prefetch"
    d.mkdir(parents=True)
    (d / f"{code}.json").write_text(json.dumps({
        "code": code, "asof": "2026-07-23",
        "mainbz": [{"period": "20251231", "bz_item": "数据存储设备",
                    "bz_sales": 4.49e9, "bz_profit": 8.0e8}],
        "fwd_eps": {"fwd_eps_2026": 5.0}, "val_band": {"pe_p25": 30, "pe_p50": 45,
                                                        "pe_p75": 70, "pe_now": 59.9},
        "notes": []}), encoding="utf-8")


def test_build_skeleton_full_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "### 📁 个股档案(近 2 次入围)\n- x\n")
    _prefetch_file(tmp_path)
    out = builder.build_skeleton("300857", "2026-07-23", name="协创数据", sector="消费电子",
                                 scan_root=tmp_path / "noscan")
    assert out["created"] is True
    text = (tmp_path / "300857.md").read_text(encoding="utf-8")
    for s in schema.SECTIONS:
        assert s in text
    assert schema.SUMMARY_HEAD in text and "数据存储设备" in text and "<!-- LLM:待首覆 -->" in text
    assert "个股档案" in text                       # §7 前科种子
    # lint:骨架允许叙事锚"(待首覆)"占位 → 六锚字符串在场即可
    assert builder_lint_clean(text)


def builder_lint_clean(text):
    return schema.lint_dossier(text) == []


def test_build_skeleton_idempotent_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "")
    _prefetch_file(tmp_path)
    builder.build_skeleton("300857", "2026-07-23", scan_root=tmp_path / "noscan")
    (tmp_path / "300857.md").write_text("人工改过", encoding="utf-8")
    out = builder.build_skeleton("300857", "2026-07-24", scan_root=tmp_path / "noscan")
    assert out["created"] is False
    assert (tmp_path / "300857.md").read_text(encoding="utf-8") == "人工改过"


def test_build_skeleton_missing_prefetch_leaves_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "")
    out = builder.build_skeleton("600350", "2026-07-23", scan_root=tmp_path / "noscan")
    text = (tmp_path / "600350.md").read_text(encoding="utf-8")
    assert out["created"] and "[数据缺,2026-07-23]" in text


def test_build_skeleton_force_overwrites(tmp_path, monkeypatch):
    """自补:--force 覆盖路径——旧文(哪怕是人工改过的)被重建,新 today 落进 frontmatter/§8。"""
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "")
    _prefetch_file(tmp_path)
    builder.build_skeleton("300857", "2026-07-23", scan_root=tmp_path / "noscan")
    (tmp_path / "300857.md").write_text("人工改过", encoding="utf-8")
    out = builder.build_skeleton("300857", "2026-07-24", scan_root=tmp_path / "noscan", force=True)
    assert out["created"] is True
    text = (tmp_path / "300857.md").read_text(encoding="utf-8")
    assert text != "人工改过"
    assert "2026-07-24" in text            # §8 首行"- 2026-07-24 建档"
    assert schema.lint_dossier(text) == []
