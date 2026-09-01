import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from .waiting import com_contexto_correto, clicar_via_eventos_js


def _achar_input_texto(d, placeholder):
    """
    Procura um wa-text-input (tget do AdvPL) pelo `placeholder` e devolve
    (host, input_interno).

    O <input> real fica DENTRO do shadow root do componente, então o id
    do host (ex: "COMP6021") não serve de âncora — o Protheus gera esse
    número a cada render da tela. O par estável é
    `data-advpl="tget"` + `placeholder`.

    O Protheus mantém campos de telas anteriores no DOM, então pode
    haver mais de um campo com o MESMO placeholder "visível" ao mesmo
    tempo (ex: o da tela de trás, coberta por uma tela nova por cima) -
    `is_displayed()` do Selenium não detecta isso, já que só olha
    CSS (display/visibility/opacity), não sobreposição/z-index. Por
    isso, entre os visíveis, prioriza o de MAIOR z-index (o que está
    fisicamente em primeiro plano) em vez do primeiro que aparecer no
    DOM - senão dá pra acabar digitando no campo errado, escondido atrás
    do que a tela realmente mostra.
    """
    alvo = placeholder.strip().lower()
    candidato_oculto = None
    melhor_visivel = None  # (z_index, host, interno)

    for host in d.find_elements(By.CSS_SELECTOR, 'wa-text-input[data-advpl="tget"]'):
        try:
            if (host.get_attribute("placeholder") or "").strip().lower() != alvo:
                continue
            root = host.shadow_root
            if not root:
                continue
            interno = root.find_element(By.CSS_SELECTOR, "input")
        except Exception:
            continue

        try:
            visivel = host.is_displayed()
        except Exception:
            visivel = False

        if visivel:
            try:
                z_index = d.execute_script(
                    "return parseInt(getComputedStyle(arguments[0]).zIndex, 10) || 0;", host
                )
            except Exception:
                z_index = 0
            if melhor_visivel is None or z_index > melhor_visivel[0]:
                melhor_visivel = (z_index, host, interno)
        elif candidato_oculto is None:
            candidato_oculto = (host, interno)

    if melhor_visivel is not None:
        return (melhor_visivel[1], melhor_visivel[2])
    return candidato_oculto


def _focar(driver, host, interno):
    """
    Garante que o <input> interno está com o foco — sem isso as teclas
    (inclusive o ENTER) não chegam a lugar nenhum.

    Tenta o clique nativo no input primeiro, porque ele gera foco de
    verdade; se estiver clipado/interceptado (o campo fica dentro de
    painéis com overflow:hidden), cai pros eventos sintéticos + focus()
    via JS. Confirma pelo activeElement do shadow root.
    """
    try:
        interno.click()
    except Exception:
        clicar_via_eventos_js(driver, interno, host)
        driver.execute_script("arguments[0].focus();", interno)

    return bool(
        driver.execute_script(
            "return arguments[0].shadowRoot && arguments[0].shadowRoot.activeElement === arguments[1];",
            host,
            interno,
        )
    )


def _limpar(driver, interno):
    """
    Esvazia o campo de um jeito bem redundante: vai pro INÍCIO e dá 12
    DELETE (apaga pra frente), depois vai pro FIM e dá 12 BACKSPACE
    (apaga pra trás) - o campo "Pesquisar" (NF com zeros à esquerda, ver
    `pesquisar`) tem no máximo 9 dígitos, então 12 de cada lado já cobre
    o campo cheio com folga, dos dois lados, não só um. Cada tecla com
    uma pausa curta - digitar
    rápido demais nesse campo mascarado (`picture`) pode fazer ele
    reformatar errado, e no fim das contas é justamente uma limpeza mal
    feita aqui que já causou sobra de busca anterior grudada com o
    número novo (ex: campo mostrando "00002890003" em vez de
    "000028903" - resto de "000028900" que não tinha sido apagado
    direito antes do "03" novo entrar).

    NÃO usa CTRL+A: em campos com `picture`/máscara (ex: "Pesquisar"), o
    CTRL+A é engolido como se fosse a letra "a" digitada.

    Último recurso: se mesmo assim sobrar valor, força a limpeza via JS
    direto no `value` + evento `input` sintético, sem depender de
    teclado.
    """
    interno.send_keys(Keys.HOME)
    for _ in range(12):
        interno.send_keys(Keys.DELETE)
        time.sleep(0.05)

    interno.send_keys(Keys.END)
    for _ in range(12):
        interno.send_keys(Keys.BACKSPACE)
        time.sleep(0.05)

    if not interno.get_attribute("value"):
        return

    driver.execute_script(
        """
        const el = arguments[0];
        el.value = '';
        el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
        el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
        """,
        interno,
    )


