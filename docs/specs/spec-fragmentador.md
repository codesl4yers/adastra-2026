# Spec — Capa de fragmentación (`fragmentador.py`)

CODEFEST Ad Astra 2026 · Etapa 1 · v1.0 · 4 ago 2026

> **Este spec es la foto del 4 ago 2026 y no se reescribe.** Es el documento de
> partida de la capa, no el estado vigente: cuando se redactó no había ni un
> extractor implementado ni encoder elegido. Lo que cambió después está en
> `docs/decisiones/` —conteo de tokens, enriquecimiento de contexto, fragmentos
> fuera de norma— y el estado actual, en el README. Donde el spec y una decisión
> posterior se contradigan, manda la decisión.

**Objetivo.** Convertir cada `Documento` de `extraidos/` en una lista de
`Fragmento` lista para codificar, respetando la completitud lingüística (§3.3
del enunciado), el límite de 250 palabras (§9.2.1) y los ocho campos de
metadata obligatorios (Tabla 1).

**Alcance.** Este spec cubre **solo** la fragmentación. No elige encoder, no
construye el índice FAISS y no implementa recuperación. Consume el contrato de
`contrato.py` y produce un artefacto intermedio que la capa de codificación
leerá.

---

## §0 Estado del que se parte

Verificado contra el repo el 4 ago 2026 (rama `claude/remove-html-extractors-v07oqf`).

| Dato | Valor |
|---|---|
| Extractores implementados | **0** (`html.py` eliminado; pdf, json_, tabular, imagen, pbf, texto son stubs) |
| Pruebas en verde | 135 |
| Documentos del corpus | 1826 (json 954 · pdf 759 · pbf 73 · csv 26 · jpg 8 · xlsx 4 · avif 1 · txt 1) |
| Encoder elegido | **ninguno todavía** |

**Consecuencia operativa:** el fragmentador se construye y se prueba contra
`Documento` armados a mano en los tests, no contra salida real de extractores.
Es viable —el contrato es estable— pero significa que **no queda validado sobre
texto real hasta que exista el extractor de JSON**. Los dos trabajos deberían ir
en paralelo, y la validación sobre corpus real es una tarea posterior explícita
(§8.2).

---

## §1 Restricciones globales

Aplican a **todas** las tareas. Heredadas del README y del enunciado.

1. **Nada de modelos generativos**, en ninguna parte. Ni para segmentar, ni para
   resumir, ni para decidir dónde cortar. La fragmentación es determinista y
   basada en reglas.
2. **`fuente` es inmutable.** Se copia tal cual desde el `Documento`. Es el
   campo de emparejamiento con el ground truth del jurado (§10.2.1).
3. **`texto` del fragmento es el texto original, sin modificaciones** (Tabla 1).
   El enriquecimiento del §6.3 vive en un campo aparte y **nunca** sustituye a
   `texto`.
4. **Determinismo total.** Mismo `Documento` + misma configuración → mismos
   fragmentos, byte a byte. Nada de `set()` sin ordenar, nada de `hash()` nativo.
5. **Ningún documento tumba la corrida.** Un `Documento` con `bloques=[]`
   produce cero fragmentos y se registra; no lanza.
6. **Nada de escritura a disco fuera del CLI.** `fragmentador.py` expone
   funciones puras; la persistencia vive en una única función de salida.
7. Salida JSON Lines: `ensure_ascii=False`, `sort_keys=True`, un objeto por
   línea, `newline="\n"`.

---

## §2 Contrato del módulo

Archivo nuevo: `fragmentador.py`, al mismo nivel que `contrato.py` e `indice.py`.

### §2.1 `ConfigFragmentacion` — dataclass frozen

Todos los parámetros del algoritmo viven aquí. **Nada de constantes mágicas
sueltas en el cuerpo de las funciones**: el informe técnico tiene que poder
citar la configuración exacta, y la fase de evaluación tiene que poder barrer
variantes sin editar código.

