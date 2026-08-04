# Capa de extracción — CODEFEST Ad Astra 2026 (Etapa 1)

Contrato de datos y extractores del pipeline RAG. Esta capa convierte cada
archivo del corpus en un `Documento` normalizado y lo persiste. **No** hace
chunking, embeddings, indexación ni recuperación: eso vive en capas
posteriores y consume la salida de `extraidos/`.

Estado: contrato, limpieza, orquestador y extractor **HTML** completos. Los
demás formatos son stubs documentados, listos para que otra persona los
complete.

## Instalación

Requiere Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
```

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

## Correr las pruebas

```bash
python -m pytest
```

172 pruebas. Las cinco que exige el enunciado:

| Requisito | Dónde |
|---|---|
| 1. `validar_documento` limpio para toda salida HTML | `tests/test_html.py::test_ninguna_salida_viola_el_contrato` |
| 2. Archivo malformado → `bloques=[]` y `errores` no vacía | `tests/test_html.py::test_archivo_malformado_no_lanza_excepcion` |
| 3. Dos corridas → bytes idénticos | `tests/test_orquestador.py::test_dos_corridas_producen_bytes_identicos` |
| 4. `fuente` = nombre exacto del archivo | `tests/test_html.py::test_fuente_conserva_la_extension_y_el_nombre_sin_normalizar` |
| 5. Breadcrumb correcto a tres niveles | `tests/test_html.py::test_breadcrumb_en_documento_anidado_a_tres_niveles` |

Los fixtures binarios (`malformado.html`, `nfd.html`) no se editan a mano; se
regeneran con `python fixtures/generar_binarios.py`.

## Estructura

```
contrato.py          Bloque, Documento, calcular_doc_id, validar_documento
indice.py            lectura del índice maestro de ADL (solo lee)
limpieza.py          normalización, idioma, detección de repetidos
orquestador.py       recorrido, persistencia y CLI
extractores/
    html.py          completo — implementación de referencia
    pdf.py           stub documentado
    json_.py         stub documentado
    tabular.py       stub documentado (csv + xlsx)
    imagen.py        stub documentado (OCR)
    pbf.py           stub documentado (mapas vectoriales)
    texto.py         stub documentado (texto plano: .txt, .md)
fixtures/            corpus sintético
scripts/             herramientas fuera del pipeline (verificación contra el corpus real)
tests/               pytest
```

Dependencias: `contrato` → `limpieza`. Los extractores dependen de ambos.
`limpieza` no depende de nada del proyecto, así que se puede usar suelto.

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
    formato: str        # "pdf"|"html"|"json"|"csv"|"xlsx"|"imagen"|"pbf"|"texto"
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

Los stubs ya traen la estrategia y la trampa principal de cada formato en su
docstring. Léelo antes de empezar: está ahí para ahorrarte el descubrimiento.

**1. Escribe primero las fixtures y las pruebas.** Añade a `fixtures/` al menos
un archivo bien formado, uno con boilerplate y uno corrupto. Copia
`tests/test_html.py` como plantilla: la mitad de sus pruebas aplican a
cualquier formato con solo cambiar el módulo.

**2. Implementa la firma exacta.** Sin excepciones, sin estado global, sin
escribir a disco:

```python
def extraer(path: Path, fenomeno: int) -> Documento:
```

**3. Envuelve todo en el blindaje.** El patrón está en `extractores/html.py`:

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
tu formato se repite: páginas en PDF, hojas en XLSX, secciones en HTML.

**5. Mantén el breadcrumb.** Una pila de `(nivel, texto)`; un título de nivel N
cierra los de nivel >= N. **Construye una lista nueva para cada `ruta`**: si
compartes la misma lista entre bloques, todos acaban viendo el último
breadcrumb. Hay una prueba para eso
(`test_la_ruta_de_un_bloque_no_se_comparte_entre_bloques`).

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

**Un HTML con bytes NUL se descarta entero.** BeautifulSoup es tan tolerante
que casi nunca falla: le das basura binaria y devuelve un árbol con fragmentos
de texto plausible. Ese es el peor resultado posible, porque entra al índice
sin que nadie lo note. Un byte NUL en un archivo de texto significa corrupción
o truncamiento, así que se registra el error y no se extrae nada.

**El boilerplate se detecta por repetición entre secciones.** Después de podar
`nav`/`footer`/`script`, lo que queda son menús inline y enlaces de navegación.
Un texto corto que reaparece en casi todas las secciones es navegación; uno
largo casi siempre es contenido. Los títulos nunca se descartan, para que el
breadcrumb de los bloques restantes siga siendo válido.

**Detección de idioma con respaldo.** `langdetect` con `DetectorFactory.seed =
0`, restringido a `es`/`en`/`pt`. Si devuelve un idioma fuera del contrato
(francés, italiano) o no está instalado, se cae a un recuento de palabras
funcionales. Ambos caminos son deterministas.

**El texto anidado no se duplica.** `_texto_propio` toma solo el texto que no
pertenece a otro elemento de interés descendiente. Sin esto, un `<p>` dentro de
un `<li>` aparece dos veces: una en el párrafo y otra en el elemento de lista.

**En HTML, `atomico` siempre es `False` y `pagina` siempre es `None`.** Un HTML
no tiene páginas y ninguno de sus bloques es un registro indivisible. En
tabular y PBF, en cambio, cada fila o feature debe ir con `atomico=True`.

## Pendiente

- Extractores de PDF, JSON, CSV/XLSX, imagen, PBF y texto plano (stubs con
  estrategia escrita). Con el corpus completo, los 1826 documentos salen con
  `bloques=[]` y "extractor no implementado": esta etapa arregla el recorrido y
  la identidad, no la extracción.
- `.avif` necesita `pillow-avif-plugin` cuando se implemente el extractor de
  imagen (1 archivo, F2-SWF-065).
