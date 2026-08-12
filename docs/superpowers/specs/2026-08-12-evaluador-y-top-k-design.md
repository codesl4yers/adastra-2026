# Diseño — Evaluador de métricas y top-k de fragmentos

CODEFEST Ad Astra 2026 · Etapa 1 · 12 ago 2026
Estado: **aprobado, sin implementar**.

Dos piezas que van juntas porque la primera no se puede medir sin la segunda:
el entregable pasa a emitir un top-k de fragmentos además del top-3 de
documentos, y un script nuevo mide F1@3 y NDCG@10 contra el ground truth
interno.

Ninguna de las dos toca el índice. Se re-corre solo la fase de respuesta.

---

## 1. El problema

`resultados.jsonl` entrega hoy 3 documentos por consulta, cada uno con el
fragmento que lo metió en el top-3. Eso da 3 ítems por consulta.

**NDCG@10 sobre una lista de 3 tiene un techo aritmético de 0,7227** —con cinco
relevantes por consulta y ganancia binaria, `DCG(3)/IDCG(5)`—. Se pierden 27
puntos de la métrica aunque las tres sean perfectas. Con 10 ítems el techo sube
a 1,0.

Y no hay con qué medir: `ground/ground_truth.json` existe desde el 11 de agosto y
nada lo lee. Cualquier decisión posterior —reranking, híbrido, el grafo de
conocimiento— se toma a ciegas mientras no haya línea base.

## 2. Alcance

Entra:

- `generador.py`: top-k de fragmentos en el entregable, con opción de CLI.
- `scripts/evaluar.py`: F1@3 y NDCG@10 contra el ground truth.
- Pruebas de las dos piezas.
- Bitácora y README.

No entra: cross-encoder, BM25 híbrido, cambio de la clave de `indice.py`,
`chunk_id` como entero de FAISS. Los cuatro se analizaron y quedaron fuera por
decisión explícita —los dos últimos no mueven la métrica; los dos primeros
esperan a tener línea base y a ver qué aporta el grafo de conocimiento—.

## 3. El entregable con dos vistas

Cada línea de `resultados.jsonl` lleva las dos listas. `documentos[]` no cambia
ni un campo:

```json
{
  "query_id": "q001",
  "consulta": "¿Cómo está transformando la inteligencia artificial...",
  "documentos": [
    {"puesto": 1, "doc_id": "F3-SIPRI-111", "fuente": "...", "score": 0.905521,
     "n_fragmentos": 15, "chunk_id": "F3-SIPRI-111-c0036", "pagina": 20, "texto": "..."}
  ],
  "fragmentos": [
    {"puesto": 1, "chunk_id": "F3-SIPRI-111-c0036", "doc_id": "F3-SIPRI-111",
     "fuente": "...", "score": 0.905521, "pagina": 20, "texto": "..."}
  ]
}
```

`documentos[]` es §8.6 —F1@3 se mide exactamente sobre tres— y `fragmentos[]` es
el top-10 de NDCG@10. Son vistas del mismo top-k de FAISS, no dos búsquedas.

**Por qué en el mismo archivo y no en uno aparte:** dos archivos hay que
mantenerlos alineados, y una desalineación entre ellos sería del mismo tipo que
la que `responder_consultas` ya vigila entre índice y metadata. Si ADL fija un
esquema que no admite el campo, se borra en `registro_de_resultado`, que sigue
siendo el único sitio que toca el JSON del entregable.

## 4. `generador.py`

```python
TOP_FRAGMENTOS = 10   # el 10 de NDCG@10
```

### 4.1 `mejores_fragmentos`

```python
def mejores_fragmentos(candidatos: list[Candidato], top: int) -> list[Candidato]:
    """Los ``top`` mejores fragmentos, ya filtrados y deduplicados."""
```

