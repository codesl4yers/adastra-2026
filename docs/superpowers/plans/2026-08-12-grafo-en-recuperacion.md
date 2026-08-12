# El grafo de conocimiento en la recuperación — Plan de implementación

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usa
> `superpowers:subagent-driven-development` (recomendada) o
> `superpowers:executing-plans` para implementar tarea por tarea. Los pasos usan
> casillas (`- [ ]`) para el seguimiento.

**Objetivo:** que el gazetteer multilingüe del componente bonus entre en la
recuperación por dos caminos medibles y apagables — expandir la consulta y
reordenar el top-k — y quedarnos con lo que gane contra el ground truth.

**Arquitectura:** `generador_grafo.py` sube a la raíz y se usa como biblioteca,
sin modificarlo, con import perezoso. Dos funciones puras nuevas en
`generador.py`, cada una tras su flag. La fusión es RRF, sin parámetro que
calibrar.

**Stack:** Python 3.11+, pytest, NetworkX (BSD-3-Clause) como dependencia
arrastrada por `generador_grafo.py`.

**Spec:** `docs/superpowers/specs/2026-08-12-grafo-en-recuperacion-design.md`

**Depende de:** `2026-08-12-evaluador-y-top-k.md`, que debe estar completo. Sin
`scripts/evaluar.py` no hay forma de decidir si estos flags se quedan encendidos,
y encenderlos sin medir es exactamente lo que este plan evita.

## Restricciones globales

- **El índice no se toca.** Ninguna tarea reconstruye `index.faiss`.
- **Sin flags, el entregable sale idéntico.** Es la prueba de que nada de esto
  altera el camino base.
- `generador_grafo.py` **no se modifica**: es código del compañero y se consume
  como biblioteca. Si hiciera falta tocarlo, se para y se habla con él.
- Import perezoso de `generador_grafo` dentro de las funciones, nunca en la
  cabecera: `generador.py` tiene que seguir siendo importable sin NetworkX.
- Determinismo (§1.4): nada de `set` en un camino que decida orden. Los conjuntos
  valen para pertenencia, no para iterar y ordenar.
- Nombres de prueba en español, comentarios superficiales, justificación en
  `docs/decisiones/`.
- **Commits:** solo si el usuario lo pide (el árbol tiene trabajo previo sin
  commitear).

---

## Estructura de archivos

| Archivo | Responsabilidad | Acción |
|---|---|---|
| `generador_grafo.py` | NER, gazetteer y grafo (del compañero) | mover de `entrega/`, sin tocar |
| `generador.py` | expansión, reordenamiento y sus flags | modificar |
| `requirements.txt` | `networkx` | modificar |
| `tests/test_grafo_en_recuperacion.py` | pruebas de las dos piezas | crear |
| `docs/decisiones/grafo-en-la-recuperacion.md` | la decisión y sus números | crear |

---

## Tarea 1: subir `generador_grafo.py` a la raíz

**Archivos:**
- Mover: `entrega/generador_grafo.py` → `generador_grafo.py`
- Modificar: `requirements.txt`
- Prueba: `tests/test_grafo_en_recuperacion.py` (crear)

**Interfaces:**
- Consume: nada.
- Produce: `generador_grafo` importable desde la raíz, con `extraer_entidades`,
  `GAZETTEER` y `Entidad`. Todas las tareas siguientes dependen de ello.

- [ ] **Paso 1: mover el archivo**

```bash
git mv entrega/generador_grafo.py generador_grafo.py
```

Si `git mv` falla porque el archivo no está versionado, `mv` normal. **No se
edita ni una línea del contenido.**

- [ ] **Paso 2: añadir NetworkX a requirements**

En `requirements.txt` de la raíz, junto al resto de dependencias:

```
networkx>=3.0,<4
```

Y la misma línea en `pyproject.toml`, dentro de `dependencies`, para que las dos
listas no se contradigan:

```python
    "networkx>=3.0,<4",
```

- [ ] **Paso 3: escribir la prueba de humo**

`tests/test_grafo_en_recuperacion.py`, archivo nuevo:

