"""Wave3 ④:L4 prompt 注入覆盖档案摘要(presence-gated·parity)。"""
from autoresearch.dossier import schema
from autoresearch.scan.agents.l4_card import _dossier_summary_mark


def _mk(code="300857", initiated="2026-07-23", summary_pad=""):
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ("---\ncode: " + code + "\nname: 协创数据\nsector: 消费电子\n"
            "pool_status: active\nentered: 2026-07-23\nentry_reason: pinned\n"
            f"initiated: {initiated}\nlast_refresh: null\nlast_delta: null\n---\n"
            f"{schema.SUMMARY_HEAD}\n- 业务: 算力租赁{summary_pad}\n- 驱动: NAND 周期\n"
            "- 带位: >P75\n- 风险: CFO/NI 0.36\n- 催化: 8/28 中报\n- 判例: 入围 5 次\n"
            + "".join(f"{s}\n(略)\n" for s in schema.SECTIONS))
    p.write_text(text, encoding="utf-8")
    return p


def _mk_distinct(code, initiated="2026-07-23"):
    """八节各带**独有** marker 正文(与 `_mk()` 统一 "(略)" 占位不同)——供
    `dossier_sections` 边界/串段测试用:能精确断言"取 §N 时,§(N±1) 的 marker 绝不
    出现在输出里"(复核 2026-07-30 Important 1:`_mk()` 的同质占位测不出切片串段)。
    码用 999xxx 段(不与本文件既有 300857/600000-7/999998-9,也不与任何真实生产档案
    码——已核实 31 份真实档案无一以 999 开头——冲突)。
    """
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ("---\ncode: " + code + "\nname: 边界测试\nsector: 测试\n"
            "pool_status: active\nentered: 2026-07-23\nentry_reason: pinned\n"
            f"initiated: {initiated}\nlast_refresh: null\nlast_delta: null\n---\n"
            f"{schema.SUMMARY_HEAD}\n- 业务: x\n- 驱动: x\n- 带位: x\n"
            "- 风险: x\n- 催化: x\n- 判例: x\n"
            + "".join(f"{s}\nMARKER_SEC{i}_BODY\n"
                      for i, s in enumerate(schema.SECTIONS, start=1)))
    p.write_text(text, encoding="utf-8")
    return p


def test_mark_injects_summary_and_contract_line():
    _mk()
    out = _dossier_summary_mark("300857")
    assert "📚 覆盖档案摘要" in out
    assert "- 业务: 算力租赁" in out and "- 判例: 入围 5 次" in out
    assert "档案对账" in out                      # 卡内节要求随注入声明
    assert "随每日 δ 刷新" in out                  # review M-2:刷新口径声明锚入测(防措辞漂移)
    assert str(schema.dossier_path("300857")) in out   # 全文路径指针
    assert schema.SECTIONS[0] not in out          # 只注摘要块,不带八节正文


def test_mark_presence_gated_missing_and_skeleton():
    assert _dossier_summary_mark("999999") == ""          # 无档案
    _mk(code="600000", initiated="null")
    assert _dossier_summary_mark("600000") == ""          # 骨架未首覆(四行占位是噪声)


def test_mark_skips_over_cap_summary():
    _mk(code="600001", summary_pad="х" * 12000)           # 摘要超 3k token → 不注
    assert _dossier_summary_mark("600001") == ""


def test_injectable_summary_four_gates():
    """schema.injectable_summary 单一事实源四门(review R1 important:注入器与 lint 同源锁)。

    缺档案 / 未首覆 / 摘要块缺 / 超帽 → "";四门皆过 → 返回摘要块本身(不含 head/tail 装饰,
    与 `_dossier_summary_mark` 分层——mark 只在 block 非空时才拼 head/tail)。
    """
    from autoresearch.dossier import schema

    assert schema.injectable_summary("999998") == ""              # 缺档案

    _mk(code="600002", initiated="null")                          # 骨架未首覆
    assert schema.injectable_summary("600002") == ""

    p = schema.dossier_path("600003")                              # 已首覆但缺摘要块
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ncode: 600003\nname: x\nsector: x\npool_status: active\n"
                 "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
                 "last_refresh: null\nlast_delta: null\n---\n", encoding="utf-8")
    assert schema.injectable_summary("600003") == ""

    _mk(code="600004", summary_pad="х" * 12000)                    # 摘要超 3k token
    assert schema.injectable_summary("600004") == ""

    _mk(code="600005")                                             # 正常:四门皆过
    block = schema.injectable_summary("600005")
    assert block and "- 业务: 算力租赁" in block and "- 判例: 入围 5 次" in block
    assert schema.SECTIONS[0] not in block                         # 只回摘要块,不带八节正文


