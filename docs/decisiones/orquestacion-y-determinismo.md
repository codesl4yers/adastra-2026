# Decisión — Orquestación, identidad y determinismo

CODEFEST Ad Astra 2026 · Etapa 1 · 11 ago 2026
Estado: **implementado**, corrido sobre los 1826 documentos del corpus real.

Recoge las decisiones de `orquestador.py`, `indice.py` y `contrato.py` que hasta
ahora vivían en sus docstrings. El plan del que salió la adaptación al corpus
real está en `docs/superpowers/plans/2026-08-02-orquestador-corpus-adl.md`.

---

## 0. El contrato de datos

Las dos únicas estructuras que cruzan la frontera entre extractores y el resto
del pipeline:

```python
@dataclass(frozen=True)
class Bloque:
    texto: str          # limpio, sin marcado, NFC, sin espacios redundantes
    tipo: str           # "titulo" | "parrafo" | "lista" | "fila" | "ocr"
    nivel: int | None   # 1..6 si y solo si tipo == "titulo"
    ruta: list[str]     # breadcrumb de encabezados ancestros vigentes
    pagina: int | None  # 1-based si el formato tiene páginas
    atomico: bool       # True => unidad indivisible (fila de CSV, feature)
    datos: dict[str, str]   # campos que no entran al texto (identificadores)

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

`validar_documento(doc) -> list[str]` devuelve los invariantes violados, vacía si
está correcto. Ver §8.

### Las cinco reglas que no se negocian

1. **Nada de modelos generativos.** Ni para limpiar, ni para resumir, ni para
   clasificar. Solo reglas, parsers y modelos encoder.
2. **`fuente` es inmutable.** Es el nombre exacto del archivo entregado, con su
   extensión, sin renombrar ni normalizar. La evaluación empareja por este campo.
   `doc_id` es interno y derivado; `fuente` es el contrato con el jurado.
3. **Determinismo total.** Nada de iterar `set()` sin ordenar, nada de depender
   del orden de `rglob`, nada de `hash()` nativo. Ver §5.
4. **Ningún extractor tumba el pipeline.** Un archivo corrupto produce un
   `Documento` válido con `bloques=[]` y el motivo en `errores`.
5. **Lo único que detiene la corrida es una identidad inconsistente**, detectada
   antes de escribir nada. Ver §3.

## 1. Quién es un documento

Tres campos y tres reglas distintas.

**`fuente` es inmutable**: el nombre exacto del archivo entregado, con su
extensión, sin renombrar ni normalizar. Es el campo con el que el jurado empareja
(§10.2.1). Nunca se toca, ni siquiera cuando choca con otro.

**`doc_id` es interno y derivado**, y se admite en tres formas, por orden de
preferencia: el `DOC_ID` del índice maestro de ADL (`F1-AIINDEX-001`), que es la
identidad oficial y trazable; derivado de la **ruta relativa**; derivado de
`fuente`. `validar_documento` acepta las tres y ninguna más: un `doc_id` que no
sea trazable a algo estable no vale.

Derivarlo del nombre en vez de la ruta le daba el mismo `doc_id` a los 7 PDF
homónimos de CSET, y el pipeline se sobrescribía a sí mismo seis veces sin
avisar.

**Se usa blake2b y no `hash()`**: el `hash()` de una cadena depende de
`PYTHONHASHSEED` y cambiaría entre corridas. 8 bytes son 16 caracteres hex,
suficiente para decenas de miles de documentos y corto para un nombre de archivo.

## 2. Los nombres se repiten: 59 nombres en 186 archivos

Son colisiones legítimas del corpus —el mismo informe archivado por tipo, el
mismo tile en varios niveles de zoom—, no un error. El pipeline **lo hacía**
abortar, y moría en la primera corrida sin procesar nada. Ahora se marcan con
`meta["fuente_ambigua"]` y la identidad se desambigua por
`meta["ruta_relativa"]`.

La consecuencia se arrastra a todo lo demás: el manifiesto se ordena por
`(fuente, ruta_relativa)` y no solo por `fuente`, o el orden relativo de esos
homónimos quedaría a merced del sistema de archivos; y `verificar_cobertura`
empareja por `doc_id`.

## 3. Lo único que detiene la corrida

Un `doc_id` duplicado entre dos documentos, o un índice de ADL malformado. Las
dos son inconsistencias de **identidad**, no problemas de extracción:

- con dos `doc_id` iguales, el JSON de uno sobrescribe al del otro y el
  manifiesto tiene dos líneas apuntando al mismo archivo;
- un índice con `DOC_ID` o rutas repetidas, fenómeno fuera de rango o columnas
  ausentes invalida la trazabilidad completa de la entrega.

Se detectan **antes de escribir nada**. Un caso concreto que parece pedante y no
lo es: un `DOC_ID` con un carácter que no sirve como nombre de archivo (una barra
por typo o por autocorrección de Excel) reventaría la escritura a mitad de una
corrida de 1826 archivos —sin manifiesto, porque se escribe al final, y sin la
corrida buena anterior si se pasó `--limpiar`—.

Todo lo demás se reporta y sigue: un archivo corrupto, una extensión sin
extractor, un archivo del índice que no está en disco.

## 4. El índice de ADL manda, y filtra

Con `--indice`, `doc_id`, `fenómeno` y `observatorio` salen del índice y **solo
se procesa lo que ADL lista**. En disco hay 13 archivos con extractor que el
índice no menciona —el enunciado, el propio índice, `FASE ORDENADA CODEFEST.xlsx`
y 10 catálogos de scraping— y no son documentos de la entrega. Se reportan en
stderr, no se procesan y no se borran.

La clave del cruce es la ruta relativa y no el nombre, por lo de §2. Se compara
con `is None` y no por verdad/falsedad: un índice vacío es un índice real —un
xlsx con cabecera y sin filas— y debe seguir filtrando a "nada", no comportarse
como si no se hubiera pasado `--indice`.

El fenómeno se resuelve por precedencia —índice, carpeta, `--fenomeno`— y **queda
escrito de dónde salió** (`origen_fenomeno`). Con la versión anterior, que caía
al valor por defecto sin decirlo, los 1367 documentos de F2 y F3 se etiquetaban
como fenómeno 1 en silencio.

## 5. Determinismo: dónde se rompe

Dos corridas sobre el mismo corpus tienen que producir los mismos bytes. Los
puntos donde eso se pierde, y qué se hace en cada uno:

| Fuente de no determinismo | Qué se hace |
|---|---|
| `rglob` depende del sistema de archivos | se ordena explícitamente por ruta POSIX |
| `hash()` depende de `PYTHONHASHSEED` | blake2b |
| iterar un `set` | se ordena antes de recorrer, siempre |
| orden de claves al serializar | `sort_keys=True` |
| CRLF en Windows | `newline="\n"` explícito en toda escritura |
| `langdetect` | `DetectorFactory.seed = 0`, y respaldo por palabras funcionales cuando devuelve un idioma fuera del contrato |
| librerías que no garantizan orden (features de un tile) | se ordena en el extractor |

El paralelismo **no** entra en esa lista: cada extracción es independiente y la
lista se ordena después, así que el orden en que terminen los procesos da igual.
Está comprobado en `test_dos_corridas_producen_bytes_identicos`, también con
`--procesos`.

Los metadatos del sistema de archivos (`.DS_Store` —el corpus viene de un Mac y
trae nueve—, `Thumbs.db`, `desktop.ini`, los gemelos AppleDouble `._nombre`) no
son "formatos sin extractor" sino basura del sistema operativo, y listarlos como
lo primero escondería a los segundos, que son los que hay que mirar.

## 6. Paralelismo y memoria

Los 759 PDF suman 2,9 GB y ~31 000 páginas, y el 99 % de ese tiempo está dentro
de `pdfplumber`, no en código propio que se pueda optimizar. En secuencial son
unas 3 horas; con 6 procesos, unos 30 minutos.

**pdfminer no le devuelve al sistema operativo toda la memoria que reserva por
PDF.** Medido sobre el corpus real: un worker que extrae el atlas de RESDAL
(250 páginas) retiene ~200 MB después de liberar el documento, y ese poso se
acumula con cada PDF grande siguiente, hasta ~330 MB de RSS tras dos atlas
seguidos. No hay forma de pedirle que la libere: la única manera de recuperarla
es que el sistema operativo se quede con el proceso entero.

**Se recicla el pool completo, no el worker.**
`ProcessPoolExecutor(max_tasks_per_child=...)` haría lo segundo, que es lo
elegante, pero tiene un [deadlock conocido de
CPython](https://github.com/python/cpython/issues/115634) sin corregir hasta
3.14: el pool se cuelga en cuanto un worker llega a su cupo y se recicla a mitad
de una tanda de envíos. Este proyecto pide 3.11+, así que se abren pools
sucesivos de `procesos × reciclar_cada` documentos (25 por defecto), cerrando cada
uno con el `with` normal, que es el único camino de arranque y cierre que de
verdad está probado.

**El valor por defecto de `--procesos` se calcula, no se adivina**: RAM libre
entre 600 MB por proceso, con el número de núcleos como tope, y se imprime al
arrancar. Antes el defecto era 1 —tres horas— y usar más obligaba al operador a
adivinar un número y arriesgarse a un `MemoryError` a mitad de una corrida de
horas.

**Cada documento se escribe en cuanto termina**, no al final: acumular 1826
documentos en memoria antes del primer byte es un segundo consumidor de RAM en el
proceso orquestador. Por eso se recorre con `as_completed` (orden de llegada) y
no con `pool.map` (orden de envío).

Con `procesos=1` no se arranca ningún pool: en un corpus pequeño, levantar
intérpretes cuesta más que el trabajo.

## 7. El manifiesto es la herramienta de regresión

Una línea por documento con su tamaño, idioma, número de bloques y errores. Su
estabilidad importa tanto como su contenido, porque su razón de ser es el `diff`
entre dos corridas: si sale vacío, ningún documento cambió; si sale con líneas,
las líneas dicen qué documentos revisar.

`--reintentar-errores` lo usa como entrada: junta los `doc_id` con errores,
reextrae solo esos y fusiona el resultado en el mismo manifiesto sin tocar el
resto del corpus. El caso típico es Tesseract no disponible en la corrida
anterior. Es incompatible con `--limpiar`, que borraría justo lo que hay que
conservar, y exige una corrida completa previa: sin manifiesto no sabría qué está
intacto.

`--limpiar` borra por patrón (`*.json` y el manifiesto), no por autoría: no
distingue un `.json` que escribió el pipeline de uno ajeno que viva en el mismo
directorio. Quien pase `--salida` debe usar un directorio dedicado.

## 7.1 Lo que se verificó contra el corpus real

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

Los 8 documentos sin bloques son legítimos: 1 JSON de origen vacío, 2 PDF
corruptos (`PdfminerException: No /Root object!`) y 5 imágenes donde Tesseract
corrió y no encontró texto fiable —son fotografías—. Sumando los 7 CSV/XLSX
truncados a 5000 filas por diseño, quedan 15 documentos con algo anotado en
`errores`; ninguno es un fallo del pipeline.

Dos cosas que conviene saber antes de indexar: los 26 CSV aportan casi tanto
texto como los 759 PDF —17,6 M de caracteres frente a 90,7 M, aun con el
truncado—, así que pesan desproporcionadamente en el índice; y entre el 5 y el
11 % de los PDF vienen escaneados.

## 8. `validar_documento` es para los tests

Devuelve la lista de invariantes violados y no lanza. **El pipeline en producción
no la llama**: un documento inválido debe ser imposible de construir, no algo que
se detecte al final. Existe para probar extractores y para auditar el corpus
(`scripts/verificar_corpus.py`).

Las dataclasses del contrato son `frozen` pero contienen listas y diccionarios:
"frozen" impide reasignar campos, no mutar su contenido. Se tratan como valores —
se construyen de una vez y no se modifican.

`documento_desde_dict` exige los campos obligatorios y **tolera la ausencia de los
que tienen default**. No es laxitud: un `extraidos/` de una corrida anterior no
tiene por qué traer los campos que se añadieron después, y hacerlo fallar
obligaría a reextraer el corpus entero para leer un solo documento.
