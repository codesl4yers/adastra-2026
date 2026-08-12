"""Pruebas del extractor de JSON.

Las formas de los fixtures son las que trae el corpus real: 964 de los 1826
documentos son artículos scrapeados con este esquema.
"""

import json

import pytest

from contrato import validar_documento
from extractores import json_


def escribir(tmp_path, datos, nombre="articulo.json", **kwargs):
    destino = tmp_path / nombre
    destino.write_text(json.dumps(datos, ensure_ascii=False, **kwargs), encoding="utf-8")
    return destino


CUERPO = [
    "El escenario de riesgo se configura a partir de las amenazas a la vida e "
    "integridad personal contra los líderes del consejo comunitario.",
    "La misión de verificación documentó catorce casos durante el último trimestre "
    "en los municipios del sur del departamento.",
]


# --- esquema de artículo ------------------------------------------------------


def test_el_titulo_del_articulo_abre_la_jerarquia(tmp_path):
    ruta = escribir(tmp_path, {"title": "Informe de riesgo", "body_paragraphs": CUERPO})
    documento = json_.extraer(ruta, fenomeno=3)

    assert documento.bloques[0].tipo == "titulo"
    assert documento.bloques[0].texto == "Informe de riesgo"
    assert documento.bloques[0].nivel == 1


def test_los_parrafos_del_cuerpo_cuelgan_del_titulo(tmp_path):
    ruta = escribir(tmp_path, {"title": "Informe de riesgo", "body_paragraphs": CUERPO})
    documento = json_.extraer(ruta, fenomeno=3)

    parrafos = [b for b in documento.bloques if b.tipo == "parrafo"]
    assert len(parrafos) == 2
    assert all(b.ruta == ["Informe de riesgo"] for b in parrafos)


