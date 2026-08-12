"""Pruebas de las reglas transversales de limpieza."""

import unicodedata

import pytest

import limpieza
from limpieza import (
    detectar_idioma,
    es_ruido_estructural,
    es_texto_util,
    lineas_repetidas,
    normalizar_texto,
)

# Se escriben con chr() y no con el carácter literal para que la intención sea
# visible y para que ningún editor los "arregle" silenciosamente.
NBSP = chr(0x00A0)  # espacio duro
ANCHO_CERO = chr(0x200B)  # zero width space
BOM = chr(0xFEFF)  # zero width no-break space


# --- normalizar_texto --------------------------------------------------------


def test_normaliza_a_nfc():
    descompuesto = unicodedata.normalize("NFD", "información")
    assert descompuesto != "información"
    assert normalizar_texto(descompuesto) == "información"


def test_colapsa_espacios_redundantes():
    assert normalizar_texto("hola    mundo") == "hola mundo"


def test_colapsa_saltos_de_linea_y_tabulaciones():
    assert normalizar_texto("hola\n\tmundo\r\nadios") == "hola mundo adios"


def test_recorta_espacios_de_los_extremos():
    assert normalizar_texto("   hola mundo   ") == "hola mundo"


def test_convierte_espacio_duro_en_espacio_normal():
    assert normalizar_texto(f"68{NBSP}% del total") == "68 % del total"


def test_elimina_caracteres_de_control():
    assert normalizar_texto("texto\x00con\x07control") == "textoconcontrol"


def test_elimina_caracteres_de_ancho_cero():
    assert normalizar_texto(f"pa{ANCHO_CERO}labra") == "palabra"
    assert normalizar_texto(f"{BOM}palabra") == "palabra"


def test_normalizar_es_idempotente():
    sucio = f"  Información {NBSP} \n del  \x00sistema "
    una_vez = normalizar_texto(sucio)
    assert normalizar_texto(una_vez) == una_vez


def test_texto_solo_espacios_queda_vacio():
    assert normalizar_texto("  \n\t   ") == ""
    assert normalizar_texto(NBSP * 3) == ""


# --- es_texto_util -----------------------------------------------------------


def test_texto_con_contenido_es_util():
    assert es_texto_util("Un párrafo con contenido")


def test_texto_vacio_no_es_util():
    assert not es_texto_util("")
    assert not es_texto_util("    ")


# --- es_ruido_estructural ----------------------------------------------------


def test_detecta_numeracion_de_pagina():
    assert es_ruido_estructural("Página 3 de 12")
    assert es_ruido_estructural("- 7 -")
    assert es_ruido_estructural("12")


def test_no_marca_como_ruido_un_parrafo_real():
    assert not es_ruido_estructural("La cobertura boscosa disminuyó un 12 % en 2024")


# --- lineas_repetidas --------------------------------------------------------


def test_detecta_linea_presente_en_todas_las_unidades():
    unidades = [
        ["Encabezado institucional", "contenido uno"],
        ["Encabezado institucional", "contenido dos"],
        ["Encabezado institucional", "contenido tres"],
    ]
    assert lineas_repetidas(unidades) == ["Encabezado institucional"]


def test_no_marca_lineas_que_aparecen_una_sola_vez():
    unidades = [["a"], ["b"], ["c"]]
    assert lineas_repetidas(unidades) == []


def test_devuelve_resultado_ordenado_y_determinista():
    unidades = [
        ["zeta", "alfa", "cuerpo uno"],
        ["zeta", "alfa", "cuerpo dos"],
        ["zeta", "alfa", "cuerpo tres"],
    ]
    resultado = lineas_repetidas(unidades)
    assert resultado == sorted(resultado)
    assert resultado == lineas_repetidas(unidades)


def test_no_aplica_con_muy_pocas_unidades():
    # Con dos páginas no hay evidencia suficiente para hablar de cabecera.
    unidades = [["repetida", "uno"], ["repetida", "dos"]]
    assert lineas_repetidas(unidades, minimo_unidades=3) == []


