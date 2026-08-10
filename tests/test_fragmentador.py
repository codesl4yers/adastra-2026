"""Pruebas de la capa de fragmentación.

Cubre las 16 pruebas exigidas por §7 del spec del fragmentador. Todas trabajan
sobre ``Documento`` armados a mano: los extractores son stubs, así que esta capa
todavía no está validada contra texto real (§8.2).
"""

import json

import pytest
from conftest import bloque

from contrato import documento_a_dict, documento_desde_dict
from fragmentador import (
    CONFIG_POR_DEFECTO,
    ConfigFragmentacion,
    contar_palabras,
    estimar_tokens,
    fragmentar,
    fragmento_a_dict,
    cargar_extraidos,
    fragmentar_corpus,
    fragmentar_documentos,
    main,
    validar_fragmento,
)
from segmentador import segmentar


def prosa(n_oraciones: int, palabras: int = 12, marca: str = "a") -> str:
    """Texto de ``n_oraciones`` oraciones sin venenos de segmentación.

    Cada oración es distinta —lleva su índice— para que las pruebas puedan
    buscarla por contenido sin ambigüedad.
    """
    relleno = " ".join(f"término{i}" for i in range(palabras - 4))
    return " ".join(
        f"La oración {marca}{numero} sostiene que {relleno}." for numero in range(n_oraciones)
    )


# --- 7.6 y trazabilidad -------------------------------------------------------


def test_un_documento_sin_bloques_produce_cero_fragmentos(documento_con_bloques):
    """7.6: ningún documento tumba la corrida (§1.5)."""
    assert fragmentar(documento_con_bloques(), CONFIG_POR_DEFECTO) == []


def test_un_documento_solo_con_espacios_produce_cero_fragmentos(documento_con_bloques):
    documento = documento_con_bloques(bloque("   "))
    assert fragmentar(documento, CONFIG_POR_DEFECTO) == []


def test_la_posicion_es_contigua_desde_cero(documento_con_bloques):
    """7.7"""
    documento = documento_con_bloques(bloque(prosa(60)))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert len(fragmentos) > 1
    assert [f.posicion for f in fragmentos] == list(range(len(fragmentos)))


def test_el_chunk_id_es_unico_dentro_del_documento(documento_con_bloques):
    """7.7"""
    documento = documento_con_bloques(bloque(prosa(60)))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    chunk_ids = [f.chunk_id for f in fragmentos]
    assert len(set(chunk_ids)) == len(chunk_ids)


def test_el_chunk_id_lleva_el_doc_id_y_la_posicion(documento_con_bloques):
    documento = documento_con_bloques(bloque(prosa(60)))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert fragmentos[0].chunk_id == f"{documento.doc_id}-c0000"
    assert fragmentos[1].chunk_id == f"{documento.doc_id}-c0001"


# --- 7.1 y 7.2: contrato y límite de palabras ---------------------------------


def test_ningun_fragmento_viola_el_contrato(documento_con_bloques):
    """7.1"""
    documento = documento_con_bloques(
        bloque("Introducción", "titulo", 1),
        bloque(prosa(40), ruta=["Introducción"]),
        bloque("Metodología", "titulo", 1),
        bloque(prosa(30, marca="b"), ruta=["Metodología"], pagina=4),
    )
    for fragmento in fragmentar(documento, CONFIG_POR_DEFECTO):
        assert validar_fragmento(fragmento, CONFIG_POR_DEFECTO) == []


def test_ningun_fragmento_supera_las_250_palabras(documento_con_bloques):
    """7.2: es el límite duro de §9.2.1 del enunciado."""
    documento = documento_con_bloques(bloque(prosa(120)))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert fragmentos
    assert all(f.num_palabras <= 250 for f in fragmentos)


def test_ningun_fragmento_supera_el_tope_de_tokens(documento_con_bloques):
    documento = documento_con_bloques(bloque(prosa(120)))
    config = ConfigFragmentacion(max_tokens=120)
    for fragmento in fragmentar(documento, config):
        assert fragmento.num_tokens <= 120


def test_el_tope_de_tokens_puede_mandar_antes_que_el_de_palabras(documento_con_bloques):
    """Los dos topes son simultáneos: manda el que se alcance primero (§2.1)."""
    documento = documento_con_bloques(bloque(prosa(120)))
    holgado = fragmentar(documento, ConfigFragmentacion())
    apretado = fragmentar(documento, ConfigFragmentacion(max_tokens=60))
    assert len(apretado) > len(holgado)


# --- 7.3 y 7.4: no se pierde, no se duplica, no se parte ----------------------


