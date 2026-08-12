"""Piezas que comparten los extractores.

Tres cosas se repetían en PDF, JSON y texto plano, y las tres son fáciles de
implementar mal de forma distinta en cada sitio:

- **La pila de encabezados.** ``contrato.validar_documento`` reconstruye la
  jerarquía y compara la ``ruta`` de cada bloque con los ancestros vigentes. Un
  extractor que lleve la pila por su cuenta acaba produciendo documentos que no
  validan por un detalle de bookkeeping. :class:`Jerarquia` la lleva una sola vez.
- **Qué es lenguaje natural.** Un identificador, una URL o un timestamp indexados
  como si fueran prosa ensucian el índice sin aportar nada recuperable.
- **Cómo se arma el ``Documento``**, incluida la detección de idioma.

Este módulo no lee archivos: trabaja con cadenas y bloques ya extraídos.
"""

from __future__ import annotations

import re
from typing import Any

from contrato import (
    NIVEL_MAXIMO,
    NIVEL_MINIMO,
    Bloque,
    Documento,
    calcular_doc_id,
)
from limpieza import detectar_idioma, normalizar_texto

# Caracteres de texto que se examinan para detectar el idioma. Con más no se
# acierta más y el corpus tiene documentos de cientos de miles de caracteres.
CARACTERES_PARA_IDIOMA = 4000

IDIOMA_POR_DEFECTO = "es"

# Un valor que casa con alguno de estos *por completo* no es lenguaje natural.
# Se ancla el patrón entero a propósito: una URL suelta es ruido, pero una URL
# dentro de una frase es parte de la frase y descartarla se llevaría el párrafo.
_NO_TEXTUALES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE),
    re.compile(r"^\S+@\S+\.\S+$"),                      # correo
    re.compile(r"^10\.\d{4,9}/\S+$"),                   # DOI
    re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE),      # hash hexadecimal
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
    re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$"),
    re.compile(r"^\S*/\S*\.\w{2,5}$"),                  # ruta de archivo
    re.compile(r"^[\d\s.,:%+\-/()]*$"),                 # solo cifras y puntuación
    re.compile(r"^\W+$", re.UNICODE),                   # solo símbolos
)

# Al menos una letra: sin esto, "· · ·" pasaría el filtro de longitud.
_TIENE_LETRA = re.compile(r"[^\W\d_]", re.UNICODE)

# Ruido que no aporta ni acompañado de su columna. Es un filtro más laxo que
# :func:`es_texto_natural` a propósito: en un registro el valor viaja pegado a
# su clave, así que "year: 2026" sí se recupera y "url: https://…" no.
_OPACOS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE),
    re.compile(r"^\S+@\S+\.\S+$"),
    re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE),
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
    re.compile(r"^\S*/\S*\.\w{2,5}$"),  # ruta de archivo
)

SEPARADOR_CAMPOS = " | "
"""Separa los pares ``columna: valor`` dentro de un registro."""


def es_texto_natural(valor: Any) -> bool:
    """``True`` si el valor merece indexarse como texto.

    El criterio es deliberadamente permisivo con las frases y estricto con los
    valores sueltos: en el corpus, ``"Inminencia"`` es el tipo de una alerta y
    vale; ``"91689"`` es su identificador interno y no.
    """
    if not isinstance(valor, str):
        return False

    limpio = normalizar_texto(valor)
    if not limpio or not _TIENE_LETRA.search(limpio):
        return False

    return not any(patron.match(limpio) for patron in _NO_TEXTUALES)


def es_valor_opaco(valor: Any) -> bool:
    """``True`` si el valor no aporta nada ni siquiera acompañado de su columna.

    Complementa a :func:`es_texto_natural`, que juzga texto suelto. Aquí se
    conservan números y fechas: ``"year: 2026"`` es recuperable, ``"2026"`` a
    secas no.
    """
    limpio = normalizar_texto(valor) if isinstance(valor, str) else str(valor).strip()
    if not limpio:
        return True
    return any(patron.match(limpio) for patron in _OPACOS)


def serializar_registro(pares) -> str:
    """Convierte pares ``(columna, valor)`` en el texto de un bloque ``fila``.

    Repetir el nombre de la columna en cada fila es redundante para un humano,
    pero es lo que hace que la fila se recupere sola, sin la cabecera como
    contexto: el vector de "país: Colombia | año: 2026" dice qué es cada cosa,
    el de "Colombia | 2026" no.

    Conserva el orden recibido —el de la cabecera— porque reordenar cambiaría
    el texto entre corridas.
    """
    campos = [
        f"{normalizar_texto(str(columna))}: {normalizar_texto(str(valor))}"
        for columna, valor in pares
        if not es_valor_opaco(valor) and normalizar_texto(str(columna))
    ]
    return SEPARADOR_CAMPOS.join(campos)


