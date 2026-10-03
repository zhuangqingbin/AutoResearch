# Agent 阶段价值评价协议

状态：**开发协议，尚未预注册、尚未执行实验**。本文件落实开发计划 D1 的评价方法与现有接口映射；没有实际样本、真实成交或收益改善结论。工作树仍有开发改动，不生成虚假的干净 commit 证明。

## 1. 固定决策口径

交易判断统一使用决策卡与 `gap_c1_o2`：T+1 收盘买入、T+2 开盘卖出。评价使用收益 fraction 与 `day_equal`，敏感尺只并列观察。评级由本股三门决定；评价程序不回写评级、生产权重、prompt 或默认工作流。零 BUY 日保持原义。

每次实验只改变一个因素，基线和候选使用同一冻结人口、相同可用信息截止与相同执行假设。

| 实验 | 基线与唯一变化 | 必须回答 |
|---|---|---|
| L3 | 同一 eligible L2 人口；仅改变 L3 保留/拒绝 | 风险过滤与错失分别是多少 |
| L4 | 同一 finalists；仅改变卡片评级/入场门 | 哪些事实或判断导致选择变化 |
| ensemble | 同一复核触发人口；验真后、ensemble 前卡对复核后卡 | 纠错、共同错误与尾部损失变化 |
| 两段初判 | 同一底层事实与最终可用资料；一段对 B3 两段 | 先验暴露次序、判断变化与新增成本 |
| E6 召回 | 同一硬门、候选池、max_buys；仅移除召回排序面 | 多路召回是否被重复奖励 |
| E6 证据 | 同上；仅改变证据排序面 | 深核发生或来源数量是否替代了投资证据 |

E6 剩余面的合成方法、tie-break 与 A/R 语义在看结果前固定。单独追踪“多路召回 → force_full → evidence_depth → E6 排名”，不把这条路径上的节点当作互相独立的证据。

## 2. 冻结与文件身份

所有 Codex 产物写入 `context_codex/` 或 `reports_codex/`，共享行情只读既有 `lake/`。不访问另一引擎的闭环数据。

### 已具备全部输入的离线实验

1. 固定真实代码版本；由 `registration.verify_code_provenance` 检查行为目录与 commit 一致，保留脏工作区拒绝门。
2. 枚举输入文件，使用 `registration.file_manifest(paths, date_slice)` 与 `manifest_digest` 生成真实清单和摘要。摘要证明字节身份，不证明历史可得性。
3. 根据 `contracts.research_experiment.REQUIRED_SPEC` 生成完整 spec；包含 hypotheses、population/selection、baseline、prompt hashes、split、purge/embargo、bootstrap、multiplicity、成熟政策、质量约束和停止规则。
4. 通过 `experiment_io.create_experiment_dir` 与 `freeze_validated_spec` 排他冻结。重复实验 ID 不覆盖；修改假设使用新 ID，失败目录也保留。
5. 冻结后才运行评价、查看比较结果。已看过结果的探索必须声明探索性，不能补写成预注册。

`validate_spec` 是字段形状校验；不替代运行入口的 engine、代码溯源、日期和输入清单检查。当前成熟政策解析只支持 `scan_days>=N` 及其既有包装形式。

### 前向观察的两次冻结

开始前冻结协议、真实代码/prompt/config、比较规则和交易日历生成的连续 **60 个分析交易日**清单；逐日在决策前冻结当日真实输入。结束后等待相应 D2 标签成熟，才生成最终输入清单和现有 evaluation spec，并在 readout 绑定原协议的 commit/hash。

未来输入哈希不预填。最终 spec 的时间不冒充启动时间。60 日不是功效保证；证据不足标 `INSUFFICIENT`。下一窗口另立方案，不按收益曲线延长或缩短。契约、来源或发布故障可提前停止，记录为故障终止。

## 3. 人口、标签和统计

