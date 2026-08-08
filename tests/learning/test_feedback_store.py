import pytest

from autoresearch.learning import feedback_store


def test_selftest():
    assert feedback_store._selftest() == 0


# ───────────────────────── Wave12-T10 · lessons ruler 字段 + 渲染标 ─────────────────────────
#
# 经验会注入 L3/L4 prompt,但多条引用旧尺(fwd_2_oc)读数且无 ruler 字段,读者无从分辨数字
# 活在哪把尺下。本节锁两件:①写侧新建/强化经验打当前 MAIN_RULER 真值(镜像 attribute_frame/
# append_ledger 同款 tag);②渲染侧(注入 L3/L4 prompt 的 render_calibration_block 用的
# `_lesson_bullet`)带 `〔尺:<ruler>〕` 标,无字段旧行按写入日期推断(<切换日 → fwd_2_oc)。


@pytest.fixture
def _tmp_know(tmp_path):
    """隔离真实 context/knowledge(镜像 test_lesson_yield.py 同款 fixture,save/restore KNOW)。"""
    old = feedback_store.KNOW
    feedback_store.set_root(tmp_path / "knowledge")
    yield
    feedback_store.set_root(old)


def test_upsert_lesson_stamps_current_ruler(_tmp_know):
    """新建经验时打 MAIN_RULER 真值。"""
    rec = feedback_store.upsert_lesson("probe", ("global", "*"), "测试规则", ["e1"])
    assert rec["ruler"] == feedback_store.MAIN_RULER


def test_upsert_lesson_reinforce_restamps_ruler(_tmp_know):
    """强化(第二次 upsert 同一 slug)也重打当前 ruler——不是只在新建那一刻写一次。"""
    feedback_store.upsert_lesson("probe2", ("global", "*"), "测试规则", ["e1"], day="2026-06-01")
    rec = feedback_store.upsert_lesson("probe2", ("global", "*"), "测试规则", ["e2"], day="2026-08-08")
    assert rec["ruler"] == feedback_store.MAIN_RULER


def test_lesson_ruler_prefers_explicit_field():
    """显式 `ruler` 字段优先于任何日期推断。"""
    lsn = {"ruler": "fwd_2_oc", "last_reinforced": "2026-08-08"}
    assert feedback_store.lesson_ruler(lsn) == "fwd_2_oc"


def test_lesson_ruler_infers_old_ruler_from_pre_switch_date():
    """无 ruler 字段的旧行:写入日期 < 2026-08-05(T16 换尺日)→ 推断为 fwd_2_oc。"""
    assert feedback_store.lesson_ruler({"last_reinforced": "2026-07-24"}) == "fwd_2_oc"
    assert feedback_store.lesson_ruler({"created": "2026-06-25", "last_reinforced": ""}) == "fwd_2_oc"


def test_lesson_ruler_infers_current_ruler_from_post_switch_date():
    """无 ruler 字段但写入日期 ≥ 切换日 → 推断为当前 MAIN_RULER(不是硬编码 gap_c1_o2 字面量,
    随 MAIN_RULER 未来再换尺自动跟随)。"""
    assert feedback_store.lesson_ruler({"last_reinforced": "2026-08-06"}) == feedback_store.MAIN_RULER


def test_lesson_ruler_falls_back_to_old_when_no_date_at_all():
    """连日期字段都没有的行(理论不该发生,防御性兜底)→ 诚实标旧尺,不是抛异常。"""
    assert feedback_store.lesson_ruler({}) == "fwd_2_oc"


def test_lesson_bullet_shows_explicit_ruler_tag():
    lsn = {"scope": {"kind": "global", "value": "*"}, "rule": "示例规则", "evidence": ["e1"],
           "confidence": 0.6, "ruler": "fwd_2_oc"}
    assert "〔尺:fwd_2_oc〕" in feedback_store._lesson_bullet(lsn)


def test_lesson_bullet_infers_tag_when_field_missing():
    lsn = {"scope": {"kind": "global", "value": "*"}, "rule": "示例规则", "evidence": ["e1"],
           "confidence": 0.6, "last_reinforced": "2026-07-01"}
    assert "〔尺:fwd_2_oc〕" in feedback_store._lesson_bullet(lsn)


def test_render_calibration_block_hit_includes_ruler_tag(_tmp_know):
    """端到端:命中经验的校准块(真正喂进 L3/L4 prompt 的产物)里能看到尺标。"""
    feedback_store.upsert_lesson("probe3", ("industry", "电子"), "电子板块过热回避", ["x"],
                                 day="2026-06-20")
    blk = feedback_store.render_calibration_block([("industry", "电子")])
    assert f"〔尺:{feedback_store.MAIN_RULER}〕" in blk
