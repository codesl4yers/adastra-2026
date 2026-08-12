# Decisión — Títulos huérfanos y fragmentos fuera de norma

CODEFEST Ad Astra 2026 · Etapa 1 · 5 ago 2026 (§7 añadido el 6 ago)
Estado: **A y B corregidos y verificados**; C corregido el 6 ago mandando a OCR
las páginas ilegibles (§7), con dos limitaciones que siguen abiertas (§7.4).

---

## 1. Qué se encontró

Al revisar la distribución de tamaños de la corrida completa (147 109 fragmentos)
aparecieron tres problemas independientes. Ninguno lo introdujo el cambio al
tokenizador real: los tres son anteriores.

Punto de partida, y corrección de una lectura equivocada: **la mediana global de
105 palabras no era una regresión**. El histograma de la corrida anterior, con
la estimación de tokens, también cruzaba el 50 % en el intervalo 100-124. La
comparación que sugería una caída se hizo contra una muestra de 8 PDFs del AI
Index, que no representa el corpus.

La mediana global tampoco es una métrica útil aquí, porque promedia tres
poblaciones que no son comparables:

| población | n | mediana |
|---|---:|---:|
| `pdf` prosa | 88 865 | 192 palabras — dentro del objetivo 150-220 de §8.2 |
| `csv`/`pbf` atómico | 40 236 | 62-68 — registros de datos, no se pueden alargar sin fusionar registros distintos (§5) |
| `pdf` título huérfano | 13 170 | **4** — el problema |

## 2. Problema A — 13 170 títulos huérfanos

**Síntoma.** El 9 % del corpus eran fragmentos de 4 palabras de mediana, del
tipo `'1.1 Publications'` o `'Chapter Highlights'`. Un vector que solo dice
"Metodología" compite por los puestos del top-10 sin aportar nada.

**Causa raíz.** `_agrupar_secciones` abría sección nueva en **todo** título de
nivel ≤ `nivel_frontera`. En la cadena más común de un informe —H2, luego H5,
luego el texto—, el H2 se quedaba solo en su sección:

```
[46] titulo n=2  '1.1 Publications'    ruta=['Chapter Highlights']   → abre sección S1
[47] titulo n=5  'Overview'            ruta=[..., '1.1 Publications'] → abre sección S2
[48] parrafo     'Total Number of...'  ruta=[..., 'Overview']         → cae en S2
```

S1 contenía únicamente su propio título. `_fusionar_huerfanos` no podía
rescatarlo porque **fusiona dentro de una sección, nunca entre secciones**. La
regla §4.4 sí funcionaba en el caso de un solo nivel, que es el que cubría la
prueba existente; por eso no se detectó.

**Evidencia.** De los 45 906 títulos del corpus, **13 516 (29,4 %) van seguidos
de otro título**, lo que coincide con los 13 170 huérfanos observados. Solo
**81** son huérfanos legítimos: el último bloque de su documento.

**Corrección.** Un título solo abre sección si la sección en curso ya tiene
cuerpo. Si únicamente contiene títulos, el nuevo se acumula en ella y el ancla
—y el breadcrumb— pasan a seguir al título más profundo de la cadena, que es
donde va a colgar el contenido. Las fronteras de verdad no se ven afectadas:
un título que llega después de contenido sigue abriendo su sección.

Pruebas: `test_un_titulo_seguido_de_otro_titulo_no_queda_huerfano`,
`test_una_cadena_larga_de_titulos_viaja_con_su_cuerpo`,
`test_la_seccion_de_una_cadena_de_titulos_es_la_del_mas_profundo` y
`test_un_titulo_con_cuerpo_propio_sigue_abriendo_su_seccion`, que es la que
impide que la regla nueva se trague fronteras legítimas.

## 3. Problema B — 1 343 fragmentos por encima de 250 palabras

**Síntoma.** Fragmentos de hasta **8 995 palabras** (17 803 tokens), violando el
límite de §9.2.1 del enunciado. El 99 % de los fragmentos que se pasaban del
tope tenían `n_oraciones == 1`.

