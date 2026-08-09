#!/usr/bin/env python3
"""scan_config.json —— 用户配置层(白名单加载 + ScanConfig 映射)。全波地基(Plan A3 Task 1)。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.2。

用户在 `.claude/skills/scan-market/scan_config.jsonc` 里管控 scan-market 全程用到的 agent
model/effort、召回旋钮、保送参数、红队触发率、卡片复用参数——**白名单外的键一律
raise**(防拼写错静默失效,是本文件存在的唯一理由);缺文件 = 现行为(`{}`,一切默认关=parity)。

装载链(技术约束:workflow 脚本无文件系统访问):`frame --json`(Stage 0)读入本模块 →
`resolve_agent_config` 解释成 resolved spec → `materialize_agent_config` 落
`context/scan/<date>/_resolved_agent_config.json` + 回显进 market_pack/run meta
(trace 记录本次跑用的配置=可复现)→ workflow 经 `args.config.resolved_agents` 消费 →
`autoresearch.trace.usage_reconcile` 对**同一份 resolved** 对账。

**Wave12-T33**:model/effort 的"解释"从三个 workflow 各抄一份 `AGENT_DEFAULTS`
收敛到本模块的 `_ROLE_FALLBACK` 一处;workflow 侧的表降为「resolved 没传到时」的兜底。
同波删掉 `apply_to_scan_config()`——它自 2026-07-11 落地起**生产零调用点**(08-09 复核:
全仓非测试引用只有两处 docstring),是一处会让人误以为"配置已经喂进 ScanConfig 了"的
文档-实现落差;真实消费路径是各消费点自己 `load_user_config()` 取需要的块。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_PATH = Path(".claude/skills/scan-market/scan_config.jsonc")
DEFAULT_PINNED_PATH = Path(".claude/skills/scan-market/pinned.jsonc")


def _strip_jsonc(text: str) -> str:
    """去掉 JSONC 的 `//` 行注释与 `/* */` 块注释(字符串内的 `//` 原样保留)→ 供 `json.loads`。

    让 `.claude/skills/scan-market/*.json` 能给每个 key 写行内说明(纯 JSON 不支持注释)。
    字符状态机:只有双引号 `"` 切换字符串态,转义 `\\` 原样带下一字符,故串内的 `//`/`/*` 不误删。
    """
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:          # 转义:原样保留下一字符(含 \" )
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":      # // 行注释 → 跳到行尾(留换行)
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":      # /* */ 块注释
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _read_jsonc(p: Path):
    """读 JSONC 文件 → 去注释 → `json.loads`。"""
    return json.loads(_strip_jsonc(p.read_text(encoding="utf-8")))

# 顶层白名单;funnel/pinned/reuse/l4_intel/l3 额外校验子键。agents 子键是 role 闭集
# (Wave11 B1:_AGENT_ROLES,T7 的 jsonc 键/T8 的 AGENT_DEFAULTS/T9 的 reconcile 全都以它
# 为词表)——每个 role 下只认 model/effort 两个子键,值也做枚举校验(见 load_user_config
# 内 agents 校验块),不再是"消费方各自解释"的自由形状。
# l3:两遍法分诊(design 2026-07-12-l3-merge-plan.md Task 1)——two_pass/pass1_target 由
# `l3_select.prepare_l3_table` 消费;finalist_max 由 merge v3 消费(`write_finalists` 已接线,
# cap=min(finalist_max, budget))。
# learning:基率收缩估计(brainstorm 2026-07-12 §4 P0-3)——shrink/shrink_k 由
# `autoresearch.learning.shrink.shrink_config` 消费,四消费点(l4_card.write_base_rates/
# cross_calib.flip_stats/buy_ledger 的 target_calib/gate_ledger 的 tail_rate)各自读取。
# 默认 shrink=true·shrink_k=15(新基线);本块是回滚杆,不是 opt-in。
_TOP_WHITELIST = {
    "agents", "funnel", "pinned", "l4_intel", "l3",
    "learning", "budgets", "performance",
}
_SUB_WHITELIST = {
    "funnel": {"recall_channels", "channel_quotas", "channel_floors"},
    "pinned": {"cap", "ttl_days"},
    "l4_intel": {"enabled", "max_queries"},
    "l3": {"two_pass", "pass1_target", "finalist_max"},
    "learning": {"shrink", "shrink_k"},
    "budgets": {
        "cache_hit_min", "stage_cost_usd", "stage_wall_seconds", "concurrency",
        "min_real_scans", "baseline_run",
    },
    "performance": {
        "streaming_l4",
    },
}

# agents={role: {model, effort}} 的 role 闭集(Wave11 B1)——白名单外一律 raise,防拼写错
# 静默掉回缺省(如 t1_diag 拼成 t1diag,不会报错只会静默丢配置)。7 个现役(strategist/
# sector_brief/l3_rank/l4_intel/l4_card/t1_diag/t1_synth,均已见于生产 scan_config.jsonc)
# + 5 个下一波(ens_review/l3_repair/dossier_init/gp_shell/gp_shell_json)先占位入闭集,
# 免得那几个 task 往配置里写 role 时被本校验拦住。
_AGENT_ROLES = {
    "strategist", "sector_brief", "l3_rank", "l4_intel", "l4_card",
    "t1_diag", "t1_synth",
    "ens_review", "l3_repair", "dossier_init", "gp_shell", "gp_shell_json",
}
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_MODELS = {"haiku", "sonnet", "opus"}

# ─────────────── Wave12-T33:resolved agent config(单一事实源 materialize)───────────────
#
# 背景:此前 model/effort 的"生效值"是三方拼出来的 —— config > 各 workflow 顶部的
# `AGENT_DEFAULTS` > `.claude/agents/*.md` frontmatter。三个 workflow **各自**抄一份
# AGENT_DEFAULTS,谁也不是权威;`usage_reconcile` 又内联第三份缺省(`_GP_SHELL_DEFAULT`)。
# 「配置的单一事实源」当时只做到了"用户面只有一处",没做到"解释也只有一处"。
#
# 本块把**解释**收进来:`resolve_agent_config()` 产出逐 role 的 resolved spec,
# `materialize_agent_config()` 落 `context/scan/<date>/_resolved_agent_config.json`,
# workflow 只消费 resolved、不再各自解释缺省,`usage_reconcile` 对同一份 resolved 对账。
#
# ⚠️ `model` 键的在场与否是**有语义的**,不能"补全"成好看的样子:
# 判断类 role(l3_rank/l4_card/…)故意不给 model 缺省,好让它落到 agent def frontmatter
# 那一层;resolved 里给它硬塞一个 model,workflow 就会显式传 model,**回退链第三层从此
# 永远吃不到**——那是行为变更,不是重构。所以下表里判断类 role 只有 effort。
_ROLE_FALLBACK: dict[str, dict] = {
    # 壳类(执行壳机械参数,不是 agent 档位):08-05 事故后钉 sonnet,不得静默回落
    "gp_shell":      {"model": "sonnet", "effort": "low"},
    "gp_shell_json": {"model": "sonnet", "effort": "low"},
    # 判断类:只给 effort,model 留空 → 落各自 agent def frontmatter
    "strategist":    {"effort": "high"},
    "sector_brief":  {"effort": "high"},
    "l3_rank":       {"effort": "max"},
    "l3_repair":     {"effort": "medium"},
    "l4_intel":      {"effort": "max"},
    "l4_card":       {"effort": "xhigh"},
    "ens_review":    {"effort": "xhigh"},
    "dossier_init":  {"effort": "max"},
    "t1_diag":       {"effort": "high"},
    "t1_synth":      {"effort": "high"},
}

#: 生产必填 role —— 生产 `scan_config.jsonc` 必须**显式列全**(12 个)。缺一即 fail-fast。
#: 为什么是"全部"而不是某个子集:闭集的意义就是"这张表就是全集";允许缺就等于允许
#: "写了一半、剩下的靠猜",而 07-21 事故的全部教训就是**猜出来的缺省没人看得见**。
_REQUIRED_AGENT_ROLES = frozenset(_AGENT_ROLES)

RESOLVED_FILENAME = "_resolved_agent_config.json"
RESOLVED_SCHEMA_VERSION = 1


def load_user_config(path: str | Path | None = None) -> dict:
    """读 scan_config.json → 白名单校验后的 dict;缺文件 → `{}`(=现行为,parity)。

    未知顶层键、或 funnel/pinned/reuse/l4_intel 内未知子键 → `ValueError`(消息含具体键名)。
    """
    p = Path(path) if path is not None else DEFAULT_PATH
    if not p.exists():
        return {}
    cfg = _read_jsonc(p)

    unknown_top = sorted(set(cfg) - _TOP_WHITELIST)
    if unknown_top:
        raise ValueError(f"scan_config.json 含未知顶层键: {unknown_top}(白名单={sorted(_TOP_WHITELIST)})")

    for key, sub_whitelist in _SUB_WHITELIST.items():
        block = cfg.get(key)
        if isinstance(block, dict):
            unknown_sub = sorted(set(block) - sub_whitelist)
            if unknown_sub:
                raise ValueError(f"scan_config.json 的 {key} 含未知子键: {unknown_sub}"
                                 f"(白名单={sorted(sub_whitelist)})")
    performance = cfg.get("performance")
    if performance is not None:
        if not isinstance(performance, dict):
            raise ValueError("scan_config.json 的 performance 必须是 object")
        for key in ("streaming_l4",):
            if key in performance and not isinstance(performance[key], bool):
                raise ValueError(f"scan_config.json performance.{key} 必须是 boolean")

    agents = cfg.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            raise ValueError("scan_config.json 的 agents 必须是 object")
        unknown = sorted(set(agents) - _AGENT_ROLES)
        if unknown:
            raise ValueError(f"scan_config.json agents 含未知 role: {unknown}"
                             f"(闭集={sorted(_AGENT_ROLES)})")
        for role, spec in agents.items():
            if spec is not None and not isinstance(spec, dict):
                raise ValueError(f"agents.{role} 必须是 object,形如 "
                                 f'{{"model": "...", "effort": "..."}}(实际={spec!r})')
            spec = spec or {}
            bad = sorted(set(spec) - {"model", "effort"})
            if bad:
                raise ValueError(f"agents.{role} 含未知子键: {bad}(只认 model/effort)")
            if "effort" in spec and (not isinstance(spec["effort"], str) or spec["effort"] not in _EFFORTS):
                raise ValueError(f"agents.{role}.effort={spec['effort']!r} 非法(∈{sorted(_EFFORTS)})")
            if "model" in spec and (not isinstance(spec["model"], str) or spec["model"] not in _MODELS):
                raise ValueError(f"agents.{role}.model={spec['model']!r} 非法(∈{sorted(_MODELS)})")
    return cfg


def resolve_agent_config(cfg: dict, *, require_all: bool = True) -> dict:
    """`scan_config.jsonc` → 逐 role 的 **resolved** spec(`{role: {model?, effort}}`)。

    这是 model/effort 唯一的"解释点":config 覆盖在 `_ROLE_FALLBACK` 之上,产出的东西
    workflow 直接吃,不再各自解释缺省。

    **四条 fail-fast**(不是洁癖,是 2026-07-21 事故的结构性防线 —— 那天配置真身是
    `.jsonc` 却按 `.json` 去找,查无 → 传了空 config → intel 被静默关掉、全体 agent 掉回
    缺省 effort,而**报告上看不出来**,事后翻记录才发现):

    1. `agents` 为空 / 整个 cfg 为 `{}` → `ValueError`。"什么都没配"必须炸,不能悄悄全用缺省。
    2. 未知 role → `ValueError`(拼写错静默失效是同一类病:`t1_diag` 写成 `t1diag` 不报错,
       只是那行配置从此不存在)。
    3. role 下未知字段 / 非法 model / 非法 effort → `ValueError`。
    4. `require_all`(生产默认)时缺任一必填 role → `ValueError`,消息列出缺哪几个。

    `require_all=False` 供**局部编排**用(如 scan-retro 只拉 t1-review):它仍然校验写了的
    那些,只是不要求写全 —— 但那样产出的 resolved 是**残表**,不该落盘冒充当日全量。
    """
    if not isinstance(cfg, dict) or not cfg:
        raise ValueError(
            "scan_config 为空 —— 空配置会静默关 intel + 全体掉回缺省 effort 且报告上看不出来"
            "(2026-07-21 事故)。要么给一份显式配置,要么显式走 allow_empty 的离线路径。")
    agents = cfg.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise ValueError("scan_config.agents 为空 —— 同上,agent 档位必须显式声明,不接受"
                         "「不写=用缺省」(那正是 07-21 事故看不见的那一半)")

    unknown = sorted(set(agents) - _AGENT_ROLES)
    if unknown:
        raise ValueError(f"scan_config agents 含未知 role: {unknown}(闭集={sorted(_AGENT_ROLES)})")

    resolved: dict[str, dict] = {}
    for role in sorted(_AGENT_ROLES):
        if role not in agents:
            continue          # 缺项交给下面汇总一次报全,别一个一个抛(读的人要一眼看到缺哪几个)
        spec = agents[role]
        if spec is not None and not isinstance(spec, dict):
            raise ValueError(f"agents.{role} 必须是 object,形如 "
                             f'{{"model": "...", "effort": "..."}}(实际={spec!r})')
        spec = spec or {}
        bad = sorted(set(spec) - {"model", "effort"})
        if bad:
            raise ValueError(f"agents.{role} 含未知子键: {bad}(只认 model/effort)")
        if "effort" in spec and spec["effort"] not in _EFFORTS:
            raise ValueError(f"agents.{role}.effort={spec['effort']!r} 非法(∈{sorted(_EFFORTS)})")
        if "model" in spec and spec["model"] not in _MODELS:
            raise ValueError(f"agents.{role}.model={spec['model']!r} 非法(∈{sorted(_MODELS)})")
        resolved[role] = {**_ROLE_FALLBACK.get(role, {}), **spec}

    if require_all:
        missing = sorted(_REQUIRED_AGENT_ROLES - set(resolved))
        if missing:
            raise ValueError(
                f"scan_config agents 缺生产必填 role: {missing} —— 闭集必须显式列全,"
                f"「不写=用缺省」的那一半永远没人看得见(07-21 事故)")
    return resolved


def materialize_agent_config(date: str, cfg: dict | None = None, *,
                             root: str | Path | None = None,
                             require_all: bool = True) -> Path:
    """把 resolved spec 落 `<root>/context/scan/<date>/_resolved_agent_config.json`。

    这份产物是 **workflow 与 `usage_reconcile` 共同的事实源**:前者照着它派发,后者
    照着它对账 —— 「期望」与「实测」终于在比同一张表,而不是各自重新解释一遍缺省。
    """
    resolved = resolve_agent_config(load_user_config() if cfg is None else cfg,
                                    require_all=require_all)
    base = Path(root) if root is not None else Path(".")
    out = base / "context" / "scan" / str(date) / RESOLVED_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": RESOLVED_SCHEMA_VERSION, "date": str(date),
               "roles": resolved}
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def load_resolved_agent_config(scan_dir: str | Path) -> dict:
    """读 resolved 产物 → `{role: spec}`;缺文件/坏文件 → `{}`(presence-gated,由调用方决定要不要炸)。"""
    path = Path(scan_dir) / RESOLVED_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    roles = payload.get("roles")
    return roles if isinstance(roles, dict) else {}


# ───────────────────────── pinned.json:保送票 loader(cap/TTL) ─────────────────────────
#
# design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.1。plan Task 3。
# 用户在 `.claude/skills/scan-market/pinned.jsonc` 里手工保送 ≤cap 只票,L1→L5 全程强制在场
# (不占各段名额、不挤他票——见 autoresearch.scan.universe.recall_select 的 `pinned=` 形参
# 与 autoresearch.scan.recall.l2_stratify.select_l2 的 `pinned` 列自动识别)。本函数只管
# 读文件 + 分类(kept/expired)+ cap 截断,不碰漏斗本身。


def _add_trading_days_approx(d: date, n: int) -> date:
    """`d` 之后第 `n` 个"交易日"的近似值:跳过周六/周日的自然日推进,**不排节假日**。

    精确交易日历见 `autoresearch.data.tushare_source._trade_days`(`pro.trade_cal`),但那
    依赖网络 + `TUSHARE_TOKEN`,不适合本函数要求的离线确定性契约——pinned.json 的 TTL 只是
    粗粒度"别让僵尸条目永久吃 token"防呆,不是交易执行时点,近似(最多偏差几个节假日天数)
    可接受。
    """
    cur = d
    n_added = 0
    while n_added < n:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:            # Mon=0 .. Fri=4,跳周六(5)/周日(6)
            n_added += 1
    return cur


def load_pinned(today: str, path: str | Path | None = None,
                cap: int = 5, ttl_days: int = 10) -> dict:
    """读 `pinned.json`(保送票)→ `{"kept": [...], "expired": [...]}`。

    条目 `{code, note, added, expires}`:`code` 必填(归一成 6 位裸码,容忍 `.SH`/`.SS`
    后缀与未 zfill 的短码);`note` 缺省 `""`;`added`(pin 入日期)缺省 = `today`(新 pin,
    当天生效,尚无 TTL 参照);`expires` 缺省 = `added` + `ttl_days`(默认 10)"交易日"
    (近似算法见 `_add_trading_days_approx`)。

    `today` > `expires` → 该条目归 `expired`(供报告备注,不参与 L1/L2 强注/强留——过期
    的保送票就该被无视,不是"降级仍算数");`today` ≤ `expires` → 归 `kept`。

    **cap**(默认 5,先过滤过期项后再对 `kept` 生效,不是原始文件行数):超出 → 按文件
    原序截断到前 `cap` 条(用户在文件里写的顺序即隐含优先级,不做任何重排),溢出条目
    打印 `[pinned]` 警告到 stderr 后**丢弃**(cap 溢出 ≠ 过期,语义不同,不混进 `expired`,
    也不在返回值里另设第三个桶——只留一句诊断)。

    缺文件/空列表 → `{"kept": [], "expired": []}`(parity:无 pinned.json = 现行为不变)。
    条目缺 `code` → `ValueError`(防拼写错静默失效,呼应 `load_user_config` 的风格)。
    """
    p = Path(path) if path is not None else DEFAULT_PINNED_PATH
    if not p.exists():
        return {"kept": [], "expired": []}
    raw = _read_jsonc(p)
    if not raw:
        return {"kept": [], "expired": []}

    today_d = datetime.strptime(str(today)[:10], "%Y-%m-%d").date()
    kept: list[dict] = []
    expired: list[dict] = []
    for i, entry in enumerate(raw):
        if not entry.get("code"):
            raise ValueError(f"pinned.json 第 {i} 条缺 code 字段: {entry}")
        code = str(entry["code"]).split(".")[0].zfill(6)
        note = entry.get("note", "")
        added_s = entry.get("added") or today
        added_d = datetime.strptime(str(added_s)[:10], "%Y-%m-%d").date()
        if entry.get("expires"):
            expires_d = datetime.strptime(str(entry["expires"])[:10], "%Y-%m-%d").date()
        else:
            expires_d = _add_trading_days_approx(added_d, ttl_days)
        norm = {"code": code, "note": note, "added": added_d.isoformat(),
                "expires": expires_d.isoformat()}
        (expired if today_d > expires_d else kept).append(norm)

    if len(kept) > cap:
        dropped, kept = kept[cap:], kept[:cap]
        codes = ", ".join(d["code"] for d in dropped)
        print(f"[pinned] kept 超出 cap={cap},截断 {len(dropped)} 条(丢弃: {codes})",
              file=sys.stderr)

    return {"kept": kept, "expired": expired}


def main() -> int:
    """CLI:打印白名单校验后的 scan_config JSON 一行。

    给不经 `frame --json` 的编排场景(如 scan-retro 拉 t1-review workflow)喂 `args.cfg` 用
    ——workflow 脚本无文件系统访问,配置必须由编排会话读出随 args 传入(装载链同 scan-market)。
    配置文件写坏(白名单外键)→ 沿用 load_user_config 的 fail-fast raise,非零退出。
    """
    print(json.dumps(load_user_config(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
