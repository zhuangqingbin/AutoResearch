#!/usr/bin/env python3
"""scan_config.json —— 用户配置层(白名单加载 + ScanConfig 映射)。全波地基(Plan A3 Task 1)。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.2。

用户在 `.claude/skills/scan-market/scan_config.jsonc` 里管控 scan-market 全程用到的 agent
model/effort、召回旋钮、L3.5 闸选择、保送参数、红队触发率、卡片复用参数——**白名单外的键一律
raise**(防拼写错静默失效,是本文件存在的唯一理由);缺文件 = 现行为(`{}`,一切默认关=parity)。

装载链(技术约束:workflow 脚本无文件系统访问):`frame --json`(Stage 0)读入本模块 → 回显进
market_pack/run meta(trace 记录本次跑用的配置=可复现)→ workflow 经 `args` 消费(Task 2)→
Python 侧 `apply_to_scan_config` 喂 `ScanConfig`(Task 3+ 的 L1/L2/L3.5/L4 各消费点)。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from autoresearch.scan.config import ScanConfig

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

# 顶层白名单;funnel/pinned/reuse/l4_intel/l3 额外校验子键(agents/l4_gate 内部形状由各自消费方
# 解释:agents={stage: {model, effort}} 无固定 stage 集,Task 2 workflow 直接按需取;l4_gate=
# {name, params},params 形状随 gate 实现而变,Task 3+ gate registry 各自校验)。
# l3:两遍法分诊(design 2026-07-12-l3-merge-plan.md Task 1)——two_pass/pass1_target 由
# `l3_select.prepare_l3_table` 消费;finalist_max 由 merge v3 消费(`write_finalists` 已接线,
# cap=min(finalist_max, budget))。
# learning:基率收缩估计(brainstorm 2026-07-12 §4 P0-3)——shrink/shrink_k 由
# `autoresearch.learning.shrink.shrink_config` 消费,四消费点(l4_card.write_base_rates/
# cross_calib.flip_stats/buy_ledger 的 target_calib/gate_ledger 的 tail_rate)各自读取。
# 默认 shrink=true·shrink_k=15(新基线);本块是回滚杆,不是 opt-in。
_TOP_WHITELIST = {"agents", "l4_gate", "funnel", "pinned", "redteam_prob", "reuse", "l4_intel", "l3",
                  "learning"}
_SUB_WHITELIST = {
    "funnel": {"recall_channels", "channel_quotas", "channel_floors"},
    "pinned": {"cap", "ttl_days"},
    "reuse": {"max_age_days", "price_delta_pct"},
    "l4_intel": {"enabled"},
    "l3": {"two_pass", "pass1_target", "finalist_max"},
    "learning": {"shrink", "shrink_k"},
}


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
    return cfg


def apply_to_scan_config(cfg: dict, sc: ScanConfig) -> ScanConfig:
    """把 `load_user_config()` 出的白名单 dict 映射进既有 `ScanConfig`(原地改,返回同一实例)。

    `funnel` 拆到既有字段(recall_channels/channel_quotas/channel_floors);其余键(agents/
    l4_gate/pinned/redteam_prob/reuse)整块挂同名新字段。cfg 中未出现的键保留 sc 原值不动
    (缺配置=parity,不用 None 覆盖已设值)。
    """
    funnel = cfg.get("funnel")
    if funnel:
        if "recall_channels" in funnel:
            sc.recall_channels = funnel["recall_channels"]
        if "channel_quotas" in funnel:
            sc.channel_quotas = funnel["channel_quotas"]
        if "channel_floors" in funnel:
            sc.channel_floors = funnel["channel_floors"]
    for key in ("agents", "l4_gate", "pinned", "redteam_prob", "reuse", "l4_intel", "l3", "learning"):
        if key in cfg:
            setattr(sc, key, cfg[key])
    return sc


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
