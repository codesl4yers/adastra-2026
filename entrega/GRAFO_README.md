# Componente Bonus — Grafo de Conocimiento

**CODEFEST AD ASTRA 2026 · Etapa 1 · Sección 7 de la Especificación Técnica**
Equipo **CodeSlayers**

---

## 1. Qué se entrega

Ubicación según la estructura de entrega exigida en la Sección 1.4 de la
especificación: el grafo va en una subcarpeta `grafo/` **dentro de
`base_vectorial/`**, al mismo nivel que las carpetas de cada encoder.

```
entrega/
├── resultados.jsonl
├── generador.py
├── informe_tecnico.pdf
├── generador_grafo.py          ← todo el componente, un solo archivo
└── base_vectorial/
    ├── encoder_granite-embedding-311m-multilingual-r2/
    │   ├── index.faiss
    │   └── metadata.jsonl
    └── grafo/
        ├── grafo.graphml               (44,7 MB)
        └── grafo_estadisticas.json
```

| Archivo | Descripción |
|---|---|
| `base_vectorial/grafo/grafo.graphml` | Grafo de conocimiento del corpus completo |
| `base_vectorial/grafo/grafo_estadisticas.json` | Métricas de la construcción |
| `generador_grafo.py` | Componente completo y autocontenido (1.692 líneas) |
| `auxiliares/metadata_ejemplo.jsonl` | Corpus mínimo para probar el pipeline |

`generador_grafo.py` no importa nada del proyecto: su única dependencia de
terceros es NetworkX. Está organizado en cuatro partes:

| Parte | Contenido |
|---|---|
| 1 | Léxico de dominio multilingüe (229 entidades canónicas, 878 variantes) |
| 2 | Reconocimiento de entidades por reglas (NER) |
| 3 | Extracción de relaciones por reglas (RE) |
| 4 | Construcción, agregación y exportación del grafo |

### Cifras del grafo entregado

| Métrica | Valor |
|---|---|
| Fragmentos procesados | 134.317 |
| Fragmentos sin ninguna entidad | 4.340 (3,2 %) |
| Menciones de relación extraídas | 1.164.467 |
| **Nodos** (tras poda) | **24.893** |
| **Aristas** (agregadas) | **100.171** |
| Nodos aislados | 0 |
| Tiempo de extracción | 412,6 s (2 núcleos) |

Distribución de nodos por tipo antes de podar nodos aislados: `NOMBRE` 33.296,
`SIGLA` 5.911, `PAIS` 50, `TEMA` 47, `EMPRESA` 42, `ORG` 33, `AGENCIA` 26,
`REGION` 17, `NORMA` 8.

---

## 2. Decisión de licenciamiento

La organización respondió a nuestra consulta que **los componentes con licencia
CC BY-NC-SA 4.0 no pueden usarse**. Eso descarta los modelos que habitualmente
resuelven esta tarea:

| Componente | Licencia | ¿Se puede usar? |
|---|---|---|
| `Babelscape/wikineural-multilingual-ner` (NER) | CC BY-NC-SA 4.0 | ❌ prohibido |
| `Babelscape/rebel-large` (RE) | CC BY-NC-SA 4.0 | ❌ prohibido |
| `spacy/es_core_news_sm` | GPL-3.0 | ⚠️ copyleft, fuera de la preferencia |
| `spacy/pt_core_news_sm` | CC BY-SA 4.0 | ⚠️ copyleft, fuera de la preferencia |
| `spacy/en_core_web_sm` | MIT | ✅ pero sólo cubre inglés |

Al revisar el stack completo encontramos que el problema era más amplio que los
dos modelos por los que preguntamos: **de los tres modelos spaCy que necesitaba
un pipeline multilingüe, sólo el de inglés es MIT**. El de español es GPL-3.0 y
el de portugués es CC BY-SA 4.0; ninguno de los dos es *NonCommercial*, pero
ambos son copyleft y quedan fuera de la preferencia Apache / MIT / CC BY que
plantea el reto.

**Decisión: eliminar por completo la dependencia de modelos preentrenados.**
El reconocimiento de entidades y la extracción de relaciones se hacen con código
propio del equipo. La única dependencia externa es **NetworkX (BSD-3-Clause)**,
usada sólo para serializar el grafo.

| Componente del pipeline | Origen | Licencia |
|---|---|---|
| `generador_grafo.py` (léxico + NER + RE + grafo) | Equipo CodeSlayers | — (propio) |
| NetworkX | Terceros | BSD-3-Clause ✅ |
| Python stdlib (`re`, `json`, `unicodedata`, …) | Terceros | PSF ✅ |

Esto también satisface la **Sección 8.3**: no interviene ningún modelo
generativo (decoder) en ninguna etapa.

---

