import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from .waiting import esperar, com_contexto_correto, clicar_via_eventos_js


def preencher_usuario(driver, usuario, timeout=30, tentativas=3):
    """
    Campo de usuário é o componente po-login (irmão do po-password),
    input real em `po-login input[type="text"]`. Sem esse campo
    preenchido, o Protheus rejeita o login como "usuário não
    autenticado" mesmo com a senha certa - diferente do acesso manual,
    onde o navegador costuma ter esse campo salvo/autocompletado.

    Mesma lógica de digitação char-a-char de `preencher_senha`, pelo
    mesmo motivo (data-binding Angular perdendo caracteres em bloco).
    """
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            campo = esperar(
                driver, lambda d: d.find_element(By.CSS_SELECTOR, 'po-login input[type="text"]'), timeout
            )
            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.DELETE)
            time.sleep(0.2)

            for char in usuario:
                campo.send_keys(char)
                time.sleep(0.05)

            valor_atual = campo.get_attribute("value")
            if valor_atual != usuario:
                raise RuntimeError(
                    f"Usuário não foi preenchido corretamente "
                    f"(campo ficou com '{valor_atual}' em vez de '{usuario}')."
                )

            return campo
        except StaleElementReferenceException as e:
            ultimo_erro = e
            time.sleep(0.5)

    raise ultimo_erro


def preencher_senha(driver, senha, timeout=30, tentativas=3):
    """
    Campo de senha é um componente Angular normal (po-password/PO-UI),
    sem shadow DOM, então find_element funciona direto.

    Digita caractere a caractere com pequena pausa em vez de send_keys
    de uma vez: o data-binding do Angular pode não acompanhar o ritmo
    de send_keys/clear em bloco e "perder" caracteres. Se o elemento
    ficar stale (re-renderizado pelo Angular no meio do processo),
    relocaliza e tenta de novo.
    """
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            campo = esperar(
                driver, lambda d: d.find_element(By.CSS_SELECTOR, 'po-password input[type="password"]'), timeout
            )
            campo.click()
            campo.send_keys(Keys.CONTROL, "a")
            campo.send_keys(Keys.DELETE)
            time.sleep(0.2)

            for char in senha:
                campo.send_keys(char)
                time.sleep(0.05)

            valor_atual = campo.get_attribute("value")
            if valor_atual != senha:
                raise RuntimeError(
                    f"Senha não foi preenchida corretamente "
                    f"(campo ficou com {len(valor_atual)} de {len(senha)} caracteres)."
                )

            return campo
        except StaleElementReferenceException as e:
            ultimo_erro = e
            time.sleep(0.5)

    raise ultimo_erro


def clicar_entrar(driver, timeout=30):
    """
    Botão "Entrar" (po-button), no mesmo iframe do campo de senha.
    Localiza por texto ao invés de classe fixa: essa tela aparece mais de
    uma vez no fluxo (login e depois confirmação/ambiente) e a classe do
    po-button pode não ser a mesma nas duas.
    """
    botao = esperar(
        driver,
        lambda d: d.find_element(
            By.XPATH, '//po-button//span[normalize-space()="Entrar"]/ancestor::button[1]'
        ),
        timeout,
    )
    botao.click()
    return botao


def _clicar_um_entrar(driver, achar, timeout, tentativas):
    """
    Clica num único "Entrar" (já localizado por `achar`) e confirma que
    ele sumiu depois - `tentativas` rodadas de clique+confirmação antes
    de desistir. Devolve True se sumiu, False se esgotou as tentativas
    (ex: "Formulário inválido" segurando a tela, mesmo caso do print que
    motivou essa função: o clique "funciona" mas a validação do form
    barra o avanço e o mesmo botão reaparece).
    """
    botao = com_contexto_correto(driver, achar, timeout)

    for i in range(tentativas):
        try:
            if i < tentativas - 1:
                botao.click()  # clique nativo primeiro
            else:
                clicar_via_eventos_js(driver, botao)  # fallback
        except Exception:
            clicar_via_eventos_js(driver, botao)

        # confirma que o botão realmente sumiu (tela avançou de verdade)
        fim = time.time() + 5
        while time.time() < fim:
            if achar(driver) is None:
                return True
            time.sleep(0.3)

        # ainda tem um "Entrar" visível: relocaliza (pode ter re-renderizado,
        # ex: apareceu "Formulário inválido") e tenta de novo
        achado = achar(driver)
        if achado is None:
            return True
        botao = achado

    return False