def test_los_fragmentos_sin_solape_reconstruyen_el_texto(documento_con_bloques):
    """7.3"""
    documento = documento_con_bloques(
        bloque("Introducción", "titulo", 1),
        bloque(prosa(40), ruta=["Introducción"]),
        bloque(prosa(20, marca="b"), ruta=["Introducción"]),
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=0))
    reconstruido = " ".join(f.texto for f in fragmentos)
    assert reconstruido == " ".join(b.texto for b in documento.bloques)


def test_ninguna_oracion_queda_partida_entre_dos_fragmentos(documento_con_bloques):
    """7.4: el requisito obligatorio de §3.3 del enunciado."""
    documento = documento_con_bloques(bloque(prosa(80)))
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=0))

    for oracion in segmentar(documento.bloques[0].texto, documento.idioma):
        apariciones = sum(1 for f in fragmentos if oracion in f.texto)
        assert apariciones == 1, f"{oracion!r} aparece {apariciones} veces"


def test_una_oracion_indivisible_sale_entera_en_su_propio_fragmento(documento_con_bloques):
    """7.13: no se trunca, no se parte, no se descarta (§3.3 del spec)."""
    gigante = "La oración larguísima dice " + " ".join(f"término{i}" for i in range(400)) + "."
    documento = documento_con_bloques(bloque(gigante))

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].texto == gigante
    assert fragmentos[0].n_oraciones == 1


# --- pseudo-oraciones: cuando el segmentador no pudo segmentar ----------------
#
# En el corpus real hay celdas de CSV con comillas desbalanceadas que hacen que
# pysbd devuelva 64 KB de texto como una sola "oración". El fragmentador la
# respetaba —§3.3 le prohíbe cortar dentro de una oración— y emitía fragmentos
# de hasta 8 995 palabras, violando el límite de 250 de §9.2.1. La salida es
# distinguir una oración larga de verdad de una que trae fronteras dentro.


def test_una_pseudo_oracion_se_reparte_por_sus_fronteras_internas():
    """Si dentro hay puntuación terminal seguida de espacio, hay oraciones de
    verdad ahí dentro: cortar por ellas no viola §3.3, lo cumple."""
    from fragmentador import _repartir_pseudo_oracion

    pegado = " ".join(f"La frase número {i} termina aquí." for i in range(60))

    trozos = _repartir_pseudo_oracion(pegado, CONFIG_POR_DEFECTO)

    assert len(trozos) == 60
    assert all(contar_palabras(t) <= CONFIG_POR_DEFECTO.max_palabras for t in trozos)


def test_una_oracion_legitima_larga_no_se_reparte():
    """§3.3 no se negocia: sin fronteras internas no hay nada que cortar, por
    grande que sea. Es el mismo caso que protege la prueba 7.13."""
    from fragmentador import _repartir_pseudo_oracion

    larga = "La oración larguísima dice " + " ".join(f"término{i}" for i in range(400)) + "."

    assert _repartir_pseudo_oracion(larga, CONFIG_POR_DEFECTO) == [larga]


def test_repartir_no_pierde_ni_anade_texto():
    """El mismo invariante que el segmentador: la trazabilidad no se negocia."""
    from fragmentador import _repartir_pseudo_oracion

    pegado = " ".join(f"Registro {i} con su dato. Fuente {i}." for i in range(80))

    assert " ".join(_repartir_pseudo_oracion(pegado, CONFIG_POR_DEFECTO)) == pegado


def test_una_oracion_dentro_del_tope_no_se_toca():
    """Repartir lo que ya cabe solo produciría fragmentos más pobres."""
    from fragmentador import _repartir_pseudo_oracion

    corta = "Primera frase. Segunda frase. Tercera frase."

    assert _repartir_pseudo_oracion(corta, CONFIG_POR_DEFECTO) == [corta]


def test_un_bloque_atomico_gigante_no_produce_fragmentos_fuera_de_norma(documento_con_bloques):
    """El caso F1-AIINDEX-041-c0401: una fila de CSV con cientos de registros
    concatenados. Ninguno de los fragmentos que salgan puede pasar de 250."""
    fila = " ".join(
        f"pmid: {30000000 + i} | title: Estudio número {i} sobre la materia." for i in range(300)
    )
    documento = documento_con_bloques(bloque(fila, "fila", atomico=True))

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert fragmentos
    assert all(f.num_palabras <= 250 for f in fragmentos)
    assert " ".join(f.texto for f in fragmentos) == fila


def test_una_oracion_indivisible_se_reporta_como_violacion_de_tamano(documento_con_bloques):
    gigante = "La oración larguísima dice " + " ".join(f"término{i}" for i in range(400)) + "."
    documento = documento_con_bloques(bloque(gigante))

    violaciones = validar_fragmento(
        fragmentar(documento, CONFIG_POR_DEFECTO)[0], CONFIG_POR_DEFECTO
    )

    assert any("oración indivisible" in v for v in violaciones)


# --- 7.14 y §6: metadata y enriquecimiento ------------------------------------