## 3. Arquitectura del pipeline

```
metadata.jsonl (Tabla 1)
        │
        ▼
  [1] Lectura en streaming            iterar_chunks()
        │
        ▼
  [2] NER por reglas                  extraer_entidades()   [Parte 2]
        │   a. gazetteer multilingüe con formas canónicas
        │   b. siglas en mayúsculas
        │   c. secuencias capitalizadas (nombres emergentes)
        │   + filtros de ruido de PDF y de etiquetas de campo
        ▼
  [3] RE por reglas                   extraer_tripletas()   [Parte 3]
        │   verbo del lexicón entre dos entidades de la misma oración
        │   → relación tipada; si no hay verbo → relacionado_con
        ▼
  [4] Agregación                      acumular()
        │   nodos: tipo, frecuencia, fenómenos, chunk_ids
        │   aristas: (sujeto, relación, objeto) → peso + trazas
        ▼
  [5] Poda y montaje                  montar_grafo()
        ▼
   grafo.graphml + estadisticas.json
```

### 3.1 Unificación cross-lingüe

El gazetteer mapea **forma canónica → variantes en es/en/pt y siglas**. Las
menciones distintas colapsan en un mismo nodo:

```
"low earth orbit" ┐
"órbita baja terrestre" ├──► nodo «órbita baja terrestre»
"órbita baixa da Terra" │
"LEO", "OBT"            ┘
```

Esto da unificación multilingüe **sin depender del encoder**: el corpus es
75 % inglés, 18 % español y 7 % portugués, y el grafo queda en un único espacio
de nombres. Son 229 entidades canónicas y 878 variantes de superficie.

### 3.2 Trazabilidad grafo ↔ base vectorial (Sección 7.3)

Cada nodo guarda `chunk_ids` y `n_docs`; cada arista guarda `chunk_ids`,
`doc_ids`, `peso` (nº de fragmentos que la respaldan) y `fenomenos`. Con eso se
puede navegar en ambos sentidos: de una entidad del grafo a los fragmentos
indexados en FAISS, y de un fragmento recuperado a las entidades y relaciones
que contiene.

Por tamaño de archivo, cada nodo y cada arista conservan hasta `--max-trazas`
(20 por defecto) identificadores de ejemplo; el conteo exacto de fragmentos se
guarda íntegro en `n_chunks` y `peso`.

### 3.3 Agregación de aristas

En 134.317 fragmentos, emitir una arista por ocurrencia produce ~700.000
aristas redundantes. Las aristas se agregan por `(sujeto, relación, objeto)`,
lo que preserva la información (en `peso`) y hace manejable el GraphML.

---

## 4. Reproducción

```bash
pip install networkx

# Corpus completo, de una sola pasada
python generador_grafo.py \
    --metadata base_vectorial/encoder_granite-embedding-311m-multilingual-r2/metadata.jsonl \
    --output base_vectorial/grafo/grafo.graphml \
    --min-freq-nodo 5 --min-peso-arista 2 --sin-aislados
```

En equipos con poca RAM o con límites de tiempo por proceso, el pipeline admite
**trabajo por lotes**. Los lotes cubren rangos disjuntos de líneas, de modo que
la fusión es exactamente equivalente a una corrida directa (verificado):

```bash
python generador_grafo.py --metadata METADATA --desde 0      --hasta 34000  --parcial lotes/l1.pkl
python generador_grafo.py --metadata METADATA --desde 34000  --hasta 68000  --parcial lotes/l2.pkl
python generador_grafo.py --metadata METADATA --desde 68000  --hasta 102000 --parcial lotes/l3.pkl
python generador_grafo.py --metadata METADATA --desde 102000 --hasta 134317 --parcial lotes/l4.pkl

python generador_grafo.py --fusionar lotes/l*.pkl --output base_vectorial/grafo/grafo.graphml \
    --min-freq-nodo 5 --min-peso-arista 2 --sin-aislados
```

Así se generó el grafo entregado.

### Parámetros

| Opción | Def. | Efecto |
|---|---|---|
| `--min-freq-nodo` | 3 | Frecuencia mínima para entidades heurísticas (SIGLA/NOMBRE). Las del gazetteer nunca se podan. |
| `--min-peso-arista` | 1 | Nº mínimo de fragmentos que deben respaldar una arista. |
| `--max-trazas` | 20 | chunk_ids de ejemplo por nodo/arista. |
| `--sin-capitalizadas` | off | Sólo gazetteer: grafo mucho más pequeño y preciso, menor cobertura. |
| `--sin-aislados` | off | Elimina nodos sin relaciones. |

---

## 5. Validación realizada

1. **Equivalencia lote / corrida directa.** Sobre 3.000 fragmentos, procesar en
   dos lotes y fusionar da exactamente los mismos nodos, aristas, menciones y
   nodos aislados que una corrida única.
