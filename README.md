# Pipeline de extracción, fragmentación e indexación — CODEFEST Ad Astra 2026 (Etapa 1)

Contrato de datos, extractores, fragmentador e índice vectorial del pipeline
RAG. La primera capa convierte cada archivo del corpus en un `Documento`
normalizado; la segunda lo parte en `Fragmento`; la tercera los codifica y
construye el `IndexFlatIP`; la misma tercera etapa responde las consultas contra
ese índice y escribe `resultados.jsonl`. **No** hay reranking: eso vive en capas
posteriores y consume `indice/`.

Estado: todas las capas implementadas —contrato, limpieza, orquestador,
extractores, segmentador, fragmentador, encoder y generador—, con 685 pruebas, y
corrida completa sobre los 1826 archivos del corpus real de ADL: extracción, OCR,
fragmentación y validación de contrato, las tres al 100 %, no sobre una muestra.

El porqué de cada decisión no obvia vive en `docs/decisiones/`; los specs de
partida, en `docs/specs/`. Este README es el manual de uso y el resumen del
estado: cuando una decisión necesita medidas o alternativas descartadas, va al
doc y aquí queda el enlace.

El encoder es `ibm-granite/granite-embedding-311m-multilingual-r2`
(ModernBERT, encoder-only, 768 dims, Apache 2.0), elegido en
`docs/specs/spec-encoder-addendum.md` §16. La arquitectura se verifica contra el
`config.json` del checkpoint antes de cargar los pesos: usar un decoder es
riesgo de descalificación por §4.2 del enunciado.

### Qué se ha verificado contra el corpus

`validar_documento` corrido sobre los 1826 documentos reales, no sobre una
muestra ni sobre fixtures sintéticas:

| Formato | Documentos | Con bloques | Bloques | Caracteres | Violaciones del contrato |
|---|---:|---:|---:|---:|---:|
| `pdf` | 759 | 757 | 522.971 | 90,7 M | 0 |
| `json` | 954 | 953 | 13.412 | 4,7 M | 0 |
| `csv` | 26 | 26 | 38.558 | 17,6 M | 0 |
| `xlsx` | 4 | 4 | 5.039 | 0,8 M | 0 |
| `pbf` | 73 | 73 | 11.979 | 5,5 M | 0 |
| `texto` | 1 | 1 | 1 | 12 K | 0 |
| `imagen` | 9 | 4 | 48 | 1,8 K | 0 |
| **total** | **1826** | **1818** | **592.008** | **119,4 M** | **0** |

Los 8 documentos sin bloques son legítimos, no bugs: 1 JSON de origen vacío
(`el JSON es una lista vacía`), 2 PDF corruptos (`PdfminerException: No /Root
object!`) y 5 imágenes donde Tesseract corrió pero no encontró texto fiable
—son fotografías, no hay texto que reconocer—. Sumando los 7 CSV/XLSX
truncados a 5000 filas por diseño (esos sí tienen bloques, solo que
incompletos), quedan 15 documentos con algo anotado en `errores`; ninguno es
un fallo del pipeline.

Dos cosas que conviene saber antes de indexar: los 26 CSV aportan casi tanto
texto como los 759 PDF —17,6 M de caracteres frente a 90,7 M, aun con el
truncado a 5000 filas por archivo—, así que pesarán desproporcionadamente en
el índice; y entre el 5 y el 11 % de los PDF vienen escaneados.

**No todas las columnas de un dataset entran al vector.** Los siete exports
bibliográficos del corpus (cinco de PubMed, lit-covid en CSV y en XLSX) traen
más de la mitad del texto en identificadores —`PMID`, `PMCID`, `NIHMS ID`,
`DOI`, `Citation`— que nadie recupera por semejanza y que diluyen el título,
que es lo único que sí se recupera. `ESQUEMAS` en `extractores/tabular.py`
declara qué columnas indexa cada export conocido; el resto viaja en
`Bloque.datos` → `Fragmento.datos` → `metadata.jsonl` como campo adicional de
los que permite §3.4. Son 29 palabras por fila en vez de 52. Una cabecera que
no case con ningún esquema se indexa entera. El detalle y las mediciones están
en `docs/decisiones/campos-indexables-tabulares.md`.

**El OCR ya está operativo.** `pytesseract` + Tesseract (`spa+eng+por`)
reconocen los PDF escaneados y las imágenes con texto real. En Windows, si el
binario no aparece por PATH —el instalador no siempre lo agrega, o queda una
entrada de una instalación anterior en otra carpeta—, `extractores/ocr.py`
prueba antes de rendirse las rutas de instalación por defecto
(`C:\Program Files\Tesseract-OCR\tesseract.exe` y su variante `(x86)`).

**La decisión de reconocer es por página, y solo si mejora.** Un PDF puede
tener capa de texto y aun así no poder leerse: si la fuente embebida no trae
tabla `ToUnicode`, pdfplumber devuelve `(cid:NN)` por carácter; si el PDF dibuja
cada letra suelta, salen separadas por espacios. La densidad de caracteres no
los detecta —`(cid:47)` son nueve caracteres por letra— así que
`_texto_ilegible` los busca explícitamente. Se reconoce **solo la página
afectada**, conservando el texto nativo del resto, y solo si el resultado aporta
más texto útil que el original: hay páginas mixtas donde el diagnóstico es
correcto pero el OCR devolvería menos de lo que había. Los detalles y las
mediciones están en `docs/decisiones/fragmentos-fuera-de-norma.md` §7.

