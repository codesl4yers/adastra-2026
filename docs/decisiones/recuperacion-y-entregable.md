# Decisión — Codificación, recuperación y entregable

CODEFEST Ad Astra 2026 · Etapa 1 · 11 ago 2026
Estado: **implementado y corrido**. Índice de 134 317 vectores y
`resultados.jsonl` con las 50 consultas de ADL, top-3 completo en todas.

Recoge las decisiones de `encoder.py` y `generador.py`, que hasta ahora vivían
en sus docstrings. La elección del modelo está en
`docs/specs/spec-encoder-addendum.md`; qué se codifica, en
`enriquecimiento-de-contexto.md`.

---

## 1. Lo que no puede fallar en silencio

Las cuatro trampas de §14 del addendum son fallos que no dan error: el pipeline
corre, los vectores salen, el NDCG cae y nadie sabe por qué. Cada una tiene su
comprobación explícita y su rastro en `reporte_indice.json`.

| Trampa | Qué se hace |
|---|---|
| Arquitectura decoder (§4.2, descalificación) | `verificar_arquitectura` lee el `model_type` del `config.json` **antes** de cargar los pesos y lanza si no es de una familia encoder-only conocida |
| Truncamiento silencioso | se cuentan los fragmentos por encima de la ventana; `n_truncados` debe ser 0 y el CLI sale con código 1 si no lo es |
| Normalización | se normaliza siempre y de forma explícita, aunque el modelo ya lo haga en su capa de salida; `norma_min`/`norma_max` quedan escritas |
| No determinismo | `model.eval()`, `torch.manual_seed`, float32 |

**float32 y no float16** a propósito: la media precisión introduce variación
entre GPU y rompe la reproducibilidad que §1.4 exige. Un índice que no se puede
reproducir no se puede auditar.

**El encoder devuelve los vectores sin normalizar** y normaliza el generador. Que
el modelo ya normalice es un detalle del checkpoint, y el índice no puede
depender de un detalle que un cambio de versión quita sin avisar.

**Norma cero es un error, no un caso borde**: dividir daría `NaN`, FAISS lo
aceptaría sin quejarse y ese vector devolvería vecinos arbitrarios en cada
consulta.

## 2. Memoria de la GPU: el lote no es lo que parece

ModernBERT materializa una máscara de atención de `(lote, 1, L, L)` en float32
—dos: global y sliding window— con `L` la longitud del texto **más largo del
lote**. El coste no lo fija el número de textos sino el cuadrado del más largo
multiplicado por el lote entero.

Por eso hay dos topes simultáneos y el que manda es el segundo:

- `lote` (4 por defecto), textos por pasada.
- `presupuesto_atencion` (128 M elementos ≈ 1 GB de máscaras), tope de
  `lote × longitud²`. Con él, los lotes se encogen cuando aparece un fragmento
  largo y el más grande de todos viaja solo.

**Con poca VRAM, lote pequeño es más rápido.** Medido sobre una muestra
sistemática del corpus en una RTX 4050 de 6 GB: 26,2 frag/s con lote 2 frente a
9,6 con lote 32. Lo que se paga es el padding al texto más largo del lote, y con
poca memoria el cuello es la memoria y no el cómputo. En una GPU con más VRAM el
óptimo será mayor: hay que medirlo, no suponerlo. (Sobre lotes **homogéneos** de
texto bibliográfico la conclusión se invierte; ver
`campos-indexables-tabulares.md`.)

**Respaldo en CPU**: si aun así un lote no cabe, se codifica en CPU con un aviso
por stderr en vez de perder una corrida de horas. El modelo entero baja a CPU y
vuelve, porque `device=` por sí solo no mueve los pesos. Se reconoce la falta de
memoria por el mensaje del `RuntimeError` y no importando `torch`, para que
`encoder.py` siga siendo importable sin torch —que es lo que permite que el
fragmentador dependa de él solo para contar tokens—. Cualquier otro error se
propaga: reintentar en CPU algo que falló por otro motivo devolvería vectores de
una operación rota.

Con el corpus re-fragmentado el caso extremo dejó de darse (máximo 3707 tokens),
pero el mecanismo se mantiene: el coste sigue siendo cuadrático.

## 3. Texto repetido: se codifica una vez, se inserta dos

El corpus trae lit-covid dos veces —`F1-AIINDEX-041` en CSV y `F1-AIINDEX-042`
en XLSX, las mismas 8866 filas—. Cada `fuente` conserva su fila en el índice
porque omitir una garantiza perder ese acierto (§10.2.1 empareja por `fuente`),
pero el pase del encoder no se repite: **12 132 vectores reutilizados**, el 9 %
del índice, anotados en `n_reutilizados`.

Solo se cachean los textos que aparecen más de una vez, detectados en una pasada
previa. Cachear todo costaría un vector por fragmento en RAM —más de un giga en
el corpus completo— para no reutilizar ninguno.

## 4. El orden es el contrato

La fila `i` del índice es la línea `i` de `metadata.jsonl`. No hay nada más que
relacione un vector con su documento, así que:

- los lotes salen en el orden de entrada, sin excepción;
- los fragmentos se leen enteros a memoria antes de empezar, para escribir la
  metadata en el mismo orden en que se indexó sin fiarse de que dos recorridos
  coincidan;
- al responder se comprueba que `ntotal` y el número de líneas cuadran, y si no,
  se para: todo lo que saliera a continuación sería metadata de otro fragmento,
  y con muy buena pinta.

