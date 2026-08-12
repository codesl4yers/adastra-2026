"""Pruebas del extractor de imágenes por OCR."""

import pytest

from contrato import validar_documento
from extractores import imagen

PIL = pytest.importorskip("PIL", reason="Pillow no está instalado")
from PIL import Image  # noqa: E402


def escribir_imagen(tmp_path, nombre="grafico.jpg", tamano=(120, 80), color=(255, 255, 255)):
    destino = tmp_path / nombre
    Image.new("RGB", tamano, color).save(destino)
    return destino


# --- metadata sin mirar los píxeles -------------------------------------------


def test_las_dimensiones_van_a_meta(tmp_path):
    """En un corpus satelital, el tamaño y el EXIF valen más que el propio OCR."""
    documento = imagen.extraer(escribir_imagen(tmp_path, tamano=(320, 200)), fenomeno=2)

    assert documento.meta["ancho"] == 320
    assert documento.meta["alto"] == 200
    assert documento.meta["formato_imagen"] == "JPEG"


def test_el_documento_es_valido_aunque_no_haya_texto(tmp_path):
    documento = imagen.extraer(escribir_imagen(tmp_path), fenomeno=2)
    assert validar_documento(documento) == []


def test_una_imagen_sin_texto_no_produce_bloques_de_basura(tmp_path, monkeypatch):
    """El OCR nunca falla: ante una imagen en blanco devuelve basura plausible."""
    monkeypatch.setattr(imagen.ocr, "hay_ocr", lambda: True)
    monkeypatch.setattr(imagen.ocr, "texto_de_imagen", lambda _: ("", 0.0))
    monkeypatch.setattr(imagen.ocr, "version", lambda: "5.3.0")

    documento = imagen.extraer(escribir_imagen(tmp_path), fenomeno=2)

    assert documento.bloques == []
    assert documento.errores != []


# --- OCR ------------------------------------------------------------------------


def test_el_texto_reconocido_sale_como_bloques_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(imagen.ocr, "hay_ocr", lambda: True)
    monkeypatch.setattr(
        imagen.ocr, "texto_de_imagen", lambda _: ("Capacidades por país\nColombia 3", 91.5)
    )
    monkeypatch.setattr(imagen.ocr, "version", lambda: "5.3.0")

    documento = imagen.extraer(escribir_imagen(tmp_path), fenomeno=2)

    assert [b.tipo for b in documento.bloques] == ["ocr", "ocr"]
    assert documento.bloques[0].texto == "Capacidades por país"


def test_la_confianza_media_queda_registrada(tmp_path, monkeypatch):
    """Para poder auditar después qué entró al índice con mala calidad."""
    monkeypatch.setattr(imagen.ocr, "hay_ocr", lambda: True)
    monkeypatch.setattr(imagen.ocr, "texto_de_imagen", lambda _: ("Texto reconocido", 91.5))
    monkeypatch.setattr(imagen.ocr, "version", lambda: "5.3.0")

    documento = imagen.extraer(escribir_imagen(tmp_path), fenomeno=2)

    assert documento.meta["confianza_ocr"] == 91.5
    assert documento.meta["version_tesseract"] == "5.3.0"


def test_sin_tesseract_lo_dice_en_vez_de_fallar(tmp_path, monkeypatch):
    monkeypatch.setattr(imagen.ocr, "hay_ocr", lambda: False)

    documento = imagen.extraer(escribir_imagen(tmp_path), fenomeno=2)

    assert documento.bloques == []
    assert any("tesseract" in e.lower() for e in documento.errores)
    assert documento.meta["requiere_ocr"] is True


# --- robustez -------------------------------------------------------------------


def test_un_archivo_que_no_es_una_imagen_no_lanza(tmp_path):
    ruta = tmp_path / "falso.jpg"
    ruta.write_bytes(b"esto no es una imagen")

    documento = imagen.extraer(ruta, fenomeno=2)

    assert documento.bloques == []
    assert documento.errores != []


def test_el_avif_del_corpus_se_intenta_abrir(dir_corpus):
    """Un solo archivo del corpus lo usa (F2-SWF-065)."""
    avif = list(dir_corpus.rglob("*.avif"))
    if not avif:
        pytest.skip("el corpus real no tiene .avif")

    documento = imagen.extraer(avif[0], fenomeno=2)

    assert validar_documento(documento) == []
    assert documento.formato == "imagen"


def test_dos_extracciones_son_identicas(tmp_path):
    ruta = escribir_imagen(tmp_path)
    assert imagen.extraer(ruta, fenomeno=2) == imagen.extraer(ruta, fenomeno=2)


def test_las_extensiones_registradas_incluyen_las_del_corpus():
    assert ".jpg" in imagen.EXTENSIONES
    assert ".avif" in imagen.EXTENSIONES