```python
"""Pruebas de la entrada del grafo de conocimiento en la recuperación.

Las dos piezas —expandir la consulta y reordenar el top-k— reciben el
reconocedor y el gazetteer por inyección: ``generador_grafo`` precompila sus
expresiones regulares al importarse, y atar la suite a las 229 entidades reales
la rompería cada vez que el componente bonus crezca.
"""

import pytest

from generador import Candidato


def test_el_componente_del_grafo_se_importa_desde_la_raiz():
    """Vive en la raíz como fuente; entrega/ es la copia, como el resto."""
    from generador_grafo import GAZETTEER, extraer_entidades

    assert len(GAZETTEER) > 100
    assert callable(extraer_entidades)


def test_el_gazetteer_unifica_una_entidad_entre_idiomas():
    """Es la razón de ser de todo esto: la consulta va en español y el corpus
    es 75 % inglés."""
    from generador_grafo import GAZETTEER, extraer_entidades

    entidades = extraer_entidades("el papel de la inteligencia artificial")
    nombres = [e.nombre for e in entidades if e.origen == "gazetteer"]

    assert "inteligencia artificial" in nombres
    assert "artificial intelligence" in GAZETTEER["inteligencia artificial"][1]
```

- [ ] **Paso 4: correr las pruebas**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -v
```

Esperado: 2 passed. Si falla con `ModuleNotFoundError: networkx`, instalar:
`pip install "networkx>=3.0,<4"`.

- [ ] **Paso 5: comprobar que la copia de entrega sigue en pie**

```bash
ls entrega/generador_grafo.py 2>/dev/null || echo "falta la copia en entrega/"
```

Esperado: `falta la copia en entrega/`. Es correcto por ahora — la copia la
repone `scripts/preparar_entrega.py` al preparar el entregable. Anotar que ese
script tendrá que incluir `generador_grafo.py` en su lista.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add generador_grafo.py requirements.txt pyproject.toml tests/test_grafo_en_recuperacion.py
git commit -m "chore: generador_grafo.py pasa a la raíz como fuente"
```

---

## Tarea 2: `expandir_consulta`

**Archivos:**
- Modificar: `generador.py` (junto a `_codificar_consultas`, línea 478)
- Prueba: `tests/test_grafo_en_recuperacion.py`

**Interfaces:**
- Consume: `generador_grafo` de la tarea 1.
- Produce:
  `expandir_consulta(texto: str, reconocer=None, gazetteer=None) -> str`.
  La tarea 3 la conecta al camino de codificación.

- [ ] **Paso 1: escribir las pruebas que fallan**

Añadir a `tests/test_grafo_en_recuperacion.py`. El doble del reconocedor imita lo
justo de la dataclass `Entidad`: `nombre` y `origen`.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EntidadFalsa:
    nombre: str
    origen: str = "gazetteer"


GAZETTEER_PRUEBA = {
    "inteligencia artificial": ("TEMA", ["inteligencia artificial", "artificial intelligence", "AI"]),
    "china": ("PAIS", ["china", "República Popular China"]),
}


def reconocedor(*nombres):
    """Un reconocedor que siempre devuelve las entidades dadas."""
    def reconocer(texto):
        return list(nombres)
    return reconocer


def test_la_consulta_gana_las_variantes_de_su_entidad():
    expandida = expandir_consulta(
        "el papel de la inteligencia artificial",
        reconocer=reconocedor(EntidadFalsa("inteligencia artificial")),
        gazetteer=GAZETTEER_PRUEBA,
    )

    assert expandida == "el papel de la inteligencia artificial artificial intelligence AI"


def test_una_variante_ya_presente_no_se_repite():
    """'inteligencia artificial' ya está en el texto: añadirla otra vez solo
    sesga el vector hacia un término que la consulta ya tenía."""
    expandida = expandir_consulta(
        "inteligencia artificial",
        reconocer=reconocedor(EntidadFalsa("inteligencia artificial")),
        gazetteer=GAZETTEER_PRUEBA,
    )

    assert expandida.count("inteligencia artificial") == 1


def test_sin_entidades_la_consulta_vuelve_intacta():
    texto = "una pregunta sin ninguna entidad conocida"

    assert expandir_consulta(texto, reconocer=reconocedor(), gazetteer=GAZETTEER_PRUEBA) == texto


def test_una_entidad_heuristica_no_aporta_variantes():
    """Las de origen 'sigla' y 'capitalizada' no están en el gazetteer: no
    tienen variantes multilingües que ofrecer."""
    texto = "amenazas NBQR"
    expandida = expandir_consulta(
        texto,
        reconocer=reconocedor(EntidadFalsa("NBQR", origen="sigla")),
        gazetteer=GAZETTEER_PRUEBA,
    )

    assert expandida == texto


def test_la_expansion_es_estable_entre_llamadas():
    """El texto que se codifica tiene que ser idéntico entre corridas (§1.4)."""
    entidades = reconocedor(
        EntidadFalsa("inteligencia artificial"), EntidadFalsa("china")
    )
    argumentos = {"reconocer": entidades, "gazetteer": GAZETTEER_PRUEBA}

    primera = expandir_consulta("pregunta", **argumentos)
    segunda = expandir_consulta("pregunta", **argumentos)

    assert primera == segunda
    assert primera.endswith("artificial intelligence AI china República Popular China")