| Campo | Tipo | Defecto | Nota |
|---|---|---|---|
| `objetivo_palabras` | `int` | 190 | tamaño al que apunta el empaquetado |
| `max_palabras` | `int` | 240 | tope duro; margen de 10 sobre el límite de 250 del enunciado |
| `max_tokens` | `int` | 450 | tope duro; margen sobre el límite típico de 512 del encoder |
| `min_palabras` | `int` | 40 | por debajo, se fusiona hacia adelante (§4.4) |
| `oraciones_solape` | `int` | 1 | 0 desactiva el solape |
| `nivel_frontera` | `int` | 6 | encabezados de nivel ≤ N abren sección nueva |
| `respetar_atomicos` | `bool` | `True` | ver §5 |

Los dos topes son **simultáneos**: manda el que se alcance primero.

### §2.2 `Fragmento` — dataclass frozen

Campos obligatorios de la Tabla 1: `doc_id`, `chunk_id`, `fuente`, `formato`,
`fenomeno`, `posicion`, `num_tokens`, `texto`.

Campos adicionales (permitidos por §3.4 del enunciado, y necesarios para
post-filtros y para el informe):

| Campo | Tipo | Para qué |
|---|---|---|
| `texto_enriquecido` | `str` | lo que se codifica (§6.3). **No se serializa a `metadata.jsonl`.** |
| `idioma` | `str` | post-filtro por idioma (§8.7) |
| `observatorio` | `str \| None` | post-filtro y enriquecimiento |
| `ruta_relativa` | `str` | trazabilidad |
| `seccion` | `list[str]` | breadcrumb heredado del `Bloque` |
| `pagina` | `int \| None` | página del primer bloque aportante |
| `num_palabras` | `int` | verificación del límite de 250 |
| `tipo_unidad` | `str` | `"prosa"`, `"atomico"` o `"titulo_huerfano"` (§5) |
| `tiene_solape` | `bool` | si arrastra oraciones del fragmento anterior |
| `n_oraciones` | `int` | diagnóstico de calidad |

`chunk_id` = `f"{doc_id}-c{posicion:04d}"`. Único dentro del documento; `posicion`
empieza en 0 y es contigua sin huecos.

### §2.3 Funciones públicas

```python
def fragmentar(documento: Documento, config: ConfigFragmentacion) -> list[Fragmento]: ...
def validar_fragmento(frag: Fragmento, config: ConfigFragmentacion) -> list[str]: ...
def fragmentar_corpus(entrada: Path, salida: Path, config: ConfigFragmentacion) -> ReporteFragmentacion: ...
```

`validar_fragmento` sigue el mismo patrón que `contrato.validar_documento`:
devuelve una lista de violaciones en texto, vacía si está limpio. **No lanza.**

`ReporteFragmentacion` (frozen): `n_documentos`, `n_fragmentos`,
`documentos_sin_bloques: list[str]`, `fragmentos_por_formato: dict[str, int]`,
`histograma_palabras: dict[str, int]` (bins de 25), `n_oraciones_unicas`
(fragmentos de una sola oración), `n_atomicos`, `n_huerfanos_fusionados`.

---

## §3 Segmentación de oraciones — el componente crítico

§3.3 del enunciado convierte la frontera oracional en el átomo de todo el
sistema: **ninguna oración puede cruzar de un fragmento a otro**. Si el
segmentador falla, todos los fragmentos afectados violan el requisito
obligatorio. Es el mayor riesgo técnico de esta capa.

### §3.1 Herramienta

Usar **`pysbd`** (Pragmatic Sentence Boundary Disambiguation): basado en reglas,
determinista, sin descarga de modelos, con soporte nativo de español, inglés y
portugués — los tres idiomas del corpus. Verificar la licencia en el repositorio
del proyecto antes de fijarla en `requirements.txt`; si no es Apache 2.0 / MIT /
BSD, escalar antes de adoptarla.

**Prohibido** usar segmentadores que requieran descarga de modelos en tiempo de
ejecución (nltk punkt) o que dependan de un modelo estadístico de spaCy: rompen
la reproducibilidad de `generador.py`, que el jurado tiene que poder ejecutar.
Si `pysbd` se descarta, la alternativa es el `sentencizer` de spaCy (reglas
puras, sin modelo) o un segmentador propio con lista de abreviaturas por idioma.

