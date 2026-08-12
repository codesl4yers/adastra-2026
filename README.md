# Pipeline RAG — CODEFEST Ad Astra 2026, Etapa 1

Convierte el corpus documental de ADL —1826 archivos en siete formatos— en un
índice vectorial consultable, y responde las 50 preguntas del reto con los tres
documentos más relevantes para cada una.

Son tres capas encadenadas, cada una con su CLI y su artefacto en disco:
**extracción** (cada archivo → un `Documento` normalizado), **fragmentación**
(cada documento → fragmentos de ≤250 palabras que nunca parten una oración) e
**indexación y recuperación** (fragmentos → `index.faiss` → `resultados.jsonl`).
No hay reranking ni modelos generativos en ninguna parte: §4.2 del enunciado
prohíbe las arquitecturas decoder en indexación y recuperación.

## Ficha técnica

| | |
|---|---|
| **Encoder** | `ibm-granite/granite-embedding-311m-multilingual-r2` |
| | ModernBERT, encoder-only, 768 dims, ventana 32.768, pooling CLS, Apache 2.0 |
| **Índice** | FAISS `IndexFlatIP`, vectores normalizados (producto interno = coseno) |
| **Corpus** | 1826 documentos · 592.008 bloques · 119,4 M caracteres |
| **Fragmentos** | 134.317 · mediana 140 palabras (p95 232) · p95 442 tokens · **0 truncados** |
| **Vectores** | 134.317 × 768 · `index.faiss` 412 MB · `metadata.jsonl` 200 MB |
| **Entregable** | `resultados.jsonl`: 50 consultas × top-3 documentos, scores 0,870–0,946 |
| **Cobertura** | 1818 de 1826 documentos con vectores (los 8 huecos son conocidos y legítimos) |
| **Métricas del reto** | **F1@3** sobre documentos (§8.6) y **NDCG@10** |
| **Ground truth interno** | 50 consultas etiquetadas a mano × 5 fragmentos (`ground/`) |
| **Pruebas** | 685, `python -m pytest` |

