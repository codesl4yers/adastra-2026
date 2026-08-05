# Capas de extracción y fragmentación — CODEFEST Ad Astra 2026 (Etapa 1)

Contrato de datos, extractores y fragmentador del pipeline RAG. La primera capa
convierte cada archivo del corpus en un `Documento` normalizado; la segunda lo
parte en `Fragmento` listos para codificar. **No** hay embeddings, indexación ni
recuperación: eso vive en capas posteriores y consume `fragmentos.jsonl`.

Estado: todas las capas implementadas —contrato, limpieza, orquestador,
extractores, segmentador y fragmentador—, con 545 pruebas, y corrida completa
sobre los 1826 archivos del corpus real de ADL: extracción, OCR, fragmentación
y validación de contrato, las tres al 100 %, no sobre una muestra.

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

**El OCR ya está operativo.** `pytesseract` + Tesseract (`spa+eng+por`)
reconocen los PDF escaneados y las imágenes con texto real. En Windows, si el
binario no aparece por PATH —el instalador no siempre lo agrega, o queda una
entrada de una instalación anterior en otra carpeta—, `extractores/ocr.py`
prueba antes de rendirse las rutas de instalación por defecto
(`C:\Program Files\Tesseract-OCR\tesseract.exe` y su variante `(x86)`).

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

Sobre el corpus completo: **1826 documentos → 140.686 fragmentos**, mediana de
123 palabras (p95: 234), 40.978 atómicos (filas de CSV/XLSX y features de PBF)
y 2693 huérfanos fusionados. Tarda unos 10-15 minutos, casi todo dentro de
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

El conteo de tokens es inyectable (`ConfigFragmentacion.contar_tokens`). Por
defecto usa la estimación conservadora `ceil(palabras × 1.6)`, porque **todavía
no hay encoder elegido**. En cuanto se elija hay que cambiarlo por su
`AutoTokenizer` y **re-fragmentar el corpus completo**: los `num_tokens`
estimados no valen para la entrega.

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

## Correr las pruebas

```bash
python -m pytest
```

545 pruebas. Las cinco que exige el enunciado:

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
scripts/             herramientas fuera del pipeline (verificación y barrido)
tests/               pytest
```

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
Cada módulo documenta en su docstring la estrategia y la trampa del formato:
léelo antes de tocar nada parecido.

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

Las que no son obvias, con su porqué:

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

## Pendiente

- **Elegir el encoder** y sustituir `estimar_tokens` por su tokenizador real.
  Bloquea la re-fragmentación del corpus completo, así que cuanto más tarde se
  cierre, más cara sale la re-corrida.
- **Ejecutar el barrido de configuraciones** (`scripts/barrido_fragmentacion.py`)
  sobre el corpus ya extraído. La tabla no elige la configuración: elegirla
  exige medir NDCG@10 y F1@3 contra un ground truth interno que aún no existe.
- **Construir ese ground truth**: 20–30 consultas etiquetadas a mano. Bloquea la
  elección tanto del encoder como de la configuración de fragmentación.
- **Relanzar `scripts/verificar_corpus.py`** (el checklist formal de aceptación,
  con su doble corrida completa para determinismo) desde que cambió la
  paralelización del orquestador. Los mismos criterios se comprobaron a mano
  contra la salida real más arriba —0 violaciones de contrato sobre los 1826
  documentos—, pero el script en sí no se ha vuelto a correr de punta a punta.
