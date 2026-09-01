from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .log import log, ok, info


def coletar_chamados_com_feito(driver):
    """
    Varre a lista filtrada de chamados do GLPI (paginando) e aceita
    apenas os que têm EXATAMENTE um "Feito" na coluna de tarefas
    (índice 14). Regra de negócio: nota já conferida mas ainda pendente.
    """
    info("Coletando chamados com exatamente um 'Feito'...")
    chamados = []
    pagina = 1

    while True:
        log(f"\n  📄 Página {pagina}")

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.search-results tbody tr"))
        )

        linhas = driver.find_elements(By.CSS_SELECTOR, "table.search-results tbody tr")

        for linha in linhas:
            tds = linha.find_elements(By.TAG_NAME, "td")
            if len(tds) < 15:
                continue

            # coluna "Tarefas - Status" é o td de índice 14
            # usa innerHTML para evitar problemas com renderização do <br>
            status_html = tds[14].get_attribute("innerHTML")
            status_tarefas = tds[14].text.strip()

            link_el = linha.find_element(By.CSS_SELECTOR, "td a[href*='ticket.form.php']")
            titulo_chamado = link_el.text.strip()

            # aceita apenas chamados com exatamente um "Feito" e que sejam da IDEAL
            qtd_a_fazer = status_html.count("A fazer")
            eh_ideal = "IDEAL" in titulo_chamado.upper()

            if qtd_a_fazer == 2 and eh_ideal:
                href = link_el.get_attribute("href")
                ticket_id = href.split("id=")[-1]

                log(f"     ✔ #{ticket_id}  {titulo_chamado}  [{status_tarefas}]")
                chamados.append({
                    "id": ticket_id,
                    "titulo": titulo_chamado,
                    "url": href,
                    "status_tarefas": status_tarefas,
                })
            else:
                if qtd_a_fazer != 2:
                    motivo = "nenhum 'A fazer'" if qtd_a_fazer == 0 else f"{qtd_a_fazer}x 'A fazer'"
                else:
                    motivo = "não é da IDEAL"
                log(f"     · ignorado ({motivo}) - {titulo_chamado}")

        # verifica se o botão "Próximo" está habilitado
        try:
            li_proxima = driver.find_element(By.CSS_SELECTOR, "li.page-item:has(a.page-link-next)")
            if "disabled" in li_proxima.get_attribute("class"):
                info("Última página atingida.")
                break

            btn_proxima = li_proxima.find_element(By.CSS_SELECTOR, "a.page-link-next")
            href_proxima = btn_proxima.get_attribute("href")
            driver.get(href_proxima)
            pagina += 1
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.search-results tbody tr"))
            )
        except Exception:
            # Selenium não acha o botão "Próximo" quando a paginação acaba -
            # a exceção em si (NoSuchElementException) não tem nada de
            # informativo pra imprimir aqui, é só o sinal de "acabou".
            info("Sem mais páginas.")
            break

    ok(f"Total de chamados encontrados: {len(chamados)}")
    return chamados