def clicar_entrar_qualquer_contexto(driver, timeout=30, tentativas=3, telas_em_sequencia=3):
    """
    Mesma coisa que `clicar_entrar`, mas procura o botão "Entrar" em
    QUALQUER contexto (documento principal ou qualquer wa-webview) via
    `com_contexto_correto`, em vez de assumir um iframe fixo, e
    CONFIRMA que o clique realmente teve efeito (o botão sumiu depois)
    antes de considerar sucesso - mesmo padrão de `clicar_botao_dialog`.

    Necessário na tela de "Boas-vindas"/Parametros que aparece logo
    depois do login: nesse ponto o DOM tem MAIS DE UM wa-webview ao
    mesmo tempo (o antigo, da tela de login, e um novo pra essa tela de
    confirmação de ambiente) - `entrar_iframe_webview` sempre pega o
    primeiro que encontra no documento, que pode não ser o certo.

    A verificação pós-clique é essencial: um clique "sem erro" pode
    cair num "Entrar" antigo/residual que passa no filtro de
    visibilidade mas não é o botão que a tela realmente mostra - nesse
    caso o clique "funciona" (sem exceção) mas a tela nunca avança.

    TELAS EM SEQUÊNCIA: depois da tela de "Boas-vindas", pode aparecer
    uma segunda tela de confirmação (ex: seleção de Grupo/Filial/
    Ambiente), com o seu PRÓPRIO botão "Entrar" - mesmo padrão
    (po-button > span "Entrar"). Por isso, depois de cada "Entrar"
    sumir, checa rapidamente (5s) se surgiu outro "Entrar" novo e clica
    nele também, até `telas_em_sequencia` telas ou até não sobrar mais
    nenhum "Entrar" visível.
    """
    def achar(d):
        for span in d.find_elements(By.XPATH, '//po-button//span[normalize-space()="Entrar"]'):
            try:
                botao = span.find_element(By.XPATH, "ancestor::button[1]")
                if botao.is_displayed():
                    return botao
            except Exception:
                continue
        return None

    for tela in range(telas_em_sequencia):
        timeout_tela = timeout if tela == 0 else 5
        try:
            com_contexto_correto(driver, achar, timeout_tela)
        except TimeoutException:
            if tela == 0:
                raise
            return  # não apareceu mais nenhum "Entrar" - terminou a sequência

        sumiu = _clicar_um_entrar(driver, achar, timeout_tela, tentativas)
        if not sumiu:
            raise TimeoutException(
                f"Botão 'Entrar' foi clicado mas a tela não avançou após {tentativas} tentativas "
                f"(tela {tela + 1} da sequência)."
            )


def login_teve_sucesso(driver, timeout=15):
    """
    Confirma se o login foi aceito: espera a tela de login (po-login)
    sumir depois do clique em "Entrar". Se ela continuar presente até
    o timeout, o Protheus rejeitou o login (usuário/senha errados ou
    "usuário não autenticado") e a tela permanece/reaparece com erro.

    Precisa ser chamada no mesmo contexto (documento ou iframe) de onde
    `clicar_entrar` foi chamada.
    """
    def sumiu(d):
        return len(d.find_elements(By.CSS_SELECTOR, "po-login")) == 0

    try:
        esperar(driver, sumiu, timeout)
        return True
    except TimeoutException:
        return False
