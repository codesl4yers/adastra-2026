"""Pruebas de las piezas que comparten los extractores."""

import pytest

from contrato import validar_documento
from extractores.comun import (
    Jerarquia,
    construir_documento,
    documento_fallido,
    es_texto_natural,
    es_valor_opaco,
    serializar_registro,
)

# --- descarte de lo que no es lenguaje natural --------------------------------


@pytest.mark.parametrize(
    "valor",
    [
        "https://www.atlanticcouncil.org/blogs/geotech-cues/europe-must-address/",
        "http://ejemplo.org",
        "www.ejemplo.org",
        "contacto@observatorio.org",
        "10.56221/spt.v3i3.55",
        "a3f5c8e9b1d2f4a6c8e0b2d4f6a8c0e2",
        "2024-03-12T08:30:00Z",
        "2024-03-12",
        "articulos/revista/issue14_95_corio.json",
        "imagenes/grafico.png",
        "91689",
        "12,5",
        "",
        "   ",
        "—",
        "· · ·",
    ],
)
def test_descarta_lo_que_no_es_lenguaje_natural(valor):
    assert es_texto_natural(valor) is False


@pytest.mark.parametrize(
    "valor",
    [
        "El escenario de riesgo se configura a partir de las amenazas.",
        "Autodefensas Gaitanistas de Colombia (AGC)",
        "Cartagena de Indias (Bolívar)",
        "Fragmented Efforts",
        "Inminencia",
    ],
)
def test_conserva_el_lenguaje_natural(valor):
    assert es_texto_natural(valor) is True


def test_una_url_dentro_de_una_frase_no_descarta_la_frase():
    """Descartar por 'contiene una URL' se llevaría por delante párrafos enteros."""
    assert es_texto_natural("El informe está en https://ejemplo.org y es público.") is True


# --- valores opacos: el filtro laxo de los registros ---------------------------


@pytest.mark.parametrize(
    "valor",
    [
        "https://defenseai.eu/wp-content/uploads/estudio.pdf",
        "contacto@observatorio.org",
        "a3f5c8e9b1d2f4a6c8e0b2d4f6a8c0e2",
        "articulos/revista/issue14_95_corio.json",
        "",
        "   ",
    ],
)
def test_un_valor_opaco_no_aporta_nada_aunque_lleve_su_clave(valor):
    assert es_valor_opaco(valor) is True


@pytest.mark.parametrize("valor", ["2026", "Brasil", "12,5", "2024-03-12", "Inminencia"])
def test_un_numero_o_una_fecha_si_aportan_junto_a_su_clave(valor):
    """En un registro el valor viaja con su columna: "year: 2026" sí se recupera."""
    assert es_valor_opaco(valor) is False


# --- serialización de registros ------------------------------------------------


def test_serializa_los_pares_con_su_columna():
    assert serializar_registro([("país", "Colombia"), ("año", "2026")]) == (
        "país: Colombia | año: 2026"
    )


def test_omite_las_celdas_vacias_y_las_opacas():
    pares = [("país", "Colombia"), ("nota", ""), ("url", "https://x.org/a.pdf")]
    assert serializar_registro(pares) == "país: Colombia"


def test_un_registro_entero_de_valores_opacos_queda_vacio():
    assert serializar_registro([("url", "https://x.org"), ("hash", "")]) == ""


def test_conserva_el_orden_de_los_pares():
    """El orden es el de la cabecera; reordenar cambiaría el texto entre corridas."""
    pares = [("z", "uno"), ("a", "dos")]
    assert serializar_registro(pares) == "z: uno | a: dos"


# --- pila de títulos: la ruta que exige el contrato ----------------------------


def test_el_primer_titulo_no_tiene_ancestros():
    jerarquia = Jerarquia()
    assert jerarquia.titulo("Resumen", 1).ruta == []


def test_un_parrafo_cuelga_del_titulo_abierto():
    jerarquia = Jerarquia()
    jerarquia.titulo("Resumen", 1)
    assert jerarquia.parrafo("Cuerpo del resumen.").ruta == ["Resumen"]


