import re

from pdf2image import convert_from_path
import pytesseract
from PIL import ImageFilter, ImageOps

from .config import POPPLER_PATH, TESSERACT_PATH, ARQUIVO_AREA_PEDIDO
from .area import carregar_area

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def extrair_numero_e_recorte(pdf_path, area=None, poppler_path=POPPLER_PATH):
    """
    Faz OCR na área definida (ver definir_area_pdf.py) da primeira página
    do PDF. Devolve (numero, recorte): o número de 9 dígitos encontrado
    (ou None) e a imagem PIL do recorte usado no OCR (já tratada: 2x,
    grayscale, sharpen) - usada por quem precisa salvar o recorte como
    print (ex: exportar_notas_do_chamado).
    """
    if area is None:
        area = carregar_area()

    pagina = convert_from_path(pdf_path, first_page=1, last_page=1, poppler_path=poppler_path, dpi=300)[0]
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
    Acha o número de 9 dígitos no texto OCR'd, ANCORADO no rótulo "N."
    (ex: "N. 000027640") - não pega qualquer sequência isolada de 9
    dígitos em qualquer lugar do recorte.

    Necessário porque a área recortada às vezes também pega um pedaço
    da "Chave de Acesso" ao lado do campo "N." (44 dígitos formatados
    em grupos) - se o OCR do "N." falhar/ficar ruidoso, um regex sem
    âncora pode "achar" por acidente 9 dígitos isolados vindos da
    chave ou de outro número da página, retornando um valor ERRADO em
    vez de None. Prefere não achar nada a achar o número errado.
    """
    texto_sem_espacos = texto.replace(" ", "")
    match = re.search(r'N[.:°ºO0,]?(\d{9})(?!\d)', texto_sem_espacos)
    return match.group(1) if match else None


def extrair_numero_pedido_e_recorte(pdf_path, area=None, poppler_path=POPPLER_PATH):
    """
    Faz OCR na área do número do pedido de compra (canto superior
    direito do PEDIDO.pdf, ex: "061410 /1") e devolve (numero, recorte):
    o número de 6 dígitos do pedido, SEM o "/N" de revisão que vem
    logo depois (ex: "061410", não "061410 /1") - e a imagem PIL do
    recorte usado no OCR, pra salvar como print.

    A área é deliberadamente mais larga que só o número (inclui o
    "/N" e um pouco do texto "...COMPRAS - REAL" antes) em vez de
    cortar rente: um corte muito justo variava de posição entre PDFs
    de fornecedores diferentes e cortava dígitos pela metade. Cortar
    largo e extrair por regex (ancorado no padrão "NNNNNN /N") é mais
    robusto - mesmo princípio de `_extrair_numero_do_texto`.

    Layout é o mesmo formulário (gerado pela Tamoios) pra qualquer
    fornecedor - não é específico do IDEAL, por isso usa
    ARQUIVO_AREA_PEDIDO (não ARQUIVO_AREA).
    """
    if area is None:
        area = carregar_area(ARQUIVO_AREA_PEDIDO)

    pagina = convert_from_path(pdf_path, first_page=1, last_page=1, poppler_path=poppler_path, dpi=300)[0]
    larg, alt = pagina.size

    recorte = pagina.crop((
        int(area["x0"] * larg),
        int(area["y0"] * alt),
        int(area["x1"] * larg),
        int(area["y1"] * alt),
    ))
    recorte = recorte.resize((recorte.width * 2, recorte.height * 2))
    recorte = ImageOps.grayscale(recorte)

    texto = pytesseract.image_to_string(recorte, lang="eng", config="--psm 6")
    match = re.search(r'(\d{6})\s*/\s*\d', texto)
    numero = match.group(1) if match else None
    return numero, recorte


def extrair_numero_pedido(pdf_path, area=None, poppler_path=POPPLER_PATH):
    """Atalho de `extrair_numero_pedido_e_recorte` que devolve só o número."""
    numero, _recorte = extrair_numero_pedido_e_recorte(pdf_path, area, poppler_path)
    return numero


def extrair_numero_nota(pdf_path, area=None, poppler_path=POPPLER_PATH):
    """
    Faz OCR na área definida (ver definir_area_pdf.py) da primeira página
    do PDF e devolve o número de 9 dígitos encontrado, ou None.
    """
    numero, _recorte = extrair_numero_e_recorte(pdf_path, area, poppler_path)
    return numero


def extrair_numeros(pdfs, area=None):
    """
    Roda extrair_numero_nota pra uma lista de PDFs. Imprime um aviso pros
    que não derem match e devolve só os números encontrados.
    """
    if area is None:
        area = carregar_area()

    numeros = []
    for pdf in sorted(pdfs):
        print(f"\n--- {pdf} ---")
        numero = extrair_numero_nota(pdf, area)
        if numero:
            numeros.append(numero)
        else:
            print(f"[AVISO] Número não encontrado em: {pdf}")
    return numeros