def test_el_texto_conserva_el_original_y_el_prefijo_vive_aparte(documento_con_bloques):
    """7.14: ``texto`` es el texto original sin modificaciones (Tabla 1)."""
    documento = documento_con_bloques(
        bloque("Metodología", "titulo", 1),
        bloque(prosa(10), ruta=["Metodología"]),
        meta={
            "ruta_relativa": "F1_Observatorio/informe.pdf",
            "observatorio": "CSET_Georgetown",
            "titulo": "Informe anual 2025",
        },
    )
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]

    assert "CSET_Georgetown" not in fragmento.texto
    assert "Informe anual 2025" not in fragmento.texto
    assert fragmento.texto_enriquecido.endswith(fragmento.texto)
    assert "CSET_Georgetown" in fragmento.texto_enriquecido
    assert "Informe anual 2025" in fragmento.texto_enriquecido


def test_sin_metadata_de_contexto_el_texto_enriquecido_es_el_texto(documento_con_bloques):
    """Los campos ausentes se omiten sin dejar separadores huérfanos (§6.3)."""
    documento = documento_con_bloques(bloque(prosa(10)), meta={})
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]
    assert fragmento.texto_enriquecido == fragmento.texto


def test_el_fragmento_hereda_la_identidad_del_documento(documento_con_bloques):
    """§6.1: doc_id, fuente, formato, fenomeno e idioma se copian sin tocar."""
    documento = documento_con_bloques(
        bloque(prosa(10)),
        fuente="SWF_informe.pdf",
        formato="pdf",
        fenomeno=2,
        idioma="en",
        meta={"ruta_relativa": "F2/SWF_informe.pdf", "observatorio": "Secure_World"},
    )
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]

    assert fragmento.doc_id == documento.doc_id
    assert fragmento.fuente == "SWF_informe.pdf"
    assert fragmento.formato == "pdf"
    assert fragmento.fenomeno == 2
    assert fragmento.idioma == "en"
    assert fragmento.observatorio == "Secure_World"
    assert fragmento.ruta_relativa == "F2/SWF_informe.pdf"


def test_la_pagina_es_la_del_primer_bloque_que_aporta_texto(documento_con_bloques):
    """§4.1: la página no es frontera, pero sí se registra la de origen."""
    documento = documento_con_bloques(
        bloque(prosa(5), pagina=7),
        bloque(prosa(5, marca="b"), pagina=8),
    )
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]
    assert fragmento.pagina == 7


def test_el_breadcrumb_de_la_seccion_viaja_en_el_fragmento(documento_con_bloques):
    documento = documento_con_bloques(
        bloque("Resultados", "titulo", 1),
        bloque("Cobertura", "titulo", 2, ruta=["Resultados"]),
        bloque(prosa(10), ruta=["Resultados", "Cobertura"]),
    )
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert fragmentos[-1].seccion == ["Resultados", "Cobertura"]


# --- §6.2: conteo de tokens inyectable ----------------------------------------


def test_el_conteo_de_tokens_por_defecto_es_la_estimacion_conservadora():
    assert estimar_tokens("una frase de cinco palabras") == 8  # ceil(5 * 1.6)


def test_el_contador_de_tokens_se_puede_inyectar(documento_con_bloques):
    """§6.2: al elegir encoder se cambia por su AutoTokenizer, sin tocar el resto."""
    documento = documento_con_bloques(bloque(prosa(10)))
    config = ConfigFragmentacion(contar_tokens=lambda texto: len(texto))
    fragmento = fragmentar(documento, config)[0]
    assert fragmento.num_tokens == len(fragmento.texto)


def test_el_numero_de_palabras_coincide_con_el_texto(documento_con_bloques):
    documento = documento_con_bloques(bloque(prosa(30)))
    for fragmento in fragmentar(documento, CONFIG_POR_DEFECTO):
        assert fragmento.num_palabras == contar_palabras(fragmento.texto)


# --- §4.1 y §4.4: secciones y huérfanos ---------------------------------------


def test_un_encabezado_suelto_se_fusiona_con_el_cuerpo_que_le_sigue(documento_con_bloques):
    """7.9: un vector que solo dice "Metodología" contamina el ranking."""
    documento = documento_con_bloques(
        bloque("Metodología", "titulo", 1),
        bloque(prosa(10), ruta=["Metodología"]),
    )
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].texto.startswith("Metodología")
    assert fragmentos[0].tipo_unidad == "prosa"


