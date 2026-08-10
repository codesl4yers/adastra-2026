# Addendum al spec — Selección de encoder

CODEFEST Ad Astra 2026 · Etapa 1 · v1.0 · 4 ago 2026
Complementa `spec-fragmentador.md` §6.2 y cierra lo que su §9 dejaba abierto.

---

## §11 El filtro que manda: arquitectura, no benchmark

§4.2 del enunciado clasifica los transformers en dos familias y dice, sobre los
decoders: *"su uso está explícitamente prohibido en las etapas de construcción
del índice y de recuperación de esta Etapa 1"*. §8.3 lo repite para
recuperación.

**Esto descalifica a todo el top del leaderboard multilingüe a julio de 2026:**

| Modelo | MTEB multiling. | Backbone | Veredicto |
|---|---|---|---|
| harrier-oss-v1-27B (Microsoft) | 74.3 (v2) | Gemma 3 | ❌ decoder |
| KaLM-Embedding-Gemma3-12B | 72.32 (MMTEB) | Gemma 3 | ❌ decoder |
| Qwen3-Embedding-8B | 70.58 | Qwen 3 | ❌ decoder |
| Llama-Embed-Nemotron-8B (NVIDIA) | top MMTEB | Llama 3.1 | ❌ decoder |
| harrier-oss-v1-270m | 66.5 (v2) | Gemma 3 | ❌ decoder |
| EmbeddingGemma-300m | — | Gemma 3 | ❌ decoder |

Que un modelo se distribuya como "embedding model" y produzca vectores no
cambia su arquitectura: harrier usa *decoder-only con last-token pooling*, y su
propia tarjeta de modelo lo dice. Usar cualquiera de estos es **riesgo de
descalificación**, no una zona gris.

**Regla operativa para el agente:** antes de adoptar cualquier checkpoint,
comprobar el `architectures` de su `config.json` en HuggingFace. Debe ser de la
familia BERT/RoBERTa/XLM-R/ModernBERT. Si aparece `Qwen`, `Llama`, `Gemma`,
`Mistral`, `Phi` o similar, se descarta sin discusión y se anota en el informe.

---

## §12 Candidatos

Los tres se implementan y se miden. La elección final sale de datos, no de esta
tabla.

### §12.1 Matriz

| | **granite-311m-r2** | **bge-m3** | **arctic-embed-l-v2.0** | *(referencia)* **mE5-large** |
|---|---|---|---|---|
| Repo HF | `ibm-granite/granite-embedding-311m-multilingual-r2` | `BAAI/bge-m3` | `Snowflake/snowflake-arctic-embed-l-v2.0` | `intfloat/multilingual-e5-large` |
| Backbone | ModernBERT multilingüe | XLM-RoBERTa-large | m-GTE / BGE-M3 (XLM-R) | XLM-RoBERTa-large |
| Parámetros | 311M | ~568M | ~568M | ~560M |
| Dimensión | 768 (Matryoshka 512/384/256/128) | 1024 | 1024 (MRL a 256) | 1024 |
| Contexto | 32 768 | 8 192 | 8 192 | 512 |
| Licencia | Apache 2.0 | MIT | Apache 2.0 | MIT |
| Prefijo requerido | ver tarjeta | ninguno | prefijo en la consulta | `query: ` / `passage: ` |
| Fortaleza | mejor encoder-only por MTEB-v2 retrieval (65.2); #2 bajo 500M; ~1 800 docs/s | robustez probada, fuerte en MIRACL (0.678), modo denso+disperso+ColBERT | mejor en CLEF (0.541 vs 0.410 de BGE-M3), que es recuperación cruzada en idiomas europeos | el estándar de facto, línea base honesta |
| Debilidad | lanzado abr-2026, poco rodaje; requiere `transformers` reciente | más lento; flojo en CLEF según Snowflake | cifras auto-reportadas; 74 idiomas | 512 tokens; requiere prefijos o degrada en silencio |

Cifras de las tarjetas de modelo y del paper de Arctic-Embed 2.0 (arXiv
2412.04506). Son auto-reportadas por sus autores; sirven para priorizar el
orden de prueba, no para decidir.

### §12.2 Descartados y por qué