Los candidatos llegan ordenados por score desde `banco.search` y ya pasaron
`filtrar_por_idioma` y `deduplicar_por_texto`, así que el cuerpo es un corte.
Tiene función propia por dos motivos: se prueba aparte, y es el punto donde se
engancha un reranking el día que se decida —entre la dedup y el corte, sin tocar
nada más—.

Con `top <= 0` devuelve la lista vacía y `fragmentos[]` no se escribe: el
entregable sale exactamente como hoy.

### 4.2 Firma y reporte

`responder_consultas` gana `top_fragmentos: int = TOP_FRAGMENTOS`.
`registro_de_resultado(consulta, documentos, fragmentos)` recibe las dos listas.

`ReporteConsultas` gana dos campos: `top_fragmentos` y
`consultas_sin_fragmentos_completos`, esta última con el mismo tratamiento que
`consultas_sin_top_completo` —se nombran por stderr, no se rellena—. Con `k=50` y
17 descartes por dedup en las 50 consultas de la última corrida, llegar a 10 está
holgado; el aviso está para el día que deje de estarlo.

### 4.3 CLI

| Opción | Qué hace |
|---|---|
| `--top-fragmentos` | Fragmentos por consulta en el entregable (10). `0` lo desactiva. |

`--top` sigue siendo el top-3 de documentos y no cambia de valor por defecto.

## 5. `scripts/evaluar.py`

```bash
python scripts/evaluar.py --resultados entrega/resultados.jsonl \
                          --ground ground/ground_truth.json [--detalle]
```

Script puro sobre artefactos, como `verificar_cobertura.py`: no importa `torch`
ni `faiss`, no carga el índice, corre en menos de un segundo. Evalúa cualquier
`resultados.jsonl`, que es lo que permite comparar dos configuraciones sin tocar
el evaluador.

### 5.1 Las dos funciones con lógica

```python
def f1_en_k(predichos: list[str], relevantes: set[str], k: int) -> float
def ndcg_en_k(predichos: list[str], ganancias: dict[str, float], k: int) -> float
```

`f1_en_k` sobre `doc_id`: precisión sobre los `k` predichos, cobertura sobre los
relevantes, media armónica. Con precisión y cobertura ambas cero devuelve 0,0 en
vez de dividir.

`ndcg_en_k` sobre `chunk_id`:

```
DCG@k  = Σ  g_i / log2(i + 1)          i = 1..k, g_i = ganancia del predicho i
IDCG@k = Σ  g / log2(j + 1)            g ordenadas de mayor a menor, j = 1..min(k, |relevantes|)
NDCG@k = DCG@k / IDCG@k                0,0 si IDCG@k = 0
```

Se llama dos veces con ganancias distintas:

- **binario**: `1,0` si el `chunk_id` está etiquetado. Es el número que se
  reporta como NDCG@10, porque es la lectura estándar y la que casi con
  seguridad aplica el jurado.
- **graduado**: `6 - rank`, o sea 5 para el rank 1 y 1 para el rank 5.
  Diagnóstico, no métrica. Si sale muy por debajo del binario, encontramos los
  fragmentos correctos y los ordenamos mal.

Un predicho que no está etiquetado tiene ganancia 0 y **consume su posición**: no
se salta ni desplaza a los siguientes. Ese es justamente el descuento que hace
que colocar basura en el puesto 1 duela.

El graduado no manda a propósito: quien etiquetó ordenó por juicio humano sobre
un pool de BM25, no midió utilidad relativa. Tratar ese orden como escala
calibrada le daría un peso que no tiene.

### 5.2 Los documentos relevantes

Salen del propio `chunk_id`: `chunk_id.rsplit("-c", 1)[0]`. Los 234 del ground
truth encajan en `<doc_id>-c<4 dígitos>` —comprobado—, así que no hace falta
abrir los 200 MB de `metadata.jsonl` para resolver 234 identificadores.

Si algún `chunk_id` no encaja en ese formato, el evaluador se detiene: derivar un
`doc_id` equivocado inventaría aciertos o los perdería, y ninguna de las dos
cosas se nota en el número final.

