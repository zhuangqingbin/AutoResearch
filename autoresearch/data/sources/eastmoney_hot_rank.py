#!/usr/bin/env python3
"""东财人气榜(个股人气榜 TOP100)—— **自采第一跳**,绕开被封的 push2。

design: docs/research/2026-08-09-hot-rank-probe.md(2026-08-09 复核后改写的裁定表)。

## 为什么不用 akshare 的 `stock_hot_rank_em`

它内部走**两跳**:

1. `POST https://emappdata.eastmoney.com/stockrank/getAllCurrentList`
   → 200 / 100 行 / keys `['sc','rk','rc','hisRc']` —— **榜单本体,完好可取**;
2. `GET  https://push2.eastmoney.com/api/qt/ulist.np/get`
   → **502**(2026-08-09 实测;11.5 小时后复跑仍 502)—— 它只负责补**最新价/涨跌幅**。

第二跳一挂,`data_json["data"]["diff"]` 直接 `JSONDecodeError`,**整张榜单被连坐丢掉**。
而 `push2.eastmoney.com` 正是本项目记忆判例点名的被封主机(「A股数据走 tushare,东财
push2 被封」)——上一轮把连败解释成「15 分钟限频」是误诊,已被复跑证伪。

价格列本仓早有 tushare `daily`,第二跳对我们零价值。故本模块**只取第一跳**:

    sc     前缀式代码(SH603259 / SZ301308)——非 tushare 后缀式,消费侧用
           `symbol_utils.to_ts_code` 转(92xxxx 北交所坑见记忆判例)
    rk     当前排名(1..100)
    rc     排名变化   ┐ 东财自己的 `rankChange` / `hisRankChange`(见 `stock_hot_rank_latest_em`
    hisRc  历史排名变化┘ 的元数据字段名);**原始列不译、不二次解释**(T1 决策④)。

快照语义:接口不接受任何日期/历史参数,只返回"此刻"的榜单 —— **今晚不采,今晚这份榜单
就永久没有了**,这正是它在 Wave12 里排最优先的唯一理由。
"""
from __future__ import annotations

import pandas as pd
import requests

HOT_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"

# akshare 同款 payload(2026-08-09 实测 200);pageSize=100 = 人气榜本身就是 TOP100。
_PAYLOAD = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}
_TIMEOUT = 15          # 夜跑必须有超时:没有它,一次挂起的连接会拖死整晚预热


def fetch_hot_rank(page_size: int = 100, timeout: int = _TIMEOUT) -> pd.DataFrame:
    """人气榜原始帧(列 `sc`/`rk`/`rc`/`hisRc`)。

    **形态不对就抛**,不返回空帧假装成功:B 级契约在上层负责"断采只损失当日、不阻断",
    但那要求断采是**显式**的 —— 静默的空帧会被写进湖并把这一天永久钉成空
    (「cache 空 pickle 永不重拉」家训的 parquet 同族)。
    """
    payload = dict(_PAYLOAD, pageSize=page_size)
    r = requests.post(HOT_RANK_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    rows = (r.json() or {}).get("data")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"东财人气榜返回体形态异常(data 不是 list,而是 {type(rows).__name__}):"
            f"{str(rows)[:120]} —— 多半是接口改版/限频,不是「今天没有榜单」")
    return pd.DataFrame(rows)