El segmentador se selecciona por el `idioma` del `Documento`, con fallback a
español.

### §3.2 Conjunto dorado de pruebas

**Obligatorio antes de dar por buena la implementación.** Construir
`fixtures/oraciones_doradas.jsonl`: mínimo **60 casos** etiquetados a mano, 20
por idioma, con al menos estos venenos representados:

- Abreviaturas: `Art.`, `núm.`, `Dr.`, `Sr.`, `etc.`, `cf.`, `vs.`, `Fig.`,
  `pág.`, `EE.UU.`, `U.S.`, `Ph.D.`
- Decimales y porcentajes: `un aumento de 3.4 % en 2024`
- Siglas con puntos: `N.A.S.A.`, `O.N.U.`
- Citas y referencias: `(Smith et al., 2023)`, `[12]`
- Listas sin punto final, un ítem por línea
- Comillas y paréntesis que envuelven el punto: `«... final.» Siguiente`
- Elipsis: `...`
- Encabezados sin puntuación terminal

Un test parametrizado sobre este archivo. Si el segmentador falla un caso, se
documenta como excepción conocida en el propio JSONL, no se borra el caso.

### §3.3 Oraciones que exceden el tope por sí solas

Una oración de más de `max_palabras` no se puede partir sin violar §3.3.
Comportamiento: **emitirla como fragmento propio** y registrarla en
`validar_fragmento` como violación de tamaño con motivo `"oración indivisible"`.
No se trunca, no se parte, no se descarta. El reporte cuenta cuántas hay; si son
más de un puñado, casi seguro el segmentador está fallando y hay que revisarlo.

---

## §4 Algoritmo de fragmentación — cascada de tres capas

Es una cascada, no dos pasadas independientes. La estructura define **fronteras
que no se cruzan**; el empaquetado por oraciones hace el trabajo real dentro de
cada frontera.

### §4.1 Capa 1 — Agrupar en secciones

Recorrer `documento.bloques` en orden y abrir sección nueva cuando:

- aparece un `Bloque` con `tipo == "titulo"` y `nivel <= config.nivel_frontera`, o
- cambia el `ruta` (breadcrumb) respecto al bloque anterior, o
- aparece un bloque con `atomico == True` (que va por su propio camino, §5).

**La página NO es frontera.** En PDF los párrafos fluyen de una página a la
siguiente; cortar por página partiría ideas por la mitad. El `pagina` del
fragmento es el del primer bloque que aporta texto.

**Realidad del corpus:** los 954 artículos JSON tienen cuerpo plano y pocos o
ningún encabezado. Ahí esta capa produce una sola sección por documento y todo
el trabajo lo hace la Capa 2. Es el comportamiento correcto, no un fallo.

### §4.2 Capa 2 — Empaquetar oraciones dentro de la sección

Segmentar el texto de la sección en oraciones (§3) y empaquetarlas de forma
codiciosa: añadir oraciones al fragmento en curso mientras
`palabras <= objetivo_palabras`. Cerrar el fragmento cuando la siguiente oración
lo llevaría por encima de `max_palabras` **o** de `max_tokens`.

Al final de la sección se cierra el fragmento en curso aunque quede por debajo
del objetivo (salvo el caso de fusión del §4.4).

### §4.3 Capa 3 — Solape

Si `oraciones_solape > 0` y la sección produjo más de un fragmento, cada
fragmento a partir del segundo arranca repitiendo las últimas
`oraciones_solape` oraciones del anterior, y se marca `tiene_solape=True`.

El solape **nunca cruza fronteras de sección ni de documento**, y nunca hace que
el fragmento supere `max_palabras`: si no cabe, se omite el solape en ese
fragmento.

**Advertencia para el informe.** El solape tiene un costo directo sobre
NDCG@10: dos fragmentos que comparten la oración relevante compiten por los
mismos puestos del top-10, y gastar dos de diez cupos en contenido casi idéntico
no aporta ganancia acumulada. Por eso el defecto es 1 oración, no una ventana
generosa, y por eso `oraciones_solape=0` tiene que ser una variante evaluada de
verdad (§8.3), no una opción teórica.