def test_un_titulo_seguido_de_otro_titulo_no_queda_huerfano(documento_con_bloques):
    """El patrón más común de los informes: H2, H5 y luego el texto. Con la
    regla de "todo título abre sección", el H2 se quedaba solo en la suya y
    salía como un fragmento de dos palabras. 13 516 títulos del corpus real
    (29,4 %) caen en este caso."""
    documento = documento_con_bloques(
        bloque("1.1 Publications", "titulo", 2),
        bloque("Overview", "titulo", 5, ruta=["1.1 Publications"]),
        bloque(prosa(10), ruta=["1.1 Publications", "Overview"]),
    )

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert [f.tipo_unidad for f in fragmentos] == ["prosa"]
    assert fragmentos[0].texto.startswith("1.1 Publications Overview")


def test_una_cadena_larga_de_titulos_viaja_con_su_cuerpo(documento_con_bloques):
    documento = documento_con_bloques(
        bloque("Parte I", "titulo", 1),
        bloque("Capítulo 2", "titulo", 2, ruta=["Parte I"]),
        bloque("Sección 3", "titulo", 3, ruta=["Parte I", "Capítulo 2"]),
        bloque(prosa(10), ruta=["Parte I", "Capítulo 2", "Sección 3"]),
    )

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].texto.startswith("Parte I Capítulo 2 Sección 3")


def test_la_seccion_de_una_cadena_de_titulos_es_la_del_mas_profundo(documento_con_bloques):
    """El breadcrumb tiene que situar al fragmento donde está su contenido."""
    documento = documento_con_bloques(
        bloque("1.1 Publications", "titulo", 2),
        bloque("Overview", "titulo", 5, ruta=["1.1 Publications"]),
        bloque(prosa(10), ruta=["1.1 Publications", "Overview"]),
    )

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert fragmentos[0].seccion == ["1.1 Publications", "Overview"]


def test_un_titulo_con_cuerpo_propio_sigue_abriendo_su_seccion(documento_con_bloques):
    """La regla nueva no puede tragarse las fronteras de verdad: si el título
    tiene cuerpo, el siguiente título abre sección aparte."""
    documento = documento_con_bloques(
        bloque("Metodología", "titulo", 1),
        bloque(prosa(10), ruta=["Metodología"]),
        bloque("Resultados", "titulo", 1),
        bloque(prosa(10), ruta=["Resultados"]),
    )

    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 2
    assert fragmentos[0].seccion == ["Metodología"]
    assert fragmentos[1].seccion == ["Resultados"]


def test_un_encabezado_sin_cuerpo_se_marca_como_huerfano(documento_con_bloques):
    """§4.4: emitirlo solo es la excepción, y queda etiquetada."""
    documento = documento_con_bloques(bloque("Anexos", "titulo", 1))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].tipo_unidad == "titulo_huerfano"


def test_un_titulo_de_nivel_uno_abre_seccion(documento_con_bloques):
    documento = documento_con_bloques(
        bloque(prosa(8), ruta=[]),
        bloque("Metodología", "titulo", 1),
        bloque(prosa(8, marca="b"), ruta=["Metodología"]),
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=0))
    assert len(fragmentos) == 2
    assert fragmentos[0].seccion == []
    assert fragmentos[1].seccion == ["Metodología"]


def test_un_documento_plano_produce_una_sola_seccion(documento_con_bloques):
    """§4.1: los 954 artículos JSON no tienen encabezados. No es un fallo."""
    documento = documento_con_bloques(bloque(prosa(8)), bloque(prosa(8, marca="b")))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert len(fragmentos) == 1


def test_un_cambio_de_breadcrumb_abre_seccion(documento_con_bloques):
    """El extractor de JSON produce rutas de claves sin emitir títulos."""
    documento = documento_con_bloques(
        bloque(prosa(8), ruta=["resumen"]),
        bloque(prosa(8, marca="b"), ruta=["conclusiones"]),
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=0))
    assert [f.seccion for f in fragmentos] == [["resumen"], ["conclusiones"]]


def test_un_titulo_bajo_el_nivel_de_frontera_no_abre_seccion(documento_con_bloques):
    """``nivel_frontera`` decide qué encabezados cortan y cuáles no."""
    documento = documento_con_bloques(
        bloque("Resultados", "titulo", 1),
        bloque(prosa(8), ruta=["Resultados"]),
        bloque("Detalle", "titulo", 3, ruta=["Resultados"]),
        bloque(prosa(8, marca="b"), ruta=["Resultados", "Detalle"]),
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(nivel_frontera=2))
    assert len(fragmentos) == 1


# --- §5: unidades atómicas ----------------------------------------------------


def test_un_bloque_atomico_no_se_fusiona_con_la_prosa_vecina(documento_con_bloques):
    """7.8: fusionar una fila con prosa produce un vector que no representa a ninguna."""
    fila = "país: Colombia · lanzamientos: 3 · presupuesto: 12 millones de dólares asignados"
    documento = documento_con_bloques(
        bloque(prosa(6)),
        bloque(fila, "fila", atomico=True),
        bloque(prosa(6, marca="b")),
    )
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    atomicos = [f for f in fragmentos if f.tipo_unidad == "atomico"]
    assert len(atomicos) == 1
    assert atomicos[0].texto == fila
    assert all(fila not in f.texto for f in fragmentos if f.tipo_unidad != "atomico")