def test_dos_entidades_aportan_sus_variantes_en_orden():
    expandida = expandir_consulta(
        "sobre china",
        reconocer=reconocedor(EntidadFalsa("china"), EntidadFalsa("inteligencia artificial")),
        gazetteer=GAZETTEER_PRUEBA,
    )

    assert expandida == (
        "sobre china República Popular China inteligencia artificial "
        "artificial intelligence AI"
    )
```

Añadir `expandir_consulta` al import de `generador`.

- [ ] **Paso 2: correr y verificar que fallan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -k expan -v
```

Esperado: `ImportError: cannot import name 'expandir_consulta'`.

- [ ] **Paso 3: implementar**

En `generador.py`, justo antes de `_codificar_consultas` (línea 478):

```python
def expandir_consulta(
    texto: str,
    reconocer: Callable[[str], list[Any]] | None = None,
    gazetteer: dict[str, tuple[str, list[str]]] | None = None,
) -> str:
    """El texto con las variantes multilingües de sus entidades del gazetteer.

    Las consultas vienen en español y el corpus es 75 % inglés: esto le da al
    encoder los términos ingleses sin depender de que el vector salve el salto.
    """
    if reconocer is None or gazetteer is None:
        # Perezoso: construir el índice no puede exigir NetworkX instalado.
        from generador_grafo import GAZETTEER, extraer_entidades

        reconocer = reconocer or extraer_entidades
        gazetteer = GAZETTEER if gazetteer is None else gazetteer

    minuscula = texto.casefold()
    variantes: list[str] = []

    for entidad in reconocer(texto):
        if entidad.origen != "gazetteer":
            continue
        entrada = gazetteer.get(entidad.nombre)
        if entrada is None:
            continue
        for variante in entrada[1]:
            if variante.casefold() in minuscula or variante in variantes:
                continue
            variantes.append(variante)

    return f"{texto} {' '.join(variantes)}" if variantes else texto
```

La lista y no un `set` es deliberado: el orden decide el texto que se codifica, y
un `set` lo haría variar entre corridas.

- [ ] **Paso 4: correr y verificar que pasan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -v
```

Esperado: 8 passed.

- [ ] **Paso 5: comprobar contra las consultas reales**

```bash
python -c "
import sys; sys.path.insert(0,'.')
from generador import cargar_consultas, expandir_consulta
qs = cargar_consultas('base_documental/Extracto_Preguntas_50_v2.pdf')
n = sum(1 for q in qs if expandir_consulta(q.texto) != q.texto)
print(f'{n} de {len(qs)} consultas se expanden')
print(expandir_consulta(qs[0].texto)[-90:])
"
```

Esperado: `40 de 50 consultas se expanden`. Si sale muy por debajo, el gazetteer
no está cargando y hay que parar antes de seguir.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add generador.py tests/test_grafo_en_recuperacion.py
git commit -m "feat: expansión de consulta con el gazetteer multilingüe"
```

---

## Tarea 3: conectar la expansión y su flag

**Archivos:**
- Modificar: `generador.py` — `_codificar_consultas` (478), `responder_consultas`
  (371 y 425), `ReporteConsultas` (356), `_construir_parser` (760), `_responder`
  (852)
- Prueba: `tests/test_grafo_en_recuperacion.py`

**Interfaces:**
- Consume: `expandir_consulta` de la tarea 2.
- Produce: el parámetro `expandir: bool = False` de `responder_consultas`, la
  opción `--expandir-consulta` y el campo `n_consultas_expandidas` del reporte.
  La tarea 6 usa la opción.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_sin_el_flag_la_consulta_se_codifica_tal_cual(indice_de_cuatro, tmp_path):
    """El camino base no cambia mientras el flag esté apagado."""
    textos = []

    responder_consultas(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        tmp_path / "r.jsonl",
        CONFIG_PRUEBA,
        codificar=codificador(textos),
    )

    assert all("artificial intelligence" not in t for t in textos[0])


def test_el_entregable_conserva_la_consulta_original(indice_de_cuatro, tmp_path):
    """Se casa con el identificador y el texto de ADL, no con nuestra expansión."""
    destino = tmp_path / "r.jsonl"

    responder_consultas(
        indice_de_cuatro,
        [Consulta("q001", "la inteligencia artificial")],
        destino,
        CONFIG_PRUEBA,
        codificar=codificador(),
        expandir=True,
    )

    assert leer_resultados(destino)[0]["consulta"] == "la inteligencia artificial"


