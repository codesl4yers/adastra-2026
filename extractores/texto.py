"""Extractor de texto plano (.txt, .md). STUB: falta implementar.

Estrategia
----------
1. Leer con ``encoding="utf-8"`` y ``errors="strict"``. Si falla, reintentar con
   ``cp1252`` y **registrar en ``meta`` la codificación usada**: un cambio de
   codificación cambia el texto, y sin dejarlo escrito no hay forma de saber
   después por qué dos corridas difieren.
2. Rechazar el archivo entero si trae bytes NUL. Un NUL en un archivo de texto
   significa corrupción, y un documento truncado que parece válido es peor que
   ninguno.
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
vallas ``` antes de interpretar un ``#``.
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