### §4.4 Huérfanos

Un fragmento con menos de `min_palabras` se fusiona **hacia adelante** con el
siguiente de la misma sección, siempre que el resultado no exceda
`max_palabras`. Si es el último de la sección, se fusiona hacia atrás. Si es el
único de la sección, se emite tal cual y se marca `tipo_unidad="titulo_huerfano"`
cuando su único contenido es un encabezado.

Un encabezado suelto sin cuerpo detrás es basura semántica en el índice: un
vector que solo dice "Metodología" contamina el ranking de cualquier consulta
sobre metodología. Fusionarlo con el cuerpo que le sigue es lo correcto; emitirlo
solo, la excepción.

---

## §5 Unidades atómicas — el tercer camino

Los `Bloque` con `atomico == True` (filas de CSV/XLSX, elementos de PBF: ~103
archivos del corpus) **no se parten y no se fusionan con vecinos** cuando
`config.respetar_atomicos` es `True`.

Cada bloque atómico produce exactamente un `Fragmento` con
`tipo_unidad="atomico"`. Una fila de un dataset es una unidad de significado
completa (`columna: valor` por todo el registro); fusionar dos filas produce un
vector que no representa ninguna de las dos, y partir una rompe la
correspondencia columna–valor.

**Excepción por tamaño:** si un bloque atómico supera `max_palabras`, se parte
en límites oracionales como cualquier otro y se registra la violación. La
alternativa —emitir un fragmento de 900 palabras— viola el límite de 250 del
enunciado, que no es negociable.

**Excepción por pequeñez:** filas muy cortas (una celda con un número) producen
fragmentos inútiles. Si un bloque atómico tiene menos de `min_palabras`, se
agrupa con los bloques atómicos **inmediatamente contiguos del mismo documento**
hasta alcanzar `objetivo_palabras`, conservando el separador de registro entre
ellos. Esto es agrupación de registros completos, no partición: ninguna fila
queda cortada.

---

## §6 Metadata y enriquecimiento

### §6.1 Herencia desde `Documento`

`doc_id`, `fuente`, `formato`, `fenomeno`, `idioma` se copian sin modificación.
`observatorio` y `ruta_relativa` salen de `documento.meta`.

### §6.2 Conteo de tokens

`num_tokens` **debe** contarse con el tokenizador del encoder que se vaya a
usar, no con un conteo de palabras. La función de conteo se inyecta:

```python
ConfigFragmentacion.contar_tokens: Callable[[str], int]
```

**Mientras no haya encoder elegido**, el defecto es una estimación conservadora
`ceil(n_palabras * 1.6)` y `max_tokens` se fija en 450 para absorber el error.
En cuanto se elija el encoder, se cambia por su `AutoTokenizer` y **se
re-fragmenta el corpus completo**: los `num_tokens` estimados no son válidos
para la entrega.

> **Corregido el 5 ago 2026: la estimación no era conservadora.** Medida contra
> el tokenizador de granite, la mediana real del corpus es de 1,77 tokens por
> palabra —3,50 en tiles vectoriales y 2,81 en datos tabulares— y el 8,2 % de
> los fragmentos excedía el tope de 450. Ver
> `docs/decisiones/conteo-de-tokens.md`.

Esto es una dependencia real hacia atrás. Conviene cerrar la elección del
encoder antes de dar por terminada esta capa, porque un modelo de contexto largo
(tipo BGE-m3, 8192 tokens) y uno de 512 admiten diseños distintos. Con todo, el
tope de 250 palabras del enunciado sigue mandando en ambos casos, así que el
diseño no cambia de forma — solo el margen.

### §6.3 Texto enriquecido

`texto` es el original sin tocar (§1.3). Aparte, `texto_enriquecido` es lo que
se pasa al encoder:

```
[observatorio] · [título del documento] · [breadcrumb de secciones]
<texto original>
```

