import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException


def esperar(driver, funcao, timeout=30, intervalo=0.3):
    """WebDriverWait não atravessa shadow DOM, então fazemos o polling manualmente."""
    return WebDriverWait(driver, timeout, poll_frequency=intervalo).until(lambda d: funcao(d) or False)


def clicar_via_eventos_js(driver, *elementos):
    """
    Clique robusto para componentes wa-* (shadow DOM): traz o elemento
    pra visão e dispara a sequência completa de eventos de pointer +
    mouse. Detalhe crucial: os eventos precisam de `composed: true`,
    senão não atravessam a fronteira do shadow DOM e o listener do
    componente nunca é acionado. Também evita o ElementClickIntercepted
    que o clique nativo do Selenium sofre quando o alvo está clipado por
    um painel com overflow:hidden. Dispara em todos os `elementos`
    passados (ex: <span> interno e o host) pra cobrir onde o componente
    escuta.
    """
    driver.execute_script(
        """
        const alvos = arguments[0];
        if (alvos[0]) alvos[0].scrollIntoView({block: 'center'});
        const opts = {bubbles: true, cancelable: true, composed: true, view: window};
        const tiposPointer = ['pointerover', 'pointerenter', 'pointerdown', 'pointerup'];
        const tiposMouse = ['mouseover', 'mousedown', 'mouseup', 'click'];
        for (const alvo of alvos) {
            if (!alvo) continue;
            for (const tipo of tiposPointer) {
                alvo.dispatchEvent(new PointerEvent(tipo, opts));
            }
            for (const tipo of tiposMouse) {
                alvo.dispatchEvent(new MouseEvent(tipo, opts));
            }
        }
        """,
        [e for e in elementos if e is not None],
    )


def com_contexto_correto(driver, localizador, timeout=30, intervalo=0.3):
    """
    O Protheus alterna entre renderizar a UI direto no documento
    principal e dentro de um <iframe> (wa-webview), dependendo da tela —
    e não dá pra saber de antemão qual é. Essa função tenta achar o
    elemento no documento principal e em cada wa-webview da página,
    deixando o driver posicionado no contexto onde ele foi encontrado.

    `localizador` é uma função (driver) -> elemento ou None/False.
    """
    fim = time.time() + timeout
    while time.time() < fim:
        driver.switch_to.default_content()
        el = localizador(driver)
        if el:
            return el

        for webview in driver.find_elements(By.CSS_SELECTOR, "wa-webview"):
            try:
                if not webview.shadow_root:
                    continue
                iframe = webview.shadow_root.find_element(By.CSS_SELECTOR, "iframe")
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe)
                el = localizador(driver)
            except WebDriverException:
                # iframe/elemento ficou inválido entre achar e trocar de
                # contexto (página re-renderizou nesse meio tempo) - não é
                # erro fatal, só significa que esse webview não serve mais;
                # tenta o próximo ou a próxima rodada do polling.
                continue
            if el:
                return el

        time.sleep(intervalo)

    raise TimeoutException("Elemento não encontrado em nenhum contexto (documento principal ou iframes).")