class Jerarquia:
    """Lleva la pila de encabezados y construye bloques con la ruta correcta.

    Un título de nivel N cierra todos los de nivel ``>= N`` que estuvieran
    abiertos, que es exactamente la regla que verifica
    ``contrato._violaciones_de_jerarquia``. Construir los bloques desde aquí es
    lo que garantiza que el documento valide.

    Los constructores devuelven ``None`` cuando el texto queda vacío tras
    normalizar, porque el contrato prohíbe bloques vacíos.
    :func:`construir_documento` los filtra.
    """

    def __init__(self) -> None:
        self._pila: list[tuple[int, str]] = []

    @property
    def ruta(self) -> list[str]:
        """Ancestros vigentes, del más externo al más interno."""
        return [texto for _, texto in self._pila]

    def titulo(self, texto: str, nivel: int, pagina: int | None = None) -> Bloque | None:
        """Abre una sección. El nivel se acota al rango que admite el contrato."""
        limpio = normalizar_texto(texto)
        if not limpio:
            return None

        nivel = max(NIVEL_MINIMO, min(int(nivel), NIVEL_MAXIMO))
        while self._pila and self._pila[-1][0] >= nivel:
            self._pila.pop()

        bloque = Bloque(
            texto=limpio,
            tipo="titulo",
            nivel=nivel,
            ruta=self.ruta,
            pagina=pagina,
            atomico=False,
        )
        self._pila.append((nivel, limpio))
        return bloque

    def parrafo(self, texto: str, pagina: int | None = None) -> Bloque | None:
        return self._simple(texto, "parrafo", pagina=pagina)

    def lista(self, texto: str, pagina: int | None = None) -> Bloque | None:
        return self._simple(texto, "lista", pagina=pagina)

    def ocr(self, texto: str, pagina: int | None = None) -> Bloque | None:
        return self._simple(texto, "ocr", pagina=pagina)

    def fila(
        self, texto: str, pagina: int | None = None, datos: dict[str, str] | None = None
    ) -> Bloque | None:
        """Un registro completo: no se parte ni se fusiona con sus vecinos.

        ``datos`` recoge los campos del registro que no entran al texto —los
        identificadores— para que sigan viajando con la fila sin pesar en el
        vector.
        """
        return self._simple(texto, "fila", pagina=pagina, atomico=True, datos=datos)

    def _simple(
        self,
        texto: str,
        tipo: str,
        pagina: int | None,
        atomico: bool = False,
        datos: dict[str, str] | None = None,
    ) -> Bloque | None:
        limpio = normalizar_texto(texto)
        if not limpio:
            return None
        return Bloque(
            texto=limpio,
            tipo=tipo,
            nivel=None,
            ruta=self.ruta,
            pagina=pagina,
            atomico=atomico,
            datos=dict(datos or {}),
        )


def construir_documento(
    *,
    fuente: str,
    formato: str,
    fenomeno: int,
    bloques: list[Bloque | None],
    meta: dict[str, Any] | None = None,
    errores: list[str] | None = None,
    idioma: str | None = None,
) -> Documento:
    """Ensambla el ``Documento`` y detecta su idioma.

    ``doc_id`` sale del nombre del archivo porque el extractor no sabe nada del
    índice de ADL. El orquestador lo sustituye después por el ``DOC_ID`` oficial
    (``orquestador._con_identidad``); ponerlo aquí solo sirve para que el
    documento sea válido por sí mismo.
    """
    utiles = [bloque for bloque in bloques if bloque is not None]
    return Documento(
        doc_id=calcular_doc_id(fuente),
        fuente=fuente,
        formato=formato,
        fenomeno=fenomeno,
        idioma=idioma or _idioma_de(utiles),
        bloques=utiles,
        meta=dict(meta or {}),
        errores=list(errores or []),
    )


def _idioma_de(bloques: list[Bloque]) -> str:
    """Idioma predominante, mirando solo el principio del documento.

    Se recorta a :data:`CARACTERES_PARA_IDIOMA` por velocidad: el detector no
    acierta más con un informe entero que con sus primeras páginas, y el corpus
    tiene documentos de cientos de miles de caracteres.
    """
    if not bloques:
        return IDIOMA_POR_DEFECTO

    acumulado: list[str] = []
    total = 0
    for bloque in bloques:
        acumulado.append(bloque.texto)
        total += len(bloque.texto)
        if total >= CARACTERES_PARA_IDIOMA:
            break

    return detectar_idioma(" ".join(acumulado)[:CARACTERES_PARA_IDIOMA], IDIOMA_POR_DEFECTO)


def documento_fallido(
    *,
    fuente: str,
    formato: str,
    fenomeno: int,
    motivo: str,
    meta: dict[str, Any] | None = None,
) -> Documento:
    """Documento válido que representa una extracción que no se pudo hacer.

    Ningún extractor propaga excepciones: un archivo corrupto produce esto y el
    pipeline sigue (§1.5 del spec). El motivo viaja en ``errores`` para que el
    reporte del orquestador pueda listarlo en vez de perderlo.
    """
    return construir_documento(
        fuente=fuente,
        formato=formato,
        fenomeno=fenomeno,
        bloques=[],
        meta=meta,
        errores=[motivo],
    )
