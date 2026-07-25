"""L0/L1/L2 计数行必须进 stdout:bash-agent 只回报 stdout,进 stderr 的等于白打(Wave5 ①)。

**边界**:`frame.py` 的计数行**必须留在 stderr** —— 它的 stdout 是 `--json` 的 payload,
workflow 里 `frame <date> --json > market_pack.json`,往 stdout 多打一个字节就毁掉那份 JSON。
所以本测试只管 universe.py,并显式钉死 frame.py 的反向约束,防后人"顺手统一"。

判据走 AST 而不是逐行 grep:本仓的 print 常跨行(`file=sys.stderr` 落在下一行),
按行匹配会把它们全判成"没带 file="——一个只会误报的守卫和一个不会报的守卫同样没用。
"""
from __future__ import annotations

import ast
from pathlib import Path

UNIVERSE_SRC = Path("autoresearch/scan/universe.py").read_text(encoding="utf-8")
FRAME_SRC = Path("autoresearch/scan/frame.py").read_text(encoding="utf-8")

# 信息类计数行(非 warn/异常)——这些是给人看的进度,必须能被 bash-agent 回报到
_COUNT_MARKERS = ("[L1 召回]", "[L2 粗排]", "[done]", "[数据契约]")


def _print_calls(src: str):
    """→ [(首参字面量前缀, 是否带 file= 关键字)];覆盖 f-string 与普通字符串。"""
    out = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print" and node.args):
            continue
        first = node.args[0]
        text = ""
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            text = first.value
        elif isinstance(first, ast.JoinedStr):
            text = "".join(v.value for v in first.values
                           if isinstance(v, ast.Constant) and isinstance(v.value, str))
        has_file = any(k.arg == "file" for k in node.keywords)
        out.append((text, has_file))
    return out


def test_count_lines_go_to_stdout():
    bad = [t for t, has_file in _print_calls(UNIVERSE_SRC)
           if any(m in t for m in _COUNT_MARKERS) and has_file]
    assert not bad, "这些计数行只进 stderr,bash-agent 看不到:\n" + "\n".join(bad)


def test_the_count_lines_actually_exist():
    """防"把行删了测试也绿":四个计数标记必须都还在。"""
    texts = [t for t, _ in _print_calls(UNIVERSE_SRC)]
    for m in _COUNT_MARKERS:
        assert any(m in t for t in texts), f"universe.py 少了计数行 {m}"


def test_warn_lines_stay_on_stderr():
    """反向约束:警告仍走 stderr(别把 warn 混进给人看的进度流)。"""
    warns = [(t, has_file) for t, has_file in _print_calls(UNIVERSE_SRC) if t.startswith("[warn]")]
    assert warns, "universe.py 一条 [warn] 都没有?先确认是不是被误改了"
    bad = [t for t, has_file in warns if not has_file]
    assert not bad, "warn 行不该进 stdout:\n" + "\n".join(bad)


def test_frame_counts_must_not_touch_stdout():
    """frame 的 stdout = market_pack.json 的 payload(`--json > market_pack.json`)。

    往那里多打一行计数 = 产出非法 JSON = 下游 market_pack 全线失效。这条测试存在的唯一
    目的是拦住"把 universe 改完顺手把 frame 也改了"。
    """
    calls = _print_calls(FRAME_SRC)
    checked = 0
    for marker in ("[frame]", "[sentinel·盘前预告]", "[macro_state]"):
        hits = [(t, has_file) for t, has_file in calls if marker in t]
        assert hits, f"frame.py 找不到 {marker} 打印(源码结构变了,先确认这条约束还在哪)"
        for t, has_file in hits:
            assert has_file, f"frame 的 {marker} 打印必须带 file=(stdout 是 --json 的 payload)"
            checked += 1
    assert checked >= 3
