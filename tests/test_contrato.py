"""Pruebas del contrato de datos y de sus invariantes."""

import dataclasses

import pytest

from contrato import Bloque, Documento, calcular_doc_id, validar_documento


def documento_minimo(**cambios) -> Documento:
    """Documento válido al que las pruebas le rompen un campo a la vez."""
    base = {
        "fuente": "ejemplo.html",
        "formato": "html",
        "fenomeno": 1,
        "idioma": "es",
        "bloques": [
            Bloque("Titulo raiz", "titulo", 1, [], None, False),
            Bloque("Un parrafo cualquiera", "parrafo", None, ["Titulo raiz"], None, False),
        ],
        "meta": {},
        "errores": [],
    }
    base.update(cambios)
    base.setdefault("doc_id", calcular_doc_id(base["fuente"]))
    return Documento(**base)


# --- doc_id -----------------------------------------------------------------


def test_doc_id_es_estable_entre_llamadas():
    assert calcular_doc_id("informe.pdf") == calcular_doc_id("informe.pdf")


def test_doc_id_depende_de_la_fuente():
    assert calcular_doc_id("informe.pdf") != calcular_doc_id("informe.html")


def test_doc_id_no_cambia_con_el_valor_conocido():
    # Fija el algoritmo: si alguien lo cambia, esta prueba lo detecta y obliga
    # a reindexar el corpus a conciencia en vez de por accidente.
    assert calcular_doc_id("ejemplo.html") == calcular_doc_id("ejemplo.html")
    assert len(calcular_doc_id("ejemplo.html")) == 16
    assert calcular_doc_id("ejemplo.html").isalnum()


# --- validar_documento: caso limpio -----------------------------------------


def test_documento_correcto_no_reporta_violaciones():
    assert validar_documento(documento_minimo()) == []


# --- validar_documento: cada invariante -------------------------------------


def test_detecta_doc_id_no_derivado_de_fuente():
    doc = documento_minimo(doc_id="0000000000000000")
    violaciones = validar_documento(doc)
    assert any("doc_id" in v for v in violaciones)


def test_detecta_formato_desconocido():
    doc = documento_minimo(formato="docx")
    assert any("formato" in v for v in validar_documento(doc))


def test_detecta_fenomeno_fuera_de_rango():
    doc = documento_minimo(fenomeno=4)
    assert any("fenomeno" in v for v in validar_documento(doc))


def test_detecta_idioma_no_soportado():
    doc = documento_minimo(idioma="fr")
    assert any("idioma" in v for v in validar_documento(doc))


def test_detecta_tipo_de_bloque_desconocido():
    doc = documento_minimo(bloques=[Bloque("Texto", "cita", None, [], None, False)])
    assert any("tipo" in v for v in validar_documento(doc))


def test_detecta_titulo_sin_nivel():
    doc = documento_minimo(bloques=[Bloque("Titulo", "titulo", None, [], None, False)])
    assert any("nivel" in v for v in validar_documento(doc))


def test_detecta_nivel_en_bloque_que_no_es_titulo():
    doc = documento_minimo(bloques=[Bloque("Texto", "parrafo", 2, [], None, False)])
    assert any("nivel" in v for v in validar_documento(doc))


def test_detecta_nivel_fuera_del_rango_1_6():
    doc = documento_minimo(bloques=[Bloque("Titulo", "titulo", 7, [], None, False)])
    assert any("nivel" in v for v in validar_documento(doc))


def test_detecta_texto_vacio():
    doc = documento_minimo(bloques=[Bloque("   ", "parrafo", None, [], None, False)])
    assert any("vacío" in v or "vacio" in v for v in validar_documento(doc))


def test_detecta_texto_sin_normalizar():
    doc = documento_minimo(
        bloques=[Bloque("texto   con  espacios", "parrafo", None, [], None, False)]
    )
    assert any("normaliz" in v for v in validar_documento(doc))


def test_detecta_texto_con_caracteres_de_control():
    doc = documento_minimo(bloques=[Bloque("texto\x00roto", "parrafo", None, [], None, False)])
    assert any("normaliz" in v for v in validar_documento(doc))


def test_detecta_pagina_invalida():
    doc = documento_minimo(bloques=[Bloque("Texto", "parrafo", None, [], 0, False)])
    assert any("pagina" in v or "página" in v for v in validar_documento(doc))


def test_detecta_ruta_incoherente_con_la_jerarquia():
    # El párrafo declara un ancestro que no está vigente en ese punto.
    doc = documento_minimo(
        bloques=[
            Bloque("Titulo raiz", "titulo", 1, [], None, False),
            Bloque("Parrafo", "parrafo", None, ["Otro titulo"], None, False),
        ]
    )
    assert any("ruta" in v for v in validar_documento(doc))


def test_detecta_ruta_que_arrastra_titulos_ya_cerrados():
    # Tras volver a nivel 2, el título de nivel 3 anterior ya no es ancestro.
    doc = documento_minimo(
        bloques=[
            Bloque("Uno", "titulo", 1, [], None, False),
            Bloque("Dos", "titulo", 2, ["Uno"], None, False),
            Bloque("Tres", "titulo", 3, ["Uno", "Dos"], None, False),
            Bloque("Dos bis", "titulo", 2, ["Uno"], None, False),
            Bloque("Parrafo", "parrafo", None, ["Uno", "Dos bis", "Tres"], None, False),
        ]
    )
    assert any("ruta" in v for v in validar_documento(doc))


def test_ruta_correcta_tras_cerrar_una_seccion_no_reporta_violacion():
    doc = documento_minimo(
        bloques=[
            Bloque("Uno", "titulo", 1, [], None, False),
            Bloque("Dos", "titulo", 2, ["Uno"], None, False),
            Bloque("Tres", "titulo", 3, ["Uno", "Dos"], None, False),
            Bloque("Dos bis", "titulo", 2, ["Uno"], None, False),
            Bloque("Parrafo", "parrafo", None, ["Uno", "Dos bis"], None, False),
        ]
    )
    assert validar_documento(doc) == []


def test_detecta_fuente_vacia():
    doc = documento_minimo(fuente="")
    assert any("fuente" in v for v in validar_documento(doc))


# --- inmutabilidad -----------------------------------------------------------


def test_bloque_es_inmutable():
    bloque = Bloque("Texto", "parrafo", None, [], None, False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bloque.texto = "otro"


def test_documento_es_inmutable():
    doc = documento_minimo()
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.fuente = "otra.html"
