"""Convierte cada ``Documento`` extraído en fragmentos listos para codificar.

Estrategia híbrida estructural-oracional, en cascada de tres capas:

1. **Secciones**: un encabezado o un cambio de breadcrumb abre sección. La
   página no es frontera.
2. **Empaquetado**: oraciones completas hasta ``objetivo_palabras``, con tope
   duro simultáneo de palabras y de tokens. Ningún corte cae dentro de una
   oración (§3.3 del enunciado).
3. **Solape**: cada fragmento repite la cola del anterior de su misma sección.

Los bloques ``atomico`` —filas de datasets, features de mapas— van por su propio
camino: no se parten ni se mezclan con prosa vecina.

Funciones puras; la única escritura a disco está en :func:`fragmentar_corpus`.
El porqué de cada capa y de sus parámetros está en
``docs/specs/spec-fragmentador.md`` y en ``docs/decisiones/``.

Uso::

    python fragmentador.py --entrada extraidos --salida fragmentos
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contrato import (
    FENOMENOS,
    FORMATOS,
    IDIOMAS,
    Bloque,
    Documento,
    documento_desde_dict,
)
from limpieza import normalizar_texto
from segmentador import segmentar

NOMBRE_SALIDA = "fragmentos.jsonl"
NOMBRE_REPORTE = "reporte_fragmentacion.json"
NOMBRE_MANIFIESTO = "manifiesto.jsonl"

TIPOS_UNIDAD: tuple[str, ...] = ("prosa", "atomico", "titulo_huerfano")

# Frontera de oración dentro de lo que el segmentador devolvió como una sola.
# El punto ideográfico está porque el corpus trae secciones en chino.
_FRONTERA_INTERNA = re.compile(r"(?<=[.!?。])\s+")

# Factor de la estimación de tokens. No es conservador sobre este corpus: la
# entrega se fragmenta con el tokenizador real (docs/decisiones/conteo-de-tokens.md).
FACTOR_TOKENS_POR_PALABRA = 1.6

ANCHO_BIN_HISTOGRAMA = 25

# Avisos de avance: uno por documento grande (pysbd tarda minutos con miles de
# bloques) y uno periódico en el resto.
UMBRAL_AVISO_BLOQUES = 1000
PROGRESO_CADA = 200


def contar_palabras(texto: str) -> int:
    """Palabras separadas por espacios, sobre texto ya normalizado."""
    return len(texto.split())


def estimar_tokens(texto: str) -> int:
    """Estimación de tokens. Para la entrega se usa el tokenizador real del
    encoder (``encoder.config_fragmentacion_con_tokenizador``)."""
    return math.ceil(contar_palabras(texto) * FACTOR_TOKENS_POR_PALABRA)


@dataclass(frozen=True)
class ConfigFragmentacion:
    """Todos los parámetros del algoritmo, en un solo objeto.

    Sin constantes sueltas en el cuerpo de las funciones: el informe tiene que
    poder citar la configuración exacta y el barrido, variarla sin tocar código.
    """

    objetivo_palabras: int = 190  # tamaño al que apunta el empaquetado
    max_palabras: int = 240       # tope duro; margen sobre las 250 de §9.2.1
    max_tokens: int = 450         # tope duro; margen sobre las 512 típicas
    min_palabras: int = 40        # por debajo, el fragmento se fusiona (§4.4)
    oraciones_solape: int = 1     # 0 desactiva el solape
    nivel_frontera: int = 6       # encabezados de nivel <= N abren sección
    respetar_atomicos: bool = True  # False empaqueta las filas como prosa

    # Inyectable para poder pasar del estimador al tokenizador real del encoder.
    contar_tokens: Callable[[str], int] = field(default=estimar_tokens)

    separador_registro: str = " · "    # entre registros atómicos agrupados
    separador_contexto: str = " · "    # entre campos del prefijo de contexto
    separador_breadcrumb: str = " > "  # entre niveles de sección de ese prefijo


CONFIG_POR_DEFECTO = ConfigFragmentacion()


@dataclass(frozen=True)
class Fragmento:
    """Una unidad indexable. Los ocho primeros campos son la Tabla 1 del enunciado."""

    doc_id: str
    chunk_id: str    # {doc_id}-c{posicion:04d}, único dentro del documento
    fuente: str      # copiado del Documento sin tocar
    formato: str
    fenomeno: int
    posicion: int    # desde 0 y contigua dentro del documento
    num_tokens: int  # contado sobre `texto` con config.contar_tokens
    texto: str       # el original, sin modificaciones

    # --- campos adicionales, permitidos por §3.4 del enunciado ---------------

    texto_enriquecido: str  # lo que ve el encoder; no va a metadata.jsonl
    idioma: str
    observatorio: str | None
    ruta_relativa: str
    seccion: list[str]     # breadcrumb heredado del bloque
    pagina: int | None     # la del primer bloque que aporta texto
    num_palabras: int
    tipo_unidad: str       # uno de TIPOS_UNIDAD
    tiene_solape: bool
    n_oraciones: int

    # Lo que el extractor apartó del texto, un dict por registro de origen. Es
    # lista porque un fragmento puede agrupar varios registros y elegir uno solo
    # sería atribuirle a un texto el identificador de otro.
    datos: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ReporteFragmentacion:
    """Qué salió de una corrida. Material directo para el informe técnico."""

    n_documentos: int
    n_fragmentos: int
    documentos_sin_bloques: list[str]  # no es un error: un PDF corrupto da cero
    fragmentos_por_formato: dict[str, int]
    histograma_palabras: dict[str, int]  # bins de 25; la mediana debe caer en 150-220
    mediana_palabras: int
    p95_palabras: int
    n_oraciones_unicas: int  # muchos significan empaquetado en falso
    n_atomicos: int
    n_huerfanos_fusionados: int
    n_indivisibles: int      # oraciones que exceden el tope solas; deben ser < 0,5 %
    n_atomicos_partidos: int


# --- estructuras internas -----------------------------------------------------


# Oraciones, si arrastra solape, tipo de unidad y datos apartados.
_Grupo = tuple[list["_Oracion"], bool, str, list[dict[str, str]]]


@dataclass(frozen=True)
class _Oracion:
    """Una oración con la trazabilidad de su bloque de origen."""

    texto: str
    pagina: int | None
    es_titulo: bool


@dataclass
class _Seccion:
    """Bloques consecutivos entre los que el empaquetado puede repartir texto."""

    breadcrumb: list[str]
    bloques: list[Bloque]
    atomica: bool

    @property
    def sin_cuerpo(self) -> bool:
        """``True`` si solo tiene títulos: aún no tiene contenido."""
        return all(bloque.tipo == "titulo" for bloque in self.bloques)


# --- API pública --------------------------------------------------------------


def fragmentar(
    documento: Documento, config: ConfigFragmentacion = CONFIG_POR_DEFECTO
) -> list[Fragmento]:
    """Fragmenta un documento. Nunca lanza: uno sin texto da ``[]``."""
    fragmentos, _ = _fragmentar_con_estadisticas(documento, config)
    return fragmentos


def validar_fragmento(
    frag: Fragmento, config: ConfigFragmentacion = CONFIG_POR_DEFECTO
) -> list[str]:
    """Devuelve los invariantes que viola ``frag``; vacía si está limpio. No lanza."""
    violaciones: list[str] = []

    if not isinstance(frag.fuente, str) or not frag.fuente.strip():
        violaciones.append("fuente vacía: es el campo de emparejamiento, no puede faltar")
    if not isinstance(frag.doc_id, str) or not frag.doc_id.strip():
        violaciones.append("doc_id vacío")

    if not isinstance(frag.posicion, int) or isinstance(frag.posicion, bool):
        violaciones.append("posicion no es int")
    elif frag.posicion < 0:
        violaciones.append(f"posicion {frag.posicion} debe ser >= 0")
    elif frag.chunk_id != chunk_id_de(frag.doc_id, frag.posicion):
        violaciones.append(
            f"chunk_id {frag.chunk_id!r} no corresponde a doc_id + posicion "
            f"(esperado {chunk_id_de(frag.doc_id, frag.posicion)!r})"
        )

    if frag.formato not in FORMATOS:
        violaciones.append(f"formato {frag.formato!r} no está en {FORMATOS}")
    if frag.fenomeno not in FENOMENOS:
        violaciones.append(f"fenomeno {frag.fenomeno!r} no está en {FENOMENOS}")
    if frag.idioma not in IDIOMAS:
        violaciones.append(f"idioma {frag.idioma!r} no está en {IDIOMAS}")
    if frag.tipo_unidad not in TIPOS_UNIDAD:
        violaciones.append(f"tipo_unidad {frag.tipo_unidad!r} no está en {TIPOS_UNIDAD}")

    violaciones.extend(_violaciones_de_texto(frag, config))

    if not isinstance(frag.seccion, list) or not all(isinstance(s, str) for s in frag.seccion):
        violaciones.append("seccion debe ser una lista de str")
    if frag.pagina is not None:
        if not isinstance(frag.pagina, int) or isinstance(frag.pagina, bool):
            violaciones.append("pagina no es int")
        elif frag.pagina < 1:
            violaciones.append(f"pagina {frag.pagina} debe ser >= 1")
    if not isinstance(frag.tiene_solape, bool):
        violaciones.append("tiene_solape no es bool")
    if not isinstance(frag.n_oraciones, int) or frag.n_oraciones < 1:
        violaciones.append(f"n_oraciones {frag.n_oraciones!r} debe ser >= 1")

    return violaciones


def _violaciones_de_texto(frag: Fragmento, config: ConfigFragmentacion) -> list[str]:
    """Comprueba el texto y los tres conteos que dependen de él."""
    violaciones: list[str] = []

    if not isinstance(frag.texto, str) or not frag.texto.strip():
        violaciones.append("texto vacío o solo espacios")
        return violaciones

    if frag.texto != normalizar_texto(frag.texto):
        violaciones.append("texto sin normalizar")
    if not frag.texto_enriquecido.endswith(frag.texto):
        violaciones.append(
            "texto_enriquecido no termina en texto: el prefijo de contexto no "
            "puede alterar lo que se reporta al jurado"
        )

    if frag.num_palabras != contar_palabras(frag.texto):
        violaciones.append(
            f"num_palabras {frag.num_palabras} no coincide con el texto "
            f"({contar_palabras(frag.texto)})"
        )
    if frag.num_tokens != config.contar_tokens(frag.texto):
        violaciones.append(
            f"num_tokens {frag.num_tokens} no coincide con el contador de la "
            f"configuración ({config.contar_tokens(frag.texto)})"
        )

    if contar_palabras(frag.texto) > config.max_palabras:
        motivo = (
            "oración indivisible: no se puede partir sin violar §3.3"
            if frag.n_oraciones <= 1
            else "el empaquetado dejó pasar el tope"
        )
        violaciones.append(
            f"tamaño: {contar_palabras(frag.texto)} palabras > "
            f"{config.max_palabras} ({motivo})"
        )
    if frag.num_tokens > config.max_tokens and frag.n_oraciones > 1:
        violaciones.append(f"tamaño: {frag.num_tokens} tokens > {config.max_tokens}")

    return violaciones


def chunk_id_de(doc_id: str, posicion: int) -> str:
    """Identificador del fragmento. Cuatro dígitos para que ordene como texto."""
    return f"{doc_id}-c{posicion:04d}"


# --- cascada ------------------------------------------------------------------


def _fragmentar_con_estadisticas(
    documento: Documento, config: ConfigFragmentacion
) -> tuple[list[Fragmento], dict[str, int]]:
    """Igual que :func:`fragmentar`, contando lo que el reporte necesita.

    Los contadores se llevan aquí porque "cuántos huérfanos se fusionaron" no se
    puede deducir del resultado: la evidencia se pierde al fusionar.
    """
    estadisticas = {"huerfanos_fusionados": 0, "atomicos_partidos": 0}

    bloques = [b for b in documento.bloques if normalizar_texto(b.texto)]
    if not bloques:
        return [], estadisticas

    fragmentos: list[Fragmento] = []
    for seccion in _agrupar_secciones(bloques, config):
        prefijo = _prefijo_de_contexto(documento, seccion.breadcrumb, config)
        presupuesto = _presupuesto_de_tokens(prefijo, config)

        if seccion.atomica:
            grupos = _grupos_atomicos(seccion, documento, config, presupuesto, estadisticas)
        else:
            grupos = _grupos_de_prosa(seccion, documento, config, presupuesto, estadisticas)

        for oraciones, tiene_solape, tipo_unidad, datos in grupos:
            fragmentos.append(
                _construir_fragmento(
                    documento=documento,
                    config=config,
                    posicion=len(fragmentos),
                    oraciones=oraciones,
                    seccion=seccion.breadcrumb,
                    tipo_unidad=tipo_unidad,
                    tiene_solape=tiene_solape,
                    prefijo=prefijo,
                    datos=datos,
                )
            )

    return fragmentos, estadisticas


def _agrupar_secciones(bloques: list[Bloque], config: ConfigFragmentacion) -> list[_Seccion]:
    """Capa 1: reparte los bloques en secciones que el empaquetado no cruzará.

    Los breadcrumbs se comparan contra el ancla de la sección abierta **por
    prefijo**, no por igualdad: con igualdad, un título y el párrafo que cuelga
    de él caerían en secciones distintas y nunca podrían fusionarse.
    """
    secciones: list[_Seccion] = []
    ancla: list[str] = []

    for bloque in bloques:
        efectiva = _ruta_efectiva(bloque)
        atomico = bool(bloque.atomico) and config.respetar_atomicos

        abre_seccion = (
            not secciones
            or atomico != secciones[-1].atomica
            or _es_titulo_frontera(bloque, config)
            or efectiva[: len(ancla)] != ancla
        )

        # Un título solo abre sección si la anterior ya tiene cuerpo, o la cadena
        # "H2 > H5 > texto" deja al H2 huérfano (fragmentos-fuera-de-norma.md §2).
        acumula_titulo = (  # noqa: E501 - la condición se lee mejor entera
            abre_seccion
            and bloque.tipo == "titulo"
            and secciones
            and atomico == secciones[-1].atomica
            and secciones[-1].sin_cuerpo
        )

        if acumula_titulo:
            # El ancla sigue al título más profundo: ahí colgará el contenido.
            ancla = efectiva
            secciones[-1].breadcrumb = list(efectiva)
        elif abre_seccion:
            ancla = efectiva
            secciones.append(_Seccion(breadcrumb=list(efectiva), bloques=[], atomica=atomico))

        secciones[-1].bloques.append(bloque)

    return secciones


def _ruta_efectiva(bloque: Bloque) -> list[str]:
    """Breadcrumb del bloque, incluyéndose a sí mismo si es un título: un título
    abre la sección que su cuerpo habitará, así que su ruta es la de sus hijos."""
    if bloque.tipo == "titulo":
        return [*bloque.ruta, bloque.texto]
    return list(bloque.ruta)


def _es_titulo_frontera(bloque: Bloque, config: ConfigFragmentacion) -> bool:
    return (
        bloque.tipo == "titulo"
        and isinstance(bloque.nivel, int)
        and bloque.nivel <= config.nivel_frontera
    )


def _grupos_de_prosa(
    seccion: _Seccion,
    documento: Documento,
    config: ConfigFragmentacion,
    presupuesto: int,
    estadisticas: dict[str, int],
) -> list[_Grupo]:
    """Capas 2 a 4 sobre una sección de prosa."""
    oraciones = _oraciones_de(seccion.bloques, documento.idioma, config)
    if not oraciones:
        return []

    grupos = _empaquetar(oraciones, config, presupuesto)
    grupos, fusiones = _fusionar_huerfanos(grupos, config, presupuesto)
    estadisticas["huerfanos_fusionados"] += fusiones

    tipo_unidad = (
        "titulo_huerfano"
        if len(grupos) == 1 and all(o.es_titulo for o in grupos[0])
        else "prosa"
    )
    return [
        (grupo, solape, tipo_unidad, [])
        for grupo, solape in _aplicar_solape(grupos, config, presupuesto)
    ]


def _grupos_atomicos(
    seccion: _Seccion,
    documento: Documento,
    config: ConfigFragmentacion,
    presupuesto: int,
    estadisticas: dict[str, int],
) -> list[_Grupo]:
    """§5: cada registro completo es una unidad.

    Solo se parte si por sí solo supera el tope de palabras. Los muy cortos se
    agrupan con sus contiguos, que es agrupar registros enteros, no partirlos.
    """
    grupos: list[_Grupo] = []

    for registros in _agrupar_registros(seccion.bloques, config):
        texto = config.separador_registro.join(b.texto for b in registros)
        oraciones = _oraciones_de_texto(texto, documento.idioma, registros[0].pagina, config)
        if not oraciones:
            continue

        # De los bloques y no de las oraciones: el texto ya viene unido y
        # segmentarlo pierde la correspondencia con la fila de origen.
        datos = [dict(b.datos) for b in registros if b.datos]

        if contar_palabras(texto) <= config.max_palabras:
            grupos.append((oraciones, False, "atomico", datos))
            continue

        estadisticas["atomicos_partidos"] += 1
        for trozo in _empaquetar(oraciones, config, presupuesto):
            grupos.append((trozo, False, "atomico", datos))

    return grupos


def _agrupar_registros(bloques: list[Bloque], config: ConfigFragmentacion) -> list[list[Bloque]]:
    """Junta registros contiguos demasiado cortos para ser un fragmento útil."""
    grupos: list[list[Bloque]] = []
    acumulado: list[Bloque] = []

    for bloque in bloques:
        if contar_palabras(bloque.texto) >= config.min_palabras:
            if acumulado:
                grupos.append(acumulado)
                acumulado = []
            grupos.append([bloque])
            continue

        acumulado.append(bloque)
        unido = config.separador_registro.join(b.texto for b in acumulado)
        if contar_palabras(unido) >= config.objetivo_palabras:
            grupos.append(acumulado)
            acumulado = []

    if acumulado:
        grupos.append(acumulado)
    return grupos


def _repartir_pseudo_oracion(texto: str, config: ConfigFragmentacion) -> list[str]:
    """Parte una "oración" que se pasa de tamaño y trae fronteras dentro.

    Si hay puntuación terminal seguida de espacio, son oraciones que el
    segmentador no supo ver y cortar por ellas cumple §3.3 en vez de violarlo;
    si no las hay, no se toca nada. Conserva el texto: ``" ".join(r) == texto``.
    El caso que lo motivó está en ``docs/decisiones/fragmentos-fuera-de-norma.md`` §3.
    """
    if contar_palabras(texto) <= config.max_palabras:
        return [texto]

    trozos = [trozo for trozo in _FRONTERA_INTERNA.split(texto) if trozo.strip()]
    if len(trozos) <= 1 or " ".join(trozos) != texto:
        return [texto]
    return trozos


def _oraciones_de(
    bloques: Iterable[Bloque], idioma: str, config: ConfigFragmentacion
) -> list[_Oracion]:
    """Segmenta bloque a bloque, nunca el texto concatenado de la sección:
    concatenar antes inventa oraciones que no están en el documento."""
    oraciones: list[_Oracion] = []
    for bloque in bloques:
        for texto in _segmentar(bloque.texto, idioma, config):
            oraciones.append(
                _Oracion(texto=texto, pagina=bloque.pagina, es_titulo=bloque.tipo == "titulo")
            )
    return oraciones


def _oraciones_de_texto(
    texto: str, idioma: str, pagina: int | None, config: ConfigFragmentacion
) -> list[_Oracion]:
    return [
        _Oracion(texto=trozo, pagina=pagina, es_titulo=False)
        for trozo in _segmentar(texto, idioma, config)
    ]


def _segmentar(texto: str, idioma: str, config: ConfigFragmentacion) -> list[str]:
    """El segmentador, con el rescate de las pseudo-oraciones encima."""
    return [
        trozo
        for oracion in segmentar(texto, idioma)
        for trozo in _repartir_pseudo_oracion(oracion, config)
    ]


def _empaquetar(
    oraciones: list[_Oracion], config: ConfigFragmentacion, presupuesto: int
) -> list[list[_Oracion]]:
    """Capa 2: acumula oraciones completas hasta el objetivo, con dos topes duros.

    Una oración que por sí sola pasa de ``max_palabras`` sale como fragmento
    propio: no se trunca, no se parte y no se descarta (§3.3).
    """
    grupos: list[list[_Oracion]] = []
    actual: list[_Oracion] = []

    for oracion in oraciones:
        if actual:
            cabe = _cabe([*actual, oracion], config, presupuesto)
            if not cabe or _palabras(actual) >= config.objetivo_palabras:
                grupos.append(actual)
                actual = []
        actual.append(oracion)

    if actual:
        grupos.append(actual)
    return grupos


def _fusionar_huerfanos(
    grupos: list[list[_Oracion]], config: ConfigFragmentacion, presupuesto: int
) -> tuple[list[list[_Oracion]], int]:
    """§4.4: un fragmento por debajo de ``min_palabras`` se fusiona con un vecino.

    Hacia adelante por defecto y hacia atrás si es el último.
    """
    resultado = [list(grupo) for grupo in grupos]
    fusiones = 0
    indice = 0

    while indice < len(resultado) and len(resultado) > 1:
        if _palabras(resultado[indice]) >= config.min_palabras:
            indice += 1
            continue

        siguiente = indice + 1
        if siguiente < len(resultado) and _cabe(
            [*resultado[indice], *resultado[siguiente]], config, presupuesto
        ):
            resultado[indice] = resultado[indice] + resultado.pop(siguiente)
            fusiones += 1
        elif indice > 0 and _cabe(
            [*resultado[indice - 1], *resultado[indice]], config, presupuesto
        ):
            resultado[indice - 1] = resultado[indice - 1] + resultado.pop(indice)
            fusiones += 1
        else:
            indice += 1

    return resultado, fusiones


def _cabe(oraciones: list[_Oracion], config: ConfigFragmentacion, presupuesto: int) -> bool:
    """Los dos topes son simultáneos: manda el que se alcance primero.

    Lo usan el empaquetado, la fusión de huérfanos y el solape: un solo sitio que
    se olvide del tope de tokens basta para que el encoder trunque en silencio.
    """
    return (
        _palabras(oraciones) <= config.max_palabras
        and config.contar_tokens(_texto_de(oraciones)) <= presupuesto
    )


def _aplicar_solape(
    grupos: list[list[_Oracion]], config: ConfigFragmentacion, presupuesto: int
) -> list[tuple[list[_Oracion], bool]]:
    """Capa 3: cada fragmento arranca repitiendo la cola del anterior.

    Se toma del contenido propio del anterior, no del que ya arrastraba, para que
    una oración no se propague en cadena. Si no cabe, se omite: manda el tope.
    """
    if config.oraciones_solape <= 0 or len(grupos) < 2:
        return [(grupo, False) for grupo in grupos]

    solapados: list[tuple[list[_Oracion], bool]] = [(grupos[0], False)]
    for anterior, grupo in zip(grupos, grupos[1:]):
        candidato = [*anterior[-config.oraciones_solape :], *grupo]
        if _cabe(candidato, config, presupuesto):
            solapados.append((candidato, True))
        else:
            solapados.append((grupo, False))
    return solapados


# --- construcción del fragmento ------------------------------------------------


def _construir_fragmento(
    *,
    documento: Documento,
    config: ConfigFragmentacion,
    posicion: int,
    oraciones: list[_Oracion],
    seccion: list[str],
    tipo_unidad: str,
    tiene_solape: bool,
    prefijo: str,
    datos: list[dict[str, str]] | None = None,
) -> Fragmento:
    texto = _texto_de(oraciones)
    return Fragmento(
        doc_id=documento.doc_id,
        chunk_id=chunk_id_de(documento.doc_id, posicion),
        fuente=documento.fuente,
        formato=documento.formato,
        fenomeno=documento.fenomeno,
        posicion=posicion,
        num_tokens=config.contar_tokens(texto),
        texto=texto,
        texto_enriquecido=f"{prefijo}\n{texto}" if prefijo else texto,
        idioma=documento.idioma,
        observatorio=_meta(documento, "observatorio"),
        ruta_relativa=_ruta_relativa(documento),
        seccion=list(seccion),
        pagina=_primera_pagina(oraciones),
        num_palabras=contar_palabras(texto),
        tipo_unidad=tipo_unidad,
        tiene_solape=tiene_solape,
        n_oraciones=len(oraciones),
        datos=list(datos or []),
    )


def _prefijo_de_contexto(
    documento: Documento, seccion: list[str], config: ConfigFragmentacion
) -> str:
    """Observatorio, título del documento y breadcrumb, por delante del texto.

    Ver ``docs/decisiones/enriquecimiento-de-contexto.md``.
    """
    partes = [
        _meta(documento, "observatorio"),
        _meta(documento, "titulo"),
        config.separador_breadcrumb.join(seccion) if seccion else None,
    ]
    return config.separador_contexto.join(parte for parte in partes if parte)


def _presupuesto_de_tokens(prefijo: str, config: ConfigFragmentacion) -> int:
    """Tokens que le quedan al texto una vez descontado el prefijo.

    Lo que entra al encoder es ``texto_enriquecido``, así que el tope se le
    aplica a él; sin descontarlo, el tokenizador trunca sin avisar.
    """
    if not prefijo:
        return config.max_tokens
    return max(1, config.max_tokens - config.contar_tokens(prefijo))


def _meta(documento: Documento, clave: str) -> str | None:
    valor = documento.meta.get(clave) if isinstance(documento.meta, dict) else None
    return valor if isinstance(valor, str) and valor.strip() else None


def _ruta_relativa(documento: Documento) -> str:
    """Trazabilidad hasta el archivo de origen; cae a ``fuente`` si falta."""
    return _meta(documento, "ruta_relativa") or documento.fuente


def _primera_pagina(oraciones: list[_Oracion]) -> int | None:
    for oracion in oraciones:
        if oracion.pagina is not None:
            return oracion.pagina
    return None


def _texto_de(oraciones: Iterable[_Oracion]) -> str:
    return " ".join(oracion.texto for oracion in oraciones)


def _palabras(oraciones: list[_Oracion]) -> int:
    return contar_palabras(_texto_de(oraciones))


# --- corpus y persistencia ------------------------------------------------------


def fragmentar_corpus(
    entrada: Path, salida: Path, config: ConfigFragmentacion = CONFIG_POR_DEFECTO
) -> ReporteFragmentacion:
    """Fragmenta el ``extraidos/`` de ``entrada`` y escribe ``fragmentos.jsonl``
    y ``reporte_fragmentacion.json``. Dos corridas dan los mismos bytes."""
    documentos = cargar_extraidos(entrada)
    fragmentos, reporte = fragmentar_documentos(documentos, config, on_progreso=_avisar_progreso)
    _escribir_salida(fragmentos, reporte, Path(salida))
    return reporte


def fragmentar_documentos(
    documentos: list[Documento],
    config: ConfigFragmentacion = CONFIG_POR_DEFECTO,
    on_progreso: Callable[[int, int, Documento], None] | None = None,
) -> tuple[list[Fragmento], ReporteFragmentacion]:
    """Fragmenta una lista de documentos ya cargados, sin tocar el disco.

    Existe aparte para el barrido de configuraciones, que prueba seis variantes
    sobre el mismo corpus. ``on_progreso`` se llama antes de cada documento con
    ``(índice, total, documento)``; sin él la función no imprime nada.
    """
    fragmentos: list[Fragmento] = []
    sin_bloques: list[str] = []
    fusiones = partidos = 0
    total = len(documentos)

    for indice, documento in enumerate(documentos, start=1):
        if on_progreso is not None:
            on_progreso(indice, total, documento)

        propios, estadisticas = _fragmentar_con_estadisticas(documento, config)
        if not propios:
            sin_bloques.append(_ruta_relativa(documento))
        fragmentos.extend(propios)
        fusiones += estadisticas["huerfanos_fusionados"]
        partidos += estadisticas["atomicos_partidos"]

    reporte = _construir_reporte(
        documentos=documentos,
        fragmentos=fragmentos,
        sin_bloques=sin_bloques,
        fusiones=fusiones,
        partidos=partidos,
        config=config,
    )
    return fragmentos, reporte


def _avisar_progreso(indice: int, total: int, documento: Documento) -> None:
    """Avance a stderr: aviso propio en documentos grandes, periódico en el resto."""
    n_bloques = len(documento.bloques)
    if n_bloques >= UMBRAL_AVISO_BLOQUES:
        print(
            f"  [{indice}/{total}] {documento.fuente}: {n_bloques} bloques, "
            "puede tardar varios minutos en segmentarse",
            file=sys.stderr,
        )
    elif indice % PROGRESO_CADA == 0:
        print(f"  [{indice}/{total}] documentos fragmentados", file=sys.stderr)


def cargar_extraidos(entrada: Path) -> list[Documento]:
    """Lee los ``Documento`` que dejó el orquestador, en orden estable.

    En el orden del manifiesto cuando existe, y por nombre de archivo cuando no:
    ese orden es el de las líneas de ``fragmentos.jsonl``.
    """
    entrada = Path(entrada)
    if not entrada.is_dir():
        raise ValueError(f"el directorio de entrada no existe: {entrada}")

    manifiesto = entrada / NOMBRE_MANIFIESTO
    if manifiesto.is_file():
        rutas = [
            entrada / f"{json.loads(linea)['doc_id']}.json"
            for linea in manifiesto.read_text(encoding="utf-8").splitlines()
            if linea.strip()
        ]
    else:
        rutas = sorted(
            (ruta for ruta in entrada.glob("*.json") if ruta.name != NOMBRE_MANIFIESTO),
            key=lambda ruta: ruta.name,
        )

    return [
        documento_desde_dict(json.loads(ruta.read_text(encoding="utf-8"))) for ruta in rutas
    ]


def _construir_reporte(
    *,
    documentos: list[Documento],
    fragmentos: list[Fragmento],
    sin_bloques: list[str],
    fusiones: int,
    partidos: int,
    config: ConfigFragmentacion,
) -> ReporteFragmentacion:
    palabras = sorted(f.num_palabras for f in fragmentos)
    return ReporteFragmentacion(
        n_documentos=len(documentos),
        n_fragmentos=len(fragmentos),
        documentos_sin_bloques=sin_bloques,
        fragmentos_por_formato=_contar(f.formato for f in fragmentos),
        histograma_palabras=_histograma(palabras),
        mediana_palabras=_percentil(palabras, 50),
        p95_palabras=_percentil(palabras, 95),
        n_oraciones_unicas=sum(1 for f in fragmentos if f.n_oraciones == 1),
        n_atomicos=sum(1 for f in fragmentos if f.tipo_unidad == "atomico"),
        n_huerfanos_fusionados=fusiones,
        n_indivisibles=sum(
            1
            for f in fragmentos
            if f.n_oraciones == 1 and f.num_palabras > config.max_palabras
        ),
        n_atomicos_partidos=partidos,
    )


def _contar(valores: Iterable[str]) -> dict[str, int]:
    """Cuenta con las claves ordenadas, para que el reporte sea comparable."""
    conteo: dict[str, int] = {}
    for valor in valores:
        conteo[valor] = conteo.get(valor, 0) + 1
    return dict(sorted(conteo.items()))


def _percentil(ordenados: list[int], porcentaje: int) -> int:
    """Percentil por rango más cercano sobre una lista ya ordenada.

    Sin interpolar: devuelve un valor que existe en los datos. Sin fragmentos, 0.
    """
    if not ordenados:
        return 0
    indice = math.ceil(porcentaje / 100 * len(ordenados)) - 1
    return ordenados[max(0, min(indice, len(ordenados) - 1))]


def _histograma(valores: Iterable[int]) -> dict[str, int]:
    """Bins de 25 palabras.

    Las claves llevan ceros a la izquierda para que ordenen igual como texto que
    como número: el reporte se serializa con ``sort_keys``.
    """
    conteo: dict[str, int] = {}
    for valor in valores:
        inicio = (valor // ANCHO_BIN_HISTOGRAMA) * ANCHO_BIN_HISTOGRAMA
        clave = f"{inicio:04d}-{inicio + ANCHO_BIN_HISTOGRAMA - 1:04d}"
        conteo[clave] = conteo.get(clave, 0) + 1
    return dict(sorted(conteo.items()))


def fragmento_a_dict(frag: Fragmento) -> dict[str, Any]:
    """Fragmento como primitivas listas para ``json.dumps``."""
    return {
        "doc_id": frag.doc_id,
        "chunk_id": frag.chunk_id,
        "fuente": frag.fuente,
        "formato": frag.formato,
        "fenomeno": frag.fenomeno,
        "posicion": frag.posicion,
        "num_tokens": frag.num_tokens,
        "texto": frag.texto,
        "texto_enriquecido": frag.texto_enriquecido,
        "idioma": frag.idioma,
        "observatorio": frag.observatorio,
        "ruta_relativa": frag.ruta_relativa,
        "seccion": list(frag.seccion),
        "pagina": frag.pagina,
        "num_palabras": frag.num_palabras,
        "tipo_unidad": frag.tipo_unidad,
        "tiene_solape": frag.tiene_solape,
        "n_oraciones": frag.n_oraciones,
        "datos": [dict(registro) for registro in frag.datos],
    }


def _escribir_salida(
    fragmentos: list[Fragmento], reporte: ReporteFragmentacion, salida: Path
) -> None:
    salida.mkdir(parents=True, exist_ok=True)

    lineas = [
        json.dumps(fragmento_a_dict(frag), ensure_ascii=False, sort_keys=True)
        for frag in fragmentos
    ]
    (salida / NOMBRE_SALIDA).write_text(
        "".join(f"{linea}\n" for linea in lineas), encoding="utf-8", newline="\n"
    )

    (salida / NOMBRE_REPORTE).write_text(
        json.dumps(reporte.__dict__, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


# --- interfaz de línea de comandos ---------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entrada", required=True, type=Path, help="directorio extraidos/")
    parser.add_argument("--salida", required=True, type=Path, help="directorio de fragmentos")
    parser.add_argument("--objetivo-palabras", type=int, default=CONFIG_POR_DEFECTO.objetivo_palabras)
    parser.add_argument("--max-palabras", type=int, default=CONFIG_POR_DEFECTO.max_palabras)
    parser.add_argument("--max-tokens", type=int, default=CONFIG_POR_DEFECTO.max_tokens)
    parser.add_argument("--min-palabras", type=int, default=CONFIG_POR_DEFECTO.min_palabras)
    parser.add_argument("--oraciones-solape", type=int, default=CONFIG_POR_DEFECTO.oraciones_solape)
    parser.add_argument("--nivel-frontera", type=int, default=CONFIG_POR_DEFECTO.nivel_frontera)
    parser.add_argument(
        "--sin-atomicos",
        action="store_true",
        help="empaqueta las filas como prosa; solo para el barrido de §8.3",
    )
    parser.add_argument(
        "--tokenizador",
        choices=("estimado", "real"),
        default="estimado",
        help=(
            "'real' usa el tokenizador del encoder (encoder.py) y es lo que "
            "exige la entrega; 'estimado' usa ceil(palabras x 1.6) y no "
            "necesita transformers instalado"
        ),
    )
    return parser


def _config_desde_args(args: argparse.Namespace) -> ConfigFragmentacion:
    """Traduce los argumentos a la configuración del algoritmo.

    Separada de :func:`main` para poder comprobar en una prueba qué contador de
    tokens usa cada opción: no se nota mirando la salida.
    """
    config = ConfigFragmentacion(
        objetivo_palabras=args.objetivo_palabras,
        max_palabras=args.max_palabras,
        max_tokens=args.max_tokens,
        min_palabras=args.min_palabras,
        oraciones_solape=args.oraciones_solape,
        nivel_frontera=args.nivel_frontera,
        respetar_atomicos=not args.sin_atomicos,
    )

    if args.tokenizador == "real":
        # Import local: la dependencia con `encoder` va en un solo sentido, y así
        # el módulo sigue funcionando sin transformers instalado.
        from encoder import config_fragmentacion_con_tokenizador

        return config_fragmentacion_con_tokenizador(base=config)
    return config


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)

    config = _config_desde_args(args)
    if args.tokenizador == "estimado":
        print(
            "AVISO: tokens estimados con ceil(palabras x 1.6). Para la entrega "
            "hay que re-fragmentar con --tokenizador real.",
            file=sys.stderr,
        )

    reporte = fragmentar_corpus(args.entrada, args.salida, config)

    print(
        f"{reporte.n_documentos} documentos -> {reporte.n_fragmentos} fragmentos "
        f"en {args.salida}"
    )
    print(f"  por formato:         {reporte.fragmentos_por_formato}", file=sys.stderr)
    print(f"  histograma:          {reporte.histograma_palabras}", file=sys.stderr)
    print(f"  atómicos:            {reporte.n_atomicos}", file=sys.stderr)
    print(f"  huérfanos fusionados:{reporte.n_huerfanos_fusionados}", file=sys.stderr)
    print(f"  de una sola oración: {reporte.n_oraciones_unicas}", file=sys.stderr)
    print(f"  oraciones indivisibles: {reporte.n_indivisibles}", file=sys.stderr)
    print(f"  documentos sin bloques: {len(reporte.documentos_sin_bloques)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
