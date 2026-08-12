# Decisión — Conteo de tokens con el tokenizador real

CODEFEST Ad Astra 2026 · Etapa 1 · 5 ago 2026 (§7 añadido el 11 ago)
Estado: **implementado y aplicado**. La re-fragmentación del corpus completo ya
se lanzó; su resultado está en §7.

---

## 1. La decisión

`ConfigFragmentacion.contar_tokens` deja de ser la estimación
`ceil(palabras × 1,6)` y pasa a ser el `AutoTokenizer` de
`ibm-granite/granite-embedding-311m-multilingual-r2`, el encoder elegido en §16
del addendum.

```python
from encoder import config_fragmentacion_con_tokenizador
from fragmentador import fragmentar_corpus

fragmentar_corpus(Path("extraidos"), Path("chunks"),
                  config_fragmentacion_con_tokenizador())
```

El conteo incluye los tokens especiales, porque el límite del encoder se aplica
a la secuencia completa. Granite añade **uno solo** (`<bos>`) y no define
`cls_token` ni `sep_token`: el "CLS pooling" que declara su tarjeta es la
primera posición de la secuencia, que es ese `<bos>`. Un conteo que asuma el par
CLS/SEP de BERT sobreestima en un token por fragmento
(`encoder.py`, `TOKENS_ESPECIALES`).

## 2. El hallazgo: la estimación no era conservadora

El código documentaba el factor 1,6 como *"sobreestima, que es el error
seguro"*. **Sobre este corpus es falso.** Medición con el tokenizador real
sobre una muestra sistemática de 1 de cada 7 fragmentos (20 098 de 140 686,
cubriendo el archivo completo):

| Formato | n | Mediana tokens/palabra | p90 | Fragmentos > 450 tokens |
|---|---|---|---|---|
| pbf | 1 353 | **3,50** | 4,04 | 5,5 % |
| csv | 4 406 | **2,81** | 3,27 | 2,9 % |
| xlsx | 89 | 1,89 | 2,03 | 0 % |
| pdf | 13 665 | 1,48 | 2,86 | 10,4 % |
| json | 583 | 1,30 | 2,04 | 2,7 % |
| texto | 2 | 1,29 | 1,38 | 0 % |
| **global** | **20 098** | **1,77** | — | **8,2 %** |

En prosa latina limpia el factor 1,6 es correcto y hasta generoso —16 palabras
de español dan 20 tokens (1,25); 13 de inglés dan 15 (1,15)—. Subestima en dos
poblaciones concretas:

1. **Registros tabulares y geoespaciales** (csv, pbf): códigos, identificadores,
   coordenadas y números no comprimen como el lenguaje natural. Mediana de 2,8
   a 3,5 tokens por "palabra".
2. **Texto sin separación por espacios**: el corpus trae secciones en chino
   dentro de informes del AI Index. Ahí `split()` cuenta un bloque entero como
   una palabra y el tokenizador produce decenas de tokens. El caso extremo
   medido es `F2-CSIS-200-c0003`: 192 palabras → 20 082 tokens.

Consecuencia: el **8,2 %** de los fragmentos de la corrida actual supera el tope
de diseño de 450 tokens, y el 5,5 % superaría incluso los 512 de un encoder
convencional. Con el estimador esos fragmentos parecían estar dentro.

## 3. Lo que este hallazgo **no** significa

**No hay ni un solo fragmento truncado.** La ventana de granite es de 32 768
tokens (`config.json` y `sentence_bert_config.json` coinciden), y el máximo
medido en todo el corpus es de 20 082. Cero fragmentos por encima de la ventana
(verificado en `tests/test_encoder_integracion.py`, `test_ningun_fragmento_del_corpus_real_excede_la_ventana`).

Es decir: el índice que saldría hoy no perdería texto. Lo que se viola es el
tope de diseño de 450, que existe por dos razones distintas de la capacidad del
modelo —granularidad de recuperación y margen frente a encoders de 512—, y esa
es la razón por la que la re-fragmentación sigue siendo obligatoria antes de la
entrega.