def test_cada_bloque_atomico_grande_produce_un_fragmento(documento_con_bloques):
    filas = [f"registro: {n} · " + prosa(4, marca=f"r{n}") for n in range(3)]
    documento = documento_con_bloques(*(bloque(f, "fila", atomico=True) for f in filas))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)
    assert [f.texto for f in fragmentos] == filas


def test_las_filas_muy_cortas_se_agrupan_con_sus_contiguas(documento_con_bloques):
    """§5: una celda con un número produce un fragmento inútil."""
    documento = documento_con_bloques(
        *(bloque(f"año: 202{n} · valor: {n}", "fila", atomico=True) for n in range(5))
    )
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].tipo_unidad == "atomico"
    assert "año: 2020" in fragmentos[0].texto
    assert "año: 2024" in fragmentos[0].texto


def test_una_fila_enorme_se_parte_en_frontera_oracional(documento_con_bloques):
    """§5: emitir un fragmento de 900 palabras violaría el límite de 250."""
    fila = prosa(60, marca="f")
    documento = documento_con_bloques(bloque(fila, "fila", atomico=True))
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) > 1
    assert all(f.num_palabras <= 250 for f in fragmentos)


def test_con_respetar_atomicos_desactivado_las_filas_se_empaquetan(documento_con_bloques):
    documento = documento_con_bloques(
        *(bloque(prosa(4, marca=f"r{n}"), "fila", atomico=True) for n in range(6))
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(respetar_atomicos=False))
    assert all(f.tipo_unidad == "prosa" for f in fragmentos)


# --- §4.3: solape -------------------------------------------------------------


def test_el_fragmento_siguiente_arranca_con_la_ultima_oracion_del_anterior(
    documento_con_bloques,
):
    """7.10"""
    documento = documento_con_bloques(bloque(prosa(60)))
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=1))
    assert len(fragmentos) > 1

    for anterior, siguiente in zip(fragmentos, fragmentos[1:]):
        ultima = segmentar(anterior.texto, "es")[-1]
        assert siguiente.texto.startswith(ultima)
        assert siguiente.tiene_solape is True


def test_el_primer_fragmento_no_tiene_solape(documento_con_bloques):
    documento = documento_con_bloques(bloque(prosa(60)))
    assert fragmentar(documento, ConfigFragmentacion(oraciones_solape=1))[0].tiene_solape is False


def test_sin_solape_no_se_repite_ninguna_oracion(documento_con_bloques):
    """7.11"""
    documento = documento_con_bloques(bloque(prosa(60)))
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=0))

    for anterior, siguiente in zip(fragmentos, fragmentos[1:]):
        ultima = segmentar(anterior.texto, "es")[-1]
        assert ultima not in siguiente.texto
    assert all(f.tiene_solape is False for f in fragmentos)


def test_el_solape_no_cruza_frontera_de_seccion(documento_con_bloques):
    """7.12"""
    documento = documento_con_bloques(
        bloque("Primera", "titulo", 1),
        bloque(prosa(30), ruta=["Primera"]),
        bloque("Segunda", "titulo", 1),
        bloque(prosa(30, marca="b"), ruta=["Segunda"]),
    )
    fragmentos = fragmentar(documento, ConfigFragmentacion(oraciones_solape=1))

    primeros_de_seccion = [f for f in fragmentos if f.seccion == ["Segunda"]][0]
    assert primeros_de_seccion.tiene_solape is False
    assert "oración a" not in primeros_de_seccion.texto


def test_el_solape_no_hace_que_el_fragmento_supere_el_tope(documento_con_bloques):
    documento = documento_con_bloques(bloque(prosa(60)))
    config = ConfigFragmentacion(oraciones_solape=3)
    for fragmento in fragmentar(documento, config):
        assert fragmento.num_palabras <= config.max_palabras


def test_el_solape_tambien_respeta_el_tope_de_tokens(documento_con_bloques):
    """Los dos topes son simultáneos también para el solape (§2.1 y §4.3)."""
    documento = documento_con_bloques(bloque(prosa(60)))
    config = ConfigFragmentacion(max_tokens=120, oraciones_solape=2)
    for fragmento in fragmentar(documento, config):
        assert fragmento.num_tokens <= config.max_tokens


def test_la_fusion_de_huerfanos_no_puede_saltarse_el_tope_de_tokens(documento_con_bloques):
    """Un ``min_palabras`` imposible de alcanzar no autoriza a pasarse del tope."""
    documento = documento_con_bloques(bloque(prosa(60)))
    config = ConfigFragmentacion(max_tokens=60, min_palabras=100, oraciones_solape=0)
    for fragmento in fragmentar(documento, config):
        assert fragmento.num_tokens <= config.max_tokens


