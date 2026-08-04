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


@pytest.fixture(scope="session")
def indice_minimo(dir_fixtures) -> Path:
    """Índice de 4 filas con la misma forma que el de ADL."""
    return dir_fixtures / "indice_minimo.xlsx"