def test_las_secciones_abren_subsecciones(tmp_path):
    datos = {
        "title": "Capacitación Cuprum",
        "sections": [
            {"heading": "Grupo Principal", "paragraphs": [CUERPO[0]]},
            {"heading": "Resultados", "paragraphs": [CUERPO[1]]},
        ],
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    titulos = [(b.texto, b.nivel, b.ruta) for b in documento.bloques if b.tipo == "titulo"]
    assert titulos == [
        ("Capacitación Cuprum", 1, []),
        ("Grupo Principal", 2, ["Capacitación Cuprum"]),
        ("Resultados", 2, ["Capacitación Cuprum"]),
    ]


def test_el_cuerpo_plano_no_queda_bajo_la_ultima_seccion(tmp_path):
    """Emitir body_paragraphs después de sections lo colgaría de la sección equivocada."""
    datos = {
        "title": "Mixto",
        "body_paragraphs": [CUERPO[0]],
        "sections": [{"heading": "Anexo", "paragraphs": [CUERPO[1]]}],
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    del_cuerpo = next(b for b in documento.bloques if b.texto == CUERPO[0])
    assert del_cuerpo.ruta == ["Mixto"]


def test_el_body_text_solo_se_usa_si_no_hay_parrafos(tmp_path):
    """body_text es el mismo cuerpo concatenado: usar ambos duplicaría el índice."""
    datos = {"title": "T", "body_paragraphs": CUERPO, "body_text": " ".join(CUERPO)}
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert len([b for b in documento.bloques if b.tipo == "parrafo"]) == 2


def test_sin_parrafos_se_recurre_al_body_text(tmp_path):
    datos = {"title": "T", "body_text": " ".join(CUERPO)}
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert [b.tipo for b in documento.bloques] == ["titulo", "parrafo"]


def test_el_resumen_se_indexa_antes_del_cuerpo(tmp_path):
    datos = {"title": "T", "abstract": "Resumen del artículo con contenido real.", "body_paragraphs": CUERPO}
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert documento.bloques[1].texto.startswith("Resumen del artículo")


def test_las_listas_se_emiten_como_bloques_de_lista(tmp_path):
    datos = {"title": "T", "lists": ["Modelos generativos: cómo aprenden y crean", "Otro ítem del programa"]}
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert [b.tipo for b in documento.bloques if b.tipo == "lista"] == ["lista", "lista"]


# --- alerta_meta: 363 archivos del corpus -------------------------------------


def test_los_campos_de_texto_de_alerta_meta_se_indexan(tmp_path):
    datos = {
        "title": "Mapa",
        "alerta_meta": {
            "codigo": "001-17",
            "tipo": "Inminencia",
            "tema_clave": CUERPO[0],
            "municipios": "Cartagena de Indias (Bolívar)",
            "detail_id": "91689",
        },
        "body_paragraphs": [CUERPO[1]],
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=3)

    texto = " ".join(b.texto for b in documento.bloques)
    assert CUERPO[0] in texto
    assert "Cartagena de Indias" in texto


def test_los_identificadores_de_alerta_meta_van_a_meta_y_no_al_texto(tmp_path):
    datos = {
        "title": "Mapa",
        "alerta_meta": {"codigo": "001-17", "detail_id": "91689", "tema_clave": CUERPO[0]},
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=3)

    assert "91689" not in " ".join(b.texto for b in documento.bloques)
    assert documento.meta["alerta_meta"]["detail_id"] == "91689"


# --- metadata -----------------------------------------------------------------


def test_la_metadata_descriptiva_viaja_en_meta_no_en_los_bloques(tmp_path):
    datos = {
        "title": "T",
        "body_paragraphs": CUERPO,
        "url": "https://www.atlanticcouncil.org/blogs/x/",
        "authors": ["Trevor H. Rudolph"],
        "date": "May 21, 2026",
        "doi": "10.56221/spt.v3i3.55",
        "keywords": ["International Relations"],
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert documento.meta["url"] == "https://www.atlanticcouncil.org/blogs/x/"
    assert documento.meta["autores"] == ["Trevor H. Rudolph"]
    assert documento.meta["fecha"] == "May 21, 2026"
    assert documento.meta["doi"] == "10.56221/spt.v3i3.55"
    assert documento.meta["etiquetas"] == ["International Relations"]
    assert "https://" not in " ".join(b.texto for b in documento.bloques)


def test_el_titulo_del_documento_queda_en_meta_para_el_enriquecimiento(tmp_path):
    """El fragmentador lo usa como prefijo de texto_enriquecido (§6.3)."""
    ruta = escribir(tmp_path, {"title": "Informe anual", "body_paragraphs": CUERPO})
    assert json_.extraer(ruta, fenomeno=1).meta["titulo"] == "Informe anual"


def test_los_enlaces_no_se_indexan_pero_se_cuentan(tmp_path):
    datos = {
        "title": "T",
        "body_paragraphs": CUERPO,
        "pdf_links": ["https://x.org/a.pdf", "https://x.org/b.pdf"],
        "images": [{"src": "https://x.org/i.png", "alt": "grafico"}],
    }
    documento = json_.extraer(escribir(tmp_path, datos), fenomeno=1)

    assert documento.meta["n_pdf_links"] == 2
    assert documento.meta["n_images"] == 1
    assert "x.org" not in " ".join(b.texto for b in documento.bloques)


# --- listas de registros: los catálogos ---------------------------------------


def test_una_lista_de_registros_produce_filas_atomicas(tmp_path):
    datos = [
        {"study_id": "DAIO 26|34", "title": "Fragmented Efforts", "country": "Brasil", "year": "2026"},
        {"study_id": "DAIO 26|35", "title": "Otro estudio", "country": "Chile", "year": "2025"},
    ]
    documento = json_.extraer(escribir(tmp_path, datos, nombre="catalogo.json"), fenomeno=1)

    assert [b.tipo for b in documento.bloques] == ["fila", "fila"]
    assert all(b.atomico for b in documento.bloques)
    assert "country: Brasil" in documento.bloques[0].texto
    assert "year: 2026" in documento.bloques[0].texto


def test_una_fila_no_arrastra_urls(tmp_path):
    datos = [{"title": "Estudio", "url_pdf": "https://defenseai.eu/x.pdf"}]
    documento = json_.extraer(escribir(tmp_path, datos, nombre="catalogo.json"), fenomeno=1)

    assert "defenseai" not in documento.bloques[0].texto


def test_una_lista_vacia_no_produce_bloques_ni_revienta(tmp_path):
    documento = json_.extraer(escribir(tmp_path, [], nombre="vacio.json"), fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


# --- GeoJSON: la trampa del stub ----------------------------------------------


def test_de_un_geojson_solo_se_indexan_las_propiedades(tmp_path):
    datos = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Río Putumayo", "tipo": "hidrografía"},
                "geometry": {"type": "LineString", "coordinates": [[1.5, 2.5], [1.6, 2.6]]},
            }
        ],
    }
    documento = json_.extraer(escribir(tmp_path, datos, nombre="mapa.geojson"), fenomeno=3)

    assert len(documento.bloques) == 1
    assert documento.bloques[0].atomico is True
    assert "Río Putumayo" in documento.bloques[0].texto
    assert "coordinates" not in documento.bloques[0].texto
    assert "1.5" not in documento.bloques[0].texto


def test_un_geojson_deja_su_extension_geografica_en_meta(tmp_path):
    datos = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Punto"},
                "geometry": {"type": "Point", "coordinates": [-74.0, 4.6]},
            }
        ],
    }
    documento = json_.extraer(escribir(tmp_path, datos, nombre="mapa.geojson"), fenomeno=3)

    assert documento.meta["bbox"] == [-74.0, 4.6, -74.0, 4.6]


# --- recorrido genérico y robustez --------------------------------------------


def test_una_forma_desconocida_cae_al_recorrido_generico(tmp_path):
    datos = {"nivel_uno": {"nivel_dos": {"comentario": CUERPO[0]}}}
    documento = json_.extraer(escribir(tmp_path, datos, nombre="raro.json"), fenomeno=1)

    assert CUERPO[0] in " ".join(b.texto for b in documento.bloques)


