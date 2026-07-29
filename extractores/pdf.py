"""Extractor de PDF. STUB: falta implementar.

Estrategia
----------
Usar una librería de extracción con posiciones (``pdfplumber`` o ``PyMuPDF``),
no una que devuelva una única cadena por página: hacen falta las coordenadas
para reconstruir el orden de lectura y para distinguir títulos de cuerpo.

1. Recorrer páginas en orden, ``pagina`` empieza en 1.
2. Agrupar caracteres en líneas y líneas en párrafos usando el interlineado
   dominante del documento como umbral, no una constante fija.
3. Inferir ``tipo="titulo"`` y su ``nivel`` por tamaño de fuente relativo: los
   tamaños más grandes que la moda del cuerpo, agrupados en escalones. El nivel
   sale del rango del escalón, no del valor absoluto en puntos.
4. Mantener la pila de encabezados para el breadcrumb ``ruta``, igual que en
   :mod:`extractores.html`.
5. Aplicar :func:`limpieza.lineas_repetidas` con las páginas como unidades para
   eliminar cabeceras y pies, y :func:`limpieza.es_ruido_estructural` para la
   numeración.

La trampa principal
-------------------
El PDF no tiene orden de lectura: tiene instrucciones de dibujo. En documentos
a dos columnas, extraer por posición vertical intercala las dos columnas línea
a línea y produce texto que parece correcto pero es basura entreverada. Hay que
detectar el número de columnas (por ejemplo, buscando un corredor vertical sin
caracteres) y extraer columna por columna.

Segunda trampa, menos visible: un PDF escaneado devuelve cero caracteres sin
lanzar ningún error. Hay que detectar ese caso y delegar en
:mod:`extractores.imagen` con OCR, o registrar el error; nunca devolver un
documento vacío en silencio, porque se pierde sin que nadie lo note.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATO = "pdf"


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo PDF.

    El orquestador captura el ``NotImplementedError`` y registra el documento
    como fallido, así que dejar el stub así no tumba el pipeline.
    """
    raise NotImplementedError("extractor de PDF pendiente: ver la estrategia en el docstring")
