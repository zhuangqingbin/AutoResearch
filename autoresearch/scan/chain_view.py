#!/usr/bin/env python3
"""推荐链路视图 —— 一条命令拉出「这只票当天是怎么被推上来的」(确定性、零 LLM、零网络)。

design: docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md §4.3 R7

## 为什么要它

复盘一只推荐票效果不好,要回答的是**在哪一段出的问题**:召回线放它进来的理由是什么、
L2 凭什么进菜单、pass1 留没留、L3 写的兑现机制是什么、L4 读到的数字长什么样、E6 为什么
挑中它、事后到底涨没涨。这些事实散在十来个产物里(护照/CSV/JSON/卡片/决策文件),
人肉拼一次要开十个文件,而且**跨 run 的口径不一样**(旧 run 缺哪几件全靠记)。

本模块只做一件事:按顺序把这些片段接起来,**缺的明写「缺席」**。它不解释、不判断、
不打分 —— 那是人的活。

## 读哪里(优先级)

`trace/staging/`(2026-08-26 起随发布镜像)→ `trace/`(老 run 的白名单副本)→
`context_<engine>/scan/<date>/`(最后兜底,**并标注**「读的是共享 staging,同数据日重跑
会覆盖,可能已不是本 run 当时那份」——实测 64 个已发布 run 只剩 49 个 staging)。

  uv run --no-sync python -m autoresearch.scan.chain_view 20260825_2149 603317
  uv run --no-sync python -m autoresearch.scan.chain_view <run_dir> 603317 --out chain.md
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from autoresearch.common import workspace as ws

ABSENT = "_缺席_"


# ───────────────────────── 定位:run 目录 → 各段产物 ─────────────────────────

class Sources:
    """一次 run 的产物定位器。三级回退,并记录每次命中来自哪一级(渲染时如实标注)。"""

    def __init__(self, run_dir: Path):
        self.run = Path(run_dir)
        self.trace = self.run / "trace"
        self.mirror = self.trace / "staging"
        self.inputs = self.trace / "inputs"
        self.analysis_date = self._analysis_date()
        self.shared = (ws.scan_root() / self.analysis_date) if self.analysis_date else None
        self.used_shared = False

    def _analysis_date(self) -> str:
        for p in (self.run / "manifest.json",):
            if p.is_file():
                try:
                    return str(json.loads(p.read_text(encoding="utf-8")).get("analysis_date", ""))
                except (OSError, json.JSONDecodeError):
                    return ""
        return ""

    def find(self, *names: str) -> Path | None:
        """按 mirror → trace → shared 找第一个存在的文件(names 是同一件东西的别名)。"""
        for base in (self.mirror, self.trace):
            for name in names:
                p = base / name
                if p.is_file():
                    return p
        if self.shared is not None:
            for name in names:
                p = self.shared / name
                if p.is_file():
                    self.used_shared = True
                    return p
        return None

    def rows(self, *names: str) -> list[dict]:
        p = self.find(*names)
        if p is None:
            return []
        try:
            with p.open(encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))
        except OSError:
            return []

    def doc(self, *names: str) -> object | None:
        p = self.find(*names)
        if p is None:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def rel(self, path: Path | None) -> str:
        """路径按 run 目录相对化 —— 视图是给人读的,绝对路径会把一行撑到 200 字符。"""
        if path is None:
            return ABSENT
        try:
            return str(Path(path).relative_to(self.run))
        except ValueError:
            return str(path)

    def text(self, *names: str) -> str | None:
        p = self.find(*names)
        if p is None:
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None

    def find_input(self, code6: str, suffix: str) -> tuple[Path | None, str]:
        """researcher 输入文件(slim/deep)三处住址(2026-09-12 §8;任务 5 修的第一个缺陷):
        `trace/inputs/slim/`(retention 永久归档)→ `trace/staging/_external_inputs/`
        (发布镜像,in-flight 的第二处)→ 共享 `context_<engine>/scan/<date>/_external_inputs/`
        (老 run 兜底,**同数据日重跑会覆盖,不构成本 run 强归属**)。

        返回 `(path, tier)`;`tier` ∈ `"archived"`(第一处,视同本 run 事实)/
        `"mirror"`(第二处,本 run 发布镜像,视同本 run 事实)/
        `"shared_no_attribution"`(第三处,只能当参考,调用方不得把它计入事实/BUY 叙事/
        收益数字——spec §6.3 抢救件同一条纪律的镜像:mtime/同数据日不能证明归属)。
        找不到 → `(None, "absent")`。
        """
        inputs_slim = self.inputs / "slim"
        if inputs_slim.is_dir():
            hit = next(iter(sorted(inputs_slim.glob(f"*{code6}*{suffix}"))), None)
            if hit is not None:
                return hit, "archived"
        mirror_ext = self.mirror / "_external_inputs"
        if mirror_ext.is_dir():
            hit = next(iter(sorted(mirror_ext.glob(f"*{code6}*{suffix}"))), None)
            if hit is not None:
                return hit, "mirror"
        if self.shared is not None:
            shared_ext = self.shared / "_external_inputs"
            if shared_ext.is_dir():
                hit = next(iter(sorted(shared_ext.glob(f"*{code6}*{suffix}"))), None)
                if hit is not None:
                    self.used_shared = True
                    return hit, "shared_no_attribution"
        return None, "absent"


def _z6(value: object) -> str:
    return str(value or "").split(".")[0].strip().zfill(6)


def _row_for(rows: list[dict], code6: str) -> dict | None:
    for r in rows:
        if _z6(r.get("code") or r.get("ticker")) == code6:
            return r
    return None


def _round_floats(text: str) -> str:
    """把 `-10.100334448160531` 这种 CSV 往返出来的 17 位浮点压到 4 位 —— 视图是给人读的,
    多余的位数只会挤掉真正要看的字。**只动显示**,不动任何产物。"""
    import re

    def _sub(m: re.Match[str]) -> str:
        return f"{float(m.group(0)):.4f}".rstrip("0").rstrip(".")

    return re.sub(r"-?\d+\.\d{6,}", _sub, text)


def _fmt(value: object, cap: int = 200) -> str:
    if value is None or value == "":
        return "—"
    s = _round_floats(str(value).replace("\n", " ").strip())
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _kv(pairs: list[tuple[str, object]]) -> list[str]:
    return [f"- **{k}**:{_fmt(v)}" for k, v in pairs]


# ───────────────────── §4–§6:逐 invocation 证据合并(capsule ↔ ledger 补录) ─────────────────────
#
# 设计稿 2026-09-12 §6.2「按 invocation 合并」:capsule(`<run>/capsule/agents/index.json`)
# 是**当场**证据;ledger(`agents_index/<report_run_id>.json`,由尚未实现的
# `transcript_binder --offline` 生产,spec §9 产物表)是**事后补录**证据。合并必须逐条
# invocation 决定,永远不能因为 capsule 索引文件**存在**就整份短路返回、无视 ledger——
# 这正是本任务的 mutation probe (a) 要打中的那一条(V01:capsule 全 GONE、ledger 已补齐
# 却因为"文件存在"被吞掉)。
#
# ledger 索引形状是本任务**合成的契约**(controller ruling #2:spec §9 只给了产物表,
# 没给字段级 schema;Task 8 必须实现成这个样子,它的复核会对着这里核形状)。逐 invocation
# 行复用 capsule `agents/index.json` 行的**同一套字段名**(status/reason/normalized/
# snapshot_id/source_sha256/...)——ledger 的职责是"离线重建出同一张表",不是发明第二套
# 词表;顶层再加 spec §6.1 明确要求的重建身份(`report_run_id`/`contract_run_id`/`engine`/
# `run_manifest_sha256`)、`current_revision_id`、`parser_version`、`computed_at`。

def _read_json_or_none(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rows_by_invocation(doc: object) -> dict[str, dict]:
    if not isinstance(doc, dict):
        return {}
    rows = doc.get("invocations")
    if not isinstance(rows, list):
        return {}
    return {str(r["invocation_id"]): r for r in rows
            if isinstance(r, dict) and r.get("invocation_id")}


def _ledger_agents_index_dir(src: Sources) -> Path:
    """`agents_index/` 的根——沿用 `outcome.ledger_root`(不另写一处 `_ledger` 字面量,
    同 `ledger_views.views_root` 的既定纪律)。"""
    from autoresearch.scan.outcome import ledger_root

    return ledger_root(src.run.parent) / "agents_index"


def _merge_invocation(capsule_row: dict | None, ledger_row: dict | None, *,
                      capsule_normalizable: bool = True) -> dict:
    """一条 invocation 的合并结果——原始状态/补录状态/实际来源/冲突全部保留(spec §6.2)。

    `effective` 只在**恰好一边**有效、或两边一致时才置位;两边都 PRESENT 但内容(源摘要)
    不同 → `conflict=True`、`effective=None`,绝不悄悄挑一边(V02)。

    `capsule_normalizable=False`(fix round 1,finding 1;spec §6.2 第三个回落触发词)
    与"缺失"/"GONE"同等对待——不能因为 `status` 字面量是 `PRESENT` 就认为这一边真的
    可用,必须真正验证过 normalized 产物才算数。这种情形下 `original_status` 不塌缩成
    普通的 `"PRESENT"`(那会让读者误以为 capsule 真的服务了这条证据)也不塌缩成
    `"NO_CAPSULE_INDEX"`(那会抹掉"它确实自称 PRESENT 过"这个原始事实)——单独一个
    字面量 `"PRESENT_UNNORMALIZABLE"`,原始事实与"不能当真"两件事都留痕。"""
    cap_claims_present = isinstance(capsule_row, dict) and capsule_row.get("status") == "PRESENT"
    cap_present = cap_claims_present and capsule_normalizable
    led_present = isinstance(ledger_row, dict) and ledger_row.get("status") == "PRESENT"
    if cap_claims_present and not capsule_normalizable:
        original_status = "PRESENT_UNNORMALIZABLE"
    else:
        original_status = (capsule_row.get("status") if isinstance(capsule_row, dict)
                           else "NO_CAPSULE_INDEX")
    backfill_status = (ledger_row.get("status") if isinstance(ledger_row, dict)
                       else "NOT_BACKFILLED")
    effective: dict | None = None
    origin = "absent"
    conflict = False
    conflict_detail: str | None = None
    if cap_present and led_present:
        cap_digest = capsule_row.get("source_sha256")
        led_digest = ledger_row.get("source_sha256")
        if cap_digest and led_digest and cap_digest != led_digest:
            conflict = True
            conflict_detail = (f"capsule source_sha256={str(cap_digest)[:12]}… 与 ledger "
                               f"source_sha256={str(led_digest)[:12]}… 不同,两边都在场但"
                               f"内容冲突,不静默挑选")
        else:
            effective, origin = capsule_row, "capsule"
    elif cap_present:
        effective, origin = capsule_row, "capsule"
    elif led_present:
        effective, origin = ledger_row, "ledger_backfill"
    return {
        "capsule": capsule_row, "ledger": ledger_row,
        "original_status": original_status, "backfill_status": backfill_status,
        "effective": effective, "origin": origin,
        "conflict": conflict, "conflict_detail": conflict_detail,
    }


def _find_invocation(merged: dict[str, dict], role: str, code6: str) -> tuple[str, dict] | None:
    """按角色 + 代码在合并表里找那条 invocation——`subject` 可能是展示名(含码但不等于
    码,capsule `_agent_expectations` 的既有行为),所以用子串匹配,不要求恰好相等。"""
    for iid, m in sorted(merged.items()):
        row = m["capsule"] or m["ledger"]
        if not isinstance(row, dict) or str(row.get("role")) != role:
            continue
        subject = str(row.get("subject") or "")
        if subject == code6 or code6 in subject:
            return iid, m
    return None


class _EvidenceCache:
    """一次 `render()` 内共享的证据读取缓存(fix round 2,finding 1)。

    修前的病灶:`_sec_l4` 与 `_sec_scene` 各自独立重建一遍合并表(各读一次 capsule
    索引、一次 ledger 索引),而每条 PRESENT 的 capsule 行,判断"能不能归一化"
    (fix round 1 finding 1 加的第三触发词)与真正消费 normalized 产物又是两次独立的
    `_read_json_or_none` 调用——一条记录一次渲染里最多被读 4 次。这正是本计划要在
    transcript 层根除的病(spec §5.1「同一份不可变快照派生一切」,读多次会在同一份
    输出里描述两次不同的读)在视图层的重现:capsule 冻结后基本不会变,但 ledger 索引
    会被 Task 8 的 `--offline` 重建改写,渲染途中撞上重建是真实可能发生的时序。

    做法:`_read` 是所有磁盘读取的**唯一**入口,按已解析路径的字符串键缓存;
    `capsule_invocations`/`ledger_invocations`/`merged_invocations`/`normalized_doc`
    都通过它读,`render()` 建一个实例贯穿整次渲染,传给 `_sec_l4`/`_sec_scene`
    共用——不再各段各建一份。"""

    def __init__(self, src: Sources, report_run_id: str):
        self.src = src
        self.report_run_id = report_run_id
        self._json: dict[str, object | None] = {}
        self._merged: dict[str, dict] | None = None

    def _read(self, path: Path) -> object | None:
        key = str(path)
        if key not in self._json:
            self._json[key] = _read_json_or_none(path)
        return self._json[key]

    def capsule_invocations(self) -> dict[str, dict]:
        return _rows_by_invocation(self._read(self.src.run / "capsule" / "agents" / "index.json"))

    def ledger_invocations(self) -> dict[str, dict]:
        path = _ledger_agents_index_dir(self.src) / f"{self.report_run_id}.json"
        return _rows_by_invocation(self._read(path))

    def _capsule_row_normalizable(self, capsule_row: dict | None) -> bool:
        """spec §6.2 第三个回落触发词(fix round 1,finding 1)——capsule 行自称
        PRESENT,但它指向的 normalized 产物读不出来/解析不了,同「缺失」「GONE」一样
        不能当真。只在 `status == "PRESENT"` 时才需要真的去读盘核实。走缓存的 `_read`,
        与 `normalized_doc` 后续真正消费同一条记录时共用同一次读取结果。"""
        if not isinstance(capsule_row, dict) or capsule_row.get("status") != "PRESENT":
            return True
        rel = capsule_row.get("normalized")
        if not rel:
            return False
        doc = self._read(self.src.run / "capsule" / rel)
        return isinstance(doc, dict)

    def merged_invocations(self) -> dict[str, dict]:
        """capsule ∪ ledger 的 invocation_id 并集,逐条合并(从不因为文件存在就整份
        短路)。同一实例内重复调用只建一次,结果缓存。"""
        if self._merged is not None:
            return self._merged
        cap_rows = self.capsule_invocations()
        led_rows = self.ledger_invocations()
        self._merged = {
            iid: _merge_invocation(
                cap_rows.get(iid), led_rows.get(iid),
                capsule_normalizable=self._capsule_row_normalizable(cap_rows.get(iid)))
            for iid in sorted(set(cap_rows) | set(led_rows))
        }
        return self._merged

    def normalized_doc(self, m: dict) -> tuple[object | None, str]:
        """合并结果 → 该 invocation 的 normalized 文档,走缓存的 `_read`——与
        `_capsule_row_normalizable` 判断"能不能归一化"时读的是同一份缓存条目,
        不会因为先判断过一次就再独立读第二次。

        `effective is None`(冲突未消解/两边皆缺)时不读——没有单一可信来源可读;
        `normalized` 引用缺失或指向的文件读不出来(corrupted normalized 场景)都返回
        `(None, 原因)`,调用方据此渲染「证据不足」而不是空白或绝对断言。"""
        row = m.get("effective")
        if row is None:
            return None, "两边均缺席,或两边冲突未消解——没有单一可信来源"
        rel = row.get("normalized")
        if not rel:
            return None, "该 invocation 没有 normalized 产物引用"
        if m["origin"] == "capsule":
            path = self.src.run / "capsule" / rel
        else:
            path = _ledger_agents_index_dir(self.src) / rel
        doc = self._read(path)
        if doc is None:
            return None, f"normalized 产物缺失或损坏({rel})"
        return doc, ""


def _merged_invocations(src: Sources, report_run_id: str) -> dict[str, dict]:
    """便捷入口(供独立调用/测试直接用):建一个一次性 `_EvidenceCache`,单次调用内部
    每个文件仍然只读一次,只是不会跨多次调用共享。`render()` 改用跨段共享的
    `_EvidenceCache` 实例(见该类),不再调用这个函数——保留它是为了不破坏直接调用它
    的既有测试与任何未来的独立诊断脚本。"""
    return _EvidenceCache(src, report_run_id).merged_invocations()


def _operations(doc: object) -> list[dict]:
    if not isinstance(doc, dict):
        return []
    ops = doc.get("operations")
    return [op for op in ops if isinstance(op, dict)] if isinstance(ops, list) else []


def _ops_matching(ops: list[dict], filename: str) -> list[dict]:
    out = []
    for op in ops:
        if op.get("path_source") != "tool_input":
            continue
        path = op.get("path")
        if isinstance(path, str) and Path(path).name == filename:
            out.append(op)
    return out


#: observation.kind(spec §3.1,逐字复用 Task 1 的词表)→ 中文人读标签。只做展示,
#: 不改变 kind 本身的判定——分类权威在 `trace.transcripts.base.classify_observation`。
_KIND_LABELS: dict[str, str] = {
    "DISCOVERED": "发现(仅路径列出,未证明读到正文)",
    "READ_REQUESTED": "已请求读取(无可关联返回)",
    "READ_SUCCEEDED": "读取成功",
    "READ_PARTIAL": "部分读取(分页/grep/截断,不满足全文断言)",
    "READ_FAILED": "读取失败",
    "WRITE_REQUESTED": "已请求写入(无可关联返回)",
    "WRITE_SUCCEEDED": "写入成功",
    "WRITE_FAILED": "写入失败",
    "SEARCH_REQUESTED": "已请求搜索(无可关联返回)",
    "SEARCH_SUCCEEDED": "搜索成功",
    "SEARCH_FAILED": "搜索失败",
}
_READ_KINDS = frozenset({"READ_REQUESTED", "READ_SUCCEEDED", "READ_PARTIAL", "READ_FAILED"})
_WRITE_KINDS = frozenset({"WRITE_REQUESTED", "WRITE_SUCCEEDED", "WRITE_FAILED"})


def _read_status(ops_for_file: list[dict], *, insufficient: bool, reason: str) -> str:
    """spec §3.1 末段的展示纪律,逐字照办:

    - 区段缺失/交错/坏行/工具不支持 → 「证据不足,未观察到」(绝不是确定结论);
    - 只有覆盖确定完整且操作可识别、且确实没找到匹配的读操作时,才可以说
      「在该调用记录中未观察到成功读取」(唯一允许的强断言,且这句话本身也不是
      「没读」——它明说是"在该调用记录中");
    - 找到了操作 → 如实列出观察到的 kind(可能不止一种:先 REQUESTED 后 FAILED 等)。
    """
    if insufficient:
        return f"证据不足,未观察到(原因:{reason})"
    if not ops_for_file:
        return "在该调用记录中未观察到成功读取"
    kinds = sorted({op.get("kind") for op in ops_for_file if op.get("kind") in _READ_KINDS})
    return "、".join(_KIND_LABELS.get(k, str(k)) for k in kinds) or "在该调用记录中未观察到成功读取"


def _deep_expectation(card_kind: str | None, early_stop: dict | None) -> str:
    """deep(P4)是否被期望到——只回答"这条路径要不要 deep",不判"做没做对"、不带评级/
    处罚(controller ruling #3)。早停发生在 P4 之前 → 不要求;`card_kind` 明确
    full/earlystop 时按卡种;两者都判不出来 → 未知。"""
    phase = str((early_stop or {}).get("phase") or "")
    if phase in {"P1", "P2", "P3"}:
        return f"该路径不要求 deep(早停于 {phase},未到 P4)"
    if card_kind == "earlystop":
        return "该路径不要求 deep(卡种 earlystop)"
    if card_kind == "full":
        return "该路径要求 deep(卡种 full)"
    return "未知(卡种未解析/早停记录缺失,无法判断——不额外给评级或处罚)"


_EDIT_TOOL_NAMES = frozenset({"edit", "edit_file", "apply_patch", "notebookedit", "Edit",
                              "NotebookEdit"})


def _hash_compare(ops_for_file: list[dict], on_disk_path: Path | None) -> tuple[str, str]:
    """写入 hash 与当前发布版本核验(spec §3.2 + 本任务 bullet 5)——**同产物核验**
    (调用方保证只传同一产物自己的操作,intel 对 intel、卡对卡,不跨产物比较)。

    返回 `(state, detail)`;state ∈:
    - `UNKNOWN`           未观察到写入操作;
    - `NO_FULL_POSTIMAGE` 全部写入都只有 diff(Edit/apply_patch),没有任何一次留下过
      完整后镜像 hash 可核对;
    - `SUBSEQUENT_EDIT`   有完整后镜像可核对,但它**不是最后一次写入**——之后还发生过
      编辑(Edit/apply_patch 从不产出完整后镜像,spec §3.2),所以这次核对只能锚定在
      "最后一次留下完整后镜像"的那次写入,不是"最后一次写入";
    - `MATCH`/`DIFFERS`   与当前发布版本字节核对的结果(在没有后续编辑时才是"最终态"
      本身的核对结果)。

    真实写入序列里,Write 后接 Edit 是**常态**(先落一版骨架,再改几处)——Edit 本身
    永远没有 artifact,如果只看"最后一条写入有没有 artifact"来判断,SUBSEQUENT_EDIT
    永远走不到(每次都会先落到 NO_FULL_POSTIMAGE),所以要在写入序列里**倒着找最后一个
    带完整后镜像的写入**,而不是只看序列的最后一条。
    """
    writes = [op for op in ops_for_file if op.get("kind") in _WRITE_KINDS]
    if not writes:
        return "UNKNOWN", "未观察到对应写入操作"
    anchor_idx = None
    for i in range(len(writes) - 1, -1, -1):
        art = writes[i].get("artifact")
        if isinstance(art, dict) and art.get("sha256"):
            anchor_idx = i
            break
    if anchor_idx is None:
        return "NO_FULL_POSTIMAGE", "只有 diff/无完整后镜像,无法核对最终字节"
    has_later_edit = anchor_idx < len(writes) - 1
    artifact = writes[anchor_idx]["artifact"]
    if on_disk_path is None or not on_disk_path.is_file():
        return "UNKNOWN", "当前发布版本文件缺失,无法核对"
    try:
        on_disk_sha = hashlib.sha256(on_disk_path.read_bytes()).hexdigest()
    except OSError:
        return "UNKNOWN", "当前发布版本文件读取失败,无法核对"
    matched = on_disk_sha == artifact["sha256"]
    if has_later_edit:
        return ("SUBSEQUENT_EDIT",
               "写入后又发生编辑,以最后一次完整后镜像为核对锚点,"
               + ("当前字节与该锚点一致" if matched else "当前字节与该锚点不同"))
    return (("MATCH", "与当前发布版本字节一致") if matched else
           ("DIFFERS", "与当前发布版本不一致(内容已变化,或经历过脱敏)"))


def _hash_compare_note(ops_for_file: list[dict], on_disk_path: Path | None) -> str:
    """`_hash_compare` 的 `(state, detail)` → 一段渲染文本(fix round 1,finding 3):
    `detail` 此前算了就扔,两处调用点都只取 `[0]`——要么接进渲染,要么删掉,不留一个
    算出来却没人读的值。选择接进渲染:不占新行(只是把已有的"写入核验:"这一行变长),
    不影响 80 行摘要预算,`state` 仍是可 grep 的枚举前缀,`detail` 补足人读原因。"""
    state, detail = _hash_compare(ops_for_file, on_disk_path)
    return f"{state}({detail})"


# ───────────────────────── 各段渲染 ─────────────────────────

def _sec_identity(src: Sources, code6: str) -> list[str]:
    out = ["## ① run 身份"]
    man: dict = {}
    mp = src.run / "manifest.json"
    if mp.is_file():
        try:
            man = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            man = {}
    contract = src.doc("run_contract.json") or {}
    if not contract:
        out.append(f"- run_contract:{ABSENT}")
    else:
        dirty = contract.get("git_dirty")
        dirty_txt = ("未记录(v1 契约)" if "git_dirty" not in contract
                     else ("**脏树**:" + ", ".join(contract.get("dirty_paths") or [])[:160]
                           if dirty else "干净"))
        out += _kv([
            ("run_id", contract.get("run_id") or man.get("run_id")),
            ("数据日", contract.get("analysis_date") or man.get("analysis_date")),
            ("git_sha", contract.get("git_sha")),
            ("工作树", dirty_txt),
            ("prompt 指纹", f"{len(contract.get('prompt_hashes') or {})} 份"
                            + ("(v1 契约未记)" if "prompt_hashes" not in contract else "")),
            ("contract_hash", contract.get("contract_hash")),
        ])
    # 六个事实分开报。旧版把「MANIFEST 里列到的文件没被改」渲染成「现场完整性 ✓」——
    # 而 MANIFEST 永远列不到没人写下的文件,于是 557 个未归档 staging、0 份 transcript、
    # $0.0000 的假成本全都躲在那个 ✓ 后面(设计稿 §2 立案证据)。
    from autoresearch.scan.evidence import evidence_facts, render_evidence_lines

    out += render_evidence_lines(evidence_facts(src.run))
    # 时间锚(§2.4 G1):数据日回答「研究的是哪天」,这四行回答「什么时候才能真的下单」。
    # 缺了它,一份 T+1 收盘之后才写完的报告看上去与当天 20:00 就出的那份一模一样。
    from autoresearch.scan.exec_anchor import read_execution

    anchor = read_execution(src.run)
    if anchor.get("analysis_date"):
        quality = anchor.get("ready_quality") or "?"
        out += _kv([
            ("批准时刻", f"{anchor.get('decision_approved_at') or ABSENT}"
                         f"({anchor.get('ready_source') or '?'}·{quality})"),
            ("第一个可执行尾盘", anchor.get("first_available_session") or ABSENT),
            ("迟到 session", anchor.get("exec_lag")),
            ("可执行状态", anchor.get("actionability_status")),
        ])
    # R01(controller 验收矩阵):两类 sentinel(SENTINEL_EMPTY/SENTINEL_PINNED)与
    # FORCED_FULL 是合法业务结果,不是异常——只在文件存在时现出一行,不为它单独伪造
    # 缺席文案(旧 run 没有这份产物是正常的,不是"这段该有却没有")。
    mode_doc = src.doc("run_mode.json")
    if isinstance(mode_doc, dict) and mode_doc.get("mode"):
        out.append(f"- **run_mode**:{mode_doc.get('mode')}")
    return out


def _sec_passport(src: Sources, code6: str) -> list[str]:
    doc = src.doc("_candidate_passport.json")
    out = ["", "## ② 候选护照(全漏斗轨迹)"]
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(`_candidate_passport.json` 未随本 run 留存)"]
    entries = doc.get("candidates") or doc.get("passports") or doc
    row = None
    if isinstance(entries, dict):
        row = entries.get(code6)
    elif isinstance(entries, list):
        row = next((e for e in entries if _z6(e.get("code")) == code6), None)
    if row is None:
        return out + ["- 护照里没有这只票(未进 L1 打分集,或护照当日未生成)"]
    # 紧凑单行(2026-09-12 Task 5:80 行摘要预算收紧后省行——不改内容,只改排版;
    # 之前 `indent=1` 的多行缩进版没有任何测试依赖其换行形态)。
    out.append(f"- {json.dumps(row, ensure_ascii=False, sort_keys=True)}")
    return out


def _sec_l1(src: Sources, code6: str) -> list[str]:
    out = ["", "## ③ L1 召回"]
    row = _row_for(src.rows("L1_scored_full.csv"), code6)
    if row is None:
        return out + [f"- L1 打分行 {ABSENT}(该票未过 L0 硬门,或产物未留存)"]
    # provenance 三列(recall_channels/n_channels/best_rank)只在**召回集**表里,
    # `L1_scored_full.csv` 是全量打分表、没有这三列 —— 未入召回的票天然没有"命中通道"。
    recall = _row_for(src.rows("L1_recall_top1000.csv"), code6) or {}
    chan = (f"{recall.get('recall_channels')}(n={recall.get('n_channels')}, "
            f"best_rank={recall.get('best_rank')})" if recall else "未进 top1000 召回集")
    out += _kv([
        ("名称/行业", f"{row.get('name')} / {row.get('industry')}"),
        ("composite", row.get("composite")),
        ("命中通道", chan),
        ("当日/60日涨幅", f"{row.get('pct_1d')}% / {row.get('pct_60d')}%"),
        ("距60日高", row.get("dist_high_60")),
        ("主力净占比 / cmf20 / obv20", f"{row.get('main_net_ratio')} / {row.get('cmf_20')} / {row.get('obv_mom_20')}"),
        ("pe / pb / np_yoy / roe", f"{row.get('pe')} / {row.get('pb')} / {row.get('np_yoy')} / {row.get('roe')}"),
    ])
    ch = [r for r in src.rows("L1_channels.csv") if _z6(r.get("code")) == code6]
    out.append("- **逐路名次**:" + (
        "、".join(f"{r.get('channel')} #{r.get('channel_rank')}({r.get('channel_score')})"
                  for r in sorted(ch, key=lambda r: str(r.get("channel"))))
        if ch else f"{ABSENT}(`L1_channels.csv` 未留存 —— 逐路名次的唯一来源)"))
    return out


def _sec_l2(src: Sources, code6: str) -> list[str]:
    out = ["", "## ④ L2 菜单"]
    row = _row_for(src.rows("L2_gbdt_top200.csv"), code6)
    if row is None:
        return out + ["- 未进 L2 菜单(被召回线或分层采样挡在外面)"]
    out += _kv([
        ("l2_rank / gbdt_score", f"{row.get('l2_rank')} / {row.get('gbdt_score')}"),
        ("进菜单的理由", f"{row.get('selection_reason')} · {row.get('selection_detail') or '—'}"),
        ("配额救回", row.get("l2_lane_reserved")),
    ])
    return out


def _sec_pass1(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑤ L3 pass1 分诊"]
    kept = _row_for(src.rows("_l3_pass1_kept.csv"), code6)
    cut = _row_for(src.rows("_l3_pass1_cut.csv"), code6)
    if kept is not None:
        out.append(f"- **留下**(`{kept.get('selection_reason')}` · {kept.get('selection_detail') or '—'})")
    elif cut is not None:
        out.append("- **被切**(落 `_l3_pass1_cut.csv` 影子;不代表判死)")
    else:
        out.append(f"- {ABSENT}(pass1 产物未留存)")
    meta = src.doc("_l3_pass1_meta.json")
    if isinstance(meta, dict):
        out.append(f"- 当日分诊:{meta.get('n_in')} → {meta.get('n_kept')}"
                   f"(target {meta.get('target')};强制补入 {meta.get('forced_in')})")
    return out


def _sec_l3(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑥ L3 精排判断"]
    judged = _row_for(src.rows("L3_judged_full.csv"), code6)
    if judged is None:
        out.append(f"- judged 行 {ABSENT}(l3-rank 未判它 / 产物未留存)")
    else:
        out += _kv([
            ("conviction / lane / 倾向", f"{judged.get('conviction')} / {judged.get('lane')} / {judged.get('triage_lean')}"),
            ("finalist", judged.get("finalist")),
            ("thesis", _fmt(judged.get("thesis"), 400)),
            ("mechanism(两日内兑现)", _fmt(judged.get("mechanism"), 300)),
            ("risk", _fmt(judged.get("risk"), 300)),
            ("catalyst", _fmt(judged.get("catalyst"), 200)),
            ("fragility", _fmt(judged.get("fragility"), 200)),
        ])
    fin = _row_for(src.rows("L3_fine_finalists.csv", "finalists.csv"), code6)
    if fin is None:
        out.append("- **未入 finalist**")
    else:
        out.append(f"- **入 finalist** · guard=`{fin.get('guard') or '—'}`"
                   f" · lane=`{fin.get('lane')}`"
                   + (f" · 📌{fin.get('pinned_note')}" if fin.get("pinned_note") else ""))
    return out


#: 输入/产物住址 tier → 人读标签。`shared_no_attribution` 单独标 ⚠️——它是 spec §6.3
#: 抢救件同一条纪律的镜像:只作参考,不构成本 run 强归属(controller ruling #5)。
_TIER_LABEL: dict[str, str] = {
    "archived": "已归档", "mirror": "镜像",
    "shared_no_attribution": "⚠️仅共享·无强归属·仅供参考", "absent": "",
}


def _sec_l4(src: Sources, code6: str, cache: _EvidenceCache) -> list[str]:
    out = ["", "## ⑦ L4 研究"]
    prompt = src.find(f"reasoning/l4/_l4_prompt_{code6}.md", f"_l4_prompt_{code6}.md")
    intel = src.find(f"reasoning/l4/_l4_intel_{code6}.md", f"_l4_intel_{code6}.md")
    slim, slim_tier = src.find_input(code6, "_slim.md")
    deep, deep_tier = src.find_input(code6, "_slim_deep.md")

    # 逐 invocation 合并证据(spec §6.2)——l4-card 管 slim/deep/卡的读写,l4-intel 管
    # 情报文件自己的写入;两者各自的 normalized 只读一次,不混用(读坏一个不牵连另一个)。
    # `cache` 由 `render()` 建一份、贯穿整次渲染传进来(fix round 2,finding 1)——不在
    # 这里再建一份新的,`_sec_scene` 共用同一个实例,一条 PRESENT 记录整次渲染只读一次。
    merged = cache.merged_invocations()
    card_found = _find_invocation(merged, "l4-card", code6)
    intel_found = _find_invocation(merged, "l4-intel", code6)

    def _ops_for(found: tuple[str, dict] | None) -> tuple[list[dict], str]:
        if found is None:
            return [], "无可关联 invocation"
        _, m = found
        doc, reason = cache.normalized_doc(m)
        if doc is None:
            return [], (reason or "证据不可读")
        return _operations(doc), ""

    card_ops, card_ops_reason = _ops_for(card_found)
    intel_ops, intel_ops_reason = _ops_for(intel_found)

    def _input_line(label: str, path: Path | None, tier: str, *, ops: list[dict],
                    ops_reason: str, extra: str = "") -> str:
        if path is None:
            return f"- {label}:{ABSENT}(未留存)"
        line = f"- {label}:{src.rel(path)} · {path.stat().st_size}B"
        tier_note = _TIER_LABEL.get(tier, tier)
        if tier_note:
            line += f" · {tier_note}"
        if tier == "shared_no_attribution":
            return line + " · 读取:不计入证据(参考资料)" + extra
        matched = _ops_matching(ops, path.name)
        status = _read_status(matched, insufficient=bool(ops_reason), reason=_fmt(ops_reason, 50))
        return line + f" · 读取:{status}" + extra

    card_kind, early_stop_entry = _card_kind_and_early_stop(src, code6)
    deep_note = f" · {_deep_expectation(card_kind, early_stop_entry)}"

    out += [
        f"- 派发 prompt:{src.rel(prompt)}",
        _input_line("slim(P1–P3 表面块)", slim, slim_tier, ops=card_ops, ops_reason=card_ops_reason),
        _input_line("deep(P4 深核)", deep, deep_tier, ops=card_ops, ops_reason=card_ops_reason,
                    extra=deep_note),
        f"- 活体情报:{src.rel(intel)}"
        + (f" · 写入核验:{_hash_compare_note(_ops_matching(intel_ops, intel.name), intel)}"
           if intel is not None else ""),
    ]
    tasks = src.doc("reasoning/l4/_l4_tasks.json", "_l4_tasks.json")
    if isinstance(tasks, dict):
        entry = (tasks.get("tasks") or {}).get(code6) if isinstance(tasks.get("tasks"), dict) else None
        if entry:
            arts = entry.get("artifacts") or {}
            slim_art = arts.get("slim") or {}
            out.append(f"- 任务簿:status={entry.get('status')} · attempt={entry.get('attempt')}"
                       + (f" · slim_hash={str(slim_art.get('content_hash'))[:12]}" if arts else ""))
            rec_path = slim_art.get("path")
            if rec_path:
                from autoresearch.scan.retention import resolve_recorded_path
                real = resolve_recorded_path(rec_path)
                note = ("原路径已解析不到(2026-08-11 引擎隔离前记的裸 `context/`)"
                        if real is None else
                        ("" if str(real) == str(rec_path) else f" → 现址 `{real}`"))
                out.append(f"- 任务簿记的 slim 路径:`{rec_path}`{note}")
    stop = src.doc("_early_stop.json")
    if isinstance(stop, dict) and code6 in stop:
        s = stop[code6]
        out.append(f"- **早停**:停于 {s.get('phase')} · 停因「{s.get('reason')}」")
    ratings = src.doc("_final_ratings.json")
    if isinstance(ratings, dict) and code6 in ratings:
        out.append(f"- **终评级**:{ratings[code6]}")
    recs = src.doc("decision_records.json")
    rec = None
    if isinstance(recs, dict):
        # 真实形状:`{"records": [ {...}, ... ]}`(列表,不是按 code 的字典)
        items = recs.get("records")
        if isinstance(items, list):
            rec = next((r for r in items if _z6(r.get("code")) == code6), None)
        elif isinstance(items, dict):
            rec = items.get(code6)
    if isinstance(rec, dict):
        gates = rec.get("gate_states") or {}
        gate_txt = "、".join(f"{k}={v}" for k, v in sorted(gates.items())) or "—"
        out.append(f"- 决策记录:source={rec.get('source_rating')} · rubric={rec.get('rubric_rating')}"
                   f" · final={rec.get('final_rating')} · proposal={rec.get('proposal')}")
        out.append(f"- OW 三门:{gate_txt}(UNKNOWN = 早停卡不写三门段)")
        out.append(f"- 记录理由:{_fmt(rec.get('reason'), 160)}")
    ens = src.doc(f"_ensemble_{code6}.json")
    if isinstance(ens, dict):
        out.append(f"- 双复核:{_fmt(json.dumps(ens, ensure_ascii=False), 300)}")
    card = None
    for cand in sorted((src.run / "details").glob("*.md")) if (src.run / "details").is_dir() else []:
        txt = cand.read_text(encoding="utf-8", errors="ignore")[:400]
        if code6 in txt:
            card = cand
            break
    card_hash_note = ""
    if card is not None:
        card_hash_note = f" · 写入核验:{_hash_compare_note(_ops_matching(card_ops, card.name), card)}"
    out.append(f"- 发布卡:{src.rel(card)}{card_hash_note}")
    return out


def _card_kind_and_early_stop(src: Sources, code6: str) -> tuple[str | None, dict | None]:
    """`deep` 期望判断要用的两件事——schema 2 才有 `card_context.card_kind`;schema 1/
    解析失败/决策文件缺席一律 `None`(未知,不额外猜)。"""
    doc = src.doc("_relative_buy_decision.json")
    card_kind = None
    if isinstance(doc, dict) and doc.get("schema_version") == 2:
        row = next((c for c in (doc.get("candidates") or []) if _z6(c.get("code")) == code6), None)
        if isinstance(row, dict):
            ctx = row.get("card_context")
            if isinstance(ctx, dict):
                card_kind = ctx.get("card_kind")
    stop = src.doc("_early_stop.json")
    entry = stop.get(code6) if isinstance(stop, dict) else None
    return card_kind, (entry if isinstance(entry, dict) else None)


def _sec_e6_card_context(row: dict) -> list[str]:
    """schema 2 的 `card_context`(spec §7.1)——三种降级态(无卡/卡解析失败/健康卡)
    必须互不相同(controller ruling #1):`source.relative_path` 有没有值区分"根本没找到
    卡"与"找到了但解析出问题",`parse_status`/`entry_stance` 再各自现出细节,不靠
    `conflicts` 里的自然语言描述当唯一辨识信号。schema 1(row 没有这个键)返回 `[]`,
    这本身就是第四种、与前三种都不同的渲染(整行都不出现)。"""
    ctx = row.get("card_context")
    if not isinstance(ctx, dict):
        return []
    source = ctx.get("source") or {}
    path = source.get("relative_path")
    return [f"- card_context:card_kind={ctx.get('card_kind')} · entry_stance={ctx.get('entry_stance')}"
           f" · parse_status={ctx.get('parse_status')} · source={path or ABSENT}"
           f"({source.get('snapshot_quality') or ABSENT})"]


def _sec_e6_selection(doc: dict) -> list[str]:
    """schema 2 的实际选择依据(spec §7.2)——`why` 按决策文件算出的样子原样展示,
    这里**不重算、不改写**(controller ruling #1 的硬约束)。"""
    out: list[str] = []
    sel = doc.get("selection")
    if isinstance(sel, dict):
        winner = sel.get("winner") or {}
        pop = sel.get("population") or {}
        out.append(f"- selection:pool={sel.get('pool')} · candidates={pop.get('candidates')}"
                   f"→passed={pop.get('passed_hard_gates')}→after_pinned="
                   f"{pop.get('after_pinned_exclusion')}→final_pool={pop.get('final_pool')}"
                   f" · winner={winner.get('code') or '—'}(池内第{winner.get('pool_rank')}名,"
                   f"观察 rank 见候选本行)")
    veto = doc.get("veto_accounting")
    if isinstance(veto, dict):
        out.append(f"- veto_accounting:vetoed_stocks={veto.get('vetoed_stocks')}"
                   f" · by_gate={json.dumps(veto.get('by_gate') or {}, ensure_ascii=False, sort_keys=True)}")
    conflicts = doc.get("conflicts") or []
    if conflicts:
        types = "、".join(sorted({str(c.get("type")) for c in conflicts if isinstance(c, dict)}))
        out.append(f"- ⚠️ conflicts({len(conflicts)}):{types}(展示性,不改选择——why 已把它算进解释)")
    why = doc.get("why")
    if why:
        out.append(f"- why:{_fmt(why, 400)}")
    return out


def _sec_e6(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑧ E6 相对 BUY 决策"]
    doc = src.doc("_relative_buy_decision.json")
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(决策文件未留存 —— 2026-08-26 前它不在 run 目录里)"]
    schema2 = doc.get("schema_version") == 2
    buys = [b.get("code") for b in (doc.get("buys") or [])]
    row = next((c for c in (doc.get("candidates") or []) if _z6(c.get("code")) == code6), None)
    out.append(f"- 当日 mode={doc.get('mode')} · rule={doc.get('rule_version')} · "
               f"blocked={doc.get('blocked')} · BUY={buys or '—'}"
               + (f" · schema={doc.get('schema_version')}" if schema2 else ""))
    if row is None:
        out.append("- 本票不在 E6 候选(当日未派 L4 / 不在候选池)")
        if schema2:
            out += _sec_e6_selection(doc)
        return out
    out += _kv([
        ("eligible / rank", f"{row.get('eligible')} / {row.get('rank')}"),
        ("四面", json.dumps(row.get("faces") or {}, ensure_ascii=False, sort_keys=True)),
        ("硬门", json.dumps(row.get("hard_gate") or {}, ensure_ascii=False, sort_keys=True)),
        ("决策分", row.get("relative_decision_score")),
        ("卡面评级", row.get("research_rating")),
    ])
    excl = [e for e in (doc.get("excluded") or []) if _z6(e.get("code")) == code6]
    for e in excl:
        out.append(f"- 被排除:`{e.get('reason')}` · {e.get('detail')}")
    if code6 in [_z6(b) for b in buys]:
        out.append("- ✅ **本票就是当日 BUY**")
    if schema2:
        out += _sec_e6_card_context(row)
        out += _sec_e6_selection(doc)
    return out


def _sec_brief(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑨ 报告口径(brief)"]
    p = src.run / "brief.md"
    if not p.is_file():
        return out + [f"- {ABSENT}"]
    hits = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if code6 in ln or "relative BUY" in ln]
    return out + ([f"- {ln}" for ln in hits] if hits else ["- brief 未点名本票"])


def _sec_outcome(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑩ 结果(事后)"]
    # 结果落 `_ledger/outcome/<run_id>.json`(**不在 run 目录内**)—— run 目录有「发布后
    # 不再变」的 MANIFEST 不变量,事后往里写会让每个 run 的 `verify` 永远报一条 `extra`。
    from autoresearch.scan.outcome import (
        MATURE,
        OUTCOME_SCHEMA_VERSION,
        TRADE_CAL_QUALITY,
        outcome_path,
    )
    doc: object | None = None
    p = outcome_path(src.run.name, src.run.parent)
    if p.is_file():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(结果账本尚未回填 —— "
                      f"`python -m autoresearch.scan.outcome fill`)"]
    status = str(doc.get("outcome_status") or "")
    cal_quality = str(doc.get("calendar_quality") or "")
    out.append(f"- 口径:主尺 {doc.get('ruler')} · T+1 {doc.get('t1') or ABSENT}"
               f" → T+2 {doc.get('t2') or ABSENT}"
               f" · 日历 quality={cal_quality or ABSENT}"
               f" · 决策 mode={doc.get('decision_mode') or '?'}"
               + ("(读自共享 staging,未必是本 run 那份)"
                  if doc.get("read_from_shared_staging") else ""))
    # P0 容忍(controller ruling #4):schema 1(2026-09-12 日历完整性修复前)的旧账本
    # 即便 `outcome_status` 字面量恰好是 `MATURE`,也从未核验过 T+1/T+2 真的来自可信
    # 交易日历——schema/日历质量任一不满足"恰好等于可信字面量",一律渲染成未核验,
    # 绝不可能被渲染成"已核验"(`outcome._is_settled` 同一条纪律的视图镜像)。
    verified = (status == MATURE and doc.get("schema_version") == OUTCOME_SCHEMA_VERSION
                and cal_quality == TRADE_CAL_QUALITY)
    if not verified:
        # 2026-09-12 §2(ruling #2):非 MATURE 的文档不携带可汇总的主尺数值——这里没有
        # "半个数字"可展示,只有状态与原因(不落到下面 `row is None` 的通用缺席文案,
        # 那句话是给"这只票压根没被评级/没入 finalist"用的,与"整个 run 还没核验通过"
        # 是两件不同的事,必须分开说)。
        legacy = ("(schema=" + str(doc.get("schema_version") or ABSENT)
                 + "·早于日历完整性修复,T+1/T+2 未经可信交易日历核验,不代表已知涨跌)"
                 if doc.get("schema_version") != OUTCOME_SCHEMA_VERSION else "")
        return out + [f"- **未成熟/未核验**:status={status or ABSENT}"
                      f" · 原因:{doc.get('reason') or ABSENT}{legacy}"]
    row = (doc.get("rows") or {}).get(code6)
    if not isinstance(row, dict):
        return out + ["- 本票不在结果账本(当日未被评级/未入 finalist)"]
    out += _kv([
        ("角色", row.get("role")),
        ("T+1 收 / 当日涨幅", f"{row.get('t1_close')} / {row.get('t1_pct_chg')}%"),
        ("T+1 收盘区间位置", row.get("t1_pos_in_range")),
        # 「执行条件」是**事后按 T+1 收盘价测算**的判断,不是盘中任何时刻被核验过
        # (spec §8 末段:「日线收盘条件不证明盘中某时刻核验过」)。
        ("执行条件(事后按收盘价测算,非盘中核验)", f"exec_ok={row.get('exec_ok')}(追强否决口径)"),
        ("T+2 开", row.get("t2_open")),
        ("推荐毛收益 gap_c1_o2(非实际成交)", f"{row.get('gap_c1_o2')}"),
        ("相对全市场 / 行业", f"{row.get('rel_gap_market')} / {row.get('rel_gap_sector')}"),
        ("fwd_5 / fwd_10(参考尺)", f"{row.get('fwd_5_oc')} / {row.get('fwd_10_oc')}"),
    ])
    # 迟到执行反事实(§2.4 G1):与上面的主尺**不是同一人口**,单独一行、明确标「反事实」
    # 与「非实际成交」,不能让读者把它当成主尺的延伸,也不能读成真的成交回报。
    # `exec_outcome_status is None` = 不适用(正常 run,没有迟到锚)—— 这一行压根不渲染,
    # 不是渲染出一个空值(Ruling #3:不适用与有原因的错误态不能塌缩成同一种"空")。
    exec_status = row.get("exec_outcome_status")
    if exec_status is not None:
        out.append(f"- ⚠️ **执行反事实估计(迟到报告反事实收益)**(迟到锚,非实际成交,"
                   f"不与主尺混算):exec_status={exec_status}"
                   f" · exec_gap_c1_o2={row.get('exec_gap_c1_o2')}")
    # 收益标签四分(controller ruling #4/spec §8 末段):推荐毛收益、事后执行条件测算、
    # 迟到报告反事实收益都已经分开命名——第四个"实际成交"必须**独立一行**说"未知",
    # 不能靠"非实际成交"四个字的否定形态替代(那只是给前三者的免责说明,不是这句本身)。
    out.append("- 实际成交:未知(本模块未接 broker,无法证明任何一笔真的成交;以上均为估算,不是净收益)")
    return out


def _visible_text_blocks(doc: object, *, cap_chars: int = 300) -> list[str]:
    """「可见分析文本」(spec §8:详细模式新增)——普通 assistant 文本/harness 摘要,
    每块至多 `cap_chars` 字。**保守提取**:只认 kind 不是 tool_request/tool_result、
    payload 里带字符串 `text`/`content` 字段的 item;不认识的形状返回空,不猜、不报错
    (item kind 的完整分类是 Task 2 的地界,这里只做已知形状的最小提取,已在报告里
    向复核者标注为已知简化)。"""
    if not isinstance(doc, dict):
        return []
    items = doc.get("items")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("kind") in ("tool_request", "tool_result"):
            continue
        payload = item.get("payload")
        text = (payload.get("text") or payload.get("content")) if isinstance(payload, dict) else None
        if isinstance(text, str) and text.strip():
            clean = text.strip()
            out.append(clean if len(clean) <= cap_chars else clean[: cap_chars - 1] + "…")
    return out


def _sec_scene(src: Sources, code6: str, cache: _EvidenceCache, *, verbose: bool) -> list[str]:
    """⑪ 证据现场——capsule↔ledger 按 invocation 合并后的展示层(spec §6.2/§8)。

    摘要模式只给「来源+原始/补录状态+冲突」与「成功/失败/部分/缺口」计数(spec §8:
    「80 行内必须保留所有冲突类型、缺口计数」);详细模式再加逐项操作(call_id/kind/
    响应与产物摘要)与可见文本块。`cache` 由 `render()` 建一份、贯穿整次渲染传进来
    (fix round 2,finding 1)——与 `_sec_l4` 共用同一个实例,不再各自重建合并表。"""
    out = ["", "## ⑪ 证据现场(transcript 归属)"]
    cap_rows = cache.capsule_invocations()
    led_rows = cache.ledger_invocations()
    if not cap_rows and not led_rows:
        return out + ["- capsule/ledger 均无证据索引(该 run 早于/未启用 transcript 绑定;"
                      "不代表研究没发生,只代表这层证据没有留痕)"]
    merged = cache.merged_invocations()
    relevant: list[tuple[str, str, dict]] = []
    for role in ("l4-card", "l4-intel"):
        found = _find_invocation(merged, role, code6)
        if found is not None:
            iid, m = found
            relevant.append((role, iid, m))
    if not relevant:
        return out + ["- 本票没有可关联的 l4-card/l4-intel invocation"
                      "(未派发,或期望/证据索引均没有这只票的行)"]
    kind_tally: dict[str, int] = {}
    gap_n = 0
    conflict_lines: list[str] = []
    op_lines: list[str] = []
    all_text_blocks: list[str] = []
    for role, iid, m in relevant:
        out.append(f"- {role}(`{iid}`):来源={m['origin']} · 原始状态={m['original_status']}"
                   f" · 补录状态={m['backfill_status']}"
                   + (" · ⚠️冲突" if m["conflict"] else ""))
        if m["conflict"]:
            conflict_lines.append(f"  - {role}:{_fmt(m['conflict_detail'], 200)}")
        doc, reason = cache.normalized_doc(m)
        if doc is None:
            gap_n += 1
            conflict_lines.append(f"  - {role}:证据不足,未观察到(原因:{_fmt(reason, 120)})")
            continue
        # 快照身份(2026-09-13 复核更正):`agents/normalized/<id>.json` 顶层自带
        # `snapshot_id`(capsule.py 写入,Task 2 原始提交就有)——可以如实标注"这批操作
        # 来自哪个快照",但**不能**据此假装知道它在 transcript 里的具体行号
        # (`item_index` 只是它在本 invocation 归一化 items 列表里的位置,过滤/展开会让
        # 它对不上原始行——2026-09-13 Task 2 复核更正,字段已改名 `item_index`)。
        snapshot_id = doc.get("snapshot_id") if isinstance(doc, dict) else None
        for op in _operations(doc):
            k = str(op.get("kind"))
            kind_tally[k] = kind_tally.get(k, 0) + 1
            if verbose:
                resp = op.get("response") or {}
                art = op.get("artifact") or {}
                op_lines.append(
                    f"  - call_id={op.get('call_id') or '—'} · kind={k}"
                    f" · tool={op.get('tool_name')} · path={op.get('path') or '?'}"
                    f" · snapshot={str(snapshot_id)[:12] if snapshot_id else '—'}"
                    f" · item_index={op.get('item_index')}(归一化记录内位置,非 transcript 行号)"
                    f" · response_sha256={str(resp.get('sha256'))[:12] if resp.get('sha256') else '—'}"
                    f" · artifact_sha256={str(art.get('sha256'))[:12] if art.get('sha256') else '—'}")
        if verbose:
            # fix round 1,finding 2:先攒**全部**可见文本块(不在这里就地截断),六块上限
            # 与"省略了多少"的账都留到渲染前一次结算——中途按每个 invocation 分别截断会
            # 让总省略数无法算清(某个 invocation 自己就有 8 块,提前切到 6 就永远不知道
            # 后面 invocation 还有几块被彻底看不见)。
            all_text_blocks += _visible_text_blocks(doc)
    out += conflict_lines
    success = sum(kind_tally.get(k, 0) for k in
                 ("READ_SUCCEEDED", "WRITE_SUCCEEDED", "SEARCH_SUCCEEDED"))
    partial = kind_tally.get("READ_PARTIAL", 0)
    failed = sum(kind_tally.get(k, 0) for k in ("READ_FAILED", "WRITE_FAILED", "SEARCH_FAILED"))
    requested = sum(kind_tally.get(k, 0) for k in
                    ("READ_REQUESTED", "WRITE_REQUESTED", "SEARCH_REQUESTED"))
    out.append(f"- 观察计数:成功 {success} · 部分 {partial} · 失败 {failed} · 仅请求 {requested}"
               f" · 缺口(证据不足) {gap_n}")
    if verbose:
        from autoresearch.scan.exec_anchor import read_execution

        out.append("- 逐项操作(call_id 级;`item_index` 是它在归一化记录里的位置,"
                   "不是 transcript 行号——快照身份见 `snapshot` 与上方来源/invocation 行):")
        out += (op_lines or ["  - (无可分类的操作;normalized 存在但 operations 为空)"])
        anchor = read_execution(src.run)
        approved = anchor.get("decision_approved_at")
        out.append(f"- 时间锚:决策批准时刻={approved or ABSENT}"
                   "(单项操作是否早于/晚于批准时刻,取决于 transcript 时间戳是否留存;"
                   "缺失一律显示未知,不能因为文件被归档就推断当时已看过)")
        if all_text_blocks:
            # fix round 1,finding 2(spec §8「长文本…至多 6 块，明确省略量」):六块上限
            # 之外的内容不能悄悄丢弃——`omitted_blocks` 就是那句话字面要求的"省略量",
            # 只在真的省略了什么时才现出这半句,不为 0 编一句空话。
            shown = all_text_blocks[:6]
            omitted_blocks = len(all_text_blocks) - len(shown)
            out.append(f"- 可见分析文本({len(shown)} 块,每块≤300字"
                       + (f",另省略 {omitted_blocks} 块" if omitted_blocks else "") + "):")
            out += [f"  > {t}" for t in shown]
    return out


_SUMMARY_LINE_BUDGET = 80


#: 摘要预算超限时**受保护、永不截断**的段:①身份/来源+执行时间锚、⑦研究证据(O01/O02
#: 就长在这里)、⑧E6 实际选择+卡面冲突、⑨brief、⑩收益口径——逐字对应 spec §8「默认摘要
#: 优先展示身份/来源、E6 实际选择、卡面冲突……已有执行时间锚与收益口径」那句列的优先级。
#: 可截断的只剩②候选护照/③L1/④L2/⑤pass1/⑥L3 判断文本——**行动漏斗的早段叙事**,真正
#: 复盘时这几段最先被跳读,也是历史上唯一没有测试断言依赖其"在摘要模式里完整出现"的段。
_PROTECTED_SECTIONS = frozenset({"_sec_identity", "_sec_l4", "_sec_e6", "_sec_brief", "_sec_outcome"})


def render(run_dir: Path | str, code: str, *, verbose: bool = False) -> str:
    src = Sources(Path(run_dir))
    code6 = _z6(code)
    # 一次渲染一份缓存(fix round 2,finding 1)——`_sec_l4`/`_sec_scene` 都要读 capsule↔
    # ledger 合并证据,以前各自独立重建、各自独立读盘,一条 PRESENT 记录最多被读 4 次;
    # 现在两段共用同一个 `_EvidenceCache` 实例,每个文件整次渲染最多读一次。
    cache = _EvidenceCache(src, src.run.name)
    head = [f"# 推荐链路 — {code6} @ run `{Path(run_dir).name}`(数据日 {src.analysis_date or '?'})",
            "",
            "_确定性生成(零 LLM);每段的「缺席」都是事实,不是渲染失败。仅供研究,非投资建议。_"]
    order = (_sec_identity, _sec_passport, _sec_l1, _sec_l2, _sec_pass1,
            _sec_l3, _sec_l4, _sec_e6, _sec_brief, _sec_outcome)
    chunks: dict[str, list[str]] = {}
    for fn in order:
        try:
            chunks[fn.__name__] = fn(src, code6, cache) if fn is _sec_l4 else fn(src, code6)
        except Exception as exc:  # noqa: BLE001 — 一段读坏不该让整张视图消失
            chunks[fn.__name__] = ["", f"## {fn.__name__} 渲染失败:{type(exc).__name__}: {exc}"]
    body = [ln for fn in order for ln in chunks[fn.__name__]]
    try:
        scene = _sec_scene(src, code6, cache, verbose=verbose)
    except Exception as exc:  # noqa: BLE001 — 同上,⑪ 读坏不该拖垮整张视图
        scene = ["", f"## ⑪ 证据现场 渲染失败:{type(exc).__name__}: {exc}"]
    # 「读了共享 staging」这条警示只能**最后**判:`used_shared` 是各段读盘时才置位的,
    # 放进 ① 会永远为假(① 跑在所有读盘之前)——写在开头的探针读不到还没发生的事实,
    # 与「brief 读了 relative_buy 的半成品」同一族的时序坑。`find_input` 命中 shared
    # 一样会置位这个旗子,所以「shared 输入参考」也会触发同一条警示。
    tail: list[str] = []
    if src.used_shared:
        tail = ["", "---", "",
               "⚠️ **本视图有片段读自共享 staging**(`context_*/scan/<date>/`)—— 同数据日"
               "重跑会原地覆盖它,那些片段**未必是本 run 当时那份**"
               "(2026-08-26 之前的 run 全部如此)。"]
    if verbose:
        return "\n".join(head + body + scene + tail) + "\n"
    total = len(head) + len(body) + len(scene) + len(tail)
    if total <= _SUMMARY_LINE_BUDGET:
        return "\n".join(head + body + scene + tail) + "\n"
    # 摘要预算超限(spec §8):受保护段(见 `_PROTECTED_SECTIONS`)与 scene(⑪ 的冲突类型/
    # 缺口计数)、tail(共享 staging 警示)永不截断;head 同样不截。截断只吃可截断段的预算,
    # **按原有顺序**从前往后填,填满即止——不改变任何一段在文档里出现的相对位置,只在
    # 超出处插入一条指向 `--verbose` 的提示,不吞掉后面受保护段的内容(旧实现的真实缺陷:
    # ⑩ 结果段曾经因为排在 body 末尾被整段砍掉,收益口径反而是最先消失的东西)。
    protected_len = sum(len(chunks[fn.__name__]) for fn in order
                        if fn.__name__ in _PROTECTED_SECTIONS)
    trimmable_budget = max(0, _SUMMARY_LINE_BUDGET - len(head) - len(scene) - len(tail)
                           - protected_len - 2)   # +2:截断提示本身占的行数
    # 两遍:第一遍只算「在哪一段可截断段里砍、砍剩多少、一共省略几行」,不产出文本——
    # 省略总数要看完全部可截断段才知道,不能一边拼一边写一个还没算完的数字。
    used = 0
    omitted = 0
    cut_at: str | None = None
    keep_of_cut = 0
    for fn in order:
        if fn.__name__ in _PROTECTED_SECTIONS:
            continue
        piece = chunks[fn.__name__]
        if cut_at is not None:
            omitted += len(piece)
            continue
        remain = trimmable_budget - used
        if len(piece) <= remain:
            used += len(piece)
        else:
            cut_at = fn.__name__
            keep_of_cut = max(0, remain)
            omitted += len(piece) - keep_of_cut
    marker = ["", f"…(摘要预算超限,已省略 {omitted} 行 —— 完整链路见 `--verbose`)"]
    # 第二遍:按原有顺序拼,截断提示紧跟在真正被砍的那一段后面(不是甩到全文最后),
    # 后面的受保护段(⑦⑧⑨⑩)照旧完整出现在它们本来的位置。
    new_body: list[str] = []
    cut_done = False
    for fn in order:
        piece = chunks[fn.__name__]
        if fn.__name__ in _PROTECTED_SECTIONS:
            new_body += piece
        elif cut_done:
            continue
        elif fn.__name__ == cut_at:
            new_body += piece[:keep_of_cut] + marker
            cut_done = True
        else:
            new_body += piece
    if cut_at is None:
        # 可截断段全部放得下(超预算全部来自受保护段本身)——没有能安全砍的地方,
        # 宁可略超预算也不能吞掉 spec 点名必须留存的内容,仍然给出 `--verbose` 指向。
        new_body += marker
    return "\n".join(head + new_body + scene + tail) + "\n"


def _resolve_run(value: str) -> Path:
    p = Path(value)
    return p if p.exists() else ws.reports_root() / "scan" / value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="推荐链路视图(零 LLM)")
    ap.add_argument("run", help="run 目录或 run_id(如 20260825_2149)")
    ap.add_argument("code", help="6 位股票代码")
    ap.add_argument("--out", default=None, help="落盘路径(默认打到 stdout)")
    ap.add_argument("--verbose", action="store_true",
                    help="详细模式:逐项操作 call_id/来源/字节摘要/版本匹配/可见分析文本"
                         "(spec §8);默认只打印 ≤80 行摘要,截断处指向本参数")
    args = ap.parse_args(argv)
    run_dir = _resolve_run(args.run)
    if not run_dir.is_dir():
        print(f"run 目录不存在:{run_dir}")
        return 2
    md = render(run_dir, args.code, verbose=args.verbose)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[chain_view] → {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
