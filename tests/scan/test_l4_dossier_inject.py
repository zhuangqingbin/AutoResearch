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
    assert str(schema.dossier_path("300857")) in out   # 全文路径指针
    assert schema.SECTIONS[0] not in out          # 只注摘要块,不带八节正文


def test_mark_presence_gated_missing_and_skeleton():
    assert _dossier_summary_mark("999999") == ""          # 无档案
    _mk(code="600000", initiated="null")
    assert _dossier_summary_mark("600000") == ""          # 骨架未首覆(四行占位是噪声)


def test_mark_skips_over_cap_summary():
    _mk(code="600001", summary_pad="х" * 12000)           # 摘要超 3k token → 不注
    assert _dossier_summary_mark("600001") == ""
