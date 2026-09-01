import tkinter as tk
from tkinter import filedialog

from pdf_ocr import extrair_numeros

root = tk.Tk()
root.withdraw()
PDFS = filedialog.askopenfilenames(
    title="Selecione os PDFs",
    filetypes=[("Arquivos PDF", "*.pdf")]
)
root.destroy()

if not PDFS:
    print("Nenhum arquivo selecionado.")
    exit()

numeros = extrair_numeros(PDFS)
print("\n".join(numeros))
