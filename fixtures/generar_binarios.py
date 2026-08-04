"""Regenera los fixtures que no se pueden escribir a mano en un editor de texto.

Los demás fixtures de este directorio son HTML normal y se editan directamente.
Estos dos necesitan control byte a byte:

- ``malformado.html``: simula una descarga truncada, con basura binaria y bytes
  NUL en medio del marcado. Sirve para comprobar que el extractor devuelve un
  ``Documento`` válido con ``bloques=[]`` y el error registrado, sin excepción.
- ``nfd.html``: texto en español con acentos en forma descompuesta (NFD).
  Sirve para comprobar que la limpieza normaliza a NFC.

Uso::

    python fixtures/generar_binarios.py
"""

import unicodedata
from pathlib import Path

import openpyxl

AQUI = Path(__file__).parent

# Marcado plausible cortado a media etiqueta, con un bloque binario incrustado.
MALFORMADO = (
    b"<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n<title>Informe parcial</title>\n"
    b"</head>\n<body>\n<h1>Informe de\x00\x00 monitoreo</h1>\n"
    b"<p>Registro \xff\xfe\x00\x01\x02 truncado por fallo de transferencia</p>\n"
    b"<div class=\"seccio"
)

# "informacion" y "analisis" con tilde descompuesta: o + U+0301, a + U+0301.
NFD = (
    "<!DOCTYPE html>\n"
    "<html lang=\"es\">\n"
    "<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<title>Información en forma descompuesta</title>\n"
    "</head>\n"
    "<body>\n"
    "<h1>Análisis de la información</h1>\n"
    "<p>El informe recopila la información de las estaciones hidrológicas.</p>\n"
    "</body>\n"
    "</html>\n"
)


# Índice mínimo con la misma forma que el de ADL: mismas columnas, mismo orden,
# un fenómeno distinto por fila y dos filas que comparten nombre de archivo en
# carpetas distintas. Reproduce en pequeño la colisión real del corpus.
CABECERA_INDICE = (
    "Fenómeno",
    "Observatorio",
    "Código Observatorio",
    "DOC_ID",
    "Nombre estandarizado",
    "Carpeta",
    "Tipo",
)

FILAS_INDICE = (
    ("F1", "AI_Index_Stanford", "AIINDEX", "F1-AIINDEX-001", "bien_formado.html", "", "HTML"),
    ("F2", "Secure_World", "SWF", "F2-SWF-001", "informe.html", "colisiones/a", "HTML"),
    ("F2", "Secure_World", "SWF", "F2-SWF-002", "informe.html", "colisiones/b", "HTML"),
    ("F3", "MAPP_OEA", "MAPP", "F3-MAPP-001", "anidado.html", "", "HTML"),
)

HTML_COLISION_A = (
    "<html lang=\"es\"><body><h1>Informe de la carpeta A</h1>"
    "<p>Contenido del primer informe homónimo.</p></body></html>\n"
)

HTML_COLISION_B = (
    "<html lang=\"es\"><body><h1>Informe de la carpeta B</h1>"
    "<p>Contenido del segundo informe homónimo.</p></body></html>\n"
)


def _escribir_indice(destino: Path) -> None:
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Inventario de Archivos"
    hoja.append(list(CABECERA_INDICE))
    for fila in FILAS_INDICE:
        hoja.append(list(fila))
    libro.save(destino)
    libro.close()


def _escribir_colisiones(raiz: Path) -> None:
    for subdir, contenido in (("a", HTML_COLISION_A), ("b", HTML_COLISION_B)):
        carpeta = raiz / subdir
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "informe.html").write_text(contenido, encoding="utf-8", newline="\n")


def main() -> None:
    (AQUI / "malformado.html").write_bytes(MALFORMADO)
    # Se normaliza aquí y no en el literal: así el fixture queda en NFD
    # aunque este archivo fuente se guarde en NFC.
    (AQUI / "nfd.html").write_bytes(unicodedata.normalize("NFD", NFD).encode("utf-8"))
    _escribir_indice(AQUI / "indice_minimo.xlsx")
    _escribir_colisiones(AQUI / "colisiones")
    print("fixtures binarios regenerados en", AQUI)


if __name__ == "__main__":
    main()
