"""送达:无人值守场把 ``brief.md`` 推到手机,失败时推一句 FAILED(确定性,零 LLM)。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §6 C3。

渠道 = ``scan_config.jsonc`` 的 ``delivery.channel``(唯一参数源):

- ``none``(默认,parity)—— 什么都不发,只落记录;
- ``bark`` —— iOS Bark 推送,一次 HTTPS POST 到 ``https://api.day.app/push``,token 以
  ``device_key`` 放在 JSON 正文里(**不进 URL**,日志与异常里就不会带出它)。token 只从环境
  变量 ``BARK_TOKEN`` 读 —— ``.env`` 由 ``import autoresearch`` 的 dotenv 装载带入;代码、
  配置、测试、日志里零字面量。Bark 正文上限约 4KB:brief 超过 3000 字节只推前 3000 字节
  (按 UTF-8 字符边界截)+ 报告路径,不报错;
- ``mail`` —— 本机 ``mail -s``,收件人只从 ``DELIVERY_MAIL_TO`` 读(同样只在 ``.env``);
- ``file`` —— brief 原文 + 报告路径复制到 ``delivery.file_dir``(iCloud / Obsidian 同步目录)。

**送达失败不影响 run 状态**:``send`` / ``notify`` 从不抛,结果落 ``_delivery.json``
(缺省在 brief 同目录;调用方可指定别处 —— canonical 发布根是封存的,不能往里写)。

  uv run --no-sync python -m autoresearch.scan.delivery send <brief.md> --run-id <RUN_ID>
  uv run --no-sync python -m autoresearch.scan.delivery notify "扫描 2026-09-28 FAILED · …"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json

CHANNELS = ("none", "bark", "mail", "file")
DELIVERY_RECORD = "_delivery.json"
BARK_ENDPOINT = "https://api.day.app/push"
#: 推送正文里 brief 部分的字节上限(Bark/APNs 单条约 4KB,留出标题与报告路径的余量)。
BARK_BODY_LIMIT = 3000
HTTP_TIMEOUT = 15.0
MAIL_TIMEOUT = 60


def delivery_limits(cfg: dict | None = None) -> dict:
    """`scan_config.delivery.{bark_body_limit, http_timeout_s, mail_timeout_s}`(缺键 = 模块常量)。"""
    from autoresearch.scan.user_config import knob
    return {"bark_body_limit": int(knob("delivery", "bark_body_limit", None, BARK_BODY_LIMIT, cfg)),
            "http_timeout_s": float(knob("delivery", "http_timeout_s", None, HTTP_TIMEOUT, cfg)),
            "mail_timeout_s": int(knob("delivery", "mail_timeout_s", None, MAIL_TIMEOUT, cfg))}
TOKEN_ENV = "BARK_TOKEN"
MAIL_TO_ENV = "DELIVERY_MAIL_TO"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def configured_delivery(cfg: Mapping | None = None) -> dict:
    """``{channel, file_dir}``;没配 = ``none``(parity)。"""
    from autoresearch.scan.user_config import knob

    return {
        "channel": knob("delivery", "channel", None, "none", cfg),
        "file_dir": knob("delivery", "file_dir", None, None, cfg),
    }


def _truncate_utf8(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def compose(text: str, report_path: str | None, *, limit: int | None = None) -> tuple[str, bool]:
    """正文 = brief(可选截断)+ 报告路径一行。返回 ``(body, truncated)``。"""
    body, truncated = (text, False) if limit is None else _truncate_utf8(text, limit)
    body = body.rstrip("\n")
    if truncated:
        body += "\n…(已截断,全文见报告)"
    if report_path:
        body += f"\n\n报告:{report_path}"
    return body, truncated


def _http_post(url: str, payload: dict, timeout: float) -> int:
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return int(getattr(response, "status", 200))


def _mail_send(to: str, subject: str, body: str) -> int:
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        ["mail", "-s", subject, to], input=body, text=True, capture_output=True, timeout=delivery_limits()["mail_timeout_s"])
    return proc.returncode


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _transport(
    channel: str,
    title: str,
    text: str,
    *,
    report_path: str | None,
    file_name: str,
    cfg: Mapping | None,
    environ: Mapping[str, str],
    http_post: Callable[[str, dict, float], int],
    mail_send: Callable[[str, str, str], int],
) -> dict:
    """真正发一次;返回 ``{status, error, truncated, body_bytes, target}``,从不抛。"""
    result = {"status": "SKIPPED", "error": None, "truncated": False, "body_bytes": 0,
              "target": None}
    if channel == "none":
        return result
    secrets = (str(environ.get(TOKEN_ENV) or ""),)
    try:
        if channel == "bark":
            token = str(environ.get(TOKEN_ENV) or "").strip()
            if not token:
                raise RuntimeError(f"{TOKEN_ENV} 未设置(放进 .env),未发送")
            body, truncated = compose(text, report_path, limit=delivery_limits()["bark_body_limit"])
            result.update(truncated=truncated, body_bytes=len(body.encode("utf-8")),
                          target=BARK_ENDPOINT)
            status = http_post(BARK_ENDPOINT, {"device_key": token, "title": title,
                                               "body": body, "group": "scan"}, delivery_limits()["http_timeout_s"])
            if not 200 <= int(status) < 300:
                raise RuntimeError(f"Bark HTTP {status}")
        elif channel == "mail":
            to = str(environ.get(MAIL_TO_ENV) or "").strip()
            if not to:
                raise RuntimeError(f"{MAIL_TO_ENV} 未设置(放进 .env),未发送")
            body, _ = compose(text, report_path)
            result.update(body_bytes=len(body.encode("utf-8")), target="mail")
            code = mail_send(to, title, body)
            if code != 0:
                raise RuntimeError(f"mail 退出码 {code}")
        elif channel == "file":
            folder = configured_delivery(cfg)["file_dir"]
            if not folder:
                raise RuntimeError("delivery.file_dir 未配置(channel=file 必填)")
            target = Path(os.path.expanduser(str(folder))) / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            body, _ = compose(text, report_path)
            target.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
            result.update(body_bytes=len(body.encode("utf-8")), target=str(target))
        else:
            raise RuntimeError(f"未知送达渠道 {channel!r}(可选 {'/'.join(CHANNELS)})")
    except Exception as exc:  # noqa: BLE001 - delivery never changes the run outcome
        result.update(status="FAILED",
                      error=_scrub(f"{type(exc).__name__}: {exc}", secrets)[:500])
        return result
    result["status"] = "SENT"
    return result


def _channel(channel: str | None, cfg: Mapping | None) -> str:
    return channel or configured_delivery(cfg)["channel"]


def send(
    brief_path: Path | str,
    *,
    run_id: str,
    channel: str | None = None,
    title: str | None = None,
    report_path: str | None = None,
    record_path: Path | str | None = None,
    cfg: Mapping | None = None,
    environ: Mapping[str, str] | None = None,
    http_post: Callable[[str, dict, float], int] | None = None,
    mail_send: Callable[[str, str, str], int] | None = None,
) -> dict:
    """推一份 brief;落 ``_delivery.json``(或 ``record_path``);返回同一份记录,从不抛。"""
    brief = Path(brief_path)
    env = os.environ if environ is None else environ
    try:
        resolved = _channel(channel, cfg)
    except Exception as exc:  # noqa: BLE001 - a broken config still leaves a record
        resolved, config_error = "none", f"{type(exc).__name__}: {exc}"
    else:
        config_error = None
    heading = title or f"扫描 brief · {run_id}"
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "channel": resolved,
        "title": heading,
        "brief_path": str(brief),
        "report_path": report_path,
        "attempted_at": _now(),
    }
    try:
        text = brief.read_text(encoding="utf-8")
    except OSError as exc:
        outcome = {"status": "FAILED", "error": f"brief 读不到:{type(exc).__name__}: {exc}",
                   "truncated": False, "body_bytes": 0, "target": None}
    else:
        record["brief_bytes"] = len(text.encode("utf-8"))
        outcome = _transport(
            resolved, heading, text, report_path=report_path,
            file_name=f"scan_{run_id}_brief.md", cfg=cfg, environ=env,
            http_post=http_post or _http_post, mail_send=mail_send or _mail_send)
    if config_error:
        outcome = {**outcome, "status": "FAILED", "error": f"delivery 配置不可读:{config_error}"}
    record.update(outcome)
    target = Path(record_path) if record_path is not None else brief.parent / DELIVERY_RECORD
    try:
        atomic_write_json(target, record)
        record["record_path"] = str(target)
    except OSError as exc:
        record["record_error"] = f"{type(exc).__name__}: {exc}"
    return record


def notify(
    title: str,
    body: str,
    *,
    channel: str | None = None,
    cfg: Mapping | None = None,
    environ: Mapping[str, str] | None = None,
    http_post: Callable[[str, dict, float], int] | None = None,
    mail_send: Callable[[str, str, str], int] | None = None,
) -> dict:
    """一句话通知(未开 / FAILED),同一渠道;不落记录(调用方的日志就是记录),从不抛。"""
    env = os.environ if environ is None else environ
    try:
        resolved = _channel(channel, cfg)
    except Exception as exc:  # noqa: BLE001
        return {"channel": None, "status": "FAILED", "error": f"delivery 配置不可读:{exc}"}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outcome = _transport(
        resolved, title, body, report_path=None, file_name=f"notify_{stamp}.md", cfg=cfg,
        environ=env, http_post=http_post or _http_post, mail_send=mail_send or _mail_send)
    return {"channel": resolved, "title": title, **outcome}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.delivery",
                                 description="送达 brief / 一句话通知(渠道 = scan_config delivery.channel)")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("send")
    s.add_argument("brief_path")
    s.add_argument("--run-id", required=True)
    s.add_argument("--channel", choices=CHANNELS)
    s.add_argument("--title")
    s.add_argument("--report-path")
    s.add_argument("--record-path")
    n = sub.add_parser("notify")
    n.add_argument("message")
    n.add_argument("--title", default="扫描通知")
    n.add_argument("--channel", choices=CHANNELS)
    args = ap.parse_args(argv)
    if args.command == "send":
        result = send(args.brief_path, run_id=args.run_id, channel=args.channel,
                      title=args.title, report_path=args.report_path,
                      record_path=args.record_path)
    else:
        result = notify(args.title, args.message, channel=args.channel)
    print(json.dumps({k: v for k, v in result.items() if k != "body"}, ensure_ascii=False))
    return 0 if result.get("status") in {"SENT", "SKIPPED"} else 1


__all__ = [
    "BARK_BODY_LIMIT", "BARK_ENDPOINT", "CHANNELS", "DELIVERY_RECORD", "compose",
    "configured_delivery", "main", "notify", "send",
]


if __name__ == "__main__":
    sys.exit(main())
