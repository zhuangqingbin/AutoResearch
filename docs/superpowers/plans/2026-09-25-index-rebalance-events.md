# 指数调样事件(日历事实 + 生效前夜守卫)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「一只股票即将被纳入/剔出指数」作为**事实日期**接进扫描漏斗(日历第三腿,L4 简报 / summary / 档案 / 行业包四处继承),并在 E6 加一道独立计数的硬门 `rebalance_close`:扫描日等于调样生效前夜的调样票不得成为 BUY。零 LLM;一年真正起作用约四个交易夜;其余日子逐字 parity。

**Architecture:** 三层新件:① 数据层——中证公告 source adapter(`autoresearch/data/sources/csindex.py`,列表 → 详情 → xlsx 名单)+ 六个 tushare 端点登记(`index_weight` / `index_basic` / `fund_basic` / `fund_share` / `fund_nav`),全部 B 级契约;② 事件层——`autoresearch/scan/index_events.py` 产出登记产物 `index_events.csv`(六指数白名单,按扫描日算相位),由 prelude `calendar` 步内 `harvest_calendar` 顺带生产,日历第三腿 `kind="index_rebalance"` 让四处消费者免费继承;③ 决策层——`relative_buy.build_decision` 在旋钮开时多一门 `rebalance_close`(独立计数,不并入 `no_redflag`),决策文件多一块 `index_events`,brief ③ 印 ⛔ 行,两引擎共用的卡契约 `l4-card.md` 加一条入场规则。四批:B0 数据层(全惰性)→ B1 日历(杆 `calendar.index_rebalance`)→ B2 守卫(杆 `relative_buy.rebalance_gate`)→ B3 ETF 规模描述字段 + stock-research 确定性行。

**Tech Stack:** Python 3.12 / pandas / pyarrow / requests / openpyxl(读 xlsx 附件,新增声明依赖)/ pytest;运行统一 `uv run --no-sync python -m pytest ...`;配置唯一事实源 `.claude/skills/scan-market/scan_config.jsonc`。

**Spec:** `docs/specs/2026-09-25-index-inclusion-signal-design.md`(本计划从它论证;执行者两份都读)。普查与 spike 读数见其附录 A/B;brainstorm 讨论稿 `docs/specs/2026-09-25-index-inclusion-signal-brainstorm.md`。

## Global Constraints

- 用户 2026-09-25 裁定 E1–E5:需求 = 「日历事实 + 生效前夜守卫」而非加分;守卫对**全部**调样票(调入与调出、六指数)一刀;路线 C(预测席位)不立项;**所有生产改动排在可买性对齐波批 4 十日真跑之后**(`docs/superpowers/plans/2026-09-24-buyability-realignment.md:3043`「期间不改规则」);只覆盖 A 股六指数(沪深300 / 中证500 / 中证1000 / 中证A500 / 科创50 / 创业板指)。
- **硬时限**:下一次半年调样公告 2026-11-27(周五盘后)、生效前夜 2026-12-10(周四扫描)、生效 2026-12-11 收盘。批 B1/B2 须在 2026-11-27 前合并,否则第一次真跑验收推到 2027-06。批 B0 可在批 4 冻结窗内于 worktree 开发,**合并等批 4 结束**。
- 不动的裁定:主尺 `gap_c1_o2`;不提 swing;E6 唯一 BUY owner(任何通道不得自产 BUY);learning 层不重开;L4 复用不恢复;「不要跌势票」是产品偏好;不加 L3 守卫、不给 L3 加列;不给 `l4-intel` 加第七面(`tests/scan/test_frozen_b_class_boundary.py:57` 冻结);不复活 L1 `event` 通道、不借 L2「事件」桶。
- 防锚定:日历行是事实日期非方向;**唯一带方向词的文案只在生效前夜出现,且方向是「禁止」**。
- `scan_config.jsonc` 是唯一参数事实源:新旋钮 = `user_config._TOP_WHITELIST`/`_SUB_WHITELIST` + `_KNOB_TYPES` + 真实 `knob()` 消费点 + `tests/scan/test_config_knobs.py`,缺一不上生产。内建默认一律 = 旧行为(parity);新行为只由生产配置打开;每批一根回滚杆。
- 新产物先登记 `autoresearch/contracts/artifacts.py` 的 `ARTIFACTS`,再写生产者;新端点先登记 `autoresearch/data/endpoints.py`(未登记 `policy()` 抛 KeyError)与 `autoresearch/data/contracts.py`(未登记契约 = 不校验);所有取数走 `cache.get_or_fetch`,不写裸 `pro.*`;写湖一律剥 `fields`。
- **缺席 ≠ 否**(2026-09-25 五连撞教训):每个新字段都要能分开「源不可达」「源可达无事件」「有事件但不在守卫相位」「守卫命中」四个世界;读历史决策文件先看有没有 `index_events` 块再下结论。
- 引擎隔离:Python 侧共用;路径走 `autoresearch.common.workspace`;agent 行为改动改 `.claude/agents/l4-card.md` + `.claude/skills/stock-research/lite-playbook.md`——Codex 的 `.codex/agents/l4_card.toml` 只引用前者(`developer_instructions`「卡片契约只读 `.claude/agents/l4-card.md`」),不改。
- 每个任务:先写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。每批末尾跑全量 `uv run --no-sync python -m pytest -q tests/` 绿 + `uv run --no-sync ruff check autoresearch tests` 干净。每批做一次变异探针(删掉新分支 → 至少一个测试红),结果写进提交说明。
- 提交信息末尾加:`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。
- **与 spec 的六处差异(Task 12 回写 spec)**:① 旋钮 `calendar.index_rebalance` 是**平铺布尔**(不是 `{enabled}` 对象,镜像 `l2.knife_cap`);② `configured_relative_buy()` 保持五元组不变,新增独立的 `configured_rebalance_gate()`(少改调用点);③ `run_health.index_events` 只记 `source / n_rows / n_finalists_involved / n_passive_close_eve`——门命中数在决策文件与 brief ③(run_health 在 E6 之前写盘,读不到门);④ `index_events.csv` 收**六指数全部**公告行(不按菜单 codes 过滤),`calendar.csv` 的第三腿行才按 L2∪finalists 过滤——summary 才能印全量计数;⑤ `RULE_VERSION` 无条件升 `"e6.v4.1"`(逐字镜像 v4.0 对 `tiering` 的先例),旋钮关时的 parity 判据 = 「除 `rule_version` 字符串外逐字节相同」;⑥ 详情缓存键 = `<ann_id>@<取数日>`,`index_events` 取数前先找湖里任一 `<ann_id>@*.parquet`,不重取。

---

## 文件结构(先定边界,再拆任务)

| 文件 | 责任 | 批 |
|---|---|---|
| `autoresearch/data/cache.py:37-39` | 键参数表加 `nav_date` / `index_code` / `ann_id` | B0 |
| `autoresearch/data/endpoints.py` | 登记 7 个端点(2 个 `csindex` 源 + 5 个 tushare) | B0 |
| `autoresearch/data/contracts.py` | 7 条 B 级契约(两条 csindex `persist_violations=False`) | B0 |
| `autoresearch/data/sources/__init__.py` | 路由 `source == "csindex"` | B0 |
| `autoresearch/data/sources/csindex.py`(新) | 中证公告列表 / 详情 / xlsx 名单解析 / 生效日文本解析 | B0 |
| `autoresearch/contracts/artifacts.py` | 登记 `index_events`(gated) | B0 |
| `autoresearch/scan/index_events.py`(新) | 事件表:白名单、相位、生效日推导、build/write/load/by_code、交易日窗 | B0 |
| `autoresearch/research/index_rebalance_census.py`(新) | 附录 A 普查的可复现 CLI(只读湖 + `index_weight`) | B0 |
| `pyproject.toml` | 声明 `openpyxl` | B0 |
| `autoresearch/scan/user_config.py` | 白名单/类型:`calendar.index_rebalance`、`relative_buy.rebalance_gate` | B1/B2 |
| `autoresearch/scan/calendar.py` | 第三腿 `kind="index_rebalance"`;flags 两种文案;section 市场级行 | B1 |
| `autoresearch/scan/prelude.py:409-425` | `_calendar` 汇总行加「调样 N」 | B1 |
| `autoresearch/scan/health.py` | `index_events_health` + `run_health` 键(旋钮开才出现) | B1 |
| `autoresearch/scan/relative_buy.py` | `_hard_gates()` / `_field_usage(tiering, rebalance_gate)` / `_hard_gate` ⑤ / `build_decision(index_events=, rebalance_gate=)` / 决策块 / `RULE_VERSION` / `configured_rebalance_gate()` / I/O 边界 | B2 |
| `autoresearch/scan/post_run.py:859-867` | 传 `rebalance_gate` 给两个写者 | B2 |
| `autoresearch/scan/relative_facts.py` | `rebalance` 键 | B2 |
| `autoresearch/scan/brief.py` | ③ ⛔ 行(blocked / 正常两分支) | B2 |
| `autoresearch/scan/self_review.py` | `card_contract_lint` 加「调样前夜入场允许」warn | B2 |
| `.claude/agents/l4-card.md`、`.claude/skills/stock-research/lite-playbook.md` | 入场行规则加一条;`tests/test_agent_defs.py` 锚 | B2 |
| `.claude/skills/scan-market/scan_config.jsonc`、`STAGES.md`、`SKILL.md`、spec | 生产开关 + 文档同步 + spec 回写 | B2 |
| `autoresearch/scan/index_flow.py`(新) | ETF 规模 → `flow_adv_days`(presence-gated) | B3 |
| `autoresearch/analyze/blocks_ashare.py:274-320` | 「指数调样 → WebSearch」换确定性行 | B3 |

---

# 批 B0 · 数据层(全惰性:无消费者,无回滚杆;冻结窗内可在 worktree 开发,合并等批 4 结束)

### Task 1: 数据层登记(缓存键参数 / 端点 / 契约 / openpyxl)

**Files:**
- Modify: `autoresearch/data/cache.py:37-39`
- Modify: `autoresearch/data/endpoints.py`(`ENDPOINTS` 表 `②' tushare 静态/日历` 段之后追加)
- Modify: `autoresearch/data/contracts.py`(`CONTRACTS` 表 B 级段末尾追加)
- Modify: `tests/data/test_endpoints.py:57`(允许的 source 集合加 `"csindex"`)
- Modify: `pyproject.toml`(dependencies 加 openpyxl)
- Test: `tests/data/test_index_event_endpoints.py`(新)

**Interfaces:**
- Produces: 端点名 `csindex_rebalance_list`(`as_of`,`snapshot: True`,实体缺省 `all`)、`csindex_rebalance_detail`(`as_of`,实体 `ann_id`)、`index_weight`(`as_of`,实体 `index_code`,as_of = 调用方 `today` 形参 = 月末)、`index_basic`/`fund_basic`(`static`)、`fund_share`/`fund_nav`(`date`;`fund_nav` 按 `nav_date` 键)。Task 2/3/13 按这些名字调 `cache.get_or_fetch`。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_index_event_endpoints.py
"""指数调样事件源的数据层登记(design 2026-09-25 §2.1):端点 policy、B 级契约、缓存键。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, endpoints

SEVEN = ("csindex_rebalance_list", "csindex_rebalance_detail", "index_weight",
         "index_basic", "fund_basic", "fund_share", "fund_nav")


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


@pytest.mark.parametrize("name,key,source", [
    ("csindex_rebalance_list", "as_of", "csindex"),
    ("csindex_rebalance_detail", "as_of", "csindex"),
    ("index_weight", "as_of", "tushare"),
    ("index_basic", "static", "tushare"),
    ("fund_basic", "static", "tushare"),
    ("fund_share", "date", "tushare"),
    ("fund_nav", "date", "tushare"),
])
def test_index_event_endpoints_registered(name, key, source):
    pol = endpoints.policy(name)
    assert pol["key"] == key and pol["source"] == source and pol["settle"] == "eod"


def test_rebalance_list_is_a_snapshot_endpoint():
    assert endpoints.policy("csindex_rebalance_list").get("snapshot") is True
    assert endpoints.policy("csindex_rebalance_detail").get("snapshot") is None   # 详情不可变,不是快照


def test_all_seven_have_tier_b_contracts():
    for name in SEVEN:
        assert contracts.CONTRACTS[name].tier == contracts.TIER_DEGRADE, name
    for name in ("csindex_rebalance_list", "csindex_rebalance_detail"):
        assert contracts.CONTRACTS[name].persist_violations is False, name   # 脆源:半截/空不入湖
    assert contracts.CONTRACTS["fund_share"].empty_ok is True
    assert contracts.CONTRACTS["fund_nav"].empty_ok is True


def test_index_weight_key_is_index_code_at_month_end(lake):
    params = {"index_code": "000300.SH", "start_date": "20260601", "end_date": "20260630"}
    p = cache.lake_path("index_weight", params, today="20260630")
    assert p == lake / "index_weight" / "000300_SH@20260630.parquet"
    q = cache.lake_path("index_weight", {**params, "index_code": "000905.SH"}, today="20260630")
    assert p != q                                             # 两个指数同月不撞键


def test_detail_key_is_announcement_id(lake):
    p = cache.lake_path("csindex_rebalance_detail", {"ann_id": "3006227"}, today="20260925")
    q = cache.lake_path("csindex_rebalance_detail", {"ann_id": "3006244"}, today="20260925")
    assert p.name == "3006227@20260925.parquet" and p != q     # 两个公告不撞键


def test_fund_nav_keys_on_nav_date(lake):
    assert cache.lake_path("fund_nav", {"nav_date": "20260924"}).name == "20260924.parquet"


def test_list_key_is_all_at_today(lake):
    assert cache.lake_path("csindex_rebalance_list", {}, today="20260925").name == "all@20260925.parquet"


def test_empty_rebalance_list_degrades_and_refuses_lake(lake, monkeypatch):
    monkeypatch.setattr(cache, "_real_today", lambda: "20260925")
    empty = pd.DataFrame(columns=["ann_id", "title", "publish_date", "theme"])
    out = cache.get_or_fetch("csindex_rebalance_list", {}, today="20260925", fetch=lambda e, p: empty)
    assert out.empty
    assert any(r["endpoint"] == "csindex_rebalance_list" for r in contracts.degradations())
    assert not list((lake / "csindex_rebalance_list").glob("*.parquet"))   # 违约不入湖(C2)
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/data/test_index_event_endpoints.py -q`
Expected: FAIL,`KeyError: unknown endpoint 'csindex_rebalance_list'`。

- [ ] **Step 3: 键参数表(`autoresearch/data/cache.py:37-39`)**

```python
_DATE_PARAM_KEYS = ("trade_date", "date", "ann_date", "cal_date", "nav_date")   # nav_date:fund_nav(2026-09-25)
_PERIOD_PARAM_KEYS = ("period", "date", "end_date")
# index_code:index_weight 一指数一月一份;ann_id:中证公告详情一公告一份(2026-09-25 指数调样事件源)
_ENTITY_PARAM_KEYS = ("ts_code", "symbol", "code", "exchange_id", "exchange", "index_code", "ann_id")
```

- [ ] **Step 4: 端点登记(`autoresearch/data/endpoints.py`,`②' tushare 静态/日历` 段之后)**

```python
    # ── ① tushare 指数 / 基金(design 2026-09-25 指数调样事件源 §2.1;全部 B 级,消费者 presence-gated)──
    # index_weight:月末成分快照,一指数一月一份 —— 实体 index_code + as_of(调用方把 today 传成月末),
    # 键形如 000300_SH@20260630;用于普查 / 对账(公告名单 vs 月末实现),**不是**前瞻源。
    "index_weight": {"key": "as_of", "settle": "eod", "source": "tushare"},
    "index_basic": {"key": "static", "settle": "eod", "source": "tushare"},   # 指数元数据(单一 market=CSI 参数集)
    "fund_basic": {"key": "static", "settle": "eod", "source": "tushare"},    # ETF 元数据(基准含指数名;market=E,status=L)
    "fund_share": {"key": "date", "settle": "eod", "source": "tushare"},      # ETF 份额(trade_date 全基金一日一份)
    "fund_nav": {"key": "date", "settle": "eod", "source": "tushare"},        # ETF 净值(nav_date 键,见 cache._DATE_PARAM_KEYS)

    # ── ② 中证指数公司公告(source=csindex 自采;akshare 无对应函数)──
    # list:queryAnnouncementByType 只给最新 5 条、不分页 → 每取数日一份快照(all@today),历史靠湖累积;
    #      snapshot=True:接口只返回「此刻」,补跑不得写成过去某天的假历史(SnapshotDateError 守门)。
    # detail:queryAnnouncementById + 附件 xlsx 解析后的长表,内容不可变 → 实体 ann_id,取一次永久留底
    #      (取数方先找湖里任一 <ann_id>@*.parquet,见 scan/index_events._load_detail)。
    "csindex_rebalance_list": {"key": "as_of", "settle": "eod", "source": "csindex", "snapshot": True},
    "csindex_rebalance_detail": {"key": "as_of", "settle": "eod", "source": "csindex"},
```

- [ ] **Step 5: 契约(`autoresearch/data/contracts.py`,`CONTRACTS` B 级段末尾、衍生品段之前追加)**

```python
    # ── B 级:指数调样事件源(design 2026-09-25 §2.1)——缺失时漏斗照常,日历第三腿/守卫整段不出现 ──
    # 两条 csindex 是爬虫式脆源:半截/空**不入湖**(落了就 path.exists() 恒命中,当日永远残缺)。
    # required_cols 只列下游真读的列;detail 不要求 index_code(无附件的纯文本公告该列合法全空)。
    "csindex_rebalance_list": _c(TIER_DEGRADE, "ann_id title publish_date", 1,
                                 note="中证调样公告列表:最新 5 条快照(scan/index_events 消费)",
                                 persist_violations=False),
    "csindex_rebalance_detail": _c(TIER_DEGRADE, "ann_id publish_date content_text", 1,
                                   note="中证调样公告详情 + 附件名单长表(一行一票一指数一侧)",
                                   persist_violations=False),
    "index_weight": _c(TIER_DEGRADE, "index_code con_code trade_date weight",
                       note="指数月末成分快照:普查 / 公告对账(research/index_rebalance_census)"),
    "index_basic": _c(TIER_DEGRADE, "ts_code name", note="指数元数据"),
    "fund_basic": _c(TIER_DEGRADE, "ts_code name benchmark", note="ETF 元数据(基准含指数名;scan/index_flow)"),
    "fund_share": _c(TIER_DEGRADE, "ts_code trade_date fd_share", note="ETF 份额:被动规模估算;某日无数据 = 真实空",
                     empty_ok=True),
    "fund_nav": _c(TIER_DEGRADE, "ts_code nav_date unit_nav", note="ETF 净值:被动规模估算;某日无数据 = 真实空",
                   empty_ok=True),
```

- [ ] **Step 6: 放行新 source 名(`tests/data/test_endpoints.py:57`)**

把 `assert pol["source"] in {"tushare", "akshare", "eastmoney", "fred", "yfinance", "cboe", "official", "sec"}, name` 的集合加上 `"csindex"`。

- [ ] **Step 7: 声明 openpyxl**

Run: `uv add "openpyxl>=3.1"`(写进 `pyproject.toml` dependencies 并更新 lock),然后 `uv run --no-sync python -c "import openpyxl; print(openpyxl.__version__)"`。
Expected: 打印版本号 ≥ 3.1。

- [ ] **Step 8: 跑绿**

Run: `uv run --no-sync python -m pytest tests/data/test_index_event_endpoints.py tests/data/test_endpoints.py tests/data/test_contracts.py tests/data/test_cache.py -q`
Expected: 全部 PASS。

- [ ] **Step 9: 提交**

```bash
git add autoresearch/data/cache.py autoresearch/data/endpoints.py autoresearch/data/contracts.py tests/data/test_endpoints.py tests/data/test_index_event_endpoints.py pyproject.toml uv.lock
git commit -m "feat(data): register index-rebalance event sources (csindex list/detail, index_weight, fund_*) as tier-B endpoints

Keys: index_weight by index_code@month-end, detail by ann_id, fund_nav by nav_date.
No consumer yet (design 2026-09-25 §2.1, batch B0).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: 中证公告 source adapter(列表 / 详情 / xlsx 名单 / 生效日文本)

**Files:**
- Create: `autoresearch/data/sources/csindex.py`
- Modify: `autoresearch/data/sources/__init__.py`(`fetch` 加 `csindex` 分支)
- Test: `tests/data/test_csindex_source.py`(新)

**Interfaces:**
- Produces(Task 3 消费):
  - `LIST_COLS = ["ann_id", "title", "publish_date", "theme"]`;`fetch_rebalance_list(timeout=20) -> pd.DataFrame`;`list_frame(items: list[dict]) -> pd.DataFrame`。
  - `DETAIL_COLS = ["ann_id", "publish_date", "title", "content_text", "attachment_url", "index_code", "index_name", "side", "code", "name"]`;`fetch_rebalance_detail(ann_id, timeout=20) -> pd.DataFrame`;`detail_frame(data: dict, xlsx_bytes: bytes | None, attachment_url: str | None) -> pd.DataFrame`。
  - `parse_adjustment_xlsx(raw: bytes) -> pd.DataFrame`(列 `index_code, index_name, side∈{add,drop}, code, name`,六位补零,`-` 空位剔除,续行承接上一指数)。
  - `parse_effective_date(content_text: str) -> tuple[str | None, str | None]`,第二元 ∈ `{"after_close", "from_date", None}`。
  - `html_to_text(html: str) -> str`。
- `publish_date` 一律 `YYYYMMDD`(去横线)。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_csindex_source.py
"""中证指数公司公告 adapter(design 2026-09-25 §2.1 / F7):列表帧、详情帧、xlsx 名单解析、生效日文本解析、路由。
全部离线:接口返回体用 2026-09-25 实测的形态造 fixture;附件按实测版式现造 xlsx。"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from autoresearch.data.sources import csindex