def test_dispatch_meta_carries_dossier_summary(tmp_path):
    """intel 已知底走 meta 内嵌(不再靠给 agent 授权 Read)。

    N-12(2026-07-24 终审记账):本条是 `dispatch_plan` meta 携带 `dossier_summary`
    这条不变量的**唯一**守卫(跨 task 变异 M2 实测:把 `_dossier_summary_text` 换成
    恒返回 `""` 后,全量回归里只有本条测试变红)。
    """
    from autoresearch.scan.agents.l4_card import dispatch_plan
    sd = tmp_path / "2026-07-24"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector\n300857,协创数据,消费电子\n002926,华西证券,非银金融\n",
        encoding="utf-8")
    (sd / "_l4_prompt_300857.md").write_text("x", encoding="utf-8")
    (sd / "_l4_prompt_002926.md").write_text("x", encoding="utf-8")
    _mk()                                            # 300857 已首覆(文件顶部 helper)
    plan = dispatch_plan("2026-07-24", root=tmp_path)
    assert "业务: 算力租赁" in plan["meta"]["300857"]["dossier_summary"]
    assert plan["meta"]["002926"]["dossier_summary"] == ""     # 无档案 → 空(parity)


def test_dispatch_meta_dossier_summary_gated_uninitiated(tmp_path):
    """intel 已知底走 dispatch_plan 时仍受单一事实源四门约束:未首覆骨架不可注入。

    review R1 I-3:变异实测把 `_dossier_summary_text` 换成绕开 `injectable_summary`
    四门直接读摘要块的版本后,scan+agent_defs 618/618 仍 PASS——卡侧那条腿有
    `test_mark_presence_gated_missing_and_skeleton` 锁着,intel 这条腿(经 dispatch_plan
    的 meta)此前一条没继承,补上。
    """
    from autoresearch.scan.agents.l4_card import dispatch_plan
    sd = tmp_path / "2026-07-24"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector\n600006,示例票六,示例行业\n", encoding="utf-8")
    (sd / "_l4_prompt_600006.md").write_text("x", encoding="utf-8")
    _mk(code="600006", initiated="null")              # 骨架未首覆(四行占位是噪声)
    plan = dispatch_plan("2026-07-24", root=tmp_path)
    assert plan["meta"]["600006"]["dossier_summary"] == ""


def test_dispatch_meta_dossier_summary_gated_over_cap(tmp_path):
    """intel 已知底走 dispatch_plan 时仍受单一事实源四门约束:摘要超帽不可注入(同上,review R1 I-3)。"""
    from autoresearch.scan.agents.l4_card import dispatch_plan
    sd = tmp_path / "2026-07-24"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector\n600007,示例票七,示例行业\n", encoding="utf-8")
    (sd / "_l4_prompt_600007.md").write_text("x", encoding="utf-8")
    _mk(code="600007", summary_pad="х" * 12000)        # 摘要超 3k token
    plan = dispatch_plan("2026-07-24", root=tmp_path)
    assert plan["meta"]["600007"]["dossier_summary"] == ""


# ── dossier_sections() 单测(复核 2026-07-30 Important 1:此前零覆盖——把函数硬编码成
# `return ""` 跑生产链路 146/146 用例仍绿,这里补的正是这道缝)。──────────────────────

