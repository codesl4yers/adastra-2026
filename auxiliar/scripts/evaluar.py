"""Mide F1@3 y NDCG@10 de un ``resultados.jsonl`` contra el ground truth interno.

Lee artefactos y nada más: no carga el índice ni el encoder, así que corre en un
segundo y sirve para comparar dos configuraciones sin reconstruir nada.

Qué mide cada número y por qué manda el NDCG binario:
``docs/decisiones/recuperacion-y-entregable.md`` §10.

Uso::

    python auxiliar/scripts/evaluar.py \\
        --resultados resultados.jsonl \\
        --ground auxiliar/ground/ground_truth.json [--detalle]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_RAIZ / "auxiliar"))
sys.path.insert(0, str(_RAIZ))


# Los chunk_id del corpus son <doc_id>-c<4 dígitos>. Se comprueba en vez de
# partir por el último guion: un doc_id mal derivado inventa o pierde aciertos.
_CHUNK_ID = re.compile(r"^(?P<doc_id>.+)-c\d{4}$")


@dataclass(frozen=True)
class ConsultaEtiquetada:
    """Una consulta del ground truth, con sus relevantes en las dos escalas."""

    query_id: str
    fragmentos: dict[str, int]  # chunk_id -> rank, 1 es el más relevante
    documentos: set[str]        # los doc_id de esos fragmentos


def doc_id_de(chunk_id: str) -> str:
    """El documento al que pertenece un fragmento, según su identificador."""
    coincidencia = _CHUNK_ID.match(chunk_id)
    if coincidencia is None:
        raise ValueError(
            f"chunk_id con formato inesperado: {chunk_id!r}. Se espera "
            f"<doc_id>-c<4 dígitos>; sin eso no se puede derivar el doc_id."
        )
    return coincidencia.group("doc_id")


def cargar_ground(ruta: Path) -> list[ConsultaEtiquetada]:
    """Lee ``ground_truth.json`` en el orden en que viene."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ValueError(f"no existe el ground truth: {ruta}")

    crudo = json.loads(ruta.read_text(encoding="utf-8"))

    etiquetadas: list[ConsultaEtiquetada] = []
    for consulta in crudo:
        fragmentos = {
            c["chunk_id"]: int(c["rank"]) for c in consulta["relevant_chunks"]
        }
        etiquetadas.append(
            ConsultaEtiquetada(
                query_id=consulta["question_id"],
                fragmentos=fragmentos,
                documentos={doc_id_de(chunk_id) for chunk_id in fragmentos},
            )
        )
    return etiquetadas