def test_el_recorrido_generico_usa_las_claves_como_breadcrumb(tmp_path):
    """Las claves se materializan como títulos: es lo que exige el contrato.

    ``contrato.validar_documento`` reconstruye la jerarquía a partir de los
    bloques de tipo título, así que una ``ruta`` sin títulos que la respalden no
    valida por mucho sentido que tenga el camino de claves.
    """
    datos = {"resumen": {"contenido": CUERPO[0]}}
    documento = json_.extraer(escribir(tmp_path, datos, nombre="raro.json"), fenomeno=1)

    parrafo = next(b for b in documento.bloques if b.texto == CUERPO[0])
    assert parrafo.ruta == ["resumen", "contenido"]
    assert [b.texto for b in documento.bloques if b.tipo == "titulo"] == ["resumen", "contenido"]


def test_el_recorrido_generico_ordena_las_claves(tmp_path):
    """Sin orden explícito, dos corridas podrían emitir los bloques al revés."""
    datos = {"zeta": CUERPO[1], "alfa": CUERPO[0]}
    documento = json_.extraer(escribir(tmp_path, datos, nombre="raro.json"), fenomeno=1)

    parrafos = [b.texto for b in documento.bloques if b.tipo == "parrafo"]
    assert parrafos == [CUERPO[0], CUERPO[1]]


def test_el_recorrido_generico_no_inventa_titulos_sin_contenido(tmp_path):
    """Una rama entera de URLs no debe dejar su clave como título huérfano."""
    datos = {"enlaces": {"a": "https://x.org/uno", "b": "https://x.org/dos"}, "nota": CUERPO[0]}
    documento = json_.extraer(escribir(tmp_path, datos, nombre="raro.json"), fenomeno=1)

    assert "enlaces" not in [b.texto for b in documento.bloques]


def test_un_anidamiento_absurdo_no_revienta_la_pila(tmp_path):
    datos = {}
    nodo = datos
    for n in range(400):
        nodo["hijo"] = {}
        nodo = nodo["hijo"]
    nodo["texto"] = CUERPO[0]

    documento = json_.extraer(escribir(tmp_path, datos, nombre="hondo.json"), fenomeno=1)

    assert documento.errores != []


def test_un_json_ilegible_no_lanza(tmp_path):
    ruta = tmp_path / "roto.json"
    ruta.write_text("{no es json", encoding="utf-8")

    documento = json_.extraer(ruta, fenomeno=1)

    assert documento.bloques == []
    assert any("json" in e.lower() for e in documento.errores)


def test_un_catalogo_de_scraping_no_produce_ruido(tmp_path):
    """ceeep_registro.json y compañía: solo URLs y hashes, cero lenguaje natural."""
    datos = {"urls": {}, "hashes": {}, "articulos": {"https://x.org/a": "articulos/a.json"}}
    documento = json_.extraer(escribir(tmp_path, datos, nombre="registro.json"), fenomeno=1)

    assert documento.bloques == []
    assert documento.errores != []


# --- contrato -----------------------------------------------------------------


@pytest.mark.parametrize(
    "datos,nombre",
    [
        ({"title": "T", "body_paragraphs": CUERPO}, "a.json"),
        ({"title": "T", "sections": [{"heading": "H", "paragraphs": CUERPO}]}, "b.json"),
        ([{"campo": "Valor de texto suficientemente largo"}], "c.json"),
        ({"raro": {"hondo": CUERPO[0]}}, "d.json"),
    ],
)
def test_toda_salida_cumple_el_contrato(tmp_path, datos, nombre):
    """Requisito 1 del enunciado."""
    documento = json_.extraer(escribir(tmp_path, datos, nombre=nombre), fenomeno=1)
    assert validar_documento(documento) == []


def test_el_idioma_se_detecta_del_cuerpo(tmp_path):
    datos = {
        "title": "Report",
        "body_paragraphs": [
            "The report states that coverage of the southern hemisphere remains the "
            "weakest link in the global tracking network for orbital debris."
        ],
    }
    assert json_.extraer(escribir(tmp_path, datos), fenomeno=2).idioma == "en"


def test_la_fuente_es_el_nombre_exacto_del_archivo(tmp_path):
    """Requisito 4 del enunciado: `fuente` es el campo de emparejamiento."""
    ruta = escribir(tmp_path, {"title": "T", "body_paragraphs": CUERPO}, nombre="ATLCOUNCIL_01.json")
    assert json_.extraer(ruta, fenomeno=1).fuente == "ATLCOUNCIL_01.json"


def test_dos_extracciones_del_mismo_archivo_son_identicas(tmp_path):
    """Requisito 3: determinismo."""
    ruta = escribir(tmp_path, {"title": "T", "body_paragraphs": CUERPO, "lists": ["Uno", "Dos"]})
    assert json_.extraer(ruta, fenomeno=1) == json_.extraer(ruta, fenomeno=1)


def test_un_archivo_en_utf8_con_bom_se_lee_igual(tmp_path):
    ruta = tmp_path / "bom.json"
    ruta.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"title": "T", "body_paragraphs": CUERPO}).encode("utf-8")
    )
    documento = json_.extraer(ruta, fenomeno=1)

    assert documento.bloques[0].texto == "T"
    assert documento.errores == []