- **jina-embeddings-v3 / v5**: la familia Jina publica pesos abiertos bajo
  CC-BY-NC (no comercial). §4.3 pide licencia de uso libre y prefiere
  Apache 2.0 / MIT / CC BY. Verificar antes de descartar del todo, pero el
  riesgo legal no compensa la ganancia.
- **gte-multilingual-base** (Alibaba, 305M, encoder-only, 8192 tokens): opción
  legítima y eficiente, pero granite-311m la supera en la misma clase de tamaño
  y con licencia igual de permisiva. Queda como suplente.
- **granite-embedding-97m-multilingual-r2**: 97M, 384 dims, 60.3 en MTEB-v2
  retrieval. **No es candidato de entrega, pero sí la herramienta de
  desarrollo**: úsalo para iterar chunking y depurar el pipeline, porque
  reindexar el corpus completo con él cuesta una fracción del tiempo. La entrega
  se hace con el modelo final.

---

## §13 Dos hechos que cambian el cálculo

### §13.1 El contexto largo no aporta nada aquí

Los fragmentos van a tener ≤250 palabras (~400 tokens). Las ventanas de 8k o 32k
están ociosas. **No hay que pagar tamaño ni latencia por contexto que no se
usa**, y `multilingual-e5-large`, con sus 512 tokens, no está en desventaja por
ese motivo.

Corolario para `spec-fragmentador.md` §2.1: `max_tokens=450` funciona con los
cuatro candidatos. La elección del encoder **no obliga a rediseñar la
fragmentación**; solo cambia la función de conteo. La re-fragmentación al fijar
el modelo sigue siendo obligatoria, pero es una re-corrida, no un rediseño.

### §13.2 Para la fusión RRF, el linaje importa más que el ranking

Arctic-Embed 2.0-L se inicializó desde el checkpoint de BGE-M3 y ambos usan el
tokenizador de XLM-R. Fusionar esos dos da ganancia marginal: fallan en los
mismos sitios. La fusión rinde cuando los modelos tienen **errores
descorrelacionados**.

Pareja recomendada si se implementa §8.4: **granite-311m-r2 (ModernBERT) +
uno de la familia XLM-R** (bge-m3 o mE5-large). Distinto pretraining, distinto
tokenizador, distintos modos de fallo.

---

## §14 Trampas de implementación, por modelo

Estas fallan **en silencio**: el pipeline corre, los vectores salen, el NDCG cae
y nadie sabe por qué.

1. **Prefijos asimétricos.** `multilingual-e5-large` exige `"query: "` en las
   consultas y `"passage: "` en los fragmentos. Sin ellos el modelo sigue
   funcionando y la calidad cae varios puntos. Arctic-v2 pide prefijo solo en la
   consulta. BGE-M3 no pide ninguno. **El prefijo es parte de la configuración
   del encoder, no del chunker**, y debe viajar con él en el mismo objeto de
   config, con un test que verifique que la consulta y el fragmento reciben
   prefijos distintos cuando el modelo lo requiere.

2. **Normalización.** §5.2 del enunciado exige `IndexFlatIP` con vectores
   normalizados para que el producto interno equivalga al coseno. Algunos
   modelos ya normalizan en su capa de salida y otros no. **Normalizar siempre y
   de forma explícita** antes de insertar, y verificar con un test que
   `‖v‖ ≈ 1.0 ± 1e-5` para una muestra.

3. **Truncamiento silencioso.** El tokenizador trunca por defecto sin avisar. Si
   un fragmento excede el límite, se indexa incompleto y nadie se entera.
   Registrar cuántos fragmentos se truncan; debe ser **cero**.

4. **Determinismo.** Fijar `torch.manual_seed`, `model.eval()`, y correr en
   float32 para la entrega. La inferencia en float16 introduce variación entre
   GPUs y rompe la reproducibilidad que exige §1.4 del enunciado para
   `generador.py`.

5. **Matryoshka.** granite-311m y arctic-v2 admiten truncar dimensiones. La
   ganancia en memoria es real y la pérdida pequeña, pero **si se trunca, hay que
   renormalizar después de truncar**, o el producto interno deja de ser el
   coseno.

6. **Versión de `transformers`.** ModernBERT necesita una versión reciente.
   Fijar la versión exacta en `requirements.txt`: el jurado tiene que poder
   ejecutar `generador.py` en su entorno.

