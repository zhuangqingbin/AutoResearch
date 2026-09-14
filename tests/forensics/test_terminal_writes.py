from __future__ import annotations

import pytest

from .support import KINDS


@pytest.mark.parametrize("forensic_case", [(kind, "business") for kind in KINDS], indirect=True)
def test_terminal_run_cannot_create_another_report(forensic_case):
    forensic_case.finish()
    before = forensic_case.snapshot_persistent_tree()

    with pytest.raises(RuntimeError, match="RUN_NOT_ACTIVE"):
        forensic_case.produce_changed_report()

    assert forensic_case.snapshot_persistent_tree() == before