Los campos ausentes se omiten sin dejar separadores huérfanos. Un fragmento de
la página 40 de un informe que dice "el crecimiento fue del 12%" no es
recuperable sin saber de qué informe y de qué sección viene; el prefijo le
devuelve ese contexto al vector sin alterar lo que se reporta al jurado.

Es legal: no interviene ningún decoder, es concatenación de metadata existente.

---

## §7 Pruebas exigidas

Todas contra `Documento` armados a mano — no hay extractores. Añadir un
constructor de fixtures en `tests/conftest.py`:
`documento_con_bloques(*bloques, **kwargs) -> Documento`.

| # | Prueba | Qué asegura |
|---|---|---|
| 7.1 | Ningún fragmento de ningún fixture viola `validar_fragmento` | contrato limpio |
| 7.2 | Ningún fragmento excede 250 palabras salvo el caso de oración indivisible | §9.2.1 |
| 7.3 | Reconstruir concatenando fragmentos sin solape devuelve el texto de origen | no se pierde ni se duplica contenido |
| 7.4 | Ninguna oración del texto original queda partida entre dos fragmentos | §3.3, el requisito obligatorio |
| 7.5 | Dos corridas producen `fragmentos.jsonl` byte a byte idéntico | determinismo |
| 7.6 | Un documento con `bloques=[]` produce cero fragmentos sin lanzar | robustez |
| 7.7 | `posicion` es contigua desde 0 y `chunk_id` único dentro del documento | trazabilidad |
| 7.8 | Un bloque atómico no se fusiona con un bloque de prosa vecino | §5 |
| 7.9 | Un encabezado suelto se fusiona con el cuerpo que le sigue | §4.4 |
| 7.10 | Con `oraciones_solape=1`, el fragmento N+1 empieza con la última oración del N | §4.3 |
| 7.11 | Con `oraciones_solape=0` no hay ninguna oración repetida entre fragmentos consecutivos | §4.3 |
| 7.12 | El solape no cruza frontera de sección | §4.3 |
| 7.13 | Una oración de 400 palabras sale como fragmento propio, no truncada | §3.3 |
| 7.14 | `texto` conserva el original; el prefijo solo vive en `texto_enriquecido` | Tabla 1 |
| 7.15 | Conjunto dorado de oraciones, parametrizado (§3.2) | segmentación |
| 7.16 | Un documento en portugués y uno en inglés se segmentan con su propio segmentador | multilingüe |

---

## §8 Criterios de aceptación

### §8.1 Sobre fixtures sintéticos

- Las 135 pruebas actuales siguen en verde.
- Las 16 pruebas del §7 en verde.
- `validar_fragmento` limpio para el 100% de los fragmentos de todos los fixtures.

### §8.2 Sobre corpus real — **diferido hasta que exista el extractor de JSON**

Cuando haya al menos un extractor implementado, correr `fragmentar_corpus`
sobre `extraidos/` y comprobar:

- La corrida termina sin excepción.
- Cero fragmentos por encima de 250 palabras (excepto oraciones indivisibles,
  que deben ser **menos del 0.5%** del total).
- Cero fragmentos con `texto` vacío o solo espacios.
- Cero oraciones partidas, comprobado sobre una muestra aleatoria de 200
  fragmentos con semilla fija.
- Dos corridas → diff vacío.
- Histograma de palabras publicado en el reporte: la mediana debería caer entre
  150 y 220 palabras. Una mediana muy por debajo indica que el empaquetado se
  está cortando en falso.

### §8.3 Barrido de configuraciones

Producir una tabla comparativa de **al menos cuatro** configuraciones, variando
`objetivo_palabras` (120 / 190 / 240) y `oraciones_solape` (0 / 1), con: número
total de fragmentos, mediana y p95 de palabras, porcentaje de fragmentos de una
sola oración, porcentaje de huérfanos fusionados.

Esta tabla **no elige** la configuración: elegirla requiere medir NDCG@10 y
F1@3 contra el conjunto interno de ground truth, que aún no existe. La tabla es
el insumo para esa decisión y material directo para el informe técnico.

