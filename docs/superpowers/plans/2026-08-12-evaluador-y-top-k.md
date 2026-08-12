# Evaluador de métricas y top-k de fragmentos — Plan de implementación

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usa
> `superpowers:subagent-driven-development` (recomendada) o
> `superpowers:executing-plans` para implementar tarea por tarea. Los pasos usan
> casillas (`- [ ]`) para el seguimiento.

**Objetivo:** medir F1@3 y NDCG@10 del pipeline contra el ground truth interno, y
emitir el top-10 de fragmentos que NDCG@10 necesita para no tener techo.

**Arquitectura:** dos piezas independientes. `generador.py` gana una función pura
de corte (`mejores_fragmentos`) y un campo `fragmentos[]` en cada línea del
entregable, sin tocar `documentos[]`. `auxiliar/scripts/evaluar.py` es un script puro que
lee `resultados.jsonl` y `ground_truth.json` y no importa ni torch ni faiss.

**Stack:** Python 3.11+, pytest, biblioteca estándar. El evaluador no añade
dependencias.

**Spec:** `docs/superpowers/specs/2026-08-12-evaluador-y-top-k-design.md`

## Restricciones globales

- **El índice no se toca.** Ninguna tarea reconstruye `index.faiss` ni
  `metadata.jsonl`. Solo la tarea 8 vuelve a correr la fase de respuesta.
- **`documentos[]` no cambia ni un campo.** El entregable actual sigue siendo
  válido; `fragmentos[]` se añade al lado.
- Comentarios superficiales: docstring de una o dos líneas por función, y la
  justificación va a `docs/decisiones/`, no al código.
- Nombres de prueba en español y descriptivos, como el resto de la suite.
- Determinismo (§1.4 del enunciado): nada de `set` en un camino que decida orden,
  nada de `hash()`, `sort_keys=True` al serializar.
- Escritura de archivos siempre con `encoding="utf-8", newline="\n"`.
- **Commits:** el árbol tiene 45 archivos modificados de trabajo anterior. Los
  pasos de commit de este plan solo se ejecutan si el usuario lo pide; si no, se
  salta el paso y se sigue.
- La suite completa (`python -m pytest`) está en 685 pruebas verdes y debe
  seguirlo al cerrar cada tarea.

---

## Estructura de archivos

| Archivo | Responsabilidad | Acción |
|---|---|---|
| `generador.py` | corte del top-k y campo `fragmentos[]` | modificar |
| `auxiliar/scripts/evaluar.py` | métricas contra el ground truth | crear |
| `auxiliar/tests/test_generador.py` | pruebas del corte y del registro | modificar |
| `auxiliar/tests/test_evaluar.py` | pruebas de F1, NDCG, techo y validaciones | crear |
| `docs/decisiones/recuperacion-y-entregable.md` | §7 y §10 nuevos | modificar |
| `README.md` | CLI nueva, evaluador, ficha técnica | modificar |

`auxiliar/scripts/preparar_entrega.py` **no se toca**: copia `resultados.jsonl` tal cual y
cuenta líneas, así que el campo nuevo le es transparente (comprobado).

---

## Tarea 1: `mejores_fragmentos`, el corte del top-k

**Archivos:**
- Modificar: `generador.py` (constante junto a `TOP_DOCUMENTOS`, línea 65; función
  junto a `agregar_por_documento`, línea 526)
- Prueba: `auxiliar/tests/test_generador.py` (sección «consultas: agregación y
  post-filtros», tras `test_el_duplicado_que_sobrevive_es_el_de_mejor_score`)

**Interfaces:**
- Consume: `Candidato` (`generador.py:337`), ya definido.
- Produce: `TOP_FRAGMENTOS: int = 10` y
  `mejores_fragmentos(candidatos: list[Candidato], top: int) -> list[Candidato]`.
  Las tareas 2 y 3 dependen de los dos nombres.

- [ ] **Paso 1: escribir las pruebas que fallan**

En `auxiliar/tests/test_generador.py`, tras
`test_el_duplicado_que_sobrevive_es_el_de_mejor_score`. El helper `candidato(...)`
ya existe en el archivo (línea 686) y se reutiliza tal cual:

```python
def test_el_top_de_fragmentos_corta_donde_se_le_pide():
    candidatos = [candidato(n, 0.9 - n / 10, f"D{n}") for n in range(5)]

    assert [c.fila for c in mejores_fragmentos(candidatos, 3)] == [0, 1, 2]


def test_el_top_de_fragmentos_respeta_el_orden_de_llegada():
    """Vienen ordenados por score desde FAISS: cortar no puede reordenar."""
    candidatos = [candidato(0, 0.9, "A"), candidato(1, 0.8, "B"), candidato(2, 0.7, "C")]

    assert [c.score for c in mejores_fragmentos(candidatos, 3)] == [0.9, 0.8, 0.7]


def test_con_menos_candidatos_que_el_tope_salen_todos():
    candidatos = [candidato(0, 0.9, "A"), candidato(1, 0.8, "B")]

    assert len(mejores_fragmentos(candidatos, 10)) == 2


def test_un_tope_de_cero_apaga_los_fragmentos():
    """Con --top-fragmentos 0 el entregable sale como antes de esta pieza."""
    candidatos = [candidato(0, 0.9, "A")]

    assert mejores_fragmentos(candidatos, 0) == []
```