No hay extractor de HTML ni entrada en `EXTRACTORES` para `.html`/`.htm`: el
corpus real de ADL no trae archivos de ese formato.

## Instalación

Requiere Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
```

Para OCR (PDF escaneados e imágenes) hace falta además el binario de
**Tesseract**, que no es instalable con `pip`: `pytesseract` solo es el
envoltorio de Python. Instálalo con los idiomas `spa+eng+por` — en Windows,
el [instalador de UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki);
en Linux, `apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-por`. Sin
Tesseract el pipeline no se detiene: los documentos que lo necesitan salen con
`bloques=[]` y el motivo en `errores`, y basta con instalarlo y volver a
extraer (ver `--reintentar-errores` más abajo).

## Correr el pipeline

```bash
python orquestador.py --entrada fixtures --salida extraidos
```

Opciones:

| Flag | Efecto |
|---|---|
| `--entrada` | Directorio del corpus. Se recorre recursivamente. |
| `--salida` | Directorio de resultados. Se crea si no existe. |
| `--fenomeno` | Fenómeno por defecto (1, 2 o 3) cuando ni el índice ni la carpeta lo determinan. |
| `--indice` | Ruta al `Indice_Datos_Codefest.xlsx` de ADL. Opcional. Con él, `doc_id`, `fenomeno` y `observatorio` salen del índice y solo se procesa lo que el índice lista. |
| `--limpiar` | Borra los resultados de la corrida anterior antes de escribir. |
| `--procesos` | Procesos de extracción en paralelo. El resultado es idéntico byte a byte al secuencial; solo cambia el tiempo. Sin este flag se calcula solo según la RAM libre en el momento de arrancar (ver abajo). |
| `--reciclar-cada` | Documentos que procesa un worker antes de reciclar el pool (por defecto 25). |
| `--reintentar-errores` | Reextrae solo los `doc_id` con error en el manifiesto de `--salida`; el resto no se toca. Requiere una corrida completa previa; incompatible con `--limpiar`. |

Sobre el corpus completo, `--procesos` no es un lujo: los 760 PDF suman 2,9 GB
y ~31.000 páginas, y el 99 % de ese tiempo está dentro de `pdfplumber`, no en
código propio que se pueda optimizar. En secuencial son unas 3 horas; con 6
procesos, unos 30 minutos.

```bash
python orquestador.py --entrada base_documental --salida extraidos \
    --indice base_documental/Indice_Datos_Codefest.xlsx --procesos 6 --limpiar
```

**Sin `--procesos`, el pipeline elige un número según la RAM libre**, con el
número de núcleos como tope: reserva 600 MB por proceso (`RAM_MB_POR_PROCESO`
en `orquestador.py`) y lo imprime por stderr al arrancar. Esa cifra sale de medir
un worker real después de extraer los dos atlas más grandes del corpus
(`RESDAL_atlas-2024-esp.pdf`, 250 páginas; `SWF_global-counterspace-capabilities-2026-hr.pdf`,
371 páginas): `pdfminer` no le devuelve al sistema operativo toda la memoria
que reserva por PDF, así que cada worker retiene un poso de varios cientos de
MB por documento grande que procesa, y ese poso solo se libera reciclando el
proceso entero. Eso lo hace `--reciclar-cada` (25 documentos por worker por
defecto): cada `--procesos * --reciclar-cada` documentos se cierra el pool
completo y se abre uno nuevo. Es un reciclaje del pool entero, no de un worker
suelto —`ProcessPoolExecutor(max_tasks_per_child=...)` haría esto último, pero
tiene [un deadlock conocido de
CPython](https://github.com/python/cpython/issues/115634) sin corregir hasta
Python 3.14 que cuelga el pool en cuanto un worker se recicla a mitad de una
tanda—. Aun así, si la máquina tiene poca RAM libre o hay otros procesos
compitiendo por ella, sigue siendo razonable fijar `--procesos` a mano y no
por encima de 6: los tiempos por documento son muy desiguales, así que el
reparto va de uno en uno.

**`--reintentar-errores` evita repetir una corrida completa** cuando la causa
de unos pocos errores ya se corrigió —el caso típico: Tesseract no estaba
disponible en la corrida anterior—. Lee el manifiesto de `--salida`, junta los
`doc_id` con `n_errores > 0`, reextrae solo esos y fusiona el resultado en el
mismo manifiesto; el resto del corpus no se toca ni un byte:

```bash
python orquestador.py --entrada base_documental --salida extraidos \
    --indice base_documental/Indice_Datos_Codefest.xlsx --reintentar-errores