**Causa raíz.** Una **comilla desbalanceada**. En `F1-AIINDEX-041-c0401`, una
celda de un CSV mal escapado contiene `title: Hot Zones" for Otolaryngologists`.
pysbd interpreta esa comilla como apertura de cita y devuelve los 64 KB
restantes como una sola pieza:

```
con la comilla → 1 trozo        sin la comilla → 556 trozos
```

Con una sola "oración" de 64 KB, el fragmentador la emitía entera, y hacía bien:
§3.3 del enunciado le prohíbe cortar dentro de una oración.

**El conflicto.** Dos requisitos obligatorios en direcciones opuestas: §3.3
prohíbe partir oraciones y §9.2.1 exige ≤250 palabras. La salida no es sacrificar
uno, sino dejar de tratar como equivalentes una oración y lo que el segmentador
devolvió como tal:

> Si dentro del texto hay puntuación terminal seguida de espacio, **eso son
> oraciones de verdad** que el segmentador no supo ver, y cortar por ellas
> cumple §3.3 en lugar de violarlo. Si no las hay, no se toca nada.

`_repartir_pseudo_oracion` aplica exactamente esa regla, y conserva el texto
(`" ".join(resultado) == texto`). Sobre el caso real produce 564 trozos, mediana
16 palabras, ninguno por encima de 250. La oración legítima de 400 palabras que
protege la prueba 7.13 no se toca, porque no tiene fronteras internas.

## 4. Resultados medidos

Sobre los tres documentos más afectados —`F1-AIINDEX-041` (el CSV),
`F1-AIINDEX-001` (títulos anidados) y `F3-RESDAL-093` (el atlas)—:

| | antes | después |
|---|---:|---:|
| fragmentos | 3 236 | 2 488 |
| mediana de palabras | 38 | **193** |
| máximo de palabras | **8 995** | **420** |
| fragmentos > 250 palabras | 46 | 8 |
| fragmentos > 450 tokens | 61 | 27 |
| títulos huérfanos | 1 204 (37,2 %) | **0** |

**Los 8 que siguen fuera de norma** son todos del atlas de RESDAL: texto de
tablas e infografías sin ninguna puntuación —listas de tratados concatenadas—,
entre 251 y 420 palabras. No tienen fronteras internas, así que partirlos
cortaría a mitad de un nombre propio. Es el caso que §3.3 protege de verdad, y
el exceso es modesto. Se aceptan y quedan registrados como violación de tamaño
por `validar_fragmento`, con el motivo "oración indivisible".

## 5. Problema C — texto de PDF ilegible (diagnóstico; la corrección está en §7)

**5 877 fragmentos (5,76 % de los de PDF)** tienen más del 35 % de sus palabras
formadas por una sola letra, y muchos llegan al 100 %:

```
'L i f e c y c l e   c o s t   e s t i m a t i o n   r e q u i r e'    (368 palabras)
'- 1 t - e v v C A I i 3 i i 3 - p t T 3 c t l t t ( r 1 ( c'          (irrecuperable)
```

No es un problema de fragmentación sino de **extracción**: son PDFs cuyo texto
sale carácter a carácter, típicamente diapositivas o gráficos donde cada letra
es un objeto de texto independiente. Una parte es reconstruible uniendo las
letras; otra parte es irrecuperable.

**Corregido el 6 ago 2026 mandándolos a OCR**, tras comprobar que el análisis
inicial mezclaba dos poblaciones distintas. Ver §7.

## 7. Problema C, segunda parte — texto presente pero ilegible → OCR

Al desglosar los fragmentos largos aparecieron **dos** causas distintas donde
al principio se vio una sola:

- **Letras dibujadas sueltas** (`L i f e c y c l e  c o s t`), lo descrito en §5.
- **Fuente sin `ToUnicode`**: pdfplumber no puede mapear los glifos y devuelve
  `(cid:NN)` por carácter. **1 783 fragmentos, el 5,3 % del coste total de
  codificación**, concentrados en 18 documentos, y con uno solo —`F3-CEOBS-030`,
  179 páginas— aportando el 93 %.

Ninguna la detectaba `_parece_escaneado`, que decide por densidad de caracteres
por página: `(cid:47)` son nueve caracteres por letra, así que estos documentos
tienen densidad altísima y nunca disparaban el OCR.

