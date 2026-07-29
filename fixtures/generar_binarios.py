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


def main() -> None:
    (AQUI / "malformado.html").write_bytes(MALFORMADO)
    # Se normaliza aquí y no en el literal: así el fixture queda en NFD
    # aunque este archivo fuente se guarde en NFC.
    (AQUI / "nfd.html").write_bytes(unicodedata.normalize("NFD", NFD).encode("utf-8"))
    print("fixtures binarios regenerados en", AQUI)


if __name__ == "__main__":
    main()
