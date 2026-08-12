"""Extractor de texto plano y Markdown (.txt, .md). Un archivo del corpus.

Los párrafos se cortan por **líneas en blanco** y nunca por salto de línea: el
texto plano de un informe viene cortado a 80 columnas. En Markdown, las
almohadillas son títulos salvo dentro de un bloque de código.

Dos detalles del corpus: los saltos de página traen cabeceras y pies repetidos en
medio del cuerpo, y ``SWF_full-text.txt`` empieza con una cabecera
``SOURCE:``/``SCRAPED:`` que es metadata y no contenido.
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

# Orden fijo: una autodetección probabilística decodificaría distinto entre corridas.
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
    """Separa la cabecera ``SOURCE``/``SCRAPED`` del cuerpo: indexarla metería una
    URL y un timestamp como primer párrafo del documento."""
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
    """Cabeceras y pies: lo que se repite página tras página. Sin saltos de
    página no hay unidades que comparar y devuelve vacío."""
    unidades = [
        [normalizar_texto(linea) for linea in pagina.splitlines() if linea.strip()]
        for pagina in paginas
    ]
    return set(lineas_repetidas(unidades))


def _bloques_de_parrafo(
    parrafo: str, jerarquia: Jerarquia, es_markdown: bool, descartables: set[str]
) -> list[Bloque | None]:
    """Convierte un bloque separado por líneas en blanco en uno o varios bloques:
    dentro puede haber encabezados y elementos de lista."""
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
