"""Segmentación de oraciones: pysbd más una capa que rehace sus cortes falsos.

Se prueba solo, contra ``fixtures/oraciones_doradas.jsonl``. El portugués usa el
motor español, que es lo más cercano que trae pysbd 0.3.4.

Por qué pysbd y no otro, y por qué la re-fusión es deliberadamente agresiva: el
README (§Segmentación de oraciones) y el §3 del spec del fragmentador.
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

# Prefijas: piden un nombre o un número detrás, así que nunca cierran oración.
_ABREVIATURAS_COMUNES: frozenset[str] = frozenset(
    """
    cf. vs. Vs. e.g. E.g. i.e. I.e. Ph.D. PhD. et.
    Fig. fig. Figs. figs. Tab. tab. Ref. ref. Refs. op. cit. loc.
    p. pp. ed. eds. Ed. vol. Vol. vols. cap. caps. núm. no. No. nº. n.
    Dr. Dra. Drs. Prof. Profa. Sr. Sra. St. Univ. Dept. Corp.
    """.split()
)

# Libres: pueden cerrar oración. Solo se fusionan si lo que sigue no es mayúscula.
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

# Siglas con puntos internos: "U.S.", "N.A.S.A.". Dos grupos mínimo, o "...la ONU."
# también casaría.
_SIGLA = re.compile(r"^(?:[A-ZÁÉÍÓÚÑÜÇ]{1,3}\.){2,}$")

# Inicial suelta de un nombre propio: "J. R. Pérez".
_INICIAL = re.compile(r"^[A-ZÁÉÍÓÚÑÜÇ]\.$")

# Puntuación que cierra oración, con el cierre de comilla o paréntesis detrás.
_TERMINAL = re.compile(r"[.!?…][\"'»”’\)\]]*$")

# La única división propia del módulo: pysbd no corta tras una comilla de cierre.
_CIERRE_DE_CITA = re.compile(
    r"(?<=[.!?][\"»”’\)\]])\s+(?=[A-ZÁÉÍÓÚÑÜ¿¡«\"])"
)


@lru_cache(maxsize=8)
def _motor(idioma: str) -> pysbd.Segmenter:
    """Segmentador de pysbd para el idioma, construido una sola vez.

    ``clean=False`` es obligatorio: con ``clean=True`` pysbd reescribe el texto y
    la unión de las oraciones deja de reproducir el original.
    """
    codigo = MOTOR_POR_IDIOMA.get(idioma, MOTOR_POR_IDIOMA[IDIOMA_POR_DEFECTO])
    return pysbd.Segmenter(language=codigo, clean=False, char_span=False)


@lru_cache(maxsize=8)
def _abreviaturas(idioma: str) -> frozenset[str]:
    """Abreviaturas prefijas del idioma: las que nunca cierran oración."""
    propias = _ABREVIATURAS_POR_IDIOMA.get(
        idioma, _ABREVIATURAS_POR_IDIOMA[IDIOMA_POR_DEFECTO]
    )
    return (_ABREVIATURAS_COMUNES | propias) - _ABREVIATURAS_AMBIGUAS


def segmentar(texto: str, idioma: str = IDIOMA_POR_DEFECTO) -> list[str]:
    """Parte ``texto`` en oraciones completas, sin perder ni añadir caracteres.

    Invariante: ``" ".join(segmentar(t, i)) == normalizar_texto(t)``. Si algo lo
    rompiera, devuelve el texto entero como una sola oración. Un idioma fuera del
    contrato cae a español en vez de lanzar.
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

    El espacio separador se queda con la parte izquierda, como hace pysbd, para
    que la concatenación siga reproduciendo el texto.
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
    """Vuelve a unir los trozos que pysbd cortó donde no había frontera."""
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

    # Sin puntuación terminal, una continuación en minúscula es corte interno.
    if not _TERMINAL.search(fin) and inicio_derecha and inicio_derecha.islower():
        return True

    return False
