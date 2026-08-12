"""Segmentación de oraciones. Es el átomo de la fragmentación.

§3.3 del enunciado prohíbe que una oración cruce de un fragmento a otro, así
que la frontera oracional decide si un fragmento cumple o viola un requisito
obligatorio. Por eso este módulo existe aparte del fragmentador: se prueba solo,
contra un conjunto dorado etiquetado a mano (``fixtures/oraciones_doradas.jsonl``).

Motor
-----
`pysbd <https://github.com/nipunsadvilkar/pySBD>`_ 0.3.4, licencia MIT: basado
en reglas, determinista y sin descarga de modelos en tiempo de ejecución, que es
lo que hace reproducible a ``generador.py``. Quedan descartados nltk-punkt (baja
un modelo) y los modelos estadísticos de spaCy por el mismo motivo.

**pysbd 0.3.4 no trae módulo de portugués.** Sus idiomas son
``{en, es, de, fr, it, nl, pl, ru, da, el, ja, zh, hi, mr, ar, fa, hy, bg, kk,
sk, ur, am, my}``. El spec daba por hecho que sí. El portugués se segmenta con
el módulo español —la lengua tipológicamente más cercana de las disponibles— y
con su propia lista de abreviaturas (``séc.``, ``p.ex.``, ``n.º``...), que es lo
que de verdad cambia el resultado en este corpus.

La capa de re-fusión
--------------------
pysbd solo no basta: su módulo español parte ``La Dra. | Gómez``, ``EE.UU. |
y la U.S. | Space Force`` y ``cf. | Martínez``, que son justo los venenos que
lista §3.2 del spec. Encima de pysbd va una capa que vuelve a unir los cortes
falsos, con tres reglas: abreviatura conocida del idioma, sigla con puntos
internos o inicial suelta, y trozo que no termina en puntuación terminal
seguido de otro que empieza en minúscula.

La capa es deliberadamente agresiva porque el error no es simétrico: **partir
una oración viola §3.3; fusionar dos oraciones de más solo engorda un fragmento**.
Las abreviaturas que sí pueden cerrar oración (``etc.``, ``Inc.``, ``Ltda.``)
van en una lista aparte y solo se fusionan cuando lo que sigue empieza en
minúscula. Cuando un caso del conjunto dorado se resista, se documenta con su
motivo en el campo ``excepcion`` del JSONL y sale como ``xfail``; no se borra.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pysbd

from limpieza import normalizar_texto

IDIOMA_POR_DEFECTO = "es"

# Idioma del contrato -> módulo de pysbd. El portugués no tiene módulo propio en
# pysbd 0.3.4; usa el español y se distingue por su lista de abreviaturas.
MOTOR_POR_IDIOMA: dict[str, str] = {"es": "es", "en": "en", "pt": "es"}

# Abreviaturas comunes a los tres idiomas: bibliografía, figuras y tratamientos
# que se escriben igual en es/en/pt.
#
# Son todas **prefijas**: piden un nombre o un número detrás ("Dr. Gómez",
# "Fig. 3"), así que el punto nunca cierra oración y se fusiona siempre.
_ABREVIATURAS_COMUNES: frozenset[str] = frozenset(
    """
    cf. vs. Vs. e.g. E.g. i.e. I.e. Ph.D. PhD. et.
    Fig. fig. Figs. figs. Tab. tab. Ref. ref. Refs. op. cit. loc.
    p. pp. ed. eds. Ed. vol. Vol. vols. cap. caps. núm. no. No. nº. n.
    Dr. Dra. Drs. Prof. Profa. Sr. Sra. St. Univ. Dept. Corp.
    """.split()
)

# Abreviaturas **libres**: cierran oración tan a menudo como no lo hacen
# ("...sensores, etc. El resultado..." frente a "...etc. y sus derivados").
# Solo se fusionan cuando lo que sigue empieza en minúscula o en dígito, que es
# la única señal fiable de que la frase continúa.
_ABREVIATURAS_AMBIGUAS: frozenset[str] = frozenset(
    """
    etc. al. Inc. Ltd. Ltda. Co. Cia. Cía. S.A. Corp.
    """.split()
)

_ABREVIATURAS_POR_IDIOMA: dict[str, frozenset[str]] = {
    "es": frozenset(
        """
        Art. art. arts. Arts. Núm. nro. Nro. pág. págs. Pág. Sres. Srta. Dres.
        Ing. Lic. Mtro. Mtra. Av. Avda. Ud. Uds. Vd. Vds. máx. mín. aprox.
        ss. sig. sigs. ej. p.ej. EE.UU. RR.HH. Gral. Cnel. Cap. izq. der.
        Ltda. Cía. admón. dcha.
        """.split()
    ),
    "en": frozenset(
        """
        Mr. Mrs. Ms. Jr. Gen. Col. Lt. Sen. Rep. Gov. Adm. Capt. Ave. Rd. Blvd.
        Jan. Feb. Mar. Apr. Jun. Jul. Aug. Sep. Sept. Oct. Nov. Dec.
        U.S. U.K. U.N. E.U. approx. est. esp. incl. min. max. Eq. Sec. Ch. Chap.
        """.split()
    ),
    "pt": frozenset(
        """
        Art. art. arts. Arts. n.º nº núm. pág. págs. Pág. séc. sécs. Séc.
        Eng. Engª. Engo. Av. Avª. Ex. ex. p.ex. Ltda. Cia. Cª. E.U.A. U.E.
        aprox. máx. mín. ed. Exmo. Exma. Prof.ª refª.
        """.split()
    ),
}

# Siglas con puntos internos: "U.S.", "N.A.S.A.", "EE.UU.". Se exigen dos grupos
# como mínimo para no capturar un final de oración legítimo como "...la ONU.",
# que sí cierra la frase.
_SIGLA = re.compile(r"^(?:[A-ZÁÉÍÓÚÑÜÇ]{1,3}\.){2,}$")

# Inicial suelta de un nombre propio: "J. R. Pérez".
_INICIAL = re.compile(r"^[A-ZÁÉÍÓÚÑÜÇ]\.$")

# Puntuación que sí cierra una oración, admitiendo cierres de comilla o
# paréntesis después del signo: «... alto.»  "... high."  (... final.)
_TERMINAL = re.compile(r"[.!?…][\"'»”’\)\]]*$")

# pysbd no corta después de una comilla o un paréntesis de cierre, así que
# «El riesgo es alto.» La recomendación... le sale de una pieza. Se corta aquí,
# y solo aquí, porque exigir un signo terminal *antes* del cierre y una
# mayúscula o una apertura después es evidencia suficiente de frontera. Es la
# única división propia del módulo: partir de más viola §3.3 y el resto de la
# capa está construida para no hacerlo nunca.
_CIERRE_DE_CITA = re.compile(
    r"(?<=[.!?][\"»”’\)\]])\s+(?=[A-ZÁÉÍÓÚÑÜ¿¡«\"])"
)


@lru_cache(maxsize=8)
def _motor(idioma: str) -> pysbd.Segmenter:
    """Segmentador de pysbd para el idioma, construido una sola vez.

    ``clean=False`` es obligatorio: con ``clean=True`` pysbd reescribe el texto
    y la unión de las oraciones deja de reproducir el original, que es
    justamente el invariante que protege al jurado de recibir un texto que no
    está en el documento.
    """
    codigo = MOTOR_POR_IDIOMA.get(idioma, MOTOR_POR_IDIOMA[IDIOMA_POR_DEFECTO])
    return pysbd.Segmenter(language=codigo, clean=False, char_span=False)


@lru_cache(maxsize=8)
def _abreviaturas(idioma: str) -> frozenset[str]:
    """Abreviaturas prefijas del idioma: las que nunca cierran oración.

    Se restan las ambiguas para que una lista por idioma no pueda ascender por
    descuido a ``Ltda.`` a la categoría de "fusionar siempre".
    """
    propias = _ABREVIATURAS_POR_IDIOMA.get(
        idioma, _ABREVIATURAS_POR_IDIOMA[IDIOMA_POR_DEFECTO]
    )
    return (_ABREVIATURAS_COMUNES | propias) - _ABREVIATURAS_AMBIGUAS


def segmentar(texto: str, idioma: str = IDIOMA_POR_DEFECTO) -> list[str]:
    """Parte ``texto`` en oraciones completas, sin perder ni añadir caracteres.

    Garantiza que ``" ".join(segmentar(t, i)) == normalizar_texto(t)``. Es un
    invariante duro: si la capa de re-fusión o el propio pysbd lo rompieran, se
    devuelve el texto entero como una sola oración. Un fragmento más grande de
    lo deseable es un problema de calidad; un texto alterado es un problema de
    trazabilidad, y ese no se negocia.

    Un idioma fuera del contrato cae a español en vez de lanzar: el fragmentador
    no puede morir porque un extractor devuelva un idioma inesperado.
    """
    limpio = normalizar_texto(texto)
    if not limpio:
        return []

    crudas = [
        subtrozo
        for trozo in _motor(idioma).segment(limpio)
        for subtrozo in _dividir_tras_cita(trozo)
    ]
    oraciones = _refundir(crudas, idioma)

    if " ".join(oraciones) != limpio:
        return [limpio]
    return oraciones


def _dividir_tras_cita(trozo: str) -> list[str]:
    """Parte un trozo de pysbd donde una comilla de cierre tapaba la frontera.

    El espacio separador se queda con la parte izquierda, igual que hace pysbd,
    para que la concatenación de los trozos siga reproduciendo el texto.
    """
    cortes = [coincidencia.end() for coincidencia in _CIERRE_DE_CITA.finditer(trozo)]
    if not cortes:
        return [trozo]

    partes: list[str] = []
    inicio = 0
    for corte in cortes:
        partes.append(trozo[inicio:corte])
        inicio = corte
    partes.append(trozo[inicio:])
    return partes


def _refundir(crudas: list[str], idioma: str) -> list[str]:
    """Vuelve a unir los trozos que pysbd cortó donde no había frontera.

    Acumula trozos mientras el corte parezca falso y solo entonces cierra la
    oración, para que ``EE.UU. | y la U.S. | Space Force firmaron.`` acabe en
    una sola pieza y no en dos.
    """
    oraciones: list[str] = []
    acumulado = ""

    for posicion, trozo in enumerate(crudas):
        acumulado += trozo
        siguiente = crudas[posicion + 1] if posicion + 1 < len(crudas) else None
        if siguiente is not None and _es_corte_falso(acumulado, siguiente, idioma):
            continue
        oraciones.append(acumulado)
        acumulado = ""

    if acumulado:
        oraciones.append(acumulado)

    return [oracion.strip() for oracion in oraciones if oracion.strip()]


def _es_corte_falso(izquierda: str, derecha: str, idioma: str) -> bool:
    """``True`` si el corte entre ``izquierda`` y ``derecha`` no es una frontera."""
    fin = izquierda.rstrip()
    if not fin:
        return True

    ultima = fin.rsplit(" ", 1)[-1]
    if ultima in _abreviaturas(idioma):
        return True
    if _SIGLA.match(ultima) or _INICIAL.match(ultima):
        return True

    inicio_derecha = derecha.lstrip()[:1]
    if ultima in _ABREVIATURAS_AMBIGUAS:
        return bool(inicio_derecha) and not inicio_derecha.isupper()

    # Sin puntuación terminal, una continuación en minúscula significa que el
    # corte cayó dentro de la oración.
    if not _TERMINAL.search(fin) and inicio_derecha and inicio_derecha.islower():
        return True

    return False