```

El fenómeno de cada documento se resuelve con esta precedencia, de más a menos
fiable:

1. **El índice**, si se pasó `--indice`: el fenómeno declarado por ADL para
   ese archivo.
2. **La carpeta**, si ninguna entrada del índice lo cubre. Prioriza el patrón
   real del corpus de ADL —carpetas raíz como `F1_IA_y_Capacidades_Estrategicas`,
   `F2_Seguridad_Entorno_Espacial`, `F3_Dinamicas_Territoriales`— y cae a la
   convención antigua (`fenomeno_1/`, `fenomeno_2/`, `fenomeno_3/`) como
   respaldo, por si ADL reorganiza el corpus con esa nomenclatura.
3. **`--fenomeno`**, para lo que ni el índice ni ninguna carpeta del camino
   declaran.

### Qué produce

- `extraidos/{doc_id}.json` — un `Documento` por archivo, con claves ordenadas
  e indentación de 2 espacios.
- `extraidos/manifiesto.jsonl` — una línea por documento, ordenada por
  `(fuente, ruta_relativa)`. Se desempata por ruta porque 59 nombres de
  archivo se repiten en 186 archivos del corpus de ADL: ordenar solo por
  `fuente` dejaría el orden relativo de esos homónimos a merced del sistema de
  archivos.

El manifiesto es la herramienta de regresión. Para comprobar que un cambio no
rompió nada:

```bash
python orquestador.py --entrada corpus --salida /tmp/antes
# ...cambias algo...
python orquestador.py --entrada corpus --salida /tmp/despues
diff /tmp/antes/manifiesto.jsonl /tmp/despues/manifiesto.jsonl
```

Si el diff sale vacío, ningún documento cambió de tamaño, idioma ni número de
bloques. Si sale con líneas, las líneas te dicen exactamente qué documentos
revisar.

## Fragmentar

Segunda capa. Consume `extraidos/` y produce fragmentos de ≤250 palabras que
nunca parten una oración por la mitad (§3.3 del enunciado).

```bash
python fragmentador.py --entrada extraidos --salida fragmentos
```

Produce `fragmentos/fragmentos.jsonl` (una línea por fragmento, claves
ordenadas) y `fragmentos/reporte_fragmentacion.json` (histograma de palabras,
mediana, p95, atómicos, huérfanos fusionados y oraciones indivisibles).

Sobre el corpus completo, con el tokenizador real y la limpieza de campos
tabulares ya aplicados: **1826 documentos → 134.317 fragmentos**, mediana de
140 palabras (p95: 232), 41.594 atómicos (filas de CSV/XLSX y features de PBF),
2159 huérfanos fusionados, 506 oraciones indivisibles y 315 registros atómicos
partidos por exceder las 250 palabras. Tarda unos 10-15 minutos, casi todo dentro de
`pysbd` —no hay paralelismo aquí, y no lo necesita salvo por un caso
particular: el atlas de RESDAL trae **8319 bloques** él solo, y `pysbd`
compila una expresión regular nueva por cada oración para ubicar su posición
en el texto (ver `segmentador.py`), así que ese documento por sí solo puede
tardar varios minutos. El CLI avisa antes de entrar en cualquier documento con
1000 bloques o más, y cada 200 documentos en el resto (`UMBRAL_AVISO_BLOQUES`
y `PROGRESO_CADA` en `fragmentador.py`) —sin eso, ese tramo se ve indistinguible
de un proceso colgado.

La estrategia es **híbrida estructural-oracional con unidades atómicas
preservadas**: la estructura del documento fija fronteras que no se cruzan, y
dentro de cada frontera se empaquetan oraciones completas hasta ~190 palabras,
con tope duro de 240 palabras y 450 tokens. Las filas de CSV/XLSX y los
elementos de PBF (`atomico=True`) van por su propio camino: no se parten ni se
mezclan con prosa vecina.

Toda la parametrización vive en `ConfigFragmentacion`; no hay constantes sueltas
en el algoritmo, para que el informe pueda citar la configuración exacta y el
barrido pueda variarla sin editar código:

```bash
python fragmentador.py --entrada extraidos --salida fragmentos \
    --objetivo-palabras 120 --oraciones-solape 0

# tabla comparativa de seis configuraciones para el informe técnico
python scripts/barrido_fragmentacion.py --entrada extraidos --salida docs/barrido.md
```

El conteo de tokens es inyectable (`ConfigFragmentacion.contar_tokens`). El
valor por defecto sigue siendo la estimación `ceil(palabras × 1.6)`, para que el
módulo funcione sin `transformers` instalado; **para la entrega hay que pasarle
el tokenizador real**:

```python
from encoder import config_fragmentacion_con_tokenizador
from fragmentador import fragmentar_corpus

fragmentar_corpus(Path("extraidos"), Path("fragmentos"),
                  config_fragmentacion_con_tokenizador())
```

La estimación **no era conservadora**: medida contra el tokenizador de granite,
la mediana real del corpus es de 1,77 tokens por palabra —3,50 en tiles
vectoriales, 2,81 en datos tabulares, 1,48 en prosa PDF—, así que el 8,2 % de
los fragmentos de la corrida estimada excedía el tope de 450. **El corpus ya se
re-fragmentó con el contador real** (es la corrida de 134.317 fragmentos de
arriba): los que siguen por encima de 450 son 2543, el 1,9 %, y el mayor mide
3691 tokens, muy por debajo de la ventana de 32 768 del modelo. El detalle y los
números están en `docs/decisiones/conteo-de-tokens.md`.

### Índice vectorial

```bash
python generador.py --entrada fragmentos --salida indice
python generador.py --entrada fragmentos --salida indice --desarrollo  # modelo 97M
```

Produce `index.faiss` (`IndexFlatIP`, vectores normalizados explícitamente),
`metadata.jsonl` —una línea por vector, **en el mismo orden que el índice**, con
los ocho campos obligatorios y sin `texto_enriquecido`— y
`reporte_indice.json`, que deja por escrito la evidencia de las dos
comprobaciones que fallan en silencio: fragmentos truncados (debe ser 0) y norma
de los vectores (debe ser 1,0). Si algún fragmento se trunca, el CLI sale con
código 1: un índice con fragmentos a medias no vale para la entrega.

**El índice del corpus completo ya está construido**: 134.317 vectores de 768
dimensiones (412 MB), 0 truncados, normas entre 0,9999999 y 1,0000001, p95 de
442 tokens y máximo de 3707 con el prefijo de contexto incluido. La justificación
de cada decisión de esta capa —qué se codifica, cómo se agrupa el top-k, por qué
el lote es pequeño— está en `docs/decisiones/recuperacion-y-entregable.md`.

Lo que se codifica es `texto_enriquecido` —observatorio, título y breadcrumb de
secciones por delante del texto—, no `texto`. La justificación completa, con
cobertura y coste medidos sobre el corpus, está en
`docs/decisiones/enriquecimiento-de-contexto.md`.

### Responder las consultas

```bash
python generador.py --indice indice \
    --consultas base_documental/Extracto_Preguntas_50_v2.pdf \
    --resultados entrega/resultados.jsonl