- 人口保留拒绝票、未入选票、UNKNOWN 与失败任务；记录缺失原因。推荐成功样本不能单独构成评价人口。
- train/validation/test 固定为互不重叠的具体 `[start, end)` 日期区间；purge 按真实标签结束时点，embargo 按交易日历。
- 使用同日人口配对、日等权差异。并列报告日期数、证券日数、有效配对日、行业集中度、缺失标签与空选择日。
- `paired_daily_selection` 对标签缺失输出 `INCOMPLETE_OUTCOMES`，任一侧选择为空输出 `EMPTY_SELECTION`；不把这些日期补成零收益。配对统计之外仍完整报告缺失和空选择频率。
- 固定 bootstrap seed/次数，并用 `robustness.block_sensitivity` 报告 block=1/5/10。注册检验族、主要比较数及多重比较处理，披露全部尝试。
- 历史模型重放标 `RETRO_REPLAY`。文件截止正确仍不能证明模型没有历史记忆，不单凭这种重放声称可交易优势。
- 主尺的点估计、区间、尾部损失、覆盖和成本约束同时呈现；区间跨零写不确定，不写已证增益或等价。

## 4. 现有接口与能力边界

| 所属模块 | 现有用途 | 使用边界 |
|---|---|---|
| `research.experiment_io` / `registration` | 排他冻结、代码与输入溯源 | 没有独立冻结 CLI；通过 Python API 调用 |
| `research.stage_value` | 人口阶段配对毛标签 | 只接受 EOD_PROXY / RETRO_REPLAY，cost_model_version 只能是 none |
| `research.robustness` | 分块敏感性、purge/embargo | 时间边界必须来自真实标签与已冻结日历 |
| `research.execution_audit` / `execution_ledger` | 快照模拟、实际成交匹配与费用 | 输入需显式指定；不自动连接券商或导入账户 |
| `research.probability_eval` | 事件概率与共同错误 | conviction 不是概率 |
| `research.efficiency_baseline` | 逐次效率、缺测覆盖、分组汇总 | proxy 不冒充真实 token；真实扫描与演练分组 |

`stage_value` 阶段对保留 `menu_to_l3`、`l3_to_l4`、`l4_to_e6`。
新增 `stage_adapters` 负责 ensemble、B3 和 E6 消融输入，独立保留缺失与 UNKNOWN。
ensemble 基线是实际 `post_verify_rating`；B3 要求两个不同运行的真实冻结 profile 与共同输入；
E6 从冻结原排序移除单一 face，保留原硬门、tie-break 与全部人口，不能将普通前后配对称为消融。

生产扫描在 assemble 后冻结实际 force_full/priors、accepted artifacts 与深读观察，随报告发布。
导出器只接受已发布、MANIFEST 覆盖且身份一致的字节；不读取当前 staging 补齐历史缺项：

```bash
uv run --no-sync python -m autoresearch.research.production_export \
  --run-dir <canonical扫描目录> --output-dir <全新导出目录>
uv run --no-sync python -m autoresearch.research.stage_adapters ensemble \
  --reference <导出目录/ensemble.ref.json> --selected-rating Buy --selected-rating Overweight \
  --output <比较文件.json>
uv run --no-sync python -m autoresearch.research.stage_adapters b3 \
  --one-stage <一段运行/b3.ref.json> --two-stage <两段运行/b3.ref.json> \
  --selected-rating Buy --selected-rating Overweight --output <比较文件.json>
uv run --no-sync python -m autoresearch.research.stage_adapters e6 \
  --reference <导出目录/e6.ref.json> --remove-face evidence --output <消融文件.json>
```

导出 `population.csv` 包含拒绝/失败/未核票，`forward-inputs.json` 是输入描述；
导出时间不是原始历史可用时间。只有实际绑定的完整原生读取观察可计 `deep_read_verified=true`，
输入清单出现 deep 仅表示 `deep_declared`。缺计量或阶段记录保留 null/UNKNOWN。

前向窗口已有独立 CLI，不能在当前脏工作树上伪造干净代码冻结：

