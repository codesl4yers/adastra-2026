"""Contrato de datos de la capa de extracción.

Define las dos únicas estructuras que cruzan la frontera entre extractores y el
resto del pipeline, y la función que verifica sus invariantes.

Regla de dependencias: este módulo depende de :mod:`limpieza` (para saber qué
es un texto normalizado) y de nada más. Los extractores dependen de ambos.

Nota sobre inmutabilidad: ambas clases son ``frozen``, pero contienen listas y
diccionarios. "Frozen" impide reasignar campos, no mutar su contenido, y hace
que las instancias no sean hashables. Trátalas como valores: constrúyelas de
una vez y no las modifiques.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from limpieza import normalizar_texto

TIPOS_BLOQUE: tuple[str, ...] = ("titulo", "parrafo", "lista", "fila", "ocr")
FORMATOS: tuple[str, ...] = ("pdf", "html", "json", "csv", "xlsx", "imagen", "pbf")
IDIOMAS: tuple[str, ...] = ("es", "en", "pt")
FENOMENOS: tuple[int, ...] = (1, 2, 3)

NIVEL_MINIMO, NIVEL_MAXIMO = 1, 6

# 8 bytes de blake2b => 16 caracteres hexadecimales. Suficiente para decenas de
# miles de documentos y corto para usarse como nombre de archivo.
BYTES_DOC_ID = 8


@dataclass(frozen=True)
class Bloque:
    """Unidad mínima de texto extraída de un documento."""

    texto: str
    """Texto limpio, sin marcado, normalizado según :func:`limpieza.normalizar_texto`."""

    tipo: str
    """Uno de :data:`TIPOS_BLOQUE`."""

    nivel: int | None
    """1..6 si y solo si ``tipo == "titulo"``; ``None`` en el resto."""

    ruta: list[str]
    """Breadcrumb de encabezados ancestros vigentes, del más externo al más interno."""

    pagina: int | None
    """Página de origen (1-based) si el formato la tiene; ``None`` si no."""

    atomico: bool
    """``True`` si es una unidad indivisible que no debe fusionarse con vecinas."""


@dataclass(frozen=True)
class Documento:
    """Un archivo del corpus, ya extraído."""

    doc_id: str
    """Identificador interno, derivado de :attr:`fuente`. Ver :func:`calcular_doc_id`."""

    fuente: str
    """Nombre o URL EXACTA del archivo original. Campo inmutable de emparejamiento."""

    formato: str
    """Uno de :data:`FORMATOS`."""

    fenomeno: int
    """1, 2 o 3."""

    idioma: str
    """ISO 639-1 predominante: uno de :data:`IDIOMAS`."""

    bloques: list[Bloque]
    """En orden de lectura del documento original."""

    meta: dict[str, Any]
    """Campos descriptivos: url, fecha, autores, titulo, hoja..."""

    errores: list[str]
    """Vacía si la extracción fue limpia."""


def calcular_doc_id(fuente: str) -> str:
    """Identificador estable derivado de la fuente.

    Se usa blake2b y no ``hash()`` porque ``hash()`` de una cadena depende de
    ``PYTHONHASHSEED`` y cambiaría entre corridas. El resultado solo depende de
    los bytes UTF-8 de ``fuente``, así que dos corridas sobre el mismo corpus
    producen los mismos nombres de archivo.
    """
    digest = hashlib.blake2b(fuente.encode("utf-8"), digest_size=BYTES_DOC_ID)
    return digest.hexdigest()


def documento_a_dict(doc: Documento) -> dict[str, Any]:
    """Convierte el documento a estructuras primitivas listas para ``json.dumps``."""
    return asdict(doc)


def _violaciones_de_bloque(indice: int, bloque: Bloque) -> list[str]:
    """Comprueba los invariantes locales de un bloque."""
    violaciones: list[str] = []
    prefijo = f"bloques[{indice}]"

    if not isinstance(bloque.texto, str):
        violaciones.append(f"{prefijo}: texto no es str")
    else:
        normalizado = normalizar_texto(bloque.texto)
        if not normalizado:
            violaciones.append(f"{prefijo}: texto vacío o solo espacios")
        elif bloque.texto != normalizado:
            violaciones.append(
                f"{prefijo}: texto sin normalizar (esperado {normalizado!r})"
            )

    if bloque.tipo not in TIPOS_BLOQUE:
        violaciones.append(f"{prefijo}: tipo {bloque.tipo!r} no está en {TIPOS_BLOQUE}")

    # nivel es no nulo si y solo si el bloque es un título.
    if bloque.tipo == "titulo":
        if bloque.nivel is None:
            violaciones.append(f"{prefijo}: un titulo debe llevar nivel")
        elif not isinstance(bloque.nivel, int) or isinstance(bloque.nivel, bool):
            violaciones.append(f"{prefijo}: nivel no es int")
        elif not NIVEL_MINIMO <= bloque.nivel <= NIVEL_MAXIMO:
            violaciones.append(
                f"{prefijo}: nivel {bloque.nivel} fuera del rango "
                f"{NIVEL_MINIMO}..{NIVEL_MAXIMO}"
            )
    elif bloque.nivel is not None:
        violaciones.append(f"{prefijo}: nivel solo se admite en bloques de tipo titulo")

    if not isinstance(bloque.ruta, list) or not all(isinstance(t, str) for t in bloque.ruta):
        violaciones.append(f"{prefijo}: ruta debe ser una lista de str")

    if bloque.pagina is not None:
        if not isinstance(bloque.pagina, int) or isinstance(bloque.pagina, bool):
            violaciones.append(f"{prefijo}: pagina no es int")
        elif bloque.pagina < 1:
            violaciones.append(f"{prefijo}: pagina {bloque.pagina} debe ser >= 1")

    if not isinstance(bloque.atomico, bool):
        violaciones.append(f"{prefijo}: atomico no es bool")

    return violaciones


def _violaciones_de_jerarquia(bloques: list[Bloque]) -> list[str]:
    """Comprueba que cada ``ruta`` sean los ancestros vigentes, no el histórico.

    Reconstruye la pila de encabezados recorriendo los bloques en orden y la
    compara con la ruta declarada. Un título de nivel N cierra todos los títulos
    de nivel >= N que estuvieran abiertos.
    """
    violaciones: list[str] = []
    pila: list[tuple[int, str]] = []  # (nivel, texto)

    for indice, bloque in enumerate(bloques):
        es_titulo_valido = (
            bloque.tipo == "titulo"
            and isinstance(bloque.nivel, int)
            and not isinstance(bloque.nivel, bool)
            and NIVEL_MINIMO <= bloque.nivel <= NIVEL_MAXIMO
        )

        if bloque.tipo == "titulo" and not es_titulo_valido:
            # El nivel ya se reportó como inválido; sin él no se puede situar
            # el título en la jerarquía, así que no se arrastra el error.
            continue

        if es_titulo_valido:
            while pila and pila[-1][0] >= bloque.nivel:
                pila.pop()

        esperada = [texto for _, texto in pila]
        if list(bloque.ruta) != esperada:
            violaciones.append(
                f"bloques[{indice}]: ruta {list(bloque.ruta)!r} no coincide con "
                f"los ancestros vigentes {esperada!r}"
            )

        if es_titulo_valido:
            pila.append((bloque.nivel, bloque.texto))

    return violaciones


def validar_documento(doc: Documento) -> list[str]:
    """Devuelve la lista de invariantes violados por ``doc``; vacía si está bien.

    Pensada para los tests y para depurar un extractor nuevo. El pipeline en
    producción no la llama: un documento inválido debe ser imposible de
    construir, no algo que se detecte al final.
    """
    violaciones: list[str] = []

    if not isinstance(doc.fuente, str) or not doc.fuente.strip():
        violaciones.append("fuente vacía: es el campo de emparejamiento, no puede faltar")
    elif doc.doc_id != calcular_doc_id(doc.fuente):
        violaciones.append(
            f"doc_id {doc.doc_id!r} no deriva de fuente {doc.fuente!r} "
            f"(esperado {calcular_doc_id(doc.fuente)!r})"
        )

    if doc.formato not in FORMATOS:
        violaciones.append(f"formato {doc.formato!r} no está en {FORMATOS}")

    if doc.fenomeno not in FENOMENOS:
        violaciones.append(f"fenomeno {doc.fenomeno!r} no está en {FENOMENOS}")

    if doc.idioma not in IDIOMAS:
        violaciones.append(f"idioma {doc.idioma!r} no está en {IDIOMAS}")

    if not isinstance(doc.meta, dict):
        violaciones.append("meta debe ser un dict")

    if not isinstance(doc.errores, list) or not all(isinstance(e, str) for e in doc.errores):
        violaciones.append("errores debe ser una lista de str")

    if not isinstance(doc.bloques, list):
        violaciones.append("bloques debe ser una lista")
        return violaciones

    for indice, bloque in enumerate(doc.bloques):
        violaciones.extend(_violaciones_de_bloque(indice, bloque))

    violaciones.extend(_violaciones_de_jerarquia(doc.bloques))

    return violaciones
