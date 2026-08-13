# Pipeline RAG — CODEFEST Ad Astra 2026, Etapa 1

Convierte el corpus documental de ADL en un índice vectorial consultable, y responde las 50 preguntas del reto con los **k** (3) documentos más relevantes para cada una.

Son tres capas encadenadas, cada una con su CLI y su artefacto:
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
| **Entregable** | `resultados.jsonl`: 50 consultas × top-3 documentos + top-10 fragmentos |
| **Cobertura** | 1818 de 1826 documentos con vectores (los 8 huecos son conocidos y legítimos) |
| **Métricas del reto** | **F1@3** = 0,169 de 0,799 alcanzable · **NDCG@10** = 0,123 |
| **Ground truth interno** | 50 consultas etiquetadas a mano × 5 fragmentos (`auxiliar/ground/`) |
| **Pruebas** | 724, `python -m pytest` |

Las dos métricas están medidas contra el ground truth interno del equipo. **El techo de F1@3 sobre ese conjunto es 0,7989**, no 1,0:
casi todas sus consultas marcan 4 o 5 documentos relevantes y el entregable solo
admite 3. El número crudo se lee contra ese techo, y aun así es bajo — el
análisis de dónde se pierde está en
[`recuperacion-y-entregable.md` §10.4](docs/decisiones/recuperacion-y-entregable.md).

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

## Pipeline completo

```bash
# 1. extraer            base_documental/ -> extraidos/
python auxiliar/orquestador.py --entrada base_documental --salida extraidos \
    --indice base_documental/Indice_Datos_Codefest.xlsx --procesos 6 --limpiar

# 2. fragmentar         extraidos/ -> chunks/
python fragmentador.py --entrada extraidos --salida chunks --tokenizador real

# 3. indexar 
python generador.py --entrada chunks --salida base_vectorial/encoder_granite-embedding-311m-multilingual-r2

# 4. responder
python generador.py --indice base_vectorial/encoder_granite-embedding-311m-multilingual-r2 \
    --consultas base_documental/Extracto_Preguntas_50_v2.pdf \
    --resultados resultados.jsonl
```


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

Produce `chunks/chunks.jsonl` y `chunks/reporte_fragmentacion.json`
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
python auxiliar/scripts/barrido_fragmentacion.py --entrada extraidos --salida docs/barrido.md
```

### 3. Indexar y responder — `generador.py`

Con `--entrada`/`--salida` construye el índice; con `--indice`/`--consultas` lo
carga de disco y responde. Con las dos cosas, hace ambas en una corrida. Si algún
fragmento se trunca, sale con código 1 y no escribe resultados.

| Flag | Efecto |
|---|---|
| `--entrada` / `--salida` | Fragmentos de entrada y directorio del índice a construir. |
| `--indice` | Directorio de un índice ya construido, para responder sin reconstruirlo. |
| `--consultas` | El PDF de ADL tal cual, un `.jsonl`, o un texto con una consulta por línea. |
| `--resultados` | Ruta del entregable. Por defecto, junto al índice. |
| `--k` | Fragmentos que se piden a FAISS antes de agregar a documento (50). |
| `--top` | Documentos por consulta (3). |
| `--top-fragmentos` | Fragmentos por consulta, para NDCG@10 (10). `0` los apaga. |
| `--idioma` | Post-filtro por idioma (§8.7). Apagado por defecto. |
| `--lote` | Textos por lote del encoder (4). |
| `--presupuesto-atencion` | Tope de `lote × longitud²` por pasada. Bájalo si la GPU se queda sin memoria; el valor por defecto está calculado para 6 GB. |
| `--dimension` | Dimensión de salida; truncar activa Matryoshka. |
| `--desarrollo` | Usa el modelo de 97M. Para iterar, no para la entrega. |



## Estructura


```
resultados.jsonl     el entregable: 50 consultas x top-3 documentos
contrato.py          Bloque, Documento, calcular_doc_id, validar_documento
limpieza.py          normalización, idioma, detección de repetidos
segmentador.py       fronteras de oración por idioma (pysbd + re-fusión)
fragmentador.py      Fragmento, ConfigFragmentacion, cascada de tres capas y CLI
encoder.py           configuración del encoder, carga del modelo, conteo de tokens
generador.py         índice FAISS, recuperación y entregable
generador_grafo.py   componente bonus: léxico, NER, RE y grafo (§7)
base_vectorial/
  encoder_<modelo>/  index.faiss, metadata.jsonl, reporte_indice.json
  grafo/             grafo.graphml, grafo_estadisticas.json
chunks/              chunks.jsonl, la entrada del indexador
docs/                bitácora: por qué cada decisión es la que es

auxiliar/            todo lo que construye lo anterior y no se entrega
  orquestador.py     recorrido, persistencia y CLI de extracción
  indice.py          lectura del índice maestro de ADL (solo lee)
  extractores/       pdf, json_, tabular, imagen, pbf, texto + comun y ocr
  scripts/           verificación, evaluación, barrido y ensamblado
  tests/             685 pruebas       fixtures/  corpus sintético
  ground/            ground truth interno: 50 consultas + metodología

base_documental/     corpus real de ADL (solo lectura, fuera de git)
```
