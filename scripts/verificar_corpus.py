"""Comprueba los criterios de aceptación contra el corpus real de ADL.

No es una prueba de pytest: depende de un corpus de 1826 archivos que no vive
en el repositorio. Se corre a mano antes de una entrega.

Uso::

    python scripts/verificar_corpus.py --corpus c:/Users/jesus/projects/base_documental_codefest
"""

from __future__ import annotations

import argparse
import collections

import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from indice import cargar_indice  # noqa: E402
from contrato import validar_documento  # noqa: E402
from orquestador import cargar_manifiesto, procesar_corpus  # noqa: E402

ESPERADO_TOTAL = 1826
ESPERADO_POR_FENOMENO = {1: 459, 2: 479, 3: 888}
ESPERADO_NOMBRES_AMBIGUOS = 59
ESPERADO_ARCHIVOS_AMBIGUOS = 186

# 9 ".DS_Store" y el .zip de 3,1 GB con el corpus empaquetado. Que ninguno tenga
# extractor es lo correcto: no son documentos de ADL.
ESPERADO_SIN_EXTRACTOR = 10


def _forzar_utf8(flujo):
    """Evita que un ``print`` con tildes reviente al redirigir la salida.

    Redirigida a un archivo, la consola de Windows cambia de códec y un
    ``print("fenómeno")`` puede lanzar ``UnicodeEncodeError`` a mitad de la
    verificación.
    """
    reconfigure = getattr(flujo, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass  # flujo que no admite reconfigurar: seguir sin romper


def comprobar(titulo: str, obtenido, esperado) -> bool:
    ok = obtenido == esperado
    marca = "[OK]  " if ok else "[FALLA]"
    print(f"{marca} {titulo}: {obtenido}" + ("" if ok else f"  (esperado {esperado})"))
    return ok


def _comprobar_extraccion(documentos) -> list[bool]:
    """Los requisitos del enunciado que dependen de lo que extrajo cada extractor.

    El requisito 1 —``validar_documento`` limpio para toda salida— solo se puede
    comprobar de verdad aquí: contra el corpus real y con los extractores
    implementados. Las pruebas unitarias lo verifican contra documentos armados
    a mano, que es otra cosa.
    """
    violaciones = [
        (documento.fuente, motivos)
        for documento in documentos
        if (motivos := validar_documento(documento))
    ]
    for fuente, motivos in violaciones[:5]:
        print(f"        {fuente}: {motivos[0]}")

    con_bloques = [documento for documento in documentos if documento.bloques]
    con_jerarquia = [
        documento
        for documento in documentos
        if any(len(bloque.ruta) >= 3 for bloque in documento.bloques)
    ]

    return [
        comprobar("documentos que violan el contrato", len(violaciones), 0),
        comprobar(
            "documentos con al menos un bloque",
            f"{len(con_bloques)}/{len(documentos)}",
            f"{len(documentos)}/{len(documentos)}",
        ),
        comprobar("hay breadcrumb de tres niveles", bool(con_jerarquia), True),
    ]


def _resumen_de_extraccion(documentos) -> None:
    """Informativo: qué salió por formato y por qué falló lo que falló."""
    print()
    print("--- extracción ---")

    por_formato: dict[str, list[int]] = {}
    for documento in documentos:
        acumulado = por_formato.setdefault(documento.formato, [0, 0, 0])
        acumulado[0] += 1
        acumulado[1] += len(documento.bloques)
        acumulado[2] += sum(len(bloque.texto) for bloque in documento.bloques)

    print(f"{'formato':>8}  {'docs':>5}  {'bloques':>9}  {'caracteres':>12}")
    for formato, (docs, bloques, chars) in sorted(por_formato.items()):
        print(f"{formato:>8}  {docs:>5}  {bloques:>9}  {chars:>12}")

    motivos = collections.Counter(
        documento.errores[0].split(":")[0]
        for documento in documentos
        if documento.errores and not documento.bloques
    )
    if motivos:
        print()
        print("documentos sin bloques, por motivo:")
        for motivo, cuantos in motivos.most_common():
            print(f"    {cuantos:>4}  {motivo[:80]}")


def main(argv: list[str] | None = None) -> int:
    _forzar_utf8(sys.stdout)
    _forzar_utf8(sys.stderr)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--salida", type=Path, default=RAIZ / "extraidos")
    parser.add_argument(
        "--procesos",
        type=int,
        default=1,
        help=(
            "procesos de extracción en paralelo. Este script hace dos corridas "
            "completas para comprobar el determinismo, y en secuencial el corpus "
            "real son unas 3 horas por corrida"
        ),
    )
    args = parser.parse_args(argv)

    xlsx = args.corpus / "Indice_Datos_Codefest.xlsx"
    indice = cargar_indice(xlsx)

    resultados = [comprobar("entradas del índice", len(indice), ESPERADO_TOTAL)]

    primera = args.salida
    documentos, reporte = procesar_corpus(
        args.corpus, primera, limpiar=True, indice=indice, procesos=args.procesos
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

    resultados += _comprobar_extraccion(documentos)

    _resumen_de_extraccion(documentos)

    # Determinismo: segunda corrida en otro directorio, diff byte a byte.
    #
    # El directorio es uno de verdad (tempfile.mkdtemp), no un hermano
    # derivado de --salida (antes: f"{primera.name}_bis"): --salida por
    # defecto es <repo>/extraidos, así que el hermano era <repo>/extraidos_bis
    # y, si un operador había guardado ahí la salida de referencia de una
    # entrega anterior, este script se la vaciaba (limpiar=True) y luego le
    # borraba el directorio entero sin preguntar -el README enseña justo ese
    # flujo de dos directorios para diffear corridas, así que no es
    # hipotético-. mkdtemp() garantiza un directorio nuevo y exclusivo: nunca
    # puede coincidir con uno que el operador ya esté usando. El try/finally
    # además evita el huérfano que dejaba una excepción a mitad de la segunda
    # corrida: antes eso hacía que el rmtree nunca se ejecutara.
    segunda = Path(tempfile.mkdtemp(prefix="verificar_corpus_bis_"))
    try:
        procesar_corpus(
            args.corpus, segunda, limpiar=True, indice=indice, procesos=args.procesos
        )
        iguales = (primera / "manifiesto.jsonl").read_bytes() == (
            segunda / "manifiesto.jsonl"
        ).read_bytes()
    finally:
        shutil.rmtree(segunda, ignore_errors=True)
    resultados.append(comprobar("dos corridas dan el mismo manifiesto", iguales, True))

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