# --- 7.16: multilingüe --------------------------------------------------------


def test_un_documento_en_portugues_usa_el_segmentador_portugues(documento_con_bloques):
    """7.16"""
    texto = "Vários órgãos, p.ex. INPE e AEB, aderiram ao acordo. O financiamento é incerto."
    documento = documento_con_bloques(bloque(texto), idioma="pt")
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]
    assert fragmento.n_oraciones == 2


def test_un_documento_en_ingles_usa_el_segmentador_ingles(documento_con_bloques):
    """7.16"""
    texto = "The array spans approx. 40 km. Coverage is continuous."
    documento = documento_con_bloques(bloque(texto), idioma="en")
    fragmento = fragmentar(documento, CONFIG_POR_DEFECTO)[0]
    assert fragmento.n_oraciones == 2


# --- §2.3: validar_fragmento --------------------------------------------------


def test_validar_fragmento_no_lanza_ante_un_fragmento_roto(documento_con_bloques):
    """Sigue el patrón de ``contrato.validar_documento``: informa, no revienta."""
    import dataclasses

    fragmento = fragmentar(documento_con_bloques(bloque(prosa(10))), CONFIG_POR_DEFECTO)[0]
    roto = dataclasses.replace(fragmento, formato="docx", idioma="fr", posicion=-1)

    violaciones = validar_fragmento(roto, CONFIG_POR_DEFECTO)

    assert len(violaciones) >= 3
    assert all(isinstance(v, str) for v in violaciones)


def test_validar_fragmento_detecta_un_chunk_id_incoherente(documento_con_bloques):
    import dataclasses

    fragmento = fragmentar(documento_con_bloques(bloque(prosa(10))), CONFIG_POR_DEFECTO)[0]
    roto = dataclasses.replace(fragmento, chunk_id="otro-c0000")

    assert any("chunk_id" in v for v in validar_fragmento(roto, CONFIG_POR_DEFECTO))


def test_validar_fragmento_detecta_un_conteo_de_palabras_mentiroso(documento_con_bloques):
    import dataclasses

    fragmento = fragmentar(documento_con_bloques(bloque(prosa(10))), CONFIG_POR_DEFECTO)[0]
    roto = dataclasses.replace(fragmento, num_palabras=1)

    assert any("num_palabras" in v for v in validar_fragmento(roto, CONFIG_POR_DEFECTO))


# --- §2.3: fragmentar_corpus --------------------------------------------------