def preencher_input(driver, texto, placeholder="Pesquisar", enter=True, timeout=30, tentativas=3):
    """
    Escreve `texto` no wa-text-input identificado por `placeholder` e,
    se `enter=True`, dispara ENTER no final.

    Digita caractere a caractere com uma pausa curta em vez de send_keys
    em bloco: o tget revalida/reformata o conteúdo a cada tecla (tem
    `picture` e `maxlength`), e o envio em bloco pode "perder"
    caracteres. Valida o `value` no fim pra não seguir o fluxo achando
    que pesquisou um número que na verdade entrou truncado.

    O ENTER vai via send_keys no <input> nativo (evento de tecla real,
    mais confiável que evento sintético). Se numa tela específica o
    ENTER não disparar a busca, use `clicar_botao_pesquisa` — a lupa
    faz a mesma coisa.

    Devolve o <input> interno.
    """
    texto = str(texto)
    ultimo_erro = None

    for _ in range(tentativas):
        try:
            host, interno = com_contexto_correto(
                driver, lambda d: _achar_input_texto(d, placeholder), timeout
            )

            maxlength = interno.get_attribute("maxlength") or host.get_attribute("maxlength")
            if maxlength and maxlength.isdigit() and len(texto) > int(maxlength):
                raise ValueError(
                    f"Texto tem {len(texto)} caracteres, mas o campo '{placeholder}' "
                    f"aceita no máximo {maxlength}."
                )

            if not _focar(driver, host, interno):
                raise RuntimeError(f"Não foi possível focar o campo '{placeholder}'.")

            _limpar(driver, interno)
            time.sleep(0.2)

            for char in texto:
                interno.send_keys(char)
                time.sleep(0.05)

            # Alguns campos (ex: "Pesquisar" do Monitor) têm sugestão/
            # autocomplete que insere texto extra na mesma digitação
            # (sobra de buscas anteriores, espaços de preenchimento até
            # o maxlength etc.). Força o valor final via JS, sobrescrevendo
            # qualquer coisa que a digitação char-a-char tenha deixado
            # sobrando, antes de validar.
            driver.execute_script(
                """
                const el = arguments[0];
                el.value = arguments[1];
                el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
                el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                """,
                interno,
                texto,
            )

            valor_atual = interno.get_attribute("value") or ""
            if valor_atual.strip() != texto.strip():
                raise RuntimeError(
                    f"Campo '{placeholder}' ficou com '{valor_atual}' em vez de '{texto}'."
                )

            if enter:
                # Reforça o foco antes do ENTER: o JS acima (execute_script)
                # pode ter tirado o foco real do campo. `send_keys(ENTER)`
                # direto no elemento não estava disparando a busca -
                # ActionChains manda a tecla pro elemento ATIVO da página
                # (mais parecido com uma tecla física de verdade) em vez
                # de um evento sintético associado só ao WebElement.
                #
                # Manda DOIS enters com 0.5s de intervalo: um ENTER só não
                # estava sendo suficiente pra disparar a busca de forma
                # confiável.
                _focar(driver, host, interno)
                ActionChains(driver).send_keys(Keys.RETURN).perform()
                time.sleep(0.5)
                ActionChains(driver).send_keys(Keys.RETURN).perform()

            return interno
        except (StaleElementReferenceException, RuntimeError) as e:
            # StaleElementReferenceException: o Protheus re-renderiza a
            # tela por conta própria. RuntimeError (foco/valor): a tela
            # ainda pode estar processando algo (ex: filtros de uma
            # dialog que acabou de fechar) quando a primeira tentativa
            # roda. Nos dois casos, relocaliza e tenta de novo.
            ultimo_erro = e
            time.sleep(0.5)

    raise ultimo_erro


def clicar_botao_pesquisa(driver, placeholder="Pesquisar", timeout=30, tentativas=3):
    """
    Clica na lupa de pesquisa. Tentar localizar esse botão pela
    estrutura do DOM (irmão do wa-text-input, seja por XPath
    `following-sibling::` ou por `nextElementSibling` via JS) não deu
    certo de forma confiável - provavelmente a relação real no DOM não
    é tão simples quanto um dump estático sugere.

    Em vez disso, usa `elementFromPoint` num ponto perto da borda
    direita do campo "Pesquisar" (onde o ícone da lupa fica desenhado
    por cima/ao lado do input) pra pegar o que está VISUALMENTE ali,
    independente de qualquer suposição sobre a estrutura do DOM.

    Confirma que o clique realmente registrou (clique nativo primeiro,
    fallback via eventos JS) - mesmo padrão de
    `clicar_entrar_qualquer_contexto`/`clicar_botao_dialog`.
    """
    def achar(d):
        host, _interno = _achar_input_texto(d, placeholder) or (None, None)
        if host is None:
            return None
        try:
            botao = d.execute_script(
                """
                const el = arguments[0];
                const r = el.getBoundingClientRect();
                const x = r.right - 15;
                const y = r.top + r.height / 2;
                return document.elementFromPoint(x, y);
                """,
                host,
            )
            if botao:
                return botao
        except Exception:
            pass
        return None

    botao = com_contexto_correto(driver, achar, timeout)

    ultimo_erro = None
    for _ in range(tentativas):
        try:
            botao.click()
            return botao
        except Exception as e:
            ultimo_erro = e
            try:
                clicar_via_eventos_js(driver, botao)
                return botao
            except Exception as e2:
                ultimo_erro = e2
                achado = achar(driver)
                if achado is None:
                    raise TimeoutException(
                        f"Lupa de pesquisa do campo '{placeholder}' não foi encontrada pra relocalizar."
                    )
                botao = achado

    raise ultimo_erro


def pesquisar(driver, texto, timeout=30):
    """
    Atalho pro caso mais comum: joga `texto` no campo "Pesquisar" da
    tela do Monitor e confirma com ENTER.
    """
    return preencher_input(driver, texto, placeholder="Pesquisar", enter=True, timeout=timeout)
