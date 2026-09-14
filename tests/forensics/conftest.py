from __future__ import annotations

import pytest

from tests.forensic_fixtures import redirect_roots

from .support import make_case


@pytest.fixture
def forensic_case_factory(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)

    def factory(kind: str, variant: str = "business"):
        return make_case(kind, variant, tmp_path)

    return factory


@pytest.fixture
def forensic_case(forensic_case_factory, request):
    kind, variant = getattr(request, "param", ("stock-research", "business"))
    return forensic_case_factory(kind, variant)