```

Es la segunda etapa del mismo módulo: carga el índice de disco —no reconstruye
nada— y escribe el entregable `resultados.jsonl`, una línea por consulta con el
top-3 de **documentos** (§8.6). Con `--entrada`/`--salida` además de
`--consultas`, construye y responde en la misma corrida; si el índice sale con
fragmentos truncados, se para ahí y no escribe resultados.

| Flag | Para qué |
|---|---|
| `--indice` | Directorio del índice ya construido. |
| `--consultas` | El PDF de ADL tal cual, un `.jsonl`, o un texto con una consulta por línea. |
| `--resultados` | Ruta del entregable. Por defecto, `resultados.jsonl` junto al índice. |
| `--k` | Fragmentos que se piden a FAISS antes de agregar a documento (50). |
| `--top` | Documentos por consulta (3). |
| `--idioma` | Post-filtro por idioma (§8.7). Apagado por defecto. |

Tres decisiones que el entregable hereda y conviene tener a mano para el informe:

- **El score del documento es el de su mejor fragmento**, no la suma de los
  suyos. Sumar corona al documento largo por ser largo —más fragmentos, más
  ocasiones de rozar la consulta—, y lo que se evalúa es si el documento
  responde. Los empates se rompen por `doc_id` para que dos corridas del mismo
  índice ordenen igual (§1.4).
- **Se piden 50 fragmentos para entregar 3 documentos.** El top-k viene en
  fragmentos y varios caen en el mismo `doc_id`; pedir 3 puede dar un solo
  documento. Si aun así alguna consulta no llega a tres documentos distintos, el
  CLI la nombra por stderr en vez de entregar un top-3 corto en silencio.
- **El post-filtro por idioma está apagado.** Las consultas vienen en español y
  el grueso del corpus está en inglés: filtrar a `es` no afina la respuesta, la
  vacía. El encoder es multilingüe justamente para no necesitarlo. En la corrida
  contra el corpus completo las 50 consultas en español recuperan documentos en
  inglés con scores de 0,87 a 0,95.

**El descarte de texto repetido es dentro de un mismo documento, nunca entre
documentos.** La distinción decide un acierto: el corpus trae lit-covid dos
veces, `F1-AIINDEX-041` en CSV y `F1-AIINDEX-042` en XLSX, con texto y vectores
idénticos. Deduplicar solo por texto parece más limpio y le quita a uno de los
dos el único vector con el que podía llegar al top-3 —el jurado empareja por
`fuente` (§10.2.1), así que son dos documentos distintos y probablemente los
dos estén en el ground truth de una consulta sobre lit-covid—. Dentro de un
mismo documento sí es ruido: §8.6 lo puntúa con su mejor fragmento, así que el
repetido no cambia su score y solo ocupa un puesto del top-k. En la corrida de
50 consultas se descartaron 17; deduplicando también entre documentos habrían
sido 113, y esos 96 de diferencia son oportunidades de acierto tiradas.

#### Memoria de la GPU

ModernBERT materializa una máscara de atención de `(lote, 1, L, L)` en float32
—dos, en realidad: global y sliding window— donde `L` es el fragmento **más
largo del lote**. El coste no lo fija el número de textos sino el cuadrado del
más largo multiplicado por el lote entero: un lote de 32 con un fragmento de
8 200 tokens reserva 8,67 GB de una sentada. En la corrida fragmentada con la
estimación de tokens eso pasaba de verdad —57 fragmentos por encima de 8 192 y
un máximo de 17 803—. Con el contador real el caso extremo desapareció: el
máximo del corpus vigente es de 3707 tokens. Los dos mecanismos de abajo se
mantienen porque el coste sigue siendo cuadrático y una configuración de
fragmentación distinta puede devolver la cola larga.

Dos mecanismos lo contienen:

- **Lotes por presupuesto** (`--presupuesto-atencion`, 128 M por defecto): el
  lote se encoge cuando aparece un fragmento largo y el más grande viaja solo.
- **Respaldo en CPU**: si aun así un lote no cabe, se codifica en CPU con un
  aviso en lugar de perder la corrida. Los vectores de CPU y GPU difieren en el
  último bit, así que si el respaldo se dispara el índice deja de ser
  reproducible bit a bit; el aviso queda en stderr para que conste.

**Con esta GPU, lote pequeño es más rápido, no más lento.** Medido sobre una
muestra sistemática del corpus en una RTX 4050 de 6 GB:

| `--lote` | fragmentos/s | corpus completo | VRAM pico |
|---:|---:|---:|---:|
| 2 | 26,2 | 93 min | 2,68 GB |
| 4 | 23,6 | 104 min | 2,82 GB |
| 8 | 19,2 | 128 min | 3,10 GB |
| 16 | 14,7 | 167 min | 3,66 GB |
| 32 | 9,6 | 255 min | 4,78 GB |

El padding al texto más largo del lote es lo que se paga, y con poca VRAM el
cuello es la memoria y no el cómputo. En una GPU con más memoria el óptimo será
mayor: súbelo con `--lote` y **mide**, no lo supongas.

Si Windows empieza a volcar a memoria compartida —VRAM llena y GPU al 0 % de
uso— conviene poner el panel de NVIDIA en *CUDA - Sysmem Fallback Policy →
Prefer No Sysmem Fallback*: así falla rápido en vez de arrastrarse durante horas.

### Segmentación de oraciones

Es el componente crítico: si la frontera oracional falla, todos los fragmentos
afectados violan un requisito obligatorio. Vive en `segmentador.py` y se prueba
contra `fixtures/oraciones_doradas.jsonl`, 65 casos etiquetados a mano —21 o más
por idioma— con abreviaturas, decimales, siglas, citas, listas, comillas y
elipsis. Un caso que el segmentador falle no se borra: se documenta con su
motivo en el campo `excepcion` del JSONL y sale como `xfail`. Hoy no hay
ninguno.

El motor es [pysbd](https://github.com/nipunsadvilkar/pySBD) (MIT, por reglas,
sin descarga de modelos). Dos avisos:

- **pysbd 0.3.4 no trae módulo de portugués.** Se usa el español —la lengua más
  cercana de las disponibles— con una lista de abreviaturas propia del
  portugués, que es lo que de verdad cambia el resultado.
- pysbd por sí solo parte `La Dra. | Gómez` y `EE.UU. | y la U.S. | Space
  Force`. Encima va una capa de re-fusión de cortes falsos, deliberadamente
  agresiva: partir una oración viola §3.3, fusionar dos de más solo engorda un
  fragmento.

### Verificación de piso: ningún documento sin vectores

```bash
python scripts/verificar_cobertura.py \
    --indice base_documental/Indice_Datos_Codefest.xlsx \
    --metadata indice/metadata.jsonl
