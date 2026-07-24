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
