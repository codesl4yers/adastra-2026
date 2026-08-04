"""Fixtures compartidas por la suite."""

from pathlib import Path

import pytest

from contrato import Bloque, Documento, calcular_doc_id

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


# --- construcción de documentos para la capa de fragmentación ------------------
#
# Los extractores son stubs, así que la fragmentación se prueba contra
# ``Documento`` armados a mano. Es viable porque el contrato es estable, pero
# hasta que exista un extractor real la capa no queda validada sobre texto de
# verdad (§8.2 del spec del fragmentador).


def bloque(
    texto: str,
    tipo: str = "parrafo",
    nivel: int | None = None,
    ruta: list[str] | None = None,
    pagina: int | None = None,
    atomico: bool = False,
) -> Bloque:
    """``Bloque`` con los valores por defecto del caso más común: prosa suelta."""
    return Bloque(
        texto=texto,
        tipo=tipo,
        nivel=nivel,
        ruta=list(ruta) if ruta else [],
        pagina=pagina,
        atomico=atomico,
    )


@pytest.fixture
def documento_con_bloques():
    """Devuelve un constructor de ``Documento`` a partir de bloques sueltos.

    Es una fábrica y no un documento fijo porque cada prueba de fragmentación
    necesita una forma distinta —una sección, tres secciones, filas atómicas—
    y compartir un único documento obligaría a que las pruebas se leyeran
    unas a otras para saber qué contiene.
    """

    def constructor(*bloques: Bloque, **cambios) -> Documento:
        campos = {
            "fuente": "informe.pdf",
            "formato": "pdf",
            "fenomeno": 1,
            "idioma": "es",
            "meta": {"ruta_relativa": "F1_Observatorio/informe.pdf"},
            "errores": [],
        }
        campos.update(cambios)
        campos.setdefault("doc_id", calcular_doc_id(campos["fuente"]))
        return Documento(bloques=list(bloques), **campos)

    return constructor
