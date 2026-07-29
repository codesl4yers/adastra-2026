"""Pruebas del extractor de referencia (HTML).

Cubre los puntos 1, 2, 4 y 5 del enunciado. El punto 3 (determinismo byte a
byte) vive en ``test_orquestador.py``, que es quien escribe a disco.
"""

import unicodedata

import pytest

from contrato import validar_documento
from extractores import html as extractor_html

FIXTURES_HTML = [
    "bien_formado.html",
    "boilerplate.html",
    "vacio.html",
    "malformado.html",
    "anidado.html",
    "multilingue.html",
    "nfd.html",
]


@pytest.fixture(params=FIXTURES_HTML)
def documento(request, dir_fixtures):
    """Un Documento por cada fixture HTML del corpus sintético."""
    return extractor_html.extraer(dir_fixtures / request.param, fenomeno=1)


# --- punto 1: ninguna salida viola el contrato -------------------------------


def test_ninguna_salida_viola_el_contrato(documento):
    assert validar_documento(documento) == []


# --- punto 4: la fuente es el nombre exacto del archivo ----------------------


def test_fuente_es_el_nombre_exacto_del_archivo(documento, dir_fixtures):
    assert (dir_fixtures / documento.fuente).exists()


def test_fuente_conserva_la_extension_y_el_nombre_sin_normalizar(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    assert doc.fuente == "bien_formado.html"


# --- punto 2: el archivo corrupto no tumba el pipeline -----------------------


def test_archivo_malformado_no_lanza_excepcion(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "malformado.html", fenomeno=1)
    assert doc.bloques == []
    assert doc.errores != []


def test_archivo_inexistente_no_lanza_excepcion(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "no_existe.html", fenomeno=1)
    assert doc.bloques == []
    assert doc.errores != []
    assert validar_documento(doc) == []


def test_directorio_en_vez_de_archivo_no_lanza_excepcion(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures, fenomeno=1)
    assert doc.bloques == []
    assert doc.errores != []


# --- punto 5: breadcrumb en tres niveles de anidación ------------------------


def test_breadcrumb_en_documento_anidado_a_tres_niveles(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "anidado.html", fenomeno=1)
    obtenido = [(b.texto, b.tipo, b.nivel, b.ruta) for b in doc.bloques]
    assert obtenido == [
        ("Nivel Uno A", "titulo", 1, []),
        ("Parrafo bajo uno A", "parrafo", None, ["Nivel Uno A"]),
        ("Nivel Dos A", "titulo", 2, ["Nivel Uno A"]),
        ("Parrafo bajo dos A", "parrafo", None, ["Nivel Uno A", "Nivel Dos A"]),
        ("Nivel Tres A", "titulo", 3, ["Nivel Uno A", "Nivel Dos A"]),
        (
            "Parrafo bajo tres A",
            "parrafo",
            None,
            ["Nivel Uno A", "Nivel Dos A", "Nivel Tres A"],
        ),
        ("Nivel Tres B", "titulo", 3, ["Nivel Uno A", "Nivel Dos A"]),
        (
            "Parrafo bajo tres B",
            "parrafo",
            None,
            ["Nivel Uno A", "Nivel Dos A", "Nivel Tres B"],
        ),
        ("Nivel Dos B", "titulo", 2, ["Nivel Uno A"]),
        ("Parrafo bajo dos B", "parrafo", None, ["Nivel Uno A", "Nivel Dos B"]),
        ("Nivel Uno B", "titulo", 1, []),
        ("Parrafo bajo uno B", "parrafo", None, ["Nivel Uno B"]),
    ]


def test_la_ruta_de_un_bloque_no_se_comparte_entre_bloques(dir_fixtures):
    # Si el extractor guardara la misma lista mutable en todos los bloques,
    # todas las rutas acabarían siendo la última vigente.
    doc = extractor_html.extraer(dir_fixtures / "anidado.html", fenomeno=1)
    rutas = [b.ruta for b in doc.bloques]
    assert len({id(r) for r in rutas}) == len(rutas)


# --- mapeo de etiquetas ------------------------------------------------------


def test_mapea_encabezados_parrafos_y_listas(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    por_tipo = {}
    for bloque in doc.bloques:
        por_tipo.setdefault(bloque.tipo, []).append(bloque.texto)

    assert "Deforestación en la Amazonía" in por_tipo["titulo"]
    assert "Mosaicos anuales sin nubes" in por_tipo["lista"]
    assert any("cobertura boscosa" in t for t in por_tipo["parrafo"])


def test_los_niveles_corresponden_a_la_etiqueta_hn(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    niveles = {b.texto: b.nivel for b in doc.bloques if b.tipo == "titulo"}
    assert niveles["Deforestación en la Amazonía"] == 1
    assert niveles["Metodología"] == 2
    assert niveles["Fuentes de datos"] == 3


def test_html_no_tiene_paginas_ni_bloques_atomicos(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    assert all(b.pagina is None for b in doc.bloques)
    assert all(b.atomico is False for b in doc.bloques)


def test_no_duplica_el_texto_de_elementos_anidados(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    textos = [b.texto for b in doc.bloques]
    # "resolución media" está en un <span> dentro de un <p>: debe aparecer una
    # sola vez, dentro del párrafo que lo contiene.
    apariciones = [t for t in textos if "resolución media" in t]
    assert len(apariciones) == 1


# --- limpieza de marcado no visible ------------------------------------------


def test_elimina_script_style_nav_y_footer(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "boilerplate.html", fenomeno=1)
    todo = " ".join(b.texto for b in doc.bloques)
    assert "dataLayer" not in todo
    assert "font-family" not in todo
    assert "Conjuntos de datos" not in todo  # venía del <nav>
    assert "Todos los derechos reservados" not in todo  # venía del <footer>


def test_elimina_marcado_no_visible(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "boilerplate.html", fenomeno=1)
    todo = " ".join(b.texto for b in doc.bloques)
    assert "oculto por CSS" not in todo
    assert "marcado como hidden" not in todo
    assert "Decoración accesible" not in todo
    assert "Active JavaScript" not in todo


def test_elimina_texto_repetido_sin_valor_informativo(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "boilerplate.html", fenomeno=1)
    textos = [b.texto for b in doc.bloques]
    assert "Volver al inicio" not in textos


def test_conserva_el_contenido_informativo(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "boilerplate.html", fenomeno=1)
    todo = " ".join(b.texto for b in doc.bloques)
    assert "material particulado" in todo
    assert "temporada seca" in todo


# --- metadatos ---------------------------------------------------------------


def test_title_descripcion_y_canonica_van_a_meta(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    assert doc.meta["titulo"] == "Deforestación en la Amazonía colombiana"
    assert "pérdida de cobertura boscosa" in doc.meta["descripcion"]
    assert doc.meta["url"] == "https://ejemplo.gov.co/informes/deforestacion-2024"


def test_los_metadatos_nunca_aparecen_en_el_cuerpo(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    textos = [b.texto for b in doc.bloques]
    assert doc.meta["titulo"] not in textos
    assert doc.meta["descripcion"] not in textos


# --- idioma ------------------------------------------------------------------


def test_detecta_el_idioma_predominante(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    assert doc.idioma == "es"


def test_marca_en_meta_los_bloques_en_otro_idioma(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "multilingue.html", fenomeno=1)
    assert doc.idioma == "es"
    discrepantes = doc.meta["bloques_en_otro_idioma"]
    assert discrepantes, "el párrafo del abstract está en inglés"
    indices = [d["indice"] for d in discrepantes]
    assert indices == sorted(indices)
    assert any(d["idioma"] == "en" for d in discrepantes)

    marcados = {doc.bloques[d["indice"]].texto for d in discrepantes if d["idioma"] == "en"}
    assert any("average discharge" in t for t in marcados)
    # Los párrafos en español no deben quedar marcados como discrepantes.
    assert not any("caudal medio" in t for t in marcados)


# --- normalización de texto --------------------------------------------------


def test_el_texto_extraido_queda_en_nfc(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "nfd.html", fenomeno=1)
    assert doc.bloques, "el fixture NFD tiene contenido"
    for bloque in doc.bloques:
        assert bloque.texto == unicodedata.normalize("NFC", bloque.texto)
    assert doc.bloques[0].texto == "Análisis de la información"


def test_documento_sin_contenido_no_produce_bloques_ni_errores(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "vacio.html", fenomeno=1)
    assert doc.bloques == []
    assert doc.errores == []


# --- firma y pureza ----------------------------------------------------------


def test_el_extractor_declara_su_formato(dir_fixtures):
    doc = extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=2)
    assert doc.formato == "html"
    assert doc.fenomeno == 2


def test_dos_llamadas_producen_el_mismo_documento(dir_fixtures):
    ruta = dir_fixtures / "boilerplate.html"
    assert extractor_html.extraer(ruta, fenomeno=1) == extractor_html.extraer(ruta, fenomeno=1)


def test_el_extractor_no_escribe_en_disco(dir_fixtures, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    extractor_html.extraer(dir_fixtures / "bien_formado.html", fenomeno=1)
    assert list(tmp_path.iterdir()) == []