2. **Integridad de la trazabilidad.** Se verificaron las **313.615 trazas de
   nodos y 412.674 trazas de aristas** contra los 134.317 `chunk_id` reales del
   `metadata.jsonl`: **0 referencias inválidas**.
3. **Reproducibilidad de las entidades.** En 60 nodos tomados al azar, 59
   vuelven a extraerse al reanalizar el fragmento que la traza señala.
4. **Revisión manual del ruido.** Tres iteraciones sobre muestras de 3.000
   fragmentos, corrigiendo en cada una los falsos positivos observados
   (ver sección 6).

---

## 6. Limitaciones conocidas

Se documentan explícitamente porque afectan cómo debe interpretarse el grafo.

* **Sin manejo de negación ni modalidad.** La relación se toma del verbo
  presente entre las dos entidades, sin analizar polaridad. Ejemplo real del
  grafo: `Irán -[lanza]-> satélite (peso 71)` proviene, entre otros, de un
  fragmento que dice que *Irán probablemente **no** tiene* capacidad de
  desarrollar tecnología de lanzamiento. Las aristas indican **co-mención en un
  contexto verbal**, no aserción factual verificada.
* **La dirección es el orden de aparición**, no el rol sintáctico. Sin árbol de
  dependencias no se distingue sujeto de objeto en voz pasiva.
* **Sin correferencia.** "el país", "la agencia" o los pronombres no se
  resuelven hacia su antecedente.
* **`relacionado_con` domina** (≈84 % de las menciones). Son co-ocurrencias
  oracionales sin verbo del lexicón: útiles como señal de asociación, no como
  afirmación.
* **Ruido residual de documentos estructurados.** Parte del corpus son
  exportaciones tabulares y textos legislativos; sobreviven algunos nodos de
  maquetación (`PUBLAW`, `LAW`, `DEC`, `NIHMS`). Están acotados y son de bajo
  grado, pero existen.
* **Cobertura desigual por idioma en las entidades emergentes.** El gazetteer es
  trilingüe, pero las heurísticas de capitalización favorecen a los idiomas con
  mayor presencia en el corpus.

### Ruido corregido durante el desarrollo

| Problema detectado | Corrección |
|---|---|
| `IA` casaba dentro de *artificial*, *migratoria*, *Colombia* | Límites de palabra + siglas sensibles a mayúsculas |
| `satélite` y `satélites` como nodos distintos | Formas canónicas con variantes |
| `El Pentágono` ≠ `Pentágono` | Eliminación de artículos iniciales, dedup sin acentos |
| `gestion` casaba dentro de *congestión* → relación errónea | Raíces del lexicón ancladas a inicio de palabra |
| `AArrttiiffiicciiaall` (glifos duplicados de PDF) | Detector de glifos duplicados |
| `Chart`, `Preview`, `Technical Appendix` como entidades | Listas de maquetación + núcleos estructurales |
| `Authors`, `Title`, `PMID` eran los nodos más conectados | Filtro posicional de etiquetas de campo (`Campo:`) |
| Entidades huérfanas sin ninguna arista | Co-ocurrencia como complemento, no sólo respaldo |

---

## 7. Formato de salida

GraphML (`MultiDiGraph`), legible por NetworkX, Gephi, Cytoscape y Neo4j.

**Nodos**

| Atributo | Descripción |
|---|---|
| `tipo` | `PAIS`, `ORG`, `AGENCIA`, `EMPRESA`, `REGION`, `NORMA`, `TEMA`, `SIGLA`, `NOMBRE` |
| `frecuencia` | Menciones totales en el corpus |
| `n_chunks` | Fragmentos distintos donde aparece |
| `n_docs` | Documentos distintos donde aparece |
| `fenomenos` | Fenómenos (1/2/3) ordenados por frecuencia |
| `chunk_ids` | Muestra de trazas hacia la base vectorial |

**Aristas**

| Atributo | Descripción |
|---|---|
| `relacion` | Relación canónica (26 tipos + `relacionado_con`) |
| `peso` | Fragmentos que respaldan la relación |
| `fenomenos` | Fenómenos donde se observa |
| `doc_ids`, `chunk_ids` | Trazas hacia la base vectorial |

```python
import networkx as nx
G = nx.read_graphml("base_vectorial/grafo/grafo.graphml")

# Vecindario de una entidad
list(G.neighbors("órbita baja terrestre"))

# Subgrafo del fenómeno 2
f2 = G.edge_subgraph(
    (u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
    if d["fenomenos"].startswith("2")
)

# De una relación a los fragmentos que la sustentan
G["China"]["satélite"]["lanza"]["chunk_ids"]
```