Y añadir `mejores_fragmentos` al bloque `from generador import (` de la línea 24,
en orden alfabético.

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_generador.py -k mejores_fragmentos -v
```

Esperado: `ImportError: cannot import name 'mejores_fragmentos' from 'generador'`.

- [ ] **Paso 3: implementar**

En `generador.py`, junto a `TOP_DOCUMENTOS` (línea 65):

```python
# El top-3 de §8.6: F1@3 se mide exactamente sobre tres.
TOP_DOCUMENTOS = 3

# El 10 de NDCG@10. Con menos, la métrica tiene un techo por construcción.
TOP_FRAGMENTOS = 10
```

Y la función, justo antes de `agregar_por_documento` (línea 526):

```python
def mejores_fragmentos(candidatos: list[Candidato], top: int) -> list[Candidato]:
    """Los ``top`` mejores fragmentos, ya filtrados y deduplicados.

    Es un corte: los candidatos llegan ordenados por score. Tiene función propia
    porque es donde se engancharía un reranking, entre la dedup y el corte.
    """
    if top <= 0:
        return []
    return candidatos[:top]
```

- [ ] **Paso 4: correr las pruebas y verificar que pasan**

```bash
python -m pytest auxiliar/tests/test_generador.py -k mejores_fragmentos -v
```

Esperado: 4 passed.

- [ ] **Paso 5: commit** *(solo si el usuario lo pidió; ver restricciones)*

```bash
git add generador.py auxiliar/tests/test_generador.py
git commit -m "feat: corte del top-k de fragmentos para NDCG@10"
```

---

## Tarea 2: `fragmentos[]` en el entregable

**Archivos:**
- Modificar: `generador.py` — `registro_de_resultado` (línea 557) y
  `responder_consultas` (línea 371, bucle de consultas en 437-465)
- Prueba: `auxiliar/tests/test_generador.py` (sección «consultas: el entregable», tras
  `test_el_entregable_no_da_mas_documentos_de_los_pedidos`, línea 591)

**Interfaces:**
- Consume: `mejores_fragmentos` y `TOP_FRAGMENTOS` de la tarea 1.
- Produce: `registro_de_resultado(consulta, documentos, fragmentos=())` y el
  parámetro `top_fragmentos: int = TOP_FRAGMENTOS` de `responder_consultas`. La
  tarea 3 llama a este parámetro desde la CLI; la tarea 7 lee el campo
  `fragmentos[]` que se escribe aquí.

- [ ] **Paso 1: escribir las pruebas que fallan**

El helper `responder(...)` de la línea 542 pasa `**extra` a `responder_consultas`,
así que `top_fragmentos=` se le puede dar directamente. `indice_de_cuatro` es la
fixture de cuatro documentos de un fragmento (línea 553).

```python
def test_el_entregable_lleva_el_top_de_fragmentos(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=3)

    fragmentos = leer_resultados(destino)[0]["fragmentos"]
    assert [f["puesto"] for f in fragmentos] == [1, 2, 3]
    assert fragmentos[0]["texto"] == "alfa"


def test_cada_fragmento_lleva_su_chunk_id(indice_de_cuatro, tmp_path):
    """Es la clave con la que el ground truth empareja: sin ella no hay NDCG."""
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "beta")], destino, top_fragmentos=2)

    primero = leer_resultados(destino)[0]["fragmentos"][0]
    assert primero["chunk_id"] == "F1-DOC-001-c0000"
    assert primero["doc_id"] == "F1-DOC-001"
    assert primero["score"] == pytest.approx(1.0, abs=1e-5)


def test_los_fragmentos_salen_ordenados_por_score(indice_de_cuatro, tmp_path):
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=4)

    scores = [f["score"] for f in leer_resultados(destino)[0]["fragmentos"]]
    assert scores == sorted(scores, reverse=True)


def test_sin_top_de_fragmentos_el_entregable_sale_como_antes(indice_de_cuatro, tmp_path):
    """El entregable de la corrida anterior sigue siendo válido palabra por palabra."""
    destino = tmp_path / "resultados.jsonl"

    responder(indice_de_cuatro, [Consulta("q001", "alfa")], destino, top_fragmentos=0)

    registro = leer_resultados(destino)[0]
    assert "fragmentos" not in registro
    assert len(registro["documentos"]) == 3


def test_el_top_de_documentos_no_cambia_por_emitir_fragmentos(indice_de_cuatro, tmp_path):
    """Las dos vistas salen del mismo top-k: emitir una no puede alterar la otra."""
    con = tmp_path / "con.jsonl"
    sin = tmp_path / "sin.jsonl"
    consultas = [Consulta("q001", "alfa"), Consulta("q002", "beta")]

    responder(indice_de_cuatro, consultas, con, top_fragmentos=10)
    responder(indice_de_cuatro, consultas, sin, top_fragmentos=0)

    assert [r["documentos"] for r in leer_resultados(con)] == [
        r["documentos"] for r in leer_resultados(sin)
    ]
```

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_generador.py -k "fragmentos and entregable or chunk_id or ordenados_por_score" -v
```

Esperado: `TypeError: responder_consultas() got an unexpected keyword argument
'top_fragmentos'`.

- [ ] **Paso 3: implementar `registro_de_resultado`**

En `generador.py:557`, sustituir la firma y añadir el bloque. `Sequence` entra en
el import de `collections.abc` de la línea 39:

```python
from collections.abc import Callable, Iterator, Sequence
```

