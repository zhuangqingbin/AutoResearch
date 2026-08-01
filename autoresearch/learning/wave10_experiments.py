#!/usr/bin/env python3
"""Wave10 预注册实验与监视器 —— 先注册 spec,再收 challenger 数据(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §C2

三条纪律都做成了**代码里的门**,不是文档里的叮嘱:

1. **先 register spec 再收数据**(§6 实验门)。definition 改了就必须换实验 id ——
   registry 的 `definition_hash` 已经强制这一点(改 spec 不改 id 会直接抛错)。
2. **family 冲突挡在注册处**。§C2.0:已在册的 `exp_20260729_l3_hard_constraint_f`
   (PREREGISTERED · l3_prompt)关闭/激活前,不得新开会改 L3 prompt 的 family。
   `assert_no_family_conflict` 把这句话变成一次会抛错的检查。
3. **monitor 不伪装成实验**(§C2.0 末句)。EXP-0/EXP-3 是**观测**,不产 RECOMMENDED、
   不可 activate,所以它们走独立的 `monitors.json` 版本化 schema,**不进 registry**。
   把 monitor 塞进 registry 的代价是:某天有人对它跑 promotion 判据,而它根本没有对照组。

晋升前零生产副作用(R4):两个 challenger 都是影子 —— EXP-1 只重算门的判据不改门,
EXP-2 的 channel floor=0、不占 quota、不写生产 finalists。

  uv run --no-sync python -m autoresearch.learning.wave10_experiments --register
  uv run --no-sync python -m autoresearch.learning.wave10_experiments --show
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autoresearch.learning.experiment_registry import (
    DEFAULT_REGISTRY,
    RegistryError,
    load_registry,
    register_experiment,
)

MONITORS_PATH = Path("context/learning/monitors.json")
MONITOR_SCHEMA_VERSION = 1

# §C2.0:这个 family 被占用期间不得新开同 family 实验
_L3_PROMPT_FAMILY = "l3_prompt"
_OPEN_STATUSES = {"PREREGISTERED", "RECOMMENDED", "APPROVED", "ACTIVE",
                  "STABLE_CANDIDATE"}


def _file_hash(path: Path | str) -> str:
    """challenger_pointer 的 content_hash —— 指向"这个实验读的是哪一版代码"。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exp1_spec(start: str = "2026-08-01", expires: str = "2026-11-30") -> dict:
    """EXP-1 `ow_gate_mainflow5d` —— 主力真在门的 5 日持续性影子口径。

    人口口径是 **participation**(主力门参与否决过的票),不是 attribution
    (只有主力门否决的票)。后者全仓只有 17 例 / 10 日,永远攒不到 50 的门槛;
    而 EXP-1 要考核的本来就是"这道门评过并否掉了谁"。见 gate_attribution 的两张表。
    """
    return {
        "id": "exp_20260801_ow_gate_mainflow5d",
        "title": "OW『主力真在』门:5 日持续性影子口径 vs 当日口径",
        "trial_family": "ow_gate_mainflow",
        "definition": {
            "challenger": ("sum(main_net_yi, T-4..T) > 0 ∧ positive_days >= 3 "
                           "∧ main_distortion == false"),
            "control": "当日口径(现行生产判据)",
            "T": "分析日;区间严格为**五个交易日**,非自然日",
            "data_source": ("moneyflow 日分区(lake);缺任一日 → UNMEASURED,"
                            "assemble **不得**联网补"),
            "population": ("gate_participation_v3.csv 中 gate=='主力真在' 的行 —— "
                           "即该门参与否决过的票;attribution 口径样本不足以支撑本实验"),
            "outcome_contract": "A11 v3(CORRECT/NEUTRAL/FALSE_KILL/UNMEASURED)",
            "pairing": "control/challenger 绑定同一 run_id/candidate/date,paired delta",
            "statistics": "date cluster bootstrap;固定 minimum,不允许可选停止",
            "production_side_effects": "无 —— 只重算判据,不改门、不改评级、不改 BUY 数",
            "negative_result_policy": "不通过即记负结果并关闭,不重跑到通过为止",
        },
        "start_date": start,
        "expires_date": expires,
        "primary_metric": "false_kill_rate_delta_pp",
        "promotion_guards": {
            # 主判据:错杀率至少降 3pp
            "research": [{"metric": "false_kill_rate_delta_pp", "op": "lte", "value": -3}],
            # 守卫:拦对率不得掉超过 3pp;被拦票超额均值不得变正
            "decision": [
                {"metric": "correct_block_rate_delta_pp", "op": "gte", "value": -3},
                {"metric": "mean_excess2_delta", "op": "lte", "value": 0},
            ],
            "token": [{"metric": "token_delta_pct", "op": "lte", "value": 0}],
            "speed": [{"metric": "wall_delta_pct", "op": "lte", "value": 5}],
            "architecture": [{"metric": "production_artifact_diff", "op": "eq", "value": 0}],
        },
        "rollback_guards": {
            "research": [{"metric": "false_kill_rate_delta_pp", "op": "gt", "value": 0}],
            "decision": [{"metric": "correct_block_rate_delta_pp", "op": "lt", "value": -5}],
            "token": [{"metric": "token_delta_pct", "op": "gt", "value": 5}],
            "speed": [{"metric": "wall_delta_pct", "op": "gt", "value": 10}],
            "architecture": [{"metric": "production_artifact_diff", "op": "gt", "value": 0}],
        },
        "challenger_pointer": {
            "kind": "shadow_gate",
            "pointer": "autoresearch/learning/gate_attribution.py::gate_participation",
            "content_hash": _file_hash("autoresearch/learning/gate_attribution.py"),
        },
        "minimums": {
            "forward_days": 20,
            "mature_events": 50,
            "unique_events": 50,
            "regimes": 2,
        },
        "rollback_window_runs": 5,
    }


