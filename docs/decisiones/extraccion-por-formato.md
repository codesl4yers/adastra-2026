# Decisión — Qué se extrae de cada formato, y qué se tira

CODEFEST Ad Astra 2026 · Etapa 1 · 11 ago 2026
Estado: **implementado y corrido sobre los 1826 documentos** (0 violaciones de
contrato). Recoge las decisiones que hasta ahora solo estaban en los docstrings
de `extractores/`.

---

## 0. Resumen por formato

| Formato | Unidad de bloque | Jerarquía | Notas |
|---|---|---|---|
| `pdf` | párrafo | por tamaño de fuente | detecta dos columnas por el corredor vertical; cabeceras y pies repetidos se descartan |
| `json` | párrafo | `title` → `sections[].heading` | reconoce el esquema de artículo del scraper; cae a recorrido genérico si no lo encuentra |
| `csv` / `xlsx` | fila **atómica** | hoja (xlsx) | `columna: valor` en cada fila, para que se recupere sin la cabecera |
| `pbf` | feature **atómica** | capa | solo `properties`; la geometría nunca entra al índice |
| `texto` | párrafo | encabezados Markdown | párrafos por línea en blanco, no por salto de línea |
| `imagen` | línea de OCR | — | metadata EXIF siempre; texto solo si hay Tesseract |

## 1. La regla común

Un extractor es una función pura `extraer(path, fenomeno) -> Documento` que
**nunca lanza**: un archivo corrupto produce un `Documento` válido con
`bloques=[]` y el motivo en `errores`. El pipeline procesa 1826 archivos de
proveniencia desigual; una excepción que suba mata la corrida entera por un solo
archivo malo.

`bloques` se asigna de una vez al final del `try`. Una lista a medio llenar es
peor que ninguna, porque parece válida.

Todo lo que se emite pasa por `limpieza.normalizar_texto` (NFC, espacios
colapsados, invisibles fuera) y por la pila de encabezados de
`extractores.comun.Jerarquia`, que es la que garantiza que la `ruta` de cada
bloque coincida con los ancestros que `contrato.validar_documento` reconstruye.
Llevar la pila por cuenta propia en cada extractor producía documentos que no
validaban por un detalle de bookkeeping.

## 2. Dos filtros distintos para dos situaciones distintas

| Función | Juzga | Criterio |
|---|---|---|
| `es_texto_natural` | un valor **suelto** que se emitiría como bloque | descarta URL, correo, DOI, hash, UUID, fecha ISO, ruta de archivo, solo cifras, solo símbolos |
| `es_valor_opaco` | un valor **dentro de un registro**, acompañado de su columna | descarta solo lo irrecuperable: URL, correo, hash, UUID, ruta |

La diferencia importa: `"2026"` suelto es ruido, pero `"year: 2026"` dentro de
una fila es recuperable y se conserva. Un solo filtro para los dos casos o
ensucia el índice o tira datos.

El nombre de la columna se repite en **cada** fila (`serializar_registro`). Es
redundante para un humano y es lo que hace que la fila se recupere sola: el
vector de `país: Colombia | año: 2026` dice qué es cada cosa y el de
`Colombia | 2026` no.

## 3. PDF — 759 documentos, ~31 000 páginas, 2,9 GB

Es el formato que domina la corrida y el que tiene más decisiones.

**Se usa `pdfplumber` y no un lector de texto plano** porque hacen falta las
coordenadas y el tamaño de fuente de cada palabra para dos cosas que ningún
`extract_text` da: distinguir un título de su cuerpo y reconstruir el orden de
lectura de una página a dos columnas.

**El PDF no tiene orden de lectura, tiene instrucciones de dibujo.** En una
página a dos columnas, ordenar por altura intercala las dos columnas línea a
línea y produce un texto que parece correcto y es basura entreverada. Por eso se
proyectan las palabras sobre el eje horizontal y se busca el **corredor
vertical** vacío más ancho de la zona central (`CORREDOR_MINIMO`, `ZONA_CENTRAL`,
`MINIMO_POR_COLUMNA`). Que caiga en el centro es lo que lo distingue de un
margen. Las palabras que cruzan el corredor —un titular a ancho completo— se
asignan a la columna con la que más se solapan: como mucho coloca mal un
titular, mientras que tratarlas aparte exigiría segmentar la página en franjas y
multiplicaría los modos de fallo.