```

Es la única comprobación que detecta la forma garantizada de perder F1@3: un
documento sin un solo vector no puede aparecer en el top-3 de ninguna consulta.
No es que recupere mal —es que es imposible que recupere—, y nada más en el
pipeline avisa: un extractor que falla en silencio produce un `Documento`
válido con cero bloques, el fragmentador produce cero fragmentos y el generador
no echa de menos lo que nunca llegó. Devuelve 1 si hay huecos.

Empareja por `doc_id` y no por `fuente` porque 59 nombres de archivo se repiten
en el corpus: con el nombre, un documento cubierto taparía el hueco de otro que
comparte nombre.

Sobre el índice actual salen **8 huecos, los 8 conocidos**: el JSON de origen
vacío, las 5 imágenes sin texto reconocible y los 2 PDF que en realidad son
páginas HTML mal descargadas.

### Duplicados exactos

El corpus trae lit-covid dos veces —`F1-AIINDEX-041` en CSV y `F1-AIINDEX-042`
en XLSX, las mismas 8 866 filas—. El generador **codifica ese texto una sola
vez y lo inserta las dos**: cada `fuente` conserva su fila en el índice, porque
omitir una garantiza perder ese acierto, pero el pase del encoder no se repite.
`reporte_indice.json` lo deja anotado en `n_reutilizados`: en la corrida vigente
son 12 132 vectores, el 9 % del índice, que no se volvieron a calcular.

## Correr las pruebas

```bash
python -m pytest
```

685 pruebas. Las cinco que exige el enunciado:

| Requisito | Dónde |
|---|---|
| 1. `validar_documento` limpio para toda salida de un extractor | `test_toda_salida_cumple_el_contrato` en cada `tests/test_extractor_*.py`, y sobre el corpus real en `scripts/verificar_corpus.py` |
| 2. Archivo malformado → `bloques=[]` y `errores` no vacía | `test_un_*_ilegible_no_lanza` en cada `tests/test_extractor_*.py`, y `tests/test_orquestador.py::test_un_archivo_corrupto_no_frena_a_los_demas` |
| 3. Dos corridas → bytes idénticos | `tests/test_orquestador.py::test_dos_corridas_producen_bytes_identicos`, también con `--procesos` |
| 4. `fuente` = nombre exacto del archivo | `tests/test_orquestador.py::test_todos_los_documentos_llevan_ruta_relativa` y `tests/test_contrato.py::test_detecta_fuente_vacia` |
| 5. Breadcrumb correcto a tres niveles | `tests/test_extractores_comun.py::test_la_jerarquia_produce_un_documento_que_valida` y `tests/test_extractor_json.py::test_las_secciones_abren_subsecciones`; sobre el corpus real lo comprueba `verificar_corpus.py` |

El fixture binario (`indice_minimo.xlsx`) no se edita a mano; se regenera con
`python fixtures/generar_binarios.py`.

## Estructura

```
contrato.py          Bloque, Documento, calcular_doc_id, validar_documento
indice.py            lectura del índice maestro de ADL (solo lee)
limpieza.py          normalización, idioma, detección de repetidos
orquestador.py       recorrido, persistencia y CLI
segmentador.py       fronteras de oración por idioma (pysbd + re-fusión)
fragmentador.py      Fragmento, ConfigFragmentacion, cascada de tres capas y CLI
extractores/
    comun.py         pila de encabezados, filtro de lenguaje natural, construcción del Documento
    ocr.py           Tesseract, compartido por imagen y PDF escaneado
    pdf.py           760 archivos: pdfplumber, jerarquía por tamaño de fuente, dos columnas
    json_.py         964 archivos: artículos scrapeados, catálogos y GeoJSON
    tabular.py       32 archivos: csv + xlsx, una fila por bloque atómico
    imagen.py        9 archivos: metadata EXIF + OCR
    pbf.py           73 archivos: tiles vectoriales, properties como registros
    texto.py         1 archivo: texto plano y Markdown
