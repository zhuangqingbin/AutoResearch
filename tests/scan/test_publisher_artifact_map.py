"""三份「该有什么」登记表改为从 `contracts.artifacts` 派生后的 **parity 守卫**(Task 9c)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K1/K3 · §2.4 A1
plan:   `docs/superpowers/plans/2026-08-29-full-coverage-p0-p1.md` Task 9

本任务是**纯管线改造**:三张表原来各自把产物名又写了一遍(K1 说的「一个名字改 40 个文件」),
现在改成从登记表取路径 / 校验登记名。**一个输出字节都不许动** —— 所以这里的每条断言都是
「派生结果 == 今天的硬编码值」,期望值是从改造前的源码里**逐字抄**过来的字面量:

  - `brief.INPUT_WHITELIST`            —— 改造前 `scan/brief.py:84-98` 的 13 项;
  - `publisher.TRACE_MAPPING`          —— 改造前 `scan/publisher.py:244-254` 的 9 项(含顺序);
  - `publisher.ASSEMBLE_STAGE_ARTIFACTS` —— 改造前 `scan/publisher.py:438-441` 的 7 项。

**期望值不许为了迁就实现而改**:派生结果与它们不等 = 这次改造真的挪动了产物,是回归。

另外两类断言(它们才是这次改造买到的东西):
  ① **drift 守卫** —— 表里每个登记名都必须能 `contracts.by_name()` 解析出**同一个路径**;
     打错一个字 / 登记表里改了名而这里没跟上,导入即炸,不会安静地漂;
  ② **接线守卫**(FN-1 家训:生产者没接线 = 死码)—— 常量必须真的被生产路径用:
     trace 映射走真拷贝、assemble 认领的产物清单走真 `stage_results/assemble.json`。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autoresearch.contracts import artifacts as C
from autoresearch.scan import brief, publisher
from tests.scan.test_brief import _RUN, _scan_dir

# ── 改造前的硬编码值(逐字抄自 `git show` 的改造前源码;**不许改**)────────────────

_BRIEF_WHITELIST_BEFORE = (
    "meta.json",
    "finalists.csv",
    "_final_ratings.json",
    "decision_records.json",
    "run_mode.json",
    "run_health.json",
    "gate_fires.csv",
    "_tripwire_conflicts.json",
    "market_view.md",
    "_relative_buy_decision.json",
    "temperature.csv",
    "menu_health",
    "overseas_calendar.csv",
)

_TRACE_MAPPING_BEFORE = (
    ("meta.json", "L0_universe_meta.json"),
    ("run_contract.json", "run_contract.json"),
    ("run_health.json", "run_health.json"),
    ("weights_used.json", "weights_used.json"),
    ("L1_scored_full.csv", "L1_scored_full.csv"),
    ("L1_recall_top1000.csv", "L1_recall_top1000.csv"),
    ("L2_gbdt_top200.csv", "L2_gbdt_top200.csv"),
    ("L3_judged_full.csv", "L3_judged_full.csv"),
    ("finalists.csv", "L3_fine_finalists.csv"),
)

_ASSEMBLE_ARTIFACTS_BEFORE = (
    "final_ratings", "decision_records", "gate_fires", "run_health",
    "summary", "appendix", "manifest",
)


# ── ① brief 输入白名单 ────────────────────────────────────────────────────────

def test_brief_whitelist_is_byte_for_byte_todays_list():
    """parity:派生出来的白名单与改造前的字面量**逐项相等**(顺序也一样)。"""
    assert brief.INPUT_WHITELIST == _BRIEF_WHITELIST_BEFORE


def test_brief_whitelist_names_resolve_in_the_registry():
    """drift 守卫:白名单里凡是登记产物的那几项,路径必须由登记表给出。

    这条才是这次改造的收益:登记表里把 `finalists.csv` 改名,而 brief 没跟上 → 红;
    今天靠人肉两处各写一份字面量,改名只会安静地漂(K1 的病)。
    """
    assert brief.REGISTERED_INPUTS, "登记名清单空了 —— 白名单又变回一堆裸字面量"
    for name in brief.REGISTERED_INPUTS:
        assert C.by_name(name).path in brief.INPUT_WHITELIST, \
            f"登记产物 {name} 的路径不在白名单里 —— 派生断了"


def test_brief_whitelist_does_not_widen_permissions():
    """白名单是**许可**表,比登记表窄:登记表里 85 个产物,brief 只准读这 10 个。

    反面锚:哪天有人图省事写成 `for_root("staging")`,这条立刻红。
    """
    staging_paths = {a.path for a in C.for_root("staging")}
    assert len(brief.REGISTERED_INPUTS) == 10
    assert len(staging_paths) > 3 * len(brief.REGISTERED_INPUTS), \
        "登记表突然变小了?这条锚是为了保证下面那句『窄很多』还有意义"
    derived = {C.by_name(n).path for n in brief.REGISTERED_INPUTS}
    assert derived < staging_paths | {C.by_name("run_health").path}, \
        "brief 可读集不再是登记表的真子集 —— 许可被放宽了"


def test_brief_unregistered_inputs_are_declared_and_still_only_three():
    """白名单里**没进登记表**的三项必须显式列出来,不许混在登记名里蒙混过关。

    `_tripwire_conflicts.json` / `temperature.csv` 今天在 `contracts.NON_ARTIFACT_LITERALS`
    里(审计判定「不是流水线产物」),`menu_health` 是确定性派生的虚拟项(根本不是文件)。
    三项都不能 `by_name()`,所以只能留字面量 —— 但要留得**看得见**。
    """
    assert brief.UNREGISTERED_INPUTS == (
        "_tripwire_conflicts.json", "temperature.csv", "menu_health")
    for name in brief.UNREGISTERED_INPUTS:
        assert name not in {a.name for a in C.ARTIFACTS}
        assert name in brief.INPUT_WHITELIST
    # 前两项确实是被审计判过的「非产物」,不是漏登记
    assert {"_tripwire_conflicts.json", "temperature.csv"} <= C.NON_ARTIFACT_LITERALS


def test_decision_filename_agrees_with_the_registry():
    """两个真身对得上:白名单那一项来自登记表,而**读点**用的是 `relative_buy.DECISION_FILENAME`。

    AST 那条 ⊇ 不变量抓不到它(读点是 import 来的名字,不是字面量),所以在这里显式对拍 ——
    否则登记表改名之后,白名单会跟着变、读点不变,brief 变成读白名单外的文件而无人报警。
    """
    assert C.by_name("relative_buy_decision").path == brief.DECISION_FILENAME


# ── ② publisher 的 trace 映射表 ──────────────────────────────────────────────

def test_trace_mapping_is_byte_for_byte_todays_map():
    """parity:9 项 src→dst 一字不差,**连顺序**都一样(拷贝顺序即 trace 目录成型顺序)。"""
    assert tuple(publisher.TRACE_MAPPING.items()) == _TRACE_MAPPING_BEFORE


def test_trace_mapping_sources_resolve_in_the_registry():
    """drift 守卫:被搬进 trace 的 9 个 staging 产物,源文件名全部来自登记表。"""
    for name in publisher.TRACE_SOURCE_NAMES:
        assert C.by_name(name).path in publisher.TRACE_MAPPING, \
            f"{name} 登记的路径不是映射表的键 —— 派生断了"
    assert len(publisher.TRACE_SOURCE_NAMES) == len(publisher.TRACE_MAPPING) == 9


def test_trace_mapping_is_actually_used_by_the_publisher(tmp_path):
    """接线守卫(FN-1):常量不是许愿单 —— `_publish_pipeline` 必须按它真拷贝。

    变异探针:把映射表里任一项删掉,trace 目录就少一个文件,本条红。
    """
    scan = tmp_path / "scan"
    scan.mkdir()
    for src in publisher.TRACE_MAPPING:
        # 同名 funnel.md 会真读 meta.json / finalists.csv,所以造的是**可解析**的最小件
        (scan / src).write_text("{}" if src.endswith(".json") else "code,lane\n",
                                encoding="utf-8")
    out = tmp_path / "run"
    publisher._publish_pipeline(scan, out, "2026-08-06")
    landed = {p.name for p in (out / "trace").iterdir() if p.is_file()}
    assert set(publisher.TRACE_MAPPING.values()) <= landed, \
        f"映射表里的目的文件没全落地:{set(publisher.TRACE_MAPPING.values()) - landed}"


# ── ③ assemble 阶段认领的产物清单 ───────────────────────────────────────────

def test_assemble_stage_artifacts_are_byte_for_byte_todays_list():
    assert publisher.ASSEMBLE_STAGE_ARTIFACTS == _ASSEMBLE_ARTIFACTS_BEFORE


def test_assemble_stage_artifacts_are_registered_names():
    """drift 守卫:StageResult 里认领的名字必须是**登记名**(不是随手编的标签)。

    这七个名字会被 `completeness` 当成「这一阶段该有什么」的证据读,拼错一个 =
    完整性结论静悄悄地少一件。
    """
    for name in publisher.ASSEMBLE_STAGE_ARTIFACTS:
        assert C.by_name(name).name == name


def test_assemble_stage_result_records_the_derived_list(tmp_path):
    """接线守卫 + 活体:走 `assemble.run` 全链,落盘的 `stage_results/assemble.json`
    里的 artifacts 必须**就是**这份派生清单。

    变异探针:把 `artifacts=list(ASSEMBLE_STAGE_ARTIFACTS)` 改回手写字面量并少一项,
    本条红(而只看常量的那条不会)。
    """
    from autoresearch.scan import assemble

    scan = _scan_dir(tmp_path)
    assemble.run("2026-08-06", scan_dir=scan, out_root=tmp_path / "reports" / "scan",
                 hhmm="2308", run_date="2026-08-06")
    doc = json.loads((scan / "stage_results" / "assemble.json").read_text(encoding="utf-8"))
    assert tuple(doc["artifacts"]) == publisher.ASSEMBLE_STAGE_ARTIFACTS


# ── ④ 产物字节没动(本任务的硬约束)─────────────────────────────────────────

#: 改造**之前**用 `tests/scan/test_brief.py` 的共享夹具渲染出的 brief.md 的 sha256。
#: 本任务是登记表管线改造,`INPUT_WHITELIST` 在渲染路径上零消费者,所以这个 hash
#: 必须一个 bit 都不变。**要动它必须是有意改 brief 内容**(那时 test_brief.py 会先红一片)。
_BRIEF_SHA256_BEFORE = "3e21fbcff1c5e83baccc04c51330774be6e7fcfc1dc0e50c4243e1c59cfe9e62"


def test_brief_bytes_did_not_move(tmp_path):
    md = brief.build(_scan_dir(tmp_path), run_folder=_RUN)["markdown"]
    got = hashlib.sha256(md.encode("utf-8")).hexdigest()
    assert got == _BRIEF_SHA256_BEFORE, (
        "brief.md 的字节变了 —— 本任务只准改登记表派生,不准动一个输出字节。\n"
        f"实际 {len(md.encode('utf-8'))}B / {got}")


def test_the_byte_pin_reads_the_real_product(tmp_path):
    """反面锚:上一条比的是真 brief,不是空串(「探针死了也像活着」同族)。"""
    md = brief.build(_scan_dir(tmp_path), run_folder=_RUN)["markdown"]
    assert "③ 结论" in md and len(md.encode("utf-8")) > 1000
    assert Path(brief.__file__).name == "brief.py"
