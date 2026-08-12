# Decisión — Enriquecimiento de contexto antes de codificar

CODEFEST Ad Astra 2026 · Etapa 1 · 5 ago 2026
Estado: **implementada y verificada** sobre el corpus completo. Las medidas de §4
son de la corrida de 140 686 fragmentos (estimación de tokens); la corrida
vigente son 134 317 fragmentos con el tokenizador real y la cobertura del
prefijo no cambia, porque depende de la metadata del documento y no del tamaño
del fragmento. La ablación de §6 sigue sin hacer, pero ya no está bloqueada: el
ground truth existe (`auxiliar/ground/ground_truth.json`).

---

## 1. La decisión

Lo que entra al encoder no es el texto del fragmento, sino el texto **precedido
de su contexto documental**:

```
{observatorio} · {título del documento} · {sección > subsección > ...}
{texto del fragmento}
```

- Las tres partes se unen con `·` (`separador_contexto`) y los niveles de
  sección con `>` (`separador_breadcrumb`).
- El prefijo va en su propia línea; el texto del fragmento queda intacto
  detrás, byte a byte.
- Las partes vacías se omiten sin dejar separadores sueltos: un documento sin
  título no produce `Observatorio ·  · Sección`.
- Vive en un campo aparte, `texto_enriquecido`. **`texto` no se toca nunca**, y
  `texto_enriquecido` **no se serializa a `metadata.jsonl`**: lo que se le
  reporta al jurado es el texto original del documento.

Implementación: `_prefijo_de_contexto` y `_construir_fragmento` en
`fragmentador.py`. El uso está en `generador.py`, que codifica
`texto_enriquecido` y escribe la metadata sin él.

## 2. Por qué es legal

§4.2 del enunciado prohíbe los modelos de arquitectura decoder en la
construcción del índice y en la recuperación. Esta técnica **no usa ningún
modelo**: es la concatenación de tres campos de metadata que ya venían en el
índice maestro de ADL y en la estructura del propio documento. No hay
generación, no hay reescritura, no hay resumen. La distinción importa porque la
variante popular de esta idea —*contextual retrieval*, donde un LLM redacta un
párrafo de contexto para cada chunk— **sí** usaría un decoder y sería motivo de
descalificación. Se descarta explícitamente por eso, no por coste.

## 3. Por qué se hace

Un fragmento de la página 40 de un informe que dice *"el crecimiento fue del
12 %"* no es recuperable: no contiene ninguna de las palabras por las que
alguien lo buscaría —ni el observatorio, ni el tema, ni el país—. El chunking
destruye ese contexto por construcción, y el vector resultante queda a la misma
distancia de una consulta sobre presupuesto militar que de una sobre lanzamientos
orbitales.

El prefijo devuelve ese contexto **al vector**, no al texto reportado. En este
corpus el efecto es grande porque los documentos son informes largos y
temáticamente homogéneos: el observatorio y la sección son, con frecuencia, la
única señal que distingue dos fragmentos numéricos casi idénticos de dos
informes distintos.

## 4. Cobertura y coste, medidos

Sobre la salida real del fragmentador (`chunks/chunks.jsonl`,
140 686 fragmentos de 1 826 documentos):

| Medida | Valor |
|---|---|
| Fragmentos con prefijo | 140 686 (**100 %**) |
| Fragmentos con observatorio | 140 686 (100 %) |
| Fragmentos con breadcrumb de secciones | 104 984 (**74,6 %**) |
| Longitud media del prefijo | 11,8 palabras · 14,4 tokens |
| p95 | 30 palabras · 35 tokens |
| Máximo | 70 palabras · 271 tokens |

El coste es de ~14 tokens por fragmento, un 5,8 % del tamaño mediano. No es
gratis, y por eso el presupuesto de tokens del empaquetado **descuenta el
prefijo antes de llenar el fragmento** (`_presupuesto_de_tokens` en
`fragmentador.py`): sin ese descuento, un fragmento al borde del tope
entraría al encoder por encima de su límite y se truncaría en silencio, que es
la trampa §14.3 del addendum del encoder.

El 25,4 % sin breadcrumb son, casi en su totalidad, formatos sin estructura de
encabezados —filas de CSV, elementos de tiles vectoriales, registros JSON—. Ahí
el prefijo se reduce a observatorio y título, que es exactamente el contexto que
esos registros necesitan: una fila `país: Colombia; gasto: 3,2 %` no dice de qué
dataset viene.

