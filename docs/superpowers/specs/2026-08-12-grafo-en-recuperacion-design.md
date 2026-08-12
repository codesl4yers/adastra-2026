# Diseño — El grafo de conocimiento en la recuperación

CODEFEST Ad Astra 2026 · Etapa 1 · 12 ago 2026
Estado: **aprobado, sin implementar**. Depende de
`2026-08-12-evaluador-y-top-k-design.md`, que aporta con qué medirlo.

El componente bonus del equipo (`generador_grafo.py`, Sección 7) entra en la
recuperación por dos caminos, cada uno tras su propio flag: **expansión de la
consulta** con el gazetteer multilingüe y **reordenamiento del top-k** por
solape de entidades, fundido con RRF.

No se toca el índice. No se carga el GraphML.

---

## 1. El problema que resuelve

Las 50 consultas de ADL están en español y el corpus es **75 % inglés**. El
encoder es multilingüe justamente para salvar ese salto, pero lo hace en el
espacio vectorial, donde una sigla o un nombre propio se diluyen entre 768
dimensiones. La coincidencia exacta de entidades es la señal que el denso pierde,
y es la que el grafo tiene resuelta: 229 entidades canónicas con 878 variantes de
superficie unificadas entre español, inglés y portugués.

Medido sobre las 50 consultas reales:

| | |
|---|---|
| Consultas con alguna entidad reconocida | 45 / 50 |
| Consultas con entidad **del gazetteer** (las que se pueden expandir) | **40 / 50** |
| Variantes nuevas por consulta expandida | 9 de media |
| NER sobre las 50 consultas | 0,014 s |
| NER sobre 2500 fragmentos (50 consultas × 50 candidatos) | 2,79 s |
| Entidades por fragmento | 13,2 de media |

Todo el componente cuesta menos de tres segundos por corrida y corre en CPU.

## 2. Alcance

Entra:

- `expandir_consulta`, tras `--expandir-consulta`.
- `reordenar_por_entidades` con fusión RRF, tras `--reordenar-entidades`.
- `generador_grafo.py` en la raíz y `networkx` en `requirements.txt`: **ya hecho**
  por la reorganización del repositorio.

**No entra la expansión por vecindario del GraphML.** Son 100.171 aristas sobre
24.893 nodos y el 84 % son `relacionado_con`, así que el vecindario de una
entidad frecuente no discrimina. Además, `GRAFO_README.md` §6 advierte de que las
aristas son co-mención en contexto verbal, no aserción: el ejemplo que da el
propio autor es `Irán -[lanza]-> satélite` extraído de un texto que dice que Irán
**no** tiene esa capacidad. Expandir por ahí introduce ruido con apariencia de
conocimiento.

## 3. Dónde vive el código

`generador_grafo.py` vive en la raíz, junto al resto del pipeline entregable.
Las herramientas que lo construyen están en `auxiliar/`, y la dirección de
dependencia va siempre de `auxiliar/` hacia la raíz, nunca al revés.

El archivo **no se modifica**: se usa como biblioteca. Importa `networkx` a nivel
de módulo aunque el NER no lo necesite, así que `networkx>=3.0,<4` (BSD-3-Clause)
entra en el `requirements.txt` de la raíz. Es una dependencia ligera y sin
modelos descargables, así que no compromete la reproducibilidad de §1.4.

Lo que se consume de él: `extraer_entidades`, `GAZETTEER` y la dataclass
`Entidad`. Nada más.

**El import es perezoso**, dentro de las funciones que lo usan y no en la cabecera
de `generador.py`. Si fuera a nivel de módulo, construir el índice —que no tiene
nada que ver con el grafo— exigiría `networkx` instalado, y `encoder.py` dejaría
de ser importable sin él. Es el mismo criterio con el que `faiss` se importa
dentro de `generar_indice`.

## 4. La expansión de la consulta

```python
def expandir_consulta(texto: str) -> str:
    """El texto con las variantes multilingües de sus entidades del gazetteer."""
```

Se reconocen las entidades de la consulta, se toman **solo las de origen
`gazetteer`** —las de origen `sigla` y `capitalizada` son heurísticas y no tienen
variantes que aportar— y se añaden al final las variantes que no estén ya en el
texto.

```
q001  "¿Cómo está transformando la inteligencia artificial la capacidad
       de los Estados para ... amenazas NBQR?"
   →  "...amenazas NBQR? artificial intelligence AI inteligência artificial"
```

**Determinismo:** las variantes salen en el orden de aparición de la entidad en
la consulta y, dentro de cada una, en el orden del gazetteer. Nada de `set` en el
camino que decide el orden; el texto que se codifica tiene que ser idéntico entre
corridas (§1.4).

La expansión ocurre **antes** del prefijo del encoder, dentro de
`_codificar_consultas`. El campo `consulta` del entregable sigue siendo el texto
literal de ADL: el entregable se casa con el suyo, no con el nuestro.

### 4.1 El riesgo: homogeneizar las consultas

Diez de las cincuenta consultas reciben exactamente el mismo apéndice
(`artificial intelligence`, `AI`, `inteligência artificial`), porque las diez
mencionan inteligencia artificial. Añadir el mismo texto a varias consultas las
acerca entre sí en el espacio vectorial y puede restarles poder discriminante.

Medido: **26 expansiones distintas sobre 50 consultas**, la mayor compartida por
10 y otras 10 consultas sin expansión. El riesgo existe y está acotado; lo
resuelve la medición de §6, no un argumento.

