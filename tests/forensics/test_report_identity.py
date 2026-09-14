from __future__ import annotations

import pytest

from .support import KINDS


@pytest.mark.parametrize("variant", ["generated_at", "business"])
@pytest.mark.parametrize("kind", KINDS)
def test_unsealed_report_is_never_accepted_as_the_frozen_version(
    forensic_case_factory,
    kind,
    variant,
):
    case = forensic_case_factory(kind, variant)
    case.finish()
    late_report = case.produce_changed_report()

    result = case.verify_report(late_report)

    assert result["integrity_ok"] is False
    assert result["root_ledger_ok"] is False
