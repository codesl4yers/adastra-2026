"""Recorre un directorio de entrada, extrae cada documento y lo persiste.

Es el único módulo que escribe a disco. Los extractores son funciones puras.

Produce:

- ``{salida}/{doc_id}.json``: un ``Documento`` serializado por archivo.
- ``{salida}/manifiesto.jsonl``: una línea por documento, ordenada por fuente.

El manifiesto existe para hacer ``diff`` entre corridas y detectar regresiones,
así que su estabilidad importa tanto como su contenido: claves ordenadas,
saltos de línea Unix y orden por ``fuente``.

Uso::

    python orquestador.py --entrada fixtures --salida extraidos
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from types import ModuleType

from contrato import Documento, calcular_doc_id, documento_a_dict
from extractores import html, imagen, json_, pbf, pdf, tabular

NOMBRE_MANIFIESTO = "manifiesto.jsonl"

# Extensión -> (módulo extractor, formato declarado en el contrato).
# Una extensión que no esté aquí se ignora: el corpus trae archivos auxiliares
# (scripts, README) que no son documentos del reto.
EXTRACTORES: dict[str, tuple[ModuleType, str]] = {
    ".html": (html, "html"),
    ".htm": (html, "html"),
    ".pdf": (pdf, "pdf"),
    ".json": (json_, "json"),
    ".geojson": (json_, "json"),
    ".csv": (tabular, "csv"),
    ".xlsx": (tabular, "xlsx"),
    ".png": (imagen, "imagen"),
    ".jpg": (imagen, "imagen"),
    ".jpeg": (imagen, "imagen"),
    ".tif": (imagen, "imagen"),
    ".tiff": (imagen, "imagen"),
    ".bmp": (imagen, "imagen"),
    ".webp": (imagen, "imagen"),
    ".pbf": (pbf, "pbf"),
    ".mvt": (pbf, "pbf"),
}

# Reconoce "fenomeno_2", "fenomeno-3", "Fenomeno 1" como nombre de carpeta.
_CARPETA_FENOMENO = re.compile(r"^fen[oó]meno[\s_\-]?([123])$", re.IGNORECASE)


def procesar_directorio(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
) -> list[Documento]:
    """Extrae todos los documentos de ``entrada`` y los escribe en ``salida``.

    Devuelve los documentos ordenados por ``fuente``, el mismo orden en que se
    escribe el manifiesto.

    Lanza ``ValueError`` si dos archivos comparten nombre: ``fuente`` es el
    campo de emparejamiento de la evaluación y ``doc_id`` deriva de él, así que
    un choque significa que un documento sobrescribiría al otro. Es el único
    caso en que este módulo se detiene: un fallo de extracción se registra, pero
    una colisión de identidad invalidaría la entrega entera.
    """
    entrada = Path(entrada)
    salida = Path(salida)

    rutas = _listar_documentos(entrada)
    _verificar_colisiones(rutas)

    documentos = [
        _extraer_documento(ruta, _fenomeno_de(ruta, entrada, fenomeno_por_defecto))
        for ruta in rutas
    ]
    documentos.sort(key=lambda doc: doc.fuente)

    _escribir_salida(documentos, salida, limpiar=limpiar)
    return documentos


# --- recorrido ----------------------------------------------------------------


def _listar_documentos(entrada: Path) -> list[Path]:
    """Rutas de los archivos con extractor registrado, en orden estable.

    Se ordena explícitamente: el orden de ``rglob`` depende del sistema de
    archivos y bastaría para que dos corridas difieran.
    """
    if not entrada.is_dir():
        raise ValueError(f"el directorio de entrada no existe: {entrada}")

    return sorted(
        (ruta for ruta in entrada.rglob("*") if ruta.is_file() and _tiene_extractor(ruta)),
        key=lambda ruta: ruta.as_posix(),
    )


def _tiene_extractor(ruta: Path) -> bool:
    return ruta.suffix.lower() in EXTRACTORES


def _verificar_colisiones(rutas: list[Path]) -> None:
    """Comprueba que ningún nombre de archivo se repita entre subdirectorios."""
    vistos: dict[str, Path] = {}
    for ruta in rutas:
        anterior = vistos.get(ruta.name)
        if anterior is not None:
            raise ValueError(
                f"colisión de fuente: {ruta.name!r} aparece en {anterior.as_posix()!r} "
                f"y en {ruta.as_posix()!r}. La fuente es el campo de emparejamiento "
                f"y debe ser única en el corpus."
            )
        vistos[ruta.name] = ruta


def _fenomeno_de(ruta: Path, entrada: Path, por_defecto: int) -> int:
    """Deduce el fenómeno del nombre de la carpeta que contiene el archivo.

    Si el corpus viene organizado en ``fenomeno_1/``, ``fenomeno_2/``... se usa
    esa pista. Si no, el valor por defecto.
    """
    for parte in ruta.relative_to(entrada).parts[:-1]:
        coincidencia = _CARPETA_FENOMENO.match(parte)
        if coincidencia:
            return int(coincidencia.group(1))
    return por_defecto


# --- extracción ----------------------------------------------------------------


def _extraer_documento(ruta: Path, fenomeno: int) -> Documento:
    """Invoca al extractor correspondiente, blindando el pipeline.

    Los stubs aún no implementados lanzan ``NotImplementedError``, y un
    extractor nuevo puede tener errores. Ninguno de los dos casos puede detener
    la corrida: se devuelve un documento válido con el motivo en ``errores``.
    """
    modulo, formato = EXTRACTORES[ruta.suffix.lower()]
    try:
        return modulo.extraer(ruta, fenomeno)
    except NotImplementedError as exc:
        motivo = f"extractor de {formato} no implementado: {exc}"
    except Exception as exc:  # noqa: BLE001 - blindaje deliberado
        motivo = f"fallo del extractor de {formato} ({type(exc).__name__}): {exc}"

    return _documento_fallido(ruta, fenomeno, formato, motivo)


def _documento_fallido(ruta: Path, fenomeno: int, formato: str, motivo: str) -> Documento:
    """Documento válido que representa una extracción que no se pudo hacer."""
    return Documento(
        doc_id=calcular_doc_id(ruta.name),
        fuente=ruta.name,
        formato=formato,
        fenomeno=fenomeno,
        idioma="es",
        bloques=[],
        meta={},
        errores=[motivo],
    )


# --- persistencia --------------------------------------------------------------


def _escribir_salida(documentos: list[Documento], salida: Path, limpiar: bool) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    if limpiar:
        _limpiar_salida(salida)

    for documento in documentos:
        _escribir_json(salida / f"{documento.doc_id}.json", documento_a_dict(documento))

    _escribir_manifiesto(documentos, salida / NOMBRE_MANIFIESTO)


def _limpiar_salida(salida: Path) -> None:
    """Borra los productos de una corrida anterior, y solo esos.

    Sin esto, los documentos de un corpus previo quedan como huérfanos y
    ensucian el diff. Se restringe a los archivos que escribe este módulo para
    no tocar nada más de ese directorio.
    """
    for archivo in sorted(salida.glob("*.json")):
        archivo.unlink()
    manifiesto = salida / NOMBRE_MANIFIESTO
    if manifiesto.exists():
        manifiesto.unlink()


def _escribir_json(ruta: Path, datos: dict) -> None:
    """Escribe JSON de forma reproducible byte a byte.

    ``sort_keys`` fija el orden de las claves, ``indent`` la forma, y
    ``newline="\\n"`` evita que Windows escriba CRLF y rompa el diff con las
    corridas hechas en Linux.
    """
    contenido = json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2)
    ruta.write_text(contenido + "\n", encoding="utf-8", newline="\n")


def _escribir_manifiesto(documentos: list[Documento], ruta: Path) -> None:
    lineas = [
        json.dumps(_entrada_de_manifiesto(documento), ensure_ascii=False, sort_keys=True)
        for documento in documentos
    ]
    contenido = "".join(f"{linea}\n" for linea in lineas)
    ruta.write_text(contenido, encoding="utf-8", newline="\n")


def _entrada_de_manifiesto(documento: Documento) -> dict:
    """Resumen de una línea del manifiesto."""
    return {
        "doc_id": documento.doc_id,
        "fuente": documento.fuente,
        "formato": documento.formato,
        "fenomeno": documento.fenomeno,
        "idioma": documento.idioma,
        "n_bloques": len(documento.bloques),
        "n_chars": sum(len(bloque.texto) for bloque in documento.bloques),
        "n_errores": len(documento.errores),
    }


def cargar_manifiesto(ruta: Path) -> list[dict]:
    """Lee un manifiesto JSONL. Útil para comparar corridas."""
    contenido = Path(ruta).read_text(encoding="utf-8")
    return [json.loads(linea) for linea in contenido.splitlines() if linea.strip()]


# --- interfaz de línea de comandos ---------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entrada", required=True, type=Path, help="directorio del corpus")
    parser.add_argument("--salida", required=True, type=Path, help="directorio de resultados")
    parser.add_argument(
        "--fenomeno",
        type=int,
        default=1,
        choices=(1, 2, 3),
        help="fenómeno por defecto cuando no se deduce del directorio",
    )
    parser.add_argument(
        "--limpiar",
        action="store_true",
        help="borra los resultados de la corrida anterior antes de escribir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)

    documentos = procesar_directorio(
        args.entrada,
        args.salida,
        fenomeno_por_defecto=args.fenomeno,
        limpiar=args.limpiar,
    )

    con_errores = [documento for documento in documentos if documento.errores]
    bloques = sum(len(documento.bloques) for documento in documentos)
    print(
        f"{len(documentos)} documentos, {bloques} bloques, "
        f"{len(con_errores)} con errores -> {args.salida}"
    )
    for documento in con_errores:
        print(f"  [error] {documento.fuente}: {documento.errores[0]}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
