"""改动时的静态成本清单 BOM(token 防膨胀 M1)。零 LLM。

回答「这次改动让一场全扫的钱变了多少」:取校准读数(本引擎最近几场量到且未断路的 redline;一场都没有
就用 ``contracts/token_bom_seeds.json`` 里的两份种子),把每份读数的每角色成本折算到**当前配置**,
取各份折算总额的中位数,与 ``budgets.declared`` 比。超线由 ``config_standard`` 的 R11 判违规
(PostToolUse hook exit 2)。绝对值以场后 redline 为准 —— 这里的数只用来比差,估算不入台账。

折算三件事:

- **线程数**:卡 / 复核 ∝ ``l4.max_cards + pinned.cap``;情报同上且情报关 = 0;行业 brief ∝
  ``sector.max_briefs``(+ 看多 top3);L3、策略师恒 1。
- **前导**:agent 文件字符变化 × 每字符 token;claude 自动记忆开关;codex 两个瘦身键。系数是实测值,
  住在种子文件里(来源写在它的 ``_note``)。
- **单价**:claude = 该角色模型首调的 5 分钟 cache 写 + 其余调用的 cache 读(``trace.pricing``);
  codex = 校准场窗口点数 / 校准场总输入(账号级窗口的粗折算,标「估」)。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from autoresearch.common import workspace as ws

SEEDS_PATH = Path(__file__).resolve().parents[1] / "contracts" / "token_bom_seeds.json"
#: 角色 → agent 文件名(两个引擎读的都是 ``.claude/agents/<name>.md``)。
ROLE_AGENT = {"macro.brief": "macro-brief", "sector.brief": "sector-brief", "scan.l3": "l3-rank",
              "scan.l3.repair": "l3-repair", "scan.l4.intel": "l4-intel", "scan.l4.card": "l4-card",
              "scan.l4.review": "l4-card"}
_CARD_DRIVEN = ("scan.l4.card", "scan.l4.review", "scan.l4.intel")


def seeds() -> dict:
    return json.loads(SEEDS_PATH.read_text(encoding="utf-8"))


def _readout_dir(engine: str) -> Path:
    return ws.reports_root().parent / f"reports_{engine}" / "_ops" / "redline"


def calibration(engine: str, *, window: int) -> list[dict]:
    """本引擎最近 ``window`` 份量到、未断路的 redline;一份都没有 → 种子。"""
    found = []
    folder = _readout_dir(engine)
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (value.get("engine") == engine and value.get("usage_status") == "MEASURED"
                    and value.get("verdict") in {"PASS", "WARN"}
                    and (engine != "codex" or (value.get("window") or {}).get("status") == "MEASURED")):
                found.append(value)
    found.sort(key=lambda value: str(value.get("run_id")))
    if found:
        return found[-int(window):]
    return [value for value in seeds()["readouts"] if value["engine"] == engine]


def _driver(role: str, config: dict) -> float:
    cards = float(config.get("max_cards", 0)) + float(config.get("pinned_cap", 0))
    if role == "scan.l4.intel":
        return cards if config.get("intel_enabled", True) else 0.0
    if role in _CARD_DRIVEN:
        return cards
    if role == "sector.brief":
        return float(config.get("sector_briefs", 0))
    return 1.0


def _prefix_tokens(engine: str, role: str, config: dict, chars: dict, coef: dict) -> float:
    """前导里会随配置 / agent 文件变的那部分 token(常量部分比差时相消)。"""
    tokens = coef["md_tokens_per_char"] * float(chars.get(ROLE_AGENT.get(role, ""), 0))
    context = config.get("preamble") or {}
    if engine == "claude":
        if context.get("claude_auto_memory", False):
            tokens += coef["claude_auto_memory_tokens"]
    else:
        budget = context.get("codex_skills_catalog_budget")
        full = coef["codex_skills_catalog_tokens"]
        # 实测只有两点:不设(全目录)与 1000(目录几乎清空);其间按预算线性、封顶全目录(估)。
        tokens += full if budget is None else (0.0 if int(budget) <= 1000 else min(full, float(budget)))
        if context.get("codex_project_doc_max_bytes") is None:
            tokens += coef["codex_project_doc_tokens"]
        elif int(context["codex_project_doc_max_bytes"]) > 0:
            tokens += coef["codex_project_doc_tokens"]          # 有上限也照读,按全文计(上界)
    return tokens


def _claude_price_per_thread(model: str | None, calls: float) -> float:
    """每多一个前导 token,一个线程多付的美元:首调 5 分钟 cache 写 + 其余调用 cache 读。"""
    from autoresearch.trace.pricing import price_for_model

    profile = price_for_model(model) or price_for_model("claude-opus-5-5")
    return (profile.cache_write_5m_per_mtok + max(0.0, calls - 1) * profile.cache_read_per_mtok) / 1e6


def fold(readout: dict, now: dict, chars_now: dict, coef: dict) -> dict:
    """一份校准读数 → 当前配置下的估算(``usd`` 或 ``window_points``)。"""
    engine = readout["engine"]
    cal = readout.get("config") or {}
    roles = readout.get("roles") or {}
    total_input = sum(float(slot.get("input") or 0) for slot in roles.values())
    points = float((readout.get("window") or {}).get("primary_points") or 0)
    per_token = points / total_input if engine == "codex" and total_input else 0.0
    out_roles, uncalibrated = {}, []
    for role, slot in roles.items():
        threads_cal = float(slot.get("threads") or 0)
        base = float(slot.get("usd") or 0) if engine == "claude" else per_token * float(slot.get("input") or 0)
        driver_cal, driver_now = _driver(role, cal), _driver(role, now)
        if driver_cal == 0:
            uncalibrated.append(role)
            continue
        threads_now = threads_cal * driver_now / driver_cal
        cost = base * driver_now / driver_cal
        delta = (_prefix_tokens(engine, role, now, chars_now, coef)
                 - _prefix_tokens(engine, role, cal, readout.get("agent_chars") or {}, coef))
        calls = float(slot.get("calls_median") or 1)
        if engine == "claude":
            model = ((readout.get("identity") or {}).get("models") or {}).get(role, [None])[0]
            cost += threads_now * delta * _claude_price_per_thread(model, calls)
        else:
            cost += threads_now * calls * delta * per_token
        out_roles[role] = {"threads": round(threads_now, 2), "cost": round(cost, 4),
                           "prefix_delta_tokens": round(delta)}
    return {"run_id": readout.get("run_id"), "seed": bool(readout.get("seed")),
            "total": round(sum(item["cost"] for item in out_roles.values()), 4),
            "roles": out_roles, "uncalibrated": uncalibrated}


def estimate(engine: str, cfg: dict | None = None, *, chars_now: dict | None = None) -> dict:
    """当前配置下一场 ``engine`` 全扫的估算与预算线。"""
    from autoresearch.scan import redline

    raw = redline._cfg(cfg)
    policy = redline.budgets_policy(raw)
    now = redline.config_snapshot(raw)
    chars_now = chars_now if chars_now is not None else redline.agent_chars()
    coef = seeds()["coefficients"]
    sources = calibration(engine, window=int(policy["relative"]["window"]))
    folded = [fold(readout, now, chars_now, coef) for readout in sources]
    total = float(statistics.median([item["total"] for item in folded])) if folded else None
    line = (policy["declared"]["claude_run_usd"] if engine == "claude"
            else policy["declared"]["codex_run_window_points"])
    return {"engine": engine, "unit": "usd" if engine == "claude" else "window_points",
            "total": None if total is None else round(total, 2), "line": line,
            "sources": [{"run_id": item["run_id"], "seed": item["seed"], "total": item["total"]} for item in folded],
            "roles": folded[-1]["roles"] if folded else {},
            "uncalibrated": sorted({role for item in folded for role in item["uncalibrated"]})}


def check(cfg: dict | None = None, *, chars_now: dict | None = None) -> list[tuple[str, str]]:
    """``[(where, message)]``:每个引擎的估算超过 ``budgets.declared`` 那条线就是一条。"""
    problems = []
    for engine, key in (("claude", "claude_run_usd"), ("codex", "codex_run_window_points")):
        bom = estimate(engine, cfg, chars_now=chars_now)
        if bom["total"] is not None and bom["total"] > bom["line"]:
            top = sorted(bom["roles"].items(), key=lambda kv: -kv[1]["cost"])[:3]
            detail = " · ".join(f"{role} {item['cost']:.1f}" for role, item in top)
            problems.append((f"budgets.declared.{key}",
                             f"静态成本清单 {bom['total']:.1f} > 线 {bom['line']:.1f}({bom['unit']};{detail});"
                             "砍掉增量,或由人上调这条线"))
    return problems


def summary_line(cfg: dict | None = None) -> str:
    parts = []
    for engine in ("claude", "codex"):
        bom = estimate(engine, cfg)
        unit = "$" if engine == "claude" else "点"
        seeded = "·种子" if bom["sources"] and all(item["seed"] for item in bom["sources"]) else ""
        parts.append(f"{engine} {bom['total']}{unit}/线 {bom['line']}{unit}{seeded}")
    return "token BOM(估):" + " · ".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.token_bom", description="当前配置下一场全扫的估算成本")
    ap.add_argument("--engine", choices=("claude", "codex"))
    args = ap.parse_args(argv)
    engines = [args.engine] if args.engine else ["claude", "codex"]
    print(json.dumps({engine: estimate(engine) for engine in engines}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
