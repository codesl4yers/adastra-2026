"""Regenera los fixtures binarios que no se pueden escribir a mano en un editor de texto.

Los demás fixtures de este directorio son texto plano normal y se editan
directamente. Este necesita control byte a byte: ``indice_minimo.xlsx`` es un
libro de Excel real, no algo que se pueda escribir como texto.

Uso::

    python fixtures/generar_binarios.py
"""

from pathlib import Path

import openpyxl

AQUI = Path(__file__).parent


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
    ("F1", "AI_Index_Stanford", "AIINDEX", "F1-AIINDEX-001", "bien_formado.txt", "", "TXT"),
    ("F2", "Secure_World", "SWF", "F2-SWF-001", "informe.txt", "colisiones/a", "TXT"),
    ("F2", "Secure_World", "SWF", "F2-SWF-002", "informe.txt", "colisiones/b", "TXT"),
    ("F3", "MAPP_OEA", "MAPP", "F3-MAPP-001", "anidado.txt", "", "TXT"),
)

TXT_COLISION_A = "Informe de la carpeta A\n\nContenido del primer informe homónimo.\n"
TXT_COLISION_B = "Informe de la carpeta B\n\nContenido del segundo informe homónimo.\n"


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
    for subdir, contenido in (("a", TXT_COLISION_A), ("b", TXT_COLISION_B)):
        carpeta = raiz / subdir
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "informe.txt").write_text(contenido, encoding="utf-8", newline="\n")


# --- PDF mínimo ---------------------------------------------------------------
#
# Se escribe a mano y no con una librería de generación para no añadir una
# dependencia solo por un fixture. Trae lo justo para ejercitar el extractor:
# un título en cuerpo 20, un cuerpo en 10 y una segunda página, de modo que se
# pueda comprobar la jerarquía por tamaño de fuente y la numeración de páginas.

_TEXTO_PDF_PAGINA_1 = b"""BT /F1 20 Tf 72 720 Td (Informe de prueba) Tj ET
BT /F1 10 Tf 72 690 Td (El informe anual describe la cobertura de sensores en la region.) Tj ET
BT /F1 10 Tf 72 678 Td (La red actual deja fuera el hemisferio sur, donde hace mas falta.) Tj ET
BT /F1 14 Tf 72 640 Td (Metodologia) Tj ET
BT /F1 10 Tf 72 620 Td (Se revisaron catorce fuentes primarias y ocho informes anuales.) Tj ET"""

_TEXTO_PDF_PAGINA_2 = b"""BT /F1 10 Tf 72 720 Td (La segunda pagina continua el analisis con datos de 2024.) Tj ET"""


def _objeto_pdf(numero: int, cuerpo: bytes) -> bytes:
    return b"%d 0 obj\n%s\nendobj\n" % (numero, cuerpo)


def _escribir_pdf_minimo(destino: Path) -> None:
    """Construye un PDF 1.4 válido, con su tabla xref bien calculada.

    La tabla xref lleva el desplazamiento en bytes de cada objeto, así que hay
    que ir midiendo conforme se escribe: un offset mal puesto produce un archivo
    que algunos lectores abren y otros rechazan, que es la peor clase de
    fixture.
    """
    contenidos = [_TEXTO_PDF_PAGINA_1, _TEXTO_PDF_PAGINA_2]
    objetos = [
        _objeto_pdf(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _objeto_pdf(2, b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>"),
        _objeto_pdf(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        ),
        _objeto_pdf(
            4,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        ),
        _objeto_pdf(
            5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        ),
    ]
    for numero, contenido in enumerate(contenidos, start=6):
        objetos.append(
            _objeto_pdf(
                numero, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(contenido), contenido)
            )
        )

    salida = bytearray(b"%PDF-1.4\n")
    desplazamientos = []
    for objeto in objetos:
        desplazamientos.append(len(salida))
        salida += objeto

    inicio_xref = len(salida)
    salida += b"xref\n0 %d\n" % (len(objetos) + 1)
    salida += b"0000000000 65535 f \n"
    for desplazamiento in desplazamientos:
        salida += b"%010d 00000 n \n" % desplazamiento
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1,
        inicio_xref,
    )

    destino.write_bytes(bytes(salida))


def main() -> None:
    _escribir_indice(AQUI / "indice_minimo.xlsx")
    _escribir_colisiones(AQUI / "colisiones")
    _escribir_pdf_minimo(AQUI / "minimo.pdf")
    print("fixtures binarios regenerados en", AQUI)


if __name__ == "__main__":
    main()
