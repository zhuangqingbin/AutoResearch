# Session Agent 学习实验

每个练习都使用临时目录或当前引擎数据，不修改生产评分参数、不读取另一引擎目录、不连接真实交易。

## 练习 1：增加只读工具

在 `operations.py` 增加一个只读、无网络、严格无参数的诊断 operation，并在 tool catalog 登记。先写测试拒绝额外参数，再验证 argv 只能指向固定模块。完成标志是 operation/catalog 集合一致且没有新增任意 shell 入口。

## 练习 2：增加非评分证据

给合成 stock run 增加一项来源时间证据，使用 artifact ID 传递，并让报告附录展示它。验证评分和最终 proposal 在有无该证据时相同；未来日期和 T4 未跟权威原文都必须失败。

## 练习 3：模拟迟到回执

在临时 run 中 claim attempt 1，记录失败后 claim attempt 2，再提交 attempt 1 的输出。完成标志是迟到提交被拒、attempt 2 仍是 RUNNING、已绑定输入没有变化。

## 练习 4：完成独立上下文复核

用宿主真实 child session 执行一个合成 review task，生成包含不同 parent/child context 身份的 host receipt。验证缺 receipt、相同 context、错误 attempt 都失败；只有真实 child receipt 可以接受。该练习会消耗订阅会话 token，应使用短合成卡。

## 练习 5：比较上下文与 token

对相同冻结输入分别运行 legacy 与 `session_v1`，用 `evaluation.build_comparison` 记录确定性差异，用 `research.efficiency_baseline` 读取真实 usage。分别记录主会话、研究角色、重试、缓存 input、耗时和缺失率。少于 10 次真实扫描时只写观察值，不外推节省比例。

建议按 1→5 顺序学习：静态工具边界、artifact、attempt、宿主证据、真实效率。每次只改一个变量并保存验证命令与 run_id。