def cargar_resultados(ruta: Path) -> dict[str, dict]:
    """Lee ``resultados.jsonl`` a un mapa ``query_id -> registro``."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ValueError(f"no existen los resultados: {ruta}")

    registros: dict[str, dict] = {}
    with ruta.open(encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError as error:
                raise ValueError(f"{ruta}:{numero} no es JSON válido: {error}") from error

            query_id = registro.get("query_id")
            if query_id in registros:
                raise ValueError(
                    f"{ruta}:{numero}: query_id repetido {query_id!r}; "
                    f"no hay forma de saber cuál de las dos líneas se evalúa"
                )
            registros[query_id] = registro
    return registros


def techo_f1(relevantes: int, k: int) -> float:
    """El F1@k máximo con ``relevantes`` documentos y ``k`` puestos.

    Con más relevantes que puestos no se pueden entregar todos y la cobertura
    tiene tope; con menos, sobran puestos y el tope lo pone la precisión.
    """
    if relevantes <= 0 or k <= 0:
        return 0.0
    aciertos = min(k, relevantes)
    precision = aciertos / k
    cobertura = aciertos / relevantes
    return 2 * precision * cobertura / (precision + cobertura)


def f1_en_k(predichos: list[str], relevantes: set[str], k: int) -> float:
    """F1 entre los ``k`` primeros predichos y el conjunto relevante.

    La precisión es sobre lo entregado y no sobre ``k``: un top corto ya se avisa
    en la corrida, y descontarlo aquí lo penalizaría dos veces.
    """
    if k <= 0 or not relevantes:
        return 0.0

    tomados = predichos[:k]
    if not tomados:
        return 0.0

    aciertos = len({p for p in tomados if p in relevantes})
    if not aciertos:
        return 0.0

    precision = aciertos / len(tomados)
    cobertura = aciertos / len(relevantes)
    return 2 * precision * cobertura / (precision + cobertura)


def ndcg_en_k(predichos: list[str], ganancias: dict[str, float], k: int) -> float:
    """NDCG de los ``k`` primeros predichos, con la ganancia de cada relevante.

    Un predicho sin ganancia consume su puesto con un cero: no se salta, que es
    lo que hace que colocar ruido arriba duela.
    """
    if k <= 0 or not ganancias:
        return 0.0

    dcg = sum(
        ganancias.get(predicho, 0.0) / math.log2(posicion + 1)
        for posicion, predicho in enumerate(predichos[:k], start=1)
    )
    ideal = sorted(ganancias.values(), reverse=True)[:k]
    idcg = sum(
        ganancia / math.log2(posicion + 1)
        for posicion, ganancia in enumerate(ideal, start=1)
    )
    return dcg / idcg if idcg else 0.0


@dataclass(frozen=True)
class Reporte:
    """Lo que se mide. Los NDCG son ``None`` si el entregable no trae fragmentos."""

    n_consultas: int
    n_ignoradas: int
    f1: float
    techo_f1: float
    ndcg_binario: float | None
    ndcg_graduado: float | None
    por_consulta: list[tuple[str, float, float | None, float | None]]


def evaluar(
    etiquetadas: list[ConsultaEtiquetada],
    resultados: dict[str, dict],
    k_documentos: int,
    k_fragmentos: int,
) -> Reporte:
    """Macro-promedia las métricas sobre las consultas del ground truth."""
    faltan = [c.query_id for c in etiquetadas if c.query_id not in resultados]
    if faltan:
        raise ValueError(
            f"faltan {len(faltan)} consultas del ground truth en los resultados "
            f"({', '.join(faltan[:10])}). Promediar sobre las que están da un "
            f"número que no se compara con nada."
        )

    hay_fragmentos = any("fragmentos" in r for r in resultados.values())

    f1s: list[float] = []
    techos: list[float] = []
    binarios: list[float] = []
    graduados: list[float] = []
    detalle: list[tuple[str, float, float | None, float | None]] = []

    for consulta in etiquetadas:
        registro = resultados[consulta.query_id]

        documentos = [d.get("doc_id") for d in registro.get("documentos", [])]
        f1 = f1_en_k(documentos, consulta.documentos, k_documentos)
        f1s.append(f1)
        techos.append(techo_f1(len(consulta.documentos), k_documentos))

        binario = graduado = None
        if hay_fragmentos:
            fragmentos = [f.get("chunk_id") for f in registro.get("fragmentos", [])]
            binario = ndcg_en_k(
                fragmentos, {c: 1.0 for c in consulta.fragmentos}, k_fragmentos
            )
            graduado = ndcg_en_k(
                fragmentos,
                {c: float(6 - rank) for c, rank in consulta.fragmentos.items()},
                k_fragmentos,
            )
            binarios.append(binario)
            graduados.append(graduado)

        detalle.append((consulta.query_id, f1, binario, graduado))

    total = len(etiquetadas)
    return Reporte(
        n_consultas=total,
        n_ignoradas=len(resultados) - total,
        f1=sum(f1s) / total,
        techo_f1=sum(techos) / total,
        ndcg_binario=sum(binarios) / total if hay_fragmentos else None,
        ndcg_graduado=sum(graduados) / total if hay_fragmentos else None,
        por_consulta=detalle,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--resultados", required=True, type=Path, help="resultados.jsonl a medir"
    )
    parser.add_argument(
        "--ground", required=True, type=Path, help="ground_truth.json de referencia"
    )
    parser.add_argument("--k-documentos", type=int, default=3, help="la k de F1@k")
    parser.add_argument("--k-fragmentos", type=int, default=10, help="la k de NDCG@k")
    parser.add_argument("--detalle", action="store_true", help="una línea por consulta")
    args = parser.parse_args(argv)

    reporte = evaluar(
        cargar_ground(args.ground),
        cargar_resultados(args.resultados),
        args.k_documentos,
        args.k_fragmentos,
    )

    print(f"consultas evaluadas   {reporte.n_consultas}")
    if reporte.n_ignoradas:
        print(f"  (+{reporte.n_ignoradas} en los resultados que no están etiquetadas)")
    print()

    porcentaje = 100 * reporte.f1 / reporte.techo_f1 if reporte.techo_f1 else 0.0
    print(
        f"F1@{args.k_documentos}         {reporte.f1:.3f}   "
        f"de {reporte.techo_f1:.3f} alcanzable   ({porcentaje:.1f}%)"
    )

    if reporte.ndcg_binario is None:
        print(
            f"NDCG@{args.k_fragmentos}      no medible: el entregable no trae "
            f"fragmentos[]. Re-corre con --top-fragmentos {args.k_fragmentos}."
        )
    else:
        print(f"NDCG@{args.k_fragmentos}      {reporte.ndcg_binario:.3f}   binario")
        print(f"NDCG@{args.k_fragmentos}      {reporte.ndcg_graduado:.3f}   graduado")

    if args.detalle:
        print("\nconsulta    F1      NDCG bin  NDCG grad")
        for query_id, f1, binario, graduado in reporte.por_consulta:
            columnas = f"{f1:.3f}"
            if binario is not None:
                columnas += f"   {binario:.3f}     {graduado:.3f}"
            print(f"{query_id:<11} {columnas}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
