# Adaptación del orquestador al corpus real de ADL — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el orquestador recorra las 1826 entradas del corpus de ADL sin abortar, tomando `doc_id` y `fenomeno` del índice maestro en vez de deducirlos, y reportando toda cobertura incompleta en vez de descartarla en silencio.

**Architecture:** Un módulo nuevo `indice.py` lee `Indice_Datos_Codefest.xlsx` (solo lectura) y devuelve un mapa `ruta_relativa -> EntradaIndice`. El orquestador consulta ese mapa como fuente de verdad de identidad y fenómeno, con respaldo por carpeta y por bandera cuando falta. Las colisiones de nombre pasan de excepción a metadato (`fuente_ambigua`); la única condición que detiene la corrida es un `doc_id` duplicado real.

**Tech Stack:** Python 3.11+, `openpyxl` (read_only), `pytest`, dataclasses frozen.

---

## Global Constraints

Copiadas literalmente de la spec §9 y del README. Aplican a **todas** las tareas.

1. **Nada de modelos generativos**, en ninguna parte.
2. **`fuente` es inmutable.** Siempre `path.name`: nombre exacto del archivo con extensión, sin renombrar ni normalizar. Es el campo de emparejamiento con el jurado.
3. **Determinismo total.** `rglob` se ordena explícitamente; claves de diccionario ordenadas al serializar; nada de `hash()` nativo; nada de iterar `set()` sin ordenar.
4. **Ningún extractor tumba el pipeline.** Un archivo corrupto produce un `Documento` válido con `bloques=[]` y el motivo en `errores`.
5. **Nada de escritura a disco fuera de `orquestador.py`.** `indice.py` solo lee.
6. **No se toca un byte del corpus real** en `c:/Users/jesus/projects/base_documental_codefest/`. Decisión del usuario, 2026-08-02.
7. Salida JSON: `ensure_ascii=False`, `sort_keys=True`, `indent=2`, `newline="\n"`.

## Decisiones tomadas antes de planificar

Tres puntos donde la spec chocaba con el código o con el corpus. Resueltos con el usuario el 2026-08-02:

| Conflicto | Resolución |
|---|---|
| `validar_documento` exige `doc_id == calcular_doc_id(fuente)`, pero la spec pide `doc_id` del índice | **Relajar la invariante** en `contrato.py` (Tarea 1). Sin esto, §7.8 y §8 son incompatibles. |
| 13 archivos en disco con extractor no están en el índice (enunciado, el propio índice, `FASE ORDENADA`, 10 `*_catalogo.json`/`*_registro.json`) | **El índice actúa como filtro** cuando se pasa `--indice`. Manifiesto = 1826 exacto; los 13 se reportan a stderr. No se borra nada. |
| §8 pide "0 archivos fuera del índice" | Inalcanzable: son 13 y no se pueden borrar. El reporte los lista y `main()` no falla por ello. Se documenta como limitación del corpus. |

## Cifras verificadas del índice real

Leídas de `Indice_Datos_Codefest.xlsx` el 2026-08-02. Coinciden al dígito con la spec §0. Úsalas como valores esperados.

| Dato | Valor |
|---|---|
| Documentos totales | 1826 |
| Por fenómeno | F1: 459 · F2: 479 · F3: 888 |
| Extensiones | `.json` 954 · `.pdf` 759 · `.pbf` 73 · `.csv` 26 · `.jpg` 8 · `.xlsx` 4 · `.avif` 1 · `.txt` 1 |
| Observatorios distintos | 20 |
| `DOC_ID` duplicados | 0 (1826/1826 únicos) |
| `ruta_relativa` duplicadas | 0 (1826/1826 únicas) |
| Nombres de archivo duplicados | 59 nombres, 186 filas |
| Entradas del índice sin archivo en disco | 0 |
| Archivos con extractor fuera del índice | 13 |

Hoja: `Inventario de Archivos`. Cabecera exacta:
`Fenómeno | Observatorio | Código Observatorio | DOC_ID | Nombre estandarizado | Carpeta | Tipo`

Raíz del corpus: `c:/Users/jesus/projects/base_documental_codefest`
Carpetas raíz: `F1_IA_y_Capacidades_Estrategicas`, `F2_Seguridad_Entorno_Espacial`, `F3_Dinamicas_Territoriales`

---

## Estructura de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `indice.py` | **Crear** | Leer el xlsx de ADL. `EntradaIndice` + `cargar_indice`. Solo lectura. |
| `extractores/texto.py` | **Crear** | Stub del extractor de texto plano (`.txt`, `.md`). |
| `contrato.py` | Modificar | Relajar la invariante de `doc_id`; añadir `"texto"` a `FORMATOS`. |
| `extractores/imagen.py` | Modificar | Añadir `.avif` a `EXTENSIONES`. |
| `orquestador.py` | Modificar | Identidad desde el índice, colisiones como metadato, fenómeno por precedencia, reporte de cobertura, `--indice`. |
| `fixtures/generar_binarios.py` | Modificar | Generar el xlsx mínimo y los dos homónimos. |
| `tests/test_indice.py` | **Crear** | Pruebas de `cargar_indice`. |
| `tests/test_orquestador.py` | Modificar | Pruebas nuevas + ajustar las 3 que la spec invalida. |
| `tests/conftest.py` | Modificar | Fixtures de rutas nuevas. |
| `requirements.txt`, `pyproject.toml` | Modificar | Declarar `openpyxl`. |

## Tests existentes que esta spec invalida

No son regresiones: la spec los reemplaza explícitamente. Hay que cambiarlos, y el total dejará de ser 106.

| Test | Por qué cambia |
|---|---|
| `test_detecta_colision_de_nombres_entre_subdirectorios` | §2 elimina el `ValueError`. Se reescribe según §7.3. |
| `test_ignora_los_formatos_sin_extractor_registrado` | Usa `notas.txt`; §4 registra `.txt`. Se cambia a `.docx`. |
| `test_el_manifiesto_tiene_una_linea_por_documento` | Compara con `glob("*.html")` de la raíz; los fixtures nuevos añaden subcarpetas y un xlsx. |
| `test_el_manifiesto_tiene_los_campos_del_contrato` | §6 añade `observatorio` y `fuente_ambigua` al manifiesto. |

`test_detecta_doc_id_no_derivado_de_fuente` (`tests/test_contrato.py:58`) **sobrevive sin cambios**: usa `doc_id="0000000000000000"` con `meta={}`, que sigue violando la invariante relajada.

---

# Tarea 1: Relajar el contrato y declarar `openpyxl`

**Files:**
- Modify: `contrato.py:24` (FORMATOS), `contrato.py:199-207` (validar_documento)
- Modify: `requirements.txt`, `pyproject.toml`
- Test: `tests/test_contrato.py`

**Interfaces:**
- Consumes: nada.
- Produces: `contrato.FORMATOS` incluye `"texto"`. `validar_documento` acepta tres formas de `doc_id`: derivado de `fuente`, derivado de `meta["ruta_relativa"]`, o un `DOC_ID` de ADL con forma `F<n>-<CODIGO>-<nnn>`.

- [ ] **Step 1: Escribir los tests que fallan**

Añadir al final de `tests/test_contrato.py`:

```python
# --- doc_id: las tres formas admisibles (spec 2026-08-02) --------------------


def test_admite_doc_id_derivado_de_la_ruta_relativa():
    """En el corpus real dos archivos comparten nombre; la ruta los desambigua."""
    doc = documento_minimo(
        fuente="informe.html",
        doc_id=calcular_doc_id("sub/a/informe.html"),
        meta={"ruta_relativa": "sub/a/informe.html"},
    )
    assert validar_documento(doc) == []


def test_admite_doc_id_del_indice_de_adl():
    doc = documento_minimo(fuente="AIINDEX_reporte.pdf", doc_id="F1-AIINDEX-001")
    assert validar_documento(doc) == []


def test_admite_doc_id_del_indice_con_codigo_alfanumerico():
    doc = documento_minimo(fuente="x.pdf", doc_id="F3-MAPP2-118")
    assert validar_documento(doc) == []


def test_rechaza_doc_id_arbitrario_aunque_haya_ruta_relativa():
    doc = documento_minimo(
        fuente="informe.html",
        doc_id="no-deriva-de-nada",
        meta={"ruta_relativa": "sub/a/informe.html"},
    )
    assert any("doc_id" in v for v in validar_documento(doc))


def test_admite_el_formato_texto():
    assert validar_documento(documento_minimo(formato="texto")) == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

```bash
.venv/Scripts/python.exe -m pytest tests/test_contrato.py -k "admite or rechaza_doc_id_arbitrario" -v
```

Esperado: FAIL. Los tres `admite_*` reportan violaciones de `doc_id`/`formato`.

- [ ] **Step 3: Implementar en `contrato.py`**

Añadir `import re` junto a `import hashlib` (línea 17), y `"texto"` a `FORMATOS`:

```python
FORMATOS: tuple[str, ...] = ("pdf", "html", "json", "csv", "xlsx", "imagen", "pbf", "texto")
```

Añadir antes de `validar_documento` (después de `documento_a_dict`, línea 101):

```python
# DOC_ID del índice maestro de ADL: "F1-AIINDEX-001", "F3-MAPP2-118".
_DOC_ID_ADL = re.compile(r"^F[123]-[A-Z0-9]+-\d+$")


def _doc_id_es_admisible(doc: Documento) -> bool:
    """Un ``doc_id`` vale si es trazable a algo estable, no si es cualquier cosa.

    Son tres formas, por orden de preferencia del pipeline:

    1. El ``DOC_ID`` que entrega ADL en su índice maestro. Es la identidad
       oficial del documento y la que el jurado puede rastrear.
    2. Derivado de ``meta["ruta_relativa"]``. Necesario porque 59 nombres de
       archivo se repiten en el corpus (186 archivos): derivarlo de ``fuente``
       le daría el mismo ``doc_id`` a documentos distintos y uno sobrescribiría
       al otro.
    3. Derivado de ``fuente``. El caso simple, sin índice y sin colisiones.
    """
    if _DOC_ID_ADL.match(doc.doc_id):
        return True
    if doc.doc_id == calcular_doc_id(doc.fuente):
        return True
    ruta_relativa = doc.meta.get("ruta_relativa") if isinstance(doc.meta, dict) else None
    return isinstance(ruta_relativa, str) and doc.doc_id == calcular_doc_id(ruta_relativa)
```

Reemplazar el bloque `contrato.py:201-207` por:

```python
    if not isinstance(doc.fuente, str) or not doc.fuente.strip():
        violaciones.append("fuente vacía: es el campo de emparejamiento, no puede faltar")
    elif not isinstance(doc.doc_id, str) or not doc.doc_id.strip():
        violaciones.append("doc_id vacío")
    elif not _doc_id_es_admisible(doc):
        violaciones.append(
            f"doc_id {doc.doc_id!r} no es trazable: no es un DOC_ID de ADL, "
            f"ni deriva de fuente {doc.fuente!r} "
            f"(esperado {calcular_doc_id(doc.fuente)!r}), "
            f"ni de meta['ruta_relativa']"
        )
