"""Pruebas del extractor de tiles vectoriales."""

import pytest

from contrato import validar_documento
from extractores import pbf

mapbox_vector_tile = pytest.importorskip("mapbox_vector_tile")


def escribir_tile(tmp_path, capas=None, nombre="tile.pbf", subdirs=("tiles", "6", "17")):
    capas = capas or [
        {
            "name": "municipios",
            "features": [
                {
                    "geometry": {"type": "Point", "coordinates": [10, 10]},
                    "properties": {
                        "fid": 162,
                        "au_country": "Brasil",
                        "au_level2": "Tracuateua",
                        "au_cv": "VERDADEIRO",
                        "au_eln": "FALSO",
                    },
                },
                {
                    "geometry": {"type": "Point", "coordinates": [20, 20]},
                    "properties": {
                        "fid": 163,
                        "au_country": "Colombia",
                        "au_level2": "Leticia",
                        "au_eln": "VERDADEIRO",
                    },
                },
            ],
        }
    ]
    carpeta = tmp_path.joinpath(*subdirs)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / nombre
    destino.write_bytes(mapbox_vector_tile.encode(capas))
    return destino


# --- features como registros atómicos -----------------------------------------


def test_cada_feature_es_un_bloque_atomico(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    filas = [b for b in documento.bloques if b.tipo == "fila"]
    assert len(filas) == 2
    assert all(b.atomico for b in filas)


def test_las_propiedades_llevan_su_clave(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    fila = next(b for b in documento.bloques if b.tipo == "fila")
    assert "au_country: Brasil" in fila.texto
    assert "au_level2: Tracuateua" in fila.texto


def test_la_capa_es_el_breadcrumb(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    fila = next(b for b in documento.bloques if b.tipo == "fila")
    assert fila.ruta == ["municipios"]


def test_la_geometria_no_entra_al_indice(tmp_path):
    """Un tile es 99 % coordenadas: indexarlas destruye la recuperación."""
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    texto = " ".join(b.texto for b in documento.bloques)
    assert "coordinates" not in texto
    assert "Point" not in texto


def test_las_ausencias_no_se_indexan(tmp_path):
    """Repetir "au_eln: FALSO" en 250 features no distingue nada y ahoga el resto."""
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    fila = next(b for b in documento.bloques if b.tipo == "fila")
    assert "FALSO" not in fila.texto
    assert "au_cv: VERDADEIRO" in fila.texto


def test_el_orden_de_las_features_es_estable(tmp_path):
    """El orden que devuelve la librería no está garantizado entre versiones."""
    ruta = escribir_tile(tmp_path)
    unos = [b.texto for b in pbf.extraer(ruta, fenomeno=3).bloques]
    otros = [b.texto for b in pbf.extraer(ruta, fenomeno=3).bloques]
    assert unos == otros


# --- metadata geográfica --------------------------------------------------------


def test_el_zoom_y_el_tile_salen_de_la_ruta(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path, subdirs=("tiles", "6", "17")), fenomeno=3)

    assert documento.meta["zoom"] == 6
    assert documento.meta["tile_x"] == 17


def test_el_bbox_se_calcula_del_tile_y_no_de_la_geometria(tmp_path):
    """Las coordenadas de un tile son relativas a él: derivar lat/lon de ellas da datos falsos."""
    ruta = escribir_tile(tmp_path, nombre="3.pbf", subdirs=("tiles", "1", "0"))
    documento = pbf.extraer(ruta, fenomeno=3)

    oeste, sur, este, norte = documento.meta["bbox"]
    assert oeste == pytest.approx(-180.0)
    assert este == pytest.approx(0.0)
    assert -90 <= sur < norte <= 90


def test_sin_zoom_en_la_ruta_no_se_inventa_bbox(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path, subdirs=("suelto",)), fenomeno=3)
    assert "bbox" not in documento.meta


def test_las_capas_y_el_numero_de_features_van_a_meta(tmp_path):
    documento = pbf.extraer(escribir_tile(tmp_path), fenomeno=3)

    assert documento.meta["capas"] == ["municipios"]
    assert documento.meta["n_features"] == 2


# --- robustez y contrato --------------------------------------------------------


def test_un_pbf_ilegible_no_lanza(tmp_path):
    ruta = tmp_path / "roto.pbf"
    ruta.write_bytes(b"\x00\x01\x02 basura")

    documento = pbf.extraer(ruta, fenomeno=3)

    assert documento.bloques == []
    assert documento.errores != []


def test_un_osm_pbf_se_reconoce_y_se_reporta(tmp_path):
    """.pbf designa dos formatos: un volcado de OSM no se lee con esta librería."""
    ruta = tmp_path / "region.osm.pbf"
    ruta.write_bytes(b"\x00\x00\x00\x0d\x0a\x07OSMHeader" + b"\x00" * 40)

    documento = pbf.extraer(ruta, fenomeno=3)

    assert documento.bloques == []
    assert any("OSM" in e for e in documento.errores)


def test_un_tile_sin_propiedades_utiles_lo_dice(tmp_path):
    capas = [{"name": "geo", "features": [{"geometry": {"type": "Point", "coordinates": [1, 1]}, "properties": {}}]}]
    documento = pbf.extraer(escribir_tile(tmp_path, capas=capas), fenomeno=3)

    assert documento.bloques == []
    assert documento.errores != []


def test_la_salida_cumple_el_contrato(tmp_path):
    assert validar_documento(pbf.extraer(escribir_tile(tmp_path), fenomeno=3)) == []


def test_un_tile_real_del_corpus_se_extrae(dir_corpus):
    reales = sorted(dir_corpus.rglob("*.pbf"))
    if not reales:
        pytest.skip("el corpus real no tiene .pbf")

    documento = pbf.extraer(reales[0], fenomeno=3)

    assert documento.bloques
    assert validar_documento(documento) == []
