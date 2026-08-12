"""Barrido de configuraciones de fragmentación (§8.3 del spec).

Tabla comparativa variando ``objetivo_palabras`` y ``oraciones_solape``, con
fragmentos, mediana, p95, porcentaje de una sola oración y de huérfanos.

**La tabla no elige la configuración**: eso exige medir NDCG@10 y F1@3 contra el
ground truth. Es el insumo de esa decisión, no la decisión.

Uso::

    python scripts/barrido_fragmentacion.py --entrada extraidos
    python scripts/barrido_fragmentacion.py --entrada extraidos --salida docs/barrido.md
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_RAIZ / "auxiliar"))
sys.path.insert(0, str(_RAIZ))

from fragmentador import (  # noqa: E402  - tras ajustar sys.path
    ConfigFragmentacion,
    cargar_extraidos,
    fragmentar_documentos,
)

OBJETIVOS: tuple[int, ...] = (120, 190, 240)
SOLAPES: tuple[int, ...] = (0, 1)

CABECERA = (
    "| objetivo | solape | fragmentos | mediana | p95 | 1 oración | huérfanos fusionados |",
    "|---:|---:|---:|---:|---:|---:|---:|",
)


@dataclass(frozen=True)
class FilaBarrido:
    """Una configuración medida. Los porcentajes van sobre el total de fragmentos."""

    objetivo_palabras: int
    oraciones_solape: int
    n_fragmentos: int
    mediana_palabras: int
    p95_palabras: int
    porcentaje_una_oracion: float
    porcentaje_huerfanos: float


def barrer(documentos, objetivos=OBJETIVOS, solapes=SOLAPES) -> list[FilaBarrido]:
    """Mide cada combinación de objetivo y solape sobre el mismo corpus."""
    filas: list[FilaBarrido] = []
    for objetivo in objetivos:
        for solape in solapes:
            config = ConfigFragmentacion(
                objetivo_palabras=objetivo, oraciones_solape=solape
            )
            _, reporte = fragmentar_documentos(documentos, config)
            filas.append(
                FilaBarrido(
                    objetivo_palabras=objetivo,
                    oraciones_solape=solape,
                    n_fragmentos=reporte.n_fragmentos,
                    mediana_palabras=reporte.mediana_palabras,
                    p95_palabras=reporte.p95_palabras,
                    porcentaje_una_oracion=_porcentaje(
                        reporte.n_oraciones_unicas, reporte.n_fragmentos
                    ),
                    porcentaje_huerfanos=_porcentaje(
                        reporte.n_huerfanos_fusionados, reporte.n_fragmentos
                    ),
                )
            )
    return filas


def _porcentaje(parte: int, total: int) -> float:
    """Porcentaje redondeado a una decimal. Sin fragmentos, 0.0 y no una división por cero."""
    return round(100 * parte / total, 1) if total else 0.0


def tabla_markdown(filas: list[FilaBarrido]) -> str:
    """Formatea el barrido para pegarlo en el informe técnico."""
    lineas = list(CABECERA)
    for fila in filas:
        lineas.append(
            f"| {fila.objetivo_palabras} | {fila.oraciones_solape} | "
            f"{fila.n_fragmentos} | {fila.mediana_palabras} | {fila.p95_palabras} | "
            f"{fila.porcentaje_una_oracion} % | {fila.porcentaje_huerfanos} % |"
        )
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entrada", required=True, type=Path, help="directorio extraidos/")
    parser.add_argument(
        "--salida", type=Path, default=None, help="archivo .md; por defecto, stdout"
    )
    args = parser.parse_args(argv)

    documentos = cargar_extraidos(args.entrada)
    print(f"{len(documentos)} documentos leídos de {args.entrada}", file=sys.stderr)

    con_bloques = sum(1 for documento in documentos if documento.bloques)
    if not con_bloques:
        print(
            "[aviso] ningún documento tiene bloques: los extractores siguen "
            "siendo stubs, así que la tabla saldrá vacía. Ver §8.2 del spec.",
            file=sys.stderr,
        )

    tabla = tabla_markdown(barrer(documentos))

    if args.salida:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(tabla, encoding="utf-8", newline="\n")
        print(f"tabla escrita en {args.salida}", file=sys.stderr)
    else:
        print(tabla, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
