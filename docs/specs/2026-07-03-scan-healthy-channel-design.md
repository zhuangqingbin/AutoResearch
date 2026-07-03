# scan healthy 召回通道 + L2 健康桶 + pre_healthy 影子反事实 设计

日期:2026-07-03 ｜ 分支:feat/scan-healthy-recall ｜ 状态:实施中

## 根因(2026-07-02 真实跑动取证,定量)

全市场 4240 只中 **261 只"健康上涨"(0<pct60<40 ∧ 主力净流入>0 ∧ cmf_20>0),0 只进入
L1 top1000**(rank 中位 3760、min 1421)。因子画像:该组 momentum/fund/tech 子分 74/76/81,
但 range 权重下 composite 仅 39(vs L2 组 62.5);L2 菜单画像 value 77.8 / momentum 21.7 =
接刀价值票。两层病因:
1. **通道空洞**:momentum 路 top250 被 pct60 100%+ 猛票占满、吸筹路要底部、无一路以
   "温和上涨+资金共振"为信号——swing 品相在 9 路间无家可归;
2. **目标函数错位**:T+1 IC 校准的 composite 在 range regime 结构性偏爱超跌价值
   (horizon 之争 pr_20260702_001 又一实锤,T+5 数据裁决)。

## 设计(全确定性,零 token)

1. **单一事实源谓词** `scoring.healthy_riser_mask(frame)`:menu_health 病灶指标与召回
   通道同一定义(菜单体检量它、召回通道捞它——同一把尺);缺列 → None 降级。
2. **第 10 路 channel `healthy`**(quota 150 / union floor 40):`healthy_riser_mask` 过门,
   按 `pct(main_net_ratio)+pct(cmf_20)` 共振强度排序(门内不再按动量排——要质量不要 froth)。
3. **L2 健康桶**:STYLE_CHANNELS 加 `健康:(healthy,)`,DEFAULT_FLOORS 加 `健康:15`
   (总 floor 78→93,merit 核 122→107)。**通道进池、桶上菜,两级都补**。
4. **pre_healthy 影子反事实**:universe 影子第 3 变体 = 旧 9 路 + 旧 floors 全程重放
   (同一 scored 帧内存重算,零网络)→ retro `shadow_compare` 直接可测 healthy 的捕获增量。
   影子块重构为可测函数 `write_shadow_variants`。

## 边界与诚实

- healthy 通道**不进 composite 权重**(不是 T+1 因子,是菜单多样性通道——正当性同 heat 路:
  "这类品相该被判断层看到",与 L2 分层同一哲学:不预测,保多样性);
- 上线即默认(multi 10 路);**反事实由影子承担**而非拖延上线——07-02 病灶明确、修复零成本、
  可逆(`--recall-channels` 可指定旧 9 路);
- 桶 floor=15 是 policy 非校准值;retro floor 自然实验(救回 vs merit vs 被挤)自动覆盖健康桶。

## 测试
healthy 通道(门/排序/缺列)、注册与配额、L2 健康桶 floor 救回、write_shadow_variants
(3 变体/pre_healthy 无 healthy 标)、menu 谓词复用回归。合成,无网络。
