import os

# Raiz do diretório da amarracao (um nível acima deste arquivo).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- GLPI ---
GLPI_LOGIN_URL = (
    "http://ti.concessionariatamoios.com.br/glpi/index.php"
    "?redirect=%2Ffront%2Fhelpdesk.public.php?error=3"
)
GLPI_URL_CHAMADOS = "http://ti.concessionariatamoios.com.br/glpi/front/ticket.php"
GLPI_USUARIO = "srv.controladoria"
GLPI_SENHA = "Ctlr@2026_Sv#9LxQ!"

# Pasta de saída DENTRO do diretório da amarracao (antes era um
# compartilhamento de rede). As pastas dos chamados do dia são criadas
# em: <amarracao>/chamados_glpi/<dd-mm-aaaa>/<nome_do_chamado>/
PASTA_SAIDA_BASE = os.path.join(BASE_DIR, "chamados_glpi")

# JSON com o resultado consolidado da extração.
RESULT_FILE = os.path.join(PASTA_SAIDA_BASE, "resultados_completo.json")

# ─────────────────────────────────────────────────────────────────────
# MODO DE TESTE: processa apenas os N primeiros chamados coletados
# (cria pasta + baixa anexos só desses; os demais são ignorados).
#
# COMO REVERTER (voltar a processar TODOS os chamados):
#   defina LIMITE_TESTE_CHAMADOS = None
# ─────────────────────────────────────────────────────────────────────
LIMITE_TESTE_CHAMADOS = None