```

- [ ] **Step 4: Declarar `openpyxl`**

En `requirements.txt`, tras la línea de `langdetect`:

```
openpyxl>=3.1,<4            # lectura del índice maestro de ADL (indice.py)
```

En `pyproject.toml`, dentro de `dependencies`:

```toml
dependencies = [
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "langdetect>=1.0.9",
    "openpyxl>=3.1",
]
```

Instalar:

```bash
.venv/Scripts/python.exe -m pip install "openpyxl>=3.1,<4"
```

- [ ] **Step 5: Correr toda la suite**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Esperado: 111 passed. Las 106 previas siguen verdes (incluida `test_detecta_doc_id_no_derivado_de_fuente`) más las 5 nuevas.

- [ ] **Step 6: Commit**

```bash
git add contrato.py tests/test_contrato.py requirements.txt pyproject.toml
git commit -m "feat(contrato): admitir doc_id de ADL y derivado de ruta relativa

El corpus de ADL repite 59 nombres de archivo en 186 archivos, asi que
derivar doc_id de fuente hace que un documento sobrescriba a otro. Se
admiten tres formas trazables: DOC_ID de ADL, derivado de ruta_relativa
y derivado de fuente. Añade el formato texto y declara openpyxl.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Tarea 2: Módulo `indice.py`

**Files:**
- Create: `indice.py`
- Test: `tests/test_indice.py`
- Modify: `fixtures/generar_binarios.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `openpyxl`.
- Produces:
  - `EntradaIndice(doc_id: str, fuente: str, ruta_relativa: str, fenomeno: int, observatorio: str, codigo_observatorio: str, tipo_declarado: str)` — dataclass frozen.
  - `cargar_indice(ruta_xlsx: Path) -> dict[str, EntradaIndice]` — clave `ruta_relativa`, orden de iteración = orden del archivo.
  - `HOJA: str = "Inventario de Archivos"`

- [ ] **Step 1: Generar el xlsx de fixture**

Añadir a `fixtures/generar_binarios.py`. Al principio, junto a `import unicodedata`:

```python
import openpyxl
```

Antes de `def main()`:

```python
# Índice mínimo con la misma forma que el de ADL: mismas columnas, mismo orden,
# un fenómeno distinto por fila y dos filas que comparten nombre de archivo en
# carpetas distintas. Reproduce en pequeño la colisión real del corpus.
CABECERA_INDICE = (
    "Fenómeno",
    "Observatorio",
    "Código Observatorio",
    "DOC_ID",
    "Nombre estandarizado",
    "Carpeta",
    "Tipo",
)

FILAS_INDICE = (
    ("F1", "AI_Index_Stanford", "AIINDEX", "F1-AIINDEX-001", "bien_formado.html", "", "HTML"),
    ("F2", "Secure_World", "SWF", "F2-SWF-001", "informe.html", "colisiones/a", "HTML"),
    ("F2", "Secure_World", "SWF", "F2-SWF-002", "informe.html", "colisiones/b", "HTML"),
    ("F3", "MAPP_OEA", "MAPP", "F3-MAPP-001", "anidado.html", "", "HTML"),
)

HTML_COLISION_A = (
    "<html lang=\"es\"><body><h1>Informe de la carpeta A</h1>"
    "<p>Contenido del primer informe homónimo.</p></body></html>\n"
)

HTML_COLISION_B = (
    "<html lang=\"es\"><body><h1>Informe de la carpeta B</h1>"
    "<p>Contenido del segundo informe homónimo.</p></body></html>\n"
)


def _escribir_indice(destino: Path) -> None:
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Inventario de Archivos"
    hoja.append(list(CABECERA_INDICE))
    for fila in FILAS_INDICE:
        hoja.append(list(fila))
    libro.save(destino)
    libro.close()


def _escribir_colisiones(raiz: Path) -> None:
    for subdir, contenido in (("a", HTML_COLISION_A), ("b", HTML_COLISION_B)):
        carpeta = raiz / subdir
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "informe.html").write_text(contenido, encoding="utf-8", newline="\n")
```

Reemplazar el cuerpo de `main()`:

```python
def main() -> None:
    (AQUI / "malformado.html").write_bytes(MALFORMADO)
    # Se normaliza aquí y no en el literal: así el fixture queda en NFD
    # aunque este archivo fuente se guarde en NFC.
    (AQUI / "nfd.html").write_bytes(unicodedata.normalize("NFD", NFD).encode("utf-8"))
    _escribir_indice(AQUI / "indice_minimo.xlsx")
    _escribir_colisiones(AQUI / "colisiones")
    print("fixtures binarios regenerados en", AQUI)
```

Ejecutar:

```bash
.venv/Scripts/python.exe fixtures/generar_binarios.py
```

Verificar que quedaron `fixtures/indice_minimo.xlsx`, `fixtures/colisiones/a/informe.html`, `fixtures/colisiones/b/informe.html`.

- [ ] **Step 2: Añadir fixtures de pytest**

En `tests/conftest.py`, tras `raiz_proyecto`:

```python
@pytest.fixture(scope="session")
def indice_minimo(dir_fixtures) -> Path:
    """Índice de 4 filas con la misma forma que el de ADL."""
    return dir_fixtures / "indice_minimo.xlsx"
```

- [ ] **Step 3: Escribir los tests que fallan**

Crear `tests/test_indice.py`:

```python
"""Pruebas del lector del índice maestro de ADL."""

import openpyxl
import pytest

from indice import EntradaIndice, cargar_indice


def escribir_xlsx(destino, filas, hoja="Inventario de Archivos"):
    """Construye un xlsx con la cabecera de ADL y las filas dadas."""
    cabecera = [
        "Fenómeno",
        "Observatorio",
        "Código Observatorio",
        "DOC_ID",
        "Nombre estandarizado",
        "Carpeta",
        "Tipo",
    ]
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = hoja
    ws.append(cabecera)
    for fila in filas:
        ws.append(list(fila))
    libro.save(destino)
    libro.close()
    return destino


# --- lectura correcta ---------------------------------------------------------


def test_carga_las_cuatro_entradas_del_fixture(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert len(entradas) == 4


def test_la_clave_es_la_ruta_relativa_no_el_nombre(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert "colisiones/a/informe.html" in entradas
    assert "colisiones/b/informe.html" in entradas
    assert "informe.html" not in entradas


def test_mapea_el_fenomeno_a_entero(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    assert entradas["bien_formado.html"].fenomeno == 1
    assert entradas["colisiones/a/informe.html"].fenomeno == 2
    assert entradas["anidado.html"].fenomeno == 3


def test_la_entrada_lleva_todos_los_campos(indice_minimo):
    entrada = cargar_indice(indice_minimo)["colisiones/a/informe.html"]
    assert entrada == EntradaIndice(
        doc_id="F2-SWF-001",
        fuente="informe.html",
        ruta_relativa="colisiones/a/informe.html",
        fenomeno=2,
        observatorio="Secure_World",
        codigo_observatorio="SWF",
        tipo_declarado="HTML",
    )


def test_una_carpeta_vacia_deja_la_ruta_igual_al_nombre(indice_minimo):
    entrada = cargar_indice(indice_minimo)["bien_formado.html"]
    assert entrada.ruta_relativa == "bien_formado.html"


def test_dos_homonimos_conservan_la_misma_fuente(indice_minimo):
    entradas = cargar_indice(indice_minimo)
    a = entradas["colisiones/a/informe.html"]
    b = entradas["colisiones/b/informe.html"]
    assert a.fuente == b.fuente == "informe.html"
    assert a.doc_id != b.doc_id


# --- normalización de separadores --------------------------------------------


def test_normaliza_separadores_de_windows(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "F1_Carpeta\\sub", "PDF")],
    )
    entradas = cargar_indice(ruta)
    assert "F1_Carpeta/sub/a.pdf" in entradas


def test_ignora_barras_sobrantes_en_la_carpeta(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "/F1_Carpeta/sub/", "PDF")],
    )
    assert "F1_Carpeta/sub/a.pdf" in cargar_indice(ruta)


# --- determinismo -------------------------------------------------------------


def test_el_orden_del_mapa_es_el_del_archivo(tmp_path):
    filas = [
        ("F1", "Obs", "OBS", f"F1-OBS-{n:03d}", f"z{9 - n}.pdf", "carpeta", "PDF")
        for n in range(1, 5)
    ]
    ruta = escribir_xlsx(tmp_path / "i.xlsx", filas)
    esperado = [f"carpeta/z{9 - n}.pdf" for n in range(1, 5)]
    assert list(cargar_indice(ruta)) == esperado


# --- índices inconsistentes: ValueError ---------------------------------------


def test_doc_id_repetido_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            ("F1", "Obs", "OBS", "F1-OBS-001", "b.pdf", "dos", "PDF"),
        ],
    )
    with pytest.raises(ValueError, match="DOC_ID duplicado"):
        cargar_indice(ruta)


def test_ruta_repetida_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            ("F1", "Obs", "OBS", "F1-OBS-002", "a.pdf", "uno", "PDF"),
        ],
    )
    with pytest.raises(ValueError, match="ruta duplicada"):
        cargar_indice(ruta)


def test_fenomeno_invalido_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F9", "Obs", "OBS", "F9-OBS-001", "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="fenómeno"):
        cargar_indice(ruta)


def test_celda_obligatoria_vacia_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", None, "a.pdf", "uno", "PDF")],
    )
    with pytest.raises(ValueError, match="DOC_ID"):
        cargar_indice(ruta)


def test_hoja_ausente_lanza_value_error(tmp_path):
    ruta = escribir_xlsx(
        tmp_path / "i.xlsx",
        [("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF")],
        hoja="Otra Hoja",
    )
    with pytest.raises(ValueError, match="Inventario de Archivos"):
        cargar_indice(ruta)


def test_columna_ausente_lanza_value_error(tmp_path):
    libro = openpyxl.Workbook()
    ws = libro.active
    ws.title = "Inventario de Archivos"
    ws.append(["Fenómeno", "DOC_ID"])
    ws.append(["F1", "F1-OBS-001"])
    destino = tmp_path / "i.xlsx"
    libro.save(destino)
    libro.close()

    with pytest.raises(ValueError, match="columnas"):
        cargar_indice(destino)


def test_archivo_inexistente_lanza_value_error(tmp_path):
    with pytest.raises(ValueError, match="no existe"):
        cargar_indice(tmp_path / "no_existe.xlsx")


# --- filas en blanco ----------------------------------------------------------


def test_ignora_las_filas_completamente_vacias(tmp_path):
    destino = tmp_path / "i.xlsx"
    escribir_xlsx(
        destino,
        [
            ("F1", "Obs", "OBS", "F1-OBS-001", "a.pdf", "uno", "PDF"),
            (None, None, None, None, None, None, None),
            ("F1", "Obs", "OBS", "F1-OBS-002", "b.pdf", "uno", "PDF"),
        ],
    )
    assert len(cargar_indice(destino)) == 2