---

## §9 Lo que este spec NO decide

Explícitamente fuera de alcance, para que el agente no se adelante:

- **Qué encoder usar.** Solo se exige que el conteo de tokens sea inyectable.
- **Qué configuración final se entrega.** Sale de la evaluación, no del diseño.
- **La división a 250 palabras al construir `resultados.jsonl`.** Si el diseño
  de §2.1 funciona, no hará falta; si hace falta, es problema de la capa de
  salida, no de esta.
- **El grafo de conocimiento.** Consume fragmentos, no los produce.

---

## §10 Justificación para el informe técnico

Redactada para levantarla casi tal cual. Ajustar cifras tras el barrido de §8.3.

**Estrategia: híbrida estructural-oracional con unidades atómicas preservadas.**

Se descartaron las estrategias puras por razones que el propio corpus impone.

*Tamaño fijo de tokens* queda excluida de entrada: §3.3 prohíbe fragmentos con
oraciones incompletas, y un corte cada n tokens parte oraciones por definición.

*Por oración* produce fragmentos lingüísticamente completos pero demasiado
cortos. Un vector construido sobre una sola oración captura poco contexto y, con
1826 documentos, multiplica el índice sin ganancia proporcional en precisión.

*Por sección* es la más cercana a la intención del autor, pero el corpus no la
sostiene: 954 de los 1826 documentos (52%) son artículos en JSON con cuerpo
plano y sin jerarquía de encabezados. Una estrategia puramente estructural
produciría ahí un solo fragmento por documento, muy por encima del límite de
250 palabras.

La estrategia adoptada usa la estructura como **frontera** y las oraciones como
**átomo**. Se agrupa el documento en secciones a partir de sus encabezados y
breadcrumbs; dentro de cada sección se empaquetan oraciones completas de forma
codiciosa hasta un objetivo de ~190 palabras, con tope duro de 240; los cortes
caen siempre en frontera oracional. Cuando el documento no tiene estructura, la
capa estructural es transparente y el empaquetado oracional actúa solo, que es
exactamente el comportamiento deseable.

**Por qué el tamaño se fijó por debajo de 250 palabras.** El límite de §9.2.1 no
es solo una restricción de formato: un fragmento indexado que lo supere debe
partirse al construir la respuesta, y cada sub-fragmento resultante ocupa **su
propio puesto** en la lista de diez que se evalúa con NDCG@10. Un chunk de 500
palabras consume así dos de los diez cupos con pedazos del mismo contenido, en
lugar de aportar dos evidencias distintas. Fijar el tope de indexación por
debajo de 250 mantiene la correspondencia uno a uno entre `chunk_id` y texto
reportado, preserva la trazabilidad y no regala cupos de ranking.

**Unidades atómicas.** Los datasets tabulares y los mapas vectoriales (~103
archivos) se fragmentan por registro completo: cada fila conserva sus pares
`columna: valor` íntegros. Partir un registro rompe la correspondencia entre
columna y valor; fusionar dos produce un vector que no representa fielmente a
ninguno. Los registros muy cortos se agrupan con sus contiguos hasta alcanzar un
tamaño útil, sin partir ninguno.

**Solape.** Se aplica un solape de una oración entre fragmentos consecutivos de
la misma sección, para que una idea situada justo en la frontera no quede
descontextualizada en ninguno de los dos. Se mantuvo deliberadamente mínimo: un
solape amplio genera fragmentos casi idénticos que compiten por los mismos
puestos del top-10, gastando cupos de evaluación en contenido redundante.

**Enriquecimiento en indexación.** El texto que se codifica lleva un prefijo con
el observatorio de origen, el título del documento y el breadcrumb de secciones;
el campo `texto` que se reporta conserva el original sin modificación. Un
fragmento aislado a mitad de un informe carece de las referencias que lo hacen
recuperable, y el prefijo se las devuelve al vector sin alterar lo que se
entrega. La operación es concatenación de metadata ya existente: no interviene
ningún modelo generativo, conforme a §4.2 y §8.3.