def test_el_reporte_cuenta_las_consultas_expandidas(indice_de_cuatro, tmp_path):
    reporte = responder_consultas(
        indice_de_cuatro,
        [Consulta("q001", "la inteligencia artificial"), Consulta("q002", "zzz qqq")],
        tmp_path / "r.jsonl",
        CONFIG_PRUEBA,
        codificar=codificador(),
        expandir=True,
    )

    assert reporte.n_consultas_expandidas == 1


def test_la_cli_acepta_la_expansion():
    parser = _construir_parser()

    assert parser.parse_args([]).expandir_consulta is False
    assert parser.parse_args(["--expandir-consulta"]).expandir_consulta is True
```

Estas pruebas necesitan los helpers de `tests/test_generador.py`
(`codificador`, `indice_de_cuatro`, `leer_resultados`, `CONFIG_PRUEBA`). Moverlos
a `tests/conftest.py` como fixtures compartidas **no** entra aquí: se importan
desde el módulo de prueba, que es lo que ya hace `test_generador.py` con
`conftest.bloque`:

```python
from test_generador import CONFIG_PRUEBA, codificador, indice_de_cuatro, leer_resultados  # noqa: F401
```

- [ ] **Paso 2: correr y verificar que fallan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -k "flag or original or expandidas or cli" -v
```

Esperado: `TypeError: responder_consultas() got an unexpected keyword argument
'expandir'`.

- [ ] **Paso 3: expandir dentro de `_codificar_consultas`**

Sustituir la función completa (línea 478):

```python
def _codificar_consultas(
    consultas: list[Consulta],
    codificar: Callable[[list[str]], np.ndarray],
    config: ConfigEncoder,
    expandir: bool = False,
) -> tuple[np.ndarray, int]:
    """Vectores de consulta normalizados, y cuántas se expandieron.

    Se agrupa solo por ``config.lote``, sin presupuesto de atención: una consulta
    son dos líneas y el cuadrado de la longitud aquí no llega a apretar.
    """
    crudos_texto = [expandir_consulta(c.texto) if expandir else c.texto for c in consultas]
    expandidas = sum(
        1 for original, final in zip(consultas, crudos_texto) if original.texto != final
    )

    textos = [texto_de_consulta(t, config) for t in crudos_texto]
    crudos = np.vstack(
        [codificar(textos[i : i + config.lote]) for i in range(0, len(textos), config.lote)]
    )
    return normalizar(truncar_dimension(crudos, config.dimension)), expandidas
```

En `responder_consultas`, la llamada (línea 425) pasa a:

```python
    vectores, n_expandidas = _codificar_consultas(pedidos, codificar, config, expandir)
```

Y la firma gana el parámetro, tras `idioma`:

```python
    expandir: bool = False,
```

- [ ] **Paso 4: ampliar el reporte**

En `ReporteConsultas`, tras los campos que añadió el plan anterior:

```python
    n_consultas_expandidas: int = 0
```

Y en el `return ReporteConsultas(...)`:

```python
        n_consultas_expandidas=n_expandidas,
```

- [ ] **Paso 5: la CLI**

En `_construir_parser`, tras `--top-fragmentos`:

```python
    parser.add_argument(
        "--expandir-consulta",
        action="store_true",
        help="añade a la consulta las variantes multilingües de sus entidades",
    )
```

En `_responder`, en la llamada:

```python
        expandir=args.expandir_consulta,
```

Y tras la línea del modelo:

```python
    if reporte.n_consultas_expandidas:
        print(
            f"  expansión:     {reporte.n_consultas_expandidas} consultas "
            f"ampliadas con variantes del gazetteer",
            file=sys.stderr,
        )
```

- [ ] **Paso 6: correr la suite entera**

```bash
python -m pytest
```

Esperado: todo verde. `_codificar_consultas` cambió de tipo de retorno; si alguna
prueba existente la llamaba directamente, ajustarla — una búsqueda de
`_codificar_consultas` en `tests/` lo confirma en segundos.

- [ ] **Paso 7: commit** *(condicional)*

```bash
git add generador.py tests/test_grafo_en_recuperacion.py
git commit -m "feat: --expandir-consulta"
```

---

## Tarea 4: `score_denso` y `reordenar_por_entidades`

**Archivos:**
- Modificar: `generador.py` — `Candidato` (336), `Recuperado` (345),
  `agregar_por_documento` (526), función nueva junto a ella
- Prueba: `tests/test_grafo_en_recuperacion.py`