```

- [ ] **Step 4: Correr para verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_indice.py -v
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'indice'`.

- [ ] **Step 5: Implementar `indice.py`**

Crear `indice.py`:

```python
"""Lee el índice maestro que entrega ADL con el corpus.

Es la fuente de verdad de la identidad de cada documento: ``DOC_ID`` y
fenómeno vienen de aquí, no de deducirlos del nombre o de la carpeta. Deducirlos
falla contra el corpus real —59 nombres de archivo se repiten y las carpetas no
siguen el patrón que esperaba el orquestador—, así que si ADL ya entrega el dato
correcto, se usa el suyo.

Este módulo **solo lee**. La escritura a disco es exclusiva de
:mod:`orquestador`.

Uso::

    from indice import cargar_indice
    entradas = cargar_indice(Path("Indice_Datos_Codefest.xlsx"))
    entradas["F1_IA_y_Capacidades_Estrategicas/.../informe.pdf"].doc_id
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl

HOJA = "Inventario de Archivos"

_COL_FENOMENO = "Fenómeno"
_COL_OBSERVATORIO = "Observatorio"
_COL_CODIGO = "Código Observatorio"
_COL_DOC_ID = "DOC_ID"
_COL_NOMBRE = "Nombre estandarizado"
_COL_CARPETA = "Carpeta"
_COL_TIPO = "Tipo"

_COLUMNAS: tuple[str, ...] = (
    _COL_FENOMENO,
    _COL_OBSERVATORIO,
    _COL_CODIGO,
    _COL_DOC_ID,
    _COL_NOMBRE,
    _COL_CARPETA,
    _COL_TIPO,
)

# La columna "Carpeta" puede venir vacía si el archivo está en la raíz.
_COLUMNAS_OPCIONALES: frozenset[str] = frozenset({_COL_CARPETA})

_FENOMENOS: dict[str, int] = {"F1": 1, "F2": 2, "F3": 3}


@dataclass(frozen=True)
class EntradaIndice:
    """Una fila del inventario de ADL, ya normalizada."""

    doc_id: str
    """``DOC_ID`` de ADL, tal cual. Forma ``F1-AIINDEX-001``."""

    fuente: str
    """"Nombre estandarizado", sin tocar. Es el nombre exacto del archivo."""

    ruta_relativa: str
    """``Carpeta/Nombre``, POSIX, relativa a la raíz del corpus. Clave del mapa."""

    fenomeno: int
    """1, 2 o 3."""

    observatorio: str
    """P. ej. ``CSET_Georgetown``."""

    codigo_observatorio: str
    """P. ej. ``CSET``."""

    tipo_declarado: str
    """El tipo que declara ADL: ``PDF``, ``JSON``, ``Otro``..."""


def cargar_indice(ruta_xlsx: Path) -> dict[str, EntradaIndice]:
    """Devuelve un mapa ``ruta_relativa -> EntradaIndice``.

    La clave es la ruta y no el nombre de archivo porque el nombre no es único:
    59 nombres se repiten en 186 filas del corpus real. La ruta sí lo es.

    El orden de iteración del mapa es el del archivo, para que dos corridas
    produzcan lo mismo.

    Lanza ``ValueError`` si el índice es inconsistente —``DOC_ID`` o rutas
    repetidas, columnas ausentes, fenómeno fuera de rango—. Un índice
    inconsistente invalida la trazabilidad completa de la entrega, así que sí es
    motivo para detenerse.
    """
    ruta_xlsx = Path(ruta_xlsx)
    if not ruta_xlsx.is_file():
        raise ValueError(f"el índice no existe: {ruta_xlsx}")

    libro = openpyxl.load_workbook(ruta_xlsx, read_only=True, data_only=True)
    try:
        if HOJA not in libro.sheetnames:
            raise ValueError(
                f"el índice no tiene la hoja {HOJA!r}; tiene {libro.sheetnames}"
            )
        return _leer_hoja(libro[HOJA])
    finally:
        libro.close()


def _leer_hoja(hoja) -> dict[str, EntradaIndice]:
    """Recorre la hoja fila a fila.

    No se usa ``ws.max_row``: en modo ``read_only`` no es fiable —cuenta filas
    con formato pero sin datos— así que se itera hasta agotar el generador.
    """
    filas = hoja.iter_rows(values_only=True)
    try:
        cabecera = next(filas)
    except StopIteration:
        raise ValueError(f"la hoja {HOJA!r} está vacía") from None

    posiciones = _posiciones_de_columnas(cabecera)

    entradas: dict[str, EntradaIndice] = {}
    doc_ids: dict[str, str] = {}

    # La cabecera es la fila 1, así que los datos empiezan en la 2.
    for numero, fila in enumerate(filas, start=2):
        if all(celda is None for celda in fila):
            continue

        entrada = _entrada_de_fila(fila, posiciones, numero)

        anterior = entradas.get(entrada.ruta_relativa)
        if anterior is not None:
            raise ValueError(
                f"fila {numero}: ruta duplicada {entrada.ruta_relativa!r} "
                f"(ya la usa {anterior.doc_id}). La ruta es la clave de join "
                f"y debe ser única."
            )

        ruta_previa = doc_ids.get(entrada.doc_id)
        if ruta_previa is not None:
            raise ValueError(
                f"fila {numero}: DOC_ID duplicado {entrada.doc_id!r} "
                f"({ruta_previa} y {entrada.ruta_relativa}). "
                f"Un índice con identidades repetidas invalida la trazabilidad."
            )

        entradas[entrada.ruta_relativa] = entrada
        doc_ids[entrada.doc_id] = entrada.ruta_relativa

    return entradas


def _posiciones_de_columnas(cabecera: tuple) -> dict[str, int]:
    """Mapea nombre de columna a su posición, para no depender del orden."""
    posiciones: dict[str, int] = {}
    for posicion, celda in enumerate(cabecera):
        if celda is None:
            continue
        posiciones[str(celda).strip()] = posicion

    faltan = [columna for columna in _COLUMNAS if columna not in posiciones]
    if faltan:
        raise ValueError(
            f"al índice le faltan columnas: {faltan}. "
            f"Esperadas: {list(_COLUMNAS)}. Encontradas: {sorted(posiciones)}"
        )
    return posiciones


def _celda(fila: tuple, posiciones: dict[str, int], columna: str, numero: int) -> str:
    """Lee una celda como texto, exigiendo que las obligatorias no estén vacías."""
    posicion = posiciones[columna]
    valor = fila[posicion] if posicion < len(fila) else None
    texto = "" if valor is None else str(valor).strip()

    if not texto and columna not in _COLUMNAS_OPCIONALES:
        raise ValueError(f"fila {numero}: la columna {columna!r} está vacía")
    return texto


def _entrada_de_fila(
    fila: tuple, posiciones: dict[str, int], numero: int
) -> EntradaIndice:
    bruto = _celda(fila, posiciones, _COL_FENOMENO, numero).upper()
    if bruto not in _FENOMENOS:
        raise ValueError(
            f"fila {numero}: fenómeno {bruto!r} no es F1, F2 ni F3"
        )

    nombre = _celda(fila, posiciones, _COL_NOMBRE, numero)
    carpeta = _celda(fila, posiciones, _COL_CARPETA, numero)
    # ADL genera el índice en Windows; el pipeline puede correr en Linux.
    carpeta = carpeta.replace("\\", "/").strip("/")

    return EntradaIndice(
        doc_id=_celda(fila, posiciones, _COL_DOC_ID, numero),
        fuente=nombre,
        ruta_relativa=f"{carpeta}/{nombre}" if carpeta else nombre,
        fenomeno=_FENOMENOS[bruto],
        observatorio=_celda(fila, posiciones, _COL_OBSERVATORIO, numero),
        codigo_observatorio=_celda(fila, posiciones, _COL_CODIGO, numero),
        tipo_declarado=_celda(fila, posiciones, _COL_TIPO, numero),
    )
```

- [ ] **Step 6: Correr los tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_indice.py -v
```

Esperado: 18 passed.

- [ ] **Step 7: Verificar contra el índice real**

```bash
.venv/Scripts/python.exe -c "
from pathlib import Path
from indice import cargar_indice
import collections
e = cargar_indice(Path(r'c:/Users/jesus/projects/base_documental_codefest/Indice_Datos_Codefest.xlsx'))
print('entradas:', len(e))
print('fenomeno:', dict(sorted(collections.Counter(v.fenomeno for v in e.values()).items())))
print('observatorios:', len({v.observatorio for v in e.values()}))
print('doc_id unicos:', len({v.doc_id for v in e.values()}))
"
```

Esperado exacto:
```
entradas: 1826
fenomeno: {1: 459, 2: 479, 3: 888}
observatorios: 20
doc_id unicos: 1826
```

- [ ] **Step 8: Commit**

```bash
git add indice.py tests/test_indice.py tests/conftest.py fixtures/
git commit -m "feat(indice): leer el indice maestro de ADL

Mapa ruta_relativa -> EntradaIndice desde la hoja 'Inventario de Archivos'.
La clave es la ruta y no el nombre porque 59 nombres se repiten en el
corpus. Falla con ValueError si el indice trae DOC_ID o rutas repetidas.
Verificado contra el indice real: 1826 entradas, F1 459 / F2 479 / F3 888.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Tarea 3: Extractor de texto plano y extensiones que faltaban

**Files:**
- Create: `extractores/texto.py`
- Modify: `extractores/imagen.py:42`, `extractores/__init__.py`
- Modify: `orquestador.py:29` (import), `orquestador.py:36-53` (EXTRACTORES)
- Test: `tests/test_orquestador.py`

**Interfaces:**
- Consumes: `contrato.Documento`, `contrato.FORMATOS` con `"texto"` (Tarea 1).
- Produces: `extractores.texto.extraer(path: Path, fenomeno: int) -> Documento`, `extractores.texto.FORMATO == "texto"`, `EXTENSIONES == (".txt", ".md")`. `EXTRACTORES` de `orquestador` mapea `.txt`, `.md` y `.avif`.

- [ ] **Step 1: Escribir los tests que fallan**

Añadir a `tests/test_orquestador.py`, sección "recorrido y robustez":

```python
def test_el_texto_plano_tiene_extractor_registrado(tmp_path):
    """SWF_full-text.txt es un informe completo, no un residuo del corpus."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "SWF_full-text.txt").write_text("Informe completo.", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["SWF_full-text.txt"]
    assert documentos[0].formato == "texto"
    assert documentos[0].bloques == []
    assert documentos[0].errores != []


def test_el_markdown_tiene_extractor_registrado(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "notas.md").write_text("# Titulo", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.formato for d in documentos] == ["texto"]


def test_avif_se_trata_como_imagen(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "grafico_web.avif").write_bytes(b"\x00\x00\x00 ftypavif")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.formato for d in documentos] == ["imagen"]
    assert documentos[0].errores != []
```