fixtures/            corpus sintético
base_documental/     corpus real de ADL (solo lectura, no se toca un byte)
ground/              ground truth interno: 50 consultas etiquetadas + metodología
docs/decisiones/     por qué de cada decisión no obvia, con sus mediciones
docs/specs/          specs de partida (fechados; el estado vigente es este README)
scripts/             herramientas fuera del pipeline (verificación y barrido)
tests/               pytest
```

Las cuentas de archivos por extractor son **archivos en disco**, no entradas del
índice: los 760 PDF incluyen el enunciado y los 964 JSON los 10 catálogos de
scraping, que el índice no lista y el pipeline no procesa. Por eso la tabla de
verificación de más arriba dice 759 y 954.

### La carpeta de entrega

```
entrega/
├── resultados.jsonl
├── generador.py
├── informe_tecnico.pdf
├── base_vectorial/
│   └── encoder_granite-embedding-311m-multilingual-r2/
│       ├── index.faiss
│       └── metadata.jsonl
└── grafo/                  (bonus)
    └── grafo.graphml
```

Se ensambla con un comando, no a mano:

```bash
python scripts/preparar_entrega.py --indice indice \
    --resultados entrega/resultados.jsonl --destino entrega
```

**`generador.py` no viaja solo.** La estructura solo lo nombra a él, pero
importa `contrato`, `encoder`, `fragmentador`, `limpieza` y `segmentador`:
entregarlo suelto es entregar un `ImportError`. El script copia el cierre
transitivo de sus imports —calculado del AST, no de una lista escrita a mano
que se quedaría vieja al primer import nuevo— al mismo directorio, porque
Python añade la carpeta del script al `sys.path` y así `python
entrega/generador.py` corre sin tocar el entorno. Verificado ejecutándolo desde
dentro de `entrega/`: 50 consultas, 50 líneas.

**Los `.py` de `entrega/` son copias: se edita el original de la raíz y se
vuelve a ensamblar.** Van versionados para que el entregable sea auditable tal
como se envía, pero editarlos ahí es perder el cambio en el siguiente
`preparar_entrega.py`. Si `git status` muestra uno de ellos modificado sin que
lo esté su original, alguien tocó la copia.

Lo único que no se versiona es `base_vectorial/`: son 585 MB —412 el índice y
200 la metadata— y GitHub rechaza cualquier archivo de más de 100 MB, así que
un `git add .` dejaría el historial ya escrito y el push roto. Se reconstruye
con el mismo comando.

`informe_tecnico.pdf` no lo genera el pipeline; el script avisa si falta.

Dependencias: `contrato` → `limpieza`. Los extractores dependen de ambos y de
`extractores.comun`. `limpieza` no depende de nada del proyecto, así que se
puede usar suelto.

### Qué extrae cada formato

| Formato | Unidad de bloque | Jerarquía | Notas |
|---|---|---|---|
| `pdf` | párrafo | por tamaño de fuente | detecta dos columnas por el corredor vertical; cabeceras y pies repetidos se descartan |
| `json` | párrafo | `title` → `sections[].heading` | reconoce el esquema de artículo del scraper; cae a recorrido genérico si no lo encuentra |
| `csv` / `xlsx` | fila **atómica** | hoja (xlsx) | `columna: valor` en cada fila, para que se recupere sin la cabecera |
| `pbf` | feature **atómica** | capa | solo `properties`; la geometría nunca entra al índice |
| `texto` | párrafo | encabezados Markdown | párrafos por línea en blanco, no por salto de línea |
| `imagen` | línea de OCR | — | metadata EXIF siempre; texto solo si hay Tesseract |

## El contrato

```python
@dataclass(frozen=True)
class Bloque:
    texto: str          # limpio, sin marcado, NFC, sin espacios redundantes
    tipo: str           # "titulo" | "parrafo" | "lista" | "fila" | "ocr"
    nivel: int | None   # 1..6 si y solo si tipo == "titulo"
    ruta: list[str]     # breadcrumb de encabezados ancestros vigentes
    pagina: int | None  # 1-based si el formato tiene páginas
    atomico: bool       # True => unidad indivisible (fila de CSV, feature)

@dataclass(frozen=True)
class Documento:
    doc_id: str         # del índice de ADL, o derivado de la ruta relativa
    fuente: str         # nombre EXACTO del archivo original
    formato: str        # "pdf"|"json"|"csv"|"xlsx"|"imagen"|"pbf"|"texto"
    fenomeno: int       # 1, 2 o 3
    idioma: str         # "es" | "en" | "pt"
    bloques: list[Bloque]
    meta: dict
    errores: list[str]
