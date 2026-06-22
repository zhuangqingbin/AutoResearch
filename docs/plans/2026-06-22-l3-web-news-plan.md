# L3 外源新闻(akshare 媒体 @ L3 + WebSearch @ L4)— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。
> 关联 spec:`docs/specs/2026-06-22-l3-web-news-design.md`。

**Goal:** L3 紧凑表加一路**媒体新闻情感**(akshare,确定性,全 ~200);L4 finalists 决策卡加 **WebSearch 实时催化**(~30,agentic)。

**Architecture:** Part A 确定性(harvest→digest→并表,TDD 合成 fixture);Part B prompt/playbook(无单测)。

## Global Constraints
- akshare 媒体新闻**不进确定性打分**,仅 Claude 定性情感佐证。
- WebSearch 仅 finalists、仅定性,数字仍出自确定性 context(继承 lite 铁律)。
- 单元测试无网络(注入 `get_or_fetch`/`fetch` 桩);单股失败降级隔离。
- TDD 红→绿→commit;分支 `l2-zoo-champion`(同分支续作)。

---

### Task 1: `news_digest` 加 `prefix` 形参(公告/媒体共用)

**Files:** `autoresearch/scan/agents/l3_news.py`;Test: `tests/scan/test_l3_news.py`

- [ ] **失败测试**:
```python
def test_news_digest_prefix_med():
    from autoresearch.scan.agents.l3_news import news_digest
    anns = [{"title": "某公司回购", "ann_date": "20260601"}]
    d = news_digest(anns, prefix="med")
    assert set(d) == {"med_n", "med_tags", "med_head"} and d["med_n"] == 1
def test_news_digest_default_prefix_unchanged():
    from autoresearch.scan.agents.l3_news import news_digest
    assert set(news_digest([])) == {"news_n", "news_tags", "news_head"}
```
- [ ] **跑红** → FAIL(prefix 不支持 / 键名)。
- [ ] **实现**:`def news_digest(anns, prefix="news")`;空返回 `{f"{prefix}_n":0, f"{prefix}_tags":"", f"{prefix}_head":"—"}`;非空键改 `f"{prefix}_n"/f"{prefix}_tags"/f"{prefix}_head"`。
- [ ] **跑绿** + `tests/scan/test_l3_news.py` 全绿(anns_d 调用默认 prefix 不变)。
- [ ] **commit**:`feat(l3): news_digest 加 prefix(公告/媒体共用)`

### Task 2: `harvest_l3_web_news`(akshare 媒体新闻入湖 + 落 staging)

**Files:** `autoresearch/scan/agents/l3_news.py`;Test: `tests/scan/test_l3_news.py`

**Produces:** `harvest_l3_web_news(date, codes, root=None) -> dict`;逐 code `get_or_fetch("stock_news_em", {"symbol": code}, today=date)` → 归一 `新闻标题→title`/`发布时间→ann_date` → 落 `context/scan/<date>/L3_webnews/<code>.json`;单股失败 → 空列表。

