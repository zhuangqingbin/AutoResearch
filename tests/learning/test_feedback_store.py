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


# ───────────────────────── C1 修复(final-review 2026-08-08):纯标注不位移 ─────────────────────────
#
# `upsert_lesson()` 的强化路径专为"有新证据被当前尺吸收"设计——每次调用必定
# `ruler=MAIN_RULER` + `confidence+0.05`。Wave12-T10 Step3 误用它给 4 条**内容仍是旧尺
# 读数**的经验打「gap 尺待重验」注解,结果:①`ruler` 被错置成 gap_c1_o2(读者以为证据已被
# 新尺复核过,实际一个字都没变);②注解追加在 evidence 末尾,被 `_lesson_bullet` 的
# `evidence[:2]` 截断吃掉,注入 prompt 的产物里一个字都不出现;③confidence 无端上浮。三者
# 叠加,产物比不打注解更误导——净回归。`mark_ruler_pending_reverify()` 是专用于这种"纯标注,
# 不重新验证"场景的写入路径:只设 `ruler`(调用方给真值,不做任何推断)+ 一条无条件渲染、
# 不受 evidence 截断影响的 `ruler_reverify_note`,其余字段(confidence/reinforce_count/
# last_reinforced/rule/evidence)逐字节不动。


def _seed_lesson(slug: str, *, confidence: float, evidence: list[str], day: str) -> dict:
    """构造一条『多条 evidence + 早于换尺日』的经验(镜像 lessons.jsonl 真实存量条目形状)。"""
    return feedback_store.upsert_lesson(slug, ("global", "*"), "示例旧尺规则", evidence,
                                        confidence=confidence, day=day)


def test_mark_ruler_pending_reverify_sets_ruler_without_side_effects(_tmp_know):
    """只改 ruler + 注解字段;confidence/reinforce_count/last_reinforced/rule/evidence 逐字节不动。"""
    before = _seed_lesson("probe4", confidence=0.69, evidence=["e1", "e2", "e3"], day="2026-07-08")
    rec = feedback_store.mark_ruler_pending_reverify(
        "probe4", "fwd_2_oc", "本条 evidence 全部产自 fwd_2_oc 旧尺,待 gap 尺复算")
    assert rec["ruler"] == "fwd_2_oc"
    assert rec["ruler_reverify_note"] == "本条 evidence 全部产自 fwd_2_oc 旧尺,待 gap 尺复算"
    assert rec["confidence"] == before["confidence"] == 0.69      # 未上浮
    assert rec["reinforce_count"] == before["reinforce_count"]
    assert rec["last_reinforced"] == before["last_reinforced"] == "2026-07-08"
    assert rec["rule"] == before["rule"]
    assert rec["evidence"] == before["evidence"]                 # 未追加新条目


def test_mark_ruler_pending_reverify_missing_lesson_returns_none(_tmp_know):
    """target 不存在 → None,不新建、不报错(待重验是对已有经验的操作,不是写新经验)。"""
    assert feedback_store.mark_ruler_pending_reverify("nope", "fwd_2_oc", "note") is None


def test_lesson_bullet_renders_reverify_note_regardless_of_evidence_truncation():
    """核心断言:注解必须在 `_lesson_bullet` 里无条件出现,不能靠"恰好排进 evidence 前两条"。"""
    lsn = {"scope": {"kind": "global", "value": "*"}, "rule": "示例旧尺规则",
           "evidence": ["e1", "e2", "e3", "e4", "e5"],  # 5 条,注解不在其中、也不在前两条
           "confidence": 0.69, "ruler": "fwd_2_oc",
           "ruler_reverify_note": "本条 evidence 全部产自 fwd_2_oc 旧尺,待 gap 尺复算"}
    bullet = feedback_store._lesson_bullet(lsn)
    assert "本条 evidence 全部产自 fwd_2_oc 旧尺,待 gap 尺复算" in bullet
    assert "〔尺:fwd_2_oc〕" in bullet


def test_lesson_bullet_no_reverify_field_unchanged_from_before_c1():
    """parity:无 `ruler_reverify_note` 字段(绝大多数经验)→ 输出与 C1 修复前逐字一致。"""
    lsn = {"scope": {"kind": "global", "value": "*"}, "rule": "示例规则", "evidence": ["e1"],
           "confidence": 0.6, "ruler": "fwd_2_oc"}
    assert "待重验" not in feedback_store._lesson_bullet(lsn)


def test_render_calibration_block_surfaces_reverify_note_end_to_end(_tmp_know):
    """端到端:mark_ruler_pending_reverify 打的注解真的能到达喂给 L3/L4 的校准块产物。"""
    _seed_lesson("probe5", confidence=0.6, evidence=["e1", "e2", "e3"], day="2026-06-20")
    feedback_store.mark_ruler_pending_reverify("probe5", "fwd_2_oc", "gap 尺待重验标记探针")
    blk = feedback_store.render_calibration_block([("global", "*")])
    assert "gap 尺待重验标记探针" in blk
