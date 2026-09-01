"""
OCR do numero da nota fiscal IDEAL — extraido de protheus_automation/pdf_ocr
para uso standalone em outro projeto.

Le a primeira pagina do PDF, recorta a area onde fica o campo "N."
(numero da nota, 9 digitos) no layout especifico do fornecedor IDEAL,
faz OCR nesse recorte e devolve so o numero.

Dependencias:
    pip install pdf2image pytesseract pillow

Tambem precisa, instalados no sistema (nao e pip install):
    - Poppler   (usado pelo pdf2image pra rasterizar o PDF)
    - Tesseract OCR

Ajuste POPPLER_PATH e TESSERACT_PATH abaixo para os caminhos de
instalacao no ambiente onde este arquivo vai rodar.

Uso:
    from ocr_numero_nota_ideal import extrair_numero_nota_ideal

    numero = extrair_numero_nota_ideal("nota.pdf")
    if numero:
        print(numero)
"""
import re

from pdf2image import convert_from_path
import pytesseract
from PIL import ImageFilter, ImageOps

# --- Ajustar para o ambiente de destino ---
POPPLER_PATH = r"C:\poppler\Library\bin"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# Area (em proporcao 0-1 da largura/altura da pagina) onde fica o
# campo "N." no layout do fornecedor IDEAL. Calibrada previamente com
# a ferramenta de recorte do projeto original — se o layout do PDF
# mudar, essa area precisa ser recalibrada.
AREA_NUMERO_NOTA_IDEAL = {"x0": 0.3327, "y0": 0.0387, "x1": 0.54, "y1": 0.28}


def extrair_numero_nota_ideal(pdf_path, area=None, poppler_path=POPPLER_PATH, dpi=300):
    """
    Faz OCR na area do numero da nota (primeira pagina do PDF) e
    devolve o numero de 9 digitos encontrado, ou None.
    """
    numero, _recorte = extrair_numero_e_recorte(pdf_path, area, poppler_path, dpi)
    return numero


def extrair_numero_e_recorte(pdf_path, area=None, poppler_path=POPPLER_PATH, dpi=300):
    """
    Igual a `extrair_numero_nota_ideal`, mas tambem devolve o recorte
    (imagem PIL, ja tratada: 2x, grayscale, sharpen) usado no OCR —
    util para debug/conferencia visual ou pra salvar como print.
    """
    if area is None:
        area = AREA_NUMERO_NOTA_IDEAL

    pagina = convert_from_path(pdf_path, first_page=1, last_page=1, poppler_path=poppler_path, dpi=dpi)[0]
    larg, alt = pagina.size

    recorte = pagina.crop((
        int(area["x0"] * larg),
        int(area["y0"] * alt),
        int(area["x1"] * larg),
        int(area["y1"] * alt),
    ))
    recorte = recorte.resize((recorte.width * 2, recorte.height * 2))
    recorte = ImageOps.grayscale(recorte)
    recorte = recorte.filter(ImageFilter.SHARPEN)

    texto = pytesseract.image_to_string(recorte, lang="eng", config="--psm 6")
    numero = _extrair_numero_do_texto(texto)
    return numero, recorte


def _extrair_numero_do_texto(texto):
    """
    Acha o numero de 9 digitos no texto OCR'd, ANCORADO no rotulo "N."
    (ex: "N. 000027640") - nao pega qualquer sequencia isolada de 9
    digitos em qualquer lugar do recorte.

    Necessario porque a area recortada as vezes tambem pega um pedaco
    da "Chave de Acesso" ao lado do campo "N." (44 digitos formatados
    em grupos) - se o OCR do "N." falhar/ficar ruidoso, um regex sem
    ancora pode "achar" por acidente 9 digitos isolados vindos da
    chave ou de outro numero da pagina, retornando um valor ERRADO em
    vez de None. Prefere nao achar nada a achar o numero errado.
    """
    texto_sem_espacos = texto.replace(" ", "")
    match = re.search(r'N[.:°ºO0,]?(\d{9})(?!\d)', texto_sem_espacos)
    return match.group(1) if match else None


def selecionar_pdf():
    """
    Abre uma janela para o usuario escolher um arquivo PDF e devolve
    o caminho escolhido (ou "" se ele cancelar).
    """
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    caminho = filedialog.askopenfilename(
        title="Selecione o PDF",
        filetypes=[("Arquivos PDF", "*.pdf")],
    )
    root.destroy()
    return caminho


if __name__ == "__main__":
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else selecionar_pdf()
    if not pdf_path:
        print("Nenhum PDF selecionado.")
        raise SystemExit(1)

    resultado = extrair_numero_nota_ideal(pdf_path)
    print(resultado or "[AVISO] Numero nao encontrado.")
