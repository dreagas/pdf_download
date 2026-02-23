import os
import sys
import time
import subprocess
import logging
import unicodedata
import socket
import threading
import ctypes
import queue
import base64
import re
import winreg 

# Bibliotecas de Interface
import customtkinter as ctk
from tkinter import messagebox, filedialog

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

# ==============================================================================
#  CONSTANTES GLOBAIS
# ==============================================================================

VERSION = "2.8.0 (Strict Click + REAP Anual)"
CHROME_DEBUG_PORT = 9222
BASE_DIR = r"C:\chrome_reap"

if getattr(sys, 'frozen', False):
    APP_PATH = os.path.dirname(sys.executable)
else:
    APP_PATH = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(APP_PATH, "reap_debug_log.txt")
CHROME_PROFILE_PATH = BASE_DIR

# URLs
URL_HOME = "https://pesqbrasil-pescadorprofissional.mpa.gov.br/"
URLS_ABERTURA = [
    "https://pesqbrasil-pescadorprofissional.mpa.gov.br/manutencao",
    "https://cadunico.dataprev.gov.br/#/home",
    "https://login.esocial.gov.br/"
]

# ==============================================================================
#  CLASSE DE LOG
# ==============================================================================

class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record)

# ==============================================================================
#  JANELA DE PROGRESSO (POP-UP)
# ==============================================================================
class ProgressPopup(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Baixando Documentos")
        self.geometry("520x320")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.bloquear_fechamento) 
        
        self.lbl_nome = ctk.CTkLabel(self, text="Pescador: Identificando...", font=("Segoe UI", 14, "bold"), text_color="#60A5FA")
        self.lbl_nome.pack(pady=(15, 10))
        
        self.frame_status = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_status.pack(fill="x", padx=20, pady=5)
        
        self.lbl_carteira = ctk.CTkLabel(self.frame_status, text="Carteira de pescador... [Aguardando]", font=("Segoe UI", 12))
        self.lbl_carteira.pack(anchor="w", pady=5)
        
        self.lbl_certificado = ctk.CTkLabel(self.frame_status, text="Certificado de regularidade... [Aguardando]", font=("Segoe UI", 12))
        self.lbl_certificado.pack(anchor="w", pady=5)

        self.lbl_reap = ctk.CTkLabel(self.frame_status, text="REAP anual... [Aguardando]", font=("Segoe UI", 12))
        self.lbl_reap.pack(anchor="w", pady=5)
        
        self.btn_ok = ctk.CTkButton(self, text="Processando...", state="disabled", command=self.fechar_popup, fg_color="#475569")
        self.btn_ok.pack(pady=(20, 10))

    def bloquear_fechamento(self):
        pass 

    def fechar_popup(self):
        self.destroy()

    def atualizar_etapa(self, etapa, status_texto, cor="white"):
        if etapa == "nome":
            self.lbl_nome.configure(text=f"Pescador: {status_texto}", text_color=cor)
        elif etapa == "carteira":
            self.lbl_carteira.configure(text=f"Carteira de pescador... [{status_texto}]", text_color=cor)
        elif etapa == "certificado":
            self.lbl_certificado.configure(text=f"Certificado de regularidade... [{status_texto}]", text_color=cor)
        elif etapa == "reap":
            self.lbl_reap.configure(text=f"REAP anual... [{status_texto}]", text_color=cor)
        elif etapa == "fim":
            self.btn_ok.configure(state="normal", text="Concluir e Fechar", fg_color="#10B981", hover_color="#059669")
            self.protocol("WM_DELETE_WINDOW", self.fechar_popup) 

# ==============================================================================
#  LÓGICA DO NAVEGADOR (BACKEND)
# ==============================================================================