- [ ] **失败测试**(注入 `get_or_fetch` 桩,无网):
```python
def test_harvest_web_news_normalizes_and_buckets(tmp_path, monkeypatch):
    import pandas as pd
    import autoresearch.scan.agents.l3_news as ln
    def fake_gof(endpoint, params, today=None, fetch=None):
        return pd.DataFrame({"新闻标题": [f"{params['symbol']} 中标大单"], "发布时间": ["2026-06-20 09:00"]})
    monkeypatch.setattr(ln, "get_or_fetch", fake_gof, raising=True)
    out = ln.harvest_l3_web_news("2026-06-20", ["600519", "000001"], root=tmp_path)
    assert set(out) == {"600519", "000001"}
    assert out["600519"][0]["title"] == "600519 中标大单" and out["600519"][0]["ann_date"]
    assert (tmp_path / "2026-06-20" / "L3_webnews" / "600519.json").exists()

def test_harvest_web_news_degrades_on_error(tmp_path, monkeypatch):
    import autoresearch.scan.agents.l3_news as ln
    def boom(endpoint, params, today=None, fetch=None):
        raise RuntimeError("no net")
    monkeypatch.setattr(ln, "get_or_fetch", boom, raising=True)
    out = ln.harvest_l3_web_news("2026-06-20", ["600519"], root=tmp_path)
    assert out["600519"] == []
```
- [ ] **跑红** → FAIL(函数不存在)。
- [ ] **实现**:模块顶 `from autoresearch.data.cache import get_or_fetch`;
```python
def harvest_l3_web_news(date, codes, root=None):
    root = root or Path("context/scan")
    out_dir = root / date / "L3_webnews"
    out_dir.mkdir(parents=True, exist_ok=True)
    buckets = {}
    for c in codes:
        code = str(c).zfill(6)
        try:
            df = get_or_fetch("stock_news_em", {"symbol": code}, today=date)
            rows = []
            if df is not None and len(df):
                for _, r in df.iterrows():
                    rows.append({"title": str(r.get("新闻标题", "")),
                                 "ann_date": str(r.get("发布时间", ""))})
            buckets[code] = rows
        except Exception:  # noqa: BLE001 — 单股降级隔离
            buckets[code] = []
        (out_dir / f"{code}.json").write_text(
            json.dumps(buckets[code], ensure_ascii=False, default=str), encoding="utf-8")
    return buckets
```
- [ ] **跑绿**。
- [ ] **commit**:`feat(l3): harvest_l3_web_news(akshare 媒体新闻入湖 + 归一 + 降级)`

### Task 3: `load_l3_input` 并入 `med_*` + `_L3_COLS` 追列

**Files:** `autoresearch/scan/agents/l3_select.py`;Test: `tests/scan/test_l3_news_table.py`

- [ ] **失败测试**:构造 `L2_gbdt_top200.csv` + `L3_webnews/<code>.json`(注入)→ `load_l3_input` 含 `med_n/med_tags/med_head`;无 webnews 目录 → 缺省 `0/""/—`。
- [ ] **跑红**。
- [ ] **实现**:`_L3_COLS` 在 `news_head` 后追加 `"med_n", "med_tags", "med_head"`;`load_l3_input` 仿 anns_d 段:读 `L3_webnews/<code>.json` → `news_digest(web, prefix="med")` → merge;缺目录/文件 → `news_digest([], prefix="med")` 缺省。
- [ ] **跑绿** + `tests/scan/test_agents.py`(finalists 合并不破)。
- [ ] **commit**:`feat(l3): L3 表并入媒体新闻 digest(med_*,与公告 news_* 并列)`

### Task 4: 文档(L3 媒体 + L4 WebSearch 步)

**Files:** `.claude/skills/scan-market/screening-playbook.md`、`SKILL.md`

- [ ] L3 段:紧凑表列加 `med_*`(媒体情感),holistic prompt 提示"公告 vs 媒体两路情感";harvest 增 `harvest_l3_web_news`。
- [ ] L4 段:Tier-1 决策卡加 **WebSearch 步**(`<名称> <代码> 最新 研报/突发/政策/订单` → 1–2 条真·催化+时效 → catalyst/risk;仅 finalists、仅定性、数字仍出确定性 context)。
- [ ] SKILL.md L3 行注媒体情感、L4 注 WebSearch。
- [ ] **commit**:`docs(scan): L3 媒体新闻 + L4 finalists WebSearch 步`

---

## Self-Review
- Spec 覆盖:Part A = Task 1-3;Part B(WebSearch)= Task 4 playbook;诚实局限入 Task 4 文档。
- 类型一致:`news_digest(anns, prefix)`、`harvest_l3_web_news(date, codes, root)`、`med_*` 列全程一致。
- 无占位:每步含真代码/真断言。
