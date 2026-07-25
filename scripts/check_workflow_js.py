#!/usr/bin/env python3
"""workflow JS 语法探针(`node --check` 对本仓 workflow 零鉴别力时的替代)。

why:`.claude/workflows/*.js` 是 ESM(顶层 `export const meta` + 顶层 `await`/`return`)。
`node --check` 会把它当模块解析并**跳过**很多检查,写坏了照样 exit 0 —— Wave3.5 实测到的
"永不变红的绿灯"。这里剥掉 `export ` 关键字后塞进 AsyncFunction 构造器,让 V8 真解析一遍
函数体(顶层 await/return 在 AsyncFunction 里合法),坏语法会抛 SyntaxError。

  uv run --no-sync python scripts/check_workflow_js.py .claude/workflows/scan-market.js
  uv run --no-sync python scripts/check_workflow_js.py            # 不带参数 = 全部 workflow
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def check(path: Path) -> tuple[bool, str]:
    src = path.read_text(encoding="utf-8").replace("export const ", "const ")
    js = "new (Object.getPrototypeOf(async function(){}).constructor)(%s)" % json.dumps(src)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=False)
    return r.returncode == 0, (r.stderr or "").strip()


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(Path(".claude/workflows").glob("*.js"))
    bad = 0
    for p in paths:
        ok, err = check(p)
        print(f"{'✓' if ok else '✗'} {p}")
        if not ok:
            bad += 1
            print(err.splitlines()[0] if err else "(无 stderr)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
