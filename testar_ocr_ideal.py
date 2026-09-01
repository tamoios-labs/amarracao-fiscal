"""
Runner do fluxo de leitura das notas fiscais IDEAL (OCR).

Rode DEPOIS que o GLPI já baixou as pastas (chamados_glpi/):

    python testar_ocr_ideal.py

Todo o fluxo e os prints ficam no módulo pdf_ocr.leitura — este arquivo
só dispara.
"""
from pdf_ocr import executar_leitura_ideal


if __name__ == "__main__":
    executar_leitura_ideal()