```python
def registro_de_resultado(
    consulta: Consulta,
    documentos: list[Recuperado],
    fragmentos: Sequence[Candidato] = (),
) -> dict[str, Any]:
    """Una línea de ``resultados.jsonl``, con los campos de la Tabla 2.

    **Este es el único sitio que hay que tocar si ADL fija otros nombres de
    campo.** ``documentos`` es el top-3 de §8.6 y ``fragmentos`` el top-10 que
    mide NDCG@10; sin fragmentos, el campo no se escribe.
    """
    registro: dict[str, Any] = {
        "query_id": consulta.id,
        "consulta": consulta.texto,
        "documentos": [
            {
                "puesto": puesto,
                "doc_id": documento.doc_id,
                "fuente": documento.metadata.get("fuente"),
                "ruta_relativa": documento.metadata.get("ruta_relativa"),
                "fenomeno": documento.metadata.get("fenomeno"),
                "observatorio": documento.metadata.get("observatorio"),
                "score": round(documento.score, 6),
                "n_fragmentos": documento.n_fragmentos,
                "chunk_id": documento.metadata.get("chunk_id"),
                "pagina": documento.metadata.get("pagina"),
                "texto": documento.metadata.get("texto"),
            }
            for puesto, documento in enumerate(documentos, start=1)
        ],
    }

    if fragmentos:
        registro["fragmentos"] = [
            {
                "puesto": puesto,
                "chunk_id": fragmento.metadata.get("chunk_id"),
                "doc_id": fragmento.metadata.get("doc_id"),
                "fuente": fragmento.metadata.get("fuente"),
                "score": round(fragmento.score, 6),
                "pagina": fragmento.metadata.get("pagina"),
                "texto": fragmento.metadata.get("texto"),
            }
            for puesto, fragmento in enumerate(fragmentos, start=1)
        ]

    return registro
```

- [ ] **Paso 4: implementar el paso por `responder_consultas`**

En la firma (línea 371), tras `top: int = TOP_DOCUMENTOS`:

```python
    top_fragmentos: int = TOP_FRAGMENTOS,
```

En el bucle de consultas (línea 437), sustituir el bloque que va desde
`documentos = agregar_por_documento(...)` hasta el `lineas.append(...)`:

```python
            documentos = agregar_por_documento(candidatos, top)
            if len(documentos) < top:
                incompletas.append(consulta.id)

            fragmentos = mejores_fragmentos(candidatos, top_fragmentos)
            if len(fragmentos) < top_fragmentos:
                fragmentos_cortos.append(consulta.id)

            lineas.append(
                json.dumps(
                    registro_de_resultado(consulta, documentos, fragmentos),
                    ensure_ascii=False,
                )
            )
```

Y declarar la lista junto a `incompletas` (línea 432):

```python
    incompletas: list[str] = []
    fragmentos_cortos: list[str] = []
```

- [ ] **Paso 5: correr las pruebas y verificar que pasan**

```bash
python -m pytest auxiliar/tests/test_generador.py -v
```

Esperado: todas verdes. `fragmentos_cortos` todavía no viaja en el reporte —eso es
la tarea 3—; aquí solo se acumula.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add generador.py auxiliar/tests/test_generador.py
git commit -m "feat: el entregable emite el top-10 de fragmentos"
```

---

## Tarea 3: la CLI y el reporte

**Archivos:**
- Modificar: `generador.py` — `ReporteConsultas` (línea 356), retorno de
  `responder_consultas` (línea 470), `_construir_parser` (línea 760),
  `_responder` (línea 852)
- Prueba: `auxiliar/tests/test_generador.py` (sección «consultas: el entregable»)

**Interfaces:**
- Consume: `top_fragmentos` de la tarea 2.
- Produce: la opción `--top-fragmentos` y los campos `top_fragmentos` y
  `consultas_sin_fragmentos_completos` de `ReporteConsultas`. La tarea 8 usa la
  opción para la corrida real.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_el_reporte_registra_cuantos_fragmentos_se_pidieron(indice_de_cuatro, tmp_path):
    reporte = responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "r.jsonl",
        top_fragmentos=7,
    )

    assert reporte.top_fragmentos == 7


def test_una_consulta_que_no_llena_el_top_de_fragmentos_se_nombra(
    indice_de_cuatro, tmp_path
):
    """El índice tiene cuatro vectores y uno se cae por duplicado: pedir diez
    no puede dar diez, y callarlo esconde un NDCG@10 mermado por construcción."""
    reporte = responder(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "r.jsonl",
        top_fragmentos=10,
    )

    assert reporte.consultas_sin_fragmentos_completos == ["q001"]


def test_la_cli_acepta_el_tope_de_fragmentos():
    parser = _construir_parser()

    assert parser.parse_args(["--top-fragmentos", "5"]).top_fragmentos == 5
    assert parser.parse_args([]).top_fragmentos == TOP_FRAGMENTOS
```

Añadir `TOP_FRAGMENTOS` y `_construir_parser` al bloque `from generador import (`.

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_generador.py -k "reporte_registra_cuantos or no_llena_el_top or cli_acepta" -v
```

Esperado: `AttributeError: 'ReporteConsultas' object has no attribute
'top_fragmentos'` y `AttributeError: 'Namespace' object has no attribute
'top_fragmentos'`.

- [ ] **Paso 3: ampliar `ReporteConsultas`**

En `generador.py:356`, tras `consultas_sin_top_completo`:

```python
    # Cada una es un F1@3 mermado por construcción: salen nombradas, no contadas.
    consultas_sin_top_completo: list[str]
    top_fragmentos: int = 0
    # Lo mismo para NDCG@10: menos de diez puestos es techo, no mal ranking.
    consultas_sin_fragmentos_completos: list[str] = field(default_factory=list)