def test_un_subtitulo_cuelga_de_su_padre():
    jerarquia = Jerarquia()
    jerarquia.titulo("Resumen", 1)
    assert jerarquia.titulo("Método", 2).ruta == ["Resumen"]
    assert jerarquia.parrafo("Cuerpo.").ruta == ["Resumen", "Método"]


def test_un_titulo_del_mismo_nivel_cierra_al_anterior():
    jerarquia = Jerarquia()
    jerarquia.titulo("Uno", 1)
    jerarquia.titulo("Detalle", 2)
    assert jerarquia.titulo("Dos", 1).ruta == []
    assert jerarquia.parrafo("Cuerpo.").ruta == ["Dos"]


def test_la_jerarquia_produce_un_documento_que_valida():
    """La prueba de fuego: contrato.validar_documento reconstruye la misma pila."""
    jerarquia = Jerarquia()
    bloques = [
        jerarquia.titulo("Informe", 1),
        jerarquia.parrafo("Primer párrafo del informe."),
        jerarquia.titulo("Metodología", 2),
        jerarquia.parrafo("Se usaron tres fuentes."),
        jerarquia.titulo("Anexos", 1),
        jerarquia.parrafo("Tablas de apoyo."),
    ]
    documento = construir_documento(
        fuente="informe.json", formato="json", fenomeno=1, bloques=bloques
    )
    assert validar_documento(documento) == []


def test_la_jerarquia_normaliza_el_texto():
    assert Jerarquia().parrafo("  dos   espacios\n y salto ").texto == "dos espacios y salto"


def test_la_jerarquia_ignora_el_texto_vacio():
    """El contrato prohíbe bloques vacíos, así que no se construyen."""
    jerarquia = Jerarquia()
    assert jerarquia.parrafo("   ") is None
    assert jerarquia.titulo("", 1) is None


def test_un_nivel_fuera_de_rango_se_acota():
    """Un JSON anidado a diez niveles no puede producir un nivel 10."""
    jerarquia = Jerarquia()
    assert jerarquia.titulo("Muy hondo", 12).nivel == 6
    assert jerarquia.titulo("Muy alto", 0).nivel == 1


def test_un_titulo_registra_su_pagina():
    """El fragmentador toma la página del primer bloque que aporta texto."""
    assert Jerarquia().titulo("Metodología", 1, pagina=12).pagina == 12


def test_una_fila_es_atomica_y_no_lleva_nivel():
    fila = Jerarquia().fila("país: Colombia | lanzamientos: 3")
    assert fila.atomico is True
    assert fila.tipo == "fila"
    assert fila.nivel is None


# --- construcción del documento -----------------------------------------------


def test_el_documento_detecta_su_idioma_del_texto():
    jerarquia = Jerarquia()
    bloques = [
        jerarquia.parrafo(
            "The report states that coverage of the southern hemisphere remains "
            "the weakest link in the global tracking network for space debris."
        )
    ]
    documento = construir_documento(
        fuente="a.json", formato="json", fenomeno=1, bloques=bloques
    )
    assert documento.idioma == "en"


def test_un_documento_sin_bloques_cae_al_idioma_por_defecto():
    documento = construir_documento(fuente="a.json", formato="json", fenomeno=1, bloques=[])
    assert documento.idioma == "es"
    assert documento.bloques == []


def test_construir_documento_descarta_los_bloques_nulos():
    """Los constructores de Jerarquia devuelven None ante texto vacío."""
    jerarquia = Jerarquia()
    bloques = [jerarquia.parrafo("Contenido."), jerarquia.parrafo("  "), None]
    documento = construir_documento(
        fuente="a.json", formato="json", fenomeno=1, bloques=bloques
    )
    assert len(documento.bloques) == 1


def test_el_documento_fallido_es_valido_y_lleva_el_motivo():
    documento = documento_fallido(
        fuente="roto.pdf", formato="pdf", fenomeno=2, motivo="cabecera ilegible"
    )
    assert validar_documento(documento) == []
    assert documento.bloques == []
    assert documento.errores == ["cabecera ilegible"]