def test_dossier_sections_no_bleed_between_adjacent_sections():
    """取 §1/§3/§5 时,相邻未取节(§2/§4/§6/§7/§8)的独有 marker 绝不出现在输出里——
    切片边界不能串到邻节。"""
    from autoresearch.dossier.schema import dossier_sections
    _mk_distinct("999011")
    out = dossier_sections("999011", keys=("§1", "§3", "§5"))
    assert "MARKER_SEC1_BODY" in out
    assert "MARKER_SEC3_BODY" in out
    assert "MARKER_SEC5_BODY" in out
    for i in (2, 4, 6, 7, 8):
        assert f"MARKER_SEC{i}_BODY" not in out, f"§{i} 串进了未请求的输出(切片边界坏了)"


def test_dossier_sections_last_section_reaches_end_of_file():
    """取最后一节(§8,文末无下一个 `## ` 边界可切)仍取到完整正文,不因
    `_section_block` 的边界查找失败(`j = text.find("\\n## ", ...)` 返回 -1)被砍空。"""
    from autoresearch.dossier.schema import dossier_sections
    _mk_distinct("999012")
    out = dossier_sections("999012", keys=("§8",))
    assert "MARKER_SEC8_BODY" in out


def test_dossier_sections_missing_header_skips_key_not_bleeds_neighbor():
    """请求的节标题在文档里根本不存在(伪造缺 §5 整节)→ 该 key 静默跳过返回段落间的
    空缺,绝不会把 §4/§6 的内容错当成 §5 顶替回来(`_section_block` 是 `find(head)`
    找不到就直接返回 "",不会退而求其次模糊匹配别的标题)。"""
    from autoresearch.dossier.schema import dossier_sections
    p = _mk_distinct("999013")
    text = p.read_text(encoding="utf-8").replace("## 5. 风险矩阵\nMARKER_SEC5_BODY\n", "")
    p.write_text(text, encoding="utf-8")
    out = dossier_sections("999013", keys=("§5",))
    assert out == ""
    assert "MARKER_SEC4_BODY" not in out and "MARKER_SEC6_BODY" not in out


def test_dossier_sections_no_dossier_file_returns_empty():
    from autoresearch.dossier.schema import dossier_sections
    assert dossier_sections("999999", keys=("§1", "§2")) == ""     # 复用既有"无档案"码


def test_dossier_sections_within_cap_no_truncation_marker():
    """正常体量(远小于 cap)不应出现截断标记——防止把标记行错当成默认输出的一部分。"""
    from autoresearch.dossier.schema import dossier_sections
    _mk_distinct("999014")
    out = dossier_sections("999014", keys=("§1", "§2", "§3", "§5"), cap=100000)
    assert "截断" not in out


def test_dossier_sections_over_cap_truncates_with_explicit_marker():
    """超 cap → 截断且留一行**显式**标记,不静默丢成空字符串(复核 Important 2:与
    `injectable_summary` 的"超帽即弃"刻意不同——四节是研报体叙事主体,弃了等于卡片
    啥也没有;截断保留能塞下的整节 + 一行标记)。"""
    from autoresearch.dossier.schema import dossier_sections
    _mk_distinct("999015")
    full = dossier_sections("999015", keys=("§1", "§2", "§3", "§5"))
    assert full and "截断" not in full
    full_n_sections = sum(f"MARKER_SEC{i}_BODY" in full for i in (1, 2, 3, 5))
    assert full_n_sections == 4                    # 无 cap 时四节全在

    capped = dossier_sections("999015", keys=("§1", "§2", "§3", "§5"), cap=1)
    assert capped, "超 cap 不应静默返回空 —— 至少留一行截断标记,不能连标记都没有"
    assert "截断" in capped
    assert "MARKER_SEC1_BODY" in capped, "第一节即使单独超 cap 也应强留,不能一节都不剩"
    # 精确断言"确实少收了节"(不用原始字节长度比大小——截断标记行本身带一段绝对路径,
    # 在本测试这种"每节正文只有一行占位"的小体量夹具下,标记行字节数反而可能超过被砍
    # 掉的正文,单纯比 len() 会给出误导性的假阴性;数"还剩几个不同节的 marker"更精确)。
    capped_n_sections = sum(f"MARKER_SEC{i}_BODY" in capped for i in (1, 2, 3, 5))
    assert capped_n_sections < full_n_sections, "cap=1 应该砍掉至少一节,不能四节还是全在"
