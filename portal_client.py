# portal_client.py
import os, time, yaml
from datetime import datetime, timedelta
from urllib.parse import urljoin
from typing import Optional

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from dotenv import load_dotenv

load_dotenv()


class PortalClient:
    """
    Cliente do portal FieldMap Web, focado em:
      - login (com tela de selecionar veículo)
      - posicionar período da grade pelo mês do comprovante
      - localizar deslocamento pela data/hora (regra pedágio/estacionamento)
      - abrir a tela de despesas via HREF do menu da linha
      - preencher/anexar e confirmar o lançamento na grade
    """

    def __init__(self, config_path="config.yaml", headless=False):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

        o = Options()
        if headless:
            o.add_argument("-headless")

        self.driver = webdriver.Firefox(options=o)
        self.wait = WebDriverWait(self.driver, 20)
        try:
            self.driver.set_window_size(1440, 900)
        except Exception:
            pass

    # -------------- utilidades --------------
    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def _js_click(self, el):
        self.driver.execute_script("arguments[0].click();", el)

    def _scroll_center(self, el):
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", el
        )
        time.sleep(0.12)

    def _robust_click(self, el):
        try:
            ActionChains(self.driver).move_to_element(el).pause(0.05).perform()
            self._scroll_center(el)
            el.click()
        except Exception:
            self._js_click(el)
        time.sleep(0.12)

    def _set_input_value_js(self, el, value_str: str):
        self.driver.execute_script("""
            const el = arguments[0], v = arguments[1];
            el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        """, el, value_str)

    def _fixar_periodo_do_mes(self, data_ref: datetime):
        """Garante que o período da grade é exatamente o mês de data_ref e clica Pesquisar."""
        d, w = self.driver, self.wait
        if "/Deslocamento/Index" not in (d.current_url or ""):
            d.get(self.cfg["tabela"]["url"])

        # limites do mês
        ini = data_ref.replace(day=1)
        prox = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
        fim = prox - timedelta(days=1)

        def fdate(dt): return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"

        inp_ini = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input#dataInicialPesquisa")))
        inp_fim = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input#dataFinalPesquisa")))
        self._set_input_value_js(inp_ini, fdate(ini))
        self._set_input_value_js(inp_fim, fdate(fim))

        # botão Pesquisar
        btn = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-success.mb-2.ml-2, button.btn.btn-success")))
        self._scroll_center(btn)
        self._robust_click(btn)
        # espera a grade renderizar
        w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, self.cfg["tabela"]["row_selector"])))
        time.sleep(0.2)

    def _abs_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return urljoin(self.driver.current_url, href)
    
    def abrir_despesas_por_href(self, href: str) -> bool:
        if not href:
            return False
        self.driver.get(href)
        try:
            self.wait.until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
            return True
        except TimeoutException:
            return False


    # -------------- login / navegação inicial --------------
    def login(self):
        c = self.cfg["login"]
        d = self.driver
        w = self.wait

        d.get(c["url"])  # https://mobile.ncratleos.com/sb0121/
        w.until(EC.presence_of_element_located((By.CSS_SELECTOR, c["user_selector"]))).send_keys(
            os.getenv("PORTAL_USER")
        )
        w.until(EC.presence_of_element_located((By.CSS_SELECTOR, c["pass_selector"]))).send_keys(
            os.getenv("PORTAL_PASS")
        )
        self._robust_click(w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, c["submit_selector"]))))

        # Selecionar veículo -> apenas clica em salvar (se existir)
        try:
            d.get(self.cfg["veiculo"]["url"])
            self._robust_click(
                w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, self.cfg["veiculo"]["salvar_selector"])))
            )
        except Exception:
            pass

        # posiciona na tela de deslocamentos
        d.get(self.cfg["tabela"]["url"])

    # -------------- período (mês do comprovante) --------------
    def _set_periodo_mes_da_data(self, quando: datetime):
        inicio = quando.replace(day=1)
        prox = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
        fim = prox - timedelta(days=1)
        self.definir_periodo(inicio, fim)

    def definir_periodo(self, data_inicio: datetime, data_fim: datetime) -> bool:
        d, w = self.driver, self.wait
        fmt = lambda dt: f"{dt.day:02d}/{dt.month:02d}/{dt.year}"

        if "/Deslocamento/Index" not in (d.current_url or ""):
            d.get(self.cfg["tabela"]["url"])

        ini = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#dataInicialPesquisa")))
        fim = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#dataFinalPesquisa")))
        btn = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-success.mb-2.ml-2")))

        self._set_input_value_js(ini, fmt(data_inicio))
        self._set_input_value_js(fim, fmt(data_fim))
        self._robust_click(btn)

        # pequena confirmação: depois do reload, os campos devem manter as datas
        try:
            w.until(lambda drv: fmt(data_inicio) in drv.find_element(By.CSS_SELECTOR, "input#dataInicialPesquisa").get_attribute("value"))
            w.until(lambda drv: fmt(data_fim) in drv.find_element(By.CSS_SELECTOR, "input#dataFinalPesquisa").get_attribute("value"))
            return True
        except Exception:
            return False


    # -------------- parsing de células de data/hora --------------
    def _parse_cell_dt(self, txt: str) -> datetime:
        txt = " ".join([p.strip() for p in txt.splitlines() if p.strip()])
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(txt, fmt)
            except ValueError:
                continue
        raise ValueError("formato de data inesperado")

    # -------------- menu da linha -> HREF de 'Despesas' --------------
    def _open_menu_and_get_despesas(self, row_el, dt_ini):
        # botão do dropdown (algumas variações)
        btn = None
        for sel in (
            "button.btn.btn-success.dropdown-toggle",
            "button.btn.dropdown-toggle",
            "button.dropdown-toggle",
        ):
            try:
                btn = row_el.find_element(By.CSS_SELECTOR, sel)
                break
            except Exception:
                continue
        if not btn:
            return None

        self._scroll_center(row_el)
        try:
            self._robust_click(btn)
        except Exception:
            self._js_click(btn)

        # menu visível
        menu = None
        for sel in (".dropdown-menu.show", "ul.dropdown-menu"):
            try:
                menu = WebDriverWait(self.driver, 4).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, sel))
                )
                break
            except TimeoutException:
                continue
        if not menu:
            return None

        # escolher "Despesas", preferindo href com data coerente
        links = menu.find_elements(By.CSS_SELECTOR, "a.dropdown-item")
        enc_date = f"{dt_ini.month:02d}%2F{dt_ini.day:02d}%2F{dt_ini.year}"
        enc_hhmm = f"{dt_ini.hour:02d}%3A{dt_ini.minute:02d}"

        best = None
        fallback = None
        for a in links:
            href = (a.get_attribute("href") or "")
            text = (a.text or "").strip().lower()
            if "/Despesa/" in href:
                if "dataInicio=" in href and enc_date in href and enc_hhmm in href:
                    best = href
                    break
                if fallback is None:
                    fallback = href
            elif "despesa" in text and fallback is None:
                fallback = a.get_attribute("href") or ""
        return best or fallback

    # -------------- encontrar linha por data/hora (retorna HREF) --------------
    # def encontrar_linha_por_data_hora(self, alvo: datetime, tipo: str):
    #     """
    #     tipo: 'pedagio' ou 'estacionamento'
    #     Retorna: HREF da opção 'Despesas' da linha correspondente, ou None.
    #     """
    #     # sempre garante o mês do comprovante
    #     self._set_periodo_mes_da_data(alvo)

    #     d, w = self.driver, self.wait
    #     row_sel = self.cfg["tabela"]["row_selector"]

    #     w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, row_sel)))
    #     rows = d.find_elements(By.CSS_SELECTOR, row_sel)
    #     if not rows:
    #         return None

    #     parsed = []
    #     for r in rows:
    #         tds = r.find_elements(By.TAG_NAME, "td")
    #         if len(tds) < 3:
    #             continue
    #         try:
    #             ini = self._parse_cell_dt(tds[1].text)
    #             fim = self._parse_cell_dt(tds[2].text)
    #             parsed.append((r, ini, fim))
    #         except Exception:
    #             continue

    #     if tipo == "pedagio":
    #         delta = timedelta(minutes=10)
    #         for r, ini, fim in reversed(parsed):
    #             if ini - delta <= alvo <= fim + delta:
    #                 href = self._open_menu_and_get_despesas(r, ini)
    #                 if href:
    #                     return href

    #     if tipo == "estacionamento":
    #         for i in range(len(parsed) - 1):
    #             r, ini, fim = parsed[i]
    #             _, prox_ini, _ = parsed[i + 1]
    #             if fim <= alvo <= prox_ini:
    #                 href = self._open_menu_and_get_despesas(r, ini)
    #                 if href:
    #                     return href
    #         # último deslocamento do dia
    #         r, ini, fim = parsed[-1]
    #         if alvo >= fim:
    #             href = self._open_menu_and_get_despesas(r, ini)
    #             if href:
    #                 return href

    #     # fallback: mesma data
    #     for r, ini, _ in reversed(parsed):
    #         if ini.date() == alvo.date():
    #             href = self._open_menu_and_get_despesas(r, ini)
    #             if href:
    #                 return href
    #     return None
    
    # def encontrar_linha_por_data_hora(self, alvo: datetime, tipo: str) -> Optional[str]:
    #     # fixa o mês do comprovante
    #     inicio = alvo.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    #     prox = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
    #     fim = prox - timedelta(days=1)
    #     if not self.definir_periodo(inicio, fim):
    #         return None

    #     d, w = self.driver, self.wait
    #     row_sel = self.cfg["tabela"]["row_selector"]
    #     w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, row_sel)))
    #     rows = d.find_elements(By.CSS_SELECTOR, row_sel)
    #     if not rows:
    #         return None

    #     # parse data/hora da grade
    #     parsed = []
    #     for r in rows:
    #         tds = r.find_elements(By.TAG_NAME, "td")
    #         if len(tds) < 3: 
    #             continue
    #         try:
    #             dt_ini = self._parse_cell_dt(tds[1].text)
    #             dt_fim = self._parse_cell_dt(tds[2].text)
    #             parsed.append((r, dt_ini, dt_fim))
    #         except Exception:
    #             continue

    #     # regra de casamento
    #     def match(r_ini, r_fim) -> bool:
    #         if tipo == "pedagio":
    #             delta = timedelta(minutes=10)
    #             return (r_ini - delta) <= alvo <= (r_fim + delta)
    #         # estacionamento: entre fim da linha e início da próxima viagem
    #         return r_ini.date() == alvo.date()

    #     # varre de baixo para cima procurando a primeira que casa
    #     for idx in range(len(parsed) - 1, -1, -1):
    #         r, r_ini, r_fim = parsed[idx]
    #         if tipo == "estacionamento":
    #             prox_ini = parsed[idx + 1][1] if idx + 1 < len(parsed) else None
    #             ok = (r_fim <= alvo <= prox_ini) if prox_ini else (alvo >= r_fim)
    #         else:
    #             ok = match(r_ini, r_fim)
    #         if not ok:
    #             continue

    #         # abre dropdown e pega HREF da opção "Despesas" que contenha /Despesa/
    #         btn = None
    #         for sel in ("button.btn.btn-success.dropdown-toggle", "td:first-child .dropdown-toggle", ".dropdown-toggle"):
    #             got = r.find_elements(By.CSS_SELECTOR, sel)
    #             if got: btn = got[0]; break
    #         if not btn:
    #             continue

    #         self._scroll_center(r); self._robust_click(btn)

    #         # menu bootstrap aparece global
    #         try:
    #             menu = WebDriverWait(self.driver, 5).until(
    #                 EC.visibility_of_element_located((By.CSS_SELECTOR, ".dropdown-menu.show"))
    #             )
    #         except TimeoutException:
    #             continue

    #         links = menu.find_elements(By.CSS_SELECTOR, "a.dropdown-item[href*='/Despesa/']")
    #         if not links:
    #             continue
    #         return links[0].get_attribute("href") or None

    #     return None
    
    def encontrar_linha_por_data_hora(self, alvo: datetime, tipo: str) -> Optional[str]:
        """
        Retorna o HREF da opção 'Despesas' da linha **ancorada no mês do comprovante**.
        Nunca faz fallback para mês atual/última linha. Se não achar, devolve None.
        """
        d, w = self.driver, self.wait
        self._fixar_periodo_do_mes(alvo)

        row_sel = self.cfg["tabela"]["row_selector"]
        w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, row_sel)))
        rows = d.find_elements(By.CSS_SELECTOR, row_sel)
        if not rows:
            return None

        def parse_dt(cell_text: str) -> Optional[datetime]:
            txt = " ".join([p.strip() for p in cell_text.splitlines() if p.strip()])
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
                try: return datetime.strptime(txt, fmt)
                except ValueError: pass
            return None

        parsed = []
        for r in rows:
            tds = r.find_elements(By.TAG_NAME, "td")
            if len(tds) < 3: 
                continue
            ini = parse_dt(tds[1].text); fim = parse_dt(tds[2].text)
            if ini and fim:
                parsed.append((r, ini, fim))

        if not parsed:
            return None

        # Regras
        if tipo == "pedagio":
            margem = timedelta(minutes=10)
            cand = [ (r, ini) for (r, ini, fim) in parsed if (ini - margem) <= alvo <= (fim + margem) ]
        else:  # estacionamento
            cand = []
            for i in range(len(parsed)-1):
                r, ini, fim = parsed[i]
                prox_ini = parsed[i+1][1]
                if fim <= alvo <= prox_ini:
                    cand.append((r, ini))
            if not cand:
                # último deslocamento do dia: alvo após o fim
                r, ini, fim = parsed[-1]
                if alvo >= fim:
                    cand.append((r, ini))

        if not cand:
            # ainda assim: mesma data (sem forçar horário) — mantém no mesmo MÊS
            cand = [ (r, ini) for (r, ini, fim) in parsed if ini.date() == alvo.date() ]

        if not cand:
            return None

        # Abre o menu e devolve o href de Despesas (robusto a dropdown fora da linha)
        r, ini_ref = cand[-1]  # normalmente a mais próxima do alvo
        # botão do menu
        btn = None
        for sel in ("button.btn.btn-success.dropdown-toggle","td:first-child .dropdown-toggle",".dropdown-toggle"):
            found = r.find_elements(By.CSS_SELECTOR, sel)
            if found: btn = found[0]; break
        if not btn: 
            return None

        self._scroll_center(r)
        self._robust_click(btn)

        menu = None
        for sel in (".dropdown-menu.show", "ul.dropdown-menu"):
            try:
                menu = WebDriverWait(d, 4).until(EC.visibility_of_element_located((By.CSS_SELECTOR, sel)))
                break
            except TimeoutException:
                continue
        if not menu:
            return None

        enc_date = f"{ini_ref.month:02d}%2F{ini_ref.day:02d}%2F{ini_ref.year}"
        enc_hhmm = f"{ini_ref.hour:02d}%3A{ini_ref.minute:02d}"

        best, fallback = None, None
        for a in menu.find_elements(By.CSS_SELECTOR, "a.dropdown-item"):
            href = (a.get_attribute("href") or "")
            txt = (a.text or "").lower()
            if "/Despesa/" in href:
                if "dataInicio=" in href and enc_date in href and enc_hhmm in href:
                    best = href; break
                if fallback is None: fallback = href
            elif "despesa" in txt and fallback is None:
                fallback = href
        return best or fallback
    
    def abrir_pagina_despesas(self, href: str):
        """Abre diretamente o /Despesa/... pelo href capturado do menu (evita stale/click intercept)."""
        self.driver.get(href)
        # espera a grade aparecer
        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/Despesa/New'], a.btn.btn-success")))
        time.sleep(0.2)


    # -------------- encontrar "última" linha que tenha Despesas (retorna HREF) --------------
    def encontrar_ultima_linha(self):
        d, w = self.driver, self.wait
        row_sel = self.cfg["tabela"]["row_selector"]

        w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, row_sel)))

        for _ in range(3):  # algumas tentativas caso a grid recarregue
            try:
                rows = d.find_elements(By.CSS_SELECTOR, row_sel)
                if not rows:
                    return None

                for idx in range(len(rows) - 1, -1, -1):
                    rows = d.find_elements(By.CSS_SELECTOR, row_sel)
                    r = rows[idx]

                    # abre menu e pega HREF
                    tds = r.find_elements(By.TAG_NAME, "td")
                    dt_ini = None
                    if len(tds) >= 2:
                        try:
                            dt_ini = self._parse_cell_dt(tds[1].text)
                        except Exception:
                            pass

                    href = self._open_menu_and_get_despesas(r, dt_ini or datetime.now())
                    if href:
                        return href
            except Exception:
                time.sleep(0.3)

        return None

    # -------------- abrir despesas por URL --------------
    def abrir_despesas(self, href: str):
        self.driver.get(self._abs_url(href))

    # -------------- formulário de despesa --------------
    # def preencher_e_anexar(self, tipo: str, valor_centavos: int, arquivo: str) -> bool:
        # d, w = self.driver, self.wait

        # url = d.current_url or ""
        # if "/Despesa/" not in url:
        #     return False

        # # /Despesa/Index -> clica em +Nova Despesa
        # if "/Despesa/Index" in url:
        #     try:
        #         btn_sel = "a[href*='/Despesa/New'], a.center-block.btn.btn-success[href*='/Despesa/New']"
        #         novo = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_sel)))
        #     except TimeoutException:
        #         novo = w.until(
        #             EC.element_to_be_clickable(
        #                 (By.XPATH, "//a[contains(., '+ Nova Despesa') or contains(., 'Nova Despesa')]")
        #             )
        #         )
        #     self._scroll_center(novo)
        #     self._robust_click(novo)
        #     w.until(lambda drv: "/Despesa/New" in (drv.current_url or ""))

        # # select Tipo
        # tipo_select = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select#Tipo, select[name='Tipo']")))
        # sel = Select(tipo_select)
        # alvo_txt = "2 - Pedagio" if tipo == "pedagio" else "1 - Estacionamento"
        # try:
        #     sel.select_by_visible_text(alvo_txt)
        # except Exception:
        #     ok = False
        #     for opt in tipo_select.find_elements(By.TAG_NAME, "option"):
        #         if (opt.text or "").strip().lower().startswith(alvo_txt.lower()[:3]):
        #             self._robust_click(opt)
        #             ok = True
        #             break
        #     if not ok:
        #         return False

        # # valor
        # valor_input = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#Valor, input[name='Valor']")))
        # valor_input.clear()
        # valor_fmt = f"{valor_centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        # valor_input.send_keys(valor_fmt)

        # # anexo
        # file_input = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
        # file_input.send_keys(os.path.abspath(arquivo))

        # # salvar
        # salvar = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
        # self._scroll_center(salvar)
        # self._robust_click(salvar)

        # # confirmação: voltar pra /Despesa/Index (ou /Despesa/Editar com card do comprovante)
        # # 1) se carregou /Despesa/Editar e já mostra o card do comprovante, está ok
        # try:
        #     if "/Despesa/Editar" in (d.current_url or ""):
        #         # normalmente aparece uma seção "Comprovantes" com botão "Excluir Comprovante"
        #         if d.find_elements(By.XPATH, "//*[contains(., 'Comprovantes')]"):
        #             return True
        # except Exception:
        #     pass

        # # 2) caso padrão: /Despesa/Index com a tabela
        # try:
        #     WebDriverWait(d, 10).until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
        #     w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
        #     linhas = d.find_elements(By.CSS_SELECTOR, "table tbody tr")
        #     alvo_tipo = "2 - Pedagio" if tipo == "pedagio" else "1 - Estacionamento"
        #     for tr in linhas:
        #         tds = tr.find_elements(By.TAG_NAME, "td")
        #         if not tds:
        #             continue
        #         texto = " | ".join(td.text for td in tds)
        #         if (alvo_tipo in texto) and (valor_fmt in texto):
        #             return True
        # except Exception:
        #     pass

        # return False

    # def preencher_e_anexar(self, tipo: str, valor_centavos: int, arquivo: str, data_evento: Optional[datetime]=None) -> bool:
    #     d, w = self.driver, self.wait

    #     # só segue se estiver em /Despesa/Index
    #     if "/Despesa/Index" not in (d.current_url or ""):
    #         return False

    #     # valida cabeçalho x data_evento, se fornecida
    #     if data_evento is not None:
    #         try:
    #             header = d.find_element(By.CSS_SELECTOR, ".card .card-body").text
    #             # extrai 'Data Início:' e 'Data Término:' da página
    #             mi = re.search(r"Data In[ií]cio:\s*(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2}:\d{2})?", header)
    #             mf = re.search(r"Data T[ée]rmino:\s*(\d{2}/\d{2}/\d{4})\s*(\d{2}:\d{2}:\d{2})?", header)
    #             if mi and mf:
    #                 di = mi.group(1); df = mf.group(1)
    #                 # se o dia do evento não for o mesmo de início/fim, não lança
    #                 if (data_evento.strftime("%d/%m/%Y") != di) and (data_evento.strftime("%d/%m/%Y") != df):
    #                     return False
    #         except Exception:
    #             # se não conseguir validar, por segurança aborta
    #             return False

    #     # botão "+ Nova Despesa"
    #     try:
    #         novo = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='/Despesa/New']")))
    #     except TimeoutException:
    #         return False
    #     self._scroll_center(novo); self._robust_click(novo)
    #     w.until(lambda drv: "/Despesa/New" in (drv.current_url or ""))

    #     # select tipo
    #     tipo_select = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select#Tipo, select[name='Tipo']")))
    #     alvo_txt = "2 - Pedagio" if tipo == "pedagio" else "1 - Estacionamento"
    #     try:
    #         Select(tipo_select).select_by_visible_text(alvo_txt)
    #     except Exception:
    #         # fallback startswith
    #         ok = False
    #         for opt in tipo_select.find_elements(By.TAG_NAME, "option"):
    #             if (opt.text or "").strip().lower().startswith(alvo_txt.lower()[:3]):
    #                 self._robust_click(opt); ok = True; break
    #         if not ok:
    #             return False

    #     # valor
    #     valor_input = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#Valor, input[name='Valor']")))
    #     valor_input.clear()
    #     valor_fmt = f"{valor_centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    #     valor_input.send_keys(valor_fmt)

    #     # anexo
    #     file_input = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
    #     file_input.send_keys(os.path.abspath(arquivo))

    #     # salvar
    #     salvar = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    #     self._scroll_center(salvar); self._robust_click(salvar)

    #     # confirmação: precisa voltar para /Despesa/Index e aparecer a linha com tipo + valor
    #     try:
    #         w.until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
    #         w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
    #         linhas = d.find_elements(By.CSS_SELECTOR, "table tbody tr")
    #         alvo_tipo = "2 - Pedagio" if tipo == "pedagio" else "1 - Estacionamento"
    #         return any((alvo_tipo in tr.text) and (valor_fmt in tr.text) for tr in linhas)
    #     except Exception:
    #         return False

    # --- IMPORTS REQUERIDOS NO TOPO DO ARQUIVO (garanta que existam) ---
    # from typing import Optional   # (se você já não tiver)
    # from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, StaleElementReferenceException
    # import os, time, re
    # from selenium.webdriver.common.by import By
    # from selenium.webdriver.support.ui import WebDriverWait, Select
    # from selenium.webdriver.support import expected_conditions as EC

    def preencher_e_anexar(self, tipo: str, valor_centavos: int, arquivo: str, data_evento=None) -> bool:
        """
        Preenche o formulário de despesa (Pedágio / Estacionamento), anexa o arquivo e valida
        no retorno para a grade se a linha foi criada. Retorna True/False.
        """
        d, w = self.driver, self.wait

        # 0) Sanidade: precisamos estar em alguma URL de /Despesa/
        url = d.current_url or ""
        if "/Despesa/" not in url:
            return False

        # 1) Se estamos na lista (/Despesa/Index), clique em "+ Nova Despesa"
        if "/Despesa/Index" in url:
            try:
                btn_sel = "a[href*='/Despesa/New'], a.center-block.btn.btn-success[href*='/Despesa/New']"
                novo = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_sel)))
            except TimeoutException:
                # fallback por texto
                novo = w.until(EC.element_to_be_clickable((
                    By.XPATH, "//a[contains(., '+ Nova Despesa') or contains(., 'Nova Despesa')]"
                )))
            # rola e clica
            try:
                self._scroll_center(novo)
            except Exception:
                pass
            try:
                self._robust_click(novo)
            except Exception:
                self._js_click(novo)

            # aguarda navegar para /Despesa/New
            w.until(lambda drv: "/Despesa/New" in (drv.current_url or ""))

        # 2) Já no formulário (/Despesa/New): selecionar Tipo
        tipo_select = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select#Tipo, select[name='Tipo']")))
        sel = Select(tipo_select)
        alvo_txt = "2 - Pedagio" if tipo.lower() == "pedagio" else "1 - Estacionamento"
        try:
            sel.select_by_visible_text(alvo_txt)
        except Exception:
            ok_opt = False
            for opt in tipo_select.find_elements(By.TAG_NAME, "option"):
                if (opt.text or "").strip().lower().startswith(alvo_txt.lower()[:3]):
                    try:
                        self._robust_click(opt)
                    except Exception:
                        self._js_click(opt)
                    ok_opt = True
                    break
            if not ok_opt:
                return False

        # 3) Valor (formatação brasileira)
        valor_input = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#Valor, input[name='Valor']")))
        try:
            valor_input.clear()
        except Exception:
            pass
        valor_fmt = f"{valor_centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        valor_input.send_keys(valor_fmt)

        # 4) Anexo
        file_input = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
        file_input.send_keys(os.path.abspath(arquivo))

        # 5) Salvar
        salvar = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
        try:
            self._scroll_center(salvar)
        except Exception:
            pass
        try:
            self._robust_click(salvar)
        except Exception:
            self._js_click(salvar)

        # 6) Confirmação pós-submit
        #    a) primeiro tenta voltar para /Despesa/Index
        try:
            w.until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
        except TimeoutException:
            # se caiu na tela de erro do Save, falha
            if "/Despesa/Save" in (d.current_url or ""):
                return False
            # ainda assim continua para tentar checar a grade

        #    b) Validar na grade se há uma linha com tipo + valor
        try:
            # garante que a grade carregou
            w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table, table#datatable, table.dataTable")))
            # pequenas tentativas, pois a linha pode surgir com leve atraso
            for _ in range(8):  # ~4–6s
                linhas = d.find_elements(By.CSS_SELECTOR, "table tbody tr")
                alvo_tipo = "2 - Pedagio" if tipo.lower() == "pedagio" else "1 - Estacionamento"
                for tr in linhas:
                    tds = tr.find_elements(By.TAG_NAME, "td")
                    if not tds:
                        continue
                    txt = " | ".join(td.text for td in tds)
                    if (alvo_tipo in txt) and (valor_fmt in txt):
                        return True
                time.sleep(0.7)
        except Exception:
            pass

        return False