---

## §15 Protocolo de evaluación

### §15.1 Fase 1 — Descarte por viabilidad (barato, sin ground truth)

Sobre una muestra de 200 documentos, para los tres candidatos:

- Verificar la arquitectura en `config.json` (§11). Cualquier decoder se cae aquí.
- Verificar la licencia en la tarjeta del modelo. Documentar cuál es.
- Medir throughput de codificación en el hardware real del equipo. Extrapolar al
  corpus completo. Si un modelo tarda más de lo que hay hasta la entrega,
  se cae.
- Verificar que cero fragmentos se truncan con `max_tokens=450`.
- Prueba de sanidad cruzada: 10 pares consulta-fragmento equivalentes en
  es/en/pt. La similitud coseno entre una consulta en español y su fragmento en
  inglés debe ser claramente mayor que contra un fragmento irrelevante. **Si un
  modelo falla esto, no sirve para este corpus**, por mucho MTEB que tenga.

### §15.2 Fase 2 — Medición real (requiere el ground truth interno)

Bloqueada hasta que exista el conjunto de 20–30 consultas etiquetadas a mano.
Con él, para cada candidato: **NDCG@10 y F1@3**, que son las métricas del reto.
Nada de proxies.

Ejecutar la matriz completa: 3 encoders × las configuraciones de chunking
supervivientes de §8.3 del spec anterior. Reportar en una tabla.

### §15.3 Fase 3 — Fusión, solo si sobra tiempo

RRF con `k₀=60` sobre la pareja de linajes distintos (§13.2). Se adopta **solo
si supera al mejor modelo individual** en el ground truth interno. Duplica el
tiempo de indexación y de consulta; no se adopta por elegancia.

---

## §16 Recomendación

**Empezar por `granite-embedding-311m-multilingual-r2`.** Es el encoder-only con
mejor recuperación multilingüe publicada, tiene Apache 2.0 sin ambigüedad, es el
más rápido de los tres, y sus 768 dimensiones ocupan un 25% menos que las 1024
de los otros dos.

**Medir siempre contra `bge-m3`** como control. Es el modelo con más rodaje en
literatura en español, así que si granite pierde contra él en nuestro ground
truth, la señal es fiable y hay que hacerle caso.

**Traer `arctic-embed-l-v2.0` solo si el corpus resulta ser más cruzado de lo
esperado** —muchas consultas en español recuperando documentos en inglés—, que
es donde sus números de CLEF prometen ventaja.

**Usar `granite-97m-r2` durante todo el desarrollo.** Reindexar 1826 documentos
tres veces al día con un modelo de 568M es tiempo que no vas a recuperar.

### §16.1 Lo que hay que decidir esta semana

La elección del modelo bloquea el conteo de tokens del chunker (§6.2 del spec
anterior) y, con él, la re-fragmentación del corpus completo. Cuanto más tarde
se cierre, más cara sale la re-corrida. La Fase 1 de §15 se puede completar en
un día y ya descarta o confirma dos de los tres.

### §16.2 Material para el informe técnico

> La selección del encoder estuvo gobernada por una restricción arquitectónica
> antes que por el rendimiento en benchmarks. §4.2 y §8.3 prohíben el uso de
> modelos de arquitectura decoder en la construcción del índice y en la
> recuperación, y a la fecha de este trabajo la totalidad de las primeras
> posiciones del leaderboard multilingüe de MTEB la ocupan modelos de embedding
> construidos sobre backbones decoder —Gemma 3, Qwen 3, Llama 3.1—, pese a
> distribuirse como modelos de embedding. Verificamos la arquitectura declarada
> en la configuración de cada checkpoint y restringimos la búsqueda a la familia
> BERT/XLM-R/ModernBERT.
>
> Dentro de ese conjunto se evaluaron [N] candidatos sobre un conjunto interno de
> consultas etiquetadas, midiendo directamente NDCG@10 y F1@3. Se seleccionó
> [modelo] por [resultado]. La longitud de contexto no fue un criterio
> discriminante: con fragmentos acotados a 250 palabras, la ventana de 512
> tokens del candidato más limitado resulta suficiente, de modo que las ventanas
> extendidas de los demás candidatos no representaban una ventaja utilizable.