**La jerarquía sale del tamaño relativo, no del absoluto.** El tamaño más
frecuente del documento entero es el cuerpo; los mayores se ordenan y cada
escalón es un nivel. 14 pt es un título de primer nivel en un documento y de
tercero en otro. El tamaño se mide sobre el documento y no por página: una
portada tiene su propia moda y no representa al cuerpo.

**El tamaño no basta para ser título**: los pies de autores y las citas
destacadas van en cuerpo mayor que el texto. Se exige además brevedad
(`MAXIMO_PALABRAS_TITULO = 20`). Tomar un párrafo por título mete el párrafo
entero en el breadcrumb de todo lo que venga detrás.

**Los párrafos se cortan por el interlineado dominante del propio documento**, no
por una constante, y el umbral se mide contra el mayor entre ese interlineado y
el tamaño de la línea: un titular de 40 pt salta 45 pt entre sus líneas y no por
eso son párrafos distintos. El interlineado es la moda de los huecos, no la
media: la media la arrastran los saltos entre párrafos, que son justo lo que se
quiere detectar.

**Las palabras de una página no sobreviven a su página.** Un atlas de 250
páginas son millones de diccionarios de palabra; materializarlos todos consume
cientos de megabytes por documento y, con varios procesos, mata el worker con
`MemoryError`. Solo se conservan las líneas, dos órdenes de magnitud más
pequeñas, y se llama a `page.close()` para vaciar la caché de pdfplumber.

**Se silencia el logger de pdfminer** por encima de ERROR: avisa por cada fuente
sin `FontBBox` parseable y en una corrida del corpus son decenas de líneas en
stderr entre las que se pierden los errores de verdad. No afecta a la
extracción.

Cuándo se manda una página a OCR, con qué umbrales y por qué la decisión es por
página y no por documento: `fragmentos-fuera-de-norma.md` §7.

## 4. CSV y XLSX — 30 archivos del índice

Un solo módulo para los dos formatos: comparten el modelo de datos y toda la
serialización, y solo cambia el lector.

**Un CSV no declara ni su codificación ni su delimitador.** Un archivo exportado
desde Excel en español viene en cp1252 y separado por `;`: leerlo como UTF-8 con
`,` produce **una única columna con todo dentro y ni un solo error**. El
delimitador se detecta con `csv.Sniffer` sobre los primeros 8 KB —con más no
acierta más— y ante la duda manda la coma, que produce el error visible (una
sola columna) en vez de uno silencioso.

**Las codificaciones se prueban en orden fijo**: `utf-8-sig`, `cp1252`,
`latin-1`. Fijo a propósito: una autodetección probabilística puede decodificar
distinto en dos corridas y romper la reproducibilidad. `utf-8-sig` va primera
porque acierta también sin BOM; `latin-1` va última porque nunca falla y taparía
a las demás. Cuál funcionó queda escrito en `meta["codificacion"]`: un cambio de
codificación cambia el texto, y sin dejarlo anotado no hay forma de saber
después por qué dos corpus difieren.

**XLSX**: `data_only=True` o las celdas con fórmula devuelven la fórmula en vez
del valor; `read_only=True` o la hoja entera se carga en memoria. Las fechas
llegan como `datetime` y se emiten en ISO, sin la hora cuando no la hay.

**El título de una hoja se emite solo si tiene filas debajo, y antes que ellas.**
Una hoja vacía dejaría un bloque que solo dice "Datos"; emitirlo después dejaría
a las filas fuera de su propia sección.

**Una fila con más celdas que columnas no se descarta**: sus celdas de más se
numeran (`columna_7`). Perder datos por un CSV mal formado es peor que indexar
un nombre de columna feo.

Qué columnas entran al vector y cuáles viajan en `Bloque.datos`:
`campos-indexables-tabulares.md`. El tope de 5000 filas por archivo, también.

## 5. JSON — 954 documentos, el 52 % del corpus

**No es un recorrido genérico de árbol.** Casi todos son artículos de un scraper
con esquema estable (`title`, `body_paragraphs`, `sections[].heading`), y 363 son
alertas tempranas de la Defensoría con un `alerta_meta` donde `tema_clave` y
`municipios` son contenido y `detail_id` es un identificador interno. Un
recorrido ciego perdería la jerarquía título/sección —que es lo único que le da
fronteras estructurales al fragmentador en la mitad del corpus— e indexaría URL y
hashes como si fueran prosa.

**El orden de emisión no es arbitrario**: título, resumen, campos textuales de
`alerta_meta`, cuerpo plano, listas y por último las secciones. El cuerpo va
antes que las secciones porque emitido después colgaría de la última sección
abierta, y su breadcrumb diría que pertenece a una sección con la que no tiene
nada que ver.