```

`validar_documento(doc) -> list[str]` devuelve la lista de invariantes
violados, vacía si está correcto. **Es para los tests, no para producción**: un
documento inválido debe ser imposible de construir, no algo que se detecte al
final del pipeline.

### Reglas que no se negocian

1. **Nada de modelos generativos.** Ni para limpiar, ni para resumir, ni para
   clasificar. Solo reglas, parsers y modelos encoder.
2. **`fuente` es inmutable.** Es el nombre exacto del archivo entregado, con su
   extensión, sin renombrar ni normalizar. La evaluación empareja por este
   campo. `doc_id` es interno y derivado; `fuente` es el contrato con el jurado.
3. **Determinismo total.** Nada de iterar `set()` sin ordenar, nada de depender
   del orden de `rglob`, nada de `hash()` nativo (depende de `PYTHONHASHSEED`).
   `calcular_doc_id` usa blake2b por eso.
4. **Ningún extractor tumba el pipeline.** Un archivo corrupto produce un
   `Documento` válido con `bloques=[]` y el motivo en `errores`.
5. **Lo único que detiene la corrida es una identidad inconsistente,
   detectada antes de escribir nada.** Dos casos: un `doc_id` duplicado entre
   dos documentos —el JSON de uno sobrescribiría al del otro y el manifiesto
   tendría dos líneas apuntando al mismo archivo—, o un índice de ADL
   malformado —`DOC_ID` o ruta repetidos, fenómeno fuera de rango, columnas
   ausentes, o un `DOC_ID` que no sirve como nombre de archivo—. Un nombre de
   archivo repetido, en cambio, ya no detiene nada: se desambigua por ruta y
   se marca con `meta["fuente_ambigua"]`.

## Cómo añadir un extractor nuevo

Los seis formatos del corpus ya están implementados; esto vale para uno nuevo.
La estrategia y la trampa de cada formato están en
`docs/decisiones/extraccion-por-formato.md`: léelo antes de tocar nada parecido.

**0. Usa `extractores/comun.py`.** `Jerarquia` lleva la pila de encabezados que
`validar_documento` va a reconstruir y comparar; llevarla por tu cuenta produce
documentos que no validan por un detalle de bookkeeping. `es_texto_natural`,
`serializar_registro` y `construir_documento` cubren el resto.

**1. Escribe primero las fixtures y las pruebas.** Añade a `fixtures/` al menos
un archivo bien formado, uno con boilerplate y uno corrupto.

**2. Implementa la firma exacta.** Sin excepciones, sin estado global, sin
escribir a disco:

```python
def extraer(path: Path, fenomeno: int) -> Documento:
```

**3. Envuelve todo en el blindaje.** El patrón:

```python
def extraer(path: Path, fenomeno: int) -> Documento:
    fuente = path.name          # nunca path.stem, nunca una ruta normalizada
    errores, meta, bloques = [], {}, []
    try:
        ...
        bloques = _extraer_bloques(...)   # se asigna al final, de una vez
    except ExtraccionFallida as exc:
        errores.append(str(exc))
    except Exception as exc:
        errores.append(f"error inesperado ({type(exc).__name__}): {exc}")
    ...