```bash
uv run --no-sync python -m autoresearch.research.forward_study begin \
  --parent <实验父目录> --template <协议模板.json> --calendar <有来源的交易日历.json> \
  --start-date <首个分析交易日> --sources <prompt和config路径清单.json> --repo-root <干净代码目录>
uv run --no-sync python -m autoresearch.research.forward_study day \
  --directory <实验目录> --date <分析交易日> --inputs <当日输入描述.json>
uv run --no-sync python -m autoresearch.research.forward_study finalize \
  --directory <实验目录> --labels <已成熟标签描述.json>
uv run --no-sync python -m autoresearch.research.forward_study abort \
  --directory <实验目录> --reason <故障原因>
```

`day` 在捕获和冻结完成后再次检查截止；`finalize` 核对连续 60 日与 D2 成熟、逐日原人口及选择标记。
缺失标签要保留对应行及状态，不能删去亏损票或用空文件通过。失败/终止记录不删除。

离线输入和 spec 完整且已冻结后，现有入口为：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.research.stage_value \
  --spec <已冻结的-spec.json> \
  --population <冻结的人口文件>
```

多个输入重复传 `--population`。协议不包含可直接执行的虚构 spec；真实路径、哈希和日期必须在实验启动时取得。

## 5. 执行与概率口径

| 层级 | 可以回答 | 必须保留的限制 |
|---|---|---|
| EOD_PROXY | 日线 C1/O2 的毛标签变化 | 不证明截止时可买、成交或滑点 |
| SNAPSHOT_SIMULATED | 给定快照、规则和版本化成本下的模拟 | 报快照覆盖、时延与部分成交假设 |
| OBSERVED_FILL | 明确授权导入的真实成交和费用 | 缺成交/费用保持缺失，人口限定为实际执行 |

快照分别核对市场发生、收到、记录、决策及有效截止。D1 的 high_so_far 不替换成最终 high。候选、可下单、委托、成交、退出完成、超窗持仓分别计数；停牌、涨跌停、部分成交和未退出不按理论开盘价补成交。费用来自版本化 policy 和实际记录。

概率事件固定为“计划隔夜窗、声明执行模式与费用口径下，完整样本净收益 > 0”。缺成交或费用则 `y=null`。仅明确针对该事件输出的概率可交给 `probability_metrics`；不将 conviction=70 转成 p=0.7。Brier、固定分箱可靠性和基准发生率并列；`error_overlap` 单独报告首卡与复核的共同错误。来源可信度、事实支持与收益概率分别计量。

## 6. 效率与上线判据

软件语义错误、未来信息泄漏、越界读取、必需证据丢失和错误发布的容忍度为零。调度与模板候选先满足确定性字段一致、关键证据完整和契约不退步，再比较成本与延迟。

每个 engine、工作流/研究模式、真实运行属性与缓存/来源覆盖分别建组；`metering_cohort_summary` 按实际模式与测量条件分组，不能把不同研究模式或合成演练混入真实扫描汇总。至少积累同组 10 次完整真实运行后才给稳定效率比较；不足时逐次报告。缺计量保持缺失，estimated_usd、weighted_input_proxy 和真实 token 不混称。

实验通过不自动切换生产：采纳需显式开发变更、回归与宿主验收。`session_v1` 默认切换仍由既有双宿主 REAL_SESSION proof 门决定。本文件不构成该证明。

## 7. 单次 readout 最小内容

1. 实验 ID、协议/spec 哈希、真实 commit、prompt/config 版本与输入清单。
2. 探索或预注册属性、证据模式、主尺、日期窗、终止原因。
3. 全人口流转与缺失分母；首卡、复核、最终选择变化及原因。
4. 主差异、区间、block 敏感性、尾部与覆盖；全部负结果和故障。
5. 逐次成本/延迟和测量覆盖；纠正的错误与共同错误。
6. 可支持的结论、不能支持的结论、尚缺证据及是否提出后续开发。

交付报告如来自 session_v1，仍须对 finish 返回的 canonical 报告执行 `verify-report --level full`，仅引用其 VerificationResult。该报告绑定门与投资效果评价各自承担自己的职责。
