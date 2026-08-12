"""Pruebas de la capa de OCR.

Tesseract es un binario del sistema y puede no estar instalado. Las pruebas que
lo necesitan se saltan solas; las que comprueban la degradación cuando falta
corren siempre, que son justo las que importan mientras no esté.
"""

import pytest

from extractores import ocr


def test_saber_si_hay_ocr_no_lanza_nunca():
    """Se llama en el camino caliente de PDF e imagen: no puede reventar."""
    assert isinstance(ocr.hay_ocr(), bool)


def test_sin_ocr_el_texto_sale_vacio_y_sin_confianza(monkeypatch):
    monkeypatch.setattr(ocr, "hay_ocr", lambda: False)
    texto, confianza = ocr.texto_de_imagen(object())

    assert texto == ""
    assert confianza == 0.0


def test_el_motivo_explica_que_falta_tesseract(monkeypatch):
    monkeypatch.setattr(ocr, "hay_ocr", lambda: False)
    assert "tesseract" in ocr.motivo_sin_ocr().lower()


def test_los_idiomas_son_los_tres_del_contrato():
    """Fijarlos importa: cambiarlos cambia el texto reconocido."""
    assert ocr.IDIOMAS == "spa+eng+por"


def test_la_configuracion_de_tesseract_esta_fijada():
    """Mismo psm y mismo oem en todas las corridas, o el texto no es reproducible."""
    assert "--psm" in ocr.CONFIGURACION
    assert "--oem" in ocr.CONFIGURACION


def test_descarta_las_lineas_por_debajo_del_umbral_de_confianza():
    """El OCR nunca falla: ante una imagen sin texto devuelve basura plausible."""
    datos = {
        "text": ["Informe", "anual", "|,-.", "l1"],
        "conf": ["96", "94", "12", "8"],
        "line_num": [1, 1, 2, 2],
        "block_num": [1, 1, 2, 2],
        "par_num": [1, 1, 1, 1],
    }
    lineas = ocr.lineas_fiables(datos, umbral=60.0)

    assert lineas == [("Informe anual", pytest.approx(95.0))]


def test_una_linea_con_una_palabra_dudosa_sobrevive_si_la_media_es_buena():
    datos = {
        "text": ["El", "informe", "anual", "xz"],
        "conf": ["98", "97", "96", "20"],
        "line_num": [1, 1, 1, 1],
        "block_num": [1, 1, 1, 1],
        "par_num": [1, 1, 1, 1],
    }
    assert len(ocr.lineas_fiables(datos, umbral=60.0)) == 1


def test_ignora_las_palabras_sin_confianza():
    """Tesseract emite filas con conf=-1 para los separadores de bloque."""
    datos = {
        "text": ["", "Informe"],
        "conf": ["-1", "95"],
        "line_num": [1, 1],
        "block_num": [1, 1],
        "par_num": [1, 1],
    }
    assert ocr.lineas_fiables(datos, umbral=60.0) == [("Informe", pytest.approx(95.0))]


def test_sin_datos_no_hay_lineas():
    vacio = {"text": [], "conf": [], "line_num": [], "block_num": [], "par_num": []}
    assert ocr.lineas_fiables(vacio, umbral=60.0) == []
