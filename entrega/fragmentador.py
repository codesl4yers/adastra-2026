"""Convierte cada ``Documento`` extraído en fragmentos listos para codificar.

Estrategia: **híbrida estructural-oracional con unidades atómicas preservadas**.
La estructura del documento define fronteras que no se cruzan; dentro de cada
frontera, el empaquetado de oraciones completas hace el trabajo real. Las tres
capas van en cascada, no en pasadas independientes:

1. **Secciones** (§4.1): un encabezado de nivel ``<= nivel_frontera`` o un
   cambio de breadcrumb abre sección. La página **no** es frontera: en PDF los
   párrafos fluyen de una página a la siguiente.
2. **Empaquetado** (§4.2): se acumulan oraciones completas hasta
   ``objetivo_palabras``, con tope duro simultáneo de ``max_palabras`` y
   ``max_tokens``. Ningún corte cae dentro de una oración, que es el requisito
   obligatorio de §3.3 del enunciado.
3. **Solape** (§4.3): cada fragmento repite las últimas oraciones del anterior
   de su misma sección. Mínimo por diseño: dos fragmentos casi idénticos
   compiten por los mismos puestos del top-10 y gastan cupos de NDCG@10.

Las filas de datasets y los elementos de mapas vectoriales (``atomico=True``,
~103 archivos del corpus) van por un cuarto camino: no se parten ni se fusionan
con prosa vecina, porque un registro partido pierde la correspondencia
``columna: valor`` y un registro fusionado con otro produce un vector que no
representa fielmente a ninguno de los dos.

Este módulo expone funciones puras; la única escritura a disco está en
:func:`fragmentar_corpus` y en el CLI (§1.6).

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
# Incluye el punto ideográfico porque el corpus trae secciones en chino dentro
# de informes del AI Index. Ver :func:`_repartir_pseudo_oracion`.
_FRONTERA_INTERNA = re.compile(r"(?<=[.!?。])\s+")

# Un token por cada 0,625 palabras. Es la estimación conservadora que usa el
# pipeline mientras no haya encoder elegido (§6.2): sobreestima, que es el error
# seguro —un fragmento más corto de lo necesario no se trunca en el encoder—.
FACTOR_TOKENS_POR_PALABRA = 1.6

# Ancho de los bins del histograma del reporte.
ANCHO_BIN_HISTOGRAMA = 25

# Bloques a partir de los que el CLI avisa antes de procesar un documento.
# pysbd no es barato por bloque —compila una expresión regular nueva por cada
# oración para ubicar su posición en el texto (ver segmentador.py)— y el atlas
# de RESDAL trae 8319 bloques él solo: varios minutos de cómputo real que, sin
# aviso, no se distinguen de un proceso colgado.
UMBRAL_AVISO_BLOQUES = 1000

# Cada cuántos documentos normales se imprime un avance. La inmensa mayoría del
# corpus tarda milisegundos, así que un aviso por documento saturaría stderr
# sin aportar nada.
PROGRESO_CADA = 200


def contar_palabras(texto: str) -> int:
    """Palabras separadas por espacios.

    ``split()`` sin argumentos y no una expresión regular: el texto ya viene
    normalizado por :func:`limpieza.normalizar_texto`, así que los separadores
    son espacios simples y cualquier otra definición solo añadiría formas de
    que dos corridas cuenten distinto.
    """
    return len(texto.split())


def estimar_tokens(texto: str) -> int:
    """Estimación de tokens mientras no haya encoder elegido (§6.2).

    En cuanto se fije el modelo hay que sustituirla por su ``AutoTokenizer`` y
    **re-fragmentar el corpus completo**: los ``num_tokens`` estimados no son
    válidos para la entrega.
    """
    return math.ceil(contar_palabras(texto) * FACTOR_TOKENS_POR_PALABRA)


@dataclass(frozen=True)
class ConfigFragmentacion:
    """Todos los parámetros del algoritmo, en un solo objeto.

    No hay constantes mágicas sueltas en el cuerpo de las funciones a propósito:
    el informe técnico tiene que poder citar la configuración exacta y la fase
    de evaluación tiene que poder barrer variantes sin editar código.
    """

    objetivo_palabras: int = 190
    """Tamaño al que apunta el empaquetado."""

    max_palabras: int = 240
    """Tope duro. Margen de 10 sobre el límite de 250 de §9.2.1 del enunciado."""

    max_tokens: int = 450
    """Tope duro. Margen sobre el límite típico de 512 del encoder."""

    min_palabras: int = 40
    """Por debajo de esto un fragmento se considera huérfano y se fusiona (§4.4)."""

    oraciones_solape: int = 1
    """Oraciones que cada fragmento repite del anterior. 0 desactiva el solape."""

    nivel_frontera: int = 6
    """Los encabezados de nivel ``<= N`` abren sección nueva."""

    respetar_atomicos: bool = True
    """Si es ``False``, las filas se empaquetan como prosa. Solo para el barrido."""

    contar_tokens: Callable[[str], int] = field(default=estimar_tokens)
    """Contador de tokens del encoder. Inyectable justamente para poder
    cambiarlo por el tokenizador real sin tocar el algoritmo (§6.2)."""

    separador_registro: str = " · "
    """Separa registros atómicos agrupados. No parte ninguno: los concatena."""

    separador_contexto: str = " · "
    """Separa los campos del prefijo de :attr:`Fragmento.texto_enriquecido`."""

    separador_breadcrumb: str = " > "
    """Separa los niveles de sección dentro de ese prefijo."""


CONFIG_POR_DEFECTO = ConfigFragmentacion()


@dataclass(frozen=True)
class Fragmento:
    """Una unidad indexable. Los ocho primeros campos son la Tabla 1 del enunciado."""

    doc_id: str
    chunk_id: str
    """``{doc_id}-c{posicion:04d}``. Único dentro del documento."""

    fuente: str
    """Copiado del ``Documento`` sin tocar: es el campo de emparejamiento con el
    ground truth del jurado (§10.2.1), y por eso es inmutable."""

    formato: str
    fenomeno: int
    posicion: int
    """Empieza en 0 y es contigua dentro del documento."""

    num_tokens: int
    """Contado sobre :attr:`texto` con ``config.contar_tokens``."""

    texto: str
    """El texto original, sin modificaciones. El enriquecimiento vive aparte."""

    # --- campos adicionales, permitidos por §3.4 del enunciado ---------------

    texto_enriquecido: str
    """Lo que se pasa al encoder. **No se serializa a metadata.jsonl.**"""

    idioma: str
    observatorio: str | None
    ruta_relativa: str
    seccion: list[str]
    """Breadcrumb heredado del bloque."""

    pagina: int | None
    """Página del primer bloque que aporta texto al fragmento."""

    num_palabras: int
    tipo_unidad: str
    """Uno de :data:`TIPOS_UNIDAD`."""

    tiene_solape: bool
    n_oraciones: int

    datos: list[dict[str, str]] = field(default_factory=list)
    """Campos que el extractor apartó del texto, uno por registro de origen.

    Es una lista y no un dict porque un fragmento puede agrupar varios
    registros cortos (``_agrupar_registros``): con un solo dict habría que
    elegir de qué fila queda el PMID, y elegir sería atribuirle a un texto el
    identificador de otro. Vacía en todo lo que no sea tabular."""


@dataclass(frozen=True)
class ReporteFragmentacion:
    """Qué salió de una corrida. Material directo para el informe técnico."""

    n_documentos: int
    n_fragmentos: int
    documentos_sin_bloques: list[str]
    """Rutas relativas. No es un error: un PDF corrupto produce cero fragmentos."""

    fragmentos_por_formato: dict[str, int]
    histograma_palabras: dict[str, int]
    """Bins de 25 palabras. La mediana debería caer entre 150 y 220 (§8.2)."""

    mediana_palabras: int
    """Una mediana muy por debajo del objetivo indica empaquetado en falso."""

    p95_palabras: int
    """Cola alta de la distribución. Es lo que roza el límite de 250."""

    n_oraciones_unicas: int
    """Fragmentos de una sola oración. Muchos significan empaquetado en falso."""

    n_atomicos: int
    n_huerfanos_fusionados: int
    n_indivisibles: int
    """Oraciones que exceden el tope por sí solas. Deben ser < 0.5% (§8.2); si
    son más, casi seguro está fallando el segmentador."""

    n_atomicos_partidos: int
    """Registros que hubo que partir por exceder el tope de 250 palabras (§5)."""


# --- estructuras internas -----------------------------------------------------


_Grupo = tuple[list["_Oracion"], bool, str, list[dict[str, str]]]
"""Lo que produce cada capa de agrupación: oraciones, si arrastra solape, qué
tipo de unidad es y los datos apartados de los registros que lo componen."""


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
        """``True`` si solo tiene títulos, es decir, si aún no tiene contenido."""
        return all(bloque.tipo == "titulo" for bloque in self.bloques)


# --- API pública --------------------------------------------------------------


def fragmentar(
    documento: Documento, config: ConfigFragmentacion = CONFIG_POR_DEFECTO
) -> list[Fragmento]:
    """Fragmenta un documento. Nunca lanza: un documento sin texto da ``[]`` (§1.5)."""
    fragmentos, _ = _fragmentar_con_estadisticas(documento, config)
    return fragmentos


def validar_fragmento(
    frag: Fragmento, config: ConfigFragmentacion = CONFIG_POR_DEFECTO
) -> list[str]:
    """Devuelve los invariantes que viola ``frag``; lista vacía si está limpio.

    Sigue el patrón de :func:`contrato.validar_documento`: informa y no lanza,
    para que una corrida sobre 1826 documentos pueda reportar todo lo que está
    mal de una vez en lugar de morir en el primero.
    """
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
    """Identificador del fragmento. Cuatro dígitos ordenan bien hasta 9999."""
    return f"{doc_id}-c{posicion:04d}"


# --- cascada ------------------------------------------------------------------


def _fragmentar_con_estadisticas(
    documento: Documento, config: ConfigFragmentacion
) -> tuple[list[Fragmento], dict[str, int]]:
    """Igual que :func:`fragmentar`, pero contando lo que el reporte necesita.

    Los contadores se llevan aquí y no se recalculan después porque "cuántos
    huérfanos se fusionaron" no se puede deducir mirando el resultado: la
    evidencia se pierde al fusionar.
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

    La comparación de breadcrumbs se hace contra el **ancla** de la sección
    abierta y por prefijo, no por igualdad. Con igualdad, un título de nivel 1 y
    el párrafo que cuelga de él caerían en secciones distintas —el título lleva
    ``ruta=[]`` y el párrafo ``ruta=["Título"]``— y §4.4 no podría fusionar
    nunca un encabezado con su cuerpo. Por prefijo, además, ``nivel_frontera``
    sigue significando algo: un subtítulo por debajo de la frontera extiende el
    ancla en vez de romperla.
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

        # §4.4: un título solo abre sección si la anterior ya tiene cuerpo. Sin
        # esta condición, la cadena "H2 > H5 > texto" —el patrón más común de
        # un informe— dejaba al H2 solo en su sección, y la fusión de huérfanos
        # no podía rescatarlo porque fusiona *dentro* de una sección. Eran
        # 13 516 títulos del corpus real, el 29 % de todos, saliendo como
        # fragmentos de dos palabras que compiten por los puestos del top-10.
        acumula_titulo = (  # noqa: E501 - la condición se lee mejor entera
            abre_seccion
            and bloque.tipo == "titulo"
            and secciones
            and atomico == secciones[-1].atomica
            and secciones[-1].sin_cuerpo
        )

        if acumula_titulo:
            # El ancla y el breadcrumb siguen al título más profundo de la
            # cadena: ahí es donde va a colgar el contenido que llegue después,
            # y es lo que hace que encaje por prefijo en esta misma sección.
            ancla = efectiva
            secciones[-1].breadcrumb = list(efectiva)
        elif abre_seccion:
            ancla = efectiva
            secciones.append(_Seccion(breadcrumb=list(efectiva), bloques=[], atomica=atomico))

        secciones[-1].bloques.append(bloque)

    return secciones