**El OCR sí los recupera**, porque el texto está dibujado en la página:

```
NATIVO: (cid:76)(cid:81)(cid:3)(cid:54)(cid:56)(cid:39)(cid:36)(cid:49)...
OCR:    Obligations of the Minamata Convention 5 The Minamata initial assessment...
```

| documento | páginas | antes | después | confianza |
|---|---:|---|---|---:|
| F3-CEOBS-030 | 179 | 2 815 808 chars de CID | 350 477 legibles | 93,9 % |
| F2-CSIS-200 | 5 | 88 888 chars de CID | 10 504 legibles | 94,7 % |
| F3-RESDAL-096 | 32 | 137 700 (38 % CID) | 90 604 | 88,9 % |

`_texto_ilegible` aplica dos criterios sobre el texto ya extraído:

- **CID > 30 %.** Limpio y sin falsos positivos posibles: `(cid:` no aparece en
  texto legítimo, y la separación es nítida (0,99 y 0,38 frente a 0,15 y menos).
- **Letras latinas sueltas > 40 %.** Contar cualquier carácter suelto no servía:
  `F2-SWF-035` daba 0,48 y es el logotipo de la fundación repetido en cinco
  alfabetos —安 全 世 界, م, ФОНД—, perfectamente legible. Restringido a letras
  latinas cae a 0,03 mientras que el documento roto se queda en 0,44.

`MAXIMO_PAGINAS_OCR` sube de 30 a 200: truncar `F3-CEOBS-030` en la página 30
tiraría 149 páginas recuperables. En total van a OCR **6 documentos, 288
páginas, unos 5 minutos** de extracción.

### 7.1 La decisión es por página, no por documento

La primera versión decidía por documento y eso arreglaba una parte estropeando
otra: `F3-RESDAL-096` era legible en un 62 % y mandarlo entero a OCR le costaba
los acentos —`análisis` salía `nalisis`—, mientras que `F2-CSIS-113` y
`F3-SIPRI-007` tienen solo la portada destrozada —"d s e f e g c u e d r e w l w
n o o K r l d"— y un cuerpo perfectamente legible con sus 57 títulos.

`extraer` evalúa **cada página** y reconoce únicamente las que no se pueden
leer. `_bloques_de_lineas` recibe qué páginas vinieron del OCR y las trata
aparte por dos motivos: sus líneas **no entran en el cálculo del tamaño del
cuerpo ni de los niveles** —no tienen tamaño de fuente real, y colarlas ahí
desplazaría la moda y cambiaría qué se considera título en todo el documento— y
se emiten con el tipo `ocr` del contrato, que conserva la trazabilidad.

| documento | páginas reconocidas | conserva |
|---|---|---|
| F2-CSIS-113 | 3 / 28 | 57 títulos y 238 párrafos nativos |
| F3-SIPRI-007 | 6 / 40 | 12 títulos y 132 párrafos |
| F3-RESDAL-096 | 13 / 32 | 22 títulos y 104 párrafos, con sus acentos |
| F2-CSIS-200 | 5 / 5 | — (roto en toda su extensión) |

### 7.2 Reconocer solo si mejora

Detectar que una página está rota **no basta para reemplazarla**. La página 64
de `F1-AIINDEX-014` mezcla texto chino perfectamente legible con las etiquetas
rotadas de un gráfico, que salen letra a letra —"C S C S T A T A t F I T M F F"—
y disparan el criterio de ilegibilidad con 0,56. El diagnóstico es correcto,
pero el OCR de esa página solo devuelve los porcentajes del gráfico: 605
caracteres de contenido se habrían convertido en 85 de ruido.

`_mejora()` compara el **texto útil** de ambas versiones y solo sustituye si el
reconocimiento aporta más. La comparación descuenta los marcadores CID —que
abultan nueve caracteres por letra sin aportar nada, y medir en bruto dejaría
sin reconocer justo las páginas que más lo necesitan— y las letras sueltas.

El efecto sobre el documento chino es grande: de **33 páginas candidatas solo se
reemplazan 3**, conservando el 99,9 % de sus 174 973 caracteres chinos y ganando
1 814 de portadas que estaban vacías.

