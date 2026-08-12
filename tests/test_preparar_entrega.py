"""Pruebas del ensamblado de la carpeta de entrega."""

import json

import pytest

from scripts.preparar_entrega import (
    modulos_necesarios,
    nombre_de_encoder,
    preparar_entrega,
)


def test_el_nombre_del_encoder_sale_del_modelo():
    """`base_vectorial/encoder_<nombre>/` con el nombre del checkpoint, sin la
    organización: el directorio no puede llevar una barra dentro."""
    assert (
        nombre_de_encoder("ibm-granite/granite-embedding-311m-multilingual-r2")
        == "encoder_granite-embedding-311m-multilingual-r2"
    )


def test_un_modelo_sin_organizacion_tambien_vale():
    assert nombre_de_encoder("modelo-de-prueba") == "encoder_modelo-de-prueba"


def test_los_modulos_necesarios_salen_de_los_imports_reales(tmp_path):
    """Una lista escrita a mano se queda vieja en cuanto alguien añade un
    import; el cierre transitivo no."""
    (tmp_path / "raiz.py").write_text("import json\nfrom hoja import algo\n", encoding="utf-8")
    (tmp_path / "hoja.py").write_text("from honda import otro\n", encoding="utf-8")
    (tmp_path / "honda.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ajena.py").write_text("y = 2\n", encoding="utf-8")

    necesarios = modulos_necesarios(tmp_path, "raiz")

    assert [p.name for p in necesarios] == ["hoja.py", "honda.py", "raiz.py"]


def test_la_entrega_queda_con_la_estructura_esperada(tmp_path):
    origen = _proyecto_falso(tmp_path)
    destino = tmp_path / "entrega"

    preparar_entrega(
        raiz=origen,
        indice=origen / "indice",
        resultados=origen / "resultados.jsonl",
        destino=destino,
        modelo="ibm-granite/granite-embedding-311m-multilingual-r2",
    )

    encoder = destino / "base_vectorial" / "encoder_granite-embedding-311m-multilingual-r2"
    assert (destino / "resultados.jsonl").is_file()
    assert (destino / "generador.py").is_file()
    assert (encoder / "index.faiss").is_file()
    assert (encoder / "metadata.jsonl").is_file()


def test_las_dependencias_viajan_junto_al_generador(tmp_path):
    """Sin ellas, `python entrega/generador.py` es un ImportError."""
    origen = _proyecto_falso(tmp_path)
    destino = tmp_path / "entrega"

    reporte = preparar_entrega(
        raiz=origen,
        indice=origen / "indice",
        resultados=origen / "resultados.jsonl",
        destino=destino,
        modelo="m",
    )

    assert (destino / "encoder.py").is_file()
    assert "encoder.py" in reporte.modulos


def test_un_indice_incompleto_se_detiene_antes_de_escribir(tmp_path):
    """Entregar una base vectorial a la que le falta la metadata es entregar
    algo que no se puede usar."""
    origen = _proyecto_falso(tmp_path)
    (origen / "indice" / "metadata.jsonl").unlink()
    destino = tmp_path / "entrega"

    with pytest.raises(ValueError, match="metadata.jsonl"):
        preparar_entrega(
            raiz=origen,
            indice=origen / "indice",
            resultados=origen / "resultados.jsonl",
            destino=destino,
            modelo="m",
        )

    assert not destino.exists()


def test_el_reporte_dice_cuantas_consultas_lleva(tmp_path):
    origen = _proyecto_falso(tmp_path)

    reporte = preparar_entrega(
        raiz=origen,
        indice=origen / "indice",
        resultados=origen / "resultados.jsonl",
        destino=tmp_path / "entrega",
        modelo="m",
    )

    assert reporte.n_consultas == 2
    assert reporte.n_vectores == 3


def _proyecto_falso(tmp_path):
    """Un proyecto mínimo con la forma del real: generador, una dependencia,
    un índice y un entregable de consultas."""
    raiz = tmp_path / "proyecto"
    (raiz / "indice").mkdir(parents=True)
    (raiz / "generador.py").write_text("from encoder import algo\n", encoding="utf-8")
    (raiz / "encoder.py").write_text("algo = 1\n", encoding="utf-8")
    (raiz / "indice" / "index.faiss").write_bytes(b"faiss")
    (raiz / "indice" / "metadata.jsonl").write_text(
        "".join(json.dumps({"doc_id": f"D{n}"}) + "\n" for n in range(3)),
        encoding="utf-8",
    )
    (raiz / "resultados.jsonl").write_text(
        "".join(json.dumps({"query_id": f"q{n:03d}"}) + "\n" for n in range(2)),
        encoding="utf-8",
    )
    return raiz


def test_un_archivo_ya_en_su_sitio_no_se_copia_sobre_si_mismo(tmp_path):
    """El generador escribe resultados.jsonl directamente en entrega/, así que
    origen y destino coinciden. En Windows eso es un PermissionError."""
    origen = _proyecto_falso(tmp_path)
    destino = tmp_path / "entrega"
    destino.mkdir()
    en_sitio = destino / "resultados.jsonl"
    en_sitio.write_text(
        json.dumps({"query_id": "q001"}) + "\n", encoding="utf-8"
    )

    reporte = preparar_entrega(
        raiz=origen,
        indice=origen / "indice",
        resultados=en_sitio,
        destino=destino,
        modelo="m",
    )

    assert reporte.n_consultas == 1
    assert json.loads(en_sitio.read_text(encoding="utf-8"))["query_id"] == "q001"
