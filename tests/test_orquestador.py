"""Pruebas del orquestador y de la persistencia.

Cubre el punto 3 del enunciado: dos corridas producen bytes idénticos.
"""

import json
import os
import subprocess
import sys

import pytest

from contrato import calcular_doc_id
from orquestador import cargar_manifiesto, procesar_directorio


@pytest.fixture
def salida_doble(dir_fixtures, tmp_path):
    """Ejecuta el pipeline dos veces sobre el mismo corpus, en directorios distintos."""
    primera = tmp_path / "corrida_1"
    segunda = tmp_path / "corrida_2"
    procesar_directorio(dir_fixtures, primera)
    procesar_directorio(dir_fixtures, segunda)
    return primera, segunda


# --- punto 3: determinismo byte a byte ---------------------------------------


def test_dos_corridas_producen_los_mismos_archivos(salida_doble):
    primera, segunda = salida_doble
    nombres_1 = sorted(p.name for p in primera.iterdir())
    nombres_2 = sorted(p.name for p in segunda.iterdir())
    assert nombres_1 == nombres_2
    assert nombres_1, "el pipeline produjo algo"


def test_dos_corridas_producen_bytes_identicos(salida_doble):
    primera, segunda = salida_doble
    for archivo in sorted(primera.iterdir()):
        gemelo = segunda / archivo.name
        assert archivo.read_bytes() == gemelo.read_bytes(), f"difiere {archivo.name}"


def test_determinismo_con_semilla_de_hash_distinta(dir_fixtures, tmp_path, raiz_proyecto):
    """El resultado no puede depender de PYTHONHASHSEED.

    Es la prueba que detecta el uso de set() sin ordenar o de hash() nativo.
    """
    salidas = []
    for semilla in ("0", "12345"):
        destino = tmp_path / f"semilla_{semilla}"
        entorno = dict(os.environ, PYTHONHASHSEED=semilla)
        resultado = subprocess.run(
            [
                sys.executable,
                "orquestador.py",
                "--entrada",
                str(dir_fixtures),
                "--salida",
                str(destino),
            ],
            cwd=raiz_proyecto,
            env=entorno,
            capture_output=True,
            text=True,
        )
        assert resultado.returncode == 0, resultado.stderr
        salidas.append(destino)

    primera, segunda = salidas
    archivos = sorted(p.name for p in primera.iterdir())
    assert archivos == sorted(p.name for p in segunda.iterdir())
    for nombre in archivos:
        assert (primera / nombre).read_bytes() == (segunda / nombre).read_bytes()


def test_los_archivos_usan_saltos_de_linea_unix(salida_doble):
    """En Windows, un salto CRLF rompería el diff entre plataformas."""
    primera, _ = salida_doble
    for archivo in primera.iterdir():
        assert b"\r\n" not in archivo.read_bytes(), f"{archivo.name} tiene CRLF"


# --- estructura de la salida -------------------------------------------------


def test_escribe_un_json_por_documento_nombrado_por_doc_id(salida_doble):
    primera, _ = salida_doble
    manifiesto = cargar_manifiesto(primera / "manifiesto.jsonl")
    for entrada in manifiesto:
        assert (primera / f"{entrada['doc_id']}.json").exists()


def test_el_json_tiene_claves_ordenadas_e_indentacion_fija(salida_doble):
    primera, _ = salida_doble
    doc_id = calcular_doc_id("bien_formado.html")
    crudo = (primera / f"{doc_id}.json").read_text(encoding="utf-8")

    datos = json.loads(crudo)
    assert list(datos.keys()) == sorted(datos.keys())
    assert crudo == json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_el_json_conserva_los_acentos_sin_escapar(salida_doble):
    primera, _ = salida_doble
    doc_id = calcular_doc_id("bien_formado.html")
    crudo = (primera / f"{doc_id}.json").read_text(encoding="utf-8")
    assert "Deforestación" in crudo
    assert "\\u00f3" not in crudo


# --- manifiesto ---------------------------------------------------------------


def test_el_manifiesto_esta_ordenado_por_fuente(salida_doble):
    primera, _ = salida_doble
    fuentes = [e["fuente"] for e in cargar_manifiesto(primera / "manifiesto.jsonl")]
    assert fuentes == sorted(fuentes)


