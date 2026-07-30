"""finalist 无档案插队建档(Wave9 R6,B-3 续)。合成,无网络。

spec: `.superpowers/sdd/2026-07-29-wave9-batch-ab-plan/task-9-brief.md`
"""
import json

from autoresearch.dossier import pool as pool_mod
from autoresearch.scan import post_run


def test_enqueues_finalists_without_dossier(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name\n920179,凯德石英\n000651,格力电器\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text('["000651"]', encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["920179"]
    entry = json.loads(pool.read_text(encoding="utf-8"))["pending_init"][0]
    assert entry["code"] == "920179"
    assert entry["priority"] == "finalist"
    assert entry["last_seen"] == "2026-07-29"


def test_idempotent_refreshes_last_seen(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-30"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": [
        {"code": "920179", "priority": "finalist", "last_seen": "2026-07-29"}]}),
        encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-30")
    assert added == []
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    assert len(entries) == 1 and entries[0]["last_seen"] == "2026-07-30"


def test_missing_dossier_present_skips_enqueue(tmp_path, monkeypatch):
    """`_dossier_present.json` 缺失(T8 新产物,老/坏 scan 目录里本就没有——07-29 真实产物
    验证了这不是假设:`context/scan/2026-07-29/` 下确实没有这个文件)= 无法判定谁已有档案。
    插队只是 advisory 排序优化,过度激进(把当日全部 finalist 都当"无档案"插队)会把整条
    队列的既有优先级搅乱——保守跳过(歧义2 裁决,详见 task-9-report.md)。"""
    d = tmp_path / "2026-07-31"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-31")
    assert added == []
    assert json.loads(pool.read_text(encoding="utf-8"))["pending_init"] == []


def test_corrupt_dossier_present_also_skips(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-31"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("{not json", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    assert post_run.enqueue_finalist_dossiers(d, "2026-07-31") == []


def test_present_json_valid_string_shape_skips_enqueue(tmp_path, monkeypatch):
    """复核 Important-1 独立复现的场景:`_dossier_present.json` 内容是语法完全合法的 JSON
    **字符串**(不是 list)——`set(json.loads(...))` 不会抛异常,而是把字符串按字符拆开,
    产出一堆单字符的"集合",没有任何一个元素等于 6 位 code,于是 `code not in present`
    对所有票恒为 True,静默复活歧义2 明确要避免的激进分支("当日全部当无档案插队")。
    形状不对 = 未知,必须整体跳过,不能只是"运气好没崩溃就继续用"。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name\n000651,格力电器\n601211,国泰海通\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        json.dumps("000651 already has one"), encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    assert post_run.enqueue_finalist_dossiers(d, "2026-07-29") == []


def test_present_json_valid_object_shape_skips_enqueue(tmp_path, monkeypatch):
    """内容是语法合法的 JSON **对象**(dict,不是 list)——`set(json.loads(...))` 按 key 迭代,
    同样不抛异常却语义全错,必须靠显式 `isinstance(..., list)` 挡住。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n601211,国泰海通\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        json.dumps({"000651": True}), encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    assert post_run.enqueue_finalist_dossiers(d, "2026-07-29") == []


def test_present_json_list_with_non_string_elements_filters_them(tmp_path, monkeypatch):
    """list 形状本身是对的,但内部元素类型混杂(int/None)——逐个丢弃,不做整数→code 的猜测
    式强转,也不让这些坏元素拖累其它已确认为字符串的合法条目:`"000651"` 仍然正确地被认定
    "已有档案"(不插队),`999999`/`None` 被静默忽略(既不会凭空让任何票被判定"已有档案",
    也不会导致崩溃),`601211` 因为没有任何合法字符串条目覆盖它而正确插队。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name\n000651,格力电器\n601211,国泰海通\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        json.dumps(["000651", 999999, None]), encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["601211"]


def test_excludes_pinned_lane_from_finalist_priority(tmp_path, monkeypatch):
    """lane=pinned 是持仓强制注入 finalists.csv,不是 L3 真选的"入围"——与
    `autoresearch.dossier.pool._selections()` 的 `lane≠pinned` 口径一致。07-29 真实产物核实:
    10 行 finalists.csv 里 3 行 pinned(688766/300857/920179),若不排除会把持仓错标成
    "finalist" 优先,且与本任务背景陈述的「9 只入围 4 档案」对不上(见 task-9-report.md)。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name,lane\n920179,凯德石英,pinned\n601211,国泰海通,healthy\n",
        encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["601211"]


def test_dedupes_same_code_appearing_in_two_lane_rows(tmp_path, monkeypatch):
    """同码可能在 finalists.csv 里以多个 lane 行出现(如既是 healthy 又是 momentum 召回),
    不该重复插队。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name,lane\n601211,国泰海通,healthy\n601211,国泰海通,momentum\n",
        encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["601211"]
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    assert len(entries) == 1


def test_strips_exchange_suffix_defensively(tmp_path, monkeypatch):
    """`code` 列今日核实无后缀,但历史上 `.SH/.SS` 后缀坑在本仓库反复复发
    (见用户记忆 ashare-harvest-sh-ss-suffix-bug)——防守性剥后缀,零成本。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n600018.SH,浦发银行\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["600018"]


def test_upgrades_legacy_string_entry_without_duplicating(tmp_path, monkeypatch):
    """`pending_init` 数组里的既有条目可能是纯字符串(非本波产物,或人工/其它路径写入)——
    必须原地升级成 dict,不能因为形态不认识就崩溃、丢弃,也不能在字符串旁边另外追加一条
    重复的 dict 条目(歧义3 裁决)。"""
    d = tmp_path / "2026-07-30"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n600018,浦发银行\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": ["600018"]}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-30")
    assert added == []  # 已在队列(纯字符串形态),非"新入队"
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    assert len(entries) == 1  # 未产生重复条目
    assert entries[0] == {"code": "600018", "priority": "finalist", "last_seen": "2026-07-30"}


def test_preserves_unrelated_legacy_string_entries(tmp_path, monkeypatch):
    """既有条目里跟今天无关的纯字符串(既不在今日 want 列表里),原样保留,不因为本函数
    看不懂它的形态就悄悄丢掉。"""
    d = tmp_path / "2026-07-30"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": ["600018"]}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-30")
    assert added == ["920179"]
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    assert "600018" in entries  # 无关的既有字符串条目原样还在
    assert any(isinstance(e, dict) and e.get("code") == "920179" for e in entries)


def test_missing_pool_file_returns_empty(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: tmp_path / "nope.json")

    assert post_run.enqueue_finalist_dossiers(d, "2026-07-29") == []


def test_no_finalists_csv_returns_empty(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    assert post_run.enqueue_finalist_dossiers(d, "2026-07-29") == []


# ───────────────────────── Wave9 final-fix I-1:插队必须真的可见 ─────────────────────────
# final-review 发现:`enqueue_finalist_dossiers` 只把码写进 `coverage_pool.json` 的
# `pending_init` 数组,但消费侧 `pool.pending_init()` 的候选集只来自 `stocks`(入池闸只放
# pinned/真选≥2 次)——首次入围的 finalist 永远进不去 `stocks`,插队对消费者恒不可见。
# 07-29 真实复现:入队 5 只,消费者可见新增 0 只,而 `post_run.py` 照样打印"插队 5 只"。
# 下面这条是那次真实复现的最小钉子——必须端到端跨两个函数验证,只测其中一个都测不出这条缺陷。


def test_enqueue_then_pending_init_makes_new_codes_visible(tmp_path, monkeypatch):
    """回归钉子(final-review Important-1,07-29 真实 pool 状态最小复刻):5 只入队
    (601211/002546/002568/003013/000333),真实当时的 pool 里只有 601211 在
    `stocks`(active),其余 4 只压根不在 `stocks`。修复前 `pending_init()` 的候选集
    只读 `stocks`,4 只永远不可见——「插队 5 只」的回执是近乎 no-op 的假象。"""
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    codes = ["601211", "002546", "002568", "003013", "000333"]
    body = "code,name\n" + "\n".join(f"{c},N{c}" for c in codes)
    (d / "finalists.csv").write_text(body, encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")

    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({
        "cap": 30,
        "stocks": {"601211": {"status": "active"}},   # 真实 07-29:只有 601211 在池
        "pending_init": [],
    }), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert set(added) == set(codes)

    pool_dict = json.loads(pool.read_text(encoding="utf-8"))
    visible = set(pool_mod.pending_init(pool_dict))
    missing = set(codes) - visible
    assert not missing, f"入队后仍不可见(修复前恒为 4 只):{missing}"


def test_enqueue_prunes_pending_entries_that_already_have_a_dossier(tmp_path, monkeypatch):
    """附带项(final-review 提):`pending_init` 数组只进不出——`pending_init()` 靠
    `dossier_path().exists()` 在**读时**把已建档的码过滤掉,但**写侧**从不清理,数组会
    无限累积。入队时顺手扫一遍,把"确认已建档"的旧条目摘掉,防止 `coverage_pool.json`
    无限增长。"""
    from autoresearch.dossier import schema

    d = tmp_path / "2026-07-30"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")

    # 600018 早先被插过队,如今已经建档(真实运行中这是常态:dossier-init 消化过它)。
    # schema.DOSSIER_DIR 已被全局 autouse fixture(tests/conftest.py::_isolate_dossier_dir)
    # 隔离到本测试专属 tmp 目录,这里写的不是真实 context/knowledge/dossiers/。
    schema.DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    schema.dossier_path("600018").write_text("# 已建档", encoding="utf-8")

    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": [
        {"code": "600018", "priority": "finalist", "last_seen": "2026-07-20"}]}),
        encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    post_run.enqueue_finalist_dossiers(d, "2026-07-30")
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    codes = {e.get("code") if isinstance(e, dict) else e for e in entries}
    assert "600018" not in codes, "已建档的旧条目应被清理,不能无限累积"
    assert "920179" in codes
