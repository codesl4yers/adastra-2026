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