def test_el_manifiesto_tiene_una_linea_por_documento(salida_doble, dir_fixtures):
    primera, _ = salida_doble
    entradas = cargar_manifiesto(primera / "manifiesto.jsonl")
    html_en_fixtures = sorted(p.name for p in dir_fixtures.glob("*.html"))
    assert [e["fuente"] for e in entradas] == html_en_fixtures


def test_el_manifiesto_tiene_los_campos_del_contrato(salida_doble):
    primera, _ = salida_doble
    esperados = {
        "doc_id",
        "fuente",
        "formato",
        "fenomeno",
        "idioma",
        "n_bloques",
        "n_chars",
        "n_errores",
    }
    for entrada in cargar_manifiesto(primera / "manifiesto.jsonl"):
        assert set(entrada.keys()) == esperados


def test_los_contadores_del_manifiesto_coinciden_con_el_documento(salida_doble):
    primera, _ = salida_doble
    for entrada in cargar_manifiesto(primera / "manifiesto.jsonl"):
        datos = json.loads((primera / f"{entrada['doc_id']}.json").read_text(encoding="utf-8"))
        assert entrada["n_bloques"] == len(datos["bloques"])
        assert entrada["n_errores"] == len(datos["errores"])
        assert entrada["n_chars"] == sum(len(b["texto"]) for b in datos["bloques"])


def test_el_manifiesto_registra_el_documento_corrupto(salida_doble):
    primera, _ = salida_doble
    entradas = {e["fuente"]: e for e in cargar_manifiesto(primera / "manifiesto.jsonl")}
    corrupto = entradas["malformado.html"]
    assert corrupto["n_bloques"] == 0
    assert corrupto["n_errores"] > 0


# --- recorrido y robustez -----------------------------------------------------


def test_ignora_los_formatos_sin_extractor_registrado(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "notas.txt").write_text("texto suelto", encoding="utf-8")
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["pagina.html"]


def test_un_formato_aun_no_implementado_no_tumba_el_pipeline(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / "informe.pdf").write_bytes(b"%PDF-1.4 contenido falso")
    (entrada / "pagina.html").write_text("<html><body><p>Hola</p></body></html>", encoding="utf-8")

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    por_fuente = {d.fuente: d for d in documentos}
    assert por_fuente["informe.pdf"].bloques == []
    assert por_fuente["informe.pdf"].errores != []
    assert por_fuente["pagina.html"].bloques != []


def test_recorre_subdirectorios(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "sub").mkdir(parents=True)
    (entrada / "sub" / "anidada.html").write_text(
        "<html><body><p>Contenido anidado</p></body></html>", encoding="utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida")
    assert [d.fuente for d in documentos] == ["anidada.html"]


def test_detecta_colision_de_nombres_entre_subdirectorios(tmp_path):
    """Dos archivos con el mismo nombre comparten fuente y por tanto doc_id."""
    entrada = tmp_path / "entrada"
    (entrada / "a").mkdir(parents=True)
    (entrada / "b").mkdir(parents=True)
    (entrada / "a" / "informe.html").write_text("<html><body><p>Uno</p></body></html>", "utf-8")
    (entrada / "b" / "informe.html").write_text("<html><body><p>Dos</p></body></html>", "utf-8")

    with pytest.raises(ValueError, match="colisión"):
        procesar_directorio(entrada, tmp_path / "salida")


def test_directorio_de_entrada_vacio_produce_manifiesto_vacio(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    salida = tmp_path / "salida"

    documentos = procesar_directorio(entrada, salida)
    assert documentos == []
    assert (salida / "manifiesto.jsonl").read_bytes() == b""


def test_el_fenomeno_se_infiere_del_subdirectorio(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / "fenomeno_2").mkdir(parents=True)
    (entrada / "fenomeno_2" / "informe.html").write_text(
        "<html><body><p>Contenido del segundo fenómeno</p></body></html>", "utf-8"
    )

    documentos = procesar_directorio(entrada, tmp_path / "salida", fenomeno_por_defecto=1)
    assert documentos[0].fenomeno == 2
