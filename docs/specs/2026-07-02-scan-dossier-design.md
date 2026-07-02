# 个股档案(前科卡)+ watchlist 触发度量 — 设计

- 状态:设计定稿(待实现)
- 日期:2026-07-02
- 触及:`autoresearch/scan/dossier.py`(新)、`scan/agents/l4_card.py`(brief 注入)、`learning/watchlist_ledger.py`(新)、`.claude/skills/scan-market/screening-playbook.md`
- 关联:brainstorm R5/R8;防锚定纪律同 [[2026-07-01-scan-market-strategist-view]]

## 1. 背景

- **R5**:紫光国微三天三张满卡撞死在同一道 CFO/FCF 门——每次全新研究,系统对"研究过谁、被什么杀死、上次要复核什么"零记忆。finalist 日间高度复现 → 重复烧 L4 token + 跨日结论漂移风险。"第五次 miss"这类触发语言说明个股历史是判断的一等公民,但它只活在散文里。
- **R8**:观察单四态日检已上线,但"触发 → 复核 → 后市"没有度量——触发单本身准不准无人知道。

## 2. 设计

### 2.1 `autoresearch/scan/dossier.py`(新,确定性零 LLM)

- `stock_dossier(code, scan_root="context/scan", max_days=10, exclude=None) -> list[dict]`:
  倒序遍历最近 max_days 个 scan 日目录(**exclude 当日**,档案=历史);code 出现在该日 `finalists.csv` → 收一条:
  `{date, conviction, lane, l3_risk(finalists.risk), rating(parse_rating(details/<code>*.md)), l4_line(assemble._l4_brief 提 binding 理由), verify(verdict+trigger,若在 verify.csv)}`。缺卡/缺文件逐项降级 None。按日期升序返回。
- `render_dossier(code, ...) -> str`:块 `### 📁 个股档案(近 N 次入围)`:
  - 逐次一行:`06-30:Hold(rubric封顶)——CFO−3.5亿/FCF负 ｜ skeptic:—`;
  - **已知证伪点**(l3_risk + l4_line 去重 ≤4 条);
  - **防锚定 footer(铁律)**:"档案是历史事实非预判;本次评级仍由本卡 rubric 三门独立定;**重点核对【变化项】——上述证伪点哪条已改变/未改变**。"
  - 无历史 → ""(presence-gated)。
- **注入**:`l4_card.compose_funnel_brief` 在市场地形块后追加 `render_dossier(code, exclude=当日)`(lazy import;异常吞掉回 "",老 brief 不破)。

### 2.2 playbook(L4 卡模板)

- P0/P1 增指令:**有档案块时,卡片必须含"变化项(vs 档案)"一小节**——逐条对上次证伪点答"已变/未变/新证据";未变的门不必重新长篇论证(引档案 + 一句现值),**变了的才展开**(增量研究,省 token 靠这里)。
- 铁律重申:档案不预判本次评级。

### 2.3 `autoresearch/learning/watchlist_ledger.py`(新,镜像 zero_buy_ledger)

- `roll(scan_root)`:glob `*/watchlist_status.csv`,取 `status` 以"触发"开头的行 →
  join 同日 `retro/attribution.csv` 的 fwd_1_oo/fwd_5_oc(触发日起算的后市,口径天然对齐)→
  `[date, code, name, fwd_1, fwd_5]`。
- `render`:逐行 + 汇总(触发后均值/胜率);空 → 优雅提示。CLI → `reports/learning/watchlist_ledger.md`。

## 3. 测试(合成,无网络)

- `tests/scan/test_dossier.py`:两历史日(finalists+details md 带 Rating/一行多空 + verify)→ 档案两条、rating/verify 正确、exclude 当日生效、render 含防锚定 footer;无历史 → "";缺 details 降级。
- `tests/scan/test_l4_brief_market_ctx.py` 增:有档案 → brief 含 `📁`;无 → 与旧 brief 一致。
- `tests/learning/test_watchlist_ledger.py`:触发行 join fwd;无触发/空目录优雅。

## 4. 非目标

- 不做持久化索引(≤10 日按需读,量级足够;慢了再缓存);
- 档案不进 L3 表(200 只逐只查档案成本高且 L3 是横向比较场景;只进 L4 单票深研);
- 不自动改评级/不给方向指令(防锚定铁律)。