def exp2_spec(start: str = "2026-08-01", expires: str = "2026-11-30") -> dict:
    """EXP-2 `recall_sector_momentum` —— 上涨侧板块动量影子召回路。

    §R4 继承裁定:**不碰防御/跌势侧轮动**;用户 07-17 裁定里被否的只有追防御/跌势侧,
    上涨板块侧未被否。floor=0 / 不占 quota / 不写生产 finalists = 零生产副作用。
    """
    return {
        "id": "exp_20260801_recall_sector_momentum",
        "title": "召回:上涨侧板块动量影子 channel(floor=0,不占 quota)",
        "trial_family": "recall_channel",
        "definition": {
            "challenger": "上涨侧 sector momentum channel,只落 per_channel 长表",
            "control": "现行召回 channel 组合",
            "isolation": ("floor=0 · 不占 quota · **不写生产 finalists** —— "
                          "Wave4 教训:『默认不启用』必须连副作用一起不启用,"
                          "加个 L2 floor 就天天改生产 LLM 输入"),
            "phase_requirement": "每相位(regime)≥5 个扫描日,不足则 IMMATURE",
            "statistics": "date cluster bootstrap 的 unique excess T+2 下界",
            "shared_instrument": ("与 pr_20260725_001 的 event 召回路共用 channel_audit "
                                 "仪器,但 family/variant 分开,**不得混样本**"),
            "negative_result_policy": "不通过即退役并把负结果写进 lessons",
        },
        "start_date": start,
        "expires_date": expires,
        "primary_metric": "unique_excess_t2_lcb",
        "promotion_guards": {
            "research": [{"metric": "unique_excess_t2_lcb", "op": "gt", "value": 0}],
            "decision": [{"metric": "left_tail_rate_delta_pp", "op": "lte", "value": 0}],
            "token": [{"metric": "token_delta_pct", "op": "lte", "value": 5}],
            "speed": [{"metric": "wall_delta_pct", "op": "lte", "value": 5}],
            "architecture": [{"metric": "production_artifact_diff", "op": "eq", "value": 0}],
        },
        "rollback_guards": {
            "research": [{"metric": "unique_excess_t2_lcb", "op": "lte", "value": 0}],
            "decision": [{"metric": "left_tail_rate_delta_pp", "op": "gt", "value": 2}],
            "token": [{"metric": "token_delta_pct", "op": "gt", "value": 10}],
            "speed": [{"metric": "wall_delta_pct", "op": "gt", "value": 10}],
            "architecture": [{"metric": "production_artifact_diff", "op": "gt", "value": 0}],
        },
        "challenger_pointer": {
            "kind": "shadow_channel",
            "pointer": "autoresearch/scan/recall/registry.py::sector_momentum(shadow)",
            "content_hash": _file_hash("autoresearch/scan/recall/registry.py"),
        },
        "minimums": {
            "forward_days": 20,
            "mature_events": 50,
            "unique_events": 50,
            "regimes": 2,
        },
        "rollback_window_runs": 5,
    }


# ────────────────────────── monitors(不进 registry) ──────────────────────────

def monitor_specs() -> list[dict]:
    """EXP-0 / EXP-3 —— **观测**,不产 RECOMMENDED、不可 activate、没有对照组。"""
    return [
        {
            "id": "mon_20260801_regime_flip",
            "schema_version": MONITOR_SCHEMA_VERSION,
            "title": "regime 首次从 risk_off 转 range/risk_on 的前后对称窗",
            "kind": "prospective_monitor",
            "records": ["candidate_n", "三门通过率", "shadow_excess", "BUY_n"],
            "window": "首次翻转后冻结前后对称窗口",
            "maturity": "每侧 ≥10 个 scan day 且每侧完整卡 ≥50;不足**自动延长**",
            "stopping_rule": "禁止选好看的第 5 日停 —— 固定成熟门",
            "verdict_policy": ("不产 RECOMMENDED。BUY>0 只作描述;结构问题升格需"
                               "『通过率无提升 **且** shadow 有 FALSE_KILL』共同成立"),
        },
        {
            "id": "mon_20260801_target_calib_postcheck",
            "schema_version": MONITOR_SCHEMA_VERSION,
            "title": "目标价 hi_2×regime p60 锚的接线后验收(既有功能,非新 challenger)",
            "kind": "postcheck_monitor",
            "records": ["calibration_error", "触达率", "中位目标幅"],
            "maturity": "mature_events ≥60 · forward_days ≥20 · regimes ≥2",
            "verdict_policy": ("只观察。calibration error 未改善 → 再另立 challenger;"
                               "**本波不重复实现现有功能**(p60 锚与超锚硬理由已接线)"),
        },
    ]


