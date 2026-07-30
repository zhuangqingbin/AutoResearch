"""dossier 档案格式契约(八节锚+frontmatter+摘要 lint;确定性,零 LLM)。

spec: docs/specs/2026-07-22-research-depth-dossier-design.md ①。八节标题与摘要锚是
机器契约:builder 写、lint 校、L4 注入器(Wave 3)按锚裁剪——改动须同步三方。
"""
from __future__ import annotations

from pathlib import Path

DOSSIER_DIR = Path("context/knowledge/dossiers")

SECTIONS: tuple[str, ...] = (
    "## 1. 业务模型", "## 2. 盈利驱动与预测留档", "## 3. 估值带",
    "## 4. 筹码与资金结构史", "## 5. 风险矩阵", "## 6. 催化剂日历",
    "## 7. 判例账本", "## 8. 变化项日志",
)
SUMMARY_HEAD = "## 摘要(注入用)"
SUMMARY_ANCHORS: tuple[str, ...] = ("业务:", "驱动:", "带位:", "风险:", "催化:", "判例:")
SUMMARY_CAP = 3000    # 注入摘要 token 硬帽(spec ①;lint 与注入器同源引用)

# 研报体素材(§1/§2/§3/§5 四节合计)token 硬帽(与 SUMMARY_CAP 同源单位 est_tokens)。
# 复核 Important 2(2026-07-30)实测:31 份真实档案该四节合计 6.8–15.1KB(≈2.4k–5.4k
# token,中位 ≈13.0KB≈4.6k token)——12000 留 >2x 头寸,当前无一份会被截。超限行为与
# SUMMARY_CAP 刻意不同:SUMMARY_CAP 超限即弃(摘要六行,弃了不伤大局);这里四节是
# 研报体叙事主体,弃了等于卡片啥也没有,`dossier_sections` 改为截断保留 + 显式标记。
RESEARCH_BODY_CAP = 12000

_META_KEYS = ("code", "name", "sector", "pool_status", "entered", "entry_reason",
              "initiated", "last_refresh", "last_delta")


def dossier_path(code6: str) -> Path:
    return DOSSIER_DIR / f"{str(code6).zfill(6)}.md"


def est_tokens(text: str) -> int:
    return int(len(text.encode("utf-8")) / 2.8)


def render_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k in _META_KEYS:
        v = meta.get(k)
        lines.append(f"{k}: {'null' if v is None else v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict = {}
    for ln in text[3:end].strip().splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        out[k.strip()] = None if v in ("null", "") else v
    return out


def _section_block(text: str, head: str) -> str:
    """`head`(原文标题字面量,如 `SUMMARY_HEAD` 或 `SECTIONS[i]`)到下一个 `## ` 标题
    (或文末)的原文切片;找不到 `head` → ""。`_summary_block`/`dossier_sections` 共用。
    """
    i = text.find(head)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(head))
    return text[i:j] if j > 0 else text[i:]


def _summary_block(text: str) -> str:
    return _section_block(text, SUMMARY_HEAD)


def dossier_sections(code6: str, keys: tuple[str, ...], *, cap: int = RESEARCH_BODY_CAP) -> str:
    """按 `§N` 简写拼接档案对应小节全文(研报体素材;Wave9 B-3)。

    `keys` 用 `"§N"` 简写(N=1..8),映射到 `SECTIONS[N-1]` 的真实标题字面量
    `"## N. ..."`——档案节标题不是 `## §N` 字面量(真实格式见 `SECTIONS`),这里做
    简写→真实标题的转译,供调用方少记一份手写映射。

    gate 只认**文件存在**(与 `_dossier_present.json` 的生产口径同门:dispatch 侧只要
    找到档案文件就算"有档案可注入"),不像 `injectable_summary` 那样额外要求
    `initiated` 真——`build_skeleton` 阶段部分节(如估值带)已有确定性表格可用,未首覆
    的节读到的是 `<!-- LLM:待首覆 --> `占位,这本身就是对读者(agent)诚实的"未覆盖"
    信号而非垃圾;额外加 `initiated` 门只会制造"标记为有档案却啥也没注入"的缝。
    单节缺失/坏档 → 该节跳过或整体返回 "",不抛异常(派发不因档案层故障中断)。

    `cap`(token,`est_tokens` 同源单位,默认 `RESEARCH_BODY_CAP`):按 `keys` 顺序整节
    累加,一旦下一节会让累计超过 `cap` 就停(已收的整节保留,不砍到半截句子/半张表);
    第一节即使单独超 `cap` 也强留(给读者一点东西,好过一个字都没有)。**截断不静默**——
    末尾追加一行 `⚠️` 显式标记 + 档案路径指回全文(复核 2026-07-30 Important 2:与
    `injectable_summary` 的"超帽即弃"刻意不同,四节是研报体叙事主体,弃了等于卡片
    啥也没有)。
    """
    try:
        p = dossier_path(code6)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        blocks = []
        for key in keys:
            if not (key.startswith("§") and key[1:].isdigit()):
                continue
            idx = int(key[1:]) - 1
            if not (0 <= idx < len(SECTIONS)):
                continue
            block = _section_block(text, SECTIONS[idx])
            if block:
                blocks.append(block.strip())
        kept: list[str] = []
        budget = 0
        truncated = False
        for b in blocks:
            bt = est_tokens(b)
            if kept and budget + bt > cap:
                truncated = True
                break
            kept.append(b)
            budget += bt
        out = "\n\n".join(kept)
        if truncated:
            marker = f"⚠️ 档案节选超 {cap} token 硬帽,已截断(原文按需 Read `{p}`)"
            out = (out + "\n\n> " + marker) if out else "> " + marker
        return out
    except Exception:  # noqa: BLE001 — 坏档=不可注入,不抛
        return ""