```

`field` entra en el import de `dataclasses` de la línea 40:

```python
from dataclasses import asdict, dataclass, field
```

Los dos campos llevan valor por defecto porque van detrás de campos que no lo
tienen; sin ellos la dataclass no compila.

Y en el `return ReporteConsultas(...)` de la línea 465, que es el único sitio del
repo donde se construye —y con argumentos con nombre, así que añadir campos al
final no rompe nada (comprobado)—:

```python
        consultas_sin_top_completo=incompletas,
        top_fragmentos=top_fragmentos,
        consultas_sin_fragmentos_completos=fragmentos_cortos,
    )
```

- [ ] **Paso 4: añadir la opción al parser**

En `_construir_parser`, tras el bloque de `--top` (línea 760):

```python
    parser.add_argument(
        "--top-fragmentos",
        type=int,
        default=TOP_FRAGMENTOS,
        help="fragmentos por consulta en el entregable (NDCG@10); 0 los apaga",
    )
```

- [ ] **Paso 5: pasarla y avisar en `_responder`**

En la llamada a `responder_consultas` de `_responder` (línea 861):

```python
        top=args.top,
        top_fragmentos=args.top_fragmentos,
        idioma=args.idioma,
```

Y tras el aviso de `consultas_sin_top_completo` (línea 889):

```python
    if reporte.consultas_sin_fragmentos_completos:
        print(
            f"AVISO: {len(reporte.consultas_sin_fragmentos_completos)} consultas "
            f"no llegaron a {reporte.top_fragmentos} fragmentos "
            f"({', '.join(reporte.consultas_sin_fragmentos_completos[:10])}). "
            f"NDCG@{reporte.top_fragmentos} tiene techo en esas.",
            file=sys.stderr,
        )
```

- [ ] **Paso 6: correr la suite entera**

```bash
python -m pytest
```

Esperado: 685 + las nuevas, todas verdes.

- [ ] **Paso 7: commit** *(condicional)*

```bash
git add generador.py auxiliar/tests/test_generador.py
git commit -m "feat: --top-fragmentos y su aviso en el reporte"
```

---

## Tarea 4: `f1_en_k`

**Archivos:**
- Crear: `auxiliar/scripts/evaluar.py`
- Crear: `auxiliar/tests/test_evaluar.py`

**Interfaces:**
- Consume: nada.
- Produce: `f1_en_k(predichos: list[str], relevantes: set[str], k: int) -> float`.
  Las tareas 6 y 7 la llaman.

- [ ] **Paso 1: escribir las pruebas que fallan**

`auxiliar/tests/test_evaluar.py`, archivo nuevo. Los valores están calculados a mano: una
implementación que se prueba contra sí misma no prueba nada.

```python
"""Pruebas del evaluador de métricas contra el ground truth interno.

Los valores esperados están calculados a mano a propósito: comprobar una
implementación de NDCG contra otra copia de la misma fórmula no prueba nada.
"""

import json

import pytest

from scripts.evaluar import f1_en_k


def test_los_tres_aciertos_de_cinco_relevantes_dan_el_techo():
    """3 predichos, 3 aciertos, 5 relevantes: P=1, R=0,6, F1=0,75. Es el techo
    de F1@3 en la mayoría de consultas del ground truth."""
    f1 = f1_en_k(["A", "B", "C"], {"A", "B", "C", "D", "E"}, 3)

    assert f1 == pytest.approx(0.75)


def test_sin_aciertos_el_f1_es_cero():
    assert f1_en_k(["X", "Y", "Z"], {"A", "B"}, 3) == 0.0


def test_un_acierto_de_tres_con_cuatro_relevantes():
    """P=1/3, R=1/4, F1 = 2·(1/3)·(1/4) / (1/3+1/4) = 0,285714."""
    f1 = f1_en_k(["A", "X", "Y"], {"A", "B", "C", "D"}, 3)

    assert f1 == pytest.approx(0.285714, abs=1e-6)


def test_solo_cuentan_los_k_primeros():
    """El acierto en el puesto 4 no entra en F1@3."""
    assert f1_en_k(["X", "Y", "Z", "A"], {"A"}, 3) == 0.0


def test_con_menos_predichos_que_k_no_se_castiga_dos_veces():
    """La precisión es sobre lo entregado, no sobre los puestos vacíos: no
    llenar el top ya se avisa aparte, y contarlo aquí lo penaliza dos veces."""
    assert f1_en_k(["A", "B"], {"A", "B"}, 3) == pytest.approx(1.0)


def test_un_predicho_repetido_no_cuenta_dos_veces():
    """P=2/3 sobre los tres entregados, R=1: F1=0,8."""
    assert f1_en_k(["A", "A", "B"], {"A", "B"}, 3) == pytest.approx(0.8)


def test_sin_relevantes_el_f1_es_cero():
    assert f1_en_k(["A"], set(), 3) == 0.0
```

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -v
```

Esperado: `ModuleNotFoundError: No module named 'scripts.evaluar'`.