Las métricas del reto todavía **no están medidas** contra el ground truth: es lo
primero de la lista de [Pendiente](#pendiente).

## Instalación

Requiere Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
```

Para el OCR hace falta además el binario de **Tesseract**, que no se instala con
`pip` (`pytesseract` es solo el envoltorio). Con los idiomas `spa+eng+por`: en
Windows, el [instalador de UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki);
en Linux, `apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-por`. Sin
Tesseract el pipeline no se detiene: los documentos que lo necesitan salen con
`bloques=[]` y el motivo en `errores`, y se recuperan después con
`--reintentar-errores`.

## El pipeline completo

```bash
# 1. extraer            base_documental/ -> extraidos/
python orquestador.py --entrada base_documental --salida extraidos \
    --indice base_documental/Indice_Datos_Codefest.xlsx --procesos 6 --limpiar

# 2. fragmentar         extraidos/ -> fragmentos/
python fragmentador.py --entrada extraidos --salida chunks --tokenizador real

# 3. indexar y responder
python generador.py --entrada fragmentos --salida indice
python generador.py --indice indice \
    --consultas base_documental/Extracto_Preguntas_50_v2.pdf \
    --resultados entrega/resultados.jsonl
```

Tiempos con el corpus completo: ~30 min de extracción con 6 procesos (3 h en
secuencial), 10-15 min de fragmentación y ~1,5 h de codificación en una RTX 4050.

### 1. Extraer — `orquestador.py`

Produce `extraidos/{doc_id}.json` (un `Documento` por archivo) y
`extraidos/manifiesto.jsonl` (una línea por documento). El manifiesto es la
herramienta de regresión: si el `diff` entre dos corridas sale vacío, ningún
documento cambió de tamaño, idioma ni número de bloques.

| Flag | Efecto |
|---|---|
| `--entrada` | Directorio del corpus. Se recorre recursivamente. |
| `--salida` | Directorio de resultados. Se crea si no existe. |
| `--indice` | Ruta al `Indice_Datos_Codefest.xlsx` de ADL. Con él, `doc_id`, `fenomeno` y `observatorio` salen del índice y solo se procesa lo que el índice lista. |
| `--fenomeno` | Fenómeno por defecto (1, 2 o 3) cuando ni el índice ni la carpeta lo determinan. |
| `--limpiar` | Borra los resultados de la corrida anterior antes de escribir. |
| `--procesos` | Procesos de extracción en paralelo. El resultado es idéntico byte a byte al secuencial; solo cambia el tiempo. |
| `--reciclar-cada` | Documentos que procesa un worker antes de reciclar el pool (25). |
| `--reintentar-errores` | Reextrae solo los `doc_id` con error en el manifiesto; el resto no se toca. Requiere una corrida completa previa; incompatible con `--limpiar`. |

**Sin `--procesos` se calcula un valor según la RAM libre**, con los núcleos como
tope, y se imprime al arrancar: `pdfminer` no devuelve toda la memoria que
reserva por PDF, así que se reserva margen (600 MB por proceso) y se recicla el
pool cada 25 documentos por worker. Con poca RAM libre, fíjalo a mano y no por
encima de 6.

**`--reintentar-errores` evita repetir la corrida completa** cuando la causa de
unos pocos errores ya se corrigió; el caso típico es que Tesseract no estuviera
instalado.

### 2. Fragmentar — `fragmentador.py`

Produce `chunks/chunks.jsonl` y `fragmentos/reporte_fragmentacion.json`
(histograma, mediana, p95, atómicos, huérfanos fusionados, indivisibles).

| Flag | Efecto |
|---|---|
| `--entrada` / `--salida` | Directorio `extraidos/` y directorio de fragmentos. |
| `--tokenizador` | `real` usa el tokenizador del encoder y **es lo que exige la entrega**; `estimado` (por defecto) usa `ceil(palabras × 1,6)` y no necesita `transformers`. |
| `--objetivo-palabras` | Tamaño al que apunta el empaquetado (190). |
| `--max-palabras` / `--max-tokens` | Topes duros simultáneos (240 / 450). |
| `--min-palabras` | Por debajo, el fragmento se fusiona con un vecino (40). |
| `--oraciones-solape` | Oraciones repetidas del fragmento anterior (1; 0 lo desactiva). |
| `--nivel-frontera` | Los encabezados de nivel ≤ N abren sección nueva (6). |
| `--sin-atomicos` | Empaqueta las filas de datasets como prosa. Solo para el barrido. |

Toda la parametrización vive en `ConfigFragmentacion`, sin constantes sueltas en
el algoritmo, para que el informe pueda citar la configuración exacta y el
barrido pueda variarla sin editar código:

```bash
python scripts/barrido_fragmentacion.py --entrada extraidos --salida docs/barrido.md
```

### 3. Indexar y responder — `generador.py`

Con `--entrada`/`--salida` construye el índice; con `--indice`/`--consultas` lo
carga de disco y responde. Con las dos cosas, hace ambas en una corrida. Si algún
fragmento se trunca, sale con código 1 y no escribe resultados: un índice con
fragmentos a medias no vale para la entrega.

| Flag | Efecto |
|---|---|
| `--entrada` / `--salida` | Fragmentos de entrada y directorio del índice a construir. |
| `--indice` | Directorio de un índice ya construido, para responder sin reconstruirlo. |
| `--consultas` | El PDF de ADL tal cual, un `.jsonl`, o un texto con una consulta por línea. |
| `--resultados` | Ruta del entregable. Por defecto, junto al índice. |
| `--k` | Fragmentos que se piden a FAISS antes de agregar a documento (50). |
| `--top` | Documentos por consulta (3). |
| `--idioma` | Post-filtro por idioma (§8.7). Apagado por defecto. |
| `--lote` | Textos por lote del encoder (4). |
| `--presupuesto-atencion` | Tope de `lote × longitud²` por pasada. Bájalo si la GPU se queda sin memoria; el valor por defecto está calculado para 6 GB. |
| `--dimension` | Dimensión de salida; truncar activa Matryoshka. |
| `--desarrollo` | Usa el modelo de 97M. Para iterar, no para la entrega. |

**Con poca VRAM, lote pequeño es más rápido, no más lento** (26 frag/s con lote 2
frente a 9,6 con lote 32 en una RTX 4050): lo que se paga es el padding al texto
más largo del lote. En una GPU con más memoria el óptimo será mayor — súbelo y
**mide**. Si un lote no cabe ni así, se codifica en CPU con un aviso en stderr.

## Verificar

```bash
python -m pytest                                    # 685 pruebas

python scripts/verificar_cobertura.py \             # ningún documento sin vectores
    --indice base_documental/Indice_Datos_Codefest.xlsx \
    --metadata indice/metadata.jsonl

python scripts/verificar_corpus.py \                # checklist de aceptación
    --corpus base_documental
```

`verificar_cobertura.py` es la comprobación de piso: un documento sin un solo
vector no puede aparecer en el top-3 de ninguna consulta, y nada más en el
pipeline avisa. Devuelve 1 si hay huecos.

Las cinco pruebas que exige el enunciado:

| Requisito | Dónde |
|---|---|
| 1. `validar_documento` limpio para toda salida de un extractor | `test_toda_salida_cumple_el_contrato` / `test_la_salida_cumple_el_contrato` en cada `tests/test_extractor_*.py` |
| 2. Archivo malformado → `bloques=[]` y `errores` no vacía | `test_un_*_ilegible_no_lanza` en cada extractor, y `test_un_archivo_corrupto_no_frena_a_los_demas` |
| 3. Dos corridas → bytes idénticos | `test_dos_corridas_producen_bytes_identicos`, también con `--procesos` |
| 4. `fuente` = nombre exacto del archivo | `test_todos_los_documentos_llevan_ruta_relativa`, `test_detecta_fuente_vacia` |
| 5. Breadcrumb correcto a tres niveles | `test_la_jerarquia_produce_un_documento_que_valida`, `test_las_secciones_abren_subsecciones` |

El fixture binario (`indice_minimo.xlsx`) se regenera con
`python fixtures/generar_binarios.py`; no se edita a mano.

## Preparar la entrega

```bash
python scripts/preparar_entrega.py --indice indice \
    --resultados entrega/resultados.jsonl --destino entrega
```

Deja `entrega/` con `resultados.jsonl`, `generador.py` **y el cierre transitivo
de sus imports** —calculado del AST, no de una lista a mano—, y
`base_vectorial/encoder_<modelo>/` con el índice y la metadata.

Dos cosas que conviene saber: los `.py` de `entrega/` son **copias**, así que se
edita el original de la raíz y se vuelve a ensamblar; y `base_vectorial/` no se
versiona (585 MB, por encima del límite de GitHub), se reconstruye con el mismo
comando. `informe_tecnico.pdf` no lo genera el pipeline: el script avisa si falta.

## Estructura

```
contrato.py          Bloque, Documento, calcular_doc_id, validar_documento
indice.py            lectura del índice maestro de ADL (solo lee)
limpieza.py          normalización, idioma, detección de repetidos
orquestador.py       recorrido, persistencia y CLI de extracción
segmentador.py       fronteras de oración por idioma (pysbd + re-fusión)
fragmentador.py      Fragmento, ConfigFragmentacion, cascada de tres capas y CLI
encoder.py           configuración del encoder, carga del modelo, conteo de tokens
generador.py         índice FAISS, recuperación y entregable
extractores/         pdf, json_, tabular, imagen, pbf, texto + comun y ocr
scripts/             verificación, barrido y ensamblado de la entrega
fixtures/            corpus sintético            tests/     pytest
base_documental/     corpus real de ADL (solo lectura)
ground/              ground truth interno: 50 consultas + metodología
docs/                bitácora del proyecto
```

## Dónde está el porqué

El README es el manual; las decisiones y sus mediciones viven en
`docs/decisiones/`, una por tema:

| Documento | De qué responde |
|---|---|
| `orquestacion-y-determinismo.md` | el contrato de datos y sus cinco reglas, identidad (`doc_id`/`fuente`/colisiones), qué detiene la corrida, paralelismo y memoria |
| `extraccion-por-formato.md` | qué se extrae y qué se tira en cada formato, los filtros de ruido, el OCR, y cómo añadir un extractor nuevo |
| `segmentacion-de-oraciones.md` | el componente crítico: pysbd, el portugués que no trae, la capa de re-fusión y el conjunto dorado |
| `fragmentos-fuera-de-norma.md` | títulos huérfanos, pseudo-oraciones de 8995 palabras y el OCR por página de los PDF ilegibles |
| `conteo-de-tokens.md` | por qué la estimación de 1,6 tokens/palabra no servía y qué salió de re-fragmentar |
| `campos-indexables-tabulares.md` | qué columnas de un dataset entran al vector y cuáles viajan como metadata |
| `enriquecimiento-de-contexto.md` | qué se codifica exactamente y por qué es legal bajo §4.2 |
| `recuperacion-y-entregable.md` | memoria de GPU, orden índice↔metadata, top-k, score por documento, deduplicación, el ground truth interno |

`docs/specs/` guarda los specs de partida —fragmentador y selección de encoder—:
son documentos fechados y no se reescriben, así que donde uno choque con una
decisión posterior, manda la decisión.

## Pendiente

- **Medir F1@3 y NDCG@10** contra `ground/ground_truth.json`. Bloquea las dos
  decisiones que siguen abiertas: la ablación del enriquecimiento de contexto y
  la configuración de fragmentación del barrido.
- **Confirmar los nombres de campo de la Tabla 2** contra el enunciado. Si ADL
  los fija de otro modo, el único sitio que cambia es `registro_de_resultado()`.
- **Relanzar `scripts/verificar_corpus.py`** de punta a punta desde que cambió la
  paralelización. Sus criterios se comprobaron a mano sobre la salida real —0
  violaciones de contrato en los 1826 documentos—, pero el script no.
- **Escribir `entrega/informe_tecnico.pdf`.**
