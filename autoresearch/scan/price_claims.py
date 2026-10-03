"""价格断言对账(确定性,零 LLM;spec 2026-07-22 dossier design ⑤-2)。

卡片/情报里的价格类断言(某日 涨X% / 涨停)与 lake OHLCV 对账——pr_20260714_006
(intel 捏造涨停)的机制化根治。精度优先:只认领**句内出现本票名称/代码/本股指代**的断言
(防把"科创50 +10%"记到个股头上);缺 bar 的日期跳过(nodata 不算失败)。advisory 用途,
一切失败路径返回空,绝不抛异常。

**语义边界(2026-07-23 终审 C-1 收紧 + round2 复核 R-1 补,四类真卡假阳):抽取器只认领
「某日已实现的单日股价移动」。**四类被排除(否则对账器会对读者输出「分析师捏造股价」的自信
误指控):
  1. 区间/累计移动(`A日→B日 涨X%` 或「累计涨幅达 X%」):日期与 % 之间出现区间标记
     (→/至/到/从/累计)⇒ 弃(华海 600521:`本股 6/09 14.64→7/16 17.63(+20%)` 是累计
     +20%,非 6/09 单日;R-1:`7-16 累计涨幅达 20%` 同族,不靠箭头,靠「累计」一词)。
  2. 前向情景/目标 %:% 前的局部窗出现情景/目标语境(Bull/Base/Bear/情景/目标/EV/延续至/
     看至/上望/上行/下行/p60/R:R)⇒ 弃该 %(普冉 688766 同句里 `延续至 510(+8.5%)` 弃、
     `7/21 单日已实测 +17.5%` 留——按每个 % 匹配点的局部语境判定,非整句一刀切)。
  3. 基本面/非股价 %:% 前后 8 字内出现基本面名词(营收/收入/净利/利润/毛利/EPS/业绩/订单/
     产能/份额/同比/环比/预告/预增/预盈/中报/年报/季报/归母)⇒ 弃(协创 300857:
     `营收同比上涨12%` 是营收%非股价%;R-1:`中报预告 +66%`/`中报预增 +105%` 是业绩
     预测%非股价%,窗内「中报/预告/预增」任一命中即弃)。
  4. 百分区间 `X%~Y%`(R-1):两个 % 由 `~` 相连 ⇒ 整段判定为预测区间,两端都弃(协创
     `中报预告 +247%~+340%`——右端离基本面名词已超 8 字窗,单靠类 3 抓不住,须整段识别)。

**已知简化(终审遗留#1):每句只取首个可认领断言(under-report,非假阳)——上面四类先滤掉
非价格 %,再取首个存活的 % 当断言;同句更靠后的第二条已实现移动会漏抽。**

**Wave10 A3(2026-08-01)把默认翻了过来**:上面那套「默认认领 + 列举排除」的架构,词表漏
一个词就多一条对读者说「分析师捏造股价」的自信误指控 —— 而词表永远补不完(具体指数名从
科创50 补到创指、再到存储指数)。现在改成**先定主语**:每个 % 由 `subject_of_pct` 判出
一个 `SUBJECTS` 里的主语,**只有 `own_stock_price` 进对账**;判不出主语记 `UNKNOWN_SUBJECT`
计量(不告警、不静默丢)。上面四类排除全部保留,成为主语判定的前置规则。详见 `SUBJECTS`
下方的立案注。
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.common import workspace as ws

# ── Wave10 A3(2026-08-01):先定主语,再抽数 ────────────────────────────────
#
# 立案现场(07-30/31 三张真卡,三个**不同**缺陷,不是一个):
#   · 000651 `主力净额…8.04 亿/占比 +3.7%`  → 主语是**资金占比**,被判成本票股价;
#   · 688766 `A股存储指数 +6.6%`           → 主语是**板块指数**;`存储指数` 不在市场指数
#                                            黑名单里,而黑名单永远补不完具体指数名;
#   · 600535 `(vs 7/29 收 15.95,-0.44%)`  → 主语**确实**是本票股价,但 `7/29` 是**基准日**
#                                            不是主语日 —— −0.44% 说的是 7/30 的移动。
#
# 前两类的共性:此前的架构是「默认认领,列举排除」—— 词表漏一个词就多一条对读者说
# 「分析师捏造股价」的自信误指控。本波把默认翻过来:**只有拿到本票股价的正面证据才认领**,
# 拿不到就记 `UNKNOWN_SUBJECT` 计量(不告警、不静默丢),让漏抽变成一个**看得见的数**。
#
# 判定方式是**就近**而不是"左窗里有没有这个词"(见 `_nearest_subject` 的注):
# 「板块普涨,本股涨 3%」里 `本股` 比 `板块` 近,那 3% 就是本股的。
#
# 收紧类改动必须同时量误排除率:本波在 542 张历史卡上做了双版回放 ——
# 认领口径一致 24→31(找回 7 条此前被词表吞掉的真阳)、新增认领 0、不再认领 10 条
# 且逐条人工判为真假阳(主力占比 / 概念板块资金 / 存储指数 / 基准日错配)。

SUBJECTS = (
    "own_stock_price",          # 唯一进对账的主语
    "ratio_share",              # 占比/净比/仓位/持股/换手/市占
    "fund_flow",                # 主力净额/净流入/融资余额…
    "index",                    # 指数(含板块/行业指数)/板块/行业整体
    "peer_or_other_stock",      # 他票(「4 家涨停」)
    "fundamental",              # 营收/净利/业绩预告…
    "forward_scenario",         # Bull/目标/看至/预告区间
    "cumulative_move",          # 区间/累计移动
    "baseline_reference",       # vs/较 某日 —— 该日是基准不是主语日
    "quoted_or_refuted",        # 转述/否决/负判
    "implausible_daily_move",   # 单日物理不可能
    "parse_artifact",           # 日期与 % 抢同段字符
    "UNKNOWN_SUBJECT",          # 定不出主语:不认领,但计量
)
_CLAIMED_SUBJECT = "own_stock_price"


@dataclass
class ClaimAudit:
    """一张卡的抽取结果 + 主语分布。`counts` 让漏抽变成看得见的数,而不是静默。"""
    claims: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    unknown_snippets: list[str] = field(default_factory=list)

    @property
    def n_candidate(self) -> int:
        return sum(self.counts.values())

    @property
    def n_own(self) -> int:
        return self.counts.get(_CLAIMED_SUBJECT, 0)

    @property
    def n_unknown(self) -> int:
        return self.counts.get("UNKNOWN_SUBJECT", 0)

    @property
    def n_excluded(self) -> int:
        return self.n_candidate - self.n_own - self.n_unknown

    @property
    def unknown_rate(self) -> float | None:
        return None if not self.n_candidate else round(
            self.n_unknown / self.n_candidate, 4)

    def summary(self) -> dict:
        return {"n_candidate": self.n_candidate, "n_own": self.n_own,
                "n_excluded": self.n_excluded, "n_unknown": self.n_unknown,
                "unknown_rate": self.unknown_rate, "subjects": dict(self.counts)}


_SENT_SPLIT = re.compile(r"[。;;\n]")
# ── Wave7 B′-b(2026-07-27 实锤,4/4 假阳):引用/否决/负判句整句不认领 ──
#
# 本探针读的是**卡片正文**,而卡片正文里出现行情词的最常见场景恰恰不是自陈,而是三种
# 「元话语」——转述、否决、否定。07-27 的四条 warn 100% 落在这三种上,其中两条卡片正
# 在做我们要它做的事:
#   · 600988:「〔转引标题〕intel 载"…赤峰黄金跌停"——slim OHLCV 显示 7/17 为 -8.3%,
#     **非跌停,intel 该价格断言未对账**」= 卡片自己拿 OHLCV 否决了 intel,却被判成捏造;
#   · 601211:「intel 称「07-27 证券板块 +4.82%…」与本股 verified -1.4% **未对账**,不采信」;
#   · 601869:〔转引标题〕新闻标题里的日期 + 同句 fwd-EPS「隐含 +637% 跃升」(见 _FUND);
#   · 601918:「兖矿/中煤涨停时『新集能源**未见于**当日龙头名单』」= 涨停属他票,句意恰是本股没涨停。
# 真捏造在 intel 稿里(由 assemble 发布层的 🔎 块另行对账,该层工作正常),不在卡片正文。
# 对假阳放任的代价不是多几行 warn,而是 P0 探针的公信力被磨掉(狼来了),之后真捏造也没人看。
# 方向选择:整句跳过 = under-report,与本模块既有取舍一致(见文件头「已知简化」)——
# 宁可漏报也不对着一张正在做对账的卡输出「分析师捏造股价」的自信误指控。
_QUOTE_OR_REFUTE = (
    "转引标题", "非本票行情自陈", "非本票行情断言", "intel 称",   # 转述:引号里的别人的话
    "他票数据", "非本票行情",                                  # 转述(泛化前缀)
    "未对账", "对账", "不采信", "未采信", "该价格断言",           # 否决:卡片已判它不可信
    "非涨停", "非跌停", "未见于", "未涨停", "未跌停",            # 负判:句意是「没发生」
)
# ⚠️ Wave10 A3 试过把 `转引` 也收成**整句**裸词(卡里写的是 `〔情报站转引 财联社〕`,
# 词表只有 `转引标题`)—— 全语料回放证明代价太大:601288 那句
# 「本票今日 +3.57%(已核·verified OHLCV)对照【网查·〔转引〕】银行指数当日 +1.43%」
# 里,**本票自陈和转引的指数行情在同一句**,整句豁免会把真自陈一起扔掉。
# 所以转引类改走**逐 % 的就近判定**(见 `_QUOTE_LOCAL`):谁离这个数字更近,主语就是谁。
# ⚠️ 2026-07-29(W8-14)加的四条,都是**词形差一点就漏网**的实例:
#   卡里写 `非本票行情断言`,词表只有 `非本票行情自陈`;
#   卡里写 `未与本票 OHLCV 对账`,词表只有 `未对账`(中间插了字,子串匹配不上)。
# 于是 601288 那张**正在做对账并声明不采信**的卡,被判成自己捏造股价(07-28 实况)。
# 词表匹配的是词形、人写的是意思 —— 所以 `对账` 收成裸词(任何"对账"语境都豁免),
# `非本票行情` 收成前缀。方向明确:豁免侧宁可放宽(under-report),指控侧宁可收紧;
# 对一张正在对账的卡喊「捏造股价」的代价是把 P0 探针的公信力磨掉,之后真捏造也没人看。


def _is_quote_or_refutation(sent: str) -> bool:
    """整句属转述/否决/负判 → 句内行情词不是本卡的事实断言,不认领。"""
    return any(m in sent for m in _QUOTE_OR_REFUTE)
# 日期:2026-07-21 / 07-21 / 7/21 / 7月21日(可带年)
_DATE = re.compile(r"(?:(20\d{2})[-/年])?(\d{1,2})[-/月](\d{1,2})日?")
_PCT = re.compile(r"(?P<verb>上涨|大涨|涨|下跌|大跌|跌|涨幅|跌幅)[^%。;;\n]{0,12}?(?P<num>[+-]?\d+(?:\.\d+)?)\s*%"
                  r"|(?P<num2>[+-]\d+(?:\.\d+)?)\s*%")
_LIMIT = re.compile(r"涨停|跌停")
_SELF_MARKS = ("本股", "个股", "该股", "本票")
_UP_VERBS = ("上涨", "大涨", "涨", "涨幅")
_DOWN_VERBS = ("下跌", "大跌", "跌", "跌幅")

# ── C-1 收紧(2026-07-23):只认「某日已实现单日股价移动」的三类排除词表 ──
# (a) 区间/累计标记:出现在日期与 % 之间 → 「A日→B日 累计涨X%」非单日(华海 6/09→7/16 +20%);
#     「累计」本身也算(R-1:「7-16 累计涨幅达 20%」不靠箭头,靠这个词)
_RANGE_MARKS = ("→", "至", "到", "从", "累计")
# (b) 情景/目标语境(% 之前的局部窗)→ 前向情景 EV/目标价,非已实现(普冉 Bull 延续至510 +8.5%)
_SCENARIO = ("Bull", "Base", "Bear", "bull", "base", "bear", "情景", "目标", "EV",
             "延续至", "看至", "上望", "上行", "下行", "p60", "R:R", "R：R")
# (c) 基本面/非股价 %(% 前后 8 字内)→ 营收/净利/同比 涨,非股价涨(协创 营收同比 +12%)
#     (毛利率被「毛利」覆盖,不单列;R-1 补业绩预告类:预告/预增/预盈/中报/年报/季报/归母
#     ——「中报预告 +66%」是业绩预测%非股价%,同族)
_FUND = ("营收", "收入", "净利", "利润", "毛利", "EPS", "业绩", "订单",
         "产能", "份额", "同比", "环比",
         "预告", "预增", "预盈", "中报", "年报", "季报", "归母",
         # Wave7 B′-b:一致预期派生量(长飞 601869「fwd-EPS 7.81 对 TTM 实际 EPS 1.06
         # = 隐含 +637% 跃升」——"EPS" 离数字已超 8 字窗,靠 "隐含" 这一跳才抓得住)。
         # 与 _QUOTE_OR_REFUTE 是两道独立防线:该句同时带〔转引标题〕,任一道都能拦。
         "隐含", "一致预期", "预期差", "fwd")
_CTX_BACK = 14      # 情景语境向前看的字符窗(延续至/目标/看至 通常紧邻 % 之前)
_FUND_WIN = 8       # 基本面名词判定窗(brief:% 前后 8 字内)
# W2-T1 复审验证指数黑名单修复时顺带隔离出的第二处遮蔽 bug(与 _INDEX_NAMES 碰撞同族不同因):
# 「数字之后 8 字」原样跨逗号,会把下一个不相关小句的基本面词(如"系统集成订单加速"的"订单")
# 误判成给本句价格 % 定性 → 静默吞真实断言。数字之前的窗不受影响(基本面修饰语通常紧邻 %
# 之前,C-1/R-1 全部既有用例都是这个方向);只收紧"之后"这一侧,止于最近的小句分隔符。
_FUND_CLAUSE_BREAK = re.compile(r"[，,、]")
# (d) 百分区间 `X%~Y%`(R-1,协创 `中报预告 +247%~+340%`):两个 % 由 `~` 相连 ⇒ 整段
#     判定为预测区间,两端都弃(右端常离基本面名词超 8 字窗,类 c 单独抓不住)
_PCT_TILDE_RANGE = re.compile(r"[+-]?\d+(?:\.\d+)?%\s*~\s*[+-]?\d+(?:\.\d+)?%")

# ── round3 立项(2026-07-23):指数名黑名单——句级自指(个股/本股等)夹带指数名时,
#    指数自己的涨跌% 不该被记到本票头上(协创 07-21 真卡:句含"个股"但 +10% 是科创50 涨幅)──
_INDEX_NAMES = ("科创50", "沪深300", "上证指数", "上证综指", "深证成指", "深成指",
                "创业板指", "北证50", "中证500", "中证1000", "恒生指数", "恒生科技",
                "纳指", "标普",
                # 简称(2026-07-29 W8-14):真实稿件用的就是简称 —— 07-28 农业银行卡里
                # 写的是「创指当日跌7.35%」,而词表只有「创业板指」,左邻窗认不出,
                # 于是那 −7.35% 被认领成农行自己的断言。同族补齐:沪指/深指/科创综指。
                "创指", "沪指", "深指", "科创综指", "科创板指")


# ── Wave10 A3:别人的主语(词表 + 一道「更近的本票标记优先」保护)──────────────
_RATIO = ("占比", "净比", "比例", "仓位", "持股", "换手", "市占", "流通盘", "权重")
_FUND_FLOW = ("主力", "净流入", "净流出", "净额", "北向", "融资余额", "融券",
              "龙虎榜", "小单", "大单", "中单", "散户", "资金净")
# 具体指数名永远补不完(科创50→创指→科创综指→存储指数…),所以再加一层**结构词**:
# 「…指数 +X%」「…板块 +X%」「…行业 +X%」的主语一律不是本票。
_INDEX_GENERIC = ("指数", "板块", "行业", "梯队", "同业")
# 「4 家涨停」「3 只涨停」= 别的票的行情
_PEER_MARKS = ("家涨停", "家跌停", "只涨停", "只跌停", "等涨停", "等跌停")
# 逐 % 的转引判定(整句豁免代价太大,见 `_QUOTE_OR_REFUTE` 下的注)
_QUOTE_LOCAL = ("转引", "转述", "据报", "网查", "intel 载", "intel 称")
# `vs 7/29 收 15.95,-0.44%` —— 该日期是**基准**不是主语日(600535 07-30 实锤)
_BASELINE_MARKS = ("vs", "VS", "较", "相对", "对比")
_BASELINE_BACK = 5      # 基准标记紧邻日期之前的窗
# `个股次日 -6%` —— 主语日是绝对日期的次日,不是它本身
_RELATIVE_DATE_MARKS = ("次日", "翌日", "隔日", "前一日", "上一日", "前一个交易日")
# 本票股价的**正面证据**:价格动作 / 已实现标记 / 本票指代
_PRICE_EVIDENCE = ("上涨", "大涨", "涨", "下跌", "大跌", "跌", "涨幅", "跌幅",
                   "涨停", "跌停", "高开", "低开", "跳空", "冲高", "冲低",
                   "反抽", "反弹", "回吐", "收涨", "收跌", "收于", "收报",
                   "报收", "后收", "收在", "收盘", "单日", "已实测", "实测",
                   "实读", "verified", "OHLCV")
_SCAN_BACK = 24         # 就近判定向前扫的字符窗
_SCAN_FWD = 12          # 向后扫的字符窗(证据也可能在数字之后:「+16.4% 反抽」)
_AFTER_PENALTY = 6      # 数字之后的命中加固定罚距 —— 中文修饰语通常前置


def _alt(words) -> re.Pattern:
    return re.compile("|".join(re.escape(w) for w in words if w))


def _subject_patterns(name: str, code6: str) -> tuple[tuple[re.Pattern, str], ...]:
    """就近判定用的 (模式, 主语) 表。本票指代含卡片自己的名称/代码。"""
    own = [*_SELF_MARKS, *_PRICE_EVIDENCE, *(t for t in (name, code6) if t)]
    return (
        (_alt(_RATIO), "ratio_share"),
        (_alt(_FUND_FLOW), "fund_flow"),
        (_alt(_INDEX_GENERIC), "index"),
        (_alt(_PEER_MARKS), "peer_or_other_stock"),
        (_alt(_QUOTE_LOCAL), "quoted_or_refuted"),
        (_alt(own), _CLAIMED_SUBJECT),
    )


def _nearest_subject(sent: str, npos: int, name: str, code6: str) -> str | None:
    """**离这个数字最近的主语标记胜出**。够不到任何标记 → None(= UNKNOWN_SUBJECT)。

    为什么不是"左窗里有没有某个词":固定窗口太脆,同一波回放里两种方向都踩到了 ——
      · 证据在更左边:`…冲高 422.37 后收 389.64(**-4.36%**)` 的 `后收` 落在 12 字窗外;
      · 证据在右边:  `那根 +16.4% 反抽后仍创新低` 的 `反抽` 根本在数字之后。
    调窗口宽度是在两类错误之间来回搬运,而不是消除它们。就近判定直接问对了问题:
    「这个百分号,离谁最近就是在说谁」——「板块普涨,本股涨 3%」里 `本股` 比 `板块` 近,
    「A股存储指数 +6.6%」里 `指数` 比任何本票指代都近。

    数字**之前**的标记优先(中文修饰语通常前置),故对数字之后的命中加一个固定罚距。
    """
    best: tuple[int, str] | None = None
    window = sent[max(0, npos - _SCAN_BACK):min(len(sent), npos + _SCAN_FWD)]
    offset = npos - max(0, npos - _SCAN_BACK)
    for pattern, subject in _subject_patterns(name, code6):
        for m in pattern.finditer(window):
            if m.end() <= offset:
                distance = offset - m.end()
            else:
                distance = m.start() - offset + _AFTER_PENALTY
            if best is None or distance < best[0]:
                best = (distance, subject)
    return None if best is None else best[1]


def _is_baseline_date(sent: str, dm: re.Match) -> bool:
    """`vs 7/29 收 …` —— 日期前紧邻基准标记 ⇒ 它是参照系,不是断言的主语日。"""
    return any(mark in sent[max(0, dm.start() - _BASELINE_BACK):dm.start()]
               for mark in _BASELINE_MARKS)


def _is_relative_date_claim(sent: str, npos: int) -> bool:
    """`个股次日 -6% 回吐` —— 主语日是"某绝对日期的次日",不是那个绝对日期本身。

    主语判对了、日期判错了,对账照样输出「捏造股价」的误指控 —— 所以宁可不认领。
    """
    return any(mark in sent[max(0, npos - _SCAN_BACK):npos]
               for mark in _RELATIVE_DATE_MARKS)


def _near_index_name(sent: str, pct_pos: int, name: str = "", window: int = 13) -> bool:
    """% 候选左邻 window 字内出现指数名 → 该 % 属指数,不认领给本票。

    W2-T1 复审 Important 修(2026-07-23):指数名黑名单会与本票名字发生子串碰撞——例如裸
    `"恒生"`(原意指恒生指数/恒生科技)本身就是**恒生电子**(600570.SH)股票名的前缀,导致该股
    每一条真实价格断言都被误判成"指数涨跌"而静默吞掉。除了把最短的碰撞条目改成限定形
    (`"恒生"` → `"恒生指数"`/`"恒生科技"`),这里再加一道通用防线:左窗命中的指数名如果本身是
    本票 `name` 的子串(且 `name` 非空),不算指数命中——不管黑名单未来加了什么新词条,都不会
    反噬名字里恰好包含该词的股票自己。

    window 12→14(本次复审顺带的第三处窄修,与上面两条不同因):复审自带的回归向量
    `"本股随恒生指数 7-21 上涨 2.1%。"`里"恒生指数"距数字 13 字,窗=12 会整词切在"恒"字
    之后、漏判该 % 真是指数的(不涉及 name 碰撞,纯粹是"指数名结束到数字"这段本身就有 13
    字)。+2 只补这一刀之窄,不是复审 Minor 项(22 字远距长句)的修复——那条更大的取舍
    (窗口越宽越可能倒吞同句真实个股断言)复审已明确记为"非本轮阻塞项",本次不动,留给未来
    有专门取舍设计的一轮。"""
    left = sent[max(0, pct_pos - window):pct_pos]
    return any(ix in left and not (name and ix in name) for ix in _INDEX_NAMES)


def _own_sentence(sent: str, name: str, code6: str) -> bool:
    if name and name in sent:
        return True
    if code6 and code6 in sent:
        return True
    return any(m in sent for m in _SELF_MARKS)


def _fmt_date(m: re.Match, year_hint: int) -> str:
    y = int(m.group(1) or year_hint)
    return f"{y:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"


def _pct_value(pm: re.Match) -> float:
    """verb 缺席(纯 +/- 字面量分支)→ 保留字面正负;verb 在场且数字无字面符号 → 按动词方向定号
    (下跌/大跌/跌/跌幅 → 负,上涨/大涨/涨/涨幅 → 正);字面 +/- 优先于动词方向。"""
    verb = pm.group("verb")
    if not verb:
        return float(pm.group("num2"))
    raw = pm.group("num")
    val = float(raw)
    if raw[0] in "+-":
        return val
    return -val if verb in _DOWN_VERBS else val


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _num_pos(pm: re.Match) -> int:
    """% 匹配里「数字」的起始位——语境检查全锚在这里,不锚在 match.start():verb 支路(如"超跌…
    延续至 510(**+8.5")会从很靠左的动词起匹配,窗口锚 match.start 会漏掉数字前的情景/区间标记。"""
    return pm.start("num") if pm.group("num") is not None else pm.start("num2")


def _fund_after_end(sent: str, end: int, width: int = _FUND_WIN) -> int:
    """基本面判定窗「数字之后」一侧的右边界:原样右探 width 字,但遇小句分隔符(，/,/、)即止
    ——不跨小句,防止逗号后不相关小句里的基本面词(如"系统集成订单加速"的"订单")误伤本句
    价格 %(恒生电子案例:验证 W2-T1 指数黑名单修复时隔离出的第二处遮蔽 bug)。"""
    limit = min(len(sent), end + width)
    m = _FUND_CLAUSE_BREAK.search(sent, end, limit)
    return m.start() if m else limit


def _range_left(sent: str, dates: list[re.Match], dm: re.Match, npos: int, gap: int = 16) -> int:
    """从最近在前日期 dm 向前吸收「紧邻(≤gap 字)的更早日期」成日期簇(`6/09 14.64→7/16` = 一个
    区间簇),返回簇最左日期的 start——区间标记须从这里查到数字,才能逮到落在两日期之间的 →/至;
    gap 卡死簇宽度:相隔很远的两个同名日期(普冉句里两个 7/21 相隔 ~100 字)不会被误并成区间。"""
    idx = dates.index(dm)
    start = dm.start()
    i = idx - 1
    while i >= 0 and dates[i].end() <= npos and start - dates[i].end() <= gap:
        start = dates[i].start()
        i -= 1
    return start


def subject_of_pct(sent: str, dm: re.Match, pm: re.Match, dates: list[re.Match],
                   *, name: str = "", code6: str = "") -> str:
    """这个 % 的**主语**是什么。`own_stock_price` 是唯一进对账的一类(Wave10 A3)。

    顺序即优先级:先把"确定属于别人/别的量纲"的排掉,最后才问"有没有本票股价的正面证据"
    —— 没有证据不等于是本票的,那叫 `UNKNOWN_SUBJECT`,计量但不认领。
    """
    if not _is_realized_price_pct(sent, dm, pm, dates, name=name):
        return _rejection_subject(sent, dm, pm, dates, name=name)
    npos = _num_pos(pm)
    if _is_baseline_date(sent, dm) or _is_relative_date_claim(sent, npos):
        return "baseline_reference"
    subject = _nearest_subject(sent, npos, name, code6)
    if subject is None:
        return "UNKNOWN_SUBJECT"
    if subject != _CLAIMED_SUBJECT:
        return subject
    if abs(_pct_value(pm)) > _MAX_DAILY_MOVE_PCT:
        return "implausible_daily_move"
    return _CLAIMED_SUBJECT


def _rejection_subject(sent: str, dm: re.Match, pm: re.Match,
                       dates: list[re.Match], name: str = "") -> str:
    """`_is_realized_price_pct` 说不是 → 复算一遍它是**因为哪一类**被排掉的(只为计量)。"""
    if _overlaps(dm.span(), pm.span()):
        return "parse_artifact"
    if any(_overlaps(pm.span(), rm.span()) for rm in _PCT_TILDE_RANGE.finditer(sent)):
        return "forward_scenario"
    npos = _num_pos(pm)
    if any(r in sent[_range_left(sent, dates, dm, npos):npos] for r in _RANGE_MARKS):
        return "cumulative_move"
    if any(k in sent[max(0, npos - _CTX_BACK):npos] for k in _SCENARIO):
        return "forward_scenario"
    if _near_index_name(sent, npos, name=name):
        return "index"
    return "fundamental"


def _is_realized_price_pct(sent: str, dm: re.Match, pm: re.Match, dates: list[re.Match],
                            name: str = "") -> bool:
    """pm(% 匹配)配 dm(其最近在前日期)是否为一条「已实现单日股价移动」。六类排除:
      · 区间幻影(如"5-10%"):_DATE 把 "5-10" 当日期、_PCT 把 "-10%" 当字面负号,两匹配抢同段字符;
      · 百分区间 `X%~Y%`(R-1):该数字与另一个 % 由 `~` 相连 → 预测区间,两端都非单日已实现;
      · 区间标记(→/至/到/从/累计)落在日期簇与数字之间 → 累计/区间移动非单日(华海
        6/09→7/16 +20%;R-1:「累计涨幅达 20%」同族,靠词不靠箭头);
      · 数字之前局部窗有情景/目标语境 → 前向情景 EV/目标价(普冉 延续至510 +8.5%);
      · 数字左邻 14 字内有指数名(round3;W2-T1 复审 Important 修:该指数名若是本票 `name` 的
        子串则不算,防黑名单反噬同名股;窗 12→14 见 `_near_index_name` 注)→ 该 % 属指数非
        本票,不认领(协创 07-21:句含"个股"但 +10% 是科创50 涨幅);
      · 数字前 8 字/数字后 8 字(止于最近小句分隔符,防跨句误伤)内有基本面名词(含 R-1 补的
        预告/预增/预盈/中报/年报/季报/归母)→ 营收/净利/业绩预告% 非股价%(协创 营收同比
        +12%、中报预告 +66%;恒生电子 放量上涨11.4%,系统集成订单加速——"订单"落在逗号后的
        下一小句,不该反噬前一小句的真实价格 %)。"""
    if _overlaps(dm.span(), pm.span()):
        return False
    if any(_overlaps(pm.span(), rm.span()) for rm in _PCT_TILDE_RANGE.finditer(sent)):
        return False
    npos = _num_pos(pm)
    if any(r in sent[_range_left(sent, dates, dm, npos):npos] for r in _RANGE_MARKS):
        return False
    if any(k in sent[max(0, npos - _CTX_BACK):npos] for k in _SCENARIO):
        return False
    if _near_index_name(sent, npos, name=name):
        return False
    fund_window = sent[max(0, npos - _FUND_WIN):_fund_after_end(sent, pm.end())]
    return not any(k in fund_window for k in _FUND)


_TRAILING_DATE_GAP = 3          # `+5.65%(7/20)` —— 只隔一个开括号
_TRAILING_OPENERS = "(([【<〔"    # 后置日期必须被括起来


def _trailing_date(sent: str, pm: re.Match,
                   dates: list[re.Match]) -> re.Match | None:
    """数字**紧后的括号里**是否跟着它自己的日期(`+5.65%(7/20)`)—— 有则以它为准。

    配对模型默认"最近在前日期",但列举式写法把日期放在后面:
    `近 5 个交易日实测单日振幅 +5.65%(7/20)、+6.38%(7/21)、-9.92%(7/22)`
    —— 三个值会全被挂到句首那个日期上,于是一条真自陈变成三条假指控。

    **必须要求括号**:688766 07-24 卡里写的是 `该股 07-21 单日实际 +17.5%、07-22 -7.45%`
    —— 这里 `+17.5%` 后面跟的 `07-22` 是**下一个值**的日期。不卡括号就会把 07-21 的
    +17.5% 挂到 07-22 头上(实为 −7.45%),修一条假指控的同时造出另一条。
    """
    for dm in dates:
        gap = sent[pm.end():dm.start()]
        if 0 <= len(gap) <= _TRAILING_DATE_GAP and any(
                ch in _TRAILING_OPENERS for ch in gap):
            return dm
    return None


def _first_realized_pct(sent: str, dates: list[re.Match], name: str = "",
                        code6: str = "", tally: dict[str, int] | None = None,
                        unknown: list[str] | None = None):
    """句内首个主语为本票股价的 %。**每个**候选都记进 `tally`(漏抽必须看得见)。

    返回 (带号数值, 配对日期匹配) 或 None(每句只取首个 = 本文件既有的已知简化)。
    """
    hit = None
    for pm in _PCT.finditer(sent):
        prev = [d for d in dates if d.end() <= pm.start()]
        dm = prev[-1] if prev else dates[0]
        subject = subject_of_pct(sent, dm, pm, dates, name=name, code6=code6)
        if tally is not None:
            tally[subject] = tally.get(subject, 0) + 1
        if subject == "UNKNOWN_SUBJECT" and unknown is not None:
            unknown.append(sent.strip()[:80])
        if hit is None and subject == _CLAIMED_SUBJECT:
            hit = (_pct_value(pm), _trailing_date(sent, pm, dates) or dm)
    return hit


def _limit_subject(sent: str, lm: re.Match, name: str, code6: str) -> str:
    """涨停/跌停的主语。「7/30 板块政策日 4 家涨停而本票 −0.44%」里的涨停属别的票。"""
    peer = sent[max(0, lm.start() - 4):lm.end()]     # 「4 家涨停」的量词紧贴涨停
    if any(mark in peer for mark in _PEER_MARKS):
        return "peer_or_other_stock"
    # 涨停/跌停自己就在价格证据词表里,不遮掉的话它离自己永远最近 → 就近判定在本分支
    # 形同虚设(变异测试逮到:把他票规则整个删掉,测试依旧全绿)。
    masked = sent[:lm.start()] + " " * (lm.end() - lm.start()) + sent[lm.end():]
    subject = _nearest_subject(masked, lm.start(), name, code6)
    # 涨停/跌停本身就是股价事件,句子已过 `_own_sentence` 与转述豁免 →
    # 够不到任何标记时保持既有认领口径(不把已有的真阳改成 UNKNOWN)
    return subject or _CLAIMED_SUBJECT


def classify_price_claims(text: str, *, name: str, code6: str,
                          year_hint: int) -> ClaimAudit:
    """抽取 + 主语分布。`extract_price_claims` 是它的薄封装(保持既有调用签名)。"""
    audit = ClaimAudit()
    for sent in _SENT_SPLIT.split(text or ""):
        if not sent.strip() or not _own_sentence(sent, name, code6):
            continue
        dates = list(_DATE.finditer(sent))
        if not dates:
            continue
        if _is_quote_or_refutation(sent):        # Wave7 B′-b:转述/否决/负判句不认领
            n_pct = sum(1 for _ in _PCT.finditer(sent)) or (
                1 if _LIMIT.search(sent) else 0)
            if n_pct:
                audit.counts["quoted_or_refuted"] = audit.counts.get(
                    "quoted_or_refuted", 0) + n_pct
            continue
        hit = _first_realized_pct(sent, dates, name=name, code6=code6,
                                  tally=audit.counts, unknown=audit.unknown_snippets)
        if hit is not None:
            val, dm = hit
            audit.claims.append({"date": _fmt_date(dm, year_hint), "kind": "pct",
                                 "value": val, "snippet": sent.strip()[:60]})
            continue
        lm = _LIMIT.search(sent)
        if lm:
            subject = _limit_subject(sent, lm, name, code6)
            audit.counts[subject] = audit.counts.get(subject, 0) + 1
            if subject == _CLAIMED_SUBJECT:
                audit.claims.append({"date": _fmt_date(dates[0], year_hint),
                                     "kind": "limit", "value": None,
                                     "dir": 1 if lm.group() == "涨停" else -1,
                                     "snippet": sent.strip()[:60]})
    return audit


def extract_price_claims(text: str, *, name: str, code6: str, year_hint: int) -> list[dict]:
    return classify_price_claims(
        text, name=name, code6=code6, year_hint=year_hint).claims


# A 股单日涨跌幅的物理上限:主板 10cm、双创 20cm、北交所 30cm。留足余量取 32 ——
# 任何号称"某日涨跌 X%"而 |X| > 32 的数,**物理上不可能是单日行情**,只能是别的东西
# (利润增速 / 区间累计涨幅 / 估值倍数 / 别家公司的数)。
#
# 2026-07-29(W8-14)加这道闸的实例:688766 普冉卡里
#   「〔网查〕**兆易创新** H1 **+1099%** 却自 6/29 高点回撤超 50%」
# —— 另一家公司的**中报利润增速**,被认领成普冉 06-29 的**股价**断言并报 mismatch。
# 词表(公司名/科目名)永远补不完,而"单日不可能涨 1099%"是物理事实,不依赖措辞。
_MAX_DAILY_MOVE_PCT = 32.0


def _limit_floor(code6: str) -> float:
    # 创业板 300/301、科创板 688/689 = 20cm;其余按 10cm 主板口径(ST 不细分,advisory 容忍)
    # 已知简化:北交所(43/83/87/92 开头,30% 板)未细分,按 9.5 处理(advisory 容忍,非结算口径)
    return 19.0 if code6.startswith(("30", "68")) else 9.5


def reconcile_claims(claims: list[dict], bars: dict[str, float], *,
                     code6: str, tol_pp: float | None = None) -> list[dict]:
    """不符断言列表;**同一 (日期, 类型, 声称值) 只报一次**(Wave7 B′-b)。

    同一条断言在一张卡里被复述多遍(摘要段 + 证据段 + 附录)时,逐条计数会让「3 条不符」
    读起来像三次独立捏造,实际是一次 —— 计数本身就是读者判断严重性的依据,不能虚高。
    """
    if tol_pp is None:
        from autoresearch.scan.observability import observability_cfg
        tol_pp = observability_cfg()["price_claim_tol_pp"]
    bad: list[dict] = []
    seen: set[tuple] = set()
    for c in claims:
        key = (c.get("date"), c.get("kind"), c.get("value"), c.get("dir"))
        if key in seen:
            continue
        seen.add(key)
        actual = bars.get(c["date"])
        if actual is None:                      # nodata:非交易日/湖缺 → 跳过,不算失败
            continue
        actual = float(actual)
        if c["kind"] == "pct":
            if abs(float(c["value"]) - actual) > tol_pp:          # 有号对账,不再抹方向
                bad.append({**c, "claimed": float(c["value"]), "actual": round(actual, 2)})
        elif c["kind"] == "limit":
            d = c.get("dir")
            if d == 1:                                            # 涨停:实涨须 >= floor
                mismatch = actual < _limit_floor(code6)
            elif d == -1:                                         # 跌停:实跌须 <= -floor
                mismatch = actual > -_limit_floor(code6)
            else:                                                 # 无 dir(手搭 dict,旧契约)→ 幅度口径不辨涨跌停
                mismatch = abs(actual) < _limit_floor(code6)
            if mismatch:
                bad.append({**c, "claimed": None, "actual": round(actual, 2)})
    return bad


def bars_for(code6: str, dates: list[str], today: str) -> dict[str, float]:
    """按日整市场 daily(湖命中为主,universe 已预热)→ 过滤本票 pct_chg。失败 → {}。"""
    dates = dates or []
    out: dict[str, float] = {}
    for dd in sorted(set(dates)):
        with contextlib.suppress(Exception):
            from autoresearch.data.cache import get_or_fetch
            df = get_or_fetch("daily", {"trade_date": dd}, today=today)
            if df is None or not len(df) or "ts_code" not in df.columns:
                continue
            hit = df[df["ts_code"].astype(str).str.startswith(code6)]
            if len(hit):
                out[dd] = float(hit.iloc[0]["pct_chg"])
    return out


def _empty_audit() -> dict:
    return {"n_claims": 0, "mismatches": [], **ClaimAudit().summary()}


def audit_card_text(text: str, *, name: str, code6: str, date: str, bars_fn=bars_for) -> dict:
    """对账结果 + 主语分布。返回值多出 `n_candidate/n_own/n_excluded/n_unknown/subjects`
    ——漏抽从此是一个看得见的数,而不是静默(Wave10 A3)。"""
    try:
        year_hint = int(str(date)[:4])
    except (ValueError, TypeError):    # date 空/非数字(advisory 入口,禁止抛异常上溯)
        return _empty_audit()
    audit = classify_price_claims(text or "", name=name, code6=code6,
                                  year_hint=year_hint)
    base = {"n_claims": len(audit.claims), "mismatches": [], **audit.summary()}
    if not audit.claims:
        return base
    bars = bars_fn(code6, [c["date"] for c in audit.claims], date) or {}
    base["mismatches"] = reconcile_claims(audit.claims, bars, code6=code6)
    return base


# ── 主语分布的跨卡聚合与回放(A3 验收:冻结 unknown-rate 基线,live 不得高于 +5pp)──
UNKNOWN_RATE_TOLERANCE_PP = 0.05


def merge_summaries(summaries: list[dict]) -> dict:
    """逐卡 summary → 当日/跨日聚合。分母是候选数,不是卡数。"""
    subjects: dict[str, int] = {}
    for s in summaries:
        for key, value in (s.get("subjects") or {}).items():
            subjects[key] = subjects.get(key, 0) + int(value)
    total = sum(subjects.values())
    unknown = subjects.get("UNKNOWN_SUBJECT", 0)
    own = subjects.get(_CLAIMED_SUBJECT, 0)
    return {"n_cards": len(summaries), "n_candidate": total, "n_own": own,
            "n_excluded": total - own - unknown, "n_unknown": unknown,
            "unknown_rate": round(unknown / total, 4) if total else None,
            "subjects": dict(sorted(subjects.items()))}


def replay(scan_root=None, *, days: int | None = None) -> dict:
    """回放历史卡片 → 跨日主语分布(零网络:只读已落盘的 details/*.md)。"""
    import pandas as pd

    root = Path(scan_root or ws.scan_root())
    if not root.exists():
        return merge_summaries([])
    per_day: dict[str, list[dict]] = {}
    for day in sorted(p for p in root.iterdir() if p.is_dir()):
        details = day / "details"
        if not details.exists():
            continue
        names: dict[str, str] = {}
        with contextlib.suppress(Exception):
            fin = pd.read_csv(day / "finalists.csv", dtype={"code": str})
            names = {str(r.code).split(".")[0].zfill(6):
                     ("" if pd.isna(r.name) else str(r.name)) for r in fin.itertuples()}
        rows = []
        for card in sorted(details.glob("*.md")):
            code = card.stem
            if not code.isdigit():
                continue
            with contextlib.suppress(Exception):
                rows.append(classify_price_claims(
                    card.read_text(encoding="utf-8"), name=names.get(code, ""),
                    code6=code, year_hint=int(day.name[:4])).summary())
        if rows:
            per_day[day.name] = rows
    chosen = sorted(per_day)[-days:] if days else sorted(per_day)
    merged = merge_summaries([r for d in chosen for r in per_day[d]])
    merged["days"] = chosen
    merged["per_day"] = {d: merge_summaries(per_day[d]) for d in chosen}
    return merged


def check_unknown_rate(live: float | None, baseline: float | None) -> tuple[bool, str]:
    """live unknown-rate 是否仍在冻结基线 +5pp 之内。缺任一侧 → 不判(不是"通过")。"""
    if live is None or baseline is None:
        return True, "UNMEASURED(缺 live 或基线,不作判定)"
    ok = live <= baseline + __import__("autoresearch.scan.observability", fromlist=["x"]).observability_cfg()["unknown_rate_tolerance"]
    return ok, (f"live {live:.2%} vs 基线 {baseline:.2%} "
                f"(+{__import__("autoresearch.scan.observability", fromlist=["x"]).observability_cfg()["unknown_rate_tolerance"]:.0%} 容差)→ {'在容差内' if ok else '⚠️ 超出'}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="价格断言主语分布回放(确定性,零网络)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--days", type=int, default=None, help="只回放最近 N 个扫描日")
    ap.add_argument("--freeze", default=None, help="把基线冻结到该路径(进 git 的审计快照)")
    args = ap.parse_args(argv)

    result = replay(args.scan_root, days=args.days)
    print(f"[price_claims] {len(result.get('days') or [])} 日 · {result['n_cards']} 卡 · "
          f"候选 {result['n_candidate']} = 认领 {result['n_own']} + 排除 "
          f"{result['n_excluded']} + 未知 {result['n_unknown']}")
    print(f"  unknown_rate = {result['unknown_rate']}")
    print(f"  主语分布 {json.dumps(result['subjects'], ensure_ascii=False)}")
    if args.freeze:
        target = Path(args.freeze)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        print(f"  → 冻结基线 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