Reemplazar `test_ignora_los_formatos_sin_extractor_registrado` (líneas 165-172), porque `.txt` ya no es una extensión desconocida:

```python
def test_ignora_los_formatos_sin_extractor_registrado(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["pagina.html"]
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orquestador.py -k "texto_plano or markdown or avif" -v
```

Esperado: FAIL — los archivos no producen documentos porque no hay extractor.

- [ ] **Step 3: Crear `extractores/texto.py`**

```python
"""Extractor de texto plano (.txt, .md). STUB: falta implementar.

Estrategia
----------
1. Leer con ``encoding="utf-8"`` y ``errors="strict"``. Si falla, reintentar con
   ``cp1252`` y **registrar en ``meta`` la codificación usada**: un cambio de
   codificación cambia el texto, y sin dejarlo escrito no hay forma de saber
   después por qué dos corridas difieren.
2. Rechazar el archivo entero si trae bytes NUL, igual que hace
   :mod:`extractores.html`. Un NUL en un archivo de texto significa corrupción,
   y un documento truncado que parece válido es peor que ninguno.
3. Partir en párrafos por líneas en blanco, no por salto de línea: el texto
   plano de un informe viene con las líneas cortadas a 80 columnas y partir por
   ``\\n`` trocearía cada frase.
4. En Markdown, reconocer los encabezados ``#``..``######`` como
   ``tipo="titulo"`` con ``nivel`` = número de almohadillas, y mantener la pila
   para el breadcrumb ``ruta``. En ``.txt`` no hay marcado: todo es ``parrafo``,
   salvo lo que descarte :func:`limpieza.es_ruido_estructural`.
5. ``pagina`` siempre ``None`` y ``atomico`` siempre ``False``: el texto plano no
   tiene páginas ni registros indivisibles.

La trampa principal
-------------------
El texto plano extraído de un PDF —que es justo el caso de ``SWF_full-text.txt``
en el corpus— conserva los cortes de página con sus cabeceras y pies repetidos
en medio del cuerpo. Sin pasar :func:`limpieza.lineas_repetidas` usando los
bloques separados por saltos de página como unidades, el índice acaba lleno de
"Secure World Foundation | 12" entre párrafo y párrafo.

Segunda trampa: un ``.md`` puede traer bloques de código con almohadillas al
principio de línea que no son encabezados. Hay que seguir el estado de las
vallas ``\u0060\u0060\u0060`` antes de interpretar un ``#``.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATO = "texto"

EXTENSIONES = (".txt", ".md")


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo de texto plano.

    El orquestador captura el ``NotImplementedError`` y registra el documento
    como fallido, así que dejar el stub así no tumba el pipeline.
    """
    raise NotImplementedError(
        "extractor de texto plano pendiente: ver la estrategia en el docstring"
    )
```

- [ ] **Step 4: Registrar `.avif` en `extractores/imagen.py`**

Reemplazar la línea 42:

```python
# .avif necesita el plugin `pillow-avif-plugin`; si no está instalado, Pillow
# lanza UnidentifiedImageError y el orquestador registra el error en el
# Documento. Un solo archivo del corpus lo usa (F2-SWF-065).
EXTENSIONES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".avif")
```

- [ ] **Step 5: Registrar en `orquestador.py`**

Cambiar el import de la línea 29:

```python
from extractores import html, imagen, json_, pbf, pdf, tabular, texto
```

Y en `EXTRACTORES`, tras la entrada de `.webp`:

```python
    ".webp": (imagen, "imagen"),
    ".avif": (imagen, "imagen"),
    ".pbf": (pbf, "pbf"),
    ".mvt": (pbf, "pbf"),
    ".txt": (texto, "texto"),
    ".md": (texto, "texto"),
```

Actualizar el comentario de `EXTRACTORES` (líneas 34-35):

```python
# Extensión -> (módulo extractor, formato declarado en el contrato).
# Una extensión que no esté aquí se ignora, pero no en silencio: el reporte de
# cobertura de `main()` la lista en stderr con su extensión.
# `.html`/`.htm` se mantienen aunque el corpus de ADL no traiga ninguno.
```

Añadir a `extractores/__init__.py`, tras el párrafo de la implementación de referencia:

```
Formatos registrados: html, pdf, json, tabular (csv/xlsx), imagen (con OCR),
pbf (mapas vectoriales) y texto (txt/md).
```

- [ ] **Step 6: Correr los tests**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Esperado: 114 passed (111 de la Tarea 1 + 3 nuevas; `test_ignora_los_formatos_sin_extractor_registrado` sigue contando como una).

- [ ] **Step 7: Commit**

```bash
git add extractores/ orquestador.py tests/test_orquestador.py
git commit -m "feat(extractores): registrar texto plano y avif

SWF_full-text.txt (F2-SWF-113) es un informe completo y se estaba
descartando sin dejar rastro, igual que el unico .avif del corpus
(F2-SWF-065). Añade el stub de texto plano para .txt/.md y mapea .avif
a imagen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Tarea 4: Identidad por ruta — colisiones sin excepción

**Files:**
- Modify: `orquestador.py` (`procesar_directorio`, `_verificar_colisiones`, `_extraer_documento`, `_documento_fallido`)
- Test: `tests/test_orquestador.py`

**Interfaces:**
- Consumes: `indice.EntradaIndice`, `indice.cargar_indice` (Tarea 2); `contrato._doc_id_es_admisible` vía `validar_documento` (Tarea 1).
- Produces:
  - `_Identidad` — dataclass frozen con `ruta: Path`, `ruta_relativa: str`, `doc_id: str`, `fenomeno: int`, `origen_doc_id: str`, `origen_fenomeno: str`, `observatorio: str | None`, `codigo_observatorio: str | None`, `fuente_ambigua: bool`.
  - `_ruta_relativa(ruta: Path, raiz: Path) -> str`
  - `_verificar_colisiones(rutas: list[Path], raiz: Path) -> dict[str, list[str]]` — ya **no lanza**.
  - `_verificar_doc_ids(identidades: list[_Identidad]) -> None` — lanza `ValueError`.
  - `procesar_directorio(..., indice: dict[str, EntradaIndice] | None = None)`

- [ ] **Step 1: Escribir los tests que fallan**

Reemplazar `test_detecta_colision_de_nombres_entre_subdirectorios` (líneas 199-208) por:

```python
def test_dos_homonimos_producen_dos_documentos_sin_excepcion(tmp_path):
    """En el corpus de ADL 59 nombres se repiten en 186 archivos.

    Son colisiones legítimas —el mismo informe en carpetas por tipo, el mismo
    tile en distintos niveles de zoom—, así que el pipeline no puede morir por
    ellas. La desambiguación se hace por ruta.
    """
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")

    assert len(documentos) == 2
    assert {d.fuente for d in documentos} == {"informe.html"}
    assert len({d.doc_id for d in documentos}) == 2
    assert all(d.meta["fuente_ambigua"] is True for d in documentos)
    assert {d.meta["ruta_relativa"] for d in documentos} == {"a/informe.html", "b/informe.html"}


