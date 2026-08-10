# Qué columnas de un dataset entran al vector

**Fecha:** 2026-08-09
**Estado:** implementado (`extractores/tabular.py`), tope de filas pendiente de decisión

## El problema

Siete archivos del corpus son exportaciones bibliográficas —cinco de PubMed,
lit-covid en CSV y en XLSX— y suman **256.383 filas**. Cada fila se serializaba
entera:

```
columna_1: 0 | PMID: 11204229 | Title: Artificial neural networks in liquid
chromatography: efficient and improved quantitative structure-retention
relationship models | Authors: Loukas YL. | Citation: J Chromatogr A. 2000 Dec
29;904(2):119-29. doi: 10.1016/s0021-9673(00)00923-7. | First Author: Loukas YL
| Journal/Book: J Chromatogr A | Publication Year: 2000 | Create Date:
2001/02/24 | PMCID: PMC123 | NIHMS ID: NIHMS456 | DOI: 10.1016/s0021-9673(00)00923-7
```

52 palabras, de las cuales menos de la mitad tienen carga semántica. `PMID`,
`PMCID`, `NIHMS ID`, `DOI` y `Create Date` son identificadores: nadie los
recupera por semejanza, y su presencia diluye el único campo que sí se
recupera, que es el título. `columna_1` es el índice de fila del export.

## La decisión

Una **lista blanca declarada** de columnas indexables por esquema de export
(`ESQUEMAS` en `extractores/tabular.py`). Lo que no está en la lista sale del
texto y viaja en `Bloque.datos` → `Fragmento.datos` → `metadata.jsonl`, como
campo adicional de los que permite §3.4. No se pierde nada: deja de pesar en el
vector y sigue siendo citable.

```
Title: Artificial neural networks in liquid chromatography: efficient and
improved quantitative structure-retention relationship models | Authors:
Loukas YL. | Journal/Book: J Chromatogr A | Publication Year: 2000
```

29 palabras. **−46 % de caracteres** sobre las 264.264 filas de CSV del corpus.

### Declarada, no inferida

Se identifican por la **cabecera** y no por el nombre del archivo: el nombre
cambia entre descargas, la cabecera es el export en sí. La firma es un
subconjunto requerido porque cuatro de los cinco CSV de PubMed traen una
columna de índice sin nombre y el quinto no.

No se infiere con una heurística del tipo «esto parece un identificador» por
dos razones: §4.2 exige determinismo, y una heurística falla en silencio ante
un export nuevo. Una cabecera que no case con ningún esquema **se indexa
entera**, que es el comportamiento anterior.

## El efecto colateral que hay que decidir

`ConfigFragmentacion.min_palabras = 40` es el umbral por debajo del cual
`_agrupar_registros` junta registros contiguos. Las filas de PubMed medían 52
palabras y por eso iban solas —una fila, un fragmento—. Limpias miden 29, cruzan
el umbral y **empiezan a agruparse**:

```
5000 filas de pubmed-artificial-intelligence
    antes:  4995 fragmentos (55 palabras de media)
    ahora:  1229 fragmentos (123 palabras de media, ~5 artículos por vector)
```

Medido sobre los siete archivos completos, sin tope de filas:

| escenario | fragmentos | palabras |
|---|---:|---:|
| A — agrupa (config actual) | 108.826 | 8,84 M |
| B — `min_palabras=1`, un registro por fragmento | 257.147 | 8,69 M |

Coste de codificación medido en la GPU del proyecto (RTX, ~10.000 tokens/s
saturada): **A ≈ 26 min, B ≈ 28 min**. Prácticamente el mismo, porque es el
mismo texto repartido de otra forma. La diferencia real está en el número de
vectores (634 MB frente a 1,07 GB de `index.faiss`) y en la recuperación: un
vector que mezcla cinco artículos distintos recupera peor cualquiera de ellos.

## Tasas medidas

Sobre lotes **homogéneos** de texto bibliográfico, en la misma RTX 4050 de 6 GB:

| longitud del fragmento | lote 2 | lote 8 | lote 16 |
|---|---:|---:|---:|
| 54 tokens (un registro) | 104 frag/s | **182 frag/s** | 180 frag/s |
| 325 tokens (seis agrupados) | 28 frag/s | **33 frag/s** | 32 frag/s |

Esto **no contradice** la tabla del README, que mide sobre una muestra
sistemática del corpus y favorece `--lote 2`. Lo que se paga en un lote es el
padding al texto más largo que lleve dentro: en una muestra heterogénea, meter
ocho fragmentos en un lote significa paddear siete de 50 tokens hasta los 500
del octavo, y ahí el lote pequeño gana. En estos datasets todos los fragmentos
miden lo mismo, no hay padding que pagar y el lote grande gana.

La consecuencia práctica: si los exports bibliográficos pasan a ser mayoría de
los vectores, conviene **volver a medir** el lote óptimo sobre la mezcla nueva
en vez de heredar el 2 de la tabla anterior.