LIST_BODY = {"code": "200", "msg": "Success", "data": {
    "companyAnnouncements": [], "indexlaunchesAnnouncements": [],
    "indexrebalancingAnnouncements": [
        {"id": 3006244, "title": "关于调整三板成指样本股的公告", "theme": "index_rebalance",
         "publishDate": "2026-09-18", "noticeType": None, "fileUrl": None, "fileName": None},
        {"id": 3006227, "title": "关于沪深300等指数样本临时调整的公告", "theme": "index_rebalance",
         "publishDate": "2026-09-09", "noticeType": None, "fileUrl": None, "fileName": None},
    ]}}

DETAIL_DATA = {
    "imgList": [], "id": 3006227, "noticeStatus": None, "publishDate": "2026-09-09",
    "title": "关于沪深300等指数样本临时调整的公告", "contentSource": None,
    "content": ('<div class="weditor"><p><font face="微软雅黑" size="3">　　针对中金公司(601995)换股吸收合并'
                '东兴证券(601198)、信达证券(601059)事项,根据指数编制规则,中证指数有限公司决定自东兴证券、'
                '信达证券退市日起,对沪深300等指数进行样本调整,调整名单见附件。</font></p><p><br/></p>'
                '<p><a class="file iconfont 3002874 xlsx" href="https://oss-ch.csindex.com.cn/notice/x.xlsx">'
                '<span class="ml5">附件:指数样本调整名单</span></a></p><p style="text-align: right;">'
                '中证指数有限公司<br/>2026年9月9日</p></div>'),
    "enclosureList": [{"id": 3002874, "fileName": "附件:指数样本调整名单.xlsx",
                       "fileUrl": "https://oss-ch.csindex.com.cn/notice/x.xlsx"}],
}


def _xlsx(rows: list[list]) -> bytes:
    """2026-09-09 实测版式:行 0 分组标题(只在组首列),行 1 证券代码/简称,行 2 起数据。"""
    header0 = ["指数代码", "指数简称", "调出", None, None, None, "调入", None, None, None]
    header1 = [None, None, "证券代码", "证券简称", "证券代码", "证券简称", "证券代码", "证券简称", "证券代码", "证券简称"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([header0, header1, *rows]).to_excel(xw, sheet_name="指数样本调整名单",
                                                          header=False, index=False)
    return buf.getvalue()


_ROWS = [
    ["000009", "上证380", "601198", "东兴证券", "688303", "大全能源", "600884", "杉杉股份", "603092", "德力佳"],
    ["000300", "沪深300", "601198", "东兴证券", "601059", "信达证券", "688303", "大全能源", "-", "-"],
    [None, None, "-", "-", "-", "-", "601995", "中金公司", "-", "-"],          # 续行:承接上一指数
    ["000905", "中证500", "-", "-", "-", "-", "600884", "杉杉股份", "-", "-"],
]


def test_list_frame_normalises_dates_and_ids():
    df = csindex.list_frame(LIST_BODY["data"]["indexrebalancingAnnouncements"])
    assert list(df.columns) == csindex.LIST_COLS
    assert df.iloc[1].to_dict() == {"ann_id": "3006227", "title": "关于沪深300等指数样本临时调整的公告",
                                    "publish_date": "20260909", "theme": "index_rebalance"}


def test_parse_adjustment_xlsx_long_table():
    out = csindex.parse_adjustment_xlsx(_xlsx(_ROWS))
    assert list(out.columns) == ["index_code", "index_name", "side", "code", "name"]
    hs300 = out[out.index_code == "000300"]
    assert set(hs300[hs300.side == "drop"].code) == {"601198", "601059"}
    assert set(hs300[hs300.side == "add"].code) == {"688303", "601995"}       # 续行归到沪深300
    assert (out[out.index_code == "000009"].side == "drop").sum() == 2
    assert (out[out.index_code == "000905"].side == "add").sum() == 1
    assert "-" not in set(out.code) and out.code.str.len().eq(6).all()


def test_parse_adjustment_xlsx_rejects_unrecognised_layout():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["a", "b"]]).to_excel(xw, header=False, index=False)
    assert csindex.parse_adjustment_xlsx(buf.getvalue()).empty


def test_detail_frame_joins_header_fields_onto_every_row():
    df = csindex.detail_frame(DETAIL_DATA, _xlsx(_ROWS), "https://oss-ch.csindex.com.cn/notice/x.xlsx")
    assert list(df.columns) == csindex.DETAIL_COLS
    assert (df.ann_id == "3006227").all() and (df.publish_date == "20260909").all()
    assert "自东兴证券、信达证券退市日起" in df.content_text.iloc[0]
    assert "<" not in df.content_text.iloc[0]                                  # HTML 已剥
    assert len(df) == 2 + 4 + 1                                                # 上证380 4 + 沪深300 4 + 中证500 1
    assert (df.attachment_url == "https://oss-ch.csindex.com.cn/notice/x.xlsx").all()


def test_detail_frame_without_attachment_is_one_text_row():
    data = {**DETAIL_DATA, "enclosureList": []}
    df = csindex.detail_frame(data, None, None)
    assert len(df) == 1 and df.index_code.isna().all() and df.content_text.iloc[0]


def test_detail_frame_with_empty_attachment_raises():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["a", "b"]]).to_excel(xw, header=False, index=False)
    with pytest.raises(RuntimeError, match="附件解析为空"):
        csindex.detail_frame(DETAIL_DATA, buf.getvalue(), "u")


@pytest.mark.parametrize("text,expect", [
    ("上述调整将于2026年6月12日收市后生效。", ("20260612", "after_close")),
    ("调整方案自 2026 年 6 月 15 日起正式实施。", ("20260615", "from_date")),
    ("本次调整于2026年12月14日正式生效", ("20261214", "from_date")),
    ("中证指数有限公司决定自东兴证券、信达证券退市日起,对沪深300等指数进行样本调整", (None, None)),
    ("中证指数有限公司 2026年9月9日", (None, None)),                       # 落款日期不是生效日
])
def test_parse_effective_date(text, expect):
    assert csindex.parse_effective_date(text) == expect


class _Resp:
    def __init__(self, *, json=None, content=b""):
        self._json, self.content = json, content

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


