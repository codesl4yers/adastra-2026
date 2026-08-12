# Decisión — Segmentación de oraciones

CODEFEST Ad Astra 2026 · Etapa 1 · 11 ago 2026
Estado: **implementado y verificado** contra el conjunto dorado; 0 `xfail`.

---

## 1. Por qué es el componente crítico

§3.3 del enunciado prohíbe que una oración cruce de un fragmento a otro. Eso
convierte la frontera oracional en el átomo de todo el sistema: si el segmentador
falla, **todos** los fragmentos afectados violan un requisito obligatorio, no uno
de calidad. Por eso vive en su propio módulo (`segmentador.py`) y se prueba
aparte del fragmentador.

## 2. El motor: pysbd

[pysbd](https://github.com/nipunsadvilkar/pySBD) 0.3.4, licencia MIT: por reglas,
determinista y **sin descarga de modelos en tiempo de ejecución**, que es lo que
mantiene reproducible a `generador.py`. Por ese mismo motivo quedan descartados
nltk-punkt (baja un modelo) y los modelos estadísticos de spaCy.

**pysbd 0.3.4 no trae módulo de portugués**, en contra de lo que daba por hecho
el spec. Sus idiomas son `{en, es, de, fr, it, nl, pl, ru, da, el, ja, zh, hi,
mr, ar, fa, hy, bg, kk, sk, ur, am, my}`. El portugués se segmenta con el módulo
español —la lengua tipológicamente más cercana de las disponibles— y con su
propia lista de abreviaturas (`séc.`, `p.ex.`, `n.º`…), que es lo que de verdad
cambia el resultado en este corpus.

`clean=False` es obligatorio: con `clean=True`, pysbd reescribe el texto y la
unión de las oraciones deja de reproducir el original.

## 3. La capa de re-fusión

pysbd solo no basta. Su módulo español parte `La Dra. | Gómez`, `EE.UU. | y la
U.S. | Space Force` y `cf. | Martínez`, que son justo los venenos que lista §3.2
del spec del fragmentador. Encima va una capa que vuelve a unir los cortes falsos
con tres reglas: abreviatura conocida del idioma, sigla con puntos internos o
inicial suelta, y trozo que no termina en puntuación terminal seguido de otro que
empieza en minúscula.

**La capa es deliberadamente agresiva porque el error no es simétrico:** partir
una oración viola §3.3; fusionar dos de más solo engorda un fragmento.

Las abreviaturas se separan en dos listas por ese motivo:

- **Prefijas** (`Dr.`, `Fig.`, `vol.`, `Art.`, `U.S.`): piden un nombre o un
  número detrás, así que el punto nunca cierra oración y se fusionan siempre.
- **Ambiguas** (`etc.`, `Inc.`, `Ltda.`, `al.`): cierran oración tan a menudo
  como no lo hacen —«…sensores, etc. El resultado…» frente a «…etc. y sus
  derivados»—. Solo se fusionan cuando lo que sigue empieza en minúscula o
  dígito, que es la única señal fiable de que la frase continúa.

La única división **propia** del módulo es el cierre de cita: pysbd no corta
después de una comilla o un paréntesis de cierre, así que «El riesgo es alto.» La
recomendación… le sale de una pieza. Se corta ahí, y solo ahí, porque exigir un
signo terminal antes del cierre y una mayúscula o apertura después es evidencia
suficiente de frontera.

## 4. El invariante que no se negocia

```
" ".join(segmentar(t, idioma)) == normalizar_texto(t)
```

Si la re-fusión o el propio pysbd lo rompieran, se devuelve el texto entero como
una sola oración. Un fragmento más grande de lo deseable es un problema de
calidad; un texto alterado es un problema de trazabilidad, y le entregaría al
jurado un texto que no está en el documento.

Un idioma fuera del contrato cae a español en vez de lanzar: el fragmentador no
puede morir porque un extractor devuelva un idioma inesperado.

## 5. El conjunto dorado

`auxiliar/fixtures/oraciones_doradas.jsonl`: **65 casos etiquetados a mano**, 21 o más por
idioma, con los venenos que exige §3.2 del spec —abreviaturas, decimales, siglas
con puntos, citas, listas sin punto final, comillas que envuelven el punto,
elipsis, encabezados sin puntuación—.

Un caso que el segmentador falle **no se borra**: se documenta con su motivo en
el campo `excepcion` del JSONL y sale como `xfail`. Hoy no hay ninguno.

## 6. El coste, y por qué el fragmentador avisa

pysbd compila una expresión regular nueva por cada oración para ubicar su
posición en el texto. Con documentos normales es irrelevante; con el atlas de
RESDAL, que trae 8319 bloques él solo, son varios minutos de cómputo real que sin
aviso no se distinguen de un proceso colgado. De ahí `UMBRAL_AVISO_BLOQUES` en el
fragmentador.

## 7. Lo que el segmentador no puede arreglar

Una comilla desbalanceada dentro de una celda de CSV hace que pysbd trate 64 KB
de texto como una sola cita. Eso no se corrige aquí —el invariante de §4 impide
tocar el texto— sino en el fragmentador, repartiendo la pseudo-oración por sus
fronteras internas reales. Ver `fragmentos-fuera-de-norma.md` §3.