def _ruta_efectiva(bloque: Bloque) -> list[str]:
    """Breadcrumb del bloque incluyéndose a sí mismo si es un título.

    Un título no cuelga de sí mismo en ``ruta``, pero sí abre la sección que su
    cuerpo habitará, así que su ruta efectiva es la de sus hijos.
    """
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
    """§5: cada registro completo es una unidad; nunca se parte por conveniencia.

    Solo se parte cuando el registro por sí solo supera el tope de 250 palabras
    del enunciado, que no es negociable. Los registros muy cortos —una celda con
    un número— se agrupan con sus contiguos: eso es agrupación de registros
    enteros, no partición.
    """
    grupos: list[_Grupo] = []

    for registros in _agrupar_registros(seccion.bloques, config):
        texto = config.separador_registro.join(b.texto for b in registros)
        oraciones = _oraciones_de_texto(texto, documento.idioma, registros[0].pagina, config)
        if not oraciones:
            continue

        # Los datos salen de los bloques y no de las oraciones porque el texto
        # de los registros ya viene unido: partirlo en oraciones pierde la
        # correspondencia con la fila de la que salió cada una.
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

    El segmentador falla en un caso concreto del corpus: una comilla
    desbalanceada dentro de una celda de CSV —``title: Hot Zones" for
    Otolaryngologists``, de un CSV mal escapado— hace que pysbd trate el resto
    del texto como una cita y devuelva 64 KB en una sola pieza. El fragmentador
    la respetaba, porque §3.3 le prohíbe cortar dentro de una oración, y emitía
    fragmentos de hasta 8 995 palabras: 1 343 fragmentos por encima del límite
    de 250 de §9.2.1.

    La salida al conflicto entre los dos requisitos es no tratarlos como
    equivalentes: si dentro del texto hay puntuación terminal seguida de
    espacio, **esas son oraciones de verdad** que el segmentador no supo ver, y
    cortar por ellas cumple §3.3 en vez de violarlo. Si no las hay —una oración
    larga de verdad, o texto sin puntuación como el chino— no se toca nada y el
    fragmento sale grande, como hasta ahora.

    Conserva el texto exacto: ``" ".join(resultado) == texto``.
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
    """Segmenta bloque a bloque, nunca el texto concatenado de la sección.

    Concatenar antes de segmentar dejaría que el final de un párrafo sin punto
    se pegue al principio del siguiente, inventando una oración que no está en
    el documento.
    """
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
    propio: no se trunca, no se parte y no se descarta (§3.3). Se registra
    después como violación de tamaño con el motivo "oración indivisible".
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

    Hacia adelante por defecto y hacia atrás si es el último. Un encabezado
    suelto sin cuerpo detrás es basura semántica en el índice: un vector que
    solo dice "Metodología" contamina el ranking de cualquier consulta sobre
    metodología.
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
    """Los dos topes son simultáneos: manda el que se alcance primero (§2.1).

    Lo comprueban por igual el empaquetado, la fusión de huérfanos y el solape.
    Un solo sitio que se olvide del tope de tokens basta para que un fragmento
    llegue al encoder y se trunque en silencio.
    """
    return (
        _palabras(oraciones) <= config.max_palabras
        and config.contar_tokens(_texto_de(oraciones)) <= presupuesto
    )