- [ ] **Paso 3: crear el script con la primera función**

`auxiliar/scripts/evaluar.py`. La cabecera de `sys.path` copia la de
`verificar_cobertura.py`, que es cómo el resto de scripts importan la raíz:

```python
"""Mide F1@3 y NDCG@10 de un ``resultados.jsonl`` contra el ground truth interno.

Lee artefactos y nada más: no carga el índice ni el encoder, así que corre en un
segundo y sirve para comparar dos configuraciones sin reconstruir nada.

Qué mide cada número y por qué manda el NDCG binario:
``docs/decisiones/recuperacion-y-entregable.md`` §10.

Uso::

    python auxiliar/scripts/evaluar.py --resultados resultados.jsonl \
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
```

- [ ] **Paso 4: correr las pruebas y verificar que pasan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -v
```

Esperado: 7 passed.

- [ ] **Paso 5: commit** *(condicional)*

```bash
git add auxiliar/scripts/evaluar.py auxiliar/tests/test_evaluar.py
git commit -m "feat: F1@k del evaluador"
```

---

## Tarea 5: `ndcg_en_k`

**Archivos:**
- Modificar: `auxiliar/scripts/evaluar.py`
- Modificar: `auxiliar/tests/test_evaluar.py`

**Interfaces:**
- Consume: nada de tareas previas.
- Produce: `ndcg_en_k(predichos: list[str], ganancias: dict[str, float], k: int)
  -> float`. La tarea 7 la llama dos veces, con ganancias binarias y graduadas.

- [ ] **Paso 1: escribir las pruebas que fallan**

Añadir a `auxiliar/tests/test_evaluar.py`, y `ndcg_en_k` al import:

```python
def test_los_relevantes_en_cabeza_dan_ndcg_uno():
    ganancias = {f"c{n}": 1.0 for n in range(1, 6)}

    assert ndcg_en_k(["c1", "c2", "c3", "c4", "c5"], ganancias, 10) == pytest.approx(1.0)


def test_sin_aciertos_el_ndcg_es_cero():
    assert ndcg_en_k(["x", "y"], {"c1": 1.0}, 10) == 0.0


def test_el_orden_no_cambia_el_binario_pero_si_el_graduado():
    """Los cinco aciertos, del peor al mejor. En binario da 1,0 porque están
    todos; en graduado cae a 0,722243 porque el rank 5 ocupa el puesto 1."""
    binarias = {f"c{n}": 1.0 for n in range(1, 6)}
    graduadas = {"c1": 5.0, "c2": 4.0, "c3": 3.0, "c4": 2.0, "c5": 1.0}
    invertido = ["c5", "c4", "c3", "c2", "c1"]

    assert ndcg_en_k(invertido, binarias, 10) == pytest.approx(1.0)
    assert ndcg_en_k(invertido, graduadas, 10) == pytest.approx(0.722243, abs=1e-6)


def test_un_no_relevante_arriba_consume_su_puesto():
    """No se salta: ocupa el puesto 1 con ganancia 0 y empuja a los buenos.
    DCG = 0/1 + 1/log2(3) + 1/log2(4) = 1,130930; IDCG = 1 + 1/log2(3) =
    1,630930; NDCG = 0,693426."""
    ganancias = {"c1": 1.0, "c2": 1.0}

    assert ndcg_en_k(["x", "c1", "c2"], ganancias, 10) == pytest.approx(
        0.693426, abs=1e-6
    )


def test_solo_cuentan_los_k_primeros_del_ndcg():
    ganancias = {"c1": 1.0}

    assert ndcg_en_k(["x", "y", "z", "c1"], ganancias, 3) == 0.0


def test_sin_ganancias_el_ndcg_es_cero():
    assert ndcg_en_k(["c1"], {}, 10) == 0.0


def test_el_ideal_no_pasa_de_k():
    """Con 5 relevantes y k=3, el ideal son los 3 mejores, no los 5: si no, el
    máximo alcanzable sería inalcanzable y todo NDCG@3 saldría deprimido."""
    ganancias = {f"c{n}": 1.0 for n in range(1, 6)}

    assert ndcg_en_k(["c1", "c2", "c3"], ganancias, 3) == pytest.approx(1.0)
```

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -k ndcg -v
```

Esperado: `ImportError: cannot import name 'ndcg_en_k'`.

- [ ] **Paso 3: implementar**

En `auxiliar/scripts/evaluar.py`, tras `f1_en_k`:

```python
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
```