def test_fetch_rebalance_detail_downloads_xlsx_attachment(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url == csindex.DETAIL_URL:
            return _Resp(json={"code": "200", "data": DETAIL_DATA})
        return _Resp(content=_xlsx(_ROWS))

    monkeypatch.setattr(csindex.requests, "get", fake_get)
    df = csindex.fetch_rebalance_detail("3006227")
    assert calls == [csindex.DETAIL_URL, "https://oss-ch.csindex.com.cn/notice/x.xlsx"]
    assert len(df) == 7


def test_fetch_rebalance_list_rejects_malformed_body(monkeypatch):
    monkeypatch.setattr(csindex.requests, "get", lambda url, **kw: _Resp(json={"code": "500", "data": None}))
    with pytest.raises(RuntimeError, match="形态异常"):
        csindex.fetch_rebalance_list()


def test_sources_fetch_routes_csindex(monkeypatch):
    from autoresearch.data import sources
    monkeypatch.setattr(csindex, "fetch_rebalance_list", lambda: pd.DataFrame({"ann_id": ["1"]}))
    monkeypatch.setattr(csindex, "fetch_rebalance_detail", lambda ann_id: pd.DataFrame({"ann_id": [str(ann_id)]}))
    assert sources.fetch("csindex_rebalance_list", {}).ann_id.tolist() == ["1"]
    assert sources.fetch("csindex_rebalance_detail", {"ann_id": "3006227"}).ann_id.tolist() == ["3006227"]
    with pytest.raises(ValueError):
        sources._fetch_csindex("csindex_nope", {})
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/data/test_csindex_source.py -q`
Expected: FAIL,`ImportError: cannot import name 'csindex'`。

- [ ] **Step 3: 实现 adapter**

```python
# autoresearch/data/sources/csindex.py
#!/usr/bin/env python3
"""中证指数公司公告 —— 指数样本调整(调入/调出)名单的**唯一确定性前瞻源**。

design: docs/specs/2026-09-25-index-inclusion-signal-design.md §2.1 / F7。

两个接口(2026-09-25 实测):
  GET queryAnnouncementByType?type=1&pageNum=1&pageSize=50
      → data.indexrebalancingAnnouncements[] = {id, title, theme, publishDate, noticeType, fileUrl, fileName}
        **只给最新 5 条、不分页** → 历史从上线日起自建湖累积(cache 快照键 all@today)。
  GET queryAnnouncementById?id=<id>
      → data = {id, title, publishDate, content(HTML), enclosureList[{id, fileName, fileUrl}], …}
        附件 xlsx(oss-ch.csindex.com.cn/notice/*.xlsx):行 0 =「指数代码 | 指数简称 | 调出 … | 调入 …」分组标题
        (组名只在组首列),行 1 = 每组重复「证券代码 | 证券简称」,行 2 起数据;一指数多于两个调入/调出时
        可能续行(指数代码为空 = 承接上一行);「-」= 空位。

**形态不对就抛,不返回空帧假装成功**:B 级契约 + `persist_violations=False` 在上层负责「断采只损失当日、
不阻断」,但那要求断采是显式的——静默空帧会被当成「这次没调样」。
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

LIST_URL = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementByType"
DETAIL_URL = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.csindex.com.cn/"}
_TIMEOUT = 20          # 夜跑必须有超时:一次挂起的连接会拖死整个 prelude

LIST_COLS = ["ann_id", "title", "publish_date", "theme"]
DETAIL_COLS = ["ann_id", "publish_date", "title", "content_text", "attachment_url",
               "index_code", "index_name", "side", "code", "name"]
TABLE_COLS = ["index_code", "index_name", "side", "code", "name"]
_SIDE_OF = {"调出": "drop", "调入": "add"}


def _compact_date(s) -> str:
    return str(s or "").replace("-", "")[:8]


def list_frame(items: list[dict]) -> pd.DataFrame:
    rows = [{"ann_id": str(it.get("id")), "title": str(it.get("title") or ""),
             "publish_date": _compact_date(it.get("publishDate")), "theme": it.get("theme")}
            for it in items]
    return pd.DataFrame(rows, columns=LIST_COLS)


def fetch_rebalance_list(timeout: int = _TIMEOUT) -> pd.DataFrame:
    r = requests.get(LIST_URL, params={"type": 1, "pageNum": 1, "pageSize": 50},
                     headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    body = r.json() or {}
    items = (body.get("data") or {}).get("indexrebalancingAnnouncements")
    if not isinstance(items, list):
        raise RuntimeError(f"中证公告列表形态异常(indexrebalancingAnnouncements 不是 list):{str(body)[:160]}")
    return list_frame(items)


def html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[　\xa0]+", " ", text).strip()


def _clean(v) -> str | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def parse_adjustment_xlsx(raw: bytes) -> pd.DataFrame:
    """附件 → 长表 `TABLE_COLS`。认不出版式(行数 <3 或列数 <4 或无「调出/调入」组)→ 空帧(由调用方判定)。"""
    sheet = pd.read_excel(io.BytesIO(raw), header=None, dtype=str)
    if sheet.shape[0] < 3 or sheet.shape[1] < 4:
        return pd.DataFrame(columns=TABLE_COLS)
    groups = [str(g).strip() if _clean(g) else None for g in sheet.iloc[0].ffill().tolist()]
    kinds = [str(k) if _clean(k) else "" for k in sheet.iloc[1].tolist()]
    rows: list[dict] = []
    last_index: tuple[str | None, str | None] = (None, None)
    for _, r in sheet.iloc[2:].iterrows():
        idx_code, idx_name = _clean(r.iloc[0]), _clean(r.iloc[1])
        if idx_code:
            last_index = (idx_code.zfill(6), idx_name)
        elif last_index[0] is None:
            continue
        j = 2
        while j < sheet.shape[1]:
            side = _SIDE_OF.get(groups[j] or "")
            if side and "代码" in kinds[j]:
                code = _clean(r.iloc[j])
                name = _clean(r.iloc[j + 1]) if j + 1 < sheet.shape[1] else None
                if code and code != "-":
                    rows.append({"index_code": last_index[0], "index_name": last_index[1],
                                 "side": side, "code": code.zfill(6), "name": name})
                j += 2
            else:
                j += 1
    return pd.DataFrame(rows, columns=TABLE_COLS)


def detail_frame(data: dict, xlsx_bytes: bytes | None, attachment_url: str | None) -> pd.DataFrame:
    base = {"ann_id": str(data.get("id")), "publish_date": _compact_date(data.get("publishDate")),
            "title": str(data.get("title") or ""), "content_text": html_to_text(str(data.get("content") or "")),
            "attachment_url": attachment_url}
    if xlsx_bytes is None:
        return pd.DataFrame([{**base, "index_code": None, "index_name": None, "side": None,
                              "code": None, "name": None}], columns=DETAIL_COLS)
    table = parse_adjustment_xlsx(xlsx_bytes)
    if table.empty:
        raise RuntimeError(f"中证公告 {base['ann_id']} 附件解析为空 —— 多半是附件版式改了,不是「这次没调样」")
    return pd.DataFrame([{**base, **row} for row in table.to_dict("records")], columns=DETAIL_COLS)


def fetch_rebalance_detail(ann_id: str | int, timeout: int = _TIMEOUT) -> pd.DataFrame:
    r = requests.get(DETAIL_URL, params={"id": ann_id}, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = (r.json() or {}).get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"中证公告详情 id={ann_id} 形态异常(data 不是 object)")
    encl = [e for e in (data.get("enclosureList") or [])
            if str(e.get("fileUrl") or "").lower().endswith(".xlsx")]
    raw, url = None, None
    if encl:
        url = encl[0]["fileUrl"]
        rr = requests.get(url, headers=_HEADERS, timeout=timeout)
        rr.raise_for_status()
        raw = rr.content
    return detail_frame(data, raw, url)


_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def parse_effective_date(content_text: str) -> tuple[str | None, str | None]:
    """正文 → (YYYYMMDD, kind)。

    kind = "after_close":「X 日收市后生效」→ 被动调仓收盘日 = X;
           "from_date"  :「X 日起生效 / 正式实施」→ 被动调仓收盘日 = X 的前一交易日(由调用方查交易日历);
           None         :解析不出(如「自退市日起」;落款日期不算)。
    """
    for m in _DATE_RE.finditer(content_text):
        tail = content_text[m.end(): m.end() + 12]
        d = f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        if "收市后" in tail or "收盘后" in tail:
            return d, "after_close"
        if re.match(r"\s*起?\s*(正式)?\s*(生效|实施)", tail):
            return d, "from_date"
    return None, None
```

- [ ] **Step 4: 路由(`autoresearch/data/sources/__init__.py`)**

在 `fetch()` 的 `if src == "eastmoney":` 分支之后加:

```python
    if src == "csindex":
        return _fetch_csindex(endpoint, params)
```

在 `_fetch_eastmoney` 之后加:

```python
def _fetch_csindex(endpoint: str, params: dict) -> pd.DataFrame:
    """中证指数公司公告(自采;design 2026-09-25 §2.1)。列表零参数;详情按 `ann_id`。"""
    from autoresearch.data.sources import csindex

    if endpoint == "csindex_rebalance_list":
        return csindex.fetch_rebalance_list()
    if endpoint == "csindex_rebalance_detail":
        return csindex.fetch_rebalance_detail(params["ann_id"])
    raise ValueError(f"unknown csindex endpoint {endpoint!r}")
```

并把模块 docstring 的路由列表补一行 `csindex → autoresearch.data.sources.csindex(公告列表/详情/附件名单)`。

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest tests/data/test_csindex_source.py -q`
Expected: 全部 PASS。

- [ ] **Step 6: 真源冒烟(一次,不进测试)**

Run: `uv run --no-sync python -c "from autoresearch.data.sources import csindex as c; l=c.fetch_rebalance_list(); print(l.to_string()); d=c.fetch_rebalance_detail(l.ann_id.iloc[0]); print(d.head(3).to_string(), len(d))"`
Expected: 列表 5 行;详情若带 xlsx 则长表行数 > 0。若接口不可达,记录到提交说明,不阻塞。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/data/sources/csindex.py autoresearch/data/sources/__init__.py tests/data/test_csindex_source.py
git commit -m "feat(data): csindex source adapter — rebalance announcement list, detail and xlsx roster parser

Announcement text → effective-close date (after_close / from_date / none);
xlsx roster → long table (index, side, code). Offline fixtures mirror the
2026-09-25 probe (design 2026-09-25 F7).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: 事件表 `scan/index_events.py`(先登记 ARTIFACTS)

**Files:**
- Modify: `autoresearch/contracts/artifacts.py`(`# ---- prelude` 段,`calendar` 条目之后追加)
- Create: `autoresearch/scan/index_events.py`
- Test: `tests/scan/test_index_events.py`(新)
- Test: `tests/contracts/test_registry_parity.py`(现有 hygiene 测试自动覆盖新登记;不改)

**Interfaces:**
- Consumes(Task 1/2):`cache.get_or_fetch("csindex_rebalance_list", {}, today=, fetch=)`、`cache.get_or_fetch("csindex_rebalance_detail", {"ann_id": ...}, today=, fetch=)`、`csindex.parse_effective_date`。
- Produces(Task 5/7/8/9/11 消费):
  - `INDEX_EVENTS_FILENAME = "index_events.csv"`;`EVENT_COLS = ["code","index_code","index_name","side","ann_date","eff_close_date","phase","source","flow_adv_days"]`;`INDEX_WHITELIST: dict[str, str]`(六位指数代码 → 中文名);`PHASES`。
  - `second_friday(year, month) -> "YYYYMMDD"`;`rule_eff_close_date(ann_date) -> str | None`;`prev_trading_day(day, trading_days) -> str | None`;`eff_close_from(parsed_date, kind, ann_date, trading_days) -> tuple[str | None, str]`(第二元 ∈ `{"csindex","rule","none"}`)。
  - `phase_for(scan_date, ann_date, eff_close_date, trading_days) -> str | None`(`None` = 不在窗口内,行不落表)。
  - `trading_days_window(scan_date, *, before=40, after=40) -> tuple[list[str], str]`(第二元 `"trade_cal"` / `"weekday_approx"`)。
  - `build_index_events(scan_date, *, today=None, fetch_list=None, fetch_detail=None, trading_days=None) -> pd.DataFrame | None`(`None` = 源不可达且已 `record_degradation`;空帧 = 源可达无窗口内事件)。
  - `write_index_events(scan_dir, df) -> Path`;`load_index_events(scan_dir) -> pd.DataFrame | None`(缺文件 → `None`);`events_by_code(df) -> dict[str, list[dict]] | None`;`harvest_index_events(scan_date, scan_dir, **kw) -> pd.DataFrame | None`。

- [ ] **Step 1: 登记产物(`autoresearch/contracts/artifacts.py`,紧跟 `Artifact("calendar", ...)` 那一行之后)**

```python
    Artifact("index_events", "index_events.csv", "staging", "prelude", "calendar", "csv", "gated",
             required_when="calendar.index_rebalance 开且中证公告源可达(design 2026-09-25 §2.2)"),
```

Run: `uv run --no-sync python -m pytest tests/contracts/test_registry_parity.py -q` → Expected: PASS(登记 hygiene 过;此时尚无字面量使用者)。

- [ ] **Step 2: 写失败测试**

```python
# tests/scan/test_index_events.py
"""指数调样事件表(design 2026-09-25 §2.2):相位、生效日推导、白名单、缺席≠否。全部离线(注入假取数)。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts
from autoresearch.scan import index_events as ie

TDS = ["20261127", "20261130", "20261201", "20261202", "20261203", "20261204", "20261207", "20261208",
       "20261209", "20261210", "20261211", "20261214", "20261215", "20261216", "20261217", "20261218"]
A, E = "20261127", "20261211"


@pytest.fixture(autouse=True)
def _clean():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    monkeypatch.setattr(cache, "_real_today", lambda: "20261210")
    return root


def test_second_friday_and_rule_dates():
    assert ie.second_friday(2026, 12) == "20261211" and ie.second_friday(2026, 6) == "20260612"
    assert ie.rule_eff_close_date("20261127") == "20261211"
    assert ie.rule_eff_close_date("20260529") == "20260612"
    assert ie.rule_eff_close_date("20260909") is None          # 临时调整无规则日


def test_eff_close_from_three_sources():
    assert ie.eff_close_from("20261211", "after_close", A, TDS) == ("20261211", "csindex")
    assert ie.eff_close_from("20261214", "from_date", A, TDS) == ("20261211", "csindex")   # 起生效 → 前一交易日
    assert ie.eff_close_from(None, None, A, TDS) == ("20261211", "rule")                  # 11 月公告 → 规则兜底
    assert ie.eff_close_from(None, None, "20260909", TDS) == (None, "none")


@pytest.mark.parametrize("scan_date,expect", [
    ("2026-11-26", None),                        # 公告前 → 不落表
    ("2026-11-27", "announced_runup"),
    ("2026-12-09", "announced_runup"),           # E−2
    ("2026-12-10", "passive_close_eve"),         # E−1:守卫相位
    ("2026-12-11", "effective"),
    ("2026-12-16", "post"),                      # E+3
    ("2026-12-17", None),                        # E+4 → 出窗
])
def test_phase_for(scan_date, expect):
    assert ie.phase_for(scan_date, A, E, TDS) == expect


def test_phase_unknown_eff_when_no_effective_date():
    assert ie.phase_for("2026-12-10", A, None, TDS) == "unknown_eff"


def _list(items):
    return lambda endpoint, params: pd.DataFrame(items, columns=ie_list_cols())


def ie_list_cols():
    from autoresearch.data.sources.csindex import LIST_COLS
    return LIST_COLS


def _detail_rows(ann_id, publish_date, content, rows):
    from autoresearch.data.sources.csindex import DETAIL_COLS
    base = {"ann_id": ann_id, "publish_date": publish_date, "title": "关于调整指数样本的公告",
            "content_text": content, "attachment_url": "u"}
    return pd.DataFrame([{**base, **r} for r in rows], columns=DETAIL_COLS)


ROSTER = [
    {"index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"},
    {"index_code": "000300", "index_name": "沪深300", "side": "drop", "code": "600188", "name": "兖矿能源"},
    {"index_code": "000905", "index_name": "中证500", "side": "add", "code": "600188", "name": "兖矿能源"},   # 跨指数迁移:同票两行
    {"index_code": "000009", "index_name": "上证380", "side": "add", "code": "603092", "name": "德力佳"},      # 非白名单
]


def _fetch_detail(ann_id_to_frame):
    return lambda endpoint, params: ann_id_to_frame[str(params["ann_id"])]


def test_build_filters_whitelist_and_labels_phase(lake):
    fl = _list([["3007001", "关于调整沪深300、中证500等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127",
                                                "上述调整将于2026年12月11日收市后生效。", ROSTER)})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert list(df.columns) == ie.EVENT_COLS
    assert set(df.index_code) == {"000300", "000905"}                       # 上证380 行不进表
    assert len(df) == 3 and (df.phase == "passive_close_eve").all()
    assert (df.eff_close_date == "20261211").all() and (df.source == "csindex").all()
    migrate = df[df.code == "600188"]
    assert set(migrate.side) == {"drop", "add"}                              # 迁移不合并,两行都在
    assert df.flow_adv_days.isna().all()                                     # B3 之前恒空 = 未计算


def test_build_uses_rule_date_when_text_has_no_date(lake):
    fl = _list([["3007002", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007002": _detail_rows("3007002", "20261127", "调整名单见附件。", ROSTER[:1])})
    df = ie.build_index_events("2026-12-01", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0].to_dict()["eff_close_date"] == "20261211" and df.iloc[0]["source"] == "rule"
    assert df.iloc[0]["phase"] == "announced_runup"


def test_build_unknown_eff_row_is_kept_with_its_own_phase(lake):
    fl = _list([["3006227", "关于沪深300等指数样本临时调整的公告", "20260909", "index_rebalance"]])
    fd = _fetch_detail({"3006227": _detail_rows("3006227", "20260909", "自东兴证券、信达证券退市日起调整", ROSTER[:1])})
    df = ie.build_index_events("2026-09-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0]["phase"] == "unknown_eff" and pd.isna(df.iloc[0]["eff_close_date"])


def test_build_returns_empty_frame_when_no_whitelist_events(lake):
    fl = _list([["3006244", "关于调整三板成指样本股的公告", "20260918", "index_rebalance"]])
    fd = _fetch_detail({"3006244": _detail_rows("3006244", "20260918", "x", ROSTER[3:])})
    df = ie.build_index_events("2026-09-20", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df is not None and df.empty and list(df.columns) == ie.EVENT_COLS     # 源可达无事件 = 空表,不是 None


def test_build_returns_none_and_records_degradation_when_list_unreachable(lake):
    def boom(endpoint, params):
        raise RuntimeError("csindex down")
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=boom, trading_days=TDS)
    assert df is None
    assert any(r["endpoint"] == "csindex_rebalance_list" for r in contracts.degradations())


def test_build_on_past_date_reuses_latest_list_snapshot_instead_of_fetching(lake):
    calls = []

    def fl(endpoint, params):
        calls.append(1)
        return pd.DataFrame([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]],
                            columns=ie_list_cols())
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127", "x", ROSTER[:1])})
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    df = ie.build_index_events("2026-12-01", today="20261201", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert len(calls) == 1 and len(df) == 1                                  # 补跑:读湖里最新快照,不取网


def test_detail_is_fetched_once_per_announcement(lake):
    calls = []
    fl = _list([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])

    def fd(endpoint, params):
        calls.append(params["ann_id"])
        return _detail_rows("3007001", "20261127", "x", ROSTER[:1])
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert calls == ["3007001"]                                              # 第二次命中 <ann_id>@*.parquet


def test_write_load_by_code_and_absence_semantics(tmp_path):
    d = tmp_path / "2026-12-10"
    d.mkdir()
    assert ie.load_index_events(d) is None and ie.events_by_code(None) is None      # 缺文件 = 缺席
    ie.write_index_events(d, pd.DataFrame(columns=ie.EVENT_COLS))
    empty = ie.load_index_events(d)
    assert empty is not None and empty.empty and ie.events_by_code(empty) == {}     # 空表 = 可达无事件
    rows = pd.DataFrame([{"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add",
                          "ann_date": A, "eff_close_date": E, "phase": "passive_close_eve",
                          "source": "csindex", "flow_adv_days": None}], columns=ie.EVENT_COLS)
    ie.write_index_events(d, rows)
    by = ie.events_by_code(ie.load_index_events(d))
    assert list(by) == ["600221"] and by["600221"][0]["phase"] == "passive_close_eve"
    assert by["600221"][0]["eff_close_date"] == E                                  # 读回仍是字符串


def test_trading_days_window_falls_back_to_weekdays_and_records_it(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    days, source = ie.trading_days_window("2026-12-10", before=7, after=7)
    assert source == "weekday_approx" and "20261210" in days and "20261212" not in days   # 周六不在
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())
```

- [ ] **Step 3: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_index_events.py -q`
Expected: FAIL,`ModuleNotFoundError: autoresearch.scan.index_events`。

- [ ] **Step 4: 实现模块**

```python
# autoresearch/scan/index_events.py
#!/usr/bin/env python3
"""指数调样事件表 `index_events.csv`(确定性,零 LLM;design 2026-09-25 §2.2)。

一行 = (票, 指数, 调入|调出)。相位按**扫描日 D** 算:
  announced_runup    A ≤ D ≤ E−2         事实日期,不作论点
  passive_close_eve  D = E−1(E 的前一交易日)   ← E6 硬门 `rebalance_close` 命中的唯一相位
  effective          D = E
  post               E < D ≤ E+3
  unknown_eff        生效日解析不出(如「自退市日起」)
E = 被动调仓的那个收盘日:「X 日收市后生效」→ X;「X 日起生效/实施」→ X 的前一交易日;半年定期调样
(公告在 5/11 月)规则兜底 = 次月第二个周五。

六指数白名单是**产品选择**:附件里其它指数(上证380/三板…)入湖不进表。

**缺席 ≠ 否**:源不可达 → 返回 None + `record_degradation`(调用方不写文件);源可达但无窗口内事件 →
只有表头的空帧(写成只有表头的文件)。跨指数迁移(沪深300 调出 = 中证500 调入)**两行都保留**。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

INDEX_EVENTS_FILENAME = "index_events.csv"
INDEX_WHITELIST: dict[str, str] = {
    "000300": "沪深300", "000905": "中证500", "000852": "中证1000",
    "000510": "中证A500", "000688": "科创50", "399006": "创业板指",
}
EVENT_COLS = ["code", "index_code", "index_name", "side", "ann_date", "eff_close_date",
              "phase", "source", "flow_adv_days"]
PHASES = ("announced_runup", "passive_close_eve", "effective", "post", "unknown_eff")
POST_WINDOW = 3          # 生效后仍展示 3 个交易日(事实,不作论点)
_LIST_EP = "csindex_rebalance_list"
_DETAIL_EP = "csindex_rebalance_detail"


# ── 日期原语 ────────────────────────────────────────────────────────────────
def second_friday(year: int, month: int) -> str:
    first = dt.date(year, month, 1)
    fridays = [first + dt.timedelta(i) for i in range(31)
               if (first + dt.timedelta(i)).month == month and (first + dt.timedelta(i)).weekday() == 4]
    return fridays[1].strftime("%Y%m%d")


def rule_eff_close_date(ann_date: str) -> str | None:
    """半年定期调样兜底:5 月公告 → 6 月第二个周五;11 月公告 → 12 月第二个周五;其它月份无规则。"""
    y, m = int(ann_date[:4]), int(ann_date[4:6])
    if m == 5:
        return second_friday(y, 6)
    if m == 11:
        return second_friday(y, 12)
    return None


def prev_trading_day(day: str, trading_days: list[str]) -> str | None:
    earlier = [d for d in trading_days if d < day]
    return earlier[-1] if earlier else None


def eff_close_from(parsed_date: str | None, kind: str | None, ann_date: str,
                   trading_days: list[str]) -> tuple[str | None, str]:
    """→ (被动调仓收盘日, source)。source ∈ {csindex, rule, none}。"""
    if parsed_date and kind == "after_close":
        return parsed_date, "csindex"
    if parsed_date and kind == "from_date":
        return prev_trading_day(parsed_date, trading_days), "csindex"
    rule = rule_eff_close_date(ann_date)
    return (rule, "rule") if rule else (None, "none")


def phase_for(scan_date: str, ann_date: str, eff_close_date: str | None,
              trading_days: list[str]) -> str | None:
    day = scan_date.replace("-", "")[:8]
    if day < ann_date:
        return None
    if not eff_close_date:
        return "unknown_eff"
    e_minus_1 = prev_trading_day(eff_close_date, trading_days)
    later = [d for d in trading_days if d > eff_close_date]
    e_plus = later[POST_WINDOW - 1] if len(later) >= POST_WINDOW else (later[-1] if later else eff_close_date)
    if day == e_minus_1:
        return "passive_close_eve"
    if day == eff_close_date:
        return "effective"
    if day < eff_close_date:
        return "announced_runup"
    if day <= e_plus:
        return "post"
    return None


def trading_days_window(scan_date: str, *, before: int = 40, after: int = 40) -> tuple[list[str], str]:
    """扫描日前后的交易日(含未来,E 常在湖之外)。tushare trade_cal 不可达 → 工作日近似 + 记降级。"""
    d0 = dt.datetime.strptime(scan_date[:10], "%Y-%m-%d").date()
    start = (d0 - dt.timedelta(days=before)).strftime("%Y%m%d")
    end = (d0 + dt.timedelta(days=after)).strftime("%Y%m%d")
    try:
        from autoresearch.data.tushare_source import _pro, _trade_days
        return _trade_days(_pro(), start, end), "trade_cal"
    except Exception as e:  # noqa: BLE001 — 无 token / 限频 / 断网:降级必须可见
        from autoresearch.data.contracts import record_degradation
        record_degradation("trade_cal", f"交易日历不可达({e!r})→ 调样相位按工作日近似", key=scan_date)
        days = [(dt.datetime.strptime(start, "%Y%m%d").date() + dt.timedelta(i)) for i in range(before + after + 1)]
        return [d.strftime("%Y%m%d") for d in days if d.weekday() < 5], "weekday_approx"


# ── 取数(经 cache;两条脆源路径全部 try,失败记账) ───────────────────────────
def _looks_like_sample_adjustment(title: str) -> bool:
    return "样本" in title and any(k in title for k in ("调整", "调入", "调出"))


def _latest_list_snapshot() -> pd.DataFrame | None:
    from autoresearch.data import cache
    files = sorted((cache.LAKE / _LIST_EP).glob("all@*.parquet"))
    return pd.read_parquet(files[-1]) if files else None


def _load_detail(ann_id: str, today: str, fetch_detail) -> pd.DataFrame | None:
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    cached = sorted((cache.LAKE / _DETAIL_EP).glob(f"{ann_id}@*.parquet"))
    if cached:
        return pd.read_parquet(cached[-1])                     # 公告不可变:任一份留底都算
    try:
        return cache.get_or_fetch(_DETAIL_EP, {"ann_id": ann_id}, today=today, fetch=fetch_detail)
    except Exception as e:  # noqa: BLE001
        record_degradation(_DETAIL_EP, f"公告 {ann_id} 详情取数失败({type(e).__name__}: {e})", key=ann_id)
        return None


def build_index_events(scan_date: str, *, today: str | None = None, fetch_list=None, fetch_detail=None,
                       trading_days: list[str] | None = None) -> pd.DataFrame | None:
    """None = 源不可达(已记降级;调用方不落文件);空帧 = 源可达、六指数无窗口内事件。"""
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    from autoresearch.data.sources.csindex import parse_effective_date

    day = scan_date.replace("-", "")[:8]
    as_of = today or day
    try:
        lst = cache.get_or_fetch(_LIST_EP, {}, today=as_of, fetch=fetch_list)
    except cache.SnapshotDateError:
        lst = _latest_list_snapshot()                          # 补跑/回放:只读湖里最新快照
        if lst is None:
            record_degradation(_LIST_EP, "补跑日无湖内列表快照,快照接口不得写假历史", key=day)
            return None
    except Exception as e:  # noqa: BLE001
        record_degradation(_LIST_EP, f"取数失败({type(e).__name__}: {e})", key=day)
        return None
    if lst is None or lst.empty:
        return pd.DataFrame(columns=EVENT_COLS)

    tds = trading_days if trading_days is not None else trading_days_window(scan_date)[0]
    rows: list[dict] = []
    for ann in lst.sort_values("publish_date", ascending=False).itertuples(index=False):
        if not _looks_like_sample_adjustment(str(ann.title)):
            continue
        detail = _load_detail(str(ann.ann_id), as_of, fetch_detail)
        if detail is None or detail.empty:
            continue
        head = detail.iloc[0]
        ann_date = str(head["publish_date"])
        parsed, kind = parse_effective_date(str(head["content_text"] or ""))
        eff, source = eff_close_from(parsed, kind, ann_date, tds)
        phase = phase_for(day, ann_date, eff, tds)
        if phase is None:
            continue
        for r in detail.itertuples(index=False):
            idx = str(r.index_code or "")[:6]
            if idx not in INDEX_WHITELIST or not r.code:
                continue
            rows.append({"code": str(r.code).zfill(6), "index_code": idx, "index_name": INDEX_WHITELIST[idx],
                         "side": r.side, "ann_date": ann_date, "eff_close_date": eff, "phase": phase,
                         "source": source, "flow_adv_days": None})
    return pd.DataFrame(rows, columns=EVENT_COLS)


# ── 落盘 / 读回 ─────────────────────────────────────────────────────────────
def write_index_events(scan_dir: Path | str, df: pd.DataFrame) -> Path:
    p = Path(scan_dir) / INDEX_EVENTS_FILENAME
    df.reindex(columns=EVENT_COLS).to_csv(p, index=False)
    return p


def load_index_events(scan_dir: Path | str) -> pd.DataFrame | None:
    """缺文件 → None(源不可达 / 旋钮关);只有表头 → 空帧(源可达无事件)。字符串列不让 pandas 猜数字。"""
    p = Path(scan_dir) / INDEX_EVENTS_FILENAME
    if not p.exists():
        return None
    return pd.read_csv(p, dtype={"code": str, "index_code": str, "ann_date": str, "eff_close_date": str,
                                 "phase": str, "side": str, "source": str})


def events_by_code(df: pd.DataFrame | None) -> dict[str, list[dict]] | None:
    if df is None:
        return None
    out: dict[str, list[dict]] = {}
    for r in df.to_dict("records"):
        out.setdefault(str(r["code"]).zfill(6), []).append(r)
    return out


def harvest_index_events(scan_date: str, scan_dir: Path | str, **kw) -> pd.DataFrame | None:
    """build → 落 `index_events.csv`(源不可达时**不落文件**,让缺席保持可读)。"""
    df = build_index_events(scan_date, **kw)
    if df is not None:
        write_index_events(scan_dir, df)
    else:
        print(f"[index_events] {scan_date} 中证公告源不可达 → 不落 {INDEX_EVENTS_FILENAME}(已记降级)",
              file=sys.stderr)
    return df


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="指数调样事件表(六指数;网络:中证公告)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    a = ap.parse_args(argv)
    d = ws.scan_root() / a.date
    d.mkdir(parents=True, exist_ok=True)
    df = harvest_index_events(a.date, d)
    print("源不可达" if df is None else f"{len(df)} 行 → {d / INDEX_EVENTS_FILENAME}")
    return 0 if df is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_index_events.py tests/contracts/test_registry_parity.py -q`
Expected: 全部 PASS(`test_registry_parity` 的字面量守卫此时因 `index_events.csv` 已登记而绿)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/contracts/artifacts.py autoresearch/scan/index_events.py tests/scan/test_index_events.py
git commit -m "feat(scan): index_events.csv — six-index rebalance roster with scan-day phase (registered before its producer)

phase ∈ announced_runup / passive_close_eve / effective / post / unknown_eff;
source absent → no file + degradation, source ok w/o events → header-only file.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: 普查 CLI 产品化 `research/index_rebalance_census.py`(验收 O8)

**Files:**
- Create: `autoresearch/research/index_rebalance_census.py`
- Test: `tests/research/test_index_rebalance_census.py`(新)

**Interfaces:**
- Consumes:`cache.get_or_fetch("index_weight", {"index_code", "start_date", "end_date"}, today=<月末>, fetch=)`;`autoresearch.data.market_panel.lake_trade_days`;`autoresearch.scan.index_events.second_friday`。
- Produces:`run_census(start="2022-06", end="2026-06", *, lake_daily=None, fetch=None) -> dict`(键 `events / n_obs / tables{"add","drop"}[phase] = {n, n_cells, exc_pp, hit_pct, t_obs, t_cell}` / `by_index`);`render(doc) -> str`;`main(argv) -> int`(写 `$RPT/research/index_rebalance_census.md` + `_index_rebalance_census.json`)。**只读**,不写 run 目录 / staging / lake 以外的任何生产路径。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_index_rebalance_census.py
"""指数调样隔夜尺普查 CLI(design 2026-09-25 附录 A 的可复现版):合成湖 + 假 index_weight,零网络。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache
from autoresearch.research import index_rebalance_census as cen

# 2026-06 调样:A=05-29(周五),E=06-12(第二个周五)。合成 22 个交易日,只做 6 月这一段。
DAYS = ["20260525", "20260526", "20260527", "20260528", "20260529", "20260601", "20260602", "20260603",
        "20260604", "20260605", "20260608", "20260609", "20260610", "20260611", "20260612", "20260615",
        "20260616", "20260617", "20260618", "20260619", "20260622", "20260623"]
ADD, OLD1, OLD2 = "600221.SH", "600000.SH", "600036.SH"


def _lake(tmp_path, monkeypatch):
    daily = tmp_path / "lake" / "daily"
    daily.mkdir(parents=True)
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    for d in DAYS:
        px = {OLD1: 10.0, OLD2: 20.0, ADD: 30.0}
        rows = []
        for code, p in px.items():
            open_ = p
            if code == ADD and d == "20260615":        # 生效日次日:调入票开盘 −2%(被动收盘后回吐)
                open_ = p * 0.98
            rows.append({"ts_code": code, "open": open_, "high": p, "low": p, "close": p,
                         "pre_close": p, "change": 0.0, "pct_chg": 0.0, "vol": 1.0, "amount": 1000.0})
        pd.DataFrame(rows).to_parquet(daily / f"{d}.parquet")
    return daily


def _fetch_index_weight(endpoint, params):
    end = params["end_date"]
    members = [OLD1, OLD2] + ([ADD] if end >= "20260601" else [])
    return pd.DataFrame({"index_code": params["index_code"], "con_code": members,
                         "trade_date": end[:6] + ("29" if end < "20260601" else "30"), "weight": 1.0})


def test_census_labels_e_minus_1_negative_for_the_add(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"})
    assert doc["events"] == [{"A": "20260529", "E": "20260612", "indexes": ["沪深300"]}]
    add_tbl = doc["tables"]["add"]
    assert set(add_tbl) >= {"1.A-1", "2.A", "3.run", "5.E-1", "6.E"}
    assert add_tbl["5.E-1"]["n"] == 1 and add_tbl["5.E-1"]["exc_pp"] == pytest.approx(-2.0, abs=0.05)
    assert add_tbl["2.A"]["exc_pp"] == pytest.approx(0.0, abs=1e-9)
    assert doc["by_index"]["沪深300"]["add"]["5.E-1"]["n"] == 1


def test_render_mentions_phase_table_and_caveats(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"})
    md = cen.render(doc)
    assert "5.E-1" in md and "同指数未变动成分股" in md and "未扣成本" in md


def test_main_writes_only_under_research_report_root(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    out = tmp_path / "research" / "index_rebalance_census.md"
    monkeypatch.setattr(cen, "_default_fetch", lambda: _fetch_index_weight)
    rc = cen.main(["--start", "2026-06", "--end", "2026-06", "--lake-daily", str(daily), "--out", str(out),
                   "--index", "000300.SH=沪深300"])
    assert rc == 0 and out.exists() and (out.parent / "_index_rebalance_census.json").exists()
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/research/test_index_rebalance_census.py -q`
Expected: FAIL,`ModuleNotFoundError`。

- [ ] **Step 3: 实现**

```python
# autoresearch/research/index_rebalance_census.py
#!/usr/bin/env python3
"""指数调样事件 · 隔夜尺普查(design 2026-09-25 附录 A 的可复现版;**只读**,不进生产)。

问题:扫描日 D 落在一次指数调样的哪一段,调入/调出票在主尺 `gap_c1_o2`(D+1 收盘买 → D+2 开盘卖)上
相对**同指数未变动成分股**是正是负?

口径(预注册,跑前锁死):
  事件日按规则推:生效 E = 6/12 月第二个周五(科创50 另 3/9 月),公告 A = E − 14 天;
  调入/调出 = tushare `index_weight` 相邻月末快照之差(经 cache,一指数一月一份);
  收益 = `lake/daily`;对照 = 同指数未变动成分股当日等权 gap;
  相位:0.A-2 / 1.A-1(预测夜)/ 2.A(公告夜)/ 3.run(A+1..E-3)/ 4.E-2 / 5.E-1(买 E 收 → 卖 E+1 开)/ 6.E / 7.post(E+1..E+3);
  t_cell 按(调样, 指数)格聚合(股票行不是独立样本)。
边界:月末快照含临时调整污染;未扣成本;单一大周期。读数只回答「守卫该不该开」,不回答「能不能赚」。

  uv run --no-sync python -m autoresearch.research.index_rebalance_census --start 2022-06 --end 2026-06
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.market_panel import lake_trade_days
from autoresearch.scan.index_events import second_friday

INDEXES: dict[str, str] = {"000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
                           "000510.SH": "中证A500", "000688.SH": "科创50", "399006.SZ": "创业板指"}
QUARTERLY = {"000688.SH"}
PHASES = ("0.A-2", "1.A-1", "2.A", "3.run", "4.E-2", "5.E-1", "6.E", "7.post")
POST = 3


def _default_fetch():
    return None                     # None → cache.get_or_fetch 走 sources.fetch(真 tushare)


def _month_iter(start: str, end: str):
    y, m = int(start[:4]), int(start[5:7])
    ye, me = int(end[:4]), int(end[5:7])
    while (y, m) <= (ye, me):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def rebalance_events(start: str, end: str, indexes: dict[str, str]) -> list[dict]:
    """[{A, E, index_codes}]:6/12 月全体;3/9 月只季度指数。"""
    out = []
    for y, m in _month_iter(start, end):
        if m in (6, 12):
            codes = list(indexes)
        elif m in (3, 9):
            codes = [c for c in indexes if c in QUARTERLY]
        else:
            continue
        if not codes:
            continue
        e = second_friday(y, m)
        a = (dt.datetime.strptime(e, "%Y%m%d").date() - dt.timedelta(days=14)).strftime("%Y%m%d")
        out.append({"A": a, "E": e, "index_codes": codes})
    return out


def constituents(index_code: str, year: int, month: int, fetch=None) -> set[str]:
    """该指数该月月末快照的成分集(经湖:键 <index_code>@<月末>,一指数一月一份)。"""
    import calendar as _cal

    from autoresearch.data import cache
    start = f"{year}{month:02d}01"
    end = f"{year}{month:02d}{_cal.monthrange(year, month)[1]:02d}"
    df = cache.get_or_fetch("index_weight", {"index_code": index_code, "start_date": start, "end_date": end},
                            today=end, fetch=fetch)
    if df is None or df.empty:
        return set()
    last = df["trade_date"].astype(str).max()
    return set(df[df["trade_date"].astype(str) == last]["con_code"].astype(str))


def _phase(i: int, i_a: int, i_e: int) -> str | None:
    oa, oe = i - i_a, i - i_e
    if oa == -2: return "0.A-2"
    if oa == -1: return "1.A-1"
    if oa == 0: return "2.A"
    if oe <= -3: return "3.run"
    if oe == -2: return "4.E-2"
    if oe == -1: return "5.E-1"
    if oe == 0: return "6.E"
    if 1 <= oe <= POST: return "7.post"
    return None


def _load_pivots(lake_daily: Path | None, days: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    frames = []
    for day in days:
        fp = d / f"{day}.parquet"
        if fp.exists():
            f = pd.read_parquet(fp, columns=["ts_code", "open", "close"])
            f["trade_date"] = day
            frames.append(f)
    px = pd.concat(frames, ignore_index=True)
    op = px.pivot(index="trade_date", columns="ts_code", values="open").sort_index()
    cl = px.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    return op, cl


def _stats(obs: pd.DataFrame) -> dict:
    out = {}
    for ph, g in obs.groupby("phase"):
        n = len(g)
        mean = float(g.exc.mean() * 100)
        t_obs = float(mean / (g.exc.std(ddof=1) * 100 / np.sqrt(n))) if n > 2 and g.exc.std(ddof=1) > 0 else None
        cells = g.groupby(["E", "index"]).exc.mean()
        t_cell = (float(cells.mean() / (cells.std(ddof=1) / np.sqrt(len(cells))))
                  if len(cells) > 2 and cells.std(ddof=1) > 0 else None)
        out[ph] = {"n": int(n), "n_cells": int(len(cells)), "exc_pp": round(mean, 3),
                   "hit_pct": round(float((g.exc > 0).mean() * 100), 1),
                   "t_obs": None if t_obs is None else round(t_obs, 2),
                   "t_cell": None if t_cell is None else round(t_cell, 2)}
    return out


def run_census(start: str = "2022-06", end: str = "2026-06", *, lake_daily: Path | None = None,
               fetch=None, indexes: dict[str, str] | None = None) -> dict:
    indexes = indexes or INDEXES
    days = lake_trade_days(lake_daily)
    op, cl = _load_pivots(lake_daily, days)
    tds = list(op.index)
    pos = {d: i for i, d in enumerate(tds)}
    gap = (op.shift(-2) / cl.shift(-1) - 1.0).replace([np.inf, -np.inf], np.nan)
    obs: list[dict] = []
    events_out: list[dict] = []
    for ev in rebalance_events(start, end, indexes):
        if ev["A"] not in pos or ev["E"] not in pos:
            continue
        i_a, i_e = pos[ev["A"]], pos[ev["E"]]
        y, m = int(ev["E"][:4]), int(ev["E"][4:6])
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        names = []
        for code in ev["index_codes"]:
            prev, cur = constituents(code, py, pm, fetch), constituents(code, y, m, fetch)
            if not prev or not cur:
                continue
            names.append(indexes[code])
            unchanged = [c for c in (prev & cur) if c in gap.columns]
            for side, codes in (("add", cur - prev), ("drop", prev - cur)):
                for i in range(i_a - 2, min(i_e + POST + 1, len(tds))):
                    if i < 0:
                        continue
                    ph = _phase(i, i_a, i_e)
                    if ph is None:
                        continue
                    day = tds[i]
                    ctrl = gap.loc[day, unchanged].mean() if unchanged else np.nan
                    for c in codes:
                        if c not in gap.columns or pd.isna(gap.at[day, c]) or pd.isna(ctrl):
                            continue
                        obs.append({"E": ev["E"], "index": indexes[code], "side": side, "phase": ph,
                                    "code": c, "exc": float(gap.at[day, c] - ctrl)})
        events_out.append({"A": ev["A"], "E": ev["E"], "indexes": names})
    df = pd.DataFrame(obs, columns=["E", "index", "side", "phase", "code", "exc"])
    by_index = {name: {side: _stats(df[(df["index"] == name) & (df.side == side)])
                       for side in ("add", "drop")} for name in sorted(df["index"].unique())}
    return {"start": start, "end": end, "events": events_out, "n_obs": int(len(df)),
            "tables": {side: _stats(df[df.side == side]) for side in ("add", "drop")},
            "by_index": by_index}


def render(doc: dict) -> str:
    lines = [f"# 指数调样事件 · 隔夜尺普查({doc['start']} → {doc['end']},{len(doc['events'])} 次调样,{doc['n_obs']} 票日)",
             "", "主尺 gap_c1_o2;对照 = 同指数未变动成分股当日等权;t_cell 按(调样, 指数)格聚合。",
             "边界:月末快照含临时调整污染;**未扣成本**;单一大周期;事件日按规则推。", ""]
    for side in ("add", "drop"):
        lines += [f"## {'调入' if side == 'add' else '调出'}票", "",
                  "| 相位 | n | 格 | 超额 pp | 胜率 | t_obs | t_cell |", "|---|---|---|---|---|---|---|"]
        for ph in PHASES:
            s = doc["tables"][side].get(ph)
            if s:
                lines.append(f"| {ph} | {s['n']} | {s['n_cells']} | {s['exc_pp']:+.2f} | {s['hit_pct']:.0f}% | "
                             f"{s['t_obs']} | {s['t_cell']} |")
        lines.append("")
    lines.append("## 分指数(调入票 5.E-1 / 1.A-1)")
    lines.append("")
    for name, sides in doc["by_index"].items():
        e1, a1 = sides["add"].get("5.E-1"), sides["add"].get("1.A-1")
        lines.append(f"- {name}:E−1 {e1['exc_pp']:+.2f}pp(胜 {e1['hit_pct']:.0f}%,n={e1['n']})" if e1 else f"- {name}:E−1 无观测")
        if a1:
            lines[-1] += f";A−1 {a1['exc_pp']:+.2f}pp(胜 {a1['hit_pct']:.0f}%)"
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="指数调样事件隔夜尺普查(只读;不写生产)")
    ap.add_argument("--start", default="2022-06")
    ap.add_argument("--end", default="2026-06")
    ap.add_argument("--lake-daily", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--index", action="append", default=None, help="CODE=名,可重复;缺省六指数")
    a = ap.parse_args(argv)
    indexes = dict(x.split("=", 1) for x in a.index) if a.index else None
    doc = run_census(start=a.start, end=a.end, lake_daily=Path(a.lake_daily) if a.lake_daily else None,
                     fetch=_default_fetch(), indexes=indexes)
    out = Path(a.out) if a.out else ws.reports_root() / "research" / "index_rebalance_census.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc), encoding="utf-8")
    (out.parent / "_index_rebalance_census.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑绿**

Run: `uv run --no-sync python -m pytest tests/research/test_index_rebalance_census.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 真湖复现附录 A(一次,读数记进提交说明;O8)**

Run: `uv run --no-sync python -m autoresearch.research.index_rebalance_census --start 2022-06 --end 2026-06`
Expected: 调入票 `5.E-1` 行 ≈ −0.31pp(±0.05;t_obs 约 −8)、`1.A-1` ≈ +0.21pp;`index_weight` 湖目录出现一指数一月一份 parquet。与 spec 附录 A 偏差 >0.05pp 时先查规则日期/对照集,再改数字。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/research/index_rebalance_census.py tests/research/test_index_rebalance_census.py
git commit -m "feat(research): reproducible index-rebalance overnight census (design 2026-09-25 appendix A)

Same-index unchanged-members control, (rebalance, index) cell t-stats,
index_weight through the lake. Read-only; writes only under research report root.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**批 B0 收尾**:`uv run --no-sync python -m pytest -q tests/` 绿 + `uv run --no-sync ruff check autoresearch tests` 干净;变异探针:注释掉 `parse_adjustment_xlsx` 的续行分支 → `test_parse_adjustment_xlsx_long_table` 红;注释掉 `_load_detail` 的湖内查找 → `test_detail_is_fetched_once_per_announcement` 红。

---

# 批 B1 · 日历事实(回滚杆 `calendar.index_rebalance=false`;批 4 结束后合并,2026-11-27 前)

### Task 5: 旋钮 `calendar.index_rebalance` 三件套 + `harvest_calendar` 第三腿 + prelude 汇总行

**Files:**
- Modify: `autoresearch/scan/user_config.py:90-126`(白名单)、`:159+`(`_KNOB_TYPES`)
- Modify: `autoresearch/scan/calendar.py:13-21`(模块级 import)、`:36-88`(`harvest_calendar`)、`main()`
- Modify: `autoresearch/scan/prelude.py:409-425`(`_calendar` 汇总行)
- Test: `tests/scan/test_config_knobs.py`、`tests/scan/test_calendar.py`

**Interfaces:**
- Consumes(Task 3):`index_events.harvest_index_events(date, outdir) -> DataFrame | None`。
- Produces:`harvest_calendar(date, codes, root=None, horizon_days=35, index_rebalance: bool | None = None)`——`None` → 读旋钮 `knob("calendar", "index_rebalance", None, False)`;开时 `calendar.csv` 多出 `kind="index_rebalance"` 行:`event_date=eff_close_date`,`detail=f"{index_name} {调入|调出}|{phase}"`,`ratio=flow_adv_days`(可空)。只收 `codes`(want)内且有生效日的行(`unknown_eff` 不进日历)。`index_events.csv` 由本函数顺带落盘(全量六指数行)。

- [ ] **Step 1: 写失败测试(旋钮)**

追加到 `tests/scan/test_config_knobs.py` 末尾:

```python
# ───────────────────────── 白名单:日历第三腿(2026-09-25 指数调样事件 §2.3) ─────────────────────────


def test_calendar_block_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalance": True}}), encoding="utf-8")
    assert load_user_config(p) == {"calendar": {"index_rebalance": True}}


@pytest.mark.parametrize("bad", ["yes", 1, None, {"enabled": True}])
def test_calendar_index_rebalance_must_be_bool(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalance": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="index_rebalance"):
        load_user_config(p)


def test_calendar_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalanc": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="calendar"):
        load_user_config(p)


def test_knob_calendar_index_rebalance_defaults_off():
    assert knob("calendar", "index_rebalance", None, False, cfg={}) is False
    assert knob("calendar", "index_rebalance", None, False, cfg={"calendar": {"index_rebalance": True}}) is True
    assert knob("calendar", "index_rebalance", False, False, cfg={"calendar": {"index_rebalance": True}}) is False
```

- [ ] **Step 2: 写失败测试(第三腿)**

追加到 `tests/scan/test_calendar.py` 末尾:

```python
# ───────────────────────── 第三腿:指数调样(2026-09-25 §2.3) ─────────────────────────
from autoresearch.scan import index_events as ie


class _DeadPro:
    """解禁/披露两腿离线:方法一律抛 → harvest_calendar 那两段按既有 try/except 静默跳过。"""

    def share_float(self, **kw):
        raise RuntimeError("offline")

    def disclosure_date(self, **kw):
        raise RuntimeError("offline")


_EV = pd.DataFrame([
    {"code": "000001", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    {"code": "999999", "index_code": "000905", "index_name": "中证500", "side": "drop", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.4},
    {"code": "000002", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260909",
     "eff_close_date": None, "phase": "unknown_eff", "source": "none", "flow_adv_days": None},
], columns=ie.EVENT_COLS)


def _offline(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    from autoresearch.scan import calendar as cal
    monkeypatch.setattr(ts_src, "_pro", lambda: _DeadPro())
    monkeypatch.setattr(cal, "knob", lambda block, key, cli, default, cfg=None: default if cli is None else cli)


def test_harvest_calendar_third_leg_is_off_by_default(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def must_not_run(*a, **k):
        raise AssertionError("index_events must not be harvested when the knob is off")
    monkeypatch.setattr(ie, "harvest_index_events", must_not_run)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path)
    assert df.empty and (tmp_path / "2026-06-11" / "calendar.csv").exists()
    assert not (tmp_path / "2026-06-11" / "index_events.csv").exists()


def test_harvest_calendar_third_leg_filters_to_wanted_codes_and_keeps_phase(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def fake_harvest(date, outdir, **k):
        ie.write_index_events(outdir, _EV)
        return _EV
    monkeypatch.setattr(ie, "harvest_index_events", fake_harvest)
    df = cal.harvest_calendar("2026-06-11", {"000001", "000002"}, root=tmp_path, index_rebalance=True)
    rows = df[df["kind"] == "index_rebalance"]
    assert rows["code"].tolist() == ["000001"]                    # 999999 不在 want;000002 无生效日不进日历
    assert rows.iloc[0]["event_date"] == "20260612"
    assert rows.iloc[0]["detail"] == "沪深300 调入|passive_close_eve"
    assert pd.isna(rows.iloc[0]["ratio"])
    assert (tmp_path / "2026-06-11" / "index_events.csv").exists()   # 全量表照落(999999 也在里面)
    assert len(ie.load_index_events(tmp_path / "2026-06-11")) == 3


def test_harvest_calendar_source_absent_leaves_other_legs_intact(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)
    monkeypatch.setattr(ie, "harvest_index_events", lambda date, outdir, **k: None)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path, index_rebalance=True)
    assert df.empty and not (tmp_path / "2026-06-11" / "index_events.csv").exists()
```

- [ ] **Step 3: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_config_knobs.py tests/scan/test_calendar.py -q`
Expected: 新用例 FAIL(`calendar` 未知顶层键;`harvest_calendar() got an unexpected keyword argument 'index_rebalance'`;`cal.knob` 不存在)。

- [ ] **Step 4: 白名单 + 类型(`autoresearch/scan/user_config.py`)**

`_TOP_WHITELIST` 集合内追加:

```python
    # 2026-09-25 指数调样事件 §2.3:日历第三腿总开关(平铺布尔,镜像 l2.knife_cap)。默认 false = parity;
    # 消费点 scan/calendar.harvest_calendar(index_rebalance=knob)。
    "calendar",
```

`_SUB_WHITELIST` 加一项:`"calendar": {"index_rebalance"},`。

`_KNOB_TYPES` 加(放在 `("l2", "sector_seats")` 之后):

```python
    # 日历第三腿(2026-09-25 §2.3):true → prelude calendar 步顺带取中证调样公告落 index_events.csv,
    # calendar.csv 多出 kind=index_rebalance 行(L4 简报/summary/档案 §6/sector pack 自动继承);
    # false(默认)= 逐字 parity(不取网、不落文件、无新行)。回滚杆就是这一个键。
    ("calendar", "index_rebalance"): (_t_bool, "boolean"),
```

- [ ] **Step 5: `harvest_calendar` 第三腿(`autoresearch/scan/calendar.py`)**

模块顶部 `from autoresearch.common import workspace as ws` 之后加:

```python
from autoresearch.scan.user_config import knob
```

函数签名与开头改为:

```python
def harvest_calendar(date: str, codes, root: Path | None = None,
                     horizon_days: int = 35, index_rebalance: bool | None = None) -> pd.DataFrame:
    """拉解禁(≤14 天分块防 6000 行分页截断)+ 预约披露 + (旋钮开)指数调样,过滤 codes → calendar.csv。网络。

    第三腿(2026-09-25 §2.3):`index_rebalance=None` → 读旋钮 `calendar.index_rebalance`(默认 False =
    parity)。开时先由 `index_events.harvest_index_events` 落全量 `index_events.csv`(源不可达 → 不落),
    再把 **want 内且有生效日** 的行写成 `kind="index_rebalance"`:`event_date=` 被动调仓收盘日,
    `detail=f"{指数} {调入|调出}|{phase}"`(phase 让 `calendar_flags` 分两种文案),`ratio=flow_adv_days`。
    `unknown_eff` 行不进日历(没有日期就不是日历事实)。
    """
    from autoresearch.data.tushare_source import _code6, _pro, _ts_call
    index_rebalance = knob("calendar", "index_rebalance", index_rebalance, False)
```

在 `period = _last_quarter_end(date)` 那段(披露腿)之后、`df = pd.DataFrame(rows, ...)` 之前插入:

```python
    if index_rebalance:
        from autoresearch.scan import index_events as _ie

        ev = _ie.harvest_index_events(date, outdir)
        if ev is not None:
            for r in ev.itertuples(index=False):
                eff = r.eff_close_date
                if r.code not in want or not isinstance(eff, str) or not eff:
                    continue
                flow = None if r.flow_adv_days is None or pd.isna(r.flow_adv_days) else float(r.flow_adv_days)
                rows.append({"code": r.code, "kind": "index_rebalance", "event_date": str(eff)[:8],
                             "detail": f"{r.index_name} {'调入' if r.side == 'add' else '调出'}|{r.phase}",
                             "ratio": flow})
```

`main()` 加 `ap.add_argument("--index-rebalance", action="store_true", default=None, help="强制开第三腿(缺省读旋钮)")`,调用改 `harvest_calendar(args.date, codes, horizon_days=args.horizon, index_rebalance=args.index_rebalance)`,打印行加 `调样 {int((df['kind'] == 'index_rebalance').sum()) if len(df) else 0} 条`。

- [ ] **Step 6: prelude 汇总行(`autoresearch/scan/prelude.py:409-425` 的 `_calendar`)**

`return f"解禁 {n_u} + 披露 {n_d}"` 改为:

```python
        n_i = int((df["kind"] == "index_rebalance").sum()) if len(df) else 0
        return f"解禁 {n_u} + 披露 {n_d}" + (f" + 调样 {n_i}" if n_i else "")
```

- [ ] **Step 7: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_config_knobs.py tests/scan/test_calendar.py tests/scan/test_user_config.py tests/scan/test_prelude.py -q`
Expected: 全部 PASS。

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/user_config.py autoresearch/scan/calendar.py autoresearch/scan/prelude.py tests/scan/test_config_knobs.py tests/scan/test_calendar.py
git commit -m "feat(calendar): third leg kind=index_rebalance behind calendar.index_rebalance (default off = parity)

harvest_calendar writes the full six-index index_events.csv, then adds
calendar rows for menu codes with a passive-close date; unknown_eff rows
stay out of the calendar. Rollback = the one knob.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `calendar_flags` 两种文案 + `calendar_section` 市场级行

**Files:**
- Modify: `autoresearch/scan/calendar.py:102-119`(`calendar_flags`)、`:122-155`(`calendar_section`)
- Test: `tests/scan/test_calendar.py`

**Interfaces:**
- Produces:`calendar_flags` 对 `kind == "index_rebalance"` 行输出两种之一——`passive_close_eve` 相位:`- ⛔ **指数调样生效前夜**:{指数 调入|调出} 于 {E} 收盘生效;今晚买入 = 与被动资金同价买入,隔夜尺历史为负(docs/specs/2026-09-25-index-inclusion-signal-design.md §1)→ 入场行写 禁止`;其它相位:`- 📅 **指数调样**:{E} 收盘生效({指数 调入|调出};ETF 被动买入≈{x:.1f} 天 ADV)——事实日期非方向`(无 ratio 时省略分号后那段)。`calendar_section` 从 `index_events.csv` 读全量,按生效日输出 `- **指数调样 {E} 收盘生效**:沪深300 ×N / 中证500 ×M(finalist 涉及 K 只)`。Task 11 的 agent 规则与 `self_review` 锚的是 `⛔ **指数调样生效前夜**` 这一串。

- [ ] **Step 1: 写失败测试**

追加到 `tests/scan/test_calendar.py` 末尾:

```python
def _mk_index(tmp_path, date="2026-06-11"):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    rows = _ROWS + [
        {"code": "000004", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "沪深300 调入|passive_close_eve", "ratio": 0.3},
        {"code": "000005", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "中证500 调出|announced_runup", "ratio": None},
        {"code": "000006", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "沪深300 调入|announced_runup", "ratio": 0.3},
    ]
    pd.DataFrame(rows).to_csv(d / "calendar.csv", index=False)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in ("000001", "000004")]).to_csv(
        d / "finalists.csv", index=False)
    ie.write_index_events(d, pd.DataFrame([
        {"code": "000004", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.3},
        {"code": "000006", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.3},
        {"code": "000005", "index_code": "000905", "index_name": "中证500", "side": "drop", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    return d


def test_calendar_flags_rebalance_eve_is_the_only_directional_line(tmp_path):
    d = _mk_index(tmp_path)
    eve = calendar_flags(d, "000004")
    assert len(eve) == 1 and eve[0].startswith("- ⛔ **指数调样生效前夜**")
    assert "沪深300 调入" in eve[0] and "20260612" in eve[0] and eve[0].endswith("入场行写 禁止")
    fact = calendar_flags(d, "000005")
    assert len(fact) == 1 and fact[0].startswith("- 📅 **指数调样**")
    assert "中证500 调出" in fact[0] and "事实日期非方向" in fact[0]
    assert "禁止" not in fact[0] and "买入" not in fact[0]           # 其它相位零方向词


def test_calendar_flags_fact_line_carries_flow_only_when_present(tmp_path):
    d = _mk_index(tmp_path)
    assert "ETF 被动买入≈0.3 天 ADV" in calendar_flags(d, "000006")[0]
    assert "ADV" not in calendar_flags(d, "000005")[0]


def test_calendar_section_prints_market_level_rebalance_counts(tmp_path):
    d = _mk_index(tmp_path)
    s = calendar_section(d)
    assert "- **指数调样 20260612 收盘生效**:" in s
    assert "沪深300 ×2" in s and "中证500 ×1" in s and "finalist 涉及 1 只" in s


def test_calendar_section_shows_rebalance_even_without_unlock_or_disclosure_rows(tmp_path):
    d = _mk_index(tmp_path)
    df = pd.read_csv(d / "calendar.csv", dtype={"code": str})
    df[df["kind"] == "index_rebalance"].to_csv(d / "calendar.csv", index=False)
    s = calendar_section(d)
    assert "指数调样 20260612" in s and "预约披露" not in s


def test_brief_injects_rebalance_eve_line(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk_index(tmp_path)
    assert "⛔ **指数调样生效前夜**" in compose_funnel_brief("000004", d)
    assert "指数调样" not in compose_funnel_brief("000002", d)
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_calendar.py -q`
Expected: 新用例 FAIL(`calendar_flags` 对未知 kind 返回空;section 无调样行)。

- [ ] **Step 3: `calendar_flags`**

在 `elif r.kind == "disclosure":` 分支之后追加:

```python
        elif r.kind == "index_rebalance":
            label, _, phase = str(r.detail).partition("|")
            if phase == "passive_close_eve":
                out.append(f"- ⛔ **指数调样生效前夜**:{label} 于 {ev} 收盘生效;今晚买入 = 与被动资金同价买入,"
                           f"隔夜尺历史为负(docs/specs/2026-09-25-index-inclusion-signal-design.md §1)→ 入场行写 禁止")
            else:
                flow = "" if r.ratio is None or pd.isna(r.ratio) else f";ETF 被动买入≈{float(r.ratio):.1f} 天 ADV"
                out.append(f"- 📅 **指数调样**:{ev} 收盘生效({label}{flow})——事实日期非方向")
```

docstring 加一句:`指数调样 → 生效前夜 ⛔(唯一带方向词的日历行,方向是「禁止」),其它相位 📅 事实行`。

- [ ] **Step 4: `calendar_section`**

把 `if not len(disc) and not len(unlk): return ""` 改为:

```python
    from autoresearch.scan.index_events import load_index_events
    ev = load_index_events(scan_dir)
    if ev is not None and len(ev):
        ev = ev[ev["eff_close_date"].notna() & (ev["eff_close_date"].astype(str).str[:8] <= cut)]
    has_ev = ev is not None and len(ev) > 0
    if not len(disc) and not len(unlk) and not has_ev:
        return ""
```

在 `lines = [f"### 📅 未来 {horizon_days} 天日历(…)"]` 的标题串里把括号内容改为 `披露=催化锚,解禁=风险窗,调样=被动调仓收盘日;事实日期非方向`,并在大解禁块之后追加:

```python
    if has_ev:
        for e_date, g in ev.groupby(ev["eff_close_date"].astype(str).str[:8]):
            counts = g.groupby("index_name").size()
            fin_n = int(g["code"].astype(str).str.zfill(6).isin(fin).sum())
            lines.append(f"- **指数调样 {e_date} 收盘生效**:"
                         + " / ".join(f"{k} ×{int(v)}" for k, v in counts.items())
                         + f"(finalist 涉及 {fin_n} 只)")
```

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_calendar.py tests/dossier tests/sector -q`
Expected: 全部 PASS(档案 §6 / sector pack 只是多认一种 kind,既有断言不变)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/calendar.py tests/scan/test_calendar.py
git commit -m "feat(calendar): rebalance flags (⛔ eve line is the only directional wording) and summary market-level counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `run_health.index_events`(旋钮开才出现)

**Files:**
- Modify: `autoresearch/scan/health.py:648-700`(`run_health`)+ 新函数 `index_events_health`
- Test: `tests/scan/test_health.py`

**Interfaces:**
- Produces:`index_events_health(scan_dir) -> {"source": "ok"|"absent"|"disabled", "n_rows", "n_finalists_involved", "n_passive_close_eve"}`;`run_health()` 只在 `source != "disabled"` 时带 `index_events` 键(旋钮关 + 无文件 = 逐字节不变)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/scan/test_health.py`:

```python
def test_run_health_index_events_three_worlds(tmp_path, monkeypatch):
    """disabled(旋钮关无文件 → 键不出现)/ absent(旋钮开无文件)/ ok(有文件,含空表)。"""
    import autoresearch.scan.user_config as uc
    from autoresearch.scan import index_events as ie
    d = _mk_day(tmp_path, "2026-12-10", codes=("000001", "000002"))
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {})
    assert "index_events" not in run_health(d)                                   # 旋钮关:逐字节不变
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"calendar": {"index_rebalance": True}})
    assert run_health(d)["index_events"] == {"source": "absent", "n_rows": 0,
                                             "n_finalists_involved": 0, "n_passive_close_eve": 0}
    ie.write_index_events(d, pd.DataFrame(columns=ie.EVENT_COLS))
    assert run_health(d)["index_events"]["source"] == "ok"                      # 空表 ≠ 缺席
    ie.write_index_events(d, pd.DataFrame([
        {"code": "000001", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "announced_runup", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    h = run_health(d)["index_events"]
    assert h == {"source": "ok", "n_rows": 2, "n_finalists_involved": 1, "n_passive_close_eve": 1}
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {})
    assert run_health(d)["index_events"]["source"] == "ok"                      # 文件在场就报,不看旋钮
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_health.py -k index_events -q` → Expected: FAIL(`KeyError: 'index_events'`)。

- [ ] **Step 3: 实现**

在 `run_health` 之前加:

```python
def index_events_health(scan_dir: Path) -> dict:
    """`index_events.csv` 三态(design 2026-09-25 §2.7 / F11):disabled = 旋钮关且无文件;absent = 旋钮开
    但源不可达(文件缺席,degraded.json 另有一行);ok = 文件在场(含只有表头的空表 = 源可达无事件)。
    门命中数不在这里——run_health 在 E6 之前写盘,命中数只在决策文件 `index_events.hits` 与 brief ③。"""
    from autoresearch.scan.index_events import load_index_events
    from autoresearch.scan.user_config import knob

    ev = load_index_events(scan_dir)
    if ev is None:
        on = bool(knob("calendar", "index_rebalance", None, False))
        return {"source": "absent" if on else "disabled", "n_rows": 0,
                "n_finalists_involved": 0, "n_passive_close_eve": 0}
    if not len(ev):
        return {"source": "ok", "n_rows": 0, "n_finalists_involved": 0, "n_passive_close_eve": 0}
    fin = _read(Path(scan_dir) / "finalists.csv")
    fin_codes = (set(fin["code"].astype(str).str.zfill(6))
                 if fin is not None and "code" in fin.columns else set())
    codes = ev["code"].astype(str).str.zfill(6)
    return {"source": "ok", "n_rows": int(len(ev)),
            "n_finalists_involved": int(codes.isin(fin_codes).sum()),
            "n_passive_close_eve": int((ev["phase"] == "passive_close_eve").sum())}
```

`run_health` 的 `return {...}` 改成先赋给 `out = {...}`,然后:

```python
    ih = index_events_health(scan_dir)
    if ih["source"] != "disabled":        # 旋钮关且无文件 → 不出现该键(run_health 逐字节不变)
        out["index_events"] = ih
    return out
```

- [ ] **Step 4: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_health.py tests/scan/test_health_artifacts.py tests/scan/test_publisher_health_snapshot.py -q` → Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/health.py tests/scan/test_health.py
git commit -m "feat(health): index_events block — disabled / absent / ok kept apart

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**批 B1 收尾**:全量 pytest 绿 + ruff 干净;parity 回放(验收 O4 前半):对任一历史 run 的 staging 目录复制到 tmp,旋钮关跑 `harvest_calendar`(离线,解禁/披露两腿 monkeypatch 成空)→ `calendar.csv` 无 `index_rebalance` 行。变异探针:删掉 `calendar_flags` 的 `index_rebalance` 分支 → Task 6 两个 flags 测试红;删掉 `harvest_calendar` 的第三腿 → Task 5 第二个测试红。

---

# 批 B2 · 守卫(回滚杆 `relative_buy.rebalance_gate=false`;批 4 结束后合并,2026-11-27 前)

### Task 8: E6 纯函数——第五门 `rebalance_close`、决策块、`RULE_VERSION`、旋钮三件套

**Files:**
- Modify: `autoresearch/scan/relative_buy.py`:`:150-153`(`RULE_VERSION`)、`:233-234`(常量)、`:277-320`(`_field_usage`)、`:628-717`(`_hard_gate`)、`:1013-1030`(`_veto_accounting_block`)、`:1101-1400`(`build_decision`)、`:1434+`(新增 `configured_rebalance_gate`)
- Modify: `autoresearch/scan/user_config.py`(`_SUB_WHITELIST["relative_buy"]` + `_KNOB_TYPES`)
- Test: `tests/scan/test_relative_buy.py`、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Consumes(Task 3):`index_events` 形参 = `events_by_code()` 的形状 `dict[code6, list[row_dict]]`,行含 `phase / index_name / side / eff_close_date`。
- Produces(Task 9/10 消费):
  - `REBALANCE_GATE = "rebalance_close"`;`_hard_gates(rebalance_gate: bool) -> tuple[str, ...]`(四门或五门)。
  - `build_decision(scan_dir, date=None, mode=MODE_SHADOW, exclude_pinned=False, pool=POOL_FINALISTS, card_snapshot=None, tiering=False, index_events: dict[str, list[dict]] | None = None, rebalance_gate: bool = False) -> dict`。
  - 旋钮开时决策文件顶层多一块 `index_events = {"source": "ok"|"absent", "gate_evaluated": bool, "n_rows": int, "n_candidates_in_events": int, "hits": [code...]}`;`candidates[].hard_gate` 多键 `rebalance_close`;`excluded[]` 多 `reason="hard_gate.rebalance_close"` 行;`field_usage.hard_gate.fields` 五项 + `field_usage.index_events`。旋钮关 → 与 v4.0 输出**除 `rule_version` 外逐字节相同**。
  - `RULE_VERSION = "e6.v4.1"`。
  - `configured_rebalance_gate() -> bool`(读 `scan_config.relative_buy.rebalance_gate`,缺/坏 → `False` + stderr 留痕)。

- [ ] **Step 1: 写失败测试(旋钮)**

追加到 `tests/scan/test_config_knobs.py`:

```python
# ───────────────────────── 白名单:E6 第五门(2026-09-25 指数调样事件 §2.4) ─────────────────────────


def test_relative_buy_rebalance_gate_whitelisted_and_bool(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"relative_buy": {"rebalance_gate": True}}), encoding="utf-8")
    assert load_user_config(p) == {"relative_buy": {"rebalance_gate": True}}
    p.write_text(json.dumps({"relative_buy": {"rebalance_gate": "on"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="rebalance_gate"):
        load_user_config(p)


def test_configured_rebalance_gate_reads_config_and_degrades_loudly(monkeypatch, capsys):
    import autoresearch.scan.user_config as uc
    from autoresearch.scan.relative_buy import configured_rebalance_gate
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": True}})
    assert configured_rebalance_gate() is True
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {})
    assert configured_rebalance_gate() is False                              # 缺键 = 关 = parity

    def boom(path=None):
        raise ValueError("bad config")
    monkeypatch.setattr(uc, "load_user_config", boom)
    assert configured_rebalance_gate() is False
    assert "rebalance_gate" in capsys.readouterr().err                       # 配置层故障留痕
```

- [ ] **Step 2: 写失败测试(纯函数)**

追加到 `tests/scan/test_relative_buy.py` 末尾:

```python
# ───────────────────────── v4.1:第五门 rebalance_close(2026-09-25 指数调样事件 §2.4) ─────────────────────────
from autoresearch.scan.relative_buy import REBALANCE_GATE, RULE_VERSION, _hard_gates


def _events(code: str, phase: str, *, index_name: str = "中证500", side: str = "add",
            eff: str = "20261211") -> dict:
    return {code: [{"code": code, "index_code": "000905", "index_name": index_name, "side": side,
                    "ann_date": "20261127", "eff_close_date": eff, "phase": phase,
                    "source": "csindex", "flow_adv_days": None}]}


def test_hard_gates_tuple_grows_only_when_the_knob_is_on():
    assert _hard_gates(False) == ("tradable", "data_a", "contract", "no_redflag")
    assert _hard_gates(True) == ("tradable", "data_a", "contract", "no_redflag", REBALANCE_GATE)
    assert RULE_VERSION == "e6.v4.1"


def test_rebalance_gate_off_is_v40_verbatim_even_with_passive_close_eve_row(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    base = build_decision(scan)
    doc = build_decision(scan, index_events=_events("002345", "passive_close_eve"))   # rebalance_gate 默认 False
    assert doc == base
    assert "index_events" not in doc
    assert "rebalance_close" not in doc["candidates"][0]["hard_gate"]
    assert doc["field_usage"]["hard_gate"]["fields"] == ["tradable", "data_a", "contract", "no_redflag"]


def test_rebalance_gate_vetoes_the_passive_close_eve_row_and_says_where(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events=_events("002345", "passive_close_eve"), rebalance_gate=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"][REBALANCE_GATE] is False and by["002345"]["eligible"] is False
    assert by["000034"]["hard_gate"][REBALANCE_GATE] is True
    assert doc["buys"][0]["code"] == "000034"                                # 原冠军被门否决,亚军上
    assert doc["index_events"] == {"source": "ok", "gate_evaluated": True, "n_rows": 1,
                                   "n_candidates_in_events": 1, "hits": ["002345"]}
    veto = [r for r in doc["excluded"] if r["reason"] == f"hard_gate.{REBALANCE_GATE}"]
    assert len(veto) == 1 and "中证500 add E=20261211" in veto[0]["detail"]
    assert doc["field_usage"]["hard_gate"]["fields"][-1] == REBALANCE_GATE
    assert "index_events" in doc["field_usage"]
    assert doc["veto_accounting"]["by_gate"][REBALANCE_GATE] == 1
    assert by["002345"]["hard_gate"]["no_redflag"] is True                  # 独立计数:不并入 no_redflag


def test_rebalance_gate_ignores_other_phases_and_also_vetoes_drops(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    ev = {**_events("002345", "announced_runup"), **_events("000034", "passive_close_eve", side="drop")}
    doc = build_decision(scan, index_events=ev, rebalance_gate=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"][REBALANCE_GATE] is True                # 跑道段:事实,不否决
    assert by["000034"]["hard_gate"][REBALANCE_GATE] is False               # E2 裁定:调出票同样一刀
    assert doc["index_events"]["hits"] == ["000034"] and doc["index_events"]["n_candidates_in_events"] == 2


def test_rebalance_gate_source_absent_passes_everyone_and_says_so(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events=None, rebalance_gate=True)
    assert all(row["hard_gate"][REBALANCE_GATE] is True for row in doc["candidates"])
    assert doc["index_events"] == {"source": "absent", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}


def test_rebalance_gate_source_ok_but_empty_is_evaluated_not_absent(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events={}, rebalance_gate=True)
    assert doc["index_events"]["source"] == "ok" and doc["index_events"]["gate_evaluated"] is True
    assert doc["buys"][0]["code"] == "002345"                                # 无事件 → 与门关同一冠军


def test_rebalance_gate_can_block_the_whole_day_honestly(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    ev = {}
    for c in _RANK_ORDER:
        ev.update(_events(c, "passive_close_eve"))
    doc = build_decision(scan, index_events=ev, rebalance_gate=True)
    assert doc["blocked"] is True and doc["buys"] == []
    assert {r["reason"] for r in doc["blocked_reasons"]} == {f"hard_gate.{REBALANCE_GATE}"}
    assert doc["index_events"]["hits"] == sorted(_RANK_ORDER)


def test_rebalance_gate_and_tiering_compose(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"601699": _card("**入场**: 允许")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True,
                         index_events=_events("601699", "passive_close_eve"), rebalance_gate=True)
    assert _by_code(doc)["601699"]["eligible"] is False                     # A 级卡也挡:门在分级之前
    assert doc["buys"][0]["tier"] == "R"
```

- [ ] **Step 3: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py -k "rebalance or hard_gates" tests/scan/test_config_knobs.py -k "rebalance" -q`
Expected: FAIL(`ImportError: REBALANCE_GATE`;`build_decision() got an unexpected keyword argument 'index_events'`)。

- [ ] **Step 4: 旋钮(`autoresearch/scan/user_config.py`)**

`_SUB_WHITELIST["relative_buy"]` 改为 `{"mode", "exclude_pinned", "activate_date", "pool", "tiering", "rebalance_gate"}`;`_KNOB_TYPES` 在 `("relative_buy", "tiering")` 之后加:

```python
    # E6 第五门总开关(2026-09-25 指数调样事件 §2.4,v4.1):true → 扫描日 = 调样生效前夜的调样票
    # (调入/调出、六指数任一)`rebalance_close` 硬门否决,决策文件多 `index_events` 块;false(默认)
    # = v4.0 逐字(除 rule_version 字符串)。回滚杆就是这一个键;它不依赖 calendar.index_rebalance
    # (文件缺席 → 门放行并记 source=absent),但两键在生产里应同开同关。
    ("relative_buy", "rebalance_gate"): (_t_bool, "boolean"),
```

- [ ] **Step 5: 常量、`_hard_gates`、`RULE_VERSION`(`relative_buy.py`)**

`RULE_VERSION = "e6.v4.0"` → `RULE_VERSION = "e6.v4.1"`,其上注释块顶部加两行:

```python
# v4.1 = v4.0 + `rebalance_gate` 开关(2026-09-25 指数调样事件 §2.4):开 → 第五门 `rebalance_close`
# (扫描日 = 调样生效前夜的调样票否决,调入调出皆算)+ 顶层 `index_events` 块;关 → v4.0 逐字(除本字符串)。
```

`_HARD_GATES = (...)` 行之后加:

```python
#: 第五门(2026-09-25 指数调样事件 §2.4;design F1):扫描日 = 调样生效前夜的调样票不得成为 BUY ——
#: 买 E 收盘 = 和被动资金在同一个收盘价买入,17 次调样隔夜尺 −0.31pp(中证500 −0.72pp、胜率 22%)。
#: **只在旋钮 `relative_buy.rebalance_gate` 开时存在**(镜像 `tiering`),关 = 四门逐字;
#: 独立计数、不并入 no_redflag(「缺席≠否」:命中数要单独可读,见 09-25 五连撞记忆)。
REBALANCE_GATE = "rebalance_close"


def _hard_gates(rebalance_gate: bool) -> tuple[str, ...]:
    """当次运行的硬门元组:旋钮关 = `_HARD_GATES` 逐字;开 = 追加第五门。所有「按门遍历」的地方读它,不读常量。"""
    return _HARD_GATES + (REBALANCE_GATE,) if rebalance_gate else _HARD_GATES
```

- [ ] **Step 6: `_field_usage(tiering, rebalance_gate=False)`**

签名改 `def _field_usage(tiering: bool, rebalance_gate: bool = False) -> dict:`;`"fields": list(_HARD_GATES)` 改 `"fields": list(_hard_gates(rebalance_gate))`;在 `if tiering:` 块之后、`return usage` 之前加:

```python
    if rebalance_gate:
        usage["index_events"] = {
            "fields": ["index_events[code].phase"],
            "role": ("hard_gate(第五门 rebalance_close:该票任一行 phase==passive_close_eve 即否决,调入调出"
                     "皆算;源缺席 → 放行并在顶层 index_events.source=absent / gate_evaluated=false 留痕)"),
        }
```

`FIELD_USAGE = _field_usage(tiering=False)` 不动。

- [ ] **Step 7: `_hard_gate` 第 ⑤ 段与返回**

在 `# ④ 无红灯` 段的 `else: gates["no_redflag"] = True` 之后、`return` 之前加:

```python
    # ⑤ 指数调样生效前夜(2026-09-25 §2.4;只在旋钮开时存在,见 _hard_gates)
    if ctx.get("rebalance_gate"):
        events = ctx.get("index_events")
        if events is None:
            gates[REBALANCE_GATE] = True          # 源缺席 → 放行(缺席≠否);顶层块记 source=absent
        else:
            hit = next((r for r in events.get(code, []) if r.get("phase") == "passive_close_eve"), None)
            if hit is None:
                gates[REBALANCE_GATE] = True
            else:
                fail(REBALANCE_GATE, f"指数调样生效前夜:{hit.get('index_name')} {hit.get('side')} "
                                     f"E={hit.get('eff_close_date')}(买 E 收盘 = 与被动资金同价买入)")
```

`return {gate: gates.get(gate, False) for gate in _HARD_GATES}, details` 改为 `... for gate in ctx.get("hard_gates", _HARD_GATES)}, details`。

- [ ] **Step 8: `_veto_accounting_block` 按当次门遍历**

签名加 `hard_gates: tuple[str, ...] = _HARD_GATES`;`for gate in _HARD_GATES}` 改 `for gate in hard_gates}`;`build_decision` 里的调用加 `hard_gates=ctx["hard_gates"]`。

- [ ] **Step 9: `build_decision`**

签名加两个形参(放在 `tiering` 之后):

```python
                   index_events: dict[str, list[dict]] | None = None,
                   rebalance_gate: bool = False) -> dict:
```

docstring 末尾加一段:

```
    `index_events` / `rebalance_gate`(v4.1,2026-09-25 指数调样事件 §2.4):`rebalance_gate=False`(缺省)
    = v4.0 逐字,`index_events` 被忽略。`True` 时 `_hard_gate` 多第五门 `rebalance_close`:该票在
    `index_events[code]` 里任一行 `phase == "passive_close_eve"` 即否决(调入调出皆算,E2 裁定);
    `index_events is None`(= 盘上无 index_events.csv:源不可达或日历腿关)→ 全员放行,顶层
    `index_events.source="absent"`、`gate_evaluated=False` 留痕;`{}` = 源可达无事件,`source="ok"`。
    `index_events` 与 `card_snapshot` 同属「固定盘上输入」:由 `write_decision`/`verify_decision`
    在 I/O 边界读盘构造,本函数不自己读文件。
```

`ctx["tiering"] = tiering` 之后加:

```python
    ctx["rebalance_gate"] = rebalance_gate
    ctx["index_events"] = index_events
    ctx["hard_gates"] = _hard_gates(rebalance_gate)
```

返回字典里 `"tier_counts": tier_counts,` 之后加:

```python
        # v4.1:第五门的评估留痕(只在旋钮开时出现;缺这个块 = 仪器未上线,不是「当日无事件」)。
        **({"index_events": {
            "source": "absent" if index_events is None else "ok",
            "gate_evaluated": index_events is not None,
            "n_rows": 0 if index_events is None else sum(len(v) for v in index_events.values()),
            "n_candidates_in_events": sum(1 for row in candidates if row["code"] in (index_events or {})),
            "hits": sorted(row["code"] for row in candidates
                           if row["hard_gate"].get(REBALANCE_GATE) is False),
        }} if rebalance_gate else {}),
```

`"field_usage": _field_usage(tiering),` 改 `"field_usage": _field_usage(tiering, rebalance_gate),`。

- [ ] **Step 10: `configured_rebalance_gate()`**

紧跟 `configured_relative_buy()` 之后加:

```python
def configured_rebalance_gate() -> bool:
    """`scan_config.relative_buy.rebalance_gate`(v4.1 第五门总开关)。独立于五元组读取:少改调用点,
    且两个写者(`post_run` 的 write / verify)必须拿同一个值。缺文件 / 缺键 / 配置层故障 → False = v4.0
    逐字(parity),故障路径打一行 stderr(降级必须可见)。"""
    try:
        from autoresearch.scan.user_config import load_user_config

        block = load_user_config().get("relative_buy") or {}
    except Exception as exc:  # noqa: BLE001 — 配置层故障不挡决策发布,但降级必须可见
        print(f"[relative_buy] scan_config 读取失败({exc!r})→ rebalance_gate 用内建默认 False", file=sys.stderr)
        return False
    return bool(block.get("rebalance_gate", False))
```

- [ ] **Step 11: 版本字面量对齐**

Run: `grep -rn "e6\.v4\.0" tests/ autoresearch/`
对每一处**断言常量取值**的测试字面量(如 `assert doc["rule_version"] == "e6.v4.0"`)改成 `"e6.v4.1"`;注释/文档里描述 v4.0 历史的原话不改;历史产物不改写。

- [ ] **Step 12: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py tests/scan/test_config_knobs.py tests/scan/test_buyability.py -q`
Expected: 全部 PASS(含既有 golden:`tiering` 关/开各自逐字;`test_real_run_20260806_is_deterministic_and_side_effect_free` 除 `rule_version` 外不变)。

- [ ] **Step 13: 提交**

```bash
git add autoresearch/scan/relative_buy.py autoresearch/scan/user_config.py tests/scan/test_relative_buy.py tests/scan/test_config_knobs.py
git commit -m "feat(e6): v4.1 — fifth hard gate rebalance_close behind relative_buy.rebalance_gate

Passive-close-eve rows (adds and drops, any of the six indices) are vetoed;
source absence passes everyone and is recorded as index_events.source=absent.
Knob off = v4.0 verbatim apart from rule_version.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: I/O 边界(两个写者读 `index_events.csv`)+ `post_run` 接线

**Files:**
- Modify: `autoresearch/scan/relative_buy.py:1493-1600`(`write_decision` / `safe_write_decision` / `verify_decision` / `safe_verify_decision`)
- Modify: `autoresearch/scan/post_run.py:859-867`
- Test: `tests/scan/test_relative_buy.py`

**Interfaces:**
- Consumes(Task 3):`index_events.load_index_events(scan) -> DataFrame | None`、`events_by_code(df) -> dict | None`;(Task 8)`build_decision(..., index_events=, rebalance_gate=)`、`configured_rebalance_gate()`。
- Produces:`write_decision(scan_dir, date=None, mode=MODE_SHADOW, exclude_pinned=False, pool=POOL_FINALISTS, tiering=False, rebalance_gate=False) -> Path`;`verify_decision(...同参...) -> dict`;`safe_write_decision` / `safe_verify_decision` 同参透传;私有 `_index_events_input(scan, rebalance_gate) -> dict | None`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/scan/test_relative_buy.py`:

```python
def _events_csv(scan: Path, code: str, phase: str) -> None:
    import pandas as pd
    from autoresearch.scan import index_events as ie
    ie.write_index_events(scan, pd.DataFrame([{
        "code": code, "index_code": "000905", "index_name": "中证500", "side": "add", "ann_date": "20261127",
        "eff_close_date": "20261211", "phase": phase, "source": "csindex", "flow_adv_days": None,
    }], columns=ie.EVENT_COLS))


def test_write_decision_reads_index_events_from_disk_when_gate_on(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    doc = json.loads(write_decision(scan, rebalance_gate=True).read_text(encoding="utf-8"))
    assert doc["index_events"]["hits"] == ["002345"] and doc["buys"][0]["code"] == "000034"


def test_write_decision_ignores_index_events_file_when_gate_off(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    assert "index_events" not in doc and doc["buys"][0]["code"] == "002345"


def test_write_decision_gate_on_without_file_records_absent(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = json.loads(write_decision(scan, rebalance_gate=True).read_text(encoding="utf-8"))
    assert doc["index_events"] == {"source": "absent", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}


def test_verify_decision_must_use_the_same_gate_switch_as_the_writer(tmp_path):
    from autoresearch.scan.relative_buy import verify_decision
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    write_decision(scan, rebalance_gate=True)
    verify_decision(scan, rebalance_gate=True)
    assert not (scan / "_relative_buy_decision.mismatch.json").exists()      # 同开关 → 安静
    verify_decision(scan, rebalance_gate=False)
    assert (scan / "_relative_buy_decision.mismatch.json").exists()          # 不同开关 → 留证据、不覆盖
    on_disk = json.loads((scan / "_relative_buy_decision.json").read_text(encoding="utf-8"))
    assert on_disk["index_events"]["hits"] == ["002345"]                     # 盘上那份没被改写
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py -k "write_decision_reads or ignores_index_events or gate_on_without or same_gate_switch" -q`
Expected: FAIL(`write_decision() got an unexpected keyword argument 'rebalance_gate'`)。

- [ ] **Step 3: I/O 边界(`relative_buy.py`)**

在 `_build_card_snapshot` 之后加:

```python
def _index_events_input(scan: Path, rebalance_gate: bool) -> dict[str, list[dict]] | None:
    """v4.1 I/O 边界(镜像 `_build_card_snapshot`):旋钮开才读 `index_events.csv`;缺文件 → None(= 源缺席,
    `build_decision` 记 source=absent 放行);旋钮关 → None 且 `build_decision` 根本不看它。两个写者共用。"""
    if not rebalance_gate:
        return None
    from autoresearch.scan.index_events import events_by_code, load_index_events

    return events_by_code(load_index_events(scan))
```

`write_decision` / `safe_write_decision` / `verify_decision` / `safe_verify_decision` 四个签名末尾各加 `rebalance_gate: bool = False`;两处 `build_decision(...)` 调用加 `index_events=_index_events_input(scan, rebalance_gate), rebalance_gate=rebalance_gate`;两个 `safe_*` 把 `rebalance_gate` 原样透传。docstring 各加一句「`rebalance_gate`(v4.1)与 `tiering` 同款透传;两个写者必须用同一个值,否则 verify 天天误报」。

- [ ] **Step 4: `post_run.py:859-867`**

```python
    from autoresearch.scan.relative_buy import configured_rebalance_gate, configured_relative_buy

    _rb_mode, _rb_exclude_pinned, _, _rb_pool, _rb_tiering = configured_relative_buy()
    _rb_gate = configured_rebalance_gate()            # v4.1:两个写者同一个开关(同 tiering 纪律)
    if decision_write == "write":
        from autoresearch.scan.relative_buy import safe_write_decision

        safe_write_decision(scan, mode=_rb_mode, exclude_pinned=_rb_exclude_pinned, pool=_rb_pool,
                            tiering=_rb_tiering, rebalance_gate=_rb_gate)
    else:
        from autoresearch.scan.relative_buy import safe_verify_decision

        safe_verify_decision(scan, mode=_rb_mode, exclude_pinned=_rb_exclude_pinned, pool=_rb_pool,
                             tiering=_rb_tiering, rebalance_gate=_rb_gate)
```

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py tests/scan/test_post_run*.py tests/scan/test_publisher*.py -q`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/relative_buy.py autoresearch/scan/post_run.py tests/scan/test_relative_buy.py
git commit -m "feat(e6): both decision writers read index_events.csv at the I/O boundary under one config switch

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `relative_facts` + brief ③ ⛔ 行

**Files:**
- Modify: `autoresearch/scan/relative_facts.py:31-70`(`relative_facts`)
- Modify: `autoresearch/scan/brief.py`(`_buy_lines` 两个出口 + 新 `_rebalance_line`)
- Test: `tests/scan/test_brief.py`

**Interfaces:**
- Consumes(Task 8):决策文件顶层 `index_events` 块。
- Produces:`relative_facts()["rebalance"]` = 该块原样(缺块 → `None`);brief ③ 在 relative BUY 行之后多一行(两种):命中 → `  ⛔ 指数调样生效前夜否决 N 只:{codes}(hard_gate.rebalance_close)`;源缺席 → `  ⛔ 指数调样门:源不可达,本日未评估(hard_gate.rebalance_close 放行,不等于无事件)`;块缺席/无命中 → 无行(旧决策文件与旋钮关 = 逐字 parity)。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_brief.py` 的 `_decision(...)` 签名加 `index_events=None`,函数末尾 `return {...}` 前加:

```python
    doc = {  # 原 return 的字典整体赋给 doc
        ...
    }
    if index_events is not None:
        doc["index_events"] = index_events
    return doc
```

追加测试:

```python
def _hits(codes, *, source="ok"):
    return {"source": source, "gate_evaluated": source == "ok", "n_rows": len(codes),
            "n_candidates_in_events": len(codes), "hits": list(codes)}


def test_buy_line_prints_rebalance_eve_vetoes_with_sources(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits(["603127"])))
    out = brief.build(scan, run_folder=_RUN)
    assert "⛔ 指数调样生效前夜否决 1 只:603127(hard_gate.rebalance_close)" in out["markdown"]
    dumped = json.dumps(out["sources"], ensure_ascii=False)
    assert "relative.rebalance_hits" in dumped and "relative.rebalance_hit_codes" in dumped


def test_buy_line_prints_rebalance_source_absent_honestly(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits([], source="absent")))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "⛔ 指数调样门:源不可达,本日未评估" in md


def test_buy_line_is_silent_without_index_events_block_or_hits(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active"))
    assert "指数调样" not in brief.build(scan, run_folder=_RUN)["markdown"]           # 旧 schema:逐字 parity
    scan2 = _scan_dir(tmp_path / "b", decision=_decision(mode="active", index_events=_hits([])))
    assert "指数调样" not in brief.build(scan2, run_folder=_RUN)["markdown"]          # 门开无命中:不出行


def test_blocked_day_also_prints_rebalance_vetoes(tmp_path):
    dec = _decision(mode="active", blocked=True, index_events=_hits(["600018", "600285"]))
    scan = _scan_dir(tmp_path, decision=dec)
    assert "⛔ 指数调样生效前夜否决 2 只:600018、600285" in brief.build(scan, run_folder=_RUN)["markdown"]
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_brief.py -k rebalance -q` → Expected: FAIL(无 ⛔ 行)。

- [ ] **Step 3: `relative_facts`**

`return {` 字典里 `"tier": ...,` 之后加:

```python
        # v4.1(2026-09-25 §2.4):第五门评估留痕。None = 门未开 / 旧 schema(**不是**「当日无事件」);
        # dict = {source, gate_evaluated, n_rows, n_candidates_in_events, hits}。
        "rebalance": decision.get("index_events"),
```

- [ ] **Step 4: brief**

在 `_buy_lines` 之前加:

```python
def _rebalance_line(rel: dict, src: list[dict]) -> str | None:
    """③ 附加行(v4.1):第五门 `rebalance_close` 的评估结果。块缺席(旧 schema / 门关)或无命中 → None(不出行)。
    源缺席要**说出来**:门放行了,但那是「没看见」不是「没事件」(design F11)。"""
    rb = rel.get("rebalance")
    if not isinstance(rb, dict):
        return None
    if rb.get("source") == "absent":
        text = "  ⛔ 指数调样门:源不可达,本日未评估(hard_gate.rebalance_close 放行,不等于无事件)"
        _src(src, "relative.rebalance_source", "absent", DECISION_FILENAME, "index_events.source", text)
        return text
    hits = [str(c) for c in (rb.get("hits") or [])]
    if not hits:
        return None
    text = f"  ⛔ 指数调样生效前夜否决 {len(hits)} 只:{'、'.join(hits)}(hard_gate.rebalance_close)"
    _src(src, "relative.rebalance_hits", len(hits), DECISION_FILENAME, "len(index_events.hits)", text)
    _src(src, "relative.rebalance_hit_codes", "、".join(hits), DECISION_FILENAME, "index_events.hits", text)
    return text
```

`_buy_lines` 的 blocked 分支:`lines.append("- " + text)` 之后、`if ba_line:` 之前加:

```python
        rb_line = _rebalance_line(rel, src)
        if rb_line:
            lines.append(rb_line)
```

正常分支:`lines.append("- " + text)`(relative BUY 主行)之后同样加这三行。`present=False` 分支不加(决策文件都没有,谈不上门)。

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_brief.py tests/scan/test_self_review_brief.py -q`
Expected: 全部 PASS(含 `test_sources_cover_every_number_and_stay_in_whitelist`、`test_byte_budget`)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/relative_facts.py autoresearch/scan/brief.py tests/scan/test_brief.py
git commit -m "feat(brief): ③ prints rebalance-eve vetoes (and says when the source was absent)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: 卡契约规则(两引擎共用文件)+ 锚测试 + `self_review` warn

**Files:**
- Modify: `.claude/agents/l4-card.md:64-67`(入场行规则段)
- Modify: `.claude/skills/stock-research/lite-playbook.md:55-58`
- Modify: `tests/test_agent_defs.py:44-60`(anchors 列表)
- Modify: `autoresearch/scan/self_review.py:288-312`(`card_contract_lint`)
- Test: `tests/scan/test_card_lint.py`

**Interfaces:**
- Consumes(Task 6 文案锚):简报日历行 `⛔ **指数调样生效前夜**`;(Task 3)`index_events.load_index_events / events_by_code`;`autoresearch.scan.l4.parsers.parse_card_context(text)["entry_stance"]`。
- Produces:agent 规则一条(两文件同文);lint 项 `{"check": "卡片契约·调样前夜入场允许", "severity": "warn", "code", "detail"}`。Codex 侧不改文件(`.codex/agents/l4_card.toml` 只引用 `l4-card.md`)。

- [ ] **Step 1: 写失败测试(锚)**

`tests/test_agent_defs.py` anchors 列表(`"**入场**:", "入场行",` 之后)追加:

```python
               # 2026-09-25 指数调样事件 §2.5:生效前夜 → 入场行一律 禁止;其它相位事实不抬不压。
               # 锚的是简报日历行的字面(Task 6 `calendar_flags`),两边同串才对得上。
               "指数调样生效前夜",
```

若该测试同时对 `playbook` 逐锚断言,则 lite-playbook 也必须含同串(本任务 Step 4 同改)。

- [ ] **Step 2: 写失败测试(lint)**

追加到 `tests/scan/test_card_lint.py`:

```python
def test_card_contract_lint_warns_when_card_allows_entry_on_rebalance_eve(tmp_path):
    """2026-09-25 §2.5:该票今晚是调样生效前夜,卡入场行却写『允许』→ warn(E6 门会否决,但卡应自己写 禁止)。"""
    import pandas as pd
    from autoresearch.scan import index_events as ie
    d = _mk(tmp_path, "2026-12-10", {
        "600035": ("# 决策卡 — 600035 楚天 @ 2026-12-10\n**Rating**: Hold\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600018": ("# 决策卡 — 600018 上港 @ 2026-12-10\n**Rating**: Hold\n进入P4倾向: Hold\n"
                   "**入场**: 禁止\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600036": ("# 决策卡 — 600036 招行 @ 2026-12-10\n**Rating**: Hold\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
    })
    ie.write_index_events(d, pd.DataFrame([
        {"code": "600035", "index_code": "000905", "index_name": "中证500", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600018", "index_code": "000300", "index_name": "沪深300", "side": "drop", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600036", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "announced_runup", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    hits = [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]
    assert {h["code"] for h in hits} == {"600035"}            # 600018 写了禁止;600036 不在守卫相位
    assert hits[0]["severity"] == "warn" and "中证500" in hits[0]["detail"]


def test_card_contract_lint_rebalance_check_is_silent_without_events_file(tmp_path):
    d = _mk(tmp_path, "2026-12-10", {"600035": "# 决策卡\n**Rating**: Hold\n进入P4倾向: Hold\n**入场**: 允许\n"})
    assert not [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]
```

- [ ] **Step 3: 跑红**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/scan/test_card_lint.py -q` → Expected: 锚测试 FAIL(缺「指数调样生效前夜」);lint 新测试 FAIL。

- [ ] **Step 4: 卡契约规则(两文件同文)**

`.claude/agents/l4-card.md` 在 `- 早停卡:只能写 \`禁止\` 或 \`条件(...)\`,…` 那条之后插入一条;`.claude/skills/stock-research/lite-playbook.md` 在对应位置插入**逐字相同**的一条:

```markdown
- **指数调样生效前夜**(2026-09-25):简报日历行出现 `⛔ **指数调样生效前夜**` → 入场行**一律写 `禁止`**(今晚买入 = 与被动资金同价买入,隔夜尺 17 次调样历史为负;理由归入现有停因 `其他`,不改七词早停契约、不改评级)。其它调样相位(📅 指数调样)只是事实日期:**不因调入抬评级、不因调出压评级**,也不作催化论点支柱。
```

- [ ] **Step 5: `self_review.card_contract_lint`**

在 `p4_re = re.compile(...)` 之后加:

```python
    # 2026-09-25 §2.5:今晚处于调样生效前夜的票 → 卡入场行不该是「允许」。文件缺席 → 整段 no-op(parity)。
    from autoresearch.scan.index_events import events_by_code, load_index_events
    from autoresearch.scan.l4.parsers import parse_card_context
    eve_rows = {code: rows for code, rows in (events_by_code(load_index_events(scan_dir)) or {}).items()
                if any(r.get("phase") == "passive_close_eve" for r in rows)}
```

在循环里 `if not has_machine_entry_line(text):` 那段之后加:

```python
        if code in eve_rows and parse_card_context(text).get("entry_stance") == "ALLOWED":
            hit = next(r for r in eve_rows[code] if r.get("phase") == "passive_close_eve")
            out.append({"check": "卡片契约·调样前夜入场允许", "severity": "warn", "code": code,
                        "detail": (f"{code} 今晚是指数调样生效前夜({hit.get('index_name')} {hit.get('side')} "
                                   f"E={hit.get('eff_close_date')}:买 E 收盘 = 与被动资金同价买入,历史隔夜为负),"
                                   f"卡入场行却写『允许』——E6 硬门 rebalance_close 会否决,但卡应自己写 禁止")})
```

- [ ] **Step 6: 跑绿**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_codex_agent_defs.py tests/scan/test_card_lint.py tests/scan/test_frozen_b_class_boundary.py -q`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py autoresearch/scan/self_review.py tests/scan/test_card_lint.py
git commit -m "feat(l4-card): rebalance-eve entry rule (both engines via the shared contract) + lint for cards that still allow entry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: 生产配置打开 + 文档同步 + spec 回写(**批 4 结束后执行;2026-11-27 前**)

**Files:**
- Modify: `.claude/skills/scan-market/scan_config.jsonc`(`l2` 块之后新增 `calendar` 块;`relative_buy` 块加 `rebalance_gate`)
- Modify: `.claude/skills/scan-market/STAGES.md:180`、`:323`、`:360-363`
- Modify: `.claude/skills/scan-market/SKILL.md:64-72`(配置表)
- Modify: `docs/specs/2026-09-25-index-inclusion-signal-design.md`(六处差异回写)
- Test: `tests/scan/test_config_knobs.py`(生产配置活体断言)

- [ ] **Step 1: 写失败测试**

追加到 `tests/scan/test_config_knobs.py`:

```python
def test_production_config_index_rebalance_knobs_on():
    """生产配置(2026-09-25 §4 批 B1/B2):日历第三腿与 E6 第五门同开。"""
    from pathlib import Path
    cfg = load_user_config(Path(".claude/skills/scan-market/scan_config.jsonc"))
    assert cfg["calendar"]["index_rebalance"] is True
    assert cfg["relative_buy"]["rebalance_gate"] is True
```

Run: `uv run --no-sync python -m pytest tests/scan/test_config_knobs.py -k production_config_index -q` → Expected: FAIL(`KeyError: 'calendar'`)。

- [ ] **Step 2: `scan_config.jsonc`**

在 `l2` 块的收尾 `},` 之后、`// ── 旁路 · 行业 brief` 之前插入:

```jsonc
  // ── 日历 · 指数调样事件(2026-09-25 指数调样事件 §2.3;用户裁定 E1「日历事实 + 生效前夜守卫」)──
  // 【生效点】scan/calendar.harvest_calendar(index_rebalance=knob)→ scan/index_events.harvest_index_events
  //   (中证公告 list→detail→xlsx 名单,六指数白名单,全量落 index_events.csv)→ calendar.csv 第三种
  //   kind=index_rebalance(只收菜单票且有生效日的行)→ calendar_flags(L4 简报 / 档案 §6 / sector pack)
  //   + calendar_section(summary 📅 市场级计数)。
  // ⚖️ 证据(17 次调样隔夜尺普查,对照 = 同指数未变动成分股):公告夜 −0.07pp、跑道 −0.10pp、
  //   **生效前夜 −0.31pp(中证500 −0.72、胜 22%)**;唯一正相位在公告前一夜且预测器不达标(spec 附录 B)。
  //   日历行是**事实日期**:只有生效前夜那一行带方向词,且方向是「禁止」(防锚定)。
  // 回滚杆 = "index_rebalance": false(一行,逐字 parity:不取网、不落文件、无新行)。
  "calendar": { "index_rebalance": true },
```

`relative_buy` 块改为:

```jsonc
  // rebalance_gate(2026-09-25 指数调样事件 §2.4,E6 v4.1):true → 扫描日 = 调样生效前夜的调样票
  //   (调入 / 调出、六指数任一)进第五硬门 rebalance_close 否决,决策文件多 index_events 块
  //   (source ok|absent / gate_evaluated / hits),brief ③ 印 ⛔ 行;false = v4.0 逐字(除 rule_version)。
  //   E2 裁定:全部调样票一刀(沪深300/A500/科创50 的生效前夜 ≈0 不亏,中证500/1000 显著负)。
  //   源缺席(calendar.index_rebalance 关或中证公告不可达)→ 门放行 + source=absent 留痕,不等于无事件。
  // 回滚杆 = "rebalance_gate": false(一行;与 calendar.index_rebalance 应同开同关)。
  "relative_buy": { "mode": "active", "exclude_pinned": true, "activate_date": "2026-08-19",
                    "pool": "finalists", "tiering": true, "rebalance_gate": true },
```

- [ ] **Step 3: STAGES.md**

`:180` 的 `P0 简报（市场地形+档案+解禁/披露旗+行业备忘+误读预警）` 改为 `P0 简报（市场地形+档案+解禁/披露/调样旗+行业备忘+误读预警）`。

`:323` E6 v4.0 段落之后新增一段:

```markdown
**E6 v4.1(2026-09-25 指数调样事件 §2.4)** —— `RULE_VERSION="e6.v4.1"`,一根总闸 `relative_buy.rebalance_gate`(默认 `false`=v4.0 逐字;**生产已开 `true`**)。开 → 第五硬门 `rebalance_close`:扫描日 = 调样生效前夜(`index_events.csv` 里 `phase=passive_close_eve`)的调样票(调入/调出、六指数任一)否决;独立计数、不并入 `no_redflag`;决策文件多 `index_events` 块(`source ok|absent`、`gate_evaluated`、`hits`)。源缺席 → 放行 + `source=absent`(「没看见」≠「没事件」)。证据:17 次调样隔夜尺普查生效前夜 −0.31pp(中证500 −0.72pp、胜 22%),见 `docs/specs/2026-09-25-index-inclusion-signal-design.md` 附录 A。数据链:prelude `calendar` 步 → `scan/index_events.py`(中证公告 list→detail→xlsx)→ `calendar.csv` 第三腿 `kind=index_rebalance` → L4 简报 ⛔/📅 行、summary 📅 市场级计数、档案 §6、sector pack。
```

`:360-363` 「已被实证否决的方向」追加两条:

```markdown
- **指数纳入当正向催化**(2026-09-25):17 次调样隔夜尺普查,公告夜 −0.07pp、生效前跑道 −0.10pp(t_day −5.4)、生效前夜 −0.31pp;「纳入=利好」在主尺不成立,只作事实日期 + 生效前夜守卫。
- **预测调样名单做席位**(2026-09-25):唯一正相位是公告前一夜(+0.7pp,完美预见),但规则复刻精度 ~50%,预测集 +0.21pp 不显著、误报票 −0.44pp/胜 19%;精度 <70% 绝不启用,重开条件见 spec §2.8。
```

- [ ] **Step 4: SKILL.md 配置表**

在 `l2.sector_seats` 行之后加:

```markdown
| 日历 | `calendar.index_rebalance` | true·false(2026-09-25 §2.3;**生产已开**) | `calendar.harvest_calendar(index_rebalance=knob)` → `index_events.harvest_index_events`(中证公告 → `index_events.csv` 全量六指数)→ `calendar.csv` 第三腿 `kind=index_rebalance` → 简报/summary/档案 §6/sector pack。回滚 = `false`(一行,parity) |
```

在 `relative_buy.tiering` 行之后加:

```markdown
| 收尾 | `relative_buy.rebalance_gate` | true·false(2026-09-25 §2.4,`RULE_VERSION="e6.v4.1"`;**生产已开**) | `relative_buy.configured_rebalance_gate` → `post_run` 两个写者 → `build_decision(rebalance_gate=…, index_events=盘上 index_events.csv)`。开:调样生效前夜的调样票进第五硬门 `rebalance_close`(独立计数);源缺席放行并记 `source=absent`。回滚 = `false`(一行) |
```

- [ ] **Step 5: spec 回写(六处差异,见本计划 Global Constraints 末条)**

`docs/specs/2026-09-25-index-inclusion-signal-design.md`:
- §2.2 产物登记行 `required_when="calendar.index_rebalance.enabled 且中证公告源可达"` → `required_when="calendar.index_rebalance 开且中证公告源可达"`;生产者段末加一句「`index_events.csv` 收六指数**全部**公告行;`calendar.csv` 第三腿才按 L2∪finalists 过滤」。
- §2.3 标题 `旋钮 \`calendar.index_rebalance.enabled\`` → `旋钮 \`calendar.index_rebalance\`(平铺布尔,镜像 l2.knife_cap)`。
- §2.4 第二条末加「配置读取用独立的 `configured_rebalance_gate()`,`configured_relative_buy()` 五元组不变」;`RULE_VERSION` 句改为「`RULE_VERSION` 无条件升 `e6.v4.1`(逐字镜像 v4.0 对 tiering 的先例);旋钮关 = 除 `rule_version` 字符串外逐字节相同(O4 判据同此)」。
- §2.7 第一条改为「`run_health.json` 加 `index_events: {source ok|absent|disabled, n_rows, n_finalists_involved, n_passive_close_eve}`(旋钮开或文件在场才出现);门命中数在决策文件 `index_events.hits` 与 brief ③,run_health 在 E6 之前写盘读不到门」。
- §2.1 detail 行备注补「取数前先找湖里任一 `<ann_id>@*.parquet`」(已写,核对措辞一致)。
- §3.1 O4 判据改为「旋钮关 → 8 个历史 run 的 `calendar.csv` 逐字节不变,`_relative_buy_decision.json` 除 `rule_version` 外逐字节不变」。

- [ ] **Step 6: 跑绿 + 全量**

Run: `uv run --no-sync python -m pytest tests/scan/test_config_knobs.py tests/scan/test_scan_config_live.py tests/test_codex_agent_defs.py -q && uv run --no-sync python -m pytest -q tests/ && uv run --no-sync ruff check autoresearch tests`
Expected: 全绿、ruff 干净。

- [ ] **Step 7: 提交**

```bash
git add .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/STAGES.md .claude/skills/scan-market/SKILL.md docs/specs/2026-09-25-index-inclusion-signal-design.md tests/scan/test_config_knobs.py
git commit -m "feat(config): switch on calendar.index_rebalance and relative_buy.rebalance_gate; sync STAGES/SKILL; write plan deviations back to the spec

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**批 B2 收尾(验收 O4–O7)**:
- O4 parity 回放:对 8 个历史 run(`reports_claude/scan/*/capsule/products/staging/<date>` 镜像到 tmp)用 `write_decision(scan)`(旋钮关)重算 → 与该 run 已发布 `_relative_buy_decision.json` 比较,**除 `rule_version` 外逐字节相同**;记录到提交说明。
- O5/O6 已由 Task 8/9 测试锁定。
- O7 变异:注释掉 `_hard_gate` 第 ⑤ 段 → `test_rebalance_gate_vetoes_the_passive_close_eve_row_and_says_where` 红;删掉 `l4-card.md` 那条规则 → `test_agent_defs` 锚红;删掉 `_rebalance_line` 调用 → `test_buy_line_prints_rebalance_eve_vetoes_with_sources` 红。三条结果写进 Task 12 提交说明。

---

# 批 B3 · 描述字段与 stock-research(2026-12 周期之后;presence-gated,缺则文案无数字)

### Task 13: ETF 规模 → `flow_adv_days`(`scan/index_flow.py`,旋钮 `calendar.index_rebalance_flow`)

**Files:**
- Create: `autoresearch/scan/index_flow.py`
- Modify: `autoresearch/scan/index_events.py`(`build_index_events` 加 `with_flow` 形参;`harvest_index_events` 读旋钮)
- Modify: `autoresearch/scan/user_config.py`(`calendar` 子键加 `index_rebalance_flow`)
- Test: `tests/scan/test_index_flow.py`(新)、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Consumes(Task 1):`fund_basic`(static;`{"market": "E", "status": "L"}`)、`fund_share`(`{"trade_date": as_of}`)、`fund_nav`(`{"nav_date": as_of, "market": "E"}`)、`index_weight`(最新月末);湖 `daily`(amount 千元)/ `daily_basic`(circ_mv 万元)。
- Produces:`BENCHMARK_PATTERNS: dict[index_code6, regex]`;`etf_aum_by_index(as_of, *, fetch=None) -> dict[str, float] | None`(亿元;任一源缺 → `None`);`flow_adv_days(events: pd.DataFrame, as_of: str, *, lake_daily=None, lake_daily_basic=None, fetch=None) -> pd.Series | None`(索引对齐 `events`,值 = 该票跨指数**净**被动流 / ADV20,天;算不出 → `None`)。`build_index_events(..., with_flow: bool = False)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_index_flow.py
"""ETF 被动规模 → flow_adv_days 描述字段(design 2026-09-25 §2.2;批 B3)。合成湖 + 假取数,零网络。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache
from autoresearch.scan import index_events as ie
from autoresearch.scan import index_flow as fl

AS_OF = "20261210"
DAYS = [f"202611{d:02d}" for d in range(2, 31) if d not in (7, 8, 14, 15, 21, 22, 28, 29)] + \
       ["20261201", "20261202", "20261203", "20261204", "20261207", "20261208", "20261209", "20261210"]


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    (root / "daily").mkdir(parents=True)
    (root / "daily_basic").mkdir(parents=True)
    monkeypatch.setattr(cache, "LAKE", root)
    for d in DAYS:
        pd.DataFrame({"ts_code": ["600221.SH", "600000.SH", "600036.SH"], "open": 10.0, "high": 10.0, "low": 10.0,
                      "close": 10.0, "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0, "vol": 1.0,
                      "amount": [1e5, 5e5, 5e5]}).to_parquet(root / "daily" / f"{d}.parquet")   # 600221 日均 1 亿
    pd.DataFrame({"ts_code": ["600221.SH", "600000.SH", "600036.SH"], "close": 10.0, "turnover_rate": 1.0,
                  "pe_ttm": 10.0, "pb": 1.0, "total_mv": 1e6, "circ_mv": [1e6, 1e6, 1e6]}).to_parquet(
        root / "daily_basic" / f"{AS_OF}.parquet")                                            # 各 100 亿流通
    return root


def _fetch(endpoint, params):
    if endpoint == "fund_basic":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "name": ["华泰柏瑞沪深300ETF", "南方中证1000ETF"],
                             "benchmark": ["沪深300指数收益率", "中证1000指数收益率"]})
    if endpoint == "fund_share":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "trade_date": params["trade_date"],
                             "fd_share": [1_000_000.0, 100_000.0], "fund_type": "ETF", "market": "SH"})  # 万份
    if endpoint == "fund_nav":
        return pd.DataFrame({"ts_code": ["510300.SH", "512100.SH"], "nav_date": params["nav_date"],
                             "unit_nav": [1.0, 1.0]})
    if endpoint == "index_weight":
        return pd.DataFrame({"index_code": params["index_code"], "con_code": ["600000.SH", "600036.SH"],
                             "trade_date": "20261130", "weight": 50.0})
    raise AssertionError(endpoint)


def test_etf_aum_by_index_sums_share_times_nav(lake):
    aum = fl.etf_aum_by_index(AS_OF, fetch=_fetch)
    assert aum["000300"] == pytest.approx(100.0) and aum["000852"] == pytest.approx(10.0)   # 亿元


def test_etf_aum_returns_none_when_any_source_is_missing(lake):
    def broken(endpoint, params):
        if endpoint == "fund_nav":
            raise RuntimeError("no permission")
        return _fetch(endpoint, params)
    assert fl.etf_aum_by_index(AS_OF, fetch=broken) is None


def test_flow_adv_days_is_net_across_indices_over_adv20(lake):
    ev = pd.DataFrame([
        {"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600221", "index_code": "000852", "index_name": "中证1000", "side": "drop", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS)
    s = fl.flow_adv_days(ev, AS_OF, fetch=_fetch)
    # 沪深300:权重代理 = 100/(100+100+100) = 1/3 → +33.33 亿;中证1000 调出:−10 × 1/3 = −3.33 亿;净 30 亿 / ADV 1 亿
    assert s.tolist() == pytest.approx([30.0, 30.0], rel=1e-3)


def test_build_index_events_with_flow_fills_the_column(lake, monkeypatch):
    monkeypatch.setattr(cache, "_real_today", lambda: AS_OF)
    from autoresearch.data.sources.csindex import DETAIL_COLS, LIST_COLS
    fl_list = lambda e, p: pd.DataFrame([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "x"]], columns=LIST_COLS)
    fl_detail = lambda e, p: pd.DataFrame([{"ann_id": "3007001", "publish_date": "20261127", "title": "t",
                                            "content_text": "将于2026年12月11日收市后生效", "attachment_url": "u",
                                            "index_code": "000300", "index_name": "沪深300", "side": "add",
                                            "code": "600221", "name": "海航控股"}], columns=DETAIL_COLS)
    monkeypatch.setattr(fl, "_default_fetch", lambda: _fetch)
    tds = [d for d in DAYS] + ["20261211", "20261214"]
    df = ie.build_index_events("2026-12-10", today=AS_OF, fetch_list=fl_list, fetch_detail=fl_detail,
                               trading_days=tds, with_flow=True)
    assert df.flow_adv_days.iloc[0] == pytest.approx(33.33, rel=1e-3)
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest tests/scan/test_index_flow.py -q` → Expected: FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 实现 `index_flow.py`**

```python
# autoresearch/scan/index_flow.py
#!/usr/bin/env python3
"""ETF 被动规模 → 调样票「被动买入 ≈ x 天 ADV」描述字段(design 2026-09-25 §2.2 `flow_adv_days`;批 B3)。

只做描述:不进任何门、不进排序、不改评级。三源(fund_basic / fund_share / fund_nav)任一缺 → None,字段留空
(空 = 未计算,不是 0)。口径:
  AUM_index(亿) = Σ 非增强/非联接 ETF 的 fd_share(万份)× unit_nav / 1e4
  weight_proxy(code, index) = circ_mv(code) / Σ circ_mv(该指数最新月末成分 ∪ 本次调入票)   —— 自由流通市值近似
  flow(亿) = Σ_index ± AUM_index × weight_proxy(调入 +,调出 −)                             —— 跨指数净额
  flow_adv_days(code) = flow / ADV20(亿;湖 daily.amount 千元 → 亿 = ×1e3/1e8),写在该票每一行上
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan.index_events import INDEX_WHITELIST

BENCHMARK_PATTERNS: dict[str, str] = {
    "000300": r"沪深300",
    "000905": r"中证500(?!成长|价值|信息|医药|红利|低波|行业中性|ESG)",
    "000852": r"中证1000(?!成长|价值)",
    "000510": r"中证A500",
    "000688": r"科创50|上证科创板50",
    "399006": r"创业板指数|创业板指(?!成长|50|动量|中盘)",
}
_EXCLUDE_NAME = re.compile(r"增强|ESG|联接|LOF")


def _default_fetch():
    return None


def _index_of(benchmark: str) -> str | None:
    for idx, pat in BENCHMARK_PATTERNS.items():
        if re.search(pat, str(benchmark)):
            return idx
    return None


def etf_aum_by_index(as_of: str, *, fetch=None) -> dict[str, float] | None:
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    fetch = fetch if fetch is not None else _default_fetch()
    try:
        basic = cache.get_or_fetch("fund_basic", {"market": "E", "status": "L"}, today=as_of, fetch=fetch)
        share = cache.get_or_fetch("fund_share", {"trade_date": as_of}, today=as_of, fetch=fetch)
        nav = cache.get_or_fetch("fund_nav", {"nav_date": as_of, "market": "E"}, today=as_of, fetch=fetch)
    except Exception as e:  # noqa: BLE001
        record_degradation("fund_share", f"ETF 规模三源之一不可达({type(e).__name__}: {e})→ flow_adv_days 留空", key=as_of)
        return None
    if basic is None or share is None or nav is None or basic.empty or share.empty or nav.empty:
        return None
    b = basic[~basic["name"].astype(str).str.contains(_EXCLUDE_NAME)].copy()
    b["idx"] = b["benchmark"].map(_index_of)
    b = b[b["idx"].notna()]
    m = (b.merge(share[["ts_code", "fd_share"]], on="ts_code", how="inner")
          .merge(nav[["ts_code", "unit_nav"]].drop_duplicates("ts_code"), on="ts_code", how="inner"))
    m["aum_yi"] = m["fd_share"].astype(float) * m["unit_nav"].astype(float) / 1e4
    out = m.groupby("idx")["aum_yi"].sum().to_dict()
    return {k: float(v) for k, v in out.items()}


def _members(index_code6: str, as_of: str, fetch) -> set[str]:
    import calendar as _cal

    from autoresearch.data import cache
    y, m = int(as_of[:4]), int(as_of[4:6])
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)                      # 最新已发布月末 = 上月末
    end = f"{py}{pm:02d}{_cal.monthrange(py, pm)[1]:02d}"
    suffix = ".SZ" if index_code6.startswith("399") else ".SH"
    df = cache.get_or_fetch("index_weight", {"index_code": index_code6 + suffix, "start_date": f"{py}{pm:02d}01",
                                             "end_date": end}, today=end, fetch=fetch)
    if df is None or df.empty:
        return set()
    last = df["trade_date"].astype(str).max()
    return set(df[df["trade_date"].astype(str) == last]["con_code"].astype(str).str[:6])


def _adv20_yi(codes: set[str], as_of: str, lake_daily: Path | None) -> dict[str, float]:
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    days = sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit() and p.stem[:8] <= as_of)[-20:]
    frames = [pd.read_parquet(d / f"{day}.parquet", columns=["ts_code", "amount"]) for day in days]
    if not frames:
        return {}
    px = pd.concat(frames, ignore_index=True)
    px["code"] = px["ts_code"].astype(str).str[:6]
    px = px[px["code"].isin(codes)]
    return (px.groupby("code")["amount"].mean() * 1e3 / 1e8).to_dict()


def _circ_mv(as_of: str, lake_daily_basic: Path | None) -> dict[str, float]:
    d = Path(lake_daily_basic) if lake_daily_basic else ws.lake_root() / "daily_basic"
    days = sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit() and p.stem[:8] <= as_of)
    if not days:
        return {}
    df = pd.read_parquet(d / f"{days[-1]}.parquet", columns=["ts_code", "circ_mv"])
    return dict(zip(df["ts_code"].astype(str).str[:6], df["circ_mv"].astype(float)))


def flow_adv_days(events: pd.DataFrame, as_of: str, *, lake_daily: Path | None = None,
                  lake_daily_basic: Path | None = None, fetch=None) -> pd.Series | None:
    if events is None or events.empty:
        return None
    fetch = fetch if fetch is not None else _default_fetch()
    aum = etf_aum_by_index(as_of, fetch=fetch)
    if aum is None:
        return None
    circ = _circ_mv(as_of, lake_daily_basic)
    adv = _adv20_yi(set(events["code"].astype(str)), as_of, lake_daily)
    flow_by_code: dict[str, float] = {}
    for idx in sorted(set(events["index_code"].astype(str))):
        if idx not in INDEX_WHITELIST or idx not in aum:
            continue
        rows = events[events["index_code"].astype(str) == idx]
        adds = set(rows[rows["side"] == "add"]["code"].astype(str))
        denom = sum(circ.get(c, 0.0) for c in (_members(idx, as_of, fetch) | adds))
        if denom <= 0:
            continue
        for r in rows.itertuples(index=False):
            w = circ.get(str(r.code), 0.0) / denom
            sign = 1.0 if r.side == "add" else -1.0
            flow_by_code[str(r.code)] = flow_by_code.get(str(r.code), 0.0) + sign * aum[idx] * w
    vals = []
    for r in events.itertuples(index=False):
        a = adv.get(str(r.code))
        f = flow_by_code.get(str(r.code))
        vals.append(None if not a or f is None else round(f / a, 2))
    return pd.Series(vals, index=events.index, dtype="float64")
```

- [ ] **Step 4: 接进 `index_events`**

`build_index_events` 签名加 `with_flow: bool = False`,`return pd.DataFrame(rows, columns=EVENT_COLS)` 改为:

```python
    df = pd.DataFrame(rows, columns=EVENT_COLS)
    if with_flow and len(df):
        from autoresearch.scan.index_flow import flow_adv_days
        try:
            flow = flow_adv_days(df, day)
            if flow is not None:
                df["flow_adv_days"] = flow.values
        except Exception as e:  # noqa: BLE001 — 描述字段算不出不挡事件表
            print(f"[index_events] flow_adv_days 计算失败({e!r})→ 留空", file=sys.stderr)
    return df
```

`harvest_index_events` 里 `build_index_events(scan_date, **kw)` 前加 `kw.setdefault("with_flow", bool(knob("calendar", "index_rebalance_flow", None, False)))`(模块顶部 `from autoresearch.scan.user_config import knob`)。

`user_config`:`_SUB_WHITELIST["calendar"] = {"index_rebalance", "index_rebalance_flow"}`;`_KNOB_TYPES[("calendar", "index_rebalance_flow")] = (_t_bool, "boolean")`,注释「ETF 规模描述字段;true → prelude 多两次 tushare 调用(fund_share/fund_nav);false(默认)= 字段留空」。`tests/scan/test_config_knobs.py` 加一条布尔类型断言(同 Task 5 形状)。

- [ ] **Step 5: 跑绿 → 提交**

Run: `uv run --no-sync python -m pytest tests/scan/test_index_flow.py tests/scan/test_index_events.py tests/scan/test_config_knobs.py -q` → Expected: PASS。

```bash
git add autoresearch/scan/index_flow.py autoresearch/scan/index_events.py autoresearch/scan/user_config.py tests/scan/test_index_flow.py tests/scan/test_config_knobs.py
git commit -m "feat(scan): flow_adv_days — ETF passive AUM × float-cap weight proxy over ADV20, net across indices (presence-gated)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: stock-research 确定性「指数成分 / 调样事件」行

**Files:**
- Create: `autoresearch/analyze/index_membership.py`
- Modify: `autoresearch/analyze/blocks_ashare.py:274-320`(`ashare_corporate_calendar` 尾段两处文案)
- Modify: `.claude/skills/stock-research/engine-playbook.md:98`
- Test: `tests/analyze/test_index_membership.py`(新)

**Interfaces:**
- Produces:`index_membership_lines(code6: str, curr_date: str, *, lake_root: Path | None = None) -> str`——只读湖(glob,零网络):`**指数成分**:当前属于 沪深300 · 中证A500(最新月末快照 20261130)` / `当前不属于六大指数(快照 …)` / `指数成分快照:湖内无`;`**调样事件(近 60 日)**:2026-11-27 公告 沪深300 调入,2026-12-11 收盘生效` / `近 60 日无调样事件(源可达)` / `调样事件:源无近 60 日快照`。

- [ ] **Step 1: 写失败测试**

```python
# tests/analyze/test_index_membership.py
"""stock-research 确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6):只读湖,零网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.analyze.index_membership import index_membership_lines


def _lake(tmp_path):
    root = tmp_path / "lake"
    (root / "index_weight").mkdir(parents=True)
    (root / "csindex_rebalance_detail").mkdir(parents=True)
    pd.DataFrame({"index_code": "000300.SH", "con_code": ["600221.SH", "600000.SH"], "trade_date": "20261130",
                  "weight": 0.1}).to_parquet(root / "index_weight" / "000300_SH@20261130.parquet")
    pd.DataFrame({"index_code": "000510.SH", "con_code": ["600221.SH"], "trade_date": "20261130",
                  "weight": 0.2}).to_parquet(root / "index_weight" / "000510_SH@20261130.parquet")
    pd.DataFrame([{"ann_id": "3007001", "publish_date": "20261127", "title": "t",
                   "content_text": "将于2026年12月11日收市后生效", "attachment_url": "u",
                   "index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"}]
                 ).to_parquet(root / "csindex_rebalance_detail" / "3007001@20261127.parquet")
    return root


def test_membership_and_recent_event_lines(tmp_path):
    root = _lake(tmp_path)
    s = index_membership_lines("600221", "2026-12-10", lake_root=root)
    assert "当前属于 沪深300 · 中证A500" in s and "20261130" in s
    assert "2026-11-27 公告 沪深300 调入,2026-12-11 收盘生效" in s


def test_non_member_and_no_event_lines(tmp_path):
    root = _lake(tmp_path)
    s = index_membership_lines("600000", "2026-12-10", lake_root=root)
    assert "当前属于 沪深300" in s and "中证A500" not in s
    assert "近 60 日无调样事件(源可达)" in s


def test_absent_lake_is_named_not_faked(tmp_path):
    s = index_membership_lines("600221", "2026-12-10", lake_root=tmp_path / "nolake")
    assert "指数成分快照:湖内无" in s and "调样事件:源无近 60 日快照" in s
```

- [ ] **Step 2: 跑红** → `uv run --no-sync python -m pytest tests/analyze/test_index_membership.py -q` → FAIL(`ModuleNotFoundError`)。

- [ ] **Step 3: 实现**

```python
# autoresearch/analyze/index_membership.py
#!/usr/bin/env python3
"""stock-research full 档的确定性「指数成分 / 调样事件」行(design 2026-09-25 §2.6)。

替代 `blocks_ashare` 里「指数调样 → WebSearch 补」的兜底。**只读湖、零网络**:成分看 `lake/index_weight/<idx>@*.parquet`
最新一份,事件看 `lake/csindex_rebalance_detail/*.parquet` 近 60 日。缺什么就写缺什么,不编。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.sources.csindex import parse_effective_date
from autoresearch.scan.index_events import INDEX_WHITELIST


def _fmt(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else d


def _membership(code6: str, lake: Path) -> tuple[list[str], str | None]:
    names, snap = [], None
    for idx, name in INDEX_WHITELIST.items():
        files = sorted((lake / "index_weight").glob(f"{idx}_*@*.parquet"))
        if not files:
            continue
        df = pd.read_parquet(files[-1], columns=["con_code", "trade_date"])
        snap = max(snap or "", str(df["trade_date"].astype(str).max()))
        if code6 in set(df["con_code"].astype(str).str[:6]):
            names.append(name)
    return names, snap


def _recent_events(code6: str, curr_date: str, lake: Path, days: int = 60) -> tuple[list[str], bool]:
    cur = curr_date.replace("-", "")[:8]
    since = (dt.datetime.strptime(cur, "%Y%m%d").date() - dt.timedelta(days=days)).strftime("%Y%m%d")
    files = sorted((lake / "csindex_rebalance_detail").glob("*.parquet"))
    lines, any_recent = [], False
    for f in files:
        df = pd.read_parquet(f)
        if df.empty:
            continue
        pub = str(df["publish_date"].iloc[0])
        if not (since <= pub <= cur):
            continue
        any_recent = True
        eff, kind = parse_effective_date(str(df["content_text"].iloc[0] or ""))
        eff_txt = f"{_fmt(eff)} {'收盘' if kind == 'after_close' else '起'}生效" if eff else "生效日待公告"
        mine = df[(df["code"].astype(str) == code6) & df["index_code"].astype(str).str[:6].isin(INDEX_WHITELIST)]
        for r in mine.itertuples(index=False):
            lines.append(f"{_fmt(pub)} 公告 {INDEX_WHITELIST[str(r.index_code)[:6]]} {'调入' if r.side == 'add' else '调出'},{eff_txt}")
    return lines, any_recent


def index_membership_lines(code6: str, curr_date: str, *, lake_root: Path | None = None) -> str:
    lake = Path(lake_root) if lake_root else ws.lake_root()
    names, snap = _membership(code6, lake)
    if snap is None:
        m = "**指数成分**:指数成分快照:湖内无(未取数)。"
    elif names:
        m = f"**指数成分**:当前属于 {' · '.join(names)}(最新月末快照 {snap})。"
    else:
        m = f"**指数成分**:当前不属于六大指数(快照 {snap})。"
    lines, any_recent = _recent_events(code6, curr_date, lake)
    if lines:
        e = "**调样事件(近 60 日)**:" + ";".join(lines) + "。事实日期非方向;生效前夜买入历史隔夜为负(spec 2026-09-25 §1)。"
    elif any_recent:
        e = "**调样事件(近 60 日)**:近 60 日无调样事件(源可达)。"
    else:
        e = "**调样事件(近 60 日)**:调样事件:源无近 60 日快照。"
    return m + "\n\n" + e
```

- [ ] **Step 4: 接进 `blocks_ashare.ashare_corporate_calendar`**

把函数末尾 `out.append("> 业绩预告窗口…指数调样 → 推理时用 **WebSearch** 补成完整催化日历,标注『实时网查』。")` 改为:

```python
    try:
        from autoresearch.analyze.index_membership import index_membership_lines
        out.append(index_membership_lines(code, curr_date))
    except Exception as e:  # noqa: BLE001 — 只读湖失败不挡日历块
        out.append(f"_指数成分/调样事件读湖失败: {e}_")
    out.append("> 业绩预告窗口(A股 1月底/4月底强制)、政策窗口(政治局会议/两会/降准降息)→ 推理时用 **WebSearch** 补,"
               "标注『实时网查』。指数调样已由上方确定性行供给,**不再网查**。")
```

`except ImportError:` 分支那句「…、指数调样 → 推理时 WebSearch 补」同样删掉「指数调样」。docstring 的 `业绩预告/政策窗口/调样 left to WebSearch` 改为 `业绩预告/政策窗口 left to WebSearch; 指数成分/调样事件 deterministic (index_membership)`。

`.claude/skills/stock-research/engine-playbook.md:98` 的「推理时 WebSearch 补政策窗口/调样(标注『实时网查』)」改为「推理时 WebSearch 补政策窗口(标注『实时网查』);**指数成分与调样事件由确定性行供给(`analyze/index_membership`,只读湖),不网查**」。

- [ ] **Step 5: 跑绿 → 提交**

Run: `uv run --no-sync python -m pytest tests/analyze -q` → Expected: PASS。

```bash
git add autoresearch/analyze/index_membership.py autoresearch/analyze/blocks_ashare.py .claude/skills/stock-research/engine-playbook.md tests/analyze/test_index_membership.py
git commit -m "feat(analyze): deterministic index membership / rebalance-event lines replace the WebSearch punt

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 真跑验收清单(操作项,非代码;spec §3.2;两引擎各自计)

| # | 日期 | 读 | 通过判据 |
|---|---|---|---|
| L1 | 2026-11-27 晚扫描 | `index_events.csv` 行数;对账 `csindex_rebalance_detail` 湖内该公告行数 | > 0,六指数行数与附件一致 |
| L2 | 11-30 → 12-09 | finalist 涉及调样票时 L4 简报出现 📅 事实行;这些卡评级分布 vs 同画像非调样票 | 无系统性抬升(防锚定观察项,同席位稿 :93) |
| L3 | **2026-12-10(E−1)** | 决策文件 `index_events.gate_evaluated=true`;候选中调样票全部 `rebalance_close=false`;brief ③ 有 ⛔ 行;两引擎卡入场行为 `禁止`(`card_contract_lint` 无「调样前夜入场允许」warn) | 全部成立 |
| L4 | 12-11 收盘后 | summary 📅 出现「指数调样 20261211 收盘生效」行 | 出现 |
| L5 | 12-14 起 | 被拦票实现 `gap_c1_o2` vs 同指数未变动成分股 | **只记录**(n 太小不设门);进 `docs/research/` 半年读数 |

规则冻结:12 月周期内不改任何阈值;L3 若不成立,先查 `index_events.csv` 在场否、旋钮两键是否同开、`degraded.json` 有无 csindex 行,再改代码。

---

## 计划自审(写完对照 spec 逐节核)

**Spec 覆盖**

| spec 节 | 任务 |
|---|---|
| §2.1 数据源与登记(7 端点 / B 级 / 键参数 / adapter) | Task 1、Task 2 |
| §2.2 事件表(列、相位、缺席语义、白名单、跨指数不合并)+ ARTIFACTS 先登记 | Task 3 |
| §2.3 日历第三腿 + 两种文案 + 市场级行 + 旋钮 | Task 5、Task 6 |
| §2.4 E6 第五门 + 决策块 + I/O 边界 + RULE_VERSION + 旋钮 | Task 8、Task 9 |
| §2.5 L4 卡规则(两引擎)+ self_review warn | Task 11 |
| §2.6 stock-research 确定性行 | Task 14 |
| §2.7 观察义务:run_health / brief ③ / buyability 子原因(现有 `wall="gates"` 已能读出 `hard_gate.rebalance_close` 桶,不新增 wall 态) | Task 7、Task 10 |
| §2.2 `flow_adv_days` 描述字段 | Task 13 |
| §2.8 路线 C 负结果(只写文档) | Task 12(STAGES 否决方向两条) |
| §3.1 O1–O3 | Task 1/2/3 测试;O4 批 B2 收尾;O5/O6 Task 8;O7 各批收尾;O8 Task 4 |
| §3.2 L1–L5 | 真跑验收清单 |
| §4 批次与回滚杆 | B0 惰性 / B1 `calendar.index_rebalance` / B2 `relative_buy.rebalance_gate` / B3 `calendar.index_rebalance_flow` + presence-gated |
| §6 文档同步 | Task 12、Task 14 |
| §7 明确不做 | Global Constraints |

**占位符扫描**:全文无 TBD/TODO/「类似 Task N」;每个代码步骤给出真实代码;Step 11 of Task 8 的 grep 是明确动作(改断言字面量)。

**类型/名字一致性**:`index_events.EVENT_COLS / load_index_events / events_by_code / write_index_events / harvest_index_events / INDEX_WHITELIST`(Task 3 定义;Task 5/7/8/9/11/13/14 消费同名);`REBALANCE_GATE / _hard_gates / configured_rebalance_gate / build_decision(index_events=, rebalance_gate=)`(Task 8 定义;Task 9/10 消费);决策块键 `source / gate_evaluated / n_rows / n_candidates_in_events / hits`(Task 8 产出;Task 10 `relative_facts["rebalance"]` 原样透传、brief 读 `source` 与 `hits`);日历行 `detail="{index_name} {调入|调出}|{phase}"`(Task 5 产出;Task 6 用 `partition("|")` 解析);简报锚串 `⛔ **指数调样生效前夜**`(Task 6 产出;Task 11 agent 规则与锚测试引用同串);`csindex.LIST_COLS / DETAIL_COLS / parse_effective_date`(Task 2 定义;Task 3/14 消费)。

**与 spec 的差异**已在 Global Constraints 末条列出六处,Task 12 Step 5 回写。

---

## 执行交接

用户 2026-09-25 裁定「可以,暂不开始开发」:本计划**只写不跑**。开工时机 = 可买性对齐波批 4 十日真跑结束后;B0 可提前在 worktree 开发但合并同样等批 4 结束;B1+B2 须在 2026-11-27 前合并。开工时两种方式:① subagent-driven(推荐;每任务一个新 subagent + 两阶段复审,`superpowers:subagent-driven-development`);② 本会话 inline(`superpowers:executing-plans`,按批设检查点)。