**Interfaces:**
- Consume: `Candidato` y `generador_grafo`.
- Produce: `Candidato.score_denso: float | None = None`,
  `Recuperado.score_denso: float | None = None`, `K_RRF = 60` y
  `reordenar_por_entidades(candidatos, entidades, k_rrf=K_RRF, reconocer=None)
  -> list[Candidato]`. La tarea 5 los conecta.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def candidato_con(fila, score, texto):
    return Candidato(fila=fila, score=score, metadata={"doc_id": f"D{fila}", "texto": texto})


def reconocedor_por_texto(texto):
    """Reconoce cada palabra del fragmento como una entidad."""
    return [EntidadFalsa(palabra) for palabra in texto.split()]


def test_el_fragmento_con_mas_entidades_compartidas_sube():
    """Es la señal que el vector diluye: coincidencia exacta de entidades."""
    candidatos = [
        candidato_con(0, 0.90, "nada que ver"),
        candidato_con(1, 0.89, "china satélite órbita"),
    ]

    ordenados = reordenar_por_entidades(
        candidatos, {"china", "satélite", "órbita"}, reconocer=reconocedor_por_texto
    )

    assert [c.fila for c in ordenados] == [1, 0]


def test_sin_entidades_en_la_consulta_no_se_reordena():
    candidatos = [candidato_con(0, 0.9, "uno"), candidato_con(1, 0.8, "dos")]

    assert reordenar_por_entidades(candidatos, set(), reconocer=reconocedor_por_texto) == candidatos


def test_el_coseno_original_se_conserva_en_score_denso():
    """El score pasa a ser el de la fusión; sin guardar el coseno se pierde de
    qué se partía."""
    candidatos = [candidato_con(0, 0.93, "china"), candidato_con(1, 0.88, "otra cosa")]

    ordenados = reordenar_por_entidades(
        candidatos, {"china"}, reconocer=reconocedor_por_texto
    )

    assert ordenados[0].score_denso == pytest.approx(0.93)
    assert ordenados[0].score != pytest.approx(0.93)


def test_el_empate_de_solape_se_rompe_por_el_orden_denso():
    """Sin desempate estable, dos corridas ordenarían distinto (§1.4)."""
    candidatos = [
        candidato_con(0, 0.90, "china"),
        candidato_con(1, 0.85, "china"),
        candidato_con(2, 0.80, "china"),
    ]

    ordenados = reordenar_por_entidades(
        candidatos, {"china"}, reconocer=reconocedor_por_texto
    )

    assert [c.fila for c in ordenados] == [0, 1, 2]


def test_reordenar_no_pierde_ni_anade_candidatos():
    candidatos = [candidato_con(n, 0.9 - n / 100, f"texto {n}") for n in range(10)]

    ordenados = reordenar_por_entidades(
        candidatos, {"texto"}, reconocer=reconocedor_por_texto
    )

    assert sorted(c.fila for c in ordenados) == list(range(10))


def test_un_candidato_muy_abajo_con_mucho_solape_remonta():
    """RRF: el puesto 40 del denso con solape máximo adelanta al puesto 3 sin
    solape, que es justo lo que se busca de la fusión."""
    candidatos = [candidato_con(n, 0.9 - n / 100, "nada") for n in range(40)]
    candidatos.append(candidato_con(40, 0.5, "china satélite"))

    ordenados = reordenar_por_entidades(
        candidatos, {"china", "satélite"}, reconocer=reconocedor_por_texto
    )

    assert ordenados[0].fila == 40
```

- [ ] **Paso 2: correr y verificar que fallan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -k "solape or reordenar or coseno or remonta" -v
```

Esperado: `ImportError: cannot import name 'reordenar_por_entidades'`.

- [ ] **Paso 3: ampliar las dataclasses**

En `Candidato` (línea 336) y `Recuperado` (345), un campo al final de cada una:

```python
@dataclass(frozen=True)
class Candidato:
    """Un fragmento del top-k, antes de agregar a documento."""

    fila: int  # fila del índice, que es la línea de metadata.jsonl
    score: float
    metadata: dict[str, Any]
    # El coseno, cuando ``score`` ya es el de una fusión y no un coseno.
    score_denso: float | None = None
```

```python
@dataclass(frozen=True)
class Recuperado:
    """Un documento del top-3, con el fragmento que lo puso ahí."""

    doc_id: str
    score: float
    n_fragmentos: int  # diagnóstico, no criterio: el puesto lo da el mejor
    metadata: dict[str, Any]
    score_denso: float | None = None
```

Y en `agregar_por_documento`, al construir el `Recuperado`, propagarlo:

```python
        Recuperado(
            doc_id=doc_id,
            score=candidato.score,
            n_fragmentos=conteo[doc_id],
            metadata=candidato.metadata,
            score_denso=candidato.score_denso,
        )
```

