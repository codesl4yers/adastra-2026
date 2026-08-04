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


def main() -> None:
    _escribir_indice(AQUI / "indice_minimo.xlsx")
    _escribir_colisiones(AQUI / "colisiones")
    print("fixtures binarios regenerados en", AQUI)


if __name__ == "__main__":
    main()
