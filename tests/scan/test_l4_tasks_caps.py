"""Wave11 T1:派发帽与资源帽解耦 —— effective_cap 只由 l4_stock 决定,批次一次全派。
Wave11 T2:tushare 操作级信号量 —— `_tushare_slot()` K 槽 fcntl 排队(带等待心跳)+
`prepare_slim()` 集成(仅包 harvest 循环,湖命中零等待)。"""
from contextlib import contextmanager

from autoresearch.scan import l4_tasks


def _harvest_ok(tmp_path):
    """caps 相关测试不关心 harvest 本身对不对,只要它一调就能让 `_slim_defect` 判过。"""
    def harvest(ticker, date):
        target = tmp_path / "ctx" / f"{ticker}_{date}_slim.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
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
        return target
    return harvest


def _init(tmp_path, n=10, caps=None):
    codes = [f"{600000+i}" for i in range(n)]
    return l4_tasks.initialize("2026-08-06", codes, root=tmp_path,
                               context_root=tmp_path / "ctx", caps=caps)


def test_dispatch_cap_is_l4_stock_only(tmp_path):
    r = _init(tmp_path, n=10)          # DEFAULT_CAPS: l4_stock=64
    assert r["effective_cap"] == 64
    b = l4_tasks.dispatch_batches(tmp_path / "2026-08-06" / "_l4_tasks.json")
    assert len(b["batches"]) == 1 and len(b["batches"][0]) == 10   # 一次全派


def test_rate_limit_failures_no_longer_shrinks_dispatch(tmp_path):
    _init(tmp_path, n=6)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    payload = json.loads(book.read_text())
    payload["rate_limit_failures"] = 3
    book.write_text(json.dumps(payload))
    b = l4_tasks.dispatch_batches(book)
    assert b["effective_cap"] == 64            # 派发帽不缩;限频收窄的是 T2 的 tushare 槽
    assert len(b["batches"][0]) == 6


def test_mark_failure_still_increments_rate_limit_failures(tmp_path):
    """C1 只让 rate_limit_failures 退出 effective_cap 运算,mark_failure() 记账逻辑本身
    不能跟着丢——T2 要靠这个计数收窄 tushare 信号量槽数。锁的是「自增」而非「置 1」,
    否则把自增写错成赋值也测不出来。"""
    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json

    l4_tasks.preflight(book, code)
    l4_tasks.mark_failure(book, code, "RATE_LIMIT")
    assert json.loads(book.read_text())["rate_limit_failures"] == 1

    l4_tasks.preflight(book, code)
    l4_tasks.mark_failure(book, code, "RATE_LIMIT")
    assert json.loads(book.read_text())["rate_limit_failures"] == 2


def test_tushare_slot_queues_when_full(tmp_path):
    import fcntl

    sem_dir = tmp_path / "_sem"
    sem_dir.mkdir()
    hold = (sem_dir / "tushare.0.lock").open("a+")
    fcntl.flock(hold, fcntl.LOCK_EX | fcntl.LOCK_NB)      # 外部占住 slot0
    got = {}
    with l4_tasks._tushare_slot(tmp_path, k=2) as slot:   # k=2 → 应立刻拿到 slot1
        got["slot"] = slot
    assert got["slot"] == 1
    hold.close()


def test_tushare_slot_waits_then_acquires(tmp_path, monkeypatch):
    import fcntl
    import threading
    import time

    sem_dir = tmp_path / "_sem"
    sem_dir.mkdir()
    fh = (sem_dir / "tushare.0.lock").open("a+")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Timer(0.3, lambda: (fcntl.flock(fh, fcntl.LOCK_UN), fh.close())).start()
    t0 = time.monotonic()
    with l4_tasks._tushare_slot(tmp_path, k=1, poll_seconds=0.05) as slot:
        assert slot == 0
    assert time.monotonic() - t0 >= 0.25                  # 真排过队