**`body_paragraphs` o `body_text`, nunca los dos.** `body_text` es el mismo
cuerpo concatenado: indexar ambos duplica el documento en el índice y hace que un
fragmento compita consigo mismo por los diez puestos del NDCG@10.

**GeoJSON**: solo las `properties`; la geometría se resume en un bounding box en
`meta`. El 99 % de los bytes de un GeoJSON son coordenadas, que no responden a
ninguna consulta en lenguaje natural y desplazan a los fragmentos que sí. El
corpus actual no trae ninguno, pero `.geojson` está registrado.

**El recorrido genérico es iterativo y con tope de profundidad** (40): la
recursión sobre un JSON arbitrario es una forma cómoda de reventar la pila con un
archivo de entrada. Las claves se recorren **ordenadas**, o el orden de los
bloques dependería del orden de inserción del archivo. En un JSON la clave es el
encabezado de su contenido, así que el camino de claves se materializa como
títulos —sin eso, la `ruta` no estaría respaldada por títulos y el documento no
validaría— y esos títulos se abren de forma perezosa: una rama entera de URL no
deja tras de sí un vector que solo dice "enlaces".

## 6. PBF — 73 tiles vectoriales

**`.pbf` designa dos formatos distintos.** Mapbox Vector Tile (los 73 del corpus,
todos de Amazon Underworld) y OpenStreetMap PBF, que necesita `osmium`. El
segundo se detecta por su cabecera `OSMHeader` y se reporta, en vez de intentar
leerlo con la librería equivocada y devolver un error incomprensible.

**Solo las propiedades; la geometría nunca entra al índice.** Un tile es
coordenadas codificadas en delta: convertirlo a texto produce megabytes de ruido
numérico que destruyen la recuperación. Lo que sí vale son los topónimos, la
clasificación administrativa y qué grupo armado tiene presencia en cada
municipio.

**Las propiedades cuyo valor es una negación se descartan** (`FALSO`, `false`,
`0`, `no`, `none`, `não`). Cada municipio trae una docena de banderas de las que
casi todas son falsas: repetir `au_eln: FALSO` en 250 features no distingue nada
y desplaza del vector al contenido que sí discrimina. Un booleano `True`, en
cambio, sí se emite: la presencia es informativa, la ausencia no.

**El bounding box se calcula del `z/x/y` de la ruta, no de las geometrías.** Las
coordenadas de un tile son relativas al propio tile: derivar lat/lon de ellas sin
cuidado da datos espaciales verosímiles y falsos. Del `z/x/y` es exacto por
definición.

**Orden explícito**: capas por nombre y features por `fid`. El orden en que la
librería las devuelve no está garantizado entre versiones, y sin ordenar la
salida deja de ser reproducible. El `id` que expone la librería vale 0 en todos
los features de este corpus, así que no sirve para desempatar.

`MAXIMO_FEATURES = 5000` por tile. Los del corpus rondan las 250; uno de 100 000
puntos sería ruido, no contenido.

## 7. Imagen — 9 archivos

**Primero lo que se sabe sin mirar los píxeles** —dimensiones, formato, EXIF con
fecha y GPS— y después el OCR. En un corpus con material satelital esa metadata
suele valer más que el texto reconocido, y se obtiene siempre, incluso sin
Tesseract instalado o con una imagen sin una letra.

Las etiquetas EXIF se leen por número y no por nombre, que es lo que devuelve
Pillow sin depender de su tabla. AVIF (un archivo del corpus, `F2-SWF-065`)
necesita `pillow-avif-plugin`; sin él, el documento sale con el motivo en
`errores` en vez de reventar.

## 8. Texto plano y Markdown — 1 archivo

**Los párrafos se cortan por líneas en blanco, nunca por salto de línea**: el
texto plano de un informe viene cortado a 80 columnas y partir por `\n` trocearía
cada frase en pedazos que no son oraciones.

El texto plano extraído de un PDF conserva los cortes de página con sus cabeceras
y pies repetidos en medio del cuerpo: se pasan las páginas —separadas por el
carácter de salto de página— como unidades a `limpieza.lineas_repetidas`. Sin eso
el índice acaba lleno de `Secure World Foundation | 12` entre párrafo y párrafo.

En Markdown se sigue el estado de las vallas ``` antes de interpretar una
almohadilla: un bloque de código puede traer `#` al principio de línea sin ser un
encabezado.