class AutomationLogic:
    def __init__(self, logger, stop_event):
        self.logger = logger
        self.stop_event = stop_event
        self.driver = None

    def check_stop(self):
        if self.stop_event.is_set():
            raise InterruptedError("Parada solicitada")

    def sanitize_filename(self, nome):
        nfkd_form = unicodedata.normalize('NFKD', nome)
        nome_sem_acento = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        limpo = re.sub(r'[\\/*?:"<>|]', "", nome_sem_acento).strip().upper()
        return limpo

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0

    def encontrar_executavel_chrome(self):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                path, _ = winreg.QueryValueEx(key, "")
                if path and os.path.exists(path): return path
        except: pass
        
        caminhos = [
            os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]
        for c in caminhos:
            if os.path.exists(c): 
                return c
        return None

    def garantir_chrome_aberto(self):
        if self.is_port_in_use(CHROME_DEBUG_PORT):
            self.logger.info("Chrome já está rodando. Conectando...", extra={'tags': 'SUCCESS'})
            return True

        self.logger.info("Abrindo Chrome...", extra={'tags': 'INFO'})
        chrome_exe = self.encontrar_executavel_chrome()
        
        if chrome_exe:
            if not os.path.exists(CHROME_PROFILE_PATH): 
                try: os.makedirs(CHROME_PROFILE_PATH, exist_ok=True)
                except: pass
            
            cmd = [
                chrome_exe, 
                f"--remote-debugging-port={CHROME_DEBUG_PORT}", 
                rf"--user-data-dir={CHROME_PROFILE_PATH}", 
                "--no-first-run", "--no-default-browser-check", "--start-maximized", 
                "--disable-popup-blocking",
                "--disable-features=PopupBlocking",
                URL_HOME
            ]
            
            subprocess.Popen(cmd)
            for i in range(20):
                time.sleep(0.5)
                if self.is_port_in_use(CHROME_DEBUG_PORT): 
                    return True
            self.logger.error("Tempo de espera esgotado para abertura da porta 9222.")
        else:
            messagebox.showerror("Erro", "Chrome não encontrado no sistema.")
        return False

    def conectar_selenium(self):
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{CHROME_DEBUG_PORT}")
        # FORÇA ABSOLUTA PARA PERMITIR POP-UPS PARA QUALQUER NOVO PC
        opts.add_experimental_option("prefs", {
            "profile.default_content_setting_values.popups": 1,
            "profile.default_content_setting_values.notifications": 1
        })
        try:
            self.driver = webdriver.Chrome(options=opts)
            self.logger.info("Selenium Conectado!", extra={'tags': 'SUCCESS'})
            return self.driver
        except Exception as e:
            self.logger.error(f"Erro conexão: {e}")
            return None

    def trazer_navegador_frente(self):
        if not self.driver: return
        try:
            self.driver.minimize_window()
            time.sleep(0.1)
            self.driver.maximize_window()
            self.driver.switch_to.window(self.driver.current_window_handle)
        except: pass

    # --- LÓGICA DE DOWNLOADS E SEGURANÇA ---

    def fechar_avisos_iniciais(self):
        """Espera a animação do aviso terminar para destruí-lo garantidamente via JS."""
        time.sleep(1.0) # Tempo vital para o botão renderizar e aparecer após reload
        try:
            script_nuclear = """
            var btns = document.querySelectorAll('button');
            for(var i=0; i<btns.length; i++) {
                if(btns[i].innerText.includes('Entendi') || btns[i].id === 'button_____rf') {
                    btns[i].click();
                }
            }
            """
            self.driver.execute_script(script_nuclear)
        except: pass 

    def voltar_para_janela_segura(self, target_handle=None):
        try:
            if target_handle:
                self.driver.switch_to.window(target_handle)
            else:
                raise WebDriverException("No target handle.")
        except WebDriverException:
            try:
                handles = self.driver.window_handles
                if handles:
                    self.driver.switch_to.window(handles[-1])
            except: pass

    def ir_para_home_seguro(self):
        """Busca super rápida pela home. CORTA o processo se não achar a aba logada."""
        url_alvo_limpa = URL_HOME.rstrip('/')
        
        try:
            # 1. Verifica se já tá na home na aba atual
            if self.driver.current_url.rstrip('/') == url_alvo_limpa:
                self.fechar_avisos_iniciais()
                return True
        except WebDriverException:
            self.voltar_para_janela_segura()

        # 2. Procura ativamente nas abas abertas por algo do PesqBrasil
        try:
            abas = self.driver.window_handles
            aba_pesqbrasil = None
            
            for aba in abas:
                try:
                    self.driver.switch_to.window(aba)
                    url_atual = self.driver.current_url
                    
                    if url_atual.rstrip('/') == url_alvo_limpa:
                        self.fechar_avisos_iniciais()
                        return True
                    elif "pesqbrasil-pescadorprofissional" in url_atual:
                        aba_pesqbrasil = aba
                except:
                    continue
                    
            # 3. Se achou uma aba do PesqBrasil que não era a Home, nós reaproveitamos ELA
            if aba_pesqbrasil:
                self.driver.switch_to.window(aba_pesqbrasil)
                self.driver.get(URL_HOME)
                time.sleep(1.5) # Aguarda redirecionamentos de login
                if "login" in self.driver.current_url.lower():
                    raise Exception("ABORT_LOGIN") # Usuário deslogado
                self.fechar_avisos_iniciais()
                return True
                
            # 4. Se não achou NENHUMA aba relacionada ao site.
            self.driver.execute_script(f"window.open('{URL_HOME}', '_blank');")
            time.sleep(1.5) # Aguarda redirecionamentos de login na nova guia
            self.driver.switch_to.window(self.driver.window_handles[-1])
            
            if "login" in self.driver.current_url.lower():
                raise Exception("ABORT_LOGIN") # Caiu no Login
                
            self.fechar_avisos_iniciais()
            return True

        except Exception as e:
            raise e # Repassa o erro para abortar

    def obter_nome_pescador(self):
        self.ir_para_home_seguro()
        try:
            btn_avatar = WebDriverWait(self.driver, 4).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(@id, 'avatar')]"))
            )
            span_nome = btn_avatar.find_element(By.XPATH, ".//span[contains(@class, 'text-medium')]")
            nome_cru = span_nome.text.strip()
            
            if not nome_cru: raise Exception("Nome vazio")
            return self.sanitize_filename(nome_cru)
        except Exception as e:
            # Se der timeout ou errar, significa que o layout está incorreto ou não logou.
            raise Exception("ABORT_ABA_INVALIDA")

    def encontrar_aba_pdf_blob(self, timeout=7):
        """Loop muito rápido (0.1s) que caça a aba do PDF assim que ela piscar."""
        start_time = time.time()
        try: main_handle = self.driver.current_window_handle
        except: main_handle = None
            
        while time.time() - start_time < timeout:
            try:
                handles = self.driver.window_handles
                if len(handles) > 1:
                    for handle in handles:
                        if handle == main_handle: continue 
                        self.driver.switch_to.window(handle)
                        if self.driver.current_url.startswith("blob:"):
                            return handle
            except: pass
            
            if main_handle:
                try: self.driver.switch_to.window(main_handle)
                except: pass
                
            time.sleep(0.1) 
            
        if main_handle:
            self.voltar_para_janela_segura(main_handle)
        return None

    def salvar_blob(self, blob_url, caminho_arquivo):
        script = """
        var uri = arguments[0];
        var callback = arguments[1];
        var xhr = new XMLHttpRequest();
        xhr.responseType = 'blob';
        xhr.onload = function() {
            var reader = new FileReader();
            reader.readAsDataURL(xhr.response);
            reader.onloadend = function() { callback(reader.result); }
        };
        xhr.open('GET', uri);
        xhr.send();
        """
        try:
            uri = self.driver.execute_async_script(script, blob_url)
            file_base64 = uri.split(',')[1]
            with open(caminho_arquivo, "wb") as f:
                f.write(base64.b64decode(file_base64))
            return True
        except Exception as e:
            self.logger.error(f"Erro JS Blob: {e}")
            return False

    def _normalizar_texto(self, texto):
        nfkd_form = unicodedata.normalize('NFKD', (texto or '').lower())
        return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

    def clicar_card_seguro(self, rotulo_esperado):
        """Clica no card da home pelo texto visível, evitando IDs dinâmicos/corrompidos."""
        WebDriverWait(self.driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".card-home-menu .card-main-text"))
        )

        rotulo_norm = self._normalizar_texto(rotulo_esperado)
        cards = self.driver.find_elements(By.CSS_SELECTOR, ".card-home-menu")
        card_escolhido = None

        for card in cards:
            try:
                if not card.is_displayed():
                    continue
                texto_el = card.find_element(By.CSS_SELECTOR, ".card-main-text")
                texto_card = self._normalizar_texto(texto_el.text)
                if rotulo_norm and rotulo_norm in texto_card:
                    card_escolhido = card
                    break
            except Exception:
                continue

        if not card_escolhido:
            raise NoSuchElementException(f"Card com rótulo '{rotulo_esperado}' não encontrado na home.")

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card_escolhido)
        time.sleep(0.2)
        self.driver.execute_script("arguments[0].click();", card_escolhido)

    def abrir_manutencao_seguro(self):
        """Garante contexto na página de manutenções e aborta em caso de redirecionamento para login."""
        self.ir_para_home_seguro()
        self.driver.get("https://pesqbrasil-pescadorprofissional.mpa.gov.br/manutencao")
        time.sleep(1.2)
        if "login" in self.driver.current_url.lower():
            raise Exception("ABORT_LOGIN")

        WebDriverWait(self.driver, 6).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'table-title') and contains(., 'Minhas manutenções')]"))
        )

    def coletar_reaps_enviados(self):
        """Retorna anos/referências com situação enviada e botão de PDF disponível."""
        script = """
        const linhas = Array.from(document.querySelectorAll('table tbody tr'));
        const saida = [];
        for (const tr of linhas) {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 8) continue;

            const ano = (tds[5].innerText || '').trim();
            const situacao = (tds[6].innerText || '').trim().toLowerCase();
            const btnPdf = tds[7].querySelector("button[aria-label='visualizar_2a_via']");

            if ((situacao.includes('enviado') || situacao.includes('enviada')) && btnPdf) {
                saida.push({
                    ano: ano,
                    btnId: btnPdf.id || ''
                });
            }
        }
        return saida;
        """
        itens = self.driver.execute_script(script) or []

        referencias = []
        for item in itens:
            ano = str(item.get("ano", "")).strip()
            btn_id = str(item.get("btnId", "")).strip()
            if ano and btn_id:
                referencias.append({"ano": ano, "btn_id": btn_id})
        return referencias

    def baixar_reaps_enviados(self, nome_pescador, pasta_destino, ui_callback):
        self.check_stop()
        ui_callback("reap", "Buscando anos enviados...", "#FACC15")

        try:
            self.abrir_manutencao_seguro()
            itens = self.coletar_reaps_enviados()
        except Exception as e:
            if "ABORT_" in str(e):
                ui_callback("reap", "Erro: Aba incorreta / Deslogado", "#EF4444")
                return "ABORT_ALL"
            self.logger.error(f"Erro ao abrir manutenção: {e}")
            ui_callback("reap", "Falha ao ler manutenção", "#EF4444")
            return False

        total = len(itens)
        if total == 0:
            ui_callback("reap", "Sem anos com situação Enviada", "#94A3B8")
            return True

        ui_callback("reap", f"{total} ano(s) com situação Enviada", "#60A5FA")

        baixados = 0
        for idx, item in enumerate(itens, start=1):
            self.check_stop()
            ano = item["ano"]
            btn_id = item["btn_id"]

            try:
                self.abrir_manutencao_seguro()
                main_window = self.driver.current_window_handle

                ui_callback("reap", f"Baixando {idx}/{total} (REAP {ano})...", "#FACC15")

                botao = WebDriverWait(self.driver, 6).until(
                    EC.element_to_be_clickable((By.ID, btn_id))
                )
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao)
                time.sleep(0.1)
                self.driver.execute_script("arguments[0].click();", botao)

                blob_handle = self.encontrar_aba_pdf_blob(timeout=10)
                if not blob_handle:
                    self.logger.warning(f"Timeout para REAP {ano}.")
                    continue

                self.driver.switch_to.window(blob_handle)
                url_blob = self.driver.current_url
                nome_arquivo = f"REAP{ano}_{nome_pescador}.pdf"
                caminho_final = os.path.join(pasta_destino, nome_arquivo)

                if self.salvar_blob(url_blob, caminho_final):
                    baixados += 1
                    self.logger.info(f"{nome_arquivo} salvo com sucesso.", extra={'tags': 'SUCCESS'})
                else:
                    self.logger.error(f"Falha ao salvar REAP {ano}.")

                self.driver.close()
                self.voltar_para_janela_segura(main_window)

            except Exception as e:
                self.logger.error(f"Erro no REAP {ano}: {e}")
                self.voltar_para_janela_segura()

        if baixados == total:
            ui_callback("reap", f"Salvo com sucesso ({baixados}/{total})", "#10B981")
            return True

        if baixados > 0:
            ui_callback("reap", f"Parcial ({baixados}/{total})", "#FACC15")
            return False

        ui_callback("reap", "Falha ao baixar REAP(s)", "#EF4444")
        return False

    def processar_item_unico(self, prefixo_arquivo, nome_pescador, pasta_destino, key_ui, ui_callback, rotulo_card):
        self.check_stop()
        
        MAX_TENTATIVAS = 3
        for tentativa in range(MAX_TENTATIVAS):
            try:
                self.ir_para_home_seguro()
                
                try: main_window = self.driver.current_window_handle
                except:
                    self.voltar_para_janela_segura()
                    main_window = self.driver.current_window_handle
                
                if tentativa > 0: ui_callback(key_ui, f"Tentando novamente ({tentativa+1})...", "#FACC15")
                else: ui_callback(key_ui, "Aguardando clique...", "#FACC15") 
                
                ui_callback(key_ui, "Aguardando gerador...", "#60A5FA")
                self.clicar_card_seguro(rotulo_card)
                
                # Check de notificação
                try:
                    aviso_erro = WebDriverWait(self.driver, 1.0).until(
                        EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'notification') and contains(@class, 'warning')]"))
                    )
                    msg_erro = aviso_erro.text.replace('\n', ' ')
                    self.logger.error(f"Bloqueio em {prefixo_arquivo}: {msg_erro}")
                    ui_callback(key_ui, "Pendência no site (Acesso Negado)", "#EF4444")
                    try: self.driver.execute_script("arguments[0].click();", aviso_erro.find_element(By.TAG_NAME, "button"))
                    except: pass
                    return False 
                except TimeoutException:
                    pass 
                    
                # Busca rápida pela aba
                blob_handle = self.encontrar_aba_pdf_blob(timeout=7)
                if blob_handle:
                    ui_callback(key_ui, "Baixando arquivo...", "#FACC15")
                    self.driver.switch_to.window(blob_handle)
                    url_atual = self.driver.current_url
                    
                    nome_arquivo = f"{prefixo_arquivo}_{nome_pescador}.pdf"
                    caminho_final = os.path.join(pasta_destino, nome_arquivo)
                    
                    if self.salvar_blob(url_atual, caminho_final):
                        ui_callback(key_ui, "Salvo com sucesso", "#10B981")
                        self.logger.info(f"{nome_arquivo} salvo com sucesso.", extra={'tags': 'SUCCESS'})
                        self.driver.close()
                        self.voltar_para_janela_segura(main_window)
                        return True
                    else:
                        ui_callback(key_ui, "Falha de gravação", "#EF4444")
                        self.driver.close()
                        self.voltar_para_janela_segura(main_window)
                else:
                    self.logger.warning(f"Timeout: PDF {prefixo_arquivo} não gerou a tempo.")
                    if tentativa == MAX_TENTATIVAS - 1:
                        ui_callback(key_ui, "Falha (Tempo esgotado)", "#EF4444")
                    self.voltar_para_janela_segura(main_window)
            
            except WebDriverException as we:
                self.logger.error(f"Crash da Aba ({tentativa + 1}): {we}")
                ui_callback(key_ui, "Recuperando conexão...", "#FACC15")
                time.sleep(0.5)
            except Exception as e:
                # SE CAIR NO LOGIN DURANTE A TENTATIVA, PARA TUDO!
                if "ABORT_" in str(e):
                    ui_callback(key_ui, "Erro: Aba incorreta / Deslogado", "#EF4444")
                    return "ABORT_ALL"
                    
                self.logger.error(f"Erro {prefixo_arquivo}: {e}")
                if tentativa == MAX_TENTATIVAS - 1:
                    ui_callback(key_ui, "Erro Desconhecido", "#EF4444")
                    return False
                else:
                    ui_callback(key_ui, "Recarregando...", "#FACC15")
                    time.sleep(0.5)

        return False

    def baixar_pacote_documentos(self, pasta_destino, ui_callback):
        self.check_stop()
        if not self.driver: return

        try:
            ui_callback("nome", "Buscando aba correta...", "#FACC15")
            
            # 1. VALIDAÇÃO RÍGIDA INICIAL
            try:
                nome_pescador = self.obter_nome_pescador()
            except Exception as e:
                erro_msg = "ERRO: Aba PesqBrasil não encontrada!"
                if "ABORT_LOGIN" in str(e):
                    erro_msg = "ERRO: Faça o login primeiro!"
                
                self.logger.error(f"Validação inicial falhou. Motivo: {e}")
                ui_callback("nome", erro_msg, "#EF4444")
                ui_callback("carteira", "Cancelado", "#EF4444")
                ui_callback("certificado", "Cancelado", "#EF4444")
                ui_callback("reap", "Cancelado", "#EF4444")
                ui_callback("fim", "", "")
                return # PARA TOTALMENTE AQUI E AGORA.

            ui_callback("nome", nome_pescador, "#60A5FA")

            # 2. DOWNLOAD DA CARTEIRA
            res_cart = self.processar_item_unico(
                prefixo_arquivo="Carteira", 
                nome_pescador=nome_pescador, 
                pasta_destino=pasta_destino, 
                key_ui="carteira", 
                ui_callback=ui_callback,
                rotulo_card="carteira de pescador"
            )
            
            # Se por acaso deslogar no meio do download, a função retorna ABORT_ALL
            if res_cart == "ABORT_ALL":
                ui_callback("certificado", "Cancelado", "#EF4444")
                ui_callback("reap", "Cancelado", "#EF4444")
                ui_callback("fim", "", "")
                return
            
            # 3. DOWNLOAD DO CERTIFICADO
            res_cert = self.processar_item_unico(
                prefixo_arquivo="Certificado_de_Regularidade", 
                nome_pescador=nome_pescador, 
                pasta_destino=pasta_destino, 
                key_ui="certificado", 
                ui_callback=ui_callback,
                rotulo_card="certificado de regularidade"
            )

            if res_cert == "ABORT_ALL":
                ui_callback("reap", "Cancelado", "#EF4444")
                ui_callback("fim", "", "")
                return

            # 4. DOWNLOAD DOS REAPs (anos enviados)
            self.baixar_reaps_enviados(
                nome_pescador=nome_pescador,
                pasta_destino=pasta_destino,
                ui_callback=ui_callback
            )
            
            ui_callback("fim", "", "")
            self.logger.info("Pacote de downloads finalizado!", extra={'tags': 'DESTAK'})

        except Exception as e:
            self.logger.error(f"Erro crítico no fluxo de download: {e}")
            ui_callback("fim", "", "")