- [ ] **Paso 4: correr las pruebas y verificar que pasan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -v
```

Esperado: 14 passed.

- [ ] **Paso 5: commit** *(condicional)*

```bash
git add auxiliar/scripts/evaluar.py auxiliar/tests/test_evaluar.py
git commit -m "feat: NDCG@k del evaluador"
```

---

## Tarea 6: cargar el ground truth y calcular el techo

**Archivos:**
- Modificar: `auxiliar/scripts/evaluar.py`
- Modificar: `auxiliar/tests/test_evaluar.py`

**Interfaces:**
- Consume: nada.
- Produce: `doc_id_de(chunk_id) -> str`, la dataclass `ConsultaEtiquetada`
  (campos `query_id: str`, `fragmentos: dict[str, int]`, `documentos: set[str]`),
  `cargar_ground(ruta: Path) -> list[ConsultaEtiquetada]` y
  `techo_f1(relevantes: int, k: int) -> float`. La tarea 7 usa las cuatro.

- [ ] **Paso 1: escribir las pruebas que fallan**

Añadir a `auxiliar/tests/test_evaluar.py` un helper y las pruebas. El helper escribe un
ground truth con la forma real del archivo de ADL:

```python
def escribir_ground(ruta, consultas):
    """Un ground_truth.json con la forma del real: lista de consultas etiquetadas."""
    ruta.write_text(
        json.dumps(consultas, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return ruta


def consulta_etiquetada(query_id, chunk_ids):
    return {
        "question_id": query_id,
        "question": f"pregunta de {query_id}",
        "relevant_chunks": [
            {
                "rank": posicion,
                "chunk_id": chunk_id,
                "source_file": "archivo.pdf",
                "excerpt": "...",
            }
            for posicion, chunk_id in enumerate(chunk_ids, start=1)
        ],
        "notes": "",
    }


def test_el_doc_id_sale_del_chunk_id():
    assert doc_id_de("F1-CSET-110-c0029") == "F1-CSET-110"


def test_un_chunk_id_con_formato_raro_detiene_la_evaluacion():
    """Derivar mal un doc_id inventa aciertos o los pierde, y ninguna de las dos
    cosas se nota en el número final."""
    with pytest.raises(ValueError, match="inesperado"):
        doc_id_de("F1-CSET-110")


def test_el_ground_agrupa_fragmentos_y_documentos(tmp_path):
    ruta = escribir_ground(
        tmp_path / "ground.json",
        [consulta_etiquetada("q001", ["F1-A-001-c0001", "F1-A-001-c0002", "F1-B-002-c0003"])],
    )

    etiquetadas = cargar_ground(ruta)

    assert len(etiquetadas) == 1
    assert etiquetadas[0].query_id == "q001"
    assert etiquetadas[0].fragmentos == {
        "F1-A-001-c0001": 1,
        "F1-A-001-c0002": 2,
        "F1-B-002-c0003": 3,
    }
    assert etiquetadas[0].documentos == {"F1-A-001", "F1-B-002"}


def test_el_techo_de_tres_sobre_cinco_relevantes():
    """El caso de 28 de las 50 consultas del ground truth real."""
    assert techo_f1(5, 3) == pytest.approx(0.75)


def test_el_techo_de_tres_sobre_cuatro_relevantes():
    assert techo_f1(4, 3) == pytest.approx(0.857142, abs=1e-6)


def test_con_tantos_relevantes_como_puestos_el_techo_es_uno():
    assert techo_f1(3, 3) == pytest.approx(1.0)


def test_con_menos_relevantes_que_puestos_el_techo_baja_por_precision():
    """2 relevantes y 3 puestos: uno sobra por fuerza. P=2/3, R=1, F1=0,8."""
    assert techo_f1(2, 3) == pytest.approx(0.8)
```

Añadir al import: `ConsultaEtiquetada`, `cargar_ground`, `doc_id_de`, `techo_f1`.

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -k "doc_id or ground or techo" -v
```

Esperado: `ImportError: cannot import name 'doc_id_de'`.

- [ ] **Paso 3: implementar**

En `auxiliar/scripts/evaluar.py`, tras los imports y antes de `f1_en_k`:

```python
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
```

- [ ] **Paso 4: correr las pruebas y verificar que pasan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -v
```

Esperado: 21 passed.

- [ ] **Paso 5: comprobar contra el ground truth real**

```bash
python -c "import sys; sys.path.insert(0,'.'); from scripts.evaluar import cargar_ground, techo_f1; g=cargar_ground('auxiliar/ground/ground_truth.json'); print(len(g), round(sum(techo_f1(len(c.documentos),3) for c in g)/len(g),4))"
```

Esperado: `50 0.7989`. Si el techo no da 0,7989, el ground truth cambió desde el
diseño y hay que revisar el spec antes de seguir.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add auxiliar/scripts/evaluar.py auxiliar/tests/test_evaluar.py
git commit -m "feat: carga del ground truth y techo de F1@3"
```

---

## Tarea 7: el script y su salida

**Archivos:**
- Modificar: `auxiliar/scripts/evaluar.py`
- Modificar: `auxiliar/tests/test_evaluar.py`

**Interfaces:**
- Consume: `f1_en_k`, `ndcg_en_k`, `cargar_ground`, `techo_f1`,
  `ConsultaEtiquetada` de las tareas 4-6.
- Produce: `cargar_resultados(ruta) -> dict[str, dict]`,
  `evaluar(etiquetadas, resultados, k_documentos, k_fragmentos) -> Reporte` y
  `main(argv) -> int`. Nada depende de ellas después.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def escribir_resultados(ruta, registros):
    ruta.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros),
        encoding="utf-8",
        newline="\n",
    )
    return ruta


def registro(query_id, doc_ids, chunk_ids=None):
    linea = {
        "query_id": query_id,
        "consulta": "da igual",
        "documentos": [
            {"puesto": n, "doc_id": doc_id} for n, doc_id in enumerate(doc_ids, start=1)
        ],
    }
    if chunk_ids is not None:
        linea["fragmentos"] = [
            {"puesto": n, "chunk_id": chunk_id, "doc_id": doc_id_de(chunk_id)}
            for n, chunk_id in enumerate(chunk_ids, start=1)
        ]
    return linea