def _aplicar_solape(
    grupos: list[list[_Oracion]], config: ConfigFragmentacion, presupuesto: int
) -> list[tuple[list[_Oracion], bool]]:
    """Capa 3: cada fragmento arranca repitiendo la cola del anterior.

    El solape se toma del contenido **propio** del fragmento anterior, no del
    que ya arrastraba, para que una oración no se propague en cadena por toda la
    sección. Y si no cabe dentro de los topes, se omite: el tope manda sobre el
    solape.
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
    """§6.3: observatorio, título del documento y breadcrumb de secciones.

    Un fragmento de la página 40 de un informe que dice "el crecimiento fue del
    12%" no es recuperable sin saber de qué informe y de qué sección viene. El
    prefijo le devuelve ese contexto al vector sin tocar lo que se reporta al
    jurado. Es legal: no interviene ningún decoder, es concatenación de metadata
    que ya existía.
    """
    partes = [
        _meta(documento, "observatorio"),
        _meta(documento, "titulo"),
        config.separador_breadcrumb.join(seccion) if seccion else None,
    ]
    return config.separador_contexto.join(parte for parte in partes if parte)


def _presupuesto_de_tokens(prefijo: str, config: ConfigFragmentacion) -> int:
    """Tokens que le quedan al texto una vez descontado el prefijo.

    Lo que entra al encoder es ``texto_enriquecido``, así que el tope de
    ``max_tokens`` se le aplica a él. Descontar el prefijo aquí es lo que evita
    el truncamiento silencioso de §14.3 del addendum del encoder: el tokenizador
    corta sin avisar y el fragmento se indexa incompleto.
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
    """Fragmenta todos los ``Documento`` de ``entrada`` y escribe la salida.

    ``entrada`` es el ``extraidos/`` que deja el orquestador. Produce
    ``fragmentos.jsonl`` —una línea por fragmento, en orden de documento y
    posición— y ``reporte_fragmentacion.json``. Dos corridas sobre el mismo
    corpus dan el mismo archivo byte a byte.
    """
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

    Separada de :func:`fragmentar_corpus` porque el barrido de configuraciones
    (§8.3) prueba seis variantes sobre el mismo corpus y escribir seis copias de
    los fragmentos solo para calcular medianas es tiempo y disco tirados.

    ``on_progreso``, si se pasa, se llama antes de cada documento con
    ``(índice, total, documento)``. Existe para que el CLI pueda avisar en
    documentos grandes (ver ``UMBRAL_AVISO_BLOQUES``) sin que esta función deje
    de ser pura por defecto para quien la llama sin él, como el barrido.
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
    """Avance a stderr: un aviso especial en documentos grandes, uno periódico en el resto."""
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

    Se recorre en el orden del manifiesto cuando existe —que es el orden por
    ``(fuente, ruta_relativa)``— y por nombre de archivo cuando no. El orden
    importa: fija el orden de las líneas de ``fragmentos.jsonl`` y con él el
    diff entre corridas.
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

    Sin interpolación y sin dependencias: devuelve siempre un valor que existe
    en los datos, que es lo que quiere leer alguien que compara dos
    configuraciones en la tabla del §8.3. Un corpus sin fragmentos da 0 en vez
    de reventar: es el caso real de un directorio de extraídos donde todos los
    extractores son todavía stubs.
    """
    if not ordenados:
        return 0
    indice = math.ceil(porcentaje / 100 * len(ordenados)) - 1
    return ordenados[max(0, min(indice, len(ordenados) - 1))]


def _histograma(valores: Iterable[int]) -> dict[str, int]:
    """Bins de 25 palabras.

    Las claves llevan ceros a la izquierda ("0175-0199") para que ordenen igual
    como texto que como número: el reporte se serializa con ``sort_keys`` y sin
    el relleno "100-124" quedaría antes que "25-49".
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
    tokens acaba usando cada opción: es la diferencia entre una corrida válida
    para la entrega y una que no lo es, y no se nota mirando la salida.
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
        # Import local: `fragmentador` no depende de `encoder`, que arrastra
        # transformers. La dependencia va en un solo sentido.
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
