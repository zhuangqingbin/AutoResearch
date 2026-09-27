"""Delivery of the brief (batch 4 Task 4, spec §6 C3). No network, no real mail.

Bark first (one HTTPS POST; token only from the environment / ``.env``), mail and file as
alternatives, ``none`` = parity (nothing sent, only ``_delivery.json``). A delivery failure
never raises: it is recorded and the run status is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import delivery

TOKEN = "fake-bark-token-for-tests"


def _brief(tmp_path: Path, text: str = "# 扫描 brief\n① 结论:今天 0 买\n") -> Path:
    path = tmp_path / "report" / "brief.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _Post:
    def __init__(self, status=200, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.error = error

    def __call__(self, url: str, payload: dict, timeout: float) -> int:
        self.calls.append((url, payload))
        if self.error:
            raise self.error
        return self.status


def _send(tmp_path, *, channel, environ=None, post=None, **kwargs):
    return delivery.send(
        _brief(tmp_path) if "brief_path" not in kwargs else kwargs.pop("brief_path"),
        run_id="20260928T132000000000Z", channel=channel,
        environ={} if environ is None else environ, http_post=post or _Post(),
        report_path="reports_claude/scan/20260928-0928_2120", **kwargs)


def test_none_channel_sends_nothing_and_records_skipped(tmp_path):
    post = _Post()
    record = _send(tmp_path, channel="none", environ={"BARK_TOKEN": TOKEN}, post=post)
    assert post.calls == [] and record["status"] == "SKIPPED"
    saved = json.loads((tmp_path / "report" / "_delivery.json").read_text(encoding="utf-8"))
    assert saved["status"] == "SKIPPED" and saved["channel"] == "none"


def test_default_channel_is_none_from_config(tmp_path):
    post = _Post()
    record = delivery.send(_brief(tmp_path), run_id="R", cfg={}, environ={"BARK_TOKEN": TOKEN},
                           http_post=post)
    assert record["channel"] == "none" and post.calls == []


def test_bark_posts_token_from_env_in_the_body_never_the_url(tmp_path):
    post = _Post()
    record = _send(tmp_path, channel="bark", environ={"BARK_TOKEN": TOKEN}, post=post)
    [(url, payload)] = post.calls
    assert url == "https://api.day.app/push" and TOKEN not in url
    assert payload["device_key"] == TOKEN
    assert "今天 0 买" in payload["body"]
    assert payload["body"].rstrip().endswith("reports_claude/scan/20260928-0928_2120")
    assert record["status"] == "SENT"
    saved = (tmp_path / "report" / "_delivery.json").read_text(encoding="utf-8")
    assert TOKEN not in saved


def test_bark_truncates_the_body_to_3000_bytes_and_keeps_the_report_path(tmp_path):
    post = _Post()
    long_brief = _brief(tmp_path, "长" * 3000)          # 9000 bytes of UTF-8
    record = _send(tmp_path, channel="bark", environ={"BARK_TOKEN": TOKEN}, post=post,
                   brief_path=long_brief)
    body = post.calls[0][1]["body"]
    head = body.split("\n")[0]
    assert len(head.encode("utf-8")) <= delivery.BARK_BODY_LIMIT
    assert head.encode("utf-8").decode("utf-8") == head          # never splits a character
    assert "reports_claude/scan/20260928-0928_2120" in body
    assert len(body.encode("utf-8")) < 4096
    assert record["truncated"] is True and record["status"] == "SENT"


def test_bark_without_token_fails_without_sending(tmp_path):
    post = _Post()
    record = _send(tmp_path, channel="bark", environ={}, post=post)
    assert post.calls == [] and record["status"] == "FAILED"
    assert "BARK_TOKEN" in record["error"]


def test_bark_http_error_is_recorded_not_raised_and_token_scrubbed(tmp_path):
    post = _Post(error=OSError(f"boom https://api.day.app/{TOKEN}/x"))
    record = _send(tmp_path, channel="bark", environ={"BARK_TOKEN": TOKEN}, post=post)
    assert record["status"] == "FAILED" and TOKEN not in record["error"]
    assert "***" in record["error"]


def test_bark_non_200_is_a_failure(tmp_path):
    record = _send(tmp_path, channel="bark", environ={"BARK_TOKEN": TOKEN}, post=_Post(status=400))
    assert record["status"] == "FAILED" and "400" in record["error"]


def test_file_channel_copies_brief_and_report_path_into_the_configured_dir(tmp_path):
    target = tmp_path / "icloud"
    record = _send(tmp_path, channel="file", cfg={"delivery": {"channel": "file",
                                                                "file_dir": str(target)}})
    [written] = list(target.glob("*.md"))
    assert "今天 0 买" in written.read_text(encoding="utf-8")
    assert "20260928T132000000000Z" in written.name
    assert record["status"] == "SENT" and record["target"] == str(written)


def test_file_channel_without_dir_fails(tmp_path):
    record = _send(tmp_path, channel="file", cfg={"delivery": {"channel": "file"}})
    assert record["status"] == "FAILED" and "file_dir" in record["error"]


def test_mail_channel_uses_local_mail_with_env_recipient(tmp_path):
    sent = []
    record = _send(tmp_path, channel="mail", environ={"DELIVERY_MAIL_TO": "me@example.invalid"},
                   mail_send=lambda to, subject, body: sent.append((to, subject, body)) or 0)
    [(to, subject, body)] = sent
    assert to == "me@example.invalid" and "今天 0 买" in body and subject
    assert record["status"] == "SENT"


def test_mail_without_recipient_fails(tmp_path):
    record = _send(tmp_path, channel="mail", environ={},
                   mail_send=lambda *a: pytest.fail("must not send"))
    assert record["status"] == "FAILED" and "DELIVERY_MAIL_TO" in record["error"]


def test_missing_brief_is_recorded_as_failed(tmp_path):
    record = delivery.send(tmp_path / "nope" / "brief.md", run_id="R", channel="bark",
                           environ={"BARK_TOKEN": TOKEN}, http_post=_Post(),
                           record_path=tmp_path / "rec.json")
    assert record["status"] == "FAILED" and "brief" in record["error"]
    assert json.loads((tmp_path / "rec.json").read_text(encoding="utf-8"))["status"] == "FAILED"


def test_explicit_record_path_wins(tmp_path):
    rec = tmp_path / "ops" / "x.delivery.json"
    _send(tmp_path, channel="none", record_path=rec)
    assert rec.is_file() and not (tmp_path / "report" / "_delivery.json").exists()


def test_notify_uses_the_same_channel_without_a_brief(tmp_path):
    post = _Post()
    result = delivery.notify("扫描 2026-09-28 FAILED", "阶段 scan.l3 · TIMEOUT · 日志 x.log",
                             channel="bark", environ={"BARK_TOKEN": TOKEN}, http_post=post)
    assert result["status"] == "SENT"
    assert post.calls[0][1]["title"] == "扫描 2026-09-28 FAILED"
    assert "scan.l3" in post.calls[0][1]["body"]


def test_notify_none_channel_sends_nothing(tmp_path):
    post = _Post()
    assert delivery.notify("t", "b", channel="none", environ={"BARK_TOKEN": TOKEN},
                           http_post=post)["status"] == "SKIPPED"
    assert post.calls == []


def test_default_http_post_goes_through_urlopen_with_a_tokenless_url(monkeypatch):
    seen = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, data=json.loads(request.data.decode("utf-8")),
                    method=request.get_method(), timeout=timeout)
        return _Response()

    monkeypatch.setattr(delivery.urllib.request, "urlopen", fake_urlopen)
    assert delivery._http_post("https://api.day.app/push", {"device_key": TOKEN}, 5.0) == 200
    assert seen["method"] == "POST" and TOKEN not in seen["url"]
    assert seen["data"]["device_key"] == TOKEN


def test_cli_notify_reads_channel_from_config(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(delivery, "notify", lambda title, body, **kw: calls.append(
        (title, body)) or {"status": "SKIPPED", "channel": "none"})
    assert delivery.main(["notify", "扫描 FAILED · 日志 x"]) == 0
    assert calls and calls[0][1] == "扫描 FAILED · 日志 x"


def test_notify_sh_hands_the_message_to_the_delivery_cli(tmp_path):
    """Run the real script under ``zsh -f`` with a fake ``uv`` first on PATH (no rc files,
    so the real uv can never be reached): it must exec the delivery CLI's notify verb."""
    import os
    import stat
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$UV_LOG"\n'
                    'echo "engine=$AUTORESEARCH_ENGINE" >> "$UV_LOG"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "uv.log"
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path), "UV_LOG": str(log)}
    proc = subprocess.run(["/bin/zsh", "-f", str(repo / "scripts/notify.sh"), "扫描", "FAILED · x"],
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    args = log.read_text(encoding="utf-8").splitlines()
    assert args[:6] == ["run", "--no-sync", "python", "-m", "autoresearch.scan.delivery", "notify"]
    assert args[6] == "扫描 FAILED · x" and args[-1] == "engine=claude"
    assert os.access(repo / "scripts/notify.sh", os.X_OK)


def test_no_token_literal_in_code_or_scripts():
    repo = Path(__file__).resolve().parents[2]
    for path in (repo / "autoresearch/scan/delivery.py", repo / "scripts/notify.sh"):
        text = path.read_text(encoding="utf-8")
        assert "api.day.app/" not in text.replace("api.day.app/push", ""), path