def write_monitors(path: Path | str = MONITORS_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": MONITOR_SCHEMA_VERSION, "monitors": monitor_specs()}
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(target)
    return target


# ────────────────────────── 注册 ──────────────────────────

def assert_no_family_conflict(spec: dict, registry_path: Path | str) -> None:
    """§C2.0:被占用的 family 不得新开实验 —— 把那句话变成一次会抛错的检查。"""
    family = spec.get("trial_family")
    payload = load_registry(registry_path)
    for exp_id, record in payload.get("experiments", {}).items():
        if exp_id == spec.get("id"):
            continue
        if (record.get("trial_family") == family
                and record.get("status") in _OPEN_STATUSES):
            raise RegistryError(
                f"family {family!r} 已被 {exp_id}(status={record.get('status')})占用 —— "
                "同 family 实验会互相污染样本,必须先处置它")
    if family == _L3_PROMPT_FAMILY:
        raise RegistryError(
            "l3_prompt family 由 exp_20260729_l3_hard_constraint_f 持有;"
            "它关闭/激活前不得新开会改变 L3 prompt 的实验(§C2.0)")


def register_all(registry_path: Path | str = DEFAULT_REGISTRY,
                 *, registered_at: str | None = None) -> list[dict]:
    """注册 EXP-1/EXP-2。幂等:definition 未变则原样返回既有记录。"""
    out = []
    for spec in (exp1_spec(), exp2_spec()):
        assert_no_family_conflict(spec, registry_path)
        out.append(register_experiment(registry_path, spec,
                                       registered_at=registered_at))
    return out


def freeze_specs(target: Path | str) -> Path:
    """把 spec + definition_hash 冻进 **git 管得着的地方**。

    registry 本体住在 gitignore 的 `context/` 下 —— 那意味着"我们在看到结果之前就承诺了
    这个判据"这件事**无法被第三方验证**。预注册的全部价值就在这个可验证性上,所以规格
    必须另有一份进版本控制的副本。`definition_hash` 与 registry 里的是同一个值:
    事后改 spec 会让两边对不上。
    """
    from autoresearch.learning.experiment_registry import canonical_hash

    specs = [exp1_spec(), exp2_spec()]
    payload = {
        "frozen_for": "Wave10 §C2 预注册",
        "note": ("registry 本体在 context/(gitignored);本文件是可审计副本。"
                 "definition_hash 与 registry 记录一致,事后改 spec 两边会对不上。"),
        "experiments": [{**s, "definition_hash": canonical_hash(s)} for s in specs],
        "monitors": monitor_specs(),
    }
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Wave10 实验预注册 + monitor schema")
    ap.add_argument("--registry", default=None)
    ap.add_argument("--register", action="store_true", help="注册 EXP-1/EXP-2 + 落 monitors")
    ap.add_argument("--show", action="store_true", help="只打印当前 registry 与 monitors")
    ap.add_argument("--freeze", default=None, help="把 spec + hash 冻结到该路径(进 git)")
    args = ap.parse_args(argv)
    path = Path(args.registry or DEFAULT_REGISTRY)

    if args.freeze:
        print(f"[freeze] {freeze_specs(args.freeze)}")
        return 0

    if args.register:
        records = register_all(path)
        target = write_monitors()
        for rec in records:
            print(f"[registry] {rec['id']} status={rec['status']} "
                  f"family={rec['trial_family']} hash={rec['definition_hash'][:12]}")
        print(f"[monitors] {len(monitor_specs())} 个 → {target}(不进 registry:"
              f"monitor 不产 RECOMMENDED、不可 activate)")
        return 0

    payload = load_registry(path)
    print(f"registry: {len(payload.get('experiments', {}))} 个实验")
    for exp_id, rec in sorted(payload.get("experiments", {}).items()):
        print(f"  {exp_id}  status={rec.get('status')}  family={rec.get('trial_family')}"
              f"  primary={rec.get('primary_metric')}  expires={rec.get('expires_date')}")
    if MONITORS_PATH.exists():
        mons = json.loads(MONITORS_PATH.read_text(encoding="utf-8"))["monitors"]
        print(f"monitors: {len(mons)} 个(独立 schema)")
        for mon in mons:
            print(f"  {mon['id']}  kind={mon['kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