## 5. El reordenamiento por entidades

```python
K_RRF = 60

def reordenar_por_entidades(
    candidatos: list[Candidato], entidades: set[str], k_rrf: int = K_RRF
) -> list[Candidato]:
    """Funde el orden denso con el de solape de entidades, por RRF."""
```

Dos rankings del mismo conjunto de candidatos:

- **denso**: el orden en que llegan de FAISS.
- **entidades**: por número de entidades de la consulta presentes en el
  fragmento, con los empates rotos por la posición densa —que es lo que lo hace
  determinista, porque el solape empata mucho—.

```
score_rrf(c) = 1/(60 + rango_denso(c) + 1) + 1/(60 + rango_entidades(c) + 1)
```

**RRF y no una suma ponderada** porque no tiene parámetro libre. Calibrar un alfa
contra las mismas 50 consultas con las que luego se reporta la métrica es
sobreajuste, y el número dejaría de ser citable en el informe técnico. El 60 es la
constante estándar del método, no un valor ajustado aquí.

### 5.1 El score que se emite

`agregar_por_documento` ordena por `Candidato.score`. Si se reordena la lista sin
tocar el score, ese paso **deshace** la fusión y el reordenamiento no tiene ningún
efecto sobre el top-3 de documentos.

Por eso `reordenar_por_entidades` devuelve candidatos con `score` sustituido por
el score RRF y el coseno original guardado en un campo nuevo:

```python
@dataclass(frozen=True)
class Candidato:
    fila: int
    score: float               # el que ordena: coseno, o RRF si se reordenó
    metadata: dict[str, Any]
    score_denso: float | None = None   # el coseno, cuando score ya no lo es
```

`registro_de_resultado` emite `score_denso` junto a `score` cuando existe. Sin el
flag, `score_denso` es `None` y el entregable sale exactamente como hoy.

Que el campo `score` cambie de escala —de 0,87-0,95 a valores del orden de 0,03—
cuando el flag está activo es deliberado y queda documentado: `score` significa
«lo que decidió el orden», y con fusión eso ya no es un coseno.

### 5.2 Dónde entra en el camino

```
banco.search
  → filtrar_por_idioma
  → deduplicar_por_texto
  → reordenar_por_entidades      ← nuevo, tras flag
  → mejores_fragmentos (top-10)
  → agregar_por_documento (top-3)
```

Antes del corte, no después: reordenar los diez que ya se eligieron por score
denso no cambiaría cuáles son los diez.

## 6. Cómo se decide si se queda

Las dos piezas van tras flags apagables **para poder medir las cuatro
combinaciones** con `auxiliar/scripts/evaluar.py` y quedarse con la que gane:

| Corrida | Flags |
|---|---|
| base | ninguno |
| expansión | `--expandir-consulta` |
| reordenamiento | `--reordenar-entidades` |
| ambas | los dos |

La expansión obliga a re-codificar las 50 consultas (segundos de GPU); el
reordenamiento no toca el encoder. **Ninguna de las cuatro reconstruye el
índice.**

Se queda la combinación con mejor NDCG@10 binario, salvo que hunda el F1@3. Si
ninguna mejora la base, los flags se quedan implementados y apagados, y eso
también se escribe en la bitácora: un resultado negativo medido vale más que una
mejora supuesta.

## 7. Lo que se reporta

`ReporteConsultas` gana `n_consultas_expandidas` y `n_reordenadas`, para que la
corrida deje constancia de cuántas consultas tocó cada pieza. Con 40 de 50
expandibles, un `n_consultas_expandidas` de 0 significa que el gazetteer no
cargó, no que las consultas no tuvieran entidades.

## 8. Pruebas

- `expandir_consulta`: una consulta con entidad del gazetteer gana sus variantes;
  una sin entidades vuelve intacta; una variante ya presente en el texto no se
  duplica; dos llamadas dan el mismo texto (determinismo); una entidad de origen
  `sigla` no aporta variantes.
- `reordenar_por_entidades`: un candidato con más solape sube; sin entidades en la
  consulta la lista vuelve intacta; el empate de solape se rompe por posición
  densa; el `score_denso` conserva el coseno original; la longitud de la lista no
  cambia.
- Integración: con `--reordenar-entidades` el top-3 de documentos puede cambiar
  —que es la prueba de que §5.1 está bien resuelto—; sin flags, el entregable es
  idéntico al de antes de esta pieza.

**Las dos funciones reciben el reconocedor y el gazetteer por inyección**, igual
que `generar_indice` recibe `codificar` y `contar_tokens`:

```python
def expandir_consulta(texto, reconocer=None, gazetteer=None) -> str
def reordenar_por_entidades(candidatos, entidades, k_rrf=K_RRF, reconocer=None) -> list[Candidato]
```

Sin ellos se usan los reales. Con ellos, las pruebas trabajan sobre un gazetteer
mínimo y propio. Es necesario, no cosmético: `generador_grafo` precompila sus
expresiones regulares al importarse, así que un gazetteer de prueba no se puede
sustituir después; y atar la suite a las 229 entidades del compañero la haría
fallar cada vez que él añada una.

## 9. Bitácora

Un `docs/decisiones/grafo-en-la-recuperacion.md` nuevo con: por qué el gazetteer y
no el GraphML, las cifras de cobertura de §1, por qué RRF y no un peso calibrado,
el problema del score de §5.1, el riesgo de homogeneización de §4.1 y **los cuatro
resultados medidos**, incluida la combinación que se descartó.
