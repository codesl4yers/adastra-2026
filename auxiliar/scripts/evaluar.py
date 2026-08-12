"""Mide F1@3 y NDCG@10 de un ``resultados.jsonl`` contra el ground truth interno.

Lee artefactos y nada más: no carga el índice ni el encoder, así que corre en un
segundo y sirve para comparar dos configuraciones sin reconstruir nada.

Qué mide cada número y por qué manda el NDCG binario:
``docs/decisiones/recuperacion-y-entregable.md`` §10.

Uso::

    python auxiliar/scripts/evaluar.py \
        --resultados resultados.jsonl \
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