def test_el_acierto_perfecto_da_las_tres_metricas_al_maximo(tmp_path):
    ground = escribir_ground(
        tmp_path / "g.json",
        [consulta_etiquetada("q001", [f"F1-A-00{n}-c0001" for n in range(1, 4)])],
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001", "F1-A-002", "F1-A-003"],
                  [f"F1-A-00{n}-c0001" for n in range(1, 4)])],
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.f1 == pytest.approx(1.0)
    assert reporte.ndcg_binario == pytest.approx(1.0)
    assert reporte.ndcg_graduado == pytest.approx(1.0)
    assert reporte.techo_f1 == pytest.approx(1.0)


def test_una_consulta_del_ground_que_falta_detiene_la_evaluacion(tmp_path):
    """Promediar sobre las que sí están devuelve un número que no compara con nada."""
    ground = escribir_ground(
        tmp_path / "g.json",
        [consulta_etiquetada("q001", ["F1-A-001-c0001"]),
         consulta_etiquetada("q002", ["F1-A-002-c0001"])],
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"])]
    )

    with pytest.raises(ValueError, match="q002"):
        evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)


def test_las_consultas_de_mas_se_ignoran(tmp_path):
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"]),
         registro("q999", ["F1-Z-999"], ["F1-Z-999-c0001"])],
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.n_consultas == 1
    assert reporte.n_ignoradas == 1


def test_sin_fragmentos_se_mide_f1_y_no_ndcg(tmp_path):
    """Un entregable de antes de esta pieza sigue siendo medible a medias."""
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"])]
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.f1 > 0.0
    assert reporte.ndcg_binario is None
    assert reporte.ndcg_graduado is None


def test_un_query_id_repetido_en_los_resultados_es_un_error(tmp_path):
    """Dos líneas para la misma consulta y no se sabe cuál se evalúa."""
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001"]), registro("q001", ["F1-B-002"])],
    )

    with pytest.raises(ValueError, match="q001"):
        cargar_resultados(resultados)


def test_el_main_imprime_las_metricas_y_sale_con_cero(tmp_path, capsys):
    """Mide, no verifica: el que falla con 1 es verificar_cobertura.py."""
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"])]
    )

    codigo = main(["--resultados", str(resultados), "--ground", str(ground)])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "F1@3" in salida
    assert "NDCG@10" in salida
```

Añadir al import: `cargar_resultados`, `evaluar`, `main`.

- [ ] **Paso 2: correr las pruebas y verificar que fallan**

```bash
python -m pytest auxiliar/tests/test_evaluar.py -k "perfecto or falta or ignoran or sin_fragmentos or repetido or main" -v
```

Esperado: `ImportError: cannot import name 'cargar_resultados'`.

- [ ] **Paso 3: implementar la carga de resultados**

En `auxiliar/scripts/evaluar.py`, tras `cargar_ground`:

```python
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
```

- [ ] **Paso 4: implementar el reporte y `evaluar`**

```python
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
```

- [ ] **Paso 5: implementar `main`**

```python
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
    parser.add_argument(
        "--detalle", action="store_true", help="una línea por consulta"
    )
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
```

El techo se imprime porque sin él un 0,61 se lee como fracaso cuando puede ser el
77 % de lo que el formato del entregable permite.

- [ ] **Paso 6: correr la suite entera**

```bash
python -m pytest
```

Esperado: todo verde.

- [ ] **Paso 7: commit** *(condicional)*

```bash
git add auxiliar/scripts/evaluar.py auxiliar/tests/test_evaluar.py
git commit -m "feat: auxiliar/scripts/evaluar.py mide F1@3 y NDCG@10"
```

---

## Tarea 8: la corrida real

**Archivos:** ninguno de código. Produce `resultados.jsonl` con
`fragmentos[]` y los números de las métricas.

**Interfaces:**
- Consume: `--top-fragmentos` (tarea 3) y `auxiliar/scripts/evaluar.py` (tarea 7).
- Produce: las cifras que la tarea 9 escribe en la bitácora.

**Requisitos:** carga `indice/index.faiss` (412 MB) y el checkpoint del encoder,
y codifica 50 consultas. Del orden de minutos en GPU. **El índice no se
reconstruye.**

- [ ] **Paso 1: guardar el entregable actual**

```bash
cp resultados.jsonl resultados.anterior.jsonl
```

Es la referencia con la que se comprueba que `documentos[]` no cambió.

- [ ] **Paso 2: re-correr solo la fase de respuesta**

```bash
python generador.py --indice base_vectorial/encoder_granite-embedding-311m-multilingual-r2 \
    --consultas base_documental/Extracto_Preguntas_50_v2.pdf \
    --resultados resultados.jsonl \
    --top-fragmentos 10
```

Esperado: `50 consultas respondidas en resultados.jsonl` y, por stderr, el
resumen del índice. Si aparece el AVISO de consultas sin 10 fragmentos, anotarlo:
esas tienen techo en NDCG@10.

- [ ] **Paso 3: verificar que `documentos[]` no cambió**

```bash
python -c "
import json
a=[json.loads(l)['documentos'] for l in open('resultados.anterior.jsonl',encoding='utf-8')]
b=[json.loads(l)['documentos'] for l in open('resultados.jsonl',encoding='utf-8')]
print('identicos' if a==b else 'CAMBIARON')
n=[len(json.loads(l).get('fragmentos',[])) for l in open('resultados.jsonl',encoding='utf-8')]
print('fragmentos por consulta:', min(n), '-', max(n))
"
```

Esperado: `identicos` y `fragmentos por consulta: 10 - 10`. Si sale `CAMBIARON`,
parar: algo de las tareas 1-3 tocó el camino de los documentos, y eso invalida la
comparación con la corrida anterior.

- [ ] **Paso 4: medir**

```bash
python auxiliar/scripts/evaluar.py --resultados resultados.jsonl \
    --ground auxiliar/ground/ground_truth.json
