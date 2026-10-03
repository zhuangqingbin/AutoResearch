# 配置开发标准

编辑本 skill、scan_config 或配置消费者前必须阅读本文件。

- **配置标准(九条;机器判定 `uv run --no-sync python -m autoresearch.scan.config_standard`,PostToolUse hook 在改 `.claude/skills/scan-market/*`、注册表或 `user_config.py` 之后自动跑,违规即拦,改到零违规为止)**:
  1. 白名单 = 注册表派生;野键、错型 load 即 raise。
  2. 每键至少一个真实消费者(函数存在且源码含键名);死键不留。
  3. 只有一侧宿主读的键要登记 hosts 并在尾注写「仅 legacy workflow / 仅 session_agent」;两侧各读的键两边都要登记消费者。
  4. 文件与注册表键集完全相等:不许暗键(注册表有、文件无)、不许野键。
  5. 注释只有两种:每键一行尾注(作用;不超过 80 字符;无日期、无历史词)+ 每块一行头注(注册表渲染,`--fix-headers` 重生成);文件头不超过 8 行;块内不许段落注释。
  6. 顶层块顺序 = 注册表;分区一「漏斗行为」整体排在分区二「运行时」之前。
  7. 代码里的缺省字面量 = 注册表 default(knob 第四参数 / `.get` 缺省 / JS `??`)。
  8. 新增可调常量必须进 config;暂时不进的登记到注册表 `CODE_CONSTANTS` 并写理由(P2/P3 待迁 或 不进 config),「待迁」条数只减不增。
  9. skill 文档不复述键值(键名后不跟数字);值只住 jsonc。