`SWF_full-text.txt`, el único archivo de este formato en el corpus, empieza con
una cabecera `SOURCE:` / `SCRAPED:`. Son metadata, no contenido, y van a `meta`:
indexarlas metería una URL y un timestamp como primer párrafo del documento,
justo donde más peso tienen.

## 9. OCR — compartido por imagen y PDF

**Tesseract es un binario del sistema, no un paquete de Python.** Se comprueba su
disponibilidad antes de cada uso (`hay_ocr`) en vez de dejar que salte una
excepción a mitad de una corrida de 1826 documentos. Tener `pytesseract`
instalado sin Tesseract detrás es el caso habitual y el que más despista, porque
el import funciona y el fallo aparece mucho después. En Windows se prueban además
las dos rutas de instalación por defecto, porque el instalador no siempre agrega
el binario al PATH.

**El OCR nunca falla.** Ante una imagen sin texto devuelve basura plausible
(`"|| ,-. l1"`). El filtro de confianza (`UMBRAL_CONFIANZA = 60`) descarta por
**línea** y no por palabra: una palabra dudosa dentro de una frase legible casi
siempre es correcta, y quitarla dejaría un hueco en mitad de la oración.

**No es determinista entre versiones.** Fijar semillas en Python no sirve de nada
porque el proceso es externo. Lo que se hace: fijar idiomas (`spa+eng+por`) y
configuración (`--oem 3 --psm 3`), y registrar la versión en `meta`, para poder
tratar un cambio de versión por lo que es —un cambio de corpus que obliga a
reindexar— y no como una diferencia inexplicable entre dos corridas.

El único preprocesado es la escala de grises: es el que más aporta y el único que
no puede empeorar el resultado. Binarizar con umbral fijo sí lo empeora en mapas
y gráficos, que son la mitad de las imágenes de este corpus.

## 10. Sin extractor de HTML

El corpus real de ADL no trae `.html` ni `.htm`. Mantener un extractor para un
formato que el corpus no tiene es complejidad muerta que arrastra dependencias
(`beautifulsoup4`, `lxml`) y fixtures que ningún archivo real ejercita.

## 11. Cómo añadir un extractor nuevo

Los seis formatos del corpus ya están; esto vale para uno nuevo.

**0. Usa `extractores/comun.py`.** `Jerarquia` lleva la pila de encabezados que
`validar_documento` va a reconstruir y comparar; llevarla por tu cuenta produce
documentos que no validan por un detalle de bookkeeping. `es_texto_natural`,
`serializar_registro` y `construir_documento` cubren el resto.

**1. Escribe primero las fixtures y las pruebas.** Al menos un archivo bien
formado, uno con boilerplate y uno corrupto.

**2. Implementa la firma exacta.** Sin excepciones, sin estado global, sin
escribir a disco:

```python
def extraer(path: Path, fenomeno: int) -> Documento:
```

**3. Envuelve todo en el blindaje**, con `bloques` asignado de una sola vez al
final del `try`. Si algo falla a mitad, queda `[]` en lugar de una lista a medio
llenar, que es peor que nada porque parece válida:

```python
def extraer(path: Path, fenomeno: int) -> Documento:
    fuente = path.name          # nunca path.stem, nunca una ruta normalizada
    errores, meta, bloques = [], {}, []
    try:
        ...
        bloques = _extraer_bloques(...)
    except ExtraccionFallida as exc:
        errores.append(str(exc))
    except Exception as exc:
        errores.append(f"error inesperado ({type(exc).__name__}): {exc}")
    ...
```

**4. Usa `limpieza`, no reinventes.** `normalizar_texto` en todo texto antes de
construir el `Bloque`. `es_ruido_estructural` para numeración de páginas.
`lineas_repetidas` para cabeceras y pies, pasándole como "unidades" lo que en tu
formato se repite: páginas en PDF, hojas en XLSX.

**5. Mantén el breadcrumb.** Una pila de `(nivel, texto)`; un título de nivel N
cierra los de nivel >= N. **Construye una lista nueva para cada `ruta`**: si
compartes la misma lista entre bloques, todos acaban viendo el último breadcrumb.

**6. Ordena todo lo que recorras.** Claves de diccionario, resultados de `glob`,
features de una capa. Si el orden lo decide una librería, ordénalo tú.

**7. Regístralo** en `EXTRACTORES` de `orquestador.py`, mapeando extensión a
`(módulo, formato)`.

**8. Comprueba el contrato.** `validar_documento` debe salir vacía para todas tus
fixtures, y dos corridas deben dar bytes idénticos.
