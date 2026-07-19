#!/usr/bin/env python3
"""跨日聚合各 scan 日的 retro/channel_eval.csv → 每路滚动边际超额(单日是噪声,跨日才是信号)。零 LLM。

主尺 = T+2(2026-07-10 用户裁定:持仓超短 1~2 日,`fwd_2_oc` 为主尺,T+5 作废退位为参考列)
—— 排序与 quota 提议(`propose_quota_adjustments`)全部由 `unique_excess_t2` 驱动,t5 列仅保留
展示不再驱动任何决策(2026-07-17 起,此前提议仍按 t5 是裁定后的漏改)。
quota 提议基线 = registry 默认 ⊕ scan_config.jsonc 的 funnel.channel_quotas 覆盖
(pr_20260714_004:基线硬编码会反复重新提议已实施进配置的改动)。

用法:
  uv run --no-sync python -m autoresearch.learning.channel_ledger     # 滚动 → reports/learning/channel_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["channel", "n_days", "sum_unique",
         "mean_unique_excess_t2", "mean_excess_t2", "mean_hit_rate_t2",
         "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/retro/channel_eval.csv 跨日 → 每路滚动汇总(按边际超额降序)。"""
    scan_root = scan_root or Path("context/scan")
    frames = []
    for p in sorted(scan_root.glob("*/retro/channel_eval.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "channel" in df.columns and len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=_COLS)
    alld = pd.concat(frames, ignore_index=True)
    for c in ("unique_excess_t5", "mean_excess_t5", "hit_rate_t5",
              "unique_excess_t2", "mean_excess_t2", "hit_rate_t2", "n_unique"):
        alld[c] = pd.to_numeric(alld.get(c), errors="coerce")
    out = alld.groupby("channel").agg(
        n_days=("channel", "size"),
        sum_unique=("n_unique", "sum"),
        mean_unique_excess_t2=("unique_excess_t2", "mean"),
        mean_excess_t2=("mean_excess_t2", "mean"),
        mean_hit_rate_t2=("hit_rate_t2", "mean"),
        mean_unique_excess_t5=("unique_excess_t5", "mean"),
        mean_excess_t5=("mean_excess_t5", "mean"),
        mean_hit_rate_t5=("hit_rate_t5", "mean"),
    ).reset_index()
    for c in ("mean_unique_excess_t2", "mean_excess_t2", "mean_hit_rate_t2",
              "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"):
        out[c] = out[c].round(4)
    return out.sort_values("mean_unique_excess_t2", ascending=False, na_position="last").reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown 表(每路近 N 日边际超额 + 命中;n_days<3 标 ⚠样本少)。"""
    out = ["# 召回各路前向边际超额(跨日 ledger)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 channel_eval 数据(需先 retro 评估出 fwd)_"]
    out += ["| 路 | 天数 | Σunique | 边际超额T2 | membership超额T2 | 命中率T2 | "
            "边际超额T5(参考) | membership超额T5(参考) | 命中率T5(参考) |",
            "|---|---|---|---|---|---|---|---|---|"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"

    for r in ledger.itertuples(index=False):
        thin = " ⚠样本少" if (r.n_days or 0) < 3 else ""
        out.append(f"| {r.channel}{thin} | {int(r.n_days)} | {int(r.sum_unique)} | "
                   f"{f(r.mean_unique_excess_t2)} | {f(r.mean_excess_t2)} | {f(r.mean_hit_rate_t2)} | "
                   f"{f(r.mean_unique_excess_t5)} | {f(r.mean_excess_t5)} | {f(r.mean_hit_rate_t5)} |")
    return out


def current_quotas(config_path: str | Path | None = None) -> dict[str, int]:
    """各路当前 quota({name: quota})= recall registry 默认 ⊕ 用户配置覆盖。

    基线先取 registry 的 CHANNEL_DEFAULTS(导入 channels 模块以填充注册表),再叠
    `.claude/skills/scan-market/scan_config.jsonc` 的 funnel.channel_quotas(生产 L1 真正
    生效的覆盖,经 user_config loader,支持 // 注释)——pr_20260714_004:此前基线只读
    registry 硬编码,已实施进配置的 quota 改动会被反复重新提议。缺文件/缺键 → 纯 registry
    默认(parity=原硬编码值);配置里的未知路名忽略(registry 是路存在性的唯一事实源)。
    """
    import autoresearch.scan.recall.channels  # noqa: F401 —— 触发 @channel 注册
    from autoresearch.scan.recall.registry import CHANNEL_DEFAULTS
    from autoresearch.scan.user_config import load_user_config
    quotas = {name: int(spec.quota) for name, spec in CHANNEL_DEFAULTS.items()}
    cfg = load_user_config(config_path)                   # 缺文件 → {}(loader 自带 parity)
    overrides = (cfg.get("funnel") or {}).get("channel_quotas") or {}
    for name, q in overrides.items():
        if name in quotas:
            quotas[name] = int(q)
    return quotas


def propose_quota_adjustments(ledger: pd.DataFrame, quotas: dict[str, int], *, min_days: int = 3,
                              neg_thresh: float = 0.0, pos_thresh: float = 0.005,
                              step_frac: float = 0.25) -> list[dict]:
    """ledger(roll 输出)+ 当前 quotas → 调整提议(**advisory,不改线上**)。

    主尺 = `mean_unique_excess_t2`(2026-07-10 用户裁定持仓超短 1~2 日、fwd_2_oc 为主尺;
    t5 列仍在 ledger 展示但不再驱动提议)。持续负边际超额(< neg_thresh,n_days≥min_days)
    → 提议降 quota;持续正(> pos_thresh)→ 提议升;中性带 (neg, pos) / 样本不足 → 不提议。
    单步 ±step_frac。
    """
    props: list[dict] = []
    if ledger is None or not len(ledger):
        return props
    for r in ledger.itertuples(index=False):
        ch = r.channel
        if ch not in quotas or (r.n_days or 0) < min_days:
            continue
        m = r.mean_unique_excess_t2
        if m is None or pd.isna(m):
            continue
        cur = int(quotas[ch])
        if m < neg_thresh:
            proposed, direction = max(1, round(cur * (1 - step_frac))), "降"
        elif m > pos_thresh:
            proposed, direction = round(cur * (1 + step_frac)), "升"
        else:
            continue
        if proposed == cur:
            continue
        props.append({"channel": ch, "cur_quota": cur, "proposed_quota": int(proposed),
                      "delta": int(proposed) - cur,
                      "reason": f"{direction}:边际超额T2 {m * 100:+.1f}% / {int(r.n_days)}日"})
    return props


def main() -> int:
    led = roll()
    body = "\n".join(render(led))
    props = propose_quota_adjustments(led, current_quotas())
    if props:
        body += "\n\n## quota 调整提议(advisory,人工 gate)\n"
        body += "\n".join(f"- {p['channel']}: {p['cur_quota']} → {p['proposed_quota']}({p['reason']})"
                          for p in props)
    outp = Path("reports/learning/channel_ledger.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
