"""
Log compartilhado por todo o glpi_automation - símbolos e seções
consistentes entre login, coleta de chamados (página a página),
extração de dados e download de anexos, em vez de cada arquivo montar
o próprio estilo de print.
"""

LARGURA = 60
TITULO = "=" * LARGURA
LINHA = "-" * LARGURA


def log(msg=""):
    print(msg, flush=True)


def titulo(msg):
    """Cabeçalho de destaque máximo - só pro início/fim do processo inteiro."""
    log(f"\n{TITULO}")
    log(f"  {msg}")
    log(TITULO)


def secao(msg):
    """Cabeçalho de uma etapa (ex: 'PASSO 1 - Login e coleta de chamados')."""
    log(f"\n{LINHA}")
    log(f"  {msg}")
    log(LINHA)


def _prefixo(nivel):
    return "  " * (nivel + 1)


def ok(msg, nivel=0):
    log(f"{_prefixo(nivel)}✔ {msg}")


def aviso(msg, nivel=0):
    log(f"{_prefixo(nivel)}⚠ {msg}")


def erro(msg, nivel=0):
    log(f"{_prefixo(nivel)}✗ {msg}")


def info(msg, nivel=0):
    log(f"{_prefixo(nivel)}• {msg}")
