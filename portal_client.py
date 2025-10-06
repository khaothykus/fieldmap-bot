# portal_client.py
import os, time, yaml, platform
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlencode
from typing import Optional

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
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
import shutil

from dotenv import load_dotenv
load_dotenv()


class PortalClient:
    def __init__(self, config_path="config.yaml", headless: bool = False):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

        # --- Firefox Options ---
        o = Options()
        if headless:
            # Firefox aceita "-headless" (ou "--headless")
            o.add_argument("-headless")

        # Força comportamento "desktop" (ajuda muito no headless/ESR)
        o.set_preference(
            "general.useragent.override",
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        o.set_preference("intl.accept_languages", "pt-BR,pt,en-US,en")

        # --- Cria o driver (Windows x Linux/ARM) ---
        self.driver = self._make_driver(options=o)
        self.wait = WebDriverWait(self.driver, 20)

        # Tamanho de janela SEMPRE após criar o driver
        try:
            self.driver.set_window_size(1400, 950)
        except Exception:
            pass

    def _make_driver(self, options: Options):
        """
        Cria o Firefox WebDriver corretamente em:
        - Windows (geckodriver no PATH)
        - Raspberry Pi (aarch64) com firefox-esr e geckodriver instalados via apt
        - Respeita ENV: FIREFOX_BIN e GECKODRIVER
        """
        system = platform.system().lower()
        arch = platform.machine().lower()

        # ENV primeiro (para você controlar pelo service/.env)
        firefox_bin = (os.getenv("FIREFOX_BIN") or "").strip()
        gecko_bin = (os.getenv("GECKODRIVER") or "").strip()

        # Defaults amigáveis no Pi
        if not firefox_bin and (system == "linux" and arch in ("aarch64", "arm64", "armv7l", "armv8")):
            if os.path.exists("/usr/bin/firefox-esr"):
                firefox_bin = "/usr/bin/firefox-esr"
        if not gecko_bin:
            # tenta which; se não, fallback comum no Pi
            gecko_bin = shutil.which("geckodriver") or "/usr/local/bin/geckodriver"

        if firefox_bin and os.path.exists(firefox_bin):
            options.binary_location = firefox_bin

        service = None
        if gecko_bin and os.path.exists(gecko_bin):
            service = Service(executable_path=gecko_bin)

        if service is not None:
            return webdriver.Firefox(service=service, options=options)
        else:
            # Deixa o Selenium Manager resolver (x86/windows, etc.)
            return webdriver.Firefox(options=options)

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

    def _is_login_page(self) -> bool:
        d = self.driver
        try:
            sel_user = self.cfg["login"]["user_selector"]
            sel_pass = self.cfg["login"]["pass_selector"]
            d.find_element(By.CSS_SELECTOR, sel_user)
            d.find_element(By.CSS_SELECTOR, sel_pass)
            return True
        except Exception:
            return False
        
    def _env_creds(self):
        """Lê credenciais somente de PORTAL_USER/PORTAL_PASS."""
        import os
        user = os.getenv("PORTAL_USER", "").strip()
        pwd  = os.getenv("PORTAL_PASS", "").strip()
        return user, pwd

    # -------------- login / navegação inicial --------------
    def _do_login(self):
        """
        Faz login se (e somente se) detectar a tela de login.
        Usa selectors do config.yaml e respeita __RequestVerificationToken,
        pois o submit é feito pelo próprio botão do formulário.
        """
        d, w = self.driver, self.wait
        login_url = self.cfg["login"]["url"].rstrip("/") + "/"
        user_sel  = self.cfg["login"]["user_selector"]
        pass_sel  = self.cfg["login"]["pass_selector"]
        subm_sel  = self.cfg["login"]["submit_selector"]

        # Se não estamos na tela de login, tenta ir pra lá
        if "login" in (d.title or "").lower() or "/sb0121/" in (d.current_url or ""):
            pass
        else:
            d.get(login_url)

        # Verifica se realmente é a tela de login (UserName/Password existem)
        try:
            usr = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, user_sel)))
            pwd = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, pass_sel)))
        except TimeoutException:
            # Pode já estar logado; sai sem erro
            return

        user, passw = self._env_creds()
        if not user or not passw:
            raise RuntimeError("Credenciais ausentes (.env: PORTAL_USER/PORTAL_PASS).")

        # Preenche e envia
        usr.clear(); usr.send_keys(user)
        pwd.clear(); pwd.send_keys(passw)

        # Clica no botão de login (leva o token oculto junto)
        btn = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, subm_sel)))
        self._scroll_center(btn)
        self._robust_click(btn)

        # Aguarda sair da tela de login (título muda e/ou URL muda)
        w.until(lambda drv: "login" not in (drv.title or "").lower())

    def ensure_on_deslocamento_index(self):
        """
        Garante sessão e navega até a grade de deslocamentos.
        Resolve o seu caso em que a automação ficava presa no login.
        """
        d, w = self.driver, self.wait
        # Se título indica login, efetua login
        if "login - fieldmap web" in (d.title or "").lower():
            self._do_login()

        # Vai para a grade
        url = self.cfg["tabela"]["url"]
        if not d.current_url or "/Deslocamento/Index" not in d.current_url:
            d.get(url)

        # Se redirecionar para login (sessão expirada), faz login e volta
        if "login - fieldmap web" in (d.title or "").lower():
            self._do_login()
            d.get(url)

        # Aguarda a grade carregar: ou linhas, ou “Buscar registros”, ou “sem registros”
        row_sel = self.cfg["tabela"]["row_selector"]
        try:
            w.until(
                lambda drv: (
                    drv.find_elements(By.CSS_SELECTOR, row_sel) or
                    drv.find_elements(By.CSS_SELECTOR, "input[placeholder*='Buscar']") or
                    drv.page_source.lower().find("sem registros") >= 0
                )
            )
        except TimeoutException:
            # Salva HTML de depuração quando a grade não aparece
            try:
                with open("debug_grid_timeout.html", "w", encoding="utf-8") as f:
                    f.write(d.page_source)
            except Exception:
                pass
            raise TimeoutException("Grade não ficou pronta (sem linhas nem 'sem registros').")

    def _save_debug(self, tag: str):
        """Salva screenshot e HTML para diagnóstico."""
        safe = tag.replace(" ", "_")
        try:
            self.driver.save_screenshot(f"debug_{safe}.png")
        except Exception:
            pass
        try:
            with open(f"debug_{safe}.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception:
            pass

    def _ensure_tela_deslocamento(self):
        """Garante que estamos na página Deslocamento/Index."""
        url_ok = self.cfg["tabela"]["url"]
        if "/Deslocamento/Index" not in (self.driver.current_url or ""):
            self.driver.get(url_ok)
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form[action*='Deslocamento']"))
            )
        except TimeoutException:
            pass

    def _wait_grid_ready(self, row_sel: str, timeout: int = 12) -> bool:
        """
        Espera a grade ficar pronta:
        - há linhas visíveis (row_sel)
        - ou aparece alguma indicação de "sem registros".
        """
        w, d = self.wait, self.driver
        try:
            WebDriverWait(d, timeout).until(lambda drv: (
                len(drv.find_elements(By.CSS_SELECTOR, row_sel)) > 0 or
                len(drv.find_elements(By.CSS_SELECTOR, ".dataTables_empty, .empty, td[colspan]")) > 0 or
                "Sem registros" in (drv.page_source or "")
            ))
            return True
        except TimeoutException:
            self._save_debug("grid_timeout")
            return False

    # -------------- período (mês do comprovante) --------------
    def _fixar_periodo_do_mes(self, quando: datetime):
        """
        Define o período para o mês de `quando`.
        - Se os inputs existem, preenche e clica Buscar.
        - No Pi/headless (ou layouts sem input), recarrega a página com dataInicialPesquisa/dataFinalPesquisa via GET.
        """
        d, w = self.driver, self.wait
        self._ensure_tela_deslocamento()

        inicio = quando.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prox = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
        fim = (prox - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)

        def fdate(dt: datetime) -> str:
            return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"

        # Tenta inputs direto
        try:
            inp_ini = d.find_element(By.CSS_SELECTOR, "input#dataInicialPesquisa, input[name='dataInicialPesquisa']")
            inp_fim = d.find_element(By.CSS_SELECTOR, "input#dataFinalPesquisa, input[name='dataFinalPesquisa']")
            btn_busca = d.find_element(By.CSS_SELECTOR, "button.btn.btn-success.mb-2.ml-2, button[type='submit']")

            self._set_input_value_js(inp_ini, fdate(inicio))
            self._set_input_value_js(inp_fim, fdate(fim))
            self._scroll_center(btn_busca)
            self._robust_click(btn_busca)
            time.sleep(0.8)
            return
        except Exception:
            # Sem inputs? Usa GET com query string (força o mês correto)
            pass

        base = self.cfg["tabela"]["url"].rstrip("/")
        qs = urlencode({
            "dataInicialPesquisa": fdate(inicio),
            "dataFinalPesquisa": fdate(fim),
        })
        d.get(f"{base}?{qs}")

    # -------------- helpers URL/HREF e abrir telas --------------
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

    def abrir_pagina_despesas(self, href: str):
        """Abre diretamente o /Despesa/... pelo href capturado do menu (evita stale/click intercept)."""
        self.driver.get(href)
        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/Despesa/New'], a.btn.btn-success")))
        time.sleep(0.2)

    # -------------- menu da linha -> HREF de 'Despesas' --------------
    def _open_menu_and_get_despesas(self, row_el, dt_ini):
        # botão do dropdown (algumas variações)
        btn = None
        for sel in (
            "button.btn.btn-success.dropdown-toggle",
            "button.btn.dropdown-toggle",
            "button.dropdown-toggle"
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
    def encontrar_linha_por_data_hora(self, alvo: datetime, tipo: str) -> Optional[str]:
        """
        Retorna o HREF da opção 'Despesas' da linha **ancorada no mês do comprovante**.
        Nunca faz fallback para mês atual/última linha. Se não achar, devolve None.
        """
        # garante login + navegação correta
        self.ensure_on_deslocamento_index()

        # fixa o período ANTES de buscar linhas
        self._fixar_periodo_do_mes(alvo)

        row_sel = self.cfg["tabela"]["row_selector"]

        # espera robusta da grid
        if not self._wait_grid_ready(row_sel, timeout=15):
            raise TimeoutException("Grade não ficou pronta (sem linhas nem 'sem registros').")

        d, w = self.driver, self.wait
        w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, row_sel)))
        rows = d.find_elements(By.CSS_SELECTOR, row_sel)
        if not rows:
            return None

        def parse_dt(cell_text: str) -> Optional[datetime]:
            txt = " ".join([p.strip() for p in cell_text.splitlines() if p.strip()])
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
                try:
                    return datetime.strptime(txt, fmt)
                except ValueError:
                    pass
            return None

        parsed = []
        for r in rows:
            tds = r.find_elements(By.TAG_NAME, "td")
            if len(tds) < 3:
                continue
            ini = parse_dt(tds[1].text)
            fim = parse_dt(tds[2].text)
            if ini and fim:
                parsed.append((r, ini, fim))

        if not parsed:
            return None

        # Regras
        if tipo.lower() == "pedagio":
            margem = timedelta(minutes=10)
            cand = [(r, ini) for (r, ini, fim) in parsed if (ini - margem) <= alvo <= (fim + margem)]
        else:  # estacionamento
            cand = []
            for i in range(len(parsed) - 1):
                r, ini, fim = parsed[i]
                prox_ini = parsed[i + 1][1]
                if fim <= alvo <= prox_ini:
                    cand.append((r, ini))
            if not cand:
                # último deslocamento do dia: alvo após o fim
                r, ini, fim = parsed[-1]
                if alvo >= fim:
                    cand.append((r, ini))

        if not cand:
            # ainda assim: mesma data (sem forçar horário) — mantém no mesmo MÊS
            cand = [(r, ini) for (r, ini, fim) in parsed if ini.date() == alvo.date()]

        if not cand:
            return None

        # Abre o menu e devolve o href de Despesas (robusto a dropdown fora da linha)
        r, ini_ref = cand[-1]  # normalmente a mais próxima do alvo
        # botão do menu
        btn = None
        for sel in ("button.btn.btn-success.dropdown-toggle", "td:first-child .dropdown-toggle", ".dropdown-toggle"):
            found = r.find_elements(By.CSS_SELECTOR, sel)
            if found:
                btn = found[0]
                break
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
                    best = href
                    break
                if fallback is None:
                    fallback = href
            elif "despesa" in txt and fallback is None:
                fallback = href
        return best or fallback

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

    # -------------- parser auxiliar --------------
    def _parse_cell_dt(self, txt: str) -> datetime:
        txt = " ".join([p.strip() for p in txt.splitlines() if p.strip()])
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(txt, fmt)
            except ValueError:
                continue
        raise ValueError("formato de data inesperado")

    # -------------- formulário de despesa --------------
    def preencher_e_anexar(self, tipo: str, valor_centavos: int, arquivo: str, data_evento: Optional[datetime] = None) -> bool:
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