### 7.3 Los dos criterios no tienen la misma granularidad

- **Texto roto** (CID o letras sueltas): **página a página**. Un informe puede
  tener la portada destrozada y el cuerpo perfectamente legible.
- **Falta de texto**: solo cuenta si lo es **del documento entero**. Cualquier
  informe tiene portadas, separadores de capítulo y páginas de figuras que salen
  casi vacías; tratarlas como rotas mandaba a OCR 298 de los 759 PDFs —1 764
  páginas, media hora— para recuperar, en el mejor caso, el título de una
  portada. Cuando la falta de texto es de todo el documento sí significa lo que
  parece: un escaneado sin capa de texto, que es el caso para el que se escribió
  `_parece_escaneado`.

Con esa distinción el alcance baja a **142 documentos y 887 páginas, unos 15
minutos**, y lo que se reconoce es texto roto de verdad. El coste se paga aunque
`_mejora()` acabe descartando el resultado, porque hay que rasterizar la página
para saber si aporta.

### 7.4 Lo que queda abierto

**Queda CID sin reconocer** donde el OCR no mejoraba: 46 marcadores en
`F3-CEOBS-030` de los 354 921 que tenía, y 535 en `F3-RESDAL-096`. Es el precio
de ser conservador, y prefiero eso a destruir contenido legible.

**Hay una rotura que ninguno de los dos criterios detecta**: el texto
entrelazado de `F2-CSIS-113` —"Tito necmoprpoowreart es su hd e tt cMhoitoenc,t
uNrAeS"—, que no es CID ni letras sueltas, sino columnas mal ordenadas por el
extractor. Su página 8 sí supera el umbral de letras sueltas (0,59), pero
`_mejora()` la rechaza con razón: tiene 49 524 caracteres y su contenido es
mixto, con párrafos perfectamente legibles —"White Papers LUNAR SURFACE CARGO
Analyzes projected needs and capabilities"— conviviendo con el desorden. El OCR
de una página no puede devolver tanto texto, así que sustituirla destruiría lo
que sí se lee.

Es un problema de **ordenación de columnas**, no de reconocimiento, y la salida
sería mejorar `detectar_corte_de_columnas` para esos casos. Queda pendiente.

### 7.5 Resultado sobre los documentos de referencia

| documento | páginas reconocidas | caracteres | CID |
|---|---:|---|---:|
| F3-CEOBS-030 | 178 / 179 | 2 815 808 → 343 788 | 354 921 → 46 |
| F1-AIINDEX-014 (chino) | 1 / 456 | 385 661 → 387 268 | 0 → 0 |
| F2-CSIS-113 | 0 / 28 | sin cambios | 0 → 0 |

`F1-AIINDEX-014` es la comprobación de que no se destruye contenido: de 456
páginas solo se reconoce una portada vacía, y sus 174 973 caracteres chinos
quedan intactos.

## 8. Párrafo para el informe técnico

> El control de calidad de la fragmentación se hizo sobre la distribución de
> tamaños del corpus completo, no sobre una muestra. El análisis reveló que la
> mediana global de palabras no es interpretable de forma directa, al promediar
> tres poblaciones de naturaleza distinta: prosa de informes (mediana de 192
> palabras, dentro del objetivo de diseño), registros de datasets y de mapas
> vectoriales (62-68 palabras, acotados por el propio registro) y encabezados
> sin cuerpo. Se corrigieron dos defectos: los encabezados que preceden
> inmediatamente a otro encabezado quedaban aislados en su propia sección y se
> emitían como fragmentos de dos o tres palabras —el 29 % de los 45 906
> encabezados del corpus—, y las celdas de datos con comillas desbalanceadas
> impedían la segmentación oracional, produciendo fragmentos de hasta 8 995
> palabras que incumplían el límite de 250. Para el segundo caso se adoptó el
> criterio de que la presencia de puntuación terminal interna evidencia
> fronteras oracionales no detectadas, de modo que segmentar por ellas satisface
> el requisito de no partir oraciones en lugar de contravenirlo. Tras las
> correcciones, la mediana de los documentos afectados pasa de 38 a 193 palabras
> y el fragmento máximo de 8 995 a 420.
