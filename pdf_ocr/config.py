import os

POPPLER_PATH = r"C:\poppler\Library\bin"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Raiz do projeto (um nível acima de pdf_ocr/).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Área de OCR do número da NF no layout específico do fornecedor IDEAL.
ARQUIVO_AREA = os.path.join(BASE_DIR, "coordenadas_pdfs", "numero_nf_ideal.json")

# Área de OCR do número do pedido de compra (PEDIDO.pdf) - layout
# padrão gerado pela Tamoios, igual pra qualquer fornecedor (não é
# específico do IDEAL).
ARQUIVO_AREA_PEDIDO = os.path.join(BASE_DIR, "coordenadas_pdfs", "numero_pedido.json")

# Pasta onde o GLPI grava os chamados baixados (fonte dos PDFs a ler).
CHAMADOS_DIR = os.path.join(BASE_DIR, "chamados_glpi")
