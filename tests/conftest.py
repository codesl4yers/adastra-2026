"""Fixtures compartidas por la suite."""

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def dir_fixtures() -> Path:
    """Directorio con el corpus sintético."""
    return RAIZ / "fixtures"


@pytest.fixture(scope="session")
def raiz_proyecto() -> Path:
    return RAIZ