## 4. Qué cambia al re-fragmentar

Con el contador real, el tope de tokens pasa a ser la restricción activa por
delante del de palabras en csv, pbf y en el material CJK: el empaquetado cerrará
fragmentos antes de llegar a las 240 palabras. Se espera una mediana de palabras
más baja que la actual y un número de fragmentos algo mayor. Hay que volver a
mirar el histograma del reporte contra el objetivo de 150–220 palabras de §8.2
y, si la mediana se hunde en los formatos tabulares, la respuesta **no** es
subir `max_tokens` sino revisar `objetivo_palabras` por tipo de unidad, midiendo
contra el ground truth.

Las oraciones indivisibles seguirán excediendo el tope: partirlas violaría §3.3
del enunciado, que prohíbe cortar dentro de una oración. `validar_fragmento` ya
las tolera explícitamente cuando `n_oraciones <= 1`, y con una ventana de 32 768
no tienen ningún efecto práctico.

## 5. Coste de la re-corrida

Tokenizar es rápido (tokenizador rápido en Rust), pero se llama muchas veces por
fragmento durante el empaquetado. La corrida completa sobre `extraidos/` es del
orden de las horas de la fragmentación actual más el sobrecoste de tokenización.
Es una re-corrida, no un rediseño: el algoritmo y todos los topes de palabras
quedan igual (§13.1 del addendum).

## 6. Resultado de la re-corrida (11 ago 2026)

Corrida completa de `fragmentar_corpus` con `--tokenizador real` sobre los 1826
documentos, ya con la limpieza de campos tabulares aplicada:

| | estimación 1,6 | tokenizador real |
|---|---:|---:|
| fragmentos | 140 686 | **134 317** |
| mediana de palabras | 123 | **140** |
| p95 de palabras | 234 | 232 |
| atómicos | 40 978 | 41 594 |
| huérfanos fusionados | 2 693 | 2 159 |
| fragmentos > 450 tokens | 8,2 % | **1,9 %** (2 543) |
| token máximo | 17 803 | **3 691** |

Lo esperado en §4 se cumple a medias y conviene dejarlo escrito: el tope de
tokens sí pasó a ser la restricción activa en csv y pbf, pero la mediana global
de palabras **subió** de 123 a 140 en vez de bajar. La causa es que las dos
correcciones viajaron juntas: los registros bibliográficos limpios (§ del doc de
campos indexables) se agrupan en vez de salir sueltos, y eso empuja la mediana
hacia arriba más de lo que el contador real la empuja hacia abajo. Sigue por
debajo del objetivo de 150–220 de §8.2, y esa distancia es de la población
tabular, no de la prosa.

El 1,9 % que queda por encima de 450 son oraciones indivisibles y registros
atómicos: partirlos violaría §3.3. Con la ventana de 32 768 del modelo y un
máximo de 3 691, **cero fragmentos truncados** en el índice construido.

## 7. Párrafo para el informe técnico

> El control de tamaño de los fragmentos se realiza con el tokenizador del
> encoder seleccionado y no con una heurística de palabras. La primera versión
> del pipeline empleaba una estimación de 1,6 tokens por palabra, documentada
> como conservadora; la medición con el tokenizador real de
> granite-embedding-311m-multilingual-r2 sobre una muestra sistemática del 14 %
> del corpus mostró una mediana global de 1,77 tokens por palabra, con 3,50 en
> tiles vectoriales y 2,81 en datos tabulares, de modo que la estimación
> subestimaba el tamaño real en los formatos no textuales y el 8,2 % de los
> fragmentos excedía el tope de diseño. Ningún fragmento supera la ventana de
> 32 768 tokens del modelo, por lo que no se produce truncamiento; el corpus se
> re-fragmenta con el contador real para que la distribución de tamaños
> reportada corresponda a la que el encoder observa.