def injectable_summary(code6: str) -> str:
    """可注入的摘要块;不可注入(缺档案/未首覆/摘要缺/超帽)→ ""。

    L4 注入器与卡契约 lint 的**单一分档事实源**(生产者/消费者必须同门,
    防「没注入却照查」的 FN-1 族缝)。异常吞成 ""(坏档不挡派发/lint)。
    """
    try:
        p = dossier_path(code6)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        if not parse_frontmatter(text).get("initiated"):
            return ""
        block = _summary_block(text)
        if not block or est_tokens(block) > SUMMARY_CAP:
            return ""
        return block
    except Exception:  # noqa: BLE001 — 坏档=不可注入,不抛
        return ""


def lint_dossier(text: str, cap: int = SUMMARY_CAP) -> list[str]:
    issues = [f"缺节锚:{s}" for s in SECTIONS if s not in text]
    if SUMMARY_HEAD not in text:
        issues.append(f"缺节锚:{SUMMARY_HEAD}")
        return issues
    block = _summary_block(text)
    if est_tokens(block) > cap:
        issues.append(f"summary>cap({est_tokens(block)}>{cap})")
    issues += [f"摘要缺锚:{a}" for a in SUMMARY_ANCHORS if a not in block]
    return issues


STALE_DAYS = 90     # 档案陈旧告警阈值(spec 风险节:last_refresh 超 90 日 → warn)


def staleness_age(text: str, today: str) -> int | None:
    """`last_refresh`(缺则退 `initiated`)距 `today` 的天数;机器面,不渲染文案。

    I-3(2026-07-24 终审):`prelude` 此前靠 `str.split('距今 ')` 从 `staleness_issues`
    的自然语言输出里反解天数——文案改一个词(「距今」→「已过」)、阈值改一个数
    (`STALE_DAYS` 90→60)都能在反解逻辑不报错的前提下让下游读出垃圾/自相矛盾读数
    (两个变异实测存活)。这里把"天数"独立成单一事实源:`staleness_issues` 内部调它
    拼文案,`prelude` 直接调它 + `STALE_DAYS` 拼消息,不再有第二份反解逻辑。

    两个日期都空 → None(骨架未首覆,归 pending_init 管);`ref` 存在但格式畸形(如手误
    漏了横杠)→ None(由 `staleness_issues` 转成可留痕的「档案日期畸形」issue,I-1)。
    `today` 畸形**不在此吞**——它是全池共享输入,一旦悄悄返回 None 会让整池探针系统性
    失聪却看起来"全新鲜"(I-1,比抛异常更危险),交调用方决定是否兜底
    (`prelude` 已有 `contextlib.suppress`)。
    """
    from datetime import date as _date
    meta = parse_frontmatter(text)
    ref = meta.get("last_refresh") or meta.get("initiated")
    if not ref:
        return None
    ty, tm, td = (int(x) for x in str(today).split("-"))   # today 畸形 → 直接抛,不 catch
    today_d = _date(ty, tm, td)
    try:
        y, m, d = (int(x) for x in str(ref).split("-"))
        return (today_d - _date(y, m, d)).days
    except Exception:  # noqa: BLE001 — 档案侧日期畸形(today 已验证合法)→ None
        return None


def staleness_issues(text: str, today: str, *, cap_days: int = STALE_DAYS) -> list[str]:
    """档案陈旧度探针:`last_refresh`(缺则退 `initiated`)距 today 超 cap_days → 一条 issue。

    与 `lint_dossier`(结构契约)分开:结构对但内容陈旧是另一类病,且需要"今天"这个
    外部输入才能判——不塞进纯结构 lint(规模检查与结构检查分开,repo 既有惯例)。
    两个日期都空 = 骨架未首覆,归 pending_init 管,不在此报。

    天数由 `staleness_age` 机算(单一事实源,见其 docstring;I-3)。`ref` 存在但格式
    畸形 → 返回一条「档案日期畸形」issue,不静默吞成 `[]`(I-1,2026-07-24 终审:此前
    的 except 把"格式畸形"和"两日期皆空"混成同一句 `return []`,而 `lint_dossier` 对
    frontmatter 日期**零校验**——两边都不报 = 降级不留痕,`reconcile.main --today` 一次
    手误就能把畸形日期写进档案且此后 1.5 年都不再告警)。`today` 本身畸形不在此吞,
    经 `staleness_age` 原样抛出,不伪装成"档案新鲜"。
    """
    meta = parse_frontmatter(text)
    has_refresh = bool(meta.get("last_refresh"))
    ref = meta.get("last_refresh") or meta.get("initiated")
    if not ref:
        return []
    age = staleness_age(text, today)
    if age is None:
        field = "last_refresh" if has_refresh else "initiated"
        return [f"档案日期畸形:{field}='{ref}' 无法解析"]
    if age <= cap_days:
        return []
    if has_refresh:
        return [f"档案陈旧:last_refresh {ref} 距今 {age} 日(>{cap_days})"]
    # last_refresh 未设、退回 initiated 计龄(m-1,2026-07-24 终审):措辞须如实指名
    # initiated——生产首批 4/4 真档案 last_refresh 皆 null,全部走这条回退路径,若消息
    # 仍写「last_refresh {ref}」会指着一个从未被写过的字段读数,误导排障。
    return [f"档案陈旧:initiated {ref} 距今 {age} 日(>{cap_days};last_refresh 未设)"]