```

Anotar los cuatro números: F1@3, su techo, NDCG@10 binario y graduado.

- [ ] **Paso 5: mirar las peores consultas**

```bash
python auxiliar/scripts/evaluar.py --resultados resultados.jsonl \
    --ground auxiliar/ground/ground_truth.json --detalle
```

Anotar las consultas con F1 en 0,0: son las que no acertaron ni un documento y
las primeras candidatas a explicar qué falla. No se arregla nada aquí; es
diagnóstico para decidir después.

- [ ] **Paso 6: borrar la copia de seguridad**

```bash
rm resultados.anterior.jsonl
```

- [ ] **Paso 7: commit** *(condicional)*

```bash
git add resultados.jsonl
git commit -m "chore: entregable con el top-10 de fragmentos"
```

---

## Tarea 9: la bitácora

**Archivos:**
- Modificar: `docs/decisiones/recuperacion-y-entregable.md`
- Modificar: `README.md`

**Interfaces:**
- Consume: las cifras de la tarea 8.
- Produce: nada de código.

- [ ] **Paso 1: reescribir §7 de `recuperacion-y-entregable.md`**

Sustituir el §7 actual («El entregable») por una versión que describa las dos
vistas: `documentos[]` es §8.6 y no cambió; `fragmentos[]` es el top-10 que mide
NDCG@10; las dos salen del mismo top-k de FAISS, no de dos búsquedas; y
`registro_de_resultado` sigue siendo el único sitio que toca el JSON. Mencionar
que con 3 ítems el NDCG@10 tenía un techo de 0,7227 y por eso se añadió.

- [ ] **Paso 2: añadir §10 a `recuperacion-y-entregable.md`**

Sección nueva «La medición», al final del archivo, con:

- qué mide `auxiliar/scripts/evaluar.py` y por qué es un script puro sobre artefactos
  (evalúa cualquier `resultados.jsonl`, así se comparan configuraciones);
- por qué el binario es el que se reporta y el graduado es diagnóstico;
- por qué se imprime el techo de F1@3, con el 0,7989 de este ground truth y su
  desglose (28 consultas con 5 documentos relevantes, 20 con 4, una con 3, una
  con 2);
- **los números medidos en la tarea 8**, con la fecha de la corrida;
- que el ground truth se construyó con BM25, así que cualquier medición futura de
  un híbrido BM25 saldrá sesgada a favor.

- [ ] **Paso 3: actualizar el README**

- En la tabla de opciones de `generador.py`, la fila:
  `| \`--top-fragmentos\` | Fragmentos por consulta en el entregable, para NDCG@10 (10). \`0\` los apaga. |`
- En «Verificar», el comando del evaluador junto a los dos scripts que ya están.
- En la ficha técnica, sustituir la fila de métricas por los valores medidos, con
  el techo al lado, y **borrar** el párrafo que dice que todavía no están
  medidas.
- En «Pendiente», quitar el punto de medir F1@3 y NDCG@10.

- [ ] **Paso 4: comprobar que no queda ninguna afirmación falsa**

```bash
grep -rn "no están medidas\|todavía no\|sin medir" README.md docs/decisiones/
```

Esperado: ninguna coincidencia que se refiera a las métricas.

- [ ] **Paso 5: correr la suite completa una última vez**

```bash
python -m pytest
```

Esperado: todo verde.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add README.md docs/
git commit -m "docs: métricas medidas en la bitácora"
```

---

## Auto-revisión del plan

**Cobertura del spec:**

| Sección del spec | Tarea |
|---|---|
| §3 entregable con dos vistas | 2 |
| §4.1 `mejores_fragmentos` | 1 |
| §4.2 firma y reporte | 2, 3 |
| §4.3 CLI | 3 |
| §5.1 las dos funciones | 4, 5 |
| §5.2 documentos relevantes | 6 |
| §5.3 el techo | 6 (cálculo), 7 (impresión) |
| §5.4 qué detiene la evaluación | 6 (chunk_id), 7 (consultas y fragmentos) |
| §6 pruebas | en cada tarea |
| §7 bitácora | 9 |
| §8 orden de trabajo | 1-9 |

Sin huecos.

**Consistencia de nombres:** `mejores_fragmentos`, `TOP_FRAGMENTOS`,
`top_fragmentos`, `f1_en_k`, `ndcg_en_k`, `doc_id_de`, `cargar_ground`,
`cargar_resultados`, `techo_f1`, `evaluar`, `ConsultaEtiquetada`, `Reporte`.
Cada uno se define en una tarea y se usa con la misma firma en las siguientes.

**Riesgo conocido:** la tarea 3 añade campos con valor por defecto a
`ReporteConsultas`, que es `frozen` y tiene campos sin defecto por delante. Van al
final por eso. Si alguna prueba existente construye `ReporteConsultas`
posicionalmente, fallará; una búsqueda de `ReporteConsultas(` en `tests/` antes
de empezar la tarea 3 lo descarta en diez segundos.
