# 2026-09-14 法证修复合成验收记录

本文件只记录软件与合成现场验证，`evidence_kind=SYNTHETIC`。它不包含 Codex 或 Claude Code
真实订阅会话矩阵，不能被 `accept_workflow` 用来切换任何 workflow 默认入口。

## 被测身份

- 分支：`feature/session-forensics-all-workflows`
- 起始提交：`fbac336e09e7ff157fcfce7fb06a7f254b58faf6`
- 被测 Python 源树摘要：`adf3778dd8dcf99a628c936b407456ce57ee20208dac1dc30c93213efae0d782`
- 摘要算法：对 `rg --files autoresearch -g '*.py' | LC_ALL=C sort` 的每个文件先做
  SHA-256，再对排序后的摘要清单做 SHA-256。文档不进入该源树摘要。
- 平台：Darwin 24.6.0 x86_64；Python 3.13.11；pytest 9.1.0。
- 引擎固定：`AUTORESEARCH_ENGINE=codex`。

## 实际命令与结果

```text
pytest -q tests/forensics tests/session_agent tests/trace tests/contracts --tb=short
1477 passed, 7 skipped, 4 subtests passed in 333.30s

pytest -q --tb=short
6557 passed, 12 skipped, 5 warnings, 4 subtests passed in 691.76s

ruff check autoresearch/contracts autoresearch/session_agent autoresearch/trace tests/forensics
All checks passed

python -m compileall -q autoresearch/contracts autoresearch/session_agent autoresearch/trace
exit 0
```

12 个全仓 skip 均由 pytest 给出明确原因：缺 gitignored 历史真现场 10 项、无生产
`weights.json` 1 项、当前文件系统拒绝 byte path 1 项。5 条 warning 为 3 条 DataFrame attrs
序列化提示与 2 条 pandas FutureWarning；没有被当成真实宿主验收证据。

## 隔离声明

- 测试现场使用 pytest 临时目录和 Codex 引擎根；没有读取或写入 Claude 的 context/reports。
- replay 隔离、拒网、拒读原 capsule/lake/expected、拒写原状态由合成故障测试覆盖；这证明代码门
  会拒绝越界，不证明本次存在一趟真实宿主 `isolation_status=ENFORCED` 的研究 run。
- T12–T15 的五类 workflow 与五类配套服务重放均为离线合成夹具；模型输出只按
  `EVIDENCE_ONLY` 回注，没有假造模型重新推理。
- 当前 Codex 与 Claude Code 的 `REAL_SESSION` portable proof 均未收齐，五类 workflow 的上线
  状态保持 `INCOMPLETE`。
