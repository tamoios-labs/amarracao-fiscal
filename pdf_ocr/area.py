import json

from .config import ARQUIVO_AREA


def carregar_area(arquivo=ARQUIVO_AREA):
    """
    Lê a área de OCR salva por definir_area_pdf.py.
    """
    try:
        with open(arquivo) as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Área de OCR ainda não definida. Rode definir_area_pdf.py primeiro (esperado em '{arquivo}')."
        )


def salvar_area(area, arquivo=ARQUIVO_AREA):
    with open(arquivo, "w") as f:
        json.dump(area, f)