- [ ] **Paso 4: implementar el reordenamiento**

En `generador.py`, junto a `TOP_FRAGMENTOS`:

```python
# Constante estándar de Reciprocal Rank Fusion. No se ajusta contra el ground
# truth: calibrarla con las mismas 50 consultas con las que se mide es
# sobreajuste, y el número dejaría de poder citarse.
K_RRF = 60
```

Y la función, justo antes de `agregar_por_documento`:

```python
def reordenar_por_entidades(
    candidatos: list[Candidato],
    entidades: set[str],
    k_rrf: int = K_RRF,
    reconocer: Callable[[str], list[Any]] | None = None,
) -> list[Candidato]:
    """Funde el orden denso con el de solape de entidades, por RRF.

    El score pasa a ser el de la fusión y el coseno se guarda en ``score_denso``:
    sin sustituirlo, ``agregar_por_documento`` volvería a ordenar por coseno y
    desharía el reordenamiento.
    """
    if not entidades or not candidatos:
        return candidatos

    if reconocer is None:
        from generador_grafo import extraer_entidades

        reconocer = extraer_entidades

    solapes = [
        len(entidades & {e.nombre for e in reconocer(str(c.metadata.get("texto", "")))})
        for c in candidatos
    ]

    # Empates rotos por la posición densa: el solape empata mucho y sin esto
    # dos corridas del mismo índice ordenarían distinto.
    por_solape = sorted(range(len(candidatos)), key=lambda i: (-solapes[i], i))
    rango_entidades = {posicion: rango for rango, posicion in enumerate(por_solape)}

    fundidos = sorted(
        (
            -(1 / (k_rrf + denso + 1) + 1 / (k_rrf + rango_entidades[denso] + 1)),
            denso,
        )
        for denso in range(len(candidatos))
    )

    return [
        Candidato(
            fila=candidatos[posicion].fila,
            score=-puntuacion,
            metadata=candidatos[posicion].metadata,
            score_denso=(
                candidatos[posicion].score
                if candidatos[posicion].score_denso is None
                else candidatos[posicion].score_denso
            ),
        )
        for puntuacion, posicion in fundidos
    ]
```

- [ ] **Paso 5: correr y verificar que pasan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -v
```

Esperado: 14 passed.

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add generador.py tests/test_grafo_en_recuperacion.py
git commit -m "feat: reordenamiento del top-k por solape de entidades (RRF)"
```

---

## Tarea 5: conectar el reordenamiento y su flag

**Archivos:**
- Modificar: `generador.py` — `responder_consultas` (bucle, 437),
  `registro_de_resultado` (557), `ReporteConsultas` (356), parser y `_responder`
- Prueba: `tests/test_grafo_en_recuperacion.py`

**Interfaces:**
- Consume: `reordenar_por_entidades` y `score_denso` de la tarea 4.
- Produce: el parámetro `reordenar: bool = False`, la opción
  `--reordenar-entidades`, el campo `n_reordenadas` y el `score_denso` del
  entregable. La tarea 6 usa la opción.

- [ ] **Paso 1: escribir las pruebas que fallan**

```python
def test_sin_el_flag_el_entregable_es_identico(indice_de_cuatro, tmp_path):
    """La prueba de que nada de esto toca el camino base."""
    con = tmp_path / "con.jsonl"
    sin = tmp_path / "sin.jsonl"
    consultas = [Consulta("q001", "alfa"), Consulta("q002", "beta")]

    responder_consultas(indice_de_cuatro, consultas, sin, CONFIG_PRUEBA,
                        codificar=codificador(), reordenar=False)
    responder_consultas(indice_de_cuatro, consultas, con, CONFIG_PRUEBA,
                        codificar=codificador(), reordenar=False)

    assert sin.read_text(encoding="utf-8") == con.read_text(encoding="utf-8")


def test_con_el_flag_el_entregable_lleva_el_coseno_aparte(indice_de_cuatro, tmp_path):
    """Con fusión, 'score' ya no es un coseno: el coseno viaja en score_denso."""
    destino = tmp_path / "r.jsonl"

    responder_consultas(
        indice_de_cuatro,
        [Consulta("q001", "alfa")],
        destino,
        CONFIG_PRUEBA,
        codificar=codificador(),
        reordenar=True,
    )

    primero = leer_resultados(destino)[0]["documentos"][0]
    assert primero["score_denso"] is not None


def test_la_cli_acepta_el_reordenamiento():
    parser = _construir_parser()

    assert parser.parse_args([]).reordenar_entidades is False
    assert parser.parse_args(["--reordenar-entidades"]).reordenar_entidades is True
```