def test_el_doc_id_de_un_homonimo_deriva_de_la_ruta_no_del_nombre(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    esperados = {calcular_doc_id("a/informe.html"), calcular_doc_id("b/informe.html")}
    assert {d.doc_id for d in documentos} == esperados


def test_dos_homonimos_escriben_dos_json_distintos(tmp_path):
    """El defecto oculto: con doc_id derivado del nombre, uno sobrescribía al otro."""
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")
    salida = tmp_path / "salida"

    documentos = procesar_directorio(entrada, salida)

    assert len(list(salida.glob("*.json"))) == 2
    assert len(cargar_manifiesto(salida / "manifiesto.jsonl")) == 2


def test_un_archivo_sin_homonimos_no_se_marca_ambiguo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "unico.html").write_text("<html><body><p>Solo</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert documentos[0].meta["fuente_ambigua"] is False


def test_todos_los_documentos_llevan_ruta_relativa(tmp_path):
    """No solo los ambiguos: la ruta es trazabilidad de todos."""
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "raiz.html").write_text("<html><body><p>Raiz</p></body></html>", "utf-8")
    (entrada / "sub" / "hoja.html").write_text("<html><body><p>Hoja</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    rutas = {d.fuente: d.meta["ruta_relativa"] for d in documentos}
    assert rutas == {"raiz.html": "raiz.html", "hoja.html": "sub/hoja.html"}


def test_la_colision_se_avisa_por_stderr(tmp_path, capsys):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    procesar_directorio(entrada, tmp_path / "salida")

    err = capsys.readouterr().err
    assert "1 nombre" in err
    assert "2 archivo" in err


def test_la_salida_de_homonimos_cumple_el_contrato(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    for documento in procesar_directorio(entrada, tmp_path / "salida"):
        assert validar_documento(documento) == []
```

Actualizar el import de la cabecera del archivo (línea 13):

```python
from contrato import calcular_doc_id, validar_documento
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orquestador.py -k "homonimo or ambigu or ruta_relativa" -v
```

Esperado: FAIL — `ValueError: colisión de fuente`.

- [ ] **Step 3: Implementar la identidad en `orquestador.py`**

Añadir a los imports de cabecera:

```python
from dataclasses import dataclass, replace

from contrato import Documento, calcular_doc_id, documento_a_dict
from indice import EntradaIndice
```

Añadir tras la definición de `_CARPETA_FENOMENO`:

```python
@dataclass(frozen=True)
class _Identidad:
    """Quién es un archivo, resuelto antes de extraerlo.

    Se calcula para todos los archivos de golpe porque la ambigüedad de un
    nombre solo se sabe mirando el conjunto, y porque el choque de ``doc_id``
    hay que detectarlo antes de escribir el primer JSON, no a mitad.
    """

    ruta: Path
    ruta_relativa: str
    doc_id: str
    fenomeno: int
    origen_doc_id: str
    origen_fenomeno: str
    observatorio: str | None
    codigo_observatorio: str | None
    fuente_ambigua: bool
```

Reemplazar `_verificar_colisiones` (líneas 114-125) por:

```python
def _ruta_relativa(ruta: Path, raiz: Path) -> str:
    """Ruta POSIX relativa a la raíz del corpus. Es la clave de join con el índice."""
    return ruta.relative_to(raiz).as_posix()


def _agrupar_colisiones(rutas: list[Path], raiz: Path) -> dict[str, list[str]]:
    """Nombres de archivo que aparecen en más de una ruta.

    Ya no lanza: en el corpus de ADL hay 59 nombres repartidos en 186 archivos y
    son colisiones legítimas —el mismo informe archivado por tipo, el mismo tile
    en varios niveles de zoom—. Abortar por ellas dejaba el pipeline sin
    procesar nada. Se registran como ``fuente_ambigua`` y se sigue.

    El orden es determinista: ``rutas`` viene ordenada y los dict de Python
    conservan el orden de inserción.
    """
    por_nombre: dict[str, list[str]] = {}
    for ruta in rutas:
        por_nombre.setdefault(ruta.name, []).append(_ruta_relativa(ruta, raiz))
    return {nombre: rs for nombre, rs in por_nombre.items() if len(rs) > 1}


def _verificar_doc_ids(identidades: list[_Identidad]) -> None:
    """Única condición que sigue deteniendo la corrida.

    Un nombre repetido es un problema del corpus y se anota. Un ``doc_id``
    repetido es un problema de identidad: el JSON de un documento sobrescribiría
    al del otro y el manifiesto tendría dos líneas apuntando al mismo archivo.
    """
    vistos: dict[str, str] = {}
    for identidad in identidades:
        anterior = vistos.get(identidad.doc_id)
        if anterior is not None:
            raise ValueError(
                f"doc_id duplicado {identidad.doc_id!r}: lo comparten "
                f"{anterior!r} y {identidad.ruta_relativa!r}. "
                f"Un documento sobrescribiría al otro."
            )
        vistos[identidad.doc_id] = identidad.ruta_relativa
```

Añadir la construcción de identidades:

```python
def _identidad_de(
    ruta: Path,
    raiz: Path,
    indice: dict[str, EntradaIndice] | None,
    ambiguos: set[str],
    fenomeno_por_defecto: int,
) -> _Identidad:
    """Resuelve identidad y fenómeno, del más fiable al menos.

    ``doc_id`` sale del índice de ADL si el archivo está listado. Si no, se
    deriva de la **ruta relativa** y no del nombre: derivarlo del nombre le daba
    el mismo ``doc_id`` a los 7 PDF homónimos de CSET.
    """
    ruta_relativa = _ruta_relativa(ruta, raiz)
    entrada = indice.get(ruta_relativa) if indice else None

    if entrada is not None:
        return _Identidad(
            ruta=ruta,
            ruta_relativa=ruta_relativa,
            doc_id=entrada.doc_id,
            fenomeno=entrada.fenomeno,
            origen_doc_id="indice",
            origen_fenomeno="indice",
            observatorio=entrada.observatorio,
            codigo_observatorio=entrada.codigo_observatorio,
            fuente_ambigua=ruta.name in ambiguos,
        )

    de_carpeta = _fenomeno_de_carpeta(ruta_relativa)
    return _Identidad(
        ruta=ruta,
        ruta_relativa=ruta_relativa,
        doc_id=calcular_doc_id(ruta_relativa),
        fenomeno=de_carpeta if de_carpeta is not None else fenomeno_por_defecto,
        origen_doc_id="derivado",
        origen_fenomeno="carpeta" if de_carpeta is not None else "defecto",
        observatorio=None,
        codigo_observatorio=None,
        fuente_ambigua=ruta.name in ambiguos,
    )


def _meta_de(identidad: _Identidad) -> dict:
    """Metadata que el orquestador añade a la que traiga el extractor.

    ``observatorio`` no es decorativo: sirve de post-filtro y, más adelante, de
    prefijo para enriquecer el texto del chunk antes de codificarlo.
    """
    meta = {
        "ruta_relativa": identidad.ruta_relativa,
        "fuente_ambigua": identidad.fuente_ambigua,
        "origen_doc_id": identidad.origen_doc_id,
        "origen_fenomeno": identidad.origen_fenomeno,
    }
    if identidad.observatorio is not None:
        meta["observatorio"] = identidad.observatorio
        meta["codigo_observatorio"] = identidad.codigo_observatorio
    return meta
```

Reemplazar `_extraer_documento` y `_documento_fallido` (líneas 144-173):

```python
def _extraer_documento(identidad: _Identidad) -> Documento:
    """Invoca al extractor correspondiente, blindando el pipeline.

    Los stubs aún no implementados lanzan ``NotImplementedError``, y un
    extractor nuevo puede tener errores. Ninguno de los dos casos puede detener
    la corrida: se devuelve un documento válido con el motivo en ``errores``.
    """
    modulo, formato = EXTRACTORES[identidad.ruta.suffix.lower()]
    try:
        documento = modulo.extraer(identidad.ruta, identidad.fenomeno)
    except NotImplementedError as exc:
        documento = _documento_fallido(
            identidad, formato, f"extractor de {formato} no implementado: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - blindaje deliberado
        documento = _documento_fallido(
            identidad,
            formato,
            f"fallo del extractor de {formato} ({type(exc).__name__}): {exc}",
        )
    return _con_identidad(documento, identidad)


def _con_identidad(documento: Documento, identidad: _Identidad) -> Documento:
    """Impone la identidad resuelta sobre lo que devolvió el extractor.

    El extractor solo ve su archivo, así que no puede saber su ``DOC_ID`` de ADL
    ni si su nombre choca con otro. ``fuente`` no se toca nunca: es el campo de
    emparejamiento con el jurado.
    """
    meta = dict(documento.meta)
    meta.update(_meta_de(identidad))
    return replace(
        documento, doc_id=identidad.doc_id, fenomeno=identidad.fenomeno, meta=meta
    )


def _documento_fallido(identidad: _Identidad, formato: str, motivo: str) -> Documento:
    """Documento válido que representa una extracción que no se pudo hacer."""
    return Documento(
        doc_id=identidad.doc_id,
        fuente=identidad.ruta.name,
        formato=formato,
        fenomeno=identidad.fenomeno,
        idioma="es",
        bloques=[],
        meta={},
        errores=[motivo],
    )
```

Reescribir `procesar_directorio` (líneas 59-89):

```python
def procesar_directorio(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
) -> list[Documento]:
    """Extrae todos los documentos de ``entrada`` y los escribe en ``salida``.

    Devuelve los documentos ordenados por ``(fuente, ruta_relativa)``, el mismo
    orden en que se escribe el manifiesto. Se desempata por ruta porque hay
    nombres repetidos y ordenar solo por ``fuente`` dejaría su orden relativo a
    merced del sistema de archivos.

    Lanza ``ValueError`` únicamente si dos archivos acaban con el mismo
    ``doc_id``: entonces uno sobrescribiría al otro. Un nombre repetido ya no
    detiene nada, solo se marca en ``meta["fuente_ambigua"]``.
    """
    documentos, _ = procesar_corpus(
        entrada,
        salida,
        fenomeno_por_defecto=fenomeno_por_defecto,
        limpiar=limpiar,
        indice=indice,
    )
    return documentos
```

- [ ] **Step 4: Correr los tests de esta tarea**

Aún fallarán por falta de `procesar_corpus` y `_fenomeno_de_carpeta`, que llegan en las Tareas 5 y 6. Es esperado: se completan allí. Verificar solo que el módulo importa:

```bash
.venv/Scripts/python.exe -c "import orquestador; print('importa')"
```

- [ ] **Step 5: Commit al terminar la Tarea 6**

Esta tarea, la 5 y la 6 tocan la misma función y se commitean juntas al final de la Tarea 6.

---

# Tarea 5: Detección de fenómeno por precedencia

**Files:**
- Modify: `orquestador.py:55-56` (regex), `orquestador.py:128-138` (`_fenomeno_de`)
- Test: `tests/test_orquestador.py`

**Interfaces:**
- Consumes: `_Identidad`, `_ruta_relativa` (Tarea 4).
- Produces: `_fenomeno_de_carpeta(ruta_relativa: str) -> int | None` — `None` si ninguna carpeta lo declara.

- [ ] **Step 1: Escribir los tests que fallan**

Añadir a `tests/test_orquestador.py`:

```python
# --- fenomeno: precedencia indice > carpeta > defecto -------------------------


def test_el_fenomeno_se_infiere_de_la_carpeta_de_adl(tmp_path):
    """Las carpetas reales son F3_Dinamicas_Territoriales, no fenomeno_3."""
    entrada = tmp_path / "entrada"
    (entrada / "F3_Dinamicas_Territoriales").mkdir(parents=True)
    (entrada / "F3_Dinamicas_Territoriales" / "informe.html").write_text(
        "<html><body><p>Contenido del tercer fenomeno</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == 3
    assert documentos[0].meta["origen_fenomeno"] == "carpeta"


@pytest.mark.parametrize(
    "carpeta,esperado",
    [
        ("F1_IA_y_Capacidades_Estrategicas", 1),
        ("F2_Seguridad_Entorno_Espacial", 2),
        ("F3_Dinamicas_Territoriales", 3),
        ("fenomeno_2", 2),
        ("Fenomeno 3", 3),
        ("fenómeno-1", 1),
    ],
)
def test_las_dos_convenciones_de_carpeta_valen(tmp_path, carpeta, esperado):
    entrada = tmp_path / "entrada"
    (entrada / carpeta).mkdir(parents=True)
    (entrada / carpeta / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == esperado


def test_una_carpeta_que_no_declara_fenomeno_cae_al_defecto(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "recursos").mkdir(parents=True)
    (entrada / "recursos" / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=2)
    assert documentos[0].fenomeno == 2
    assert documentos[0].meta["origen_fenomeno"] == "defecto"


def test_una_carpeta_llamada_F4_no_se_confunde_con_un_fenomeno(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "F4_Otra_Cosa").mkdir(parents=True)
    (entrada / "F4_Otra_Cosa" / "informe.html").write_text(
        "<html><body><p>Contenido</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].meta["origen_fenomeno"] == "defecto"
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orquestador.py -k "fenomeno" -v
```

Esperado: FAIL — `F3_Dinamicas_Territoriales` da fenómeno 1.

- [ ] **Step 3: Implementar**

Añadir `PurePosixPath` al import de `pathlib`:

```python
from pathlib import Path, PurePosixPath
```

Reemplazar las líneas 55-56:

```python
# Carpetas raíz del corpus de ADL: "F1_IA_y_Capacidades_Estrategicas",
# "F2_Seguridad_Entorno_Espacial", "F3_Dinamicas_Territoriales".
_CARPETA_FENOMENO_ADL = re.compile(r"^F([123])[_\s\-]", re.IGNORECASE)

# Convención anterior: "fenomeno_2", "fenomeno-3", "Fenomeno 1". Se mantiene por
# si ADL reorganiza el corpus con la nomenclatura que esperaba el orquestador.
_CARPETA_FENOMENO_LEGADO = re.compile(r"^fen[oó]meno[\s_\-]?([123])$", re.IGNORECASE)

_PATRONES_FENOMENO = (_CARPETA_FENOMENO_ADL, _CARPETA_FENOMENO_LEGADO)
```

Reemplazar `_fenomeno_de` (líneas 128-138):

```python
def _fenomeno_de_carpeta(ruta_relativa: str) -> int | None:
    """Fenómeno declarado por alguna carpeta del camino, o ``None``.

    Devuelve ``None`` en vez del valor por defecto para que quien llame pueda
    distinguir "lo dice la carpeta" de "no lo dice nadie" y registrarlo en
    ``origen_fenomeno``. Con la versión anterior los 1367 documentos de F2 y F3
    se etiquetaban como fenómeno 1 en silencio.
    """
    for parte in PurePosixPath(ruta_relativa).parts[:-1]:
        for patron in _PATRONES_FENOMENO:
            coincidencia = patron.match(parte)
            if coincidencia:
                return int(coincidencia.group(1))
    return None
```

- [ ] **Step 4: Continuar en la Tarea 6**

Los tests seguirán fallando hasta que exista `procesar_corpus`. Se cierra ahí.

---

# Tarea 6: `procesar_corpus`, reporte de cobertura y CLI `--indice`

**Files:**
- Modify: `orquestador.py` (`procesar_corpus`, `_listar_*`, `_entrada_de_manifiesto`, `_construir_parser`, `main`)
- Test: `tests/test_orquestador.py`

**Interfaces:**
- Consumes: `_Identidad`, `_agrupar_colisiones`, `_verificar_doc_ids`, `_identidad_de`, `_meta_de`, `_extraer_documento` (Tarea 4); `_fenomeno_de_carpeta` (Tarea 5); `indice.cargar_indice` (Tarea 2).
- Produces:
  - `ReporteCobertura` — dataclass frozen con `sin_extractor: list[str]`, `huerfanos_del_indice: list[str]`, `fuera_del_indice: list[str]`, `por_origen_doc_id: dict[str, int]`, `por_origen_fenomeno: dict[str, int]`, `nombres_ambiguos: int`, `archivos_ambiguos: int`.
  - `procesar_corpus(entrada, salida, fenomeno_por_defecto=1, limpiar=False, indice=None) -> tuple[list[Documento], ReporteCobertura]`
  - CLI: `--indice PATH`.

- [ ] **Step 1: Escribir los tests que fallan**

Añadir a `tests/test_orquestador.py`. Actualizar los imports de cabecera:

```python
from indice import cargar_indice
from orquestador import cargar_manifiesto, procesar_corpus, procesar_directorio
```

```python
# --- indice como fuente de verdad ---------------------------------------------


def test_con_indice_el_doc_id_y_el_fenomeno_salen_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(
        entrada, tmp_path / "salida", fenomeno_por_defecto=1, indice=cargar_indice(indice_minimo)
    )

    documento = documentos[0]
    assert documento.doc_id == "F2-SWF-001"
    assert documento.fenomeno == 2
    assert documento.meta["origen_doc_id"] == "indice"
    assert documento.meta["origen_fenomeno"] == "indice"
    assert documento.meta["observatorio"] == "Secure_World"
    assert documento.meta["codigo_observatorio"] == "SWF"
    assert documento.fuente == "informe.html"


def test_el_indice_gana_a_la_carpeta(tmp_path, indice_minimo):
    """La carpeta dice F1; el indice dice F2. Manda el indice."""
    entrada = tmp_path / "entrada"
    destino = entrada / "F1_IA_y_Capacidades_Estrategicas" / "colisiones" / "a"
    destino.mkdir(parents=True)
    (destino / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")

    indice = {
        "F1_IA_y_Capacidades_Estrategicas/colisiones/a/informe.html": next(
            e for e in cargar_indice(indice_minimo).values() if e.doc_id == "F2-SWF-001"
        )
    }
    documentos = procesar_directorio(entrada, tmp_path / "salida", indice=indice)
    assert documentos[0].fenomeno == 2
    assert documentos[0].meta["origen_fenomeno"] == "indice"


def test_un_archivo_fuera_del_indice_no_se_procesa(tmp_path, indice_minimo):
    """Con indice, el indice filtra: manda ADL sobre lo que haya en disco."""
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "intruso.html").write_text("<html><body><p>No listado</p></body></html>", "utf-8")

    documentos, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    assert [d.fuente for d in documentos] == ["informe.html"]
    assert reporte.fuera_del_indice == ["intruso.html"]


def test_sin_indice_el_pipeline_sigue_funcionando(tmp_path):
    """Las fixtures sinteticas no tienen indice; no puede ser obligatorio."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida", indice=None)
    assert documentos[0].meta["origen_doc_id"] == "derivado"


# --- reporte de cobertura -----------------------------------------------------


def test_el_reporte_lista_los_archivos_sin_extractor(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")
    (entrada / "presentacion.docx").write_bytes(b"PK\x03\x04 falso")

    documentos, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.sin_extractor == ["presentacion.docx"]
    assert [d.fuente for d in documentos] == ["pagina.html"]


def test_el_reporte_lista_las_entradas_huerfanas_del_indice(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "bien_formado.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(
        entrada, tmp_path / "salida", indice=cargar_indice(indice_minimo)
    )

    # El orden es el del archivo xlsx, no el alfabético: F1 bien_formado (en
    # disco), F2 colisiones/a, F2 colisiones/b, F3 anidado.
    assert reporte.huerfanos_del_indice == [
        "colisiones/a/informe.html",
        "colisiones/b/informe.html",
        "anidado.html",
    ]


def test_el_reporte_cuenta_los_origenes(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "F2_Seguridad_Entorno_Espacial").mkdir(parents=True)
    (entrada / "F2_Seguridad_Entorno_Espacial" / "a.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "suelto.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.por_origen_fenomeno == {"carpeta": 1, "defecto": 1}
    assert reporte.por_origen_doc_id == {"derivado": 2}


def test_el_reporte_cuenta_las_fuentes_ambiguas(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    _, reporte = procesar_corpus(entrada, tmp_path / "salida")

    assert reporte.nombres_ambiguos == 1
    assert reporte.archivos_ambiguos == 2


# --- doc_id duplicado: lo unico que sigue deteniendo la corrida ---------------


def test_un_doc_id_duplicado_en_el_indice_detiene_la_corrida(tmp_path):
    from indice import EntradaIndice

    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "uno.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "dos.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    def entrada_con(ruta, fuente):
        return EntradaIndice(
            doc_id="F1-OBS-001",
            fuente=fuente,
            ruta_relativa=ruta,
            fenomeno=1,
            observatorio="Obs",
            codigo_observatorio="OBS",
            tipo_declarado="HTML",
        )

    indice = {
        "a/uno.html": entrada_con("a/uno.html", "uno.html"),
        "b/dos.html": entrada_con("b/dos.html", "dos.html"),
    }

    with pytest.raises(ValueError, match="doc_id duplicado"):
        procesar_directorio(entrada, tmp_path / "salida", indice=indice)


# --- manifiesto ampliado ------------------------------------------------------


def test_el_manifiesto_lleva_observatorio_y_fuente_ambigua(tmp_path, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "b").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    (entrada / "colisiones" / "b" / "informe.html").write_text(
        "<html><body><p>Dos</p></body></html>", "utf-8"
    )
    salida = tmp_path / "salida"

    procesar_directorio(entrada, salida, indice=cargar_indice(indice_minimo))

    entradas = cargar_manifiesto(salida / "manifiesto.jsonl")
    assert all(e["observatorio"] == "Secure_World" for e in entradas)
    assert all(e["fuente_ambigua"] is True for e in entradas)


# --- CLI ----------------------------------------------------------------------


def test_la_cli_acepta_indice(tmp_path, raiz_proyecto, indice_minimo):
    entrada = tmp_path / "entrada"
    (entrada / "colisiones" / "a").mkdir(parents=True)
    (entrada / "colisiones" / "a" / "informe.html").write_text(
        "<html><body><p>Uno</p></body></html>", "utf-8"
    )
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [
            sys.executable, "orquestador.py",
            "--entrada", str(entrada),
            "--salida", str(salida),
            "--indice", str(indice_minimo),
        ],
        cwd=raiz_proyecto, capture_output=True, text=True, encoding="utf-8",
    )

    assert resultado.returncode == 0, resultado.stderr
    entradas = cargar_manifiesto(salida / "manifiesto.jsonl")
    assert [e["doc_id"] for e in entradas] == ["F2-SWF-001"]


def test_la_cli_funciona_sin_indice(tmp_path, raiz_proyecto):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", "utf-8")
    salida = tmp_path / "salida"

    resultado = subprocess.run(
        [sys.executable, "orquestador.py", "--entrada", str(entrada), "--salida", str(salida)],
        cwd=raiz_proyecto, capture_output=True, text=True, encoding="utf-8",
    )
    assert resultado.returncode == 0, resultado.stderr
```

Actualizar `test_el_manifiesto_tiene_los_campos_del_contrato` (líneas 129-142) añadiendo las dos claves nuevas:

```python
def test_el_manifiesto_tiene_los_campos_del_contrato(salida_doble):
    primera, _ = salida_doble
    esperados = {
        "doc_id",
        "fuente",
        "formato",
        "fenomeno",
        "idioma",
        "n_bloques",
        "n_chars",
        "n_errores",
        "observatorio",
        "fuente_ambigua",
    }
    for entrada in cargar_manifiesto(primera / "manifiesto.jsonl"):
        assert set(entrada.keys()) == esperados
```

Reemplazar `test_el_manifiesto_tiene_una_linea_por_documento` (líneas 122-127), porque `fixtures/` ya no es solo HTML en la raíz:

```python
def test_el_manifiesto_tiene_una_linea_por_documento(salida_doble, dir_fixtures):
    from orquestador import EXTRACTORES

    primera, _ = salida_doble
    entradas = cargar_manifiesto(primera / "manifiesto.jsonl")
    con_extractor = [
        p for p in dir_fixtures.rglob("*") if p.is_file() and p.suffix.lower() in EXTRACTORES
    ]
    assert len(entradas) == len(con_extractor)
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_orquestador.py -v
```

Esperado: FAIL con `ImportError: cannot import name 'procesar_corpus'`.

- [ ] **Step 3: Implementar `procesar_corpus` y el reporte**

Añadir tras `_Identidad`:

```python
@dataclass(frozen=True)
class ReporteCobertura:
    """Qué quedó fuera y por qué.

    Existe porque la versión anterior filtraba en silencio: una extensión sin
    extractor desaparecía sin dejar rastro y nadie se enteraba hasta el cierre.
    """

    sin_extractor: list[str]
    """Rutas relativas de archivos en disco cuya extensión no está en ``EXTRACTORES``."""

    huerfanos_del_indice: list[str]
    """Rutas que el índice lista pero que no existen en disco."""

    fuera_del_indice: list[str]
    """Rutas con extractor que están en disco pero el índice no lista."""

    por_origen_doc_id: dict[str, int]
    """Cuántos ``doc_id`` salieron de "indice" y cuántos de "derivado"."""

    por_origen_fenomeno: dict[str, int]
    """Cuántos fenómenos salieron de "indice", "carpeta" y "defecto"."""

    nombres_ambiguos: int
    """Nombres de archivo que aparecen en más de una ruta."""

    archivos_ambiguos: int
    """Archivos afectados por esos nombres."""
```

Reemplazar `_listar_documentos` (líneas 95-107) por:

```python
def _listar_archivos(entrada: Path) -> list[Path]:
    """Todos los archivos del corpus, en orden estable.

    Se ordena explícitamente: el orden de ``rglob`` depende del sistema de
    archivos y bastaría para que dos corridas difieran.
    """
    if not entrada.is_dir():
        raise ValueError(f"el directorio de entrada no existe: {entrada}")

    return sorted(
        (ruta for ruta in entrada.rglob("*") if ruta.is_file()),
        key=lambda ruta: ruta.as_posix(),
    )
```

Añadir `procesar_corpus` justo después de la sección de imports/constantes, sustituyendo el cuerpo antiguo de `procesar_directorio`:

```python
def procesar_corpus(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
) -> tuple[list[Documento], ReporteCobertura]:
    """Como :func:`procesar_directorio`, pero devolviendo también la cobertura.

    Cuando hay índice, el índice manda: solo se procesa lo que ADL lista. Un
    archivo en disco que el índice no menciona no es un documento de la entrega
    —en el corpus real son el enunciado, el propio índice y los catálogos de
    scraping—, así que se reporta y no se procesa.
    """
    entrada = Path(entrada)
    salida = Path(salida)

    archivos = _listar_archivos(entrada)
    con_extractor = [ruta for ruta in archivos if _tiene_extractor(ruta)]
    sin_extractor = [
        _ruta_relativa(ruta, entrada) for ruta in archivos if not _tiene_extractor(ruta)
    ]

    rutas, fuera_del_indice, huerfanos = _cruzar_con_indice(con_extractor, entrada, indice)

    colisiones = _agrupar_colisiones(rutas, entrada)
    ambiguos = set(colisiones)
    _avisar_colisiones(colisiones)

    identidades = [
        _identidad_de(ruta, entrada, indice, ambiguos, fenomeno_por_defecto)
        for ruta in rutas
    ]
    _verificar_doc_ids(identidades)

    documentos = [_extraer_documento(identidad) for identidad in identidades]
    documentos.sort(key=lambda doc: (doc.fuente, doc.meta["ruta_relativa"]))

    _escribir_salida(documentos, salida, limpiar=limpiar)

    reporte = ReporteCobertura(
        sin_extractor=sin_extractor,
        huerfanos_del_indice=huerfanos,
        fuera_del_indice=fuera_del_indice,
        por_origen_doc_id=_contar(i.origen_doc_id for i in identidades),
        por_origen_fenomeno=_contar(i.origen_fenomeno for i in identidades),
        nombres_ambiguos=len(colisiones),
        archivos_ambiguos=sum(len(rs) for rs in colisiones.values()),
    )
    return documentos, reporte


def _cruzar_con_indice(
    con_extractor: list[Path],
    entrada: Path,
    indice: dict[str, EntradaIndice] | None,
) -> tuple[list[Path], list[str], list[str]]:
    """Reparte los archivos entre los que el índice lista y los que no.

    Sin índice no hay nada que cruzar: se procesa todo lo que tenga extractor.
    """
    if not indice:
        return con_extractor, [], []

    listadas, sueltas = [], []
    vistas: set[str] = set()
    for ruta in con_extractor:
        relativa = _ruta_relativa(ruta, entrada)
        if relativa in indice:
            listadas.append(ruta)
            vistas.add(relativa)
        else:
            sueltas.append(relativa)

    # Se recorre el índice, no un set, para que el orden sea el del archivo.
    huerfanos = [relativa for relativa in indice if relativa not in vistas]
    return listadas, sueltas, huerfanos


def _contar(valores) -> dict[str, int]:
    """Cuenta ocurrencias con las claves ordenadas, para que el reporte sea estable."""
    conteo: dict[str, int] = {}
    for valor in valores:
        conteo[valor] = conteo.get(valor, 0) + 1
    return dict(sorted(conteo.items()))


def _avisar_colisiones(colisiones: dict[str, list[str]]) -> None:
    """Resumen a stderr. No detiene nada: es información, no un fallo."""
    if not colisiones:
        return
    archivos = sum(len(rutas) for rutas in colisiones.values())
    print(
        f"[aviso] {len(colisiones)} nombres de archivo se repiten en "
        f"{archivos} archivos; se desambiguan por ruta y se marcan con "
        f"meta['fuente_ambigua']",
        file=sys.stderr,
    )
```

Actualizar `_entrada_de_manifiesto` (líneas 224-235):

```python
def _entrada_de_manifiesto(documento: Documento) -> dict:
    """Resumen de una línea del manifiesto.

    ``observatorio`` y ``fuente_ambigua`` están aquí y no solo en el JSON para
    poder filtrar y auditar el corpus entero sin abrir 1826 archivos.
    """
    return {
        "doc_id": documento.doc_id,
        "fuente": documento.fuente,
        "formato": documento.formato,
        "fenomeno": documento.fenomeno,
        "idioma": documento.idioma,
        "n_bloques": len(documento.bloques),
        "n_chars": sum(len(bloque.texto) for bloque in documento.bloques),
        "n_errores": len(documento.errores),
        "observatorio": documento.meta.get("observatorio"),
        "fuente_ambigua": documento.meta.get("fuente_ambigua", False),
    }
```

- [ ] **Step 4: Implementar la CLI y el reporte en `main`**

Añadir al parser, tras `--fenomeno`:

```python
    parser.add_argument(
        "--indice",
        type=Path,
        default=None,
        help=(
            "ruta al Indice_Datos_Codefest.xlsx. Opcional: sin él se deduce el "
            "fenómeno de la carpeta y el doc_id de la ruta relativa"
        ),
    )
```

Reemplazar `main` (líneas 266-285):

```python
def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)

    indice = cargar_indice(args.indice) if args.indice else None
    if indice is not None:
        print(f"índice: {len(indice)} entradas desde {args.indice}", file=sys.stderr)

    documentos, reporte = procesar_corpus(
        args.entrada,
        args.salida,
        fenomeno_por_defecto=args.fenomeno,
        limpiar=args.limpiar,
        indice=indice,
    )

    con_errores = [documento for documento in documentos if documento.errores]
    bloques = sum(len(documento.bloques) for documento in documentos)
    print(
        f"{len(documentos)} documentos, {bloques} bloques, "
        f"{len(con_errores)} con errores -> {args.salida}"
    )

    _informar_cobertura(reporte)

    for documento in con_errores:
        print(f"  [error] {documento.fuente}: {documento.errores[0]}", file=sys.stderr)

    return 0


def _informar_cobertura(reporte: ReporteCobertura, muestra: int = 10) -> None:
    """Vuelca el reporte a stderr.

    No devuelve código de error: los tres conteos en cero es lo deseable, pero
    en el corpus de ADL hay 13 archivos legítimos fuera del índice —el
    enunciado, el propio índice y los catálogos de scraping— y abortar por eso
    sería peor que informarlo.
    """
    print("--- cobertura ---", file=sys.stderr)
    print(f"  doc_id por origen:   {reporte.por_origen_doc_id}", file=sys.stderr)
    print(f"  fenomeno por origen: {reporte.por_origen_fenomeno}", file=sys.stderr)
    print(
        f"  fuentes ambiguas:    {reporte.nombres_ambiguos} nombres, "
        f"{reporte.archivos_ambiguos} archivos",
        file=sys.stderr,
    )

    for titulo, rutas in (
        ("archivos sin extractor registrado", reporte.sin_extractor),
        ("entradas del índice sin archivo en disco", reporte.huerfanos_del_indice),
        ("archivos en disco fuera del índice (omitidos)", reporte.fuera_del_indice),
    ):
        print(f"  {titulo}: {len(rutas)}", file=sys.stderr)
        for ruta in rutas[:muestra]:
            print(f"      {ruta}", file=sys.stderr)
        if len(rutas) > muestra:
            print(f"      ... y {len(rutas) - muestra} más", file=sys.stderr)
```

Añadir el import de `cargar_indice` en la cabecera:

```python
from indice import EntradaIndice, cargar_indice
```

- [ ] **Step 5: Correr toda la suite**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Esperado: todo verde. El total rondará las 145 pruebas.

Si `test_dos_corridas_producen_bytes_identicos` falla, el culpable es casi seguro un `set()` iterado sin ordenar en `_cruzar_con_indice` o en `_contar`. Revisar que `huerfanos` se construya recorriendo `indice` y no `set(indice) - vistas`.

- [ ] **Step 6: Commit**

```bash
git add orquestador.py tests/test_orquestador.py
git commit -m "feat(orquestador): identidad por indice, colisiones sin abortar

Cuatro defectos contra el corpus real de ADL:

- _verificar_colisiones abortaba la corrida: 59 nombres se repiten en 186
  archivos legitimos. Pasa a marcar meta['fuente_ambigua'] y seguir.
- calcular_doc_id(ruta.name) daba el mismo doc_id a los 7 PDF homonimos de
  CSET, sobrescribiendo la salida. Ahora sale del indice, o de la ruta.
- La regex de fenomeno no matcheaba F1_IA_y_Capacidades_Estrategicas, asi
  que 1367 documentos de F2 y F3 se etiquetaban como 1 en silencio.
- Las extensiones sin extractor se filtraban sin dejar rastro. Ahora hay
  reporte de cobertura en stderr.

Añade --indice y saca observatorio y fuente_ambigua al manifiesto.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Tarea 7: Verificación contra el corpus real

**Files:**
- Create: `scripts/verificar_corpus.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: script de verificación de los criterios de §8. No forma parte de la suite de pytest: depende de un corpus que no está en el repo.

- [ ] **Step 1: Escribir el verificador**

Crear `scripts/verificar_corpus.py`:

```python
"""Comprueba los criterios de aceptación contra el corpus real de ADL.

No es una prueba de pytest: depende de un corpus de 1826 archivos que no vive
en el repositorio. Se corre a mano antes de una entrega.

Uso::

    python scripts/verificar_corpus.py --corpus c:/Users/jesus/projects/base_documental_codefest
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from indice import cargar_indice  # noqa: E402
from orquestador import cargar_manifiesto, procesar_corpus  # noqa: E402

ESPERADO_TOTAL = 1826
ESPERADO_POR_FENOMENO = {1: 459, 2: 479, 3: 888}
ESPERADO_NOMBRES_AMBIGUOS = 59
ESPERADO_ARCHIVOS_AMBIGUOS = 186


def comprobar(titulo: str, obtenido, esperado) -> bool:
    ok = obtenido == esperado
    marca = "[OK]  " if ok else "[FALLA]"
    print(f"{marca} {titulo}: {obtenido}" + ("" if ok else f"  (esperado {esperado})"))
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--salida", type=Path, default=RAIZ / "extraidos")
    args = parser.parse_args(argv)

    xlsx = args.corpus / "Indice_Datos_Codefest.xlsx"
    indice = cargar_indice(xlsx)

    resultados = [comprobar("entradas del índice", len(indice), ESPERADO_TOTAL)]

    primera = args.salida
    documentos, reporte = procesar_corpus(
        args.corpus, primera, limpiar=True, indice=indice
    )

    manifiesto = cargar_manifiesto(primera / "manifiesto.jsonl")
    por_fenomeno = dict(sorted(collections.Counter(e["fenomeno"] for e in manifiesto).items()))
    doc_ids = [e["doc_id"] for e in manifiesto]
    ambiguos = [e for e in manifiesto if e["fuente_ambigua"]]
    nombres_ambiguos = {e["fuente"] for e in ambiguos}

    resultados += [
        comprobar("líneas del manifiesto", len(manifiesto), ESPERADO_TOTAL),
        comprobar("conteo por fenómeno", por_fenomeno, ESPERADO_POR_FENOMENO),
        comprobar("doc_id únicos", len(set(doc_ids)), ESPERADO_TOTAL),
        comprobar(
            "doc_id con formato de ADL",
            sum(1 for d in doc_ids if d.startswith(("F1-", "F2-", "F3-"))),
            ESPERADO_TOTAL,
        ),
        comprobar("documentos ambiguos", len(ambiguos), ESPERADO_ARCHIVOS_AMBIGUOS),
        comprobar("nombres ambiguos", len(nombres_ambiguos), ESPERADO_NOMBRES_AMBIGUOS),
        comprobar("archivos sin extractor", len(reporte.sin_extractor), 0),
        comprobar("entradas huérfanas del índice", len(reporte.huerfanos_del_indice), 0),
        comprobar("todos con observatorio", sum(1 for e in manifiesto if e["observatorio"]), ESPERADO_TOTAL),
    ]

    # Determinismo: segunda corrida en otro directorio, diff byte a byte.
    segunda = primera.parent / f"{primera.name}_bis"
    procesar_corpus(args.corpus, segunda, limpiar=True, indice=indice)
    iguales = (primera / "manifiesto.jsonl").read_bytes() == (
        segunda / "manifiesto.jsonl"
    ).read_bytes()
    resultados.append(comprobar("dos corridas dan el mismo manifiesto", iguales, True))
    shutil.rmtree(segunda)

    print()
    print(f"archivos fuera del índice (informativo): {len(reporte.fuera_del_indice)}")
    for ruta in reporte.fuera_del_indice:
        print(f"    {ruta}")

    print()
    if all(resultados):
        print("TODOS LOS CRITERIOS SE CUMPLEN")
        return 0
    print(f"{sum(1 for r in resultados if not r)} criterios fallan")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Correrlo**

```bash
.venv/Scripts/python.exe scripts/verificar_corpus.py --corpus c:/Users/jesus/projects/base_documental_codefest
```

Esperado: los 11 criterios en `[OK]`, y una lista informativa de 13 archivos fuera del índice.

Si `conteo por fenómeno` falla, comparar `reporte.por_origen_fenomeno`: debe ser `{"indice": 1826}`. Cualquier `carpeta` o `defecto` significa que el cruce con el índice no está emparejando y hay que revisar `_ruta_relativa`.

- [ ] **Step 3: Actualizar el README**

Añadir `--indice` a la tabla de flags (línea 36):

```markdown
| `--indice` | Ruta al `Indice_Datos_Codefest.xlsx` de ADL. Opcional. Con él, `doc_id`, `fenomeno` y `observatorio` salen del índice y solo se procesa lo que el índice lista. |
```

Reemplazar el bloque "Decisiones de diseño" sobre `fuente = path.name` (líneas 203-208):

```markdown
**`fuente = path.name`, no la ruta relativa.** El enunciado pide el nombre
exacto del archivo entregado. La contrapartida es que dos archivos con el mismo
nombre en subdirectorios distintos comparten `fuente`, y en el corpus de ADL eso
pasa en **59 nombres repartidos por 186 archivos** (CSET_Georgetown 112, Amazon
Underworld 72, ESA_Space_Debris 2). Son colisiones legítimas: el mismo informe
archivado por tipo, el mismo tile en varios niveles de zoom. El pipeline no se
detiene por ellas —lo hacía, y moría en la primera corrida sin procesar nada—:
las marca con `meta["fuente_ambigua"]` y desambigua la identidad por
`meta["ruta_relativa"]`. Es una limitación del corpus, no del pipeline.

**`doc_id` sale del índice de ADL cuando lo hay.** Es la identidad oficial y
trazable (`F1-AIINDEX-001`). Sin índice se deriva de la ruta relativa, nunca del
nombre: derivarlo del nombre daba el mismo `doc_id` a los 7 PDF homónimos de
CSET y el pipeline se sobrescribía a sí mismo seis veces sin avisar.

**El índice filtra.** Con `--indice`, solo se procesa lo que ADL lista. En disco
hay 13 archivos con extractor que el índice no menciona —el enunciado, el propio
índice, `FASE ORDENADA CODEFEST.xlsx` y 10 `*_catalogo.json`/`*_registro.json`
de scraping— y no son documentos de la entrega. Se reportan en stderr, no se
procesan y no se borran.
```

Actualizar la sección "Correr las pruebas" (línea 67) con el conteo real que arroje `pytest -q`, y la de "Pendiente" (líneas 235-241):

```markdown
## Pendiente

- Extractores de PDF, JSON, CSV/XLSX, imagen, PBF y texto plano (stubs con
  estrategia escrita). Con el corpus completo, los 1826 documentos salen con
  `bloques=[]` y "extractor no implementado": esta etapa arregla el recorrido y
  la identidad, no la extracción.
- `.avif` necesita `pillow-avif-plugin` cuando se implemente el extractor de
  imagen (1 archivo, F2-SWF-065).
```

Añadir `indice.py` al árbol de estructura (línea 83):

```
contrato.py          Bloque, Documento, calcular_doc_id, validar_documento
indice.py            lectura del indice maestro de ADL (solo lee)
limpieza.py          normalización, idioma, detección de repetidos
orquestador.py       recorrido, persistencia y CLI
```

- [ ] **Step 4: Correr la suite completa una última vez**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Esperado: todo verde, sin fallos ni errores.

- [ ] **Step 5: Commit**

```bash
git add scripts/verificar_corpus.py README.md
git commit -m "test: verificador de los criterios de aceptacion contra el corpus real

Comprueba las 11 casillas de la spec sobre las 1826 entradas de ADL:
manifiesto completo, conteo por fenomeno, doc_id unicos con formato de
ADL, 186 documentos ambiguos sobre 59 nombres y dos corridas identicas.
Fuera de pytest: depende de un corpus que no vive en el repo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review

**Cobertura de la spec:**

| Sección | Dónde se implementa |
|---|---|
| §1 `indice.py`, `EntradaIndice`, `cargar_indice` | Tarea 2 |
| §1.1 clave = ruta_relativa | Tarea 2, `_entrada_de_fila` + test `test_la_clave_es_la_ruta_relativa_no_el_nombre` |
| §1.2 normalizar separadores | Tarea 2, `carpeta.replace("\\", "/")` + test |
| §1.3 `ValueError` en duplicados | Tarea 2, `_leer_hoja` + 2 tests |
| §1.4 `read_only=True`, sin `max_row` | Tarea 2, `_leer_hoja` |
| §1.5 determinismo del orden | Tarea 2 + test `test_el_orden_del_mapa_es_el_del_archivo` |
| §2.1 doc_id del índice / respaldo por ruta | Tarea 4, `_identidad_de` |
| §2.2 `fuente` inmutable | Tarea 4, `_documento_fallido` y `_con_identidad` no tocan `fuente` |
| §2.3 colisiones → advertencia + meta | Tarea 4, `_agrupar_colisiones` + `_avisar_colisiones` |
| §2.4 solo doc_id duplicado detiene | Tarea 4, `_verificar_doc_ids` |
| §3 precedencia índice > carpeta > defecto | Tareas 4 y 5 |
| §3 regex nueva + legado | Tarea 5, `_PATRONES_FENOMENO` |
| §3 conteo por vía | Tarea 6, `reporte.por_origen_fenomeno` |
| §4 `.txt`/`.md` y `.avif` | Tarea 3 |
| §4 reporte de cobertura (3 conteos) | Tarea 6, `ReporteCobertura` + `_informar_cobertura` |
| §4 `.html`/`.htm` se quedan | Tarea 3, no se tocan |
| §5 `--indice` opcional | Tarea 6 |
| §6 los 6 campos de meta | Tarea 4, `_meta_de` |
| §6 manifiesto + observatorio, fuente_ambigua | Tarea 6, `_entrada_de_manifiesto` |
| §7.1–§7.8 los ocho tests exigidos | Tareas 2, 4, 5, 6 |
| §7 fixtures: xlsx por script + homónimos | Tarea 2, Step 1 |
| §8 criterios de aceptación | Tarea 7 |
| §9 reglas innegociables | Global Constraints |

**Desviaciones conscientes de la spec, y por qué:**

1. **§8 "0 archivos fuera del índice" no se cumple: son 13.** Verificado contra el corpus real. Son el enunciado, el propio índice, `FASE ORDENADA CODEFEST.xlsx` y 10 catálogos de scraping. El usuario decidió no borrarlos, así que el reporte los lista y `main()` no falla. El verificador de la Tarea 7 los imprime como informativos, no como criterio.
2. **§8 "106 pruebas existentes en verde" — 4 cambian.** La spec las invalida ella misma (§2 quita el `ValueError`, §4 registra `.txt`, §6 amplía el manifiesto). Están tabuladas arriba.
3. **`contrato.py` se toca**, pese a §0. Sin relajar la invariante de `doc_id`, §7.8 y §8 son mutuamente imposibles. Decidido con el usuario.
4. **`meta["fuente_ambigua"]` está siempre** (`True`/`False`), no solo cuando es `True`. §6 dice "`True` solo si el nombre colisiona", que se cumple —el valor solo es `True` entonces—, y tenerlo siempre hace consultable el manifiesto sin `.get()` por todas partes.
5. **`procesar_directorio` se conserva** con su firma anterior y devuelve solo los documentos; `procesar_corpus` es la que devuelve el reporte. Así las ~100 pruebas que la usan no cambian.
6. **El orden del manifiesto pasa a `(fuente, ruta_relativa)`.** Con 186 nombres repetidos, ordenar solo por `fuente` dejaba el desempate al orden de `rglob`.

**Tipos y firmas, comprobación cruzada:**

- `EntradaIndice` se construye en Tarea 2 y se consume en Tareas 4 y 6 con los mismos 7 campos.
- `_Identidad` se define en Tarea 4 y se consume en Tareas 4 y 6; los 9 campos coinciden.
- `_ruta_relativa(ruta, raiz)` se define en Tarea 4 y se usa en Tareas 4, 6 — misma firma.
- `_fenomeno_de_carpeta(ruta_relativa: str) -> int | None` se define en Tarea 5 y se usa en `_identidad_de` (Tarea 4). **El orden de escritura importa:** la Tarea 4 no corre verde hasta que la 5 y la 6 estén hechas; está anotado en su Step 4.
- `ReporteCobertura` se define en Tarea 6 y se consume en `_informar_cobertura` (Tarea 6) y en el verificador (Tarea 7): `sin_extractor`, `huerfanos_del_indice`, `fuera_del_indice`, `por_origen_doc_id`, `por_origen_fenomeno`, `nombres_ambiguos`, `archivos_ambiguos`. Coinciden.
- `contrato.FORMATOS` gana `"texto"` en Tarea 1, que es lo que `extractores/texto.py` declara en Tarea 3.