```

`bloques` se asigna de una sola vez al final del `try`. Si algo falla a mitad,
queda `[]` en lugar de una lista a medio llenar, que es peor que nada porque
parece válida.

**4. Usa `limpieza`, no reinventes.** `normalizar_texto` en todo texto antes de
construir el `Bloque`. `es_ruido_estructural` para numeración de páginas.
`lineas_repetidas` para cabeceras y pies, pasándole como "unidades" lo que en
tu formato se repite: páginas en PDF, hojas en XLSX.

**5. Mantén el breadcrumb.** Una pila de `(nivel, texto)`; un título de nivel N
cierra los de nivel >= N. **Construye una lista nueva para cada `ruta`**: si
compartes la misma lista entre bloques, todos acaban viendo el último
breadcrumb.

**6. Ordena todo lo que recorras.** Claves de diccionario, resultados de
`glob`, features de una capa. Si el orden lo decide una librería, ordénalo tú.

**7. Regístralo** en `EXTRACTORES` de `orquestador.py`, mapeando extensión a
`(módulo, formato)`.

**8. Comprueba el contrato.** `validar_documento` debe salir vacía para todas
tus fixtures, y dos corridas deben dar bytes idénticos.

## Decisiones de diseño

La bitácora completa está en `docs/decisiones/`, una por tema, con las medidas
sobre las que se tomó cada una y las alternativas que se descartaron:

| Documento | De qué responde |
|---|---|
| `orquestacion-y-determinismo.md` | identidad (`doc_id`/`fuente`/colisiones), qué detiene la corrida, el índice como filtro, paralelismo y memoria, el manifiesto |
| `extraccion-por-formato.md` | qué se extrae y qué se tira en pdf, json, csv/xlsx, pbf, imagen y texto; los dos filtros de ruido; OCR |
| `fragmentos-fuera-de-norma.md` | títulos huérfanos, pseudo-oraciones de 8995 palabras, y el OCR por página de los PDF ilegibles |
| `conteo-de-tokens.md` | por qué la estimación de 1,6 tokens/palabra no servía y qué salió de re-fragmentar con el tokenizador real |
| `campos-indexables-tabulares.md` | qué columnas de un dataset entran al vector y cuáles viajan como metadata |
| `enriquecimiento-de-contexto.md` | qué se codifica exactamente y por qué es legal bajo §4.2 |
| `recuperacion-y-entregable.md` | lotes y memoria de GPU, orden índice↔metadata, top-k, score por documento, deduplicación, `resultados.jsonl` |

Y `docs/specs/` guarda los specs de partida —fragmentador y addendum de
selección de encoder—, que son documentos fechados y no se reescriben: cuando
uno choca con una decisión posterior, manda la decisión.

Un extracto de las que más se preguntan:

**`fuente = path.name`, no la ruta relativa.** El enunciado pide el nombre
exacto del archivo entregado. La contrapartida es que dos archivos con el mismo
nombre en subdirectorios distintos comparten `fuente`, y en el corpus de ADL eso
pasa en **59 nombres repartidos por 186 archivos** (CSET_Georgetown 112, Amazon
Underworld 72, ESA_Space_Debris 2). Son colisiones legítimas: el mismo informe
archivado por tipo, el mismo tile en varios niveles de zoom. El pipeline no se
detiene por ellas —lo hacía, y moría en la primera corrida sin procesar nada—:
las marca con `meta["fuente_ambigua"]` y desambigua la identidad por
`meta["ruta_relativa"]`. Es una limitación del corpus, no del pipeline.

**`doc_id` sale del índice de ADL cuando lo hay.** Es la identidad oficial y
trazable (`F1-AIINDEX-001`). Sin índice se deriva de la ruta relativa, nunca del
nombre: derivarlo del nombre daba el mismo `doc_id` a los 7 PDF homónimos de
CSET y el pipeline se sobrescribía a sí mismo seis veces sin avisar.

**El índice filtra.** Con `--indice`, solo se procesa lo que ADL lista. En disco
hay 13 archivos con extractor que el índice no menciona —el enunciado, el propio
índice, `FASE ORDENADA CODEFEST.xlsx` y 10 `*_catalogo.json`/`*_registro.json`
de scraping— y no son documentos de la entrega. Se reportan en stderr, no se
procesan y no se borran.

**Sin extractor de HTML.** El corpus real de ADL no trae archivos `.html` ni
`.htm`. Mantener un extractor para un formato que el corpus no tiene es
complejidad muerta que arrastra dependencias (`beautifulsoup4`, `lxml`) y
fixtures sin ningún archivo real que los ejercite, así que no está en
`EXTRACTORES`.

**Detección de idioma con respaldo.** `langdetect` con `DetectorFactory.seed =
0`, restringido a `es`/`en`/`pt`. Si devuelve un idioma fuera del contrato
(francés, italiano) o no está instalado, se cae a un recuento de palabras
funcionales. Ambos caminos son deterministas.

## Ground truth interno

`ground/ground_truth.json`: **50 consultas etiquetadas a mano**, las mismas de
ADL (`q001`–`q050`), con cinco fragmentos relevantes cada una ordenados por
`rank` — 250 referencias sobre 234 `chunk_id` distintos y 132 archivos de
origen. `ground/ground_truth_metodologia.pdf` documenta cómo se construyó:
índice BM25 sobre el corpus completo, expansión bilingüe de cada consulta
escrita a mano —el corpus es 75 % inglés y las preguntas son en español—,
filtrado automático de índices, bibliografías y filas tabulares, y selección
final por juicio humano sobre el pool recuperado.

Los 234 `chunk_id` **resuelven contra el índice vigente**: comprobado línea a
línea sobre `indice/metadata.jsonl`, 234 de 234.

Dos límites que conviene tener presentes al leer cualquier métrica que salga de
aquí, y que el propio documento declara: no es exhaustivo —marca cinco
fragmentos por consulta, no todos los relevantes, así que un acierto fuera de la
lista no es necesariamente un fallo— y 14 consultas llevan salvedades en `notes`,
tres de ellas porque el supuesto de la pregunta no existe en el corpus (q031,
q046, q047).

Con esto se desbloquean las dos mediciones que estaban esperándolo: la ablación
del enriquecimiento de contexto y el barrido de configuraciones de fragmentación.

## Pendiente

- **Medir la ablación del enriquecimiento de contexto** (con y sin prefijo)
  contra el ground truth. La decisión de mantenerlo sigue siendo provisional
  hasta esa medición; ver `docs/decisiones/enriquecimiento-de-contexto.md` §6.
- **Ejecutar el barrido de configuraciones** (`scripts/barrido_fragmentacion.py`)
  y elegir con NDCG@10 y F1@3, no con la tabla de tamaños.
- **Confirmar los nombres de campo de la Tabla 2** contra el enunciado. El
  entregable se escribe hoy con `query_id`, `consulta` y `documentos[]`; si ADL
  los fija de otro modo, el único sitio que cambia es
  `registro_de_resultado()` en `generador.py`.
- **Relanzar `scripts/verificar_corpus.py`** (el checklist formal de aceptación,
  con su doble corrida completa para determinismo) desde que cambió la
  paralelización del orquestador. Los mismos criterios se comprobaron a mano
  contra la salida real más arriba —0 violaciones de contrato sobre los 1826
  documentos—, pero el script en sí no se ha vuelto a correr de punta a punta.
- **Escribir `entrega/informe_tecnico.pdf`**, que no lo genera el pipeline.
  `scripts/preparar_entrega.py` avisa si falta.

Ya no está pendiente, y la bitácora lo daba por hacer hasta el 11 ago: la
re-fragmentación con el tokenizador real, la construcción del índice completo,
la corrida de las 50 consultas contra él y la construcción del ground truth.