- [ ] **Paso 2: correr y verificar que fallan**

```bash
python -m pytest tests/test_grafo_en_recuperacion.py -k "identico or coseno_aparte or cli_acepta_el_reor" -v
```

Esperado: `TypeError: responder_consultas() got an unexpected keyword argument
'reordenar'`.

- [ ] **Paso 3: conectar en el bucle de consultas**

En `responder_consultas`, la firma gana `reordenar: bool = False` tras `expandir`.
En el bucle, entre la dedup y `mejores_fragmentos`:

```python
            candidatos = filtrar_por_idioma(candidatos, idioma)
            candidatos, repetidos = deduplicar_por_texto(candidatos)
            descartados += repetidos

            if reordenar:
                entidades = {e.nombre for e in _reconocer(consulta.texto)}
                if entidades:
                    candidatos = reordenar_por_entidades(candidatos, entidades)
                    n_reordenadas += 1

            fragmentos = mejores_fragmentos(candidatos, top_fragmentos)
```

Con el contador declarado junto a `descartados` (`n_reordenadas = 0`) y un ayudante
perezoso junto a `expandir_consulta`:

```python
def _reconocer(texto: str) -> list[Any]:
    """Entidades del texto, con el reconocedor del componente del grafo."""
    from generador_grafo import extraer_entidades

    return extraer_entidades(texto)
```

Reordenar **antes** del corte a `top_fragmentos`: hacerlo después solo barajaría
los diez que ya eligió el orden denso.

- [ ] **Paso 4: emitir `score_denso` en el entregable**

En `registro_de_resultado`, añadir la clave a cada documento y a cada fragmento,
justo detrás de `score`:

```python
                "score": round(documento.score, 6),
                "score_denso": (
                    None if documento.score_denso is None
                    else round(documento.score_denso, 6)
                ),
```

Y en el bloque de `fragmentos`:

```python
                "score": round(fragmento.score, 6),
                "score_denso": (
                    None if fragmento.score_denso is None
                    else round(fragmento.score_denso, 6)
                ),
```

- [ ] **Paso 5: el reporte y la CLI**

`ReporteConsultas` gana `n_reordenadas: int = 0`, y el `return` lo pasa. En el
parser, tras `--expandir-consulta`:

```python
    parser.add_argument(
        "--reordenar-entidades",
        action="store_true",
        help="funde el orden denso con el solape de entidades por RRF",
    )
```

En `_responder`, `reordenar=args.reordenar_entidades` en la llamada, y el aviso:

```python
    if reporte.n_reordenadas:
        print(
            f"  reordenadas:   {reporte.n_reordenadas} consultas con fusión RRF; "
            f"'score' ya no es un coseno, el coseno va en 'score_denso'",
            file=sys.stderr,
        )
```

- [ ] **Paso 6: correr la suite entera**

```bash
python -m pytest
```

Esperado: todo verde. Atención a las pruebas del plan anterior que comprueban el
contenido de `documentos[]`: ahora llevan `score_denso: null` cuando no se
reordena. Si alguna compara el dict completo, hay que actualizarla —y eso es
correcto, el esquema cambió—.

- [ ] **Paso 7: commit** *(condicional)*

```bash
git add generador.py tests/test_grafo_en_recuperacion.py
git commit -m "feat: --reordenar-entidades"
```

---

## Tarea 6: las cuatro corridas

**Archivos:** ninguno de código. Produce los números que deciden qué queda
encendido.

**Requisitos:** índice y encoder. Las corridas con `--expandir-consulta`
re-codifican las 50 consultas (segundos de GPU); las otras dos no tocan el
encoder más que para eso mismo. **Ninguna reconstruye el índice.**

- [ ] **Paso 1: correr las cuatro combinaciones**

```bash
BASE="python generador.py --indice indice --consultas base_documental/Extracto_Preguntas_50_v2.pdf --top-fragmentos 10"

$BASE --resultados /tmp/base.jsonl
$BASE --resultados /tmp/expansion.jsonl  --expandir-consulta
$BASE --resultados /tmp/reorden.jsonl    --reordenar-entidades
$BASE --resultados /tmp/ambas.jsonl      --expandir-consulta --reordenar-entidades
```

En Windows, usar el directorio de trabajo temporal en lugar de `/tmp`.

- [ ] **Paso 2: medir las cuatro**

```bash
for r in base expansion reorden ambas; do
  echo "== $r"
  python scripts/evaluar.py --resultados /tmp/$r.jsonl --ground ground/ground_truth.json
done
```

Anotar la tabla completa: F1@3, techo, NDCG@10 binario y graduado de cada una.

- [ ] **Paso 3: decidir**

