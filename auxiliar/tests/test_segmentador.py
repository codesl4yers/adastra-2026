"""Pruebas del segmentador de oraciones.

Cubre §3 del spec del fragmentador: la frontera oracional es el átomo de todo
el sistema y §3.3 del enunciado prohíbe que una oración cruce de un fragmento a
otro. Si esto falla, todos los fragmentos afectados violan un requisito
obligatorio, así que es el componente con más pruebas por línea de código.
"""

import json
from pathlib import Path

import pytest

from limpieza import normalizar_texto
from segmentador import segmentar

# --- conjunto dorado (§3.2) ---------------------------------------------------


def _cargar_dorados():
    """Lee ``fixtures/oraciones_doradas.jsonl``.

    Se lee en tiempo de importación, no con una fixture de pytest, porque
    ``parametrize`` necesita los casos antes de que exista la sesión.
    """
    ruta = Path(__file__).resolve().parent.parent / "fixtures" / "oraciones_doradas.jsonl"
    contenido = ruta.read_text(encoding="utf-8")
    return [json.loads(linea) for linea in contenido.splitlines() if linea.strip()]


DORADOS = _cargar_dorados()


def test_el_conjunto_dorado_tiene_al_menos_60_casos():
    assert len(DORADOS) >= 60


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_hay_al_menos_20_casos_por_idioma(idioma):
    assert len([c for c in DORADOS if c["idioma"] == idioma]) >= 20


def test_los_identificadores_del_conjunto_dorado_son_unicos():
    ids = [caso["id"] for caso in DORADOS]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("caso", DORADOS, ids=lambda c: c["id"])
def test_conjunto_dorado(caso):
    """Cada caso etiquetado a mano se segmenta como dice su etiqueta.

    Un caso que el segmentador falla no se borra: se documenta en el propio
    JSONL con el motivo en ``excepcion`` y aquí sale como xfail. Borrarlo
    escondería una debilidad conocida del segmentador.
    """
    if caso["excepcion"]:
        pytest.xfail(caso["excepcion"])
    assert segmentar(caso["texto"], caso["idioma"]) == caso["oraciones"]


@pytest.mark.parametrize("caso", DORADOS, ids=lambda c: c["id"])
def test_las_oraciones_reconstruyen_el_texto(caso):
    """Invariante que no admite excepciones, ni siquiera en los casos xfail.

    Segmentar no puede perder ni inventar caracteres: si la unión de las
    oraciones no devuelve el texto de partida, el fragmentador acabaría
    reportando al jurado un texto que no está en el documento.
    """
    oraciones = segmentar(caso["texto"], caso["idioma"])
    assert " ".join(oraciones) == normalizar_texto(caso["texto"])


# --- comportamiento del segmentador -------------------------------------------


@pytest.mark.parametrize("texto", ["", "   ", "\n\t"])
def test_un_texto_sin_contenido_no_produce_oraciones(texto):
    assert segmentar(texto, "es") == []


def test_las_oraciones_no_llevan_espacios_en_los_extremos():
    oraciones = segmentar("Primera oración.   Segunda oración.", "es")
    assert oraciones == ["Primera oración.", "Segunda oración."]


def test_el_texto_se_normaliza_antes_de_segmentar():
    """El fragmentador puede recibir texto de un extractor recién escrito."""
    assert segmentar("Uno.\n\nDos.", "es") == ["Uno.", "Dos."]


def test_dos_llamadas_devuelven_exactamente_lo_mismo():
    texto = "El Art. 5 rige. La Dra. Gómez firmó. Se revisa en 2027."
    assert segmentar(texto, "es") == segmentar(texto, "es")


def test_un_idioma_fuera_del_contrato_cae_a_espanol():
    """El contrato solo admite es/en/pt, pero el fragmentador no debe reventar."""
    texto = "El Art. 5 establece el marco. La revisión es anual."
    assert segmentar(texto, "fr") == segmentar(texto, "es")


# --- §7.16: cada idioma con su propio segmentador -----------------------------


def test_el_portugues_usa_sus_propias_abreviaturas():
    """``p.ex.`` es portugués; en inglés no significa nada y se corta."""
    texto = "Vários órgãos, p.ex. INPE e AEB, aderiram ao acordo. O financiamento é incerto."
    assert len(segmentar(texto, "pt")) == 2
    assert len(segmentar(texto, "en")) > 2


def test_el_ingles_usa_sus_propias_abreviaturas():
    """``approx.`` es inglés; en español no está en la lista y se corta."""
    texto = "The array spans approx. 40 km. Coverage is continuous."
    assert len(segmentar(texto, "en")) == 2
    assert len(segmentar(texto, "es")) > 2


def test_una_oracion_muy_larga_sale_entera():
    """El segmentador no parte por longitud: eso es trabajo del empaquetado."""
    texto = " ".join(["palabra"] * 400) + "."
    assert segmentar(texto, "es") == [texto]