def test_prepare_slim_sem_wait_s_zero_when_slim_already_valid(tmp_path):
    """defect 从一开始就是 None(湖命中/已有合格 slim)—— 零网络也零等待,完全不进
    `_tushare_slot`;但 sem_wait_s 这个键仍必须写 0.0,不能因为跳过分支就漏写。"""
    import json

    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    ticker = json.loads(book.read_text())["tasks"][code]["ticker"]
    slim_path = tmp_path / "ctx" / f"{ticker}_2026-08-06_slim.md"
    slim_path.parent.mkdir(parents=True, exist_ok=True)
    slim_path.write_text(
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
    got = l4_tasks.prepare_slim(book, code)
    assert got["ok"] is True
    assert got["attempts"] == 0          # 零网络:没进 harvest 循环,自然也没进信号量
    assert got["sem_wait_s"] == 0.0
    payload = json.loads(book.read_text())
    assert payload["tasks"][code]["sem_wait_s"] == 0.0


def test_prepare_slim_records_sem_wait_s_in_task_book_on_harvest_path(tmp_path):
    """sem_wait_s 是新接口键,brief 要求返回 dict 和任务簿回写两处同步记 —— 只断言其中
    一处测不出另一处被漏写或写岔(两个数各写各的、互不相等)。"""
    import json

    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"

    def harvest(ticker, date):
        target = tmp_path / "ctx" / f"{ticker}_{date}_slim.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
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
        return target

    got = l4_tasks.prepare_slim(book, code, harvest_fn=harvest, retries=0)
    assert got["ok"] is True
    assert isinstance(got["sem_wait_s"], float)
    payload = json.loads(book.read_text())
    assert payload["tasks"][code]["sem_wait_s"] == got["sem_wait_s"]


def test_prepare_slim_records_failure_when_caps_missing_tushare_key(tmp_path):
    """Review Important-2:k 计算/信号量获取曾经落在记账块之外、无保护 —— caps 字典被
    改坏(旧格式/手工改坏,缺 tushare 键)时 KeyError 会从 prepare_slim 顶层冒出,任务簿
    永远到不了 `with _locked(path)` 那段,该票卡在 RUNNING 直到 stale_after_seconds 超时
    才被拉回,期间账本上零失败痕迹——这正是本 task 本身要治的「静默卡住」的同类病。
    修复后:异常必须转成 defect,照常流进记账块,写下 last_error_class/last_error,
    且不进 harvest 循环(k 计算先炸,harvest_fn 压根不该被调用)。"""
    import json

    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    payload = json.loads(book.read_text())
    payload["caps"] = {"web_search": 4, "web_fetch": 4, "l4_stock": 64}  # 缺 tushare 键
    book.write_text(json.dumps(payload))

    calls = []

    def harvest(ticker, date):
        calls.append(ticker)
        raise AssertionError("k 计算应该先炸,不该走到 harvest")

    got = l4_tasks.prepare_slim(book, code, harvest_fn=harvest, retries=0)

    assert calls == []                 # 没进 harvest 循环
    assert got["ok"] is False
    assert got["attempts"] == 0

    saved = json.loads(book.read_text())
    task = saved["tasks"][code]
    # 结构性失败,不是瞬时的——不能标 RATE_LIMIT/TIMEOUT 那类可重试的错误类别。
    assert task["last_error_class"] == "DATA_INTEGRITY"
    assert task["last_error"] == got["reason"]


def test_prepare_slim_tushare_k_subtracts_rate_limit_failures(tmp_path, monkeypatch):
    """Review Important-1:`k = caps.tushare - rate_limit_failures` 这条核心公式此前
    零测试能观测到——两个 `_tushare_slot` 原语测试直接传字面量 k 绕过公式;湖命中测试
    压根不进 `if defect:` 分支;唯一执行到公式的测试恰好 tushare=4、rate_limit_failures=0,
    `4-0=4`,减法项在不在对断言零影响。这里 monkeypatch `_tushare_slot` 记录真正传入的
    k,直接断言减法生效(而不是恰好等于 tushare 原值)。"""
    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    payload = json.loads(book.read_text())
    payload["rate_limit_failures"] = 2          # caps.tushare 默认 4
    book.write_text(json.dumps(payload))

    seen = {}

    @contextmanager
    def fake_slot(scan_dir, k, **kwargs):
        seen["k"] = k
        yield 0

    monkeypatch.setattr(l4_tasks, "_tushare_slot", fake_slot)
    got = l4_tasks.prepare_slim(book, code, harvest_fn=_harvest_ok(tmp_path), retries=0)

    assert got["ok"] is True
    assert seen["k"] == 2            # 4 - 2 = 2,减法项生效


def test_prepare_slim_tushare_k_floors_at_one(tmp_path, monkeypatch):
    """rate_limit_failures 远大于 caps.tushare 时 k 必须下限 1,不能是 0 或负数——
    `_tushare_slot` 内部对 k 自己也有 `max(1, int(k))` 兜底,但这里要锁的是 `prepare_slim`
    这一层的公式本身有没有 floor,不能靠下游兜底掩盖上游漏洞(万一以后下游那层被改掉)。"""
    r = _init(tmp_path, n=1)
    code = r["codes"][0]
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    payload = json.loads(book.read_text())
    payload["rate_limit_failures"] = 9          # caps.tushare 默认 4;4-9=-5
    book.write_text(json.dumps(payload))

    seen = {}

    @contextmanager
    def fake_slot(scan_dir, k, **kwargs):
        seen["k"] = k
        yield 0

    monkeypatch.setattr(l4_tasks, "_tushare_slot", fake_slot)
    got = l4_tasks.prepare_slim(book, code, harvest_fn=_harvest_ok(tmp_path), retries=0)

    assert got["ok"] is True
    assert seen["k"] == 1             # floor:max(1, 4-9) = 1,不是 0/负数


def test_stats_counts_errors_and_wait(tmp_path):
    _init(tmp_path, n=3)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    p = json.loads(book.read_text())
    codes = list(p["tasks"])
    p["tasks"][codes[0]]["last_error_class"] = "RATE_LIMIT"
    p["tasks"][codes[1]]["sem_wait_s"] = 42.0
    book.write_text(json.dumps(p))
    s = l4_tasks.stats(book)
    assert s["error_classes"] == {"RATE_LIMIT": 1}
    assert s["sem_wait_max_s"] == 42.0


def test_stats_is_pure_read_and_does_not_touch_book(tmp_path):
    """设计口径:stats() 纯读 —— 不得写任何文件、不得改任务簿一个字节。字节级 +
    mtime 双重断言:光比内容相等测不出「原地重写成同样内容」这种打脸。"""
    _init(tmp_path, n=2)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    before = book.read_bytes()
    before_mtime_ns = book.stat().st_mtime_ns

    l4_tasks.stats(book)

    assert book.read_bytes() == before
    assert book.stat().st_mtime_ns == before_mtime_ns
    assert not book.with_name(f"{book.name}.tmp").exists()


def test_stats_missing_sem_wait_is_absent_not_zero(tmp_path):
    """老账本没有 sem_wait_s 是正常的(该键晚于任务簿本身上线)——缺失必须读作
    「无该项」,不能被当 0 计入 max/均值,否则会伪造出一个不存在的「零等待」。"""
    _init(tmp_path, n=2)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"

    s = l4_tasks.stats(book)

    assert s["sem_wait_max_s"] is None
    assert s["sem_wait_mean_s"] is None


def test_stats_sem_wait_mean_averages_only_present_values(tmp_path):
    """0.0(湖命中零等待)是「有记录」,必须计入均值;第三只缺字段的票不能被当 0
    拉低均值 —— 均值应为 (0.0+10.0)/2=5.0,不是 (0+10+0)/3。"""
    import json

    _init(tmp_path, n=3)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    p = json.loads(book.read_text())
    codes = list(p["tasks"])
    p["tasks"][codes[0]]["sem_wait_s"] = 0.0
    p["tasks"][codes[1]]["sem_wait_s"] = 10.0
    # codes[2] 保持缺 sem_wait_s 字段(模拟未进 prepare_slim 或老账本)
    book.write_text(json.dumps(p))

    s = l4_tasks.stats(book)

    assert s["sem_wait_max_s"] == 10.0
    assert s["sem_wait_mean_s"] == 5.0


def test_stats_slim_attempts_total_sums_across_tasks(tmp_path):
    import json

    _init(tmp_path, n=3)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    p = json.loads(book.read_text())
    codes = list(p["tasks"])
    p["tasks"][codes[0]]["slim_attempts"] = 2
    p["tasks"][codes[1]]["slim_attempts"] = 1
    book.write_text(json.dumps(p))

    s = l4_tasks.stats(book)

    assert s["slim_attempts_total"] == 3


def test_stats_error_classes_counts_multiple_and_ignores_none(tmp_path):
    """None(未失败过的正常票)不能被计进 error_classes,否则「零错误」的票会
    污染计数,一眼看限频风暴的读数就失真了。"""
    import json

    _init(tmp_path, n=3)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    p = json.loads(book.read_text())
    codes = list(p["tasks"])
    p["tasks"][codes[0]]["last_error_class"] = "RATE_LIMIT"
    p["tasks"][codes[1]]["last_error_class"] = "RATE_LIMIT"
    p["tasks"][codes[2]]["last_error_class"] = None
    book.write_text(json.dumps(p))

    s = l4_tasks.stats(book)

    assert s["error_classes"] == {"RATE_LIMIT": 2}


def test_main_stats_prints_one_line_json(tmp_path, capsys):
    """CLI 出口:`stats` 吃 DATE(同 batches 分支),打一行 JSON 到 stdout,退出码 0
    —— 壳 agent 靠这一行原样带回,不能被 print 拆成多行或夹杂旁白。"""
    import json

    _init(tmp_path, n=2)
    rc = l4_tasks.main(["stats", "2026-08-06", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]

    assert rc == 0
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is True
    assert payload["n_tasks"] == 2
