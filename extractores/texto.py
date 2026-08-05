"""Extractor de texto plano y Markdown (.txt, .md).

Cómo se decide dónde acaba un párrafo
-------------------------------------
Por **líneas en blanco**, nunca por salto de línea: el texto plano de un informe
viene con las líneas cortadas a 80 columnas y partir por ``\\n`` trocearía cada
frase en pedazos que no son oraciones. Un salto suelto une; dos separan.

En Markdown se reconocen los encabezados ``#``..``######`` como títulos, con el
número de almohadillas como nivel, y se mantiene la pila para el breadcrumb. En
``.txt`` no hay marcado: todo es párrafo, salvo lo que descarte
:func:`limpieza.es_ruido_estructural`.

La trampa principal
-------------------
El texto plano extraído de un PDF conserva los cortes de página con sus
cabeceras y pies repetidos en medio del cuerpo. Se pasan las páginas —separadas
por el carácter de salto de página— como unidades a
:func:`limpieza.lineas_repetidas`; sin eso, el índice acaba lleno de
"Secure World Foundation | 12" entre párrafo y párrafo.

Segunda trampa: un ``.md`` puede traer bloques de código con almohadillas al
principio de línea que no son encabezados. Se sigue el estado de las vallas
``````` antes de interpretar un ``#``.

El caso real del corpus
-----------------------
``SWF_full-text.txt`` es el volcado de una página web y empieza con una cabecera
``SOURCE:`` / ``SCRAPED:`` seguida de una regla de signos igual. Esos dos datos
son metadata, no contenido, y van a ``meta``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from contrato import Bloque, Documento
from extractores.comun import Jerarquia, construir_documento, documento_fallido
from limpieza import es_ruido_estructural, lineas_repetidas, normalizar_texto

FORMATO = "texto"

EXTENSIONES = (".txt", ".md")

# Orden fijo de codificaciones. utf-8-sig acierta con y sin BOM; cp1252 es lo
# que produce Windows en español. Fijar el orden importa: una autodetección
# probabilística puede decodificar distinto en dos corridas.
CODIFICACIONES = ("utf-8-sig", "cp1252", "latin-1")

SALTO_DE_PAGINA = "\x0c"

_ENCABEZADO_MD = re.compile(r"^(#{1,6})\s+(.*)$")
_VALLA_CODIGO = re.compile(r"^\s*(?:```|~~~)")
_ELEMENTO_LISTA = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")

# Cabecera que antepone el scraper del corpus.
_CAMPOS_CABECERA = {"SOURCE": "url", "SCRAPED": "fecha_scraping"}
_REGLA = re.compile(r"^[=\-_]{10,}$")


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo de texto plano. Nunca lanza."""
    path = Path(path)
    try:
        crudo = path.read_bytes()
    except OSError as exc:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"no se pudo leer el archivo ({type(exc).__name__}): {exc}",
        )

    if b"\x00" in crudo:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=(
                "el archivo contiene bytes NUL: es un binario o está corrupto. "
                "Un documento truncado que parece válido es peor que ninguno."
            ),
        )

    contenido, codificacion = _decodificar(crudo)
    if contenido is None:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"no se pudo decodificar con ninguna de {CODIFICACIONES}",
        )

    meta: dict[str, Any] = {"codificacion": codificacion}
    contenido = _extraer_cabecera(contenido, meta)

    es_markdown = path.suffix.lower() == ".md"
    bloques = _bloques_de(contenido, es_markdown)

    errores = [] if any(bloques) else ["el archivo no tiene texto útil"]
    return construir_documento(
        fuente=path.name,
        formato=FORMATO,
        fenomeno=fenomeno,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


def _decodificar(crudo: bytes) -> tuple[str | None, str | None]:
    for codificacion in CODIFICACIONES:
        try:
            texto = crudo.decode(codificacion)
        except (UnicodeDecodeError, LookupError):
            continue
        return texto, "utf-8" if codificacion == "utf-8-sig" else codificacion
    return None, None


def _extraer_cabecera(contenido: str, meta: dict[str, Any]) -> str:
    """Separa la cabecera ``SOURCE``/``SCRAPED`` del cuerpo.

    Indexarla metería una URL y un timestamp como si fueran el primer párrafo
    del documento, justo donde más peso tienen.
    """
    lineas = contenido.splitlines()
    consumidas = 0

    for linea in lineas:
        limpia = linea.strip()
        if not limpia:
            consumidas += 1
            continue
        if _REGLA.match(limpia):
            consumidas += 1
            break
        etiqueta, separador, valor = limpia.partition(":")
        if separador and etiqueta in _CAMPOS_CABECERA and valor.strip():
            meta[_CAMPOS_CABECERA[etiqueta]] = valor.strip()
            consumidas += 1
            continue
        break

    if not any(campo in meta for campo in _CAMPOS_CABECERA.values()):
        return contenido
    return "\n".join(lineas[consumidas:])


def _bloques_de(contenido: str, es_markdown: bool) -> list[Bloque | None]:
    paginas = contenido.split(SALTO_DE_PAGINA)
    descartables = _descartables(paginas)

    jerarquia = Jerarquia()
    bloques: list[Bloque | None] = []

    for pagina in paginas:
        for parrafo in pagina.split("\n\n"):
            bloques.extend(_bloques_de_parrafo(parrafo, jerarquia, es_markdown, descartables))

    return bloques


def _descartables(paginas: list[str]) -> set[str]:
    """Cabeceras y pies: lo que se repite página tras página.

    Solo tiene sentido si el archivo trae saltos de página; con una sola unidad
    :func:`limpieza.lineas_repetidas` devuelve vacío por falta de evidencia.
    """
    unidades = [
        [normalizar_texto(linea) for linea in pagina.splitlines() if linea.strip()]
        for pagina in paginas
    ]
    return set(lineas_repetidas(unidades))


def _bloques_de_parrafo(
    parrafo: str, jerarquia: Jerarquia, es_markdown: bool, descartables: set[str]
) -> list[Bloque | None]:
    """Convierte un bloque separado por líneas en blanco en uno o varios bloques.

    Un párrafo puede contener varios encabezados o elementos de lista, así que
    no siempre sale un único bloque de aquí.
    """
    bloques: list[Bloque | None] = []
    acumulado: list[str] = []
    en_codigo = False

    def cerrar() -> None:
        if acumulado:
            bloques.append(jerarquia.parrafo(" ".join(acumulado)))
            acumulado.clear()

    for linea in parrafo.splitlines():
        limpia = linea.strip()

        if es_markdown and _VALLA_CODIGO.match(linea):
            cerrar()
            en_codigo = not en_codigo
            continue

        if not limpia or normalizar_texto(limpia) in descartables or es_ruido_estructural(limpia):
            continue

        if es_markdown and not en_codigo:
            encabezado = _ENCABEZADO_MD.match(limpia)
            if encabezado:
                cerrar()
                bloques.append(jerarquia.titulo(encabezado.group(2), len(encabezado.group(1))))
                continue

            elemento = _ELEMENTO_LISTA.match(limpia)
            if elemento:
                cerrar()
                bloques.append(jerarquia.lista(elemento.group(1)))
                continue

        acumulado.append(limpia)

    cerrar()
    return bloques