## 5. Qué se descartó

| Alternativa | Por qué no |
|---|---|
| Reescribir `texto` con el prefijo incluido | Falsea lo que se le reporta al jurado: el campo `texto` debe ser el del documento original. Además rompería el emparejamiento con el ground truth. |
| Contexto redactado por un LLM (*contextual retrieval*) | Interviene un decoder → §4.2, riesgo de descalificación. |
| Repetir el prefijo en cada oración del fragmento | Diluye el texto real bajo metadata repetida y consume presupuesto de tokens sin añadir información nueva. |
| Indexar el prefijo como campo separado y fusionar puntuaciones | Duplica el índice y añade un peso que habría que ajustar sin ground truth para hacerlo. Se puede reconsiderar si la ablación de §6 lo justifica. |
| Prefijos tipo `passage:` del encoder | No los pide granite (su tarjeta no documenta ninguno). Van en la config del encoder, no aquí: `ConfigEncoder.prefijo_fragmento`. |

## 6. Riesgo asumido y cómo se mide

El riesgo real es la **dilución**: 14 tokens de metadata idéntica en los ~2 400
fragmentos de un mismo informe acercan entre sí a todos esos vectores, y eso
puede empeorar la precisión dentro de un documento aunque mejore la
recuperación entre documentos. Con p95 de 35 tokens sobre un fragmento mediano
de 247, la proporción es baja, pero no es cero.

La decisión de mantenerlo es **provisional hasta la ablación**. El ground truth
interno que la bloqueaba ya existe —50 consultas etiquetadas, cinco fragmentos
por consulta, `auxiliar/ground/ground_truth.json`—, así que la medición está pendiente de
lanzar y no de construir: NDCG@10 y F1@3 con y sin prefijo, misma configuración
de chunking y mismo encoder. Es una re-corrida del generador sobre el mismo `chunks.jsonl`,
porque el campo `texto` no cambia: basta codificar `texto` en vez de
`texto_enriquecido`. Si el prefijo no aporta, se quita y se documenta.

## 7. Verificación en código

Se referencian por nombre y no por número de línea: el número caduca en el
primer refactor y manda a leer otra cosa.

| Qué se garantiza | Prueba |
|---|---|
| El prefijo nunca altera el texto reportado | `test_el_texto_conserva_el_original_y_el_prefijo_vive_aparte` |
| Sin metadata de contexto, `texto_enriquecido == texto` | `test_sin_metadata_de_contexto_el_texto_enriquecido_es_el_texto` |
| `texto_enriquecido` termina siempre en `texto` (invariante del validador) | `fragmentador._violaciones_de_texto` |
| Lo que se codifica es `texto_enriquecido`, no `texto` | `test_se_codifica_el_texto_enriquecido_y_no_el_texto` |
| `metadata.jsonl` no lleva `texto_enriquecido` | `test_la_metadata_no_lleva_el_texto_enriquecido` |
| El prefijo del encoder (si el modelo lo pidiera) se aplica al codificar, no al fragmentar | `test_el_prefijo_del_encoder_se_aplica_al_codificar` |

## 8. Párrafo para el informe técnico

> Antes de la codificación, cada fragmento se enriquece con su contexto
> documental: el observatorio de origen, el título del documento y la ruta de
> encabezados de la sección a la que pertenece, antepuestos al texto en una
> línea separada. La técnica no interviene ningún modelo generativo —es la
> concatenación de metadata estructural ya presente en el índice maestro y en la
> jerarquía del documento—, por lo que es compatible con la restricción de §4.2
> sobre arquitecturas decoder; se descartó explícitamente la variante de
> *contextual retrieval* basada en LLM por incumplirla. El enriquecimiento se
> aplica sobre el vector y no sobre el dato reportado: el campo `texto` que
> acompaña a cada resultado conserva el contenido original del documento. La
> cobertura sobre el corpus es del 100 % para observatorio y título y del 74,6 %
> para la ruta de secciones, con un coste mediano de 14 tokens por fragmento
> (5,8 % de su longitud), descontado del presupuesto de empaquetado para evitar
> truncamiento en el encoder. Su aporte se cuantificará por ablación contra el
> conjunto interno de consultas etiquetadas, midiendo NDCG@10 y F1@3.