def test_respeta_el_umbral_de_frecuencia():
    unidades = [["x", "uno"], ["x", "dos"], ["y", "tres"], ["y", "cuatro"]]
    assert lineas_repetidas(unidades, umbral=0.9) == []
    assert lineas_repetidas(unidades, umbral=0.5) == ["x", "y"]


def test_cuenta_unidades_no_apariciones_totales():
    # "eco" aparece tres veces pero todas dentro de la misma unidad: no es
    # una cabecera, es una repetición local.
    unidades = [["eco", "eco", "eco"], ["uno"], ["dos"]]
    assert lineas_repetidas(unidades) == []


# --- detectar_idioma ---------------------------------------------------------


def test_detecta_espanol():
    texto = (
        "La pérdida de cobertura boscosa alcanzó las ciento veinte mil hectáreas "
        "durante el periodo analizado por la entidad ambiental competente."
    )
    assert detectar_idioma(texto) == "es"


def test_detecta_ingles():
    texto = (
        "The average discharge of the monitored rivers decreased steadily over "
        "the last three years, directly affecting the water supply systems."
    )
    assert detectar_idioma(texto) == "en"


def test_detecta_portugues():
    texto = (
        "A perda de cobertura florestal atingiu cento e vinte mil hectares "
        "durante o período analisado pelas autoridades ambientais competentes."
    )
    assert detectar_idioma(texto) == "pt"


def test_es_determinista_entre_llamadas():
    texto = "El informe describe la situación de las cuencas hidrográficas del país."
    assert detectar_idioma(texto) == detectar_idioma(texto) == detectar_idioma(texto)


def test_texto_insuficiente_cae_al_valor_por_defecto():
    assert detectar_idioma("", por_defecto="es") == "es"
    assert detectar_idioma("ok", por_defecto="pt") == "pt"


def test_nunca_devuelve_un_idioma_fuera_del_contrato():
    # Aunque el detector reconozca francés, el contrato solo admite es/en/pt.
    texto = "Le rapport décrit la situation des bassins hydrographiques du pays."
    assert detectar_idioma(texto) in {"es", "en", "pt"}


# --- respaldo sin langdetect -------------------------------------------------
# El pipeline debe seguir dando un idioma válido y determinista aunque la
# dependencia opcional no esté instalada.


@pytest.fixture
def sin_langdetect(monkeypatch):
    monkeypatch.setattr(limpieza, "_HAY_LANGDETECT", False)
    # El caché guarda resultados calculados con langdetect disponible.
    limpieza.detectar_idioma.cache_clear()
    yield
    limpieza.detectar_idioma.cache_clear()


def test_el_respaldo_distingue_espanol_de_portugues(sin_langdetect):
    espanol = (
        "La pérdida de cobertura boscosa alcanzó las ciento veinte mil hectáreas "
        "durante el periodo analizado por la entidad ambiental competente."
    )
    portugues = (
        "A perda de cobertura florestal atingiu cento e vinte mil hectares "
        "durante o período analisado pelas autoridades ambientais competentes."
    )
    assert limpieza.detectar_idioma(espanol) == "es"
    assert limpieza.detectar_idioma(portugues) == "pt"


def test_el_respaldo_detecta_ingles(sin_langdetect):
    texto = (
        "The average discharge of the monitored rivers decreased steadily over "
        "the last three years, directly affecting the water supply systems."
    )
    assert limpieza.detectar_idioma(texto) == "en"


def test_el_respaldo_es_determinista(sin_langdetect):
    texto = "El informe describe la situación de las cuencas hidrográficas del país."
    resultados = {limpieza.detectar_idioma(texto) for _ in range(5)}
    assert resultados == {"es"}


def test_el_respaldo_cae_al_defecto_sin_palabras_reconocibles(sin_langdetect):
    # Texto largo pero sin ninguna palabra funcional de los tres idiomas.
    assert limpieza.detectar_idioma("zzzz qqqq wwww xxxx yyyy kkkk", por_defecto="pt") == "pt"
