"""finalist 无档案插队建档(Wave9 R6,B-3 续)。合成,无网络。

spec: `.superpowers/sdd/2026-07-29-wave9-batch-ab-plan/task-9-brief.md`
"""
import json

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
