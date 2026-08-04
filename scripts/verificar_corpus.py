"""Comprueba los criterios de aceptación contra el corpus real de ADL.

No es una prueba de pytest: depende de un corpus de 1826 archivos que no vive
en el repositorio. Se corre a mano antes de una entrega.

Uso::

    python scripts/verificar_corpus.py --corpus c:/Users/jesus/projects/base_documental_codefest
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from indice import cargar_indice  # noqa: E402
from orquestador import cargar_manifiesto, procesar_corpus  # noqa: E402

ESPERADO_TOTAL = 1826
ESPERADO_POR_FENOMENO = {1: 459, 2: 479, 3: 888}
ESPERADO_NOMBRES_AMBIGUOS = 59
ESPERADO_ARCHIVOS_AMBIGUOS = 186

# 9 archivos ".DS_Store" (residuo que macOS Finder deja en cualquier carpeta
# que se navegue) más 1 ".zip" de 3.1 GB (empaquetado del corpus completo, no
# un documento individual de la entrega). Que ninguno de los 10 tenga
# extractor registrado es lo correcto y lo deseable: no son documentos de
# ADL, son ruido del sistema de archivos y un archivo contenedor. No es una
# tolerancia arbitraria ni un fallo pendiente de arreglar.
ESPERADO_SIN_EXTRACTOR = 10


def _forzar_utf8(flujo):
    """Evita que un ``print`` con tildes reviente al redirigir la salida.

    La consola de Windows suele hablar en una página de códigos que no cubre
    los acentos del español (cp1252, cp850...). Mientras se imprime a una
    consola interactiva, Python suele arreglárselas; en cuanto la salida se
    redirige a un archivo o a una tubería, el códec cambia y un simple
    ``print("fenómeno")`` puede lanzar ``UnicodeEncodeError`` a mitad de la
    verificación. Reconfigurar a UTF-8 con reemplazo de lo no representable
    hace que el script nunca aborte por esto, aquí ni en la consola de quien
    lo corra.
    """
    reconfigure = getattr(flujo, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass  # Flujo que no admite reconfigurar (p. ej. ya cerrado): seguir sin romper.


def comprobar(titulo: str, obtenido, esperado) -> bool:
    ok = obtenido == esperado
    marca = "[OK]  " if ok else "[FALLA]"
    print(f"{marca} {titulo}: {obtenido}" + ("" if ok else f"  (esperado {esperado})"))
    return ok


def main(argv: list[str] | None = None) -> int:
    _forzar_utf8(sys.stdout)
    _forzar_utf8(sys.stderr)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--salida", type=Path, default=RAIZ / "extraidos")
    args = parser.parse_args(argv)

    xlsx = args.corpus / "Indice_Datos_Codefest.xlsx"
    indice = cargar_indice(xlsx)

    resultados = [comprobar("entradas del índice", len(indice), ESPERADO_TOTAL)]

    primera = args.salida
    documentos, reporte = procesar_corpus(
        args.corpus, primera, limpiar=True, indice=indice
    )

    manifiesto = cargar_manifiesto(primera / "manifiesto.jsonl")
    por_fenomeno = dict(sorted(collections.Counter(e["fenomeno"] for e in manifiesto).items()))
    doc_ids = [e["doc_id"] for e in manifiesto]
    ambiguos = [e for e in manifiesto if e["fuente_ambigua"]]
    nombres_ambiguos = {e["fuente"] for e in ambiguos}

    resultados += [
        comprobar("líneas del manifiesto", len(manifiesto), ESPERADO_TOTAL),
        comprobar("conteo por fenómeno", por_fenomeno, ESPERADO_POR_FENOMENO),
        comprobar("doc_id únicos", len(set(doc_ids)), ESPERADO_TOTAL),
        comprobar(
            "doc_id con formato de ADL",
            sum(1 for d in doc_ids if d.startswith(("F1-", "F2-", "F3-"))),
            ESPERADO_TOTAL,
        ),
        comprobar("documentos ambiguos", len(ambiguos), ESPERADO_ARCHIVOS_AMBIGUOS),
        comprobar("nombres ambiguos", len(nombres_ambiguos), ESPERADO_NOMBRES_AMBIGUOS),
        comprobar("archivos sin extractor", len(reporte.sin_extractor), ESPERADO_SIN_EXTRACTOR),
        comprobar("entradas huérfanas del índice", len(reporte.huerfanos_del_indice), 0),
        comprobar("todos con observatorio", sum(1 for e in manifiesto if e["observatorio"]), ESPERADO_TOTAL),
    ]

    # Determinismo: segunda corrida en otro directorio, diff byte a byte.
    segunda = primera.parent / f"{primera.name}_bis"
    procesar_corpus(args.corpus, segunda, limpiar=True, indice=indice)
    iguales = (primera / "manifiesto.jsonl").read_bytes() == (
        segunda / "manifiesto.jsonl"
    ).read_bytes()
    resultados.append(comprobar("dos corridas dan el mismo manifiesto", iguales, True))
    shutil.rmtree(segunda)

    print()
    print(f"archivos fuera del índice (informativo): {len(reporte.fuera_del_indice)}")
    for ruta in reporte.fuera_del_indice:
        print(f"    {ruta}")

    print()
    if all(resultados):
        print("TODOS LOS CRITERIOS SE CUMPLEN")
        return 0
    print(f"{sum(1 for r in resultados if not r)} criterios fallan")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