### 5.3 El techo

Cada métrica se reporta junto a su máximo alcanzable. **Las cifras del ejemplo
son inventadas**: la única medida es el 0,799 del techo, que no depende del
sistema sino del ground truth.

```
consultas evaluadas   50

F1@3         0.612   de 0.799 alcanzable   (76.6%)     <- ejemplo
NDCG@10      0.548   binario                           <- ejemplo
NDCG@10      0.501   graduado                          <- ejemplo
```

**El techo de F1@3 es 0,7989** sobre este ground truth: 28 consultas tienen 5
documentos relevantes distintos, 20 tienen 4, una tiene 3 y una tiene 2, y
entregando 3 el máximo por consulta es 0,75 / 0,857 / 1,0 / 0,8 respectivamente.
Se calcula, no se escribe a mano: depende del ground truth que se le pase.

Sin esa columna un 0,61 se lee como fracaso cuando puede ser el 77 % de lo que el
formato del entregable permite. El techo de NDCG@10 con 10 fragmentos es 1,0 y
por eso no se imprime.

`--detalle` añade una línea por consulta con sus tres métricas, para localizar
las que fallan.

### 5.4 Qué detiene la evaluación y qué no

| Situación | Qué hace |
|---|---|
| Falta en `resultados.jsonl` una consulta del ground truth | **Se detiene.** Evaluar 47 de 50 y promediar sobre 47 devuelve un número que no es comparable con nada. |
| Sobran consultas en `resultados.jsonl` | Se ignoran, con aviso del número. |
| Ninguna línea lleva `fragmentos[]` | Reporta F1@3 y, en lugar de los NDCG, la orden de re-correr con `--top-fragmentos 10`. Un entregable viejo sigue siendo medible a medias. |
| `chunk_id` del ground truth con formato inesperado | **Se detiene** (§5.2). |

Código de salida 0 salvo error de entrada: esto mide, no verifica. El que falla
con 1 es `verificar_cobertura.py`, que comprueba un piso.

## 6. Pruebas

Rojo antes que verde, y las métricas con números calculados a mano —una
implementación de NDCG que se prueba contra sí misma no prueba nada—.

`tests/test_evaluar.py`:

- F1@3: acierto perfecto, cero aciertos, aciertos parciales, lista más corta que
  `k`, y el caso `k > |relevantes|`.
- NDCG@10: acierto perfecto en orden = 1,0; los mismos aciertos en orden
  invertido separan binario de graduado; ningún acierto = 0,0; un no relevante
  intercalado baja el resultado sin desplazar a los que van detrás.
- El techo: una consulta con 5 documentos relevantes da 0,75.
- Los cuatro casos de §5.4.

`tests/test_generador.py` amplía: el corte a `top`, `top=0`, menos candidatos que
`top`, y que `fragmentos[]` sale ordenado y con los campos de §3.

## 7. Bitácora

- `docs/decisiones/recuperacion-y-entregable.md`: §7 pasa a describir las dos
  vistas; entra un §10 con qué mide el evaluador, por qué manda el binario y por
  qué se reporta el techo.
- `README.md`: `--top-fragmentos` en la tabla del generador, `evaluar.py` en
  «Verificar», y la ficha técnica deja de decir que las métricas no están
  medidas —en cuanto se corran—.

## 8. Orden de trabajo

1. `mejores_fragmentos` y el campo `fragmentos[]`, con sus pruebas.
2. La CLI `--top-fragmentos` y el reporte.
3. `f1_en_k` y `ndcg_en_k`, con sus pruebas.
4. El script y su salida.
5. Re-correr la fase de respuesta —solo respuesta, el índice no se toca— y medir.
6. Bitácora y README con los números reales.

Los pasos 1-4 no necesitan GPU ni índice. El 5 sí: carga `index.faiss` y codifica
50 consultas, del orden de minutos.
