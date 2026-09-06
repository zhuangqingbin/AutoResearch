"""券商成交取数层 —— 把手机 App 导出的交割单/对账单/截图表解析成标准化成交表(零 LLM、零网络)。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md

模块:schema(列与两级契约)· sniff(真身嗅探)· adapters(各来源 → RAW 帧)· store(幂等落盘与合并)
· ingest / reconcile(CLI)。只记不学:不回注 prompt、不改门/权重/评级。
"""
