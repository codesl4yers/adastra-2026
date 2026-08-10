"""Pruebas de la verificación de piso: ningún documento del índice sin vectores.

Es la única comprobación que detecta la forma garantizada de perder F1@3: un
archivo con cero fragmentos no puede aparecer en el top-3 por buena que sea la
consulta, y nada más en el pipeline avisa de ello —el extractor que falla en
silencio produce un documento válido y vacío—.
"""

import json

import pytest

from scripts.verificar_cobertura import documentos_sin_vectores, main


def escribir_metadata(ruta, registros):
    ruta.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in registros),
        encoding="utf-8",
        newline="\n",
    )
    return ruta


@pytest.fixture
def entradas():
    """Dos documentos del inventario de ADL, ya normalizados."""
    from indice import EntradaIndice

    return {
        "carpeta/uno.pdf": EntradaIndice(
            doc_id="F1-OBS-001",
            fuente="uno.pdf",
            ruta_relativa="carpeta/uno.pdf",
            fenomeno=1,
            observatorio="OBS",
            codigo_observatorio="OBS",
            tipo_declarado="PDF",
        ),
        "carpeta/dos.csv": EntradaIndice(
            doc_id="F1-OBS-002",
            fuente="dos.csv",
            ruta_relativa="carpeta/dos.csv",
            fenomeno=1,
            observatorio="OBS",
            codigo_observatorio="OBS",
            tipo_declarado="CSV",
        ),
    }


def test_un_documento_sin_vectores_se_reporta(entradas, tmp_path):
    metadata = escribir_metadata(
        tmp_path / "metadata.jsonl", [{"doc_id": "F1-OBS-001", "fuente": "uno.pdf"}]
    )

    huecos = documentos_sin_vectores(entradas, metadata)

    assert [e.doc_id for e in huecos] == ["F1-OBS-002"]


def test_con_todos_los_documentos_cubiertos_no_hay_huecos(entradas, tmp_path):
    metadata = escribir_metadata(
        tmp_path / "metadata.jsonl",
        [
            {"doc_id": "F1-OBS-001", "fuente": "uno.pdf"},
            {"doc_id": "F1-OBS-002", "fuente": "dos.csv"},
        ],
    )

    assert documentos_sin_vectores(entradas, metadata) == []


def test_un_doc_id_de_metadata_que_no_esta_en_el_indice_no_tapa_un_hueco(
    entradas, tmp_path
):
    """Contar líneas no basta: hay que emparejar por identidad."""
    metadata = escribir_metadata(
        tmp_path / "metadata.jsonl",
        [
            {"doc_id": "F1-OBS-001", "fuente": "uno.pdf"},
            {"doc_id": "F9-OTRO-999", "fuente": "intruso.pdf"},
        ],
    )

    huecos = documentos_sin_vectores(entradas, metadata)

    assert [e.doc_id for e in huecos] == ["F1-OBS-002"]


def test_el_cli_falla_cuando_hay_huecos(entradas, tmp_path, monkeypatch):
    metadata = escribir_metadata(
        tmp_path / "metadata.jsonl", [{"doc_id": "F1-OBS-001", "fuente": "uno.pdf"}]
    )
    monkeypatch.setattr("scripts.verificar_cobertura.cargar_indice", lambda _: entradas)

    codigo = main(["--indice", "falso.xlsx", "--metadata", str(metadata)])

    assert codigo == 1


def test_el_cli_pasa_cuando_no_hay_huecos(entradas, tmp_path, monkeypatch):
    metadata = escribir_metadata(
        tmp_path / "metadata.jsonl",
        [
            {"doc_id": "F1-OBS-001", "fuente": "uno.pdf"},
            {"doc_id": "F1-OBS-002", "fuente": "dos.csv"},
        ],
    )
    monkeypatch.setattr("scripts.verificar_cobertura.cargar_indice", lambda _: entradas)

    codigo = main(["--indice", "falso.xlsx", "--metadata", str(metadata)])

    assert codigo == 0