@pytest.fixture
def corpus_extraido(tmp_path, documento_con_bloques):
    """Un ``extraidos/`` mínimo, como el que deja el orquestador."""
    entrada = tmp_path / "extraidos"
    entrada.mkdir()

    documentos = [
        documento_con_bloques(
            bloque("Introducción", "titulo", 1),
            bloque(prosa(40), ruta=["Introducción"]),
            fuente="uno.pdf",
            meta={"ruta_relativa": "F1/uno.pdf", "observatorio": "CSET"},
        ),
        documento_con_bloques(
            bloque(prosa(30, marca="b")),
            fuente="dos.json",
            formato="json",
            fenomeno=3,
            meta={"ruta_relativa": "F3/dos.json"},
        ),
        documento_con_bloques(
            fuente="vacio.pdf",
            meta={"ruta_relativa": "F1/vacio.pdf"},
            errores=["extractor de pdf no implementado"],
        ),
    ]
    for documento in documentos:
        destino = entrada / f"{documento.doc_id}.json"
        destino.write_text(
            json.dumps(documento_a_dict(documento), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
    return entrada


def test_fragmentar_corpus_escribe_una_linea_por_fragmento(corpus_extraido, tmp_path):
    salida = tmp_path / "fragmentos"
    reporte = fragmentar_corpus(corpus_extraido, salida, CONFIG_POR_DEFECTO)

    lineas = (salida / "fragmentos.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == reporte.n_fragmentos
    assert reporte.n_documentos == 3


def test_fragmentar_corpus_registra_los_documentos_sin_bloques(corpus_extraido, tmp_path):
    """Se registra la ruta y no la fuente: 59 nombres se repiten en el corpus."""
    reporte = fragmentar_corpus(corpus_extraido, tmp_path / "fragmentos", CONFIG_POR_DEFECTO)
    assert reporte.documentos_sin_bloques == ["F1/vacio.pdf"]


def test_fragmentar_corpus_cuenta_los_fragmentos_por_formato(corpus_extraido, tmp_path):
    reporte = fragmentar_corpus(corpus_extraido, tmp_path / "fragmentos", CONFIG_POR_DEFECTO)
    assert set(reporte.fragmentos_por_formato) == {"pdf", "json"}


def test_el_histograma_reparte_en_bins_de_25(corpus_extraido, tmp_path):
    reporte = fragmentar_corpus(corpus_extraido, tmp_path / "fragmentos", CONFIG_POR_DEFECTO)
    assert sum(reporte.histograma_palabras.values()) == reporte.n_fragmentos


def test_dos_corridas_producen_fragmentos_identicos_byte_a_byte(corpus_extraido, tmp_path):
    """7.5"""
    primera, segunda = tmp_path / "una", tmp_path / "otra"
    fragmentar_corpus(corpus_extraido, primera, CONFIG_POR_DEFECTO)
    fragmentar_corpus(corpus_extraido, segunda, CONFIG_POR_DEFECTO)

    assert (primera / "fragmentos.jsonl").read_bytes() == (
        segunda / "fragmentos.jsonl"
    ).read_bytes()


def test_la_salida_no_lleva_el_texto_enriquecido_en_texto(corpus_extraido, tmp_path):
    """§1.3: lo que se reporta al jurado es el texto original."""
    salida = tmp_path / "fragmentos"
    fragmentar_corpus(corpus_extraido, salida, CONFIG_POR_DEFECTO)

    for linea in (salida / "fragmentos.jsonl").read_text(encoding="utf-8").splitlines():
        registro = json.loads(linea)
        assert registro["texto_enriquecido"].endswith(registro["texto"])


def test_todos_los_fragmentos_del_corpus_pasan_la_validacion(corpus_extraido):
    """8.1: validar_fragmento limpio para el 100% de los fragmentos."""
    for ruta in sorted(corpus_extraido.glob("*.json")):
        documento = documento_desde_dict(json.loads(ruta.read_text(encoding="utf-8")))
        for fragmento in fragmentar(documento, CONFIG_POR_DEFECTO):
            assert validar_fragmento(fragmento, CONFIG_POR_DEFECTO) == []


def test_ningun_fragmento_del_corpus_supera_las_250_palabras(corpus_extraido, tmp_path):
    """8.2, sobre fixtures: el límite de §9.2.1 se comprueba en la salida real."""
    salida = tmp_path / "fragmentos"
    fragmentar_corpus(corpus_extraido, salida, CONFIG_POR_DEFECTO)

    for linea in (salida / "fragmentos.jsonl").read_text(encoding="utf-8").splitlines():
        registro = json.loads(linea)
        assert registro["num_palabras"] <= 250
        assert registro["texto"].strip()


def test_el_reporte_trae_la_mediana_y_el_p95_de_palabras(corpus_extraido, tmp_path):
    """§8.3: la tabla del barrido se construye con estas dos cifras."""
    reporte = fragmentar_corpus(corpus_extraido, tmp_path / "fragmentos", CONFIG_POR_DEFECTO)

    assert 0 < reporte.mediana_palabras <= reporte.p95_palabras
    assert reporte.p95_palabras <= CONFIG_POR_DEFECTO.max_palabras


def test_cargar_extraidos_devuelve_los_documentos_del_directorio(corpus_extraido):
    documentos = cargar_extraidos(corpus_extraido)
    assert {d.fuente for d in documentos} == {"uno.pdf", "dos.json", "vacio.pdf"}


def test_cargar_extraidos_es_estable_entre_llamadas(corpus_extraido):
    unos = [d.doc_id for d in cargar_extraidos(corpus_extraido)]
    otros = [d.doc_id for d in cargar_extraidos(corpus_extraido)]
    assert unos == otros


def test_fragmentar_documentos_no_escribe_a_disco(corpus_extraido, tmp_path):
    """El barrido de §8.3 prueba seis configuraciones; escribirlas todas sobra."""
    documentos = cargar_extraidos(corpus_extraido)

    antes = sorted(p.name for p in tmp_path.rglob("*"))

    fragmentos, reporte = fragmentar_documentos(documentos, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == reporte.n_fragmentos
    assert sorted(p.name for p in tmp_path.rglob("*")) == antes


def test_el_cli_escribe_los_fragmentos_y_el_reporte(corpus_extraido, tmp_path, capsys):
    salida = tmp_path / "fragmentos"

    codigo = main(["--entrada", str(corpus_extraido), "--salida", str(salida)])

    assert codigo == 0
    assert (salida / "fragmentos.jsonl").is_file()
    assert json.loads((salida / "reporte_fragmentacion.json").read_text(encoding="utf-8"))
    assert "fragmentos" in capsys.readouterr().out


def test_el_cli_pasa_la_configuracion_al_algoritmo(corpus_extraido, tmp_path):
    """El barrido de §8.3 se hace desde la línea de comandos, sin editar código."""
    anchos, estrechos = tmp_path / "anchos", tmp_path / "estrechos"

    main(["--entrada", str(corpus_extraido), "--salida", str(anchos), "--objetivo-palabras", "240"])
    main(["--entrada", str(corpus_extraido), "--salida", str(estrechos), "--objetivo-palabras", "60"])

    def lineas(directorio):
        return (directorio / "fragmentos.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(lineas(estrechos)) > len(lineas(anchos))


def test_el_cli_estima_los_tokens_por_defecto():
    """Sin ``transformers`` instalado el fragmentador tiene que seguir corriendo:
    el barrido de configuraciones no necesita conteos exactos."""
    from fragmentador import _config_desde_args, _construir_parser, estimar_tokens

    args = _construir_parser().parse_args(["--entrada", "e", "--salida", "s"])

    assert _config_desde_args(args).contar_tokens is estimar_tokens


def test_el_cli_puede_pedir_el_tokenizador_real():
    """La corrida de entrega. Sin esta opción, cablear el tokenizador obligaría
    a escribir un script en vez de usar el CLI que documenta el README."""
    from fragmentador import _config_desde_args, _construir_parser, estimar_tokens

    try:
        from encoder import contar_tokens
    except ImportError as error:  # pragma: no cover - entorno sin transformers
        pytest.skip(f"encoder no disponible: {error}")

    args = _construir_parser().parse_args(
        ["--entrada", "e", "--salida", "s", "--tokenizador", "real"]
    )
    config = _config_desde_args(args)

    assert config.contar_tokens is not estimar_tokens
    try:
        assert config.contar_tokens("informe anual") == contar_tokens("informe anual")
    except Exception as error:  # noqa: BLE001 - sin red ni caché del checkpoint
        pytest.skip(f"tokenizador de granite no disponible: {error}")


def test_un_corpus_sin_fragmentos_no_revienta_los_percentiles(tmp_path, documento_con_bloques):
    entrada = tmp_path / "extraidos"
    entrada.mkdir()
    vacio = documento_con_bloques(fuente="vacio.pdf", errores=["sin extractor"])
    (entrada / f"{vacio.doc_id}.json").write_text(
        json.dumps(documento_a_dict(vacio), ensure_ascii=False), encoding="utf-8"
    )

    reporte = fragmentar_corpus(entrada, tmp_path / "fragmentos", CONFIG_POR_DEFECTO)

    assert reporte.n_fragmentos == 0
    assert reporte.mediana_palabras == 0
    assert reporte.p95_palabras == 0


# --- datos apartados del texto -------------------------------------------------


def test_el_fragmento_conserva_los_datos_de_su_fila(documento_con_bloques):
    """Los identificadores salen del vector pero no del corpus (§3.4)."""
    fila = bloque(
        "title: Redes neuronales | journal: Nature",
        tipo="fila",
        atomico=True,
        datos={"pmid": "11204229"},
    )
    fragmentos = fragmentar(documento_con_bloques(fila), CONFIG_POR_DEFECTO)

    assert [f.datos for f in fragmentos] == [[{"pmid": "11204229"}]]


def test_un_fragmento_que_agrupa_filas_conserva_los_datos_de_cada_una():
    """Al agrupar registros cortos, atribuir un solo identificador mentiría."""
    from conftest import bloque as _b

    filas = [
        _b(f"title: Estudio {n}", tipo="fila", atomico=True, datos={"pmid": str(n)})
        for n in range(3)
    ]
    from contrato import Documento, calcular_doc_id

    documento = Documento(
        doc_id=calcular_doc_id("datos.csv"),
        fuente="datos.csv",
        formato="csv",
        fenomeno=1,
        idioma="es",
        bloques=filas,
        meta={},
        errores=[],
    )
    fragmentos = fragmentar(documento, CONFIG_POR_DEFECTO)

    assert len(fragmentos) == 1
    assert fragmentos[0].datos == [{"pmid": "0"}, {"pmid": "1"}, {"pmid": "2"}]


def test_la_prosa_no_lleva_datos(documento_con_bloques):
    fragmentos = fragmentar(documento_con_bloques(bloque(prosa(3))), CONFIG_POR_DEFECTO)

    assert all(f.datos == [] for f in fragmentos)


def test_los_datos_llegan_al_jsonl(documento_con_bloques):
    """Si no se serializan, nunca llegan a metadata.jsonl y se pierden."""
    fila = bloque(
        "title: Redes neuronales | journal: Nature",
        tipo="fila",
        atomico=True,
        datos={"pmid": "11204229"},
    )
    fragmentos = fragmentar(documento_con_bloques(fila), CONFIG_POR_DEFECTO)

    crudo = fragmento_a_dict(fragmentos[0])

    assert crudo["datos"] == [{"pmid": "11204229"}]
    assert json.loads(json.dumps(crudo))["datos"] == [{"pmid": "11204229"}]