Gana la mejor en NDCG@10 binario **salvo que hunda el F1@3**. Si ninguna mejora
la base, los flags se quedan apagados: un resultado negativo medido es un
resultado, y se escribe igual.

- [ ] **Paso 4: dejar el entregable con la combinación ganadora**

```bash
python generador.py --indice indice \
    --consultas base_documental/Extracto_Preguntas_50_v2.pdf \
    --resultados entrega/resultados.jsonl \
    --top-fragmentos 10 <flags ganadores>
```

- [ ] **Paso 5: verificar el piso**

```bash
python scripts/evaluar.py --resultados entrega/resultados.jsonl \
    --ground ground/ground_truth.json
```

Esperado: los mismos números que la corrida ganadora del paso 2.

---

## Tarea 7: la bitácora y la sincronización de `entrega/`

**Archivos:**
- Crear: `docs/decisiones/grafo-en-la-recuperacion.md`
- Modificar: `README.md`, `scripts/preparar_entrega.py`

- [ ] **Paso 1: escribir el doc de decisión**

`docs/decisiones/grafo-en-la-recuperacion.md`, con: por qué el gazetteer y no el
GraphML (las cifras de aristas y el ejemplo de `Irán -[lanza]-> satélite` del
README del componente); la cobertura medida (40/50, 9 variantes, 0,014 s); por
qué RRF y no un peso calibrado; el problema del score de §5.1 del spec y cómo se
resolvió; el riesgo de homogeneización (26 expansiones distintas sobre 50); y
**la tabla de las cuatro corridas**, incluida la combinación descartada.

- [ ] **Paso 2: añadir `generador_grafo.py` a la preparación de la entrega**

En `scripts/preparar_entrega.py`, incluir `generador_grafo.py` en la lista de
módulos que se copian a `entrega/`, junto a `contrato.py` y los demás.

- [ ] **Paso 3: resincronizar `entrega/`**

Los seis módulos copiados están desincronizados con la raíz desde la poda de
comentarios. Correr la preparación y comprobar:

```bash
for f in contrato.py encoder.py fragmentador.py generador.py limpieza.py segmentador.py generador_grafo.py; do
  diff -q "$f" "entrega/$f" >/dev/null 2>&1 && echo "OK       $f" || echo "DISTINTO $f"
done
```

Esperado: siete `OK`.

- [ ] **Paso 4: actualizar el README**

Las dos opciones nuevas en la tabla de `generador.py`, el grafo en la sección de
estructura, y `grafo-en-la-recuperacion.md` en la tabla de «Dónde está el porqué».

- [ ] **Paso 5: correr la suite completa**

```bash
python -m pytest
```

- [ ] **Paso 6: commit** *(condicional)*

```bash
git add README.md docs/ scripts/preparar_entrega.py entrega/
git commit -m "docs: el grafo en la recuperación, con sus números"
```

---

## Auto-revisión del plan

**Cobertura del spec:**

| Sección del spec | Tarea |
|---|---|
| §3 dónde vive el código, import perezoso | 1, 2 |
| §4 expansión de la consulta | 2, 3 |
| §4.1 riesgo de homogeneización | 6 (se mide), 7 (se documenta) |
| §5 reordenamiento y RRF | 4 |
| §5.1 el score que se emite | 4 (dataclasses), 5 (entregable) |
| §5.2 dónde entra en el camino | 5 |
| §6 las cuatro combinaciones | 6 |
| §7 lo que se reporta | 3 (`n_consultas_expandidas`), 5 (`n_reordenadas`) |
| §8 pruebas e inyección | 2, 4 |
| §9 bitácora | 7 |

Sin huecos.

**Consistencia de nombres:** `expandir_consulta`, `reordenar_por_entidades`,
`K_RRF`, `score_denso`, `_reconocer`, `expandir`, `reordenar`,
`n_consultas_expandidas`, `n_reordenadas`, `--expandir-consulta`,
`--reordenar-entidades`. Cada uno se define en una tarea y se usa con la misma
firma en las siguientes.

**Riesgos conocidos:**

1. `_codificar_consultas` cambia de tipo de retorno en la tarea 3 (de `ndarray` a
   tupla). Es interna, pero conviene buscarla en `tests/` antes de tocarla.
2. La tarea 5 añade `score_denso` a todos los documentos del entregable, también
   cuando vale `null`. Cualquier prueba que compare un documento completo como
   dict fallará, y debe actualizarse: el esquema cambió a propósito.
3. La tarea 1 deja `entrega/generador_grafo.py` inexistente hasta la tarea 7. Si
   hubiera que entregar entre medias, falta ese archivo.
