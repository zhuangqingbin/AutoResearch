"""Shared research wording for session dispatch and generated legacy adapters."""
from __future__ import annotations

import json
import re

TEMPLATES = {
    'scan.l4.card': '执行 {prompt}:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 {output}。最后返回该卡最终五档评级与 FINAL 行(code / rating / conviction / proposal=FINAL TRANSACTION PROPOSAL 的值,如 "SELL")。',
    'scan.l4.review': '独立复核 run{run_index}(不知道其它 run 结论):执行 {prompt} 的任务包,按人设走渐进深度 DD,决策卡写到 {output}(先自行创建 ensemble/ 目录),返回 code/rating/conviction/proposal。',
}
START = '// BEGIN GENERATED RESEARCH TEMPLATES'
END = '// END GENERATED RESEARCH TEMPLATES'


def render_research_prompt(role: str, **values) -> str:
    template = TEMPLATES[role]
    if set(re.findall(r'\{(\w+)\}', template)) != set(values):
        raise ValueError('research template arguments mismatch')
    if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in values.values()):
        raise ValueError('research template arguments must be explicit text or integer')
    return template.format_map(values)


def legacy_template_block():
    encoded = json.dumps(TEMPLATES, ensure_ascii=False, sort_keys=True)
    return (START + '\n// Owner: autoresearch/common/research_prompts.py; regenerate with python -m scripts.sync_research_prompts\n'
            + 'const RESEARCH_TEMPLATES = ' + encoded + '\n'
            + "const researchPrompt = (role, values) => {\n"
            + "  const template = RESEARCH_TEMPLATES[role]\n"
            + "  if (!template) throw new Error('unknown research template')\n"
            + "  const keys = [...new Set([...template.matchAll(/\\{(\\w+)\\}/g)].map(m => m[1]))].sort()\n"
            + "  if (JSON.stringify(keys) !== JSON.stringify(Object.keys(values).sort())) throw new Error('research template arguments mismatch')\n"
            + "  return template.replace(/\\{(\\w+)\\}/g, (_, key) => String(values[key]))\n"
            + '}\n' + END)
