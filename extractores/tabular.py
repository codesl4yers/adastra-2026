"""Extractor de CSV y XLSX. STUB: falta implementar.

Un solo módulo para los dos formatos porque comparten el modelo de datos
(filas y columnas) y toda la lógica de serialización de filas. Solo cambia el
lector: ``csv`` de la estándar para uno, ``openpyxl`` en modo ``read_only``
para el otro. El ``formato`` del ``Documento`` sí debe diferenciarlos: ``"csv"``
o ``"xlsx"`` según la extensión.

Estrategia
----------
1. Leer la cabecera y guardarla en ``meta["columnas"]``. En XLSX, una entrada
   por hoja, y ``meta["hoja"]`` en cada bloque de esa hoja.
2. Emitir **una fila por bloque**, con ``tipo="fila"`` y ``atomico=True``: una
   fila es un registro y no debe fusionarse con la siguiente ni partirse.
3. Serializar cada fila como ``"columna: valor"`` separado por ``" | "``,
   omitiendo las celdas vacías. Repetir el nombre de la columna en cada fila es
   redundante para un humano, pero es lo que permite que la fila se recupere
   sola, sin la cabecera como contexto.
4. ``ruta`` = ``[nombre_de_hoja]`` en XLSX, ``[]`` en CSV. ``pagina`` se deja en
   ``None``: una hoja no es una página.
5. Descartar columnas cuyo contenido no es lenguaje natural (identificadores
   opacos, códigos internos) salvo que sean la clave del registro.

La trampa principal
-------------------
El CSV no declara ni su codificación ni su delimitador. Un archivo exportado
desde Excel en español suele venir en cp1252 y separado por ``;``; leerlo como
UTF-8 con ``,`` produce una única columna con todo el contenido dentro y ni un
solo error. Hay que detectar el delimitador con ``csv.Sniffer`` sobre una
muestra y confirmar la codificación probando en un orden fijo (BOM, UTF-8,
cp1252 con reemplazo como último recurso). Fijar el orden importa: si se
delega en una autodetección probabilística, dos corridas pueden decodificar
distinto.

Segunda trampa, en XLSX: las celdas con fórmula devuelven la fórmula y no el
valor si no se abre con ``data_only=True``, y las fechas llegan como enteros
seriales de Excel. Además, ``openpyxl`` sin ``read_only=True`` carga la hoja
entera en memoria y un archivo grande basta para tumbar el proceso.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATOS_SOPORTADOS = {".csv": "csv", ".xlsx": "xlsx"}


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un CSV o un XLSX.

    El formato se decide por la extensión de ``path``.
    """
    raise NotImplementedError(
        "extractor tabular (csv/xlsx) pendiente: ver la estrategia en el docstring"
    )
