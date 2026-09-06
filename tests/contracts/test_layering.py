"""分层守卫 —— 「下层不许 import 上层」。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K9 / §2.4 A7。

## 病灶(2026-08-29 审计实测的 import 矩阵)

依赖方向今天是倒挂的:观察层依赖被观察者。

- `trace → scan` **8 行**(`trace/capsule.py:23-24,443,2335`、`completeness.py:20,137`、
  `capsule_models.py:15`、`usage_reconcile.py:448` import
  `scan.artifacts/run_contract/run_bootstrap/run_profile/user_config`),
  而 `scan → trace` 又有 15 行 —— **成环**;
- `research ⇄ scan`、`scan ⇄ dossier`(自注「防环」的惰性 import)、`scan ⇄ sector`、
  `common ⇄ data`、`data → trace`、`derivatives → scan` 同族。

## 做法(ratchet,不是一次性大扫除)

允许清单 = **今天的现状**,并且**只许减不许增**:新增一条向上的边当场红,
存量的边随 A7 逐条收敛时把清单改小。这样守卫从第一天就有鉴别力,
而不必等重构做完才上线。

`contracts` 是本波新建的最底层,它**一条向上的边都不许有** —— 这条是硬的。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "autoresearch"

#: 从底到顶。同层之间互相 import 不算向上。
LAYERS: tuple[tuple[str, ...], ...] = (
    ("contracts",),
    ("common", "dataflows", "agents"),
    ("data",),
    ("trace",),
    ("news", "derivatives", "dossier"),
    ("sector", "macro", "analyze"),
    ("scan",),
    ("research", "ops"),
)

_LEVEL: dict[str, int] = {p: i for i, layer in enumerate(LAYERS) for p in layer}

#: **存量**的向上边(2026-08-29 实测)。只许减不许增。
#: 每一条都记着它为什么还在,以及归哪件收敛。
KNOWN_UPWARD: frozenset[tuple[str, str]] = frozenset({
    # trace → scan:法证层依赖业务登记表,而 scan → trace 又有 15 行 —— **成环**。
    # A7 的收敛路径 = 把这些声明往下搬。2026-08-31(D6.2)搬掉了两处:`RunContract`
    # 进 `common/run_identity.py`、profile 工厂改按 kind 动态取,于是这条边从
    # **8 行 / 4 文件**降到 **3 行 / 2 文件**(`capsule.py` 的
    # `scan.artifacts` 与惰性 `scan.run_bootstrap`、`usage_reconcile.py` 的
    # `scan.user_config`)。整条边还在,所以这一项还不能删。
    #
    # 2026-09-06(E5 步 3)`usage_reconcile` 那一行收窄成**单一显式旧桥**
    # (`_resolved_via_legacy_bridge`,只在调用方不注入 resolved 时才走):库函数
    # `reconcile_with_resolved` 与 `reconcile(..., resolved_agent_config=…)` 已不读 scan。
    # 行数没变(仍 3 行 / 2 文件)—— CP7 的 `python -m autoresearch.trace.usage_reconcile`
    # 还在走旧桥,砍早了断生产管线;等 CLI 路由迁到 scan 入口,这一行才真正消失。
    ("trace", "scan"),
    # trace → news(1):`evidence_index.py` 读 claim_ledger。
    ("trace", "news"),
    # scan → research(5):`l4/producers`、`populations`、`outcome`、`ledger_views`、
    # `prelude` 用 research 的仪器。收敛路径 = 共享件下沉 common/rulers.py。
    ("scan", "research"),
    # data → trace(1):`cache.py` 落 lineage。
    ("data", "trace"),
    # common → data(2):`scoring.py` 读湖权重、`uzi_lenses.py` 直接取数。
    ("common", "data"),
    # dossier / sector / derivatives → scan(3/2/1):惰性 import,自注「防环」。
    ("dossier", "scan"),
    ("sector", "scan"),
    ("derivatives", "scan"),
})


def _top(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "autoresearch":
        return parts[1]
    return None


def _edges() -> set[tuple[str, str, str]]:
    """(from_pkg, to_pkg, file) 三元组;只看 autoresearch 内部的边。"""
    out: set[tuple[str, str, str]] = set()
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(REPO)
        src_pkg = path.relative_to(PKG).parts[0]
        if src_pkg.endswith(".py"):        # autoresearch/*.py 顶层模块
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                 # 被别的 agent 写到一半 —— 不是分层问题
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                dst = _top(node.module)
            elif isinstance(node, ast.Import):
                dst = next((_top(a.name) for a in node.names if _top(a.name)), None)
            else:
                continue
            if dst and dst != src_pkg and src_pkg in _LEVEL and dst in _LEVEL:
                out.add((src_pkg, dst, str(rel)))
    return out


def test_contracts_never_imports_upward():
    """契约层是最底层 —— 它一条向上的边都不许有(这条是硬的,没有存量豁免)。"""
    bad = [(s, d, f) for s, d, f in _edges() if s == "contracts"]
    assert not bad, f"contracts 不许依赖上层:{sorted(bad)}"


def test_no_new_upward_edges():
    """向上的边只许减不许增。新增一条 = 红。"""
    upward = {
        (s, d) for s, d, _ in _edges() if _LEVEL[s] < _LEVEL[d]
    }
    new = sorted(upward - KNOWN_UPWARD)
    assert not new, (
        "新增了向上的依赖边(下层 import 上层)。要么换个方向,"
        f"要么把声明搬进 autoresearch/contracts/:{new}"
    )


def test_allowlist_has_no_stale_entries():
    """清单里已经不存在的边要及时删掉,否则「只许减」变成一句空话。"""
    upward = {(s, d) for s, d, _ in _edges() if _LEVEL[s] < _LEVEL[d]}
    stale = sorted(KNOWN_UPWARD - upward)
    assert not stale, (
        f"这些向上的边已经没有了,请从 KNOWN_UPWARD 删除(棘轮要收紧):{stale}"
    )


def test_guard_would_catch_a_new_edge():
    """守卫的鉴别力自证:构造一条不在清单里的向上边,它必须被判为新增。"""
    upward = {("scan", "contracts"), ("common", "scan")}   # 后者不在 KNOWN_UPWARD
    new = sorted(e for e in upward if e not in KNOWN_UPWARD and _LEVEL[e[0]] < _LEVEL[e[1]])
    assert new == [("common", "scan")]