`metadata.jsonl` **no lleva `texto_enriquecido`**: el prefijo de contexto es una
decisión del indexador, no un dato del corpus, y devolverlo como si fuera el
texto del fragmento falsearía lo que se reporta.

Para responder no se carga la metadata en memoria: 200 MB de JSON serían del
orden de un giga de dicts para leer cincuenta líneas por consulta. Se indexan los
desplazamientos en bytes de cada línea —140k enteros— y se lee solo lo que el
índice devuelve.

## 5. De fragmentos a documentos

**Se piden 50 fragmentos para entregar 3 documentos.** El top-k viene en
fragmentos y varios caen en el mismo `doc_id`; pedir 3 puede dar un solo
documento. Si aun así una consulta no llega a tres documentos distintos, el CLI
la nombra por stderr en vez de entregar un top-3 corto en silencio.

**El score de un documento es el de su mejor fragmento, no la suma.** Sumar
corona al documento largo por ser largo —más fragmentos, más ocasiones de rozar
la consulta— y lo que se evalúa es si el documento responde. `n_fragmentos` viaja
en la salida como diagnóstico, no como criterio.

**Los empates se rompen por `doc_id`.** No es cosmético: dos documentos con el
mismo score tienen que salir siempre en el mismo orden o la corrida deja de ser
reproducible (§1.4).

**El descarte de texto repetido es dentro de un mismo documento, nunca entre
documentos.** La distinción decide un acierto: lit-covid está dos veces con
vectores idénticos, y deduplicar solo por texto le quita a uno de los dos el
único vector con el que podía llegar al top-3, cuando el jurado los cuenta como
documentos distintos y probablemente los dos estén en el ground truth de una
consulta sobre lit-covid. Dentro de un mismo documento sí es ruido: el repetido
no cambia su score y solo ocupa un puesto del top-k que otro documento podría
usar. En la corrida de 50 consultas se descartaron 17; deduplicando también entre
documentos habrían sido 113, y esos 96 de diferencia son oportunidades de acierto
tiradas.

**El post-filtro por idioma está apagado.** Las consultas vienen en español y el
grueso del corpus está en inglés: filtrar a `es` no afina la respuesta, la vacía.
El encoder es multilingüe justamente para no necesitarlo. Queda expuesto porque
§8.7 lo pide como capacidad. En la corrida contra el corpus completo las 50
consultas en español recuperan documentos en inglés con scores de 0,870 a 0,946.

## 6. Las consultas, vengan como vengan

Se acepta el PDF de ADL tal cual, un JSONL o un texto con una consulta por línea.
El formato del día de la prueba no está prometido, y quedarse esperando al que
sea es perder la corrida entera por un parser.

El identificador (`q001`…) **es de ADL y se lee, no se inventa**: el entregable se
casa con el suyo. Las consultas del PDF vienen partidas en varias líneas, así que
el corte lo marca el identificador siguiente y no el salto de línea. Un
identificador duplicado detiene la corrida: `resultados.jsonl` saldría con dos
líneas para la misma consulta y sin saber cuál vale.

## 7. El entregable

`registro_de_resultado()` es **el único sitio que hay que tocar si ADL fija otros
nombres de campo** para la Tabla 2; todo lo demás trabaja con objetos, no con el
JSON. Hoy se escribe `query_id`, `consulta` y `documentos[]`.

Cada documento va acompañado del fragmento que lo metió en el top-3: es la
evidencia de por qué está ahí, y sin ella el jurado tiene un `doc_id` y nada con
que comprobarlo.

## 8. El ground truth interno

`ground/ground_truth.json`: 50 consultas etiquetadas a mano —las mismas de ADL,
`q001`–`q050`— con cinco fragmentos relevantes cada una ordenados por `rank`. Son
250 referencias sobre 234 `chunk_id` distintos y 132 archivos de origen, todos
`pdf` (221) y `json` (29).

Cómo se construyó, según `ground/ground_truth_metodologia.pdf`: un índice BM25
disperso sobre el corpus completo, expansión bilingüe de cada consulta escrita a
mano —el corpus es 75 % inglés y las preguntas están en español—, filtrado
automático de índices, bibliografías y filas tabulares, y selección final por
juicio humano sobre el pool recuperado. Los `chunk_id` se copian del corpus, no
se inventan: los 234 resuelven contra el `metadata.jsonl` vigente (comprobado,
234 de 234).

Tres límites al interpretar cualquier métrica que salga de aquí:

- **No es exhaustivo.** Marca cinco fragmentos por consulta, no todos los
  relevantes: un acierto fuera de la lista no es necesariamente un fallo.
- **14 consultas llevan salvedades** en `notes`, y en tres de ellas el supuesto
  de la pregunta no existe en el corpus (q031, q046, q047).
- **Amazon Underworld no aparece**, pese a aportar ~11.000 fragmentos al
  fenómeno 3: son tiles vectoriales y CSV geoespaciales, sin prosa que pueda
  sustentar una respuesta.

## 9. La verificación de piso

`scripts/verificar_cobertura.py` es la única comprobación que detecta la forma
garantizada de perder F1@3: un documento sin un solo vector no puede aparecer en
el top-3 de ninguna consulta. No es que recupere mal, es que es imposible que
recupere, y nada más en el pipeline avisa —un extractor que falla en silencio
produce un `Documento` válido con cero bloques, el fragmentador produce cero
fragmentos y el generador no echa de menos lo que nunca llegó—.

Empareja por `doc_id` y no por `fuente` porque 59 nombres de archivo se repiten:
con el nombre, un documento cubierto taparía el hueco de otro homónimo. Sobre el
índice actual salen 8 huecos, los 8 conocidos.
