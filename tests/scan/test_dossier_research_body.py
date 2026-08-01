"""决策卡档案研报体(Wave9 B-3):`product_shape_lint` 探针 10。

有档案票(`_dossier_present.json` 记录)必须写「研报体(档案δ)」或「微研报」段;
无档案票必须写「档案未建」缺档声明行。两侧口径都对齐 `.claude/agents/l4-card.md`
模板措辞——这是同一批改动的两半,漂了就是「lint 罚一个没被告知过的 agent」。
"""
from autoresearch.learning.self_review import product_shape_lint


def _mk(tmp_path, card_body: str, has_dossier: bool):
    d = tmp_path / "2026-07-29"
    (d / "details").mkdir(parents=True)
    (d / "details" / "000651.md").write_text(card_body, encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,lane\n000651,格力电器,healthy\n",
                                     encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        '["000651"]' if has_dossier else "[]", encoding="utf-8")
    return d


FULL = """# 决策卡 — 000651 格力电器 @ 2026-07-29
## 维度评分卡
| 基本面 | 强 |
**Rating**: Hold
"""


def test_warns_when_dossier_exists_but_no_research_body(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["check"] for h in hits)


def test_no_warn_when_research_body_present(tmp_path):
    d = _mk(tmp_path, FULL + "\n## 研报体(档案δ)\n- **业务**:空调\n", has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["check"] for h in hits)


def test_no_warn_when_no_dossier_but_declared(tmp_path):
    d = _mk(tmp_path, FULL + "\n研报体:档案未建(已插队今晚建档)\n", has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["check"] for h in hits)


def test_warns_when_no_dossier_and_no_declaration(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["check"] for h in hits)


# ── 生产侧冒烟(复核 2026-07-30 Important 1):以上 4 条只测 `product_shape_lint`
# (消费侧,喂手写 `_dossier_present.json` 夹具)。没有任何测试真正调过 `write_dispatch_pack`
# 去验证它自己算出的 `with_dossier` / 落盘的 `_dossier_present.json` / prompt 文件里的
# 研报体素材是不是对的(生产侧)。复核把 `dossier_sections` 硬编码成 `return ""` 跑
# 146 个触碰 write_dispatch_pack/compose_funnel_brief 的用例仍 146/146 绿 —— 这里补上
# 这条链路本身的直接覆盖。码用 999xxx 段,不与任何真实档案/既有测试码冲突。────────────

def _mk_dossier_for_dispatch(code, marker="DISPATCHTEST_MARKER"):
    """真实档案文件,§1/§5 正文含可断言的 distinctive marker(其余节留占位)。"""
    from autoresearch.dossier import schema
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ("---\ncode: " + code + "\nname: x\nsector: x\npool_status: active\n"
            "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
            "last_refresh: null\nlast_delta: null\n---\n"
            f"{schema.SUMMARY_HEAD}\n- 业务: x\n- 驱动: x\n- 带位: x\n"
            "- 风险: x\n- 催化: x\n- 判例: x\n"
            + "".join(f"{s}\n{marker if s in (schema.SECTIONS[0], schema.SECTIONS[4]) else '(略)'}\n"
                      for s in schema.SECTIONS))
    p.write_text(text, encoding="utf-8")
    return p


def test_write_dispatch_pack_dossier_present_and_body_wired_legacy(tmp_path):
    """唯一的 prompt 组装路径(Wave10 B4 起 stable_context 分支已删):
    ① `_dossier_present.json` 落盘内容与磁盘上"谁真的有档案文件"精确一致;
    ② 有档案票的 prompt 文件里**真的含四节正文**(distinctive marker),不只是
    「### 档案节选」这行标题;③ 无档案票两者皆无。
    """
    import json

    import pandas as pd

    from autoresearch.scan.agents.l4_card import write_dispatch_pack

    code_with, code_without = "999021", "999022"
    _mk_dossier_for_dispatch(code_with)                # code_without 故意不建档案文件

    sd = tmp_path / "2026-07-29"
    sd.mkdir()
    pd.DataFrame({"code": [code_with, code_without], "name": ["甲", "乙"],
                  "sector": ["测试行业", "测试行业"]}).to_csv(sd / "finalists.csv", index=False)

    res = write_dispatch_pack(sd)
    assert res["n_prompts"] == 2

    present = json.loads((sd / "_dossier_present.json").read_text(encoding="utf-8"))
    assert present == [code_with]                      # ① with_dossier 落盘内容与磁盘现实一致

    text_with = (sd / f"_l4_prompt_{code_with}.md").read_text(encoding="utf-8")
    assert "### 档案节选(研报体素材)" in text_with
    assert "DISPATCHTEST_MARKER" in text_with           # ② 真的含四节正文,不只是标题

    text_without = (sd / f"_l4_prompt_{code_without}.md").read_text(encoding="utf-8")
    assert "### 档案节选(研报体素材)" not in text_without
    assert "DISPATCHTEST_MARKER" not in text_without    # ③ 无档案票两者皆无


# Wave10 B4:stable_context 分支已退役;legacy 那条用例保留 —— 它现在是**唯一**的路。
