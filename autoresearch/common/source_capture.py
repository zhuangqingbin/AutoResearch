"""Context-local supplier capture port; adapters are installed by the caller."""
from contextlib import contextmanager
from contextvars import ContextVar

from autoresearch.common import workspace as ws

_RECORDER = ContextVar("supplier_recorder", default=None)
_DEFAULT_RECORDER = None


def register_source_recorder(recorder):
    """Install the application composition root adapter; overrides stay local."""
    if not callable(recorder):
        raise TypeError("source recorder must be callable")
    global _DEFAULT_RECORDER
    _DEFAULT_RECORDER = recorder


@contextmanager
def use_source_recorder(recorder, *, if_unset=False):
    if if_unset and _RECORDER.get() is not None:
        yield
        return
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


def record_source_response(**kwargs):
    recorder = _RECORDER.get()
    if recorder is None:
        recorder = _DEFAULT_RECORDER
    if recorder is None:
        if ws.active_run_id() is not None:
            raise RuntimeError("active supplier call requires a source recorder")
        return None
    return recorder(**kwargs)