# ==============================================================================
#  INTERFACE GRÁFICA (MINIMALISTA)
# ==============================================================================

class ReapApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")
        
        self.title(f"Extrator PesqBrasil - {VERSION}")
        self.geometry("450x450")
        
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.automation = None 
        self.popup_progresso = None

        self.setup_ui()
        self.setup_logging()
        self.after(500, self.boot_app)
        self.after(100, self.process_log_queue)

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        
        self.frame_top = ctk.CTkFrame(self, fg_color="#1E293B", corner_radius=0, height=80)
        self.frame_top.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(self.frame_top, text="PESQBRASIL PDF", font=("Segoe UI", 20, "bold"), text_color="#60A5FA").pack(pady=(15,0))
        self.lbl_status = ctk.CTkLabel(self.frame_top, text="Iniciando...", font=("Segoe UI", 12), text_color="gray")
        self.lbl_status.pack(pady=(0,10))

        self.frame_main = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_main.grid(row=1, column=0, padx=20, pady=20, sticky="ew")

        self.btn_reconnect = ctk.CTkButton(self.frame_main, text="RECONECTAR NAVEGADOR", command=self.boot_app,
                                           fg_color="#475569", height=40)
        self.btn_reconnect.pack(fill="x", pady=5)

        self.btn_pdf = ctk.CTkButton(self.frame_main, text="BAIXAR PACOTE DE DOCUMENTOS", command=self.action_download,
                                     fg_color="#D97706", hover_color="#B45309", height=50, 
                                     font=("Segoe UI", 14, "bold"), state="disabled")
        self.btn_pdf.pack(fill="x", pady=15)

        ctk.CTkLabel(self.frame_main, text="Selecione a pasta de destino para salvar os PDFs.", font=("Segoe UI", 11), text_color="gray").pack()

        self.log_box = ctk.CTkTextbox(self, font=("Consolas", 10), fg_color="#000000", text_color="#A1A1AA")
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.log_box.tag_config("INFO", foreground="#E2E8F0")
        self.log_box.tag_config("ERROR", foreground="#F87171")
        self.log_box.tag_config("SUCCESS", foreground="#38BDF8")
        self.log_box.tag_config("DESTAK", foreground="#818CF8")

    def setup_logging(self):
        self.logger = logging.getLogger("REAP_GUI")
        self.logger.setLevel(logging.INFO)
        handler = QueueHandler(self.log_queue)
        formatter = logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def process_log_queue(self):
        try:
            while True:
                record = self.log_queue.get_nowait()
                msg = self.logger.handlers[0].format(record)
                tag = "INFO"
                if hasattr(record, 'tags'): tag = record.tags
                elif record.levelno == logging.ERROR: tag = "ERROR"
                
                self.log_box.configure(state="normal")
                self.log_box.insert("end", msg + "\n", tag)
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty: pass
        self.after(100, self.process_log_queue)

    def boot_app(self):
        self.lbl_status.configure(text="Conectando ao Chrome...")
        threading.Thread(target=self._thread_boot, daemon=True).start()

    def _thread_boot(self):
        self.automation = AutomationLogic(self.logger, self.stop_event)
        if self.automation.garantir_chrome_aberto():
            if self.automation.conectar_selenium():
                self.after(0, lambda: self.btn_pdf.configure(state="normal"))
                self.after(0, lambda: self.lbl_status.configure(text="Conectado", text_color="#10B981"))
            else:
                self.logger.error("Falha ao conectar Selenium.")
        else:
            self.logger.error("Falha ao abrir Chrome.")

    def atualizar_ui_popup(self, etapa, texto, cor):
        if self.popup_progresso and self.popup_progresso.winfo_exists():
            self.after(0, lambda: self.popup_progresso.atualizar_etapa(etapa, texto, cor))

    def action_download(self):
        self.stop_event.clear()
        
        pasta_selecionada = filedialog.askdirectory(title="Selecione a pasta do Pescador")
        if not pasta_selecionada:
            self.logger.info("Operação cancelada: Nenhuma pasta selecionada.")
            return

        if self.automation:
            if self.popup_progresso is None or not self.popup_progresso.winfo_exists():
                self.popup_progresso = ProgressPopup(self)
            
            threading.Thread(target=self.automation.trazer_navegador_frente).start()
            
            threading.Thread(
                target=self.automation.baixar_pacote_documentos, 
                args=(pasta_selecionada, self.atualizar_ui_popup), 
                daemon=True
            ).start()

if __name__ == "__main__":
    app = ReapApp()
    app.mainloop()
