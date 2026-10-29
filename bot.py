import os
import re
import json
import time
import html
import socket
import imaplib
import email
import logging
import asyncio
from threading import Thread
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from email.header import decode_header
from html.parser import HTMLParser

from flask import Flask
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================
# LOGS E CONFIGURAÇÕES DE AMBIENTE
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM_MASTER = os.environ.get("TOKEN_TELEGRAM", "SEU_TOKEN_AQUI").strip()
ADMIN_ID = os.environ.get("ADMIN_ID", "7496198484").strip()

RAW_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
RAW_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
SUPABASE_URL = RAW_SUPABASE_URL.rstrip("/").strip("'").strip('"')
SUPABASE_KEY = RAW_SUPABASE_KEY.strip("'").strip('"')

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("✅ Supabase conectado para persistência de dados!")
    except Exception as e:
        logging.error(f"⚠️ Erro ao inicializar Supabase: {e}")

CLIENTES_FIXOS_PADRAO = {
    str(ADMIN_ID): {
        "emails_permitidos": {"*": "2030-12-31"},
        "permitir_sensivel": True,
        "criado_por": str(ADMIN_ID)
    }
}

APLICACOES_BOTS = {}

# ==========================================
# FUNÇÃO DE GERAR TECLADO DINÂMICO POR NÍVEL
# ==========================================
def obter_teclado_usuario(user_id: str) -> ReplyKeyboardMarkup:
    user_id_str = str(user_id).strip()
    
    if eh_admin_master(user_id_str):
        botoes = [
            [KeyboardButton("👑 Painel Master"), KeyboardButton("💼 Revendedores")],
            [KeyboardButton("🤖 Bots White-Label"), KeyboardButton("⚠️ Ver Erros")],
            [KeyboardButton("📖 Como Usar"), KeyboardButton("🔑 Senhas de App")],
            [KeyboardButton("⚠️ Reportar Erro / Print")]
        ]
    elif eh_revendedor(user_id_str):
        botoes = [
            [KeyboardButton("💼 Painel Revenda"), KeyboardButton("📋 Meus Clientes")],
            [KeyboardButton("📧 Minhas Contas"), KeyboardButton("⚠️ Ver Erros")],
            [KeyboardButton("📖 Como Usar"), KeyboardButton("🔑 Senhas de App")],
            [KeyboardButton("⚠️ Reportar Erro / Print")]
        ]
    else:
        botoes = [
            [KeyboardButton("📖 Como Usar")],
            [KeyboardButton("⚠️ Reportar Erro / Print")]
        ]
        
    return ReplyKeyboardMarkup(botoes, resize_keyboard=True, is_persistent=True)

# ==========================================
# PARSER ESTRUTURADO DE HTML
# ==========================================
class HTMLExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.in_anchor = False
        self.current_href = ""
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            attrs_dict = {k.lower(): v for k, v in attrs if k and v}
            if 'href' in attrs_dict:
                self.in_anchor = True
                self.current_href = attrs_dict['href']
                self.current_text = []

    def handle_data(self, data):
        if self.in_anchor:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.in_anchor:
            text = " ".join("".join(self.current_text).split())
            self.anchors.append((self.current_href, text))
            self.in_anchor = False
            self.current_href = ""
            self.current_text = []

def extrair_anchors_html(corpo_html: str):
    parser = HTMLExtractor()
    try:
        parser.feed(corpo_html)
    except Exception:
        pass
    return parser.anchors

# ==========================================
# FUNÇÕES AUXILIARES DE TRATAMENTO DE DATAS E TEXTO
# ==========================================
def converter_para_date(data_str: str):
    if not data_str:
        return None
    data_limpa = str(data_str).split("T")[0].strip()
    formatos = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]
    for fmt in formatos:
        try:
            return datetime.strptime(data_limpa, fmt).date()
        except ValueError:
            pass
    return None

def formatar_data_exibicao_br(data_str: str) -> str:
    dt_obj = converter_para_date(data_str)
    if dt_obj:
        return dt_obj.strftime("%d-%m-%Y")
    return str(data_str)

# ==========================================
# SERVIDOR FLASK (HEALTH CHECK DO RENDER)
# ==========================================
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos Multi-Revendas White-Label Online"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

# ==========================================
# BANCO DE DADOS E PERSISTÊNCIA
# ==========================================
def carregar_json(caminho: str) -> dict:
    if caminho == "clientes.json":
        return carregar_clientes_db()
    elif caminho == "contas.json":
        return carregar_contas_db()
    elif caminho == "revendedores.json":
        return carregar_revendedores_db()
    elif caminho == "config.json":
        return carregar_config_db()
    elif caminho == "bots.json":
        return carregar_bots_db()
    elif caminho == "erros.json":
        return carregar_erros_db()

    dados = {}
    try:
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
    except Exception as e:
        logging.error(f"Erro ao carregar {caminho}: {e}")
    return dados

def salvar_json(caminho: str, dados: dict):
    if caminho == "clientes.json":
        salvar_clientes_db(dados)
        return
    elif caminho == "contas.json":
        salvar_contas_db(dados)
        return
    elif caminho == "revendedores.json":
        salvar_revendedores_db(dados)
        return
    elif caminho == "config.json":
        salvar_config_db(dados)
        return
    elif caminho == "bots.json":
        salvar_bots_db(dados)
        return
    elif caminho == "erros.json":
        salvar_erros_db(dados)
        return

    try:
        caminho_tmp = f"{caminho}.tmp"
        with open(caminho_tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace(caminho_tmp, caminho)
    except Exception as e:
        logging.error(f"Erro ao salvar {caminho}: {e}")

# --- ARMAZENAMENTO DE ERROS REPORTADOS ---
def carregar_erros_db() -> list:
    dados = []
    if supabase:
        try:
            res = supabase.table("erros_bot").select("*").order("created_at", desc=True).limit(20).execute()
            if res.data:
                return res.data
        except Exception:
            pass
    if os.path.exists("erros.json"):
        try:
            with open("erros.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return dados

def salvar_erros_db(dados: list):
    try:
        with open("erros.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("erros.json.tmp", "erros.json")
    except Exception:
        pass

def salvar_novo_erro(user_id: str, mensagem: str, tem_foto: bool = False):
    lista_erros = carregar_erros_db()
    novo_item = {
        "user_id": str(user_id),
        "mensagem": mensagem,
        "tem_foto": tem_foto,
        "created_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }
    lista_erros.insert(0, novo_item)
    lista_erros = lista_erros[:50]
    salvar_erros_db(lista_erros)

    if supabase:
        try:
            supabase.table("erros_bot").insert(novo_item).execute()
        except Exception:
            pass

# --- GERENCIAMENTO DE BOTS WHITE-LABEL ---
def carregar_bots_db() -> dict:
    dados = {}
    if supabase:
        try:
            res = supabase.table("bots_revenda").select("*").execute()
            if res.data:
                for row in res.data:
                    tok = str(row.get("token")).strip()
                    dados[tok] = {"dono_id": str(row.get("dono_id")).strip()}
        except Exception:
            pass
    if not dados and os.path.exists("bots.json"):
        try:
            with open("bots.json", "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass
    return dados

def salvar_bots_db(dados: dict):
    try:
        with open("bots.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("bots.json.tmp", "bots.json")
    except Exception:
        pass

    if supabase:
        try:
            for tok, info in dados.items():
                supabase.table("bots_revenda").upsert({"token": tok, "dono_id": str(info.get("dono_id"))}).execute()
        except Exception:
            pass

# --- CONFIGURAÇÕES GERAIS ---
def carregar_config_db() -> dict:
    padrao = {"notificar_clientes": True}
    if supabase:
        try:
            res = supabase.table("config_bot").select("*").execute()
            if res.data:
                for row in res.data:
                    padrao[row.get("chave")] = row.get("valor")
                return padrao
        except Exception:
            pass
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return padrao

def salvar_config_db(dados: dict):
    try:
        with open("config.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("config.json.tmp", "config.json")
    except Exception:
        pass

    if supabase:
        try:
            for k, v in dados.items():
                supabase.table("config_bot").upsert({"chave": k, "valor": v}).execute()
        except Exception:
            pass

# --- GERENCIAMENTO DE REVENDEDORES ---
def carregar_revendedores_db() -> dict:
    dados = {}
    if supabase:
        try:
            res = supabase.table("revendedores_bot").select("*").execute()
            if res.data:
                for row in res.data:
                    t_id = str(row.get("telegram_id")).strip()
                    dados[t_id] = {
                        "cadastrado_em": row.get("cadastrado_em", ""),
                        "expira_em": row.get("expira_em", "2030-12-31"),
                        "cadastrado_por": str(row.get("cadastrado_por", ADMIN_ID)),
                        "alerta_expiracao_enviado": bool(row.get("alerta_expiracao_enviado", False))
                    }
        except Exception as e:
            logging.error(f"Erro ao carregar revendedores do Supabase: {e}")

    if not dados and os.path.exists("revendedores.json"):
        try:
            with open("revendedores.json", "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            logging.error(f"Erro ao ler revendedores.json local: {e}")

    return dados

def salvar_revendedores_db(dados: dict):
    try:
        with open("revendedores.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("revendedores.json.tmp", "revendedores.json")
    except Exception as e:
        logging.error(f"Erro ao salvar revendedores.json local: {e}")

    if supabase:
        try:
            for t_id, info in dados.items():
                payload = {
                    "telegram_id": str(t_id).strip(),
                    "cadastrado_em": info.get("cadastrado_em", datetime.now().strftime("%Y-%m-%d")),
                    "expira_em": info.get("expira_em", "2030-12-31"),
                    "cadastrado_por": str(info.get("cadastrado_por", ADMIN_ID)),
                    "alerta_expiracao_enviado": bool(info.get("alerta_expiracao_enviado", False))
                }
                supabase.table("revendedores_bot").upsert(payload).execute()
        except Exception as e:
            logging.error(f"Erro ao salvar revendedores no Supabase: {e}")

# --- GERENCIAMENTO DE CONTAS DE STREAMING ---
def carregar_contas_db() -> dict:
    dados = {}
    if supabase:
        try:
            res = supabase.table("contas_bot").select("*").execute()
            if res.data:
                for row in res.data:
                    email_c = str(row.get("email")).strip().lower()
                    dados[email_c] = {
                        "senha": row.get("senha"),
                        "host_imap": row.get("host_imap", "imap.gmail.com"),
                        "porta": row.get("porta", 993),
                        "email_login": row.get("email_login", email_c),
                        "dono_id": str(row.get("dono_id", ADMIN_ID))
                    }
        except Exception as e:
            logging.error(f"Erro ao carregar contas do Supabase: {e}")

    if not dados and os.path.exists("contas.json"):
        try:
            with open("contas.json", "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            logging.error(f"Erro ao ler contas.json local: {e}")

    return dados

def salvar_contas_db(dados: dict):
    try:
        with open("contas.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("contas.json.tmp", "contas.json")
    except Exception as e:
        logging.error(f"Erro ao salvar contas.json local: {e}")

    if supabase:
        try:
            for email_c, info in dados.items():
                payload = {
                    "email": email_c.strip().lower(),
                    "senha": info.get("senha"),
                    "host_imap": info.get("host_imap", "imap.gmail.com"),
                    "porta": info.get("porta", 993),
                    "email_login": info.get("email_login", email_c),
                    "dono_id": str(info.get("dono_id", ADMIN_ID))
                }
                supabase.table("contas_bot").upsert(payload).execute()
        except Exception as e:
            logging.error(f"Erro ao salvar contas no Supabase: {e}")

# --- GERENCIAMENTO DE CLIENTES ---
def carregar_clientes_db() -> dict:
    dados = {}
    if supabase:
        try:
            res = supabase.table("clientes_bot").select("*").execute()
            if res.data:
                for row in res.data:
                    t_id = str(row.get("telegram_id")).strip()
                    emails = row.get("emails_permitidos") or {}
                    permitir = bool(row.get("permitir_sensivel", False))
                    criado = str(row.get("criado_por", ADMIN_ID))
                    alertas = row.get("alertas_enviados") or {}
                    dados[t_id] = {
                        "emails_permitidos": emails,
                        "permitir_sensivel": permitir,
                        "criado_por": criado,
                        "alertas_enviados": alertas
                    }
        except Exception as e:
            logging.error(f"Erro ao carregar clientes do Supabase: {e}")

    if not dados and os.path.exists("clientes.json"):
        try:
            with open("clientes.json", "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            logging.error(f"Erro ao ler clientes.json local: {e}")

    for id_fixo, info_fixa in CLIENTES_FIXOS_PADRAO.items():
        if id_fixo not in dados:
            dados[id_fixo] = {
                "emails_permitidos": dict(info_fixa.get("emails_permitidos", {})),
                "permitir_sensivel": info_fixa.get("permitir_sensivel", True),
                "criado_por": info_fixa.get("criado_por", str(ADMIN_ID)),
                "alertas_enviados": {}
            }

    return dados

def salvar_clientes_db(dados: dict):
    try:
        with open("clientes.json.tmp", "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace("clientes.json.tmp", "clientes.json")
    except Exception as e:
        logging.error(f"Erro ao salvar clientes.json local: {e}")

    if supabase:
        try:
            for telegram_id, info in dados.items():
                payload = {
                    "telegram_id": str(telegram_id).strip(),
                    "emails_permitidos": info.get("emails_permitidos", {}),
                    "permitir_sensivel": bool(info.get("permitir_sensivel", False)),
                    "criado_por": str(info.get("criado_por", ADMIN_ID)),
                    "alertas_enviados": info.get("alertas_enviados", {}),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                supabase.table("clientes_bot").upsert(payload).execute()
        except Exception as e:
            logging.error(f"Erro ao salvar clientes no Supabase: {e}")

def eh_admin_master(user_id: str) -> bool:
    return str(user_id).strip() == str(ADMIN_ID).strip()

def eh_revendedor(user_id: str) -> bool:
    user_id_str = str(user_id).strip()
    if eh_admin_master(user_id_str):
        return True
    revendedores = carregar_json("revendedores.json")
    if user_id_str in revendedores:
        exp_date = converter_para_date(revendedores[user_id_str].get("expira_em"))
        if exp_date and exp_date >= datetime.now().date():
            return True
    return False

# ==========================================
# MOTOR DE TRATAMENTO E EXTRAÇÃO IMAP
# ==========================================
def normalizar_email_gmail(email_str: str) -> str:
    if not email_str:
        return ""
    partes = email_str.strip().lower().split('@')
    if len(partes) != 2:
        return email_str.strip().lower()
    usuario, dominio = partes
    if dominio in ["gmail.com", "googlemail.com"]:
        usuario = usuario.split('+')[0]
        usuario = usuario.replace(".", "")
        return f"{usuario}@{dominio}"
    return email_str.strip().lower()

def decodificar_assunto(assunto_raw: str) -> str:
    if not assunto_raw:
        return ""
    try:
        partes = decode_header(assunto_raw)
        texto_decodificado = ""
        for conteudo, charset in partes:
            if isinstance(conteudo, bytes):
                texto_decodificado += conteudo.decode(charset or 'utf-8', errors='ignore')
            else:
                texto_decodificado += str(conteudo)
        return texto_decodificado
    except Exception:
        return assunto_raw

def tratar_quopri_e_html(payload_bytes: bytes, charset: str = 'utf-8') -> str:
    if not payload_bytes:
        return ""
    try:
        texto = payload_bytes.decode(charset or 'utf-8', errors='ignore')
    except Exception:
        texto = payload_bytes.decode('utf-8', errors='ignore')
    return html.unescape(texto)

def extrair_apenas_texto_visivel(html_str: str) -> str:
    if not html_str:
        return ""
    texto = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r'<[^>]+>', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def limpar_url_link(link: str) -> str:
    if not link:
        return ""
    link_limpo = html.unescape(str(link))
    link_limpo = re.sub(r'[\r\n\t\s]+', '', link_limpo)
    return link_limpo.strip()

def extrair_link_acao_netflix(corpo_html: str):
    if not corpo_html:
        return None, None

    bloqueadas = ["unsubscribe", "help.netflix.com", "privacy", "terms", "facebook", "twitter", "instagram"]
    anchors = extrair_anchors_html(corpo_html)

    for href, text in anchors:
        l_limpo = limpar_url_link(href)
        l_lower = l_limpo.lower()
        if "netflix.com" in l_lower and not any(x in l_lower for x in bloqueadas):
            txt_lower = text.lower()
            if any(p in txt_lower for p in ["sim, fui eu", "fui eu", "atualizar residência", "confirmar residência", "atualizar residencia", "confirmar residencia"]):
                return l_limpo, "residencia"
            if any(p in txt_lower for p in ["receber código", "receber codigo", "solicitar código", "solicitar codigo", "obter código", "obter codigo", "estou viajando", "código temporário", "codigo temporario"]):
                return l_limpo, "viagem"
            if any(p in txt_lower for p in ["redefinir senha", "redefinir sua senha", "alterar senha", "reset password"]):
                return l_limpo, "redefinicao"

    for href, text in anchors:
        l_limpo = limpar_url_link(href)
        l_lower = l_limpo.lower()
        if "netflix.com" in l_lower and not any(x in l_lower for x in bloqueadas):
            if "update-primary-location" in l_lower or "household" in l_lower:
                return l_limpo, "residencia"
            if "travel" in l_lower or "accountaccess" in l_lower or "verify" in l_lower:
                return l_limpo, "viagem"
            if "password" in l_lower and "reset" in l_lower:
                return l_limpo, "redefinicao"

    return None, None

def extrair_link_hbomax_html(corpo_html: str) -> str:
    if not corpo_html:
        return ""
    palavras_hbomax = ["restabelecer", "redefinir", "reset", "nova senha", "alterar senha", "criar senha"]
    anchors = extrair_anchors_html(corpo_html)
    for href, text in anchors:
        if any(p in text.lower() for p in palavras_hbomax):
            return limpar_url_link(href)

    for href, text in anchors:
        link_lower = href.lower()
        if ("hbomax.com" in link_lower or "max.com" in link_lower) and any(p in link_lower for p in ["reset", "token", "password", "auth", "credential"]):
            if not any(x in link_lower for x in ["unsubscribe", "help", "privacy", "terms"]):
                return limpar_url_link(href)
    return ""

def extrair_link_redefinicao_html(corpo_html: str) -> str:
    if not corpo_html:
        return ""
    anchors = extrair_anchors_html(corpo_html)
    for href, text in anchors:
        link_lower = href.lower()
        if any(p in link_lower for p in ["recuperar", "redefinir", "reset", "password", "alterar", "senha", "token", "confirm"]):
            if not any(x in link_lower for x in ["unsubscribe", "help", "privacy", "terms", "facebook", "twitter"]):
                return limpar_url_link(href)
    return ""

def extrair_url_pura_href(corpo_html: str, termo_busca: str = "http") -> str:
    if not corpo_html:
        return ""
    anchors = extrair_anchors_html(corpo_html)
    for href, text in anchors:
        if termo_busca in href.lower() and not any(x in href.lower() for x in ["unsubscribe", "help", "privacy", "terms", "manageaccountaccess"]):
            return limpar_url_link(href)
    return ""

def extrair_codigo_imap_wrapper(dados_conta: dict, pode_acessar_sensivel: bool = False) -> str:
    email_usuario = dados_conta.get("email_usuario")
    if "yahoo" in str(email_usuario):
        host = dados_conta.get("host_imap", "imap.mail.yahoo.com")
    elif "outlook" in str(email_usuario) or "hotmail" in str(email_usuario):
        host = dados_conta.get("host_imap", "outlook.office365.com")
    else:
        host = dados_conta.get("host_imap", "imap.gmail.com")
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap") or dados_conta.get("senha") or dados_conta.get("password") or dados_conta.get("pass")

    mail = None
    try:
        socket.setdefaulttimeout(10)
        mail = imaplib.IMAP4_SSL(host, porta)
        mail.login(email_usuario, senha)
        mail.select("INBOX", readonly=True)

        status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages[0]:
            mail.close()
            mail.logout()
            return "⚠️ Nenhum e-mail recente foi localizado nesta caixa."

        id_list = messages[0].split()
        ultimos_ids = id_list[-5:]
        ultimos_ids.reverse()

        agora = datetime.now(timezone.utc)

        for msg_id in ultimos_ids:
            _, data_body = mail.fetch(msg_id, "(RFC822)")
            if not data_body or not data_body[0]:
                continue

            msg = email.message_from_bytes(data_body[0][1])
            data_email_header = msg.get("Date")
            if not data_email_header:
                continue

            try:
                data_email = parsedate_to_datetime(data_email_header)
                if data_email.tzinfo is None:
                    data_email = data_email.replace(tzinfo=timezone.utc)
                diferenca_segundos = (agora - data_email).total_seconds()
            except Exception:
                diferenca_segundos = 0

            if diferenca_segundos > 1200 or diferenca_segundos < -300:
                continue

            assunto_original = decodificar_assunto(str(msg.get("Subject", "")))
            assunto_lower = assunto_original.lower()
            remetente_lower = str(msg.get("From", "")).lower()

            corpo_bruto = b""
            charset_msg = "utf-8"
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if "attachment" not in content_disposition:
                        if content_type in ["text/plain", "text/html"]:
                            payload = part.get_payload(decode=True)
                            if payload:
                                corpo_bruto += payload + b"\n"
                                if part.get_content_charset():
                                    charset_msg = part.get_content_charset()
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    corpo_bruto = payload
                    if msg.get_content_charset():
                        charset_msg = msg.get_content_charset()

            corpo_html = tratar_quopri_e_html(corpo_bruto, charset=charset_msg)
            corpo_texto_puro = extrair_apenas_texto_visivel(corpo_html)

            minutos = max(1, int(diferenca_segundos // 60)) if diferenca_segundos > 0 else 1

            eh_netflix = ("netflix" in remetente_lower or "netflix" in assunto_lower or "netflix" in corpo_html.lower())
            eh_disney = ("disney" in remetente_lower or "disney" in assunto_lower or "disneyplus" in corpo_html.lower())

            if eh_disney:
                codigos = re.findall(r'\b\d{6}\b', corpo_texto_puro)
                if not codigos:
                    codigos = re.findall(r'\b\d{4,6}\b', corpo_texto_puro)
                codigo_valido = next((c for c in codigos if not c.startswith("0800") and c != "2026"), None)
                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código Disney+ Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            if eh_netflix:
                link_acao, tipo_acao = extrair_link_acao_netflix(corpo_html)

                if link_acao:
                    if tipo_acao == "redefinicao":
                        if not pode_acessar_sensivel:
                            mail.close()
                            mail.logout()
                            return "🚫 <b>Solicitação Não Permitida!</b>\n\nEste e-mail trata de redefinição de senha ou alteração de dados."
                        mail.close()
                        mail.logout()
                        return (
                            f"✅ <b>Link de Redefinição de Senha Netflix Encontrado!</b>\n\n"
                            f"👉 <a href=\"{link_acao}\">Clique aqui para redefinir sua senha</a>\n\n"
                            f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                        )

                    elif tipo_acao == "residencia":
                        mail.close()
                        mail.logout()
                        return (
                            f"✅ <b>Link de Confirmação de Residência Netflix Encontrado!</b>\n\n"
                            f"👉 <a href=\"{link_acao}\">Clique aqui para confirmar no botão (Sim, fui eu)</a>\n\n"
                            f"💡 <i><b>Aviso do Celular:</b> Se abrir a tela 'A Netflix fica melhor com o app', clique nos 3 pontinhos do navegador e marque <b>'Versão para Computador' (Desktop)</b>.</i>\n\n"
                            f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                        )

                    elif tipo_acao == "viagem":
                        mail.close()
                        mail.logout()
                        return (
                            f"✅ <b>Link de Acesso Temporário / Viagem (Netflix)!</b>\n\n"
                            f"👉 <a href=\"{link_acao}\">Clique aqui para gerar/receber o código no site da Netflix</a>\n\n"
                            f"💡 <i><b>Importante para Celular:</b> Se abrir a tela 'A Netflix fica melhor com o app', toque nos 3 pontinhos do navegador do celular e ative <b>'Versão para Computador' (Desktop)</b> para que o código apareça.</i>\n\n"
                            f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                        )

                texto_limpo = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '', corpo_texto_puro)
                texto_limpo = re.sub(r'\b\d{1,2}\s+de\s+[a-zç]+\b', '', texto_limpo, flags=re.IGNORECASE)

                codigos = re.findall(r'\b\d{4,6}\b', texto_limpo)
                codigo_valido = next((c for c in codigos if c not in ["2024", "2025", "2026", "2027", "1056"] and not c.startswith("0800")), None)

                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código de Acesso Netflix Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

                link_fallback = extrair_url_pura_href(corpo_html, "netflix.com")
                if link_fallback and not any(x in link_fallback for x in ["unsubscribe", "help", "privacy", "terms"]):
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ <b>Link Netflix Encontrado!</b>\n\n"
                        f"👉 <a href=\"{link_fallback}\">Clique aqui para acessar a solicitação da Netflix</a>\n\n"
                        f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                    )

            eh_email_codigo = any(p in assunto_lower for p in ["código", "codigo", "temporário", "temporario", "autorização", "autorizacao", "verificação", "verificacao"])
            if eh_email_codigo:
                texto_limpo = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '', corpo_texto_puro)
                codigos = re.findall(r'\b\d{4,6}\b', texto_limpo)
                codigo_valido = next((c for c in codigos if not c.startswith("0800") and c not in ["2024", "2025", "2026", "2027"]), None)
                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            eh_redefinicao = (
                any(p in assunto_lower for p in ["redefinir", "recuperar", "alteração de senha", "alterar senha", "reset password", "restabelecer senha"])
                or ("recuperar" in assunto_lower and "senha" in assunto_lower)
                or "reset-password" in corpo_html.lower()
            )
            if eh_redefinicao:
                if not pode_acessar_sensivel:
                    mail.close()
                    mail.logout()
                    return "🚫 <b>Solicitação Não Permitida!</b>\n\nEste e-mail trata de redefinir senha ou alterar dados da conta."
                if "hbomax" in remetente_lower or "max.com" in remetente_lower or "hbo" in assunto_lower:
                    link_vip = extrair_link_hbomax_html(corpo_html)
                else:
                    link_vip = extrair_link_redefinicao_html(corpo_html)

                if link_vip:
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ <b>Link de Redefinição/Alteração de Senha Encontrado!</b>\n\n"
                        f"👉 <a href=\"{link_vip}\">Clique aqui para redefinir sua senha</a>\n\n"
                        f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                    )

            texto_limpo = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '', corpo_texto_puro)
            codigos_encontrados = re.findall(r'\b\d{4,6}\b', texto_limpo)
            codigo_valido = next((c for c in codigos_encontrados if not c.startswith("0800") and c not in ["2024", "2025", "2026", "2027"]), None)
            if codigo_valido:
                mail.close()
                mail.logout()
                return f"✅ <b>Código Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            link_encontrado = extrair_link_hbomax_html(corpo_html) or extrair_url_pura_href(corpo_html, "http")

            if link_encontrado:
                mail.close()
                mail.logout()
                return (
                    f"✅ <b>Link Encontrado!</b>\n\n"
                    f"👉 <a href=\"{link_encontrado}\">Clique aqui para acessar</a>\n\n"
                    f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                )

        mail.close()
        mail.logout()
        return "⚠️ Nenhum e-mail recente com código ou link válido foi localizado nesta caixa."

    except Exception as e:
        logging.error(f"Erro IMAP para {email_usuario}: {e}")
        return "❌ O servidor de e-mail demorou para responder. Tente novamente em alguns segundos."
    finally:
        try:
            if mail:
                mail.logout()
        except Exception:
            pass

# ==========================================
# MOTOR DE ALERTAS E NOTIFICAÇÕES AUTOMÁTICAS
# ==========================================
async def verificar_alertas_vencimento(app):
    while True:
        try:
            await asyncio.sleep(3600)
            hoje = datetime.now().date()
            amanha = hoje + timedelta(days=1)

            revendedores = carregar_json("revendedores.json")
            alterado_rev = False
            for r_id, info in revendedores.items():
                exp_date = converter_para_date(info.get("expira_em"))
                alerta_enviado = info.get("alerta_expiracao_enviado", False)

                if exp_date == amanha and not alerta_enviado:
                    try:
                        await app.bot.send_message(
                            chat_id=int(r_id),
                            text=(
                                f"⚠️ <b>AVISO DE VENCIMENTO DE REVENDA!</b>\n\n"
                                f"Sua assinatura como Revendedor expira <b>amanhã ({amanha.strftime('%d-%m-%Y')})</b>.\n\n"
                                f"📞 Entre em contato com o <b>Administrador Master</b> para efetuar a renovação!"
                            ),
                            parse_mode="HTML"
                        )
                        revendedores[r_id]["alerta_expiracao_enviado"] = True
                        alterado_rev = True
                    except Exception as e:
                        logging.error(f"Erro ao notificar revendedor {r_id}: {e}")

            if alterado_rev:
                salvar_json("revendedores.json", revendedores)

            config = carregar_json("config.json")
            if config.get("notificar_clientes", True):
                clientes = carregar_json("clientes.json")
                alterado_cli = False

                for c_id, info in clientes.items():
                    emails = info.get("emails_permitidos", {})
                    alertas_enviados = info.get("alertas_enviados", {})

                    for email_c, data_exp in emails.items():
                        if email_c == "*":
                            continue
                        exp_date = converter_para_date(data_exp)

                        if exp_date == amanha and not alertas_enviados.get(email_c, False):
                            try:
                                await app.bot.send_message(
                                    chat_id=int(c_id),
                                    text=(
                                        f"⏳ <b>AVISO DE VENCIMENTO DE ACESSO!</b>\n\n"
                                        f"O seu acesso ao e-mail <code>{email_c}</code> expira <b>amanhã ({amanha.strftime('%d-%m-%Y')})</b>.\n\n"
                                        f"🔄 Entre em contato com o suporte/revendedor para renovar sem interrupção!"
                                    ),
                                    parse_mode="HTML"
                                )
                                if "alertas_enviados" not in clientes[c_id]:
                                    clientes[c_id]["alertas_enviados"] = {}
                                clientes[c_id]["alertas_enviados"][email_c] = True
                                alterado_cli = True
                            except Exception as e:
                                logging.error(f"Erro ao notificar cliente {c_id}: {e}")

                if alterado_cli:
                    salvar_json("clientes.json", clientes)

        except Exception as err:
            logging.error(f"Erro na rotina de alertas de vencimento: {err}")

# ==========================================
# TUTORIAIS E REPORTADOR DE ERROS
# ==========================================
async def senhas_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    texto_senhas = (
        "🔑 <b>GUIA: COMO GERAR SENHA DE APLICATIVO POR PROVEDOR</b>\n\n"
        "🔴 <b>GMAIL (@gmail.com):</b>\n"
        "1️⃣ Acesse: <a href=\"https://myaccount.google.com/security\">myaccount.google.com/security</a>\n"
        "2️⃣ Ative a <b>Verificação em duas etapas</b>.\n"
        "3️⃣ Busque por <b>'Senhas de app'</b> e gere uma nova para o bot.\n\n"
        "🔵 <b>OUTLOOK / HOTMAIL (@outlook.com / @hotmail.com):</b>\n"
        "1️⃣ Acesse: <a href=\"https://account.microsoft.com/security\">account.microsoft.com/security</a>\n"
        "2️⃣ Vá em <b>Opções de segurança avançadas</b> > <b>Senhas de aplicativo</b>.\n\n"
        "🟣 <b>YAHOO (@yahoo.com):</b>\n"
        "1️⃣ Acesse: <a href=\"https://login.yahoo.com/account/security\">login.yahoo.com/account/security</a>\n"
        "2️⃣ Clique em <b>Gerar senha de aplicativo</b>."
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(texto_senhas, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await update.message.reply_text(texto_senhas, parse_mode="HTML", disable_web_page_preview=True, reply_markup=obter_teclado_usuario(user_id))

async def reportar_erro_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    context.user_data["aguardando_reporte"] = True
    msg_texto = (
        "📸 <b>Reportar Problema ao Suporte</b>\n\n"
        "Por favor, envie um <b>print (foto da tela)</b> ou uma <b>mensagem detalhada</b> descrevendo o erro que você encontrou.\n\n"
        "<i>Nosso suporte receberá sua mensagem imediatamente!</i>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg_texto, parse_mode="HTML")
    else:
        await update.message.reply_text(msg_texto, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def listar_erros_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    erros = carregar_erros_db()
    if not erros:
        await update.message.reply_text("📋 <b>Nenhum erro foi reportado recentemente.</b>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    msg = "📋 <b>Últimos Erros Reportados:</b>\n\n"
    for idx, item in enumerate(erros[:15], 1):
        tem_f = "📸 com Print" if item.get("tem_foto") else "📝 Texto"
        msg += f"<b>{idx}. ID Usuário:</b> <code>{item.get('user_id')}</code> ({tem_f})\n ├ 📅 Data: {item.get('created_at')}\n └ 💬 Relato: {item.get('mensagem')}\n\n"

    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

# ==========================================
# COMANDOS E PAINEL / MENU DO BOT
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    teclado_customizado = obter_teclado_usuario(user_id)

    await update.message.reply_text(
        f"👋 <b>Central de Liberação de Códigos</b>\n\n"
        f"🆔 <b>Seu ID do Telegram:</b> <code>{user_id}</code>\n\n"
        f"Envie o e-mail cadastrado abaixo para buscar códigos/links recentes.\n\n"
        f"<i>Dúvidas ou recursos? Utilize o menu no rodapé do teclado 👇</i>",
        parse_mode="HTML",
        reply_markup=teclado_customizado
    )

async def menu_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    teclado_customizado = obter_teclado_usuario(user_id)

    if not eh_revendedor(user_id):
        await update.message.reply_text("⛔ Você não possui acesso ao painel de administração.", parse_mode="HTML", reply_markup=teclado_customizado)
        return

    is_master = eh_admin_master(user_id)
    config = carregar_json("config.json")
    status_notif = "LIGADO 🔔" if config.get("notificar_clientes", True) else "DESLIGADO 🔕"

    if is_master:
        texto_menu = (
            "👑 <b>PAINEL MASTER ADM</b>\n\n"
            "🌐 <b>Domínios Suportados:</b> Gmail, Outlook/Hotmail e Yahoo.\n"
            "💡 Use o comando <code>/senhas</code> para ver o tutorial de Senhas de App.\n"
            "⚠️ Use o comando <code>/erros</code> para ver os relatos com prints enviadas pelos clientes/revendas.\n\n"
            "<b>Gerenciamento de Revendedores:</b>\n"
            "• <code>/addrevenda ID_TELEGRAM DIAS_OU_DATA</code> - Adicionar Revendedor\n"
            "• <code>/delrevenda ID_TELEGRAM</code> - Remover Revendedor\n"
            "• <code>/listarrevendas</code> - Ver Revendedores e Métricas\n\n"
            "<b>Bots White-Label (Marca Própria):</b>\n"
            "• <code>/addbot TOKEN ID_REVENDEDOR</code> - Vincular Bot para Revenda\n"
            "• <code>/delbot TOKEN</code> - Remover Bot de Revenda\n"
            "• <code>/listarbots</code> - Listar Bots Ativos\n\n"
            "<b>Gerenciamento de Contas e Clientes:</b>\n"
            "• <code>/addconta EMAIL SENHA_APP</code> - Cadastrar E-mail\n"
            "• <code>/delconta EMAIL</code> - Remover E-mail\n"
            "• <code>/listarcontas</code> - Ver E-mails no Banco\n"
            "• <code>/autorizar ID_CLIENTE EMAIL DIAS_OU_DATA</code> - Liberar Cliente\n"
            "• <code>/listar</code> - Listar Clientes e Validades\n"
            "• <code>/editar ID_CLIENTE EMAIL DATA</code> - Alterar Data de Vencimento\n"
            "• <code>/remover ID_CLIENTE EMAIL</code> - Cancelar Acesso\n\n"
            f"<b>Configurações Gerais:</b>\n"
            f"• <code>/notificacoes sim/nao</code> - Alertas de Clientes (Atual: <b>{status_notif}</b>)"
        )
    else:
        texto_menu = (
            "💼 <b>PAINEL DO REVENDEDOR</b>\n\n"
            "🌐 <b>Domínios Suportados:</b> Gmail, Outlook/Hotmail e Yahoo.\n"
            "🔑 Use o comando <code>/senhas</code> para ver como gerar a Senha de App.\n"
            "⚠️ Use o comando <code>/erros</code> para ver relatos de erros dos seus clientes.\n\n"
            "<b>Gerenciamento de Suas Contas:</b>\n"
            "• <code>/addconta EMAIL SENHA_APP</code> - Cadastrar seu E-mail\n"
            "• <code>/delconta EMAIL</code> - Remover seu E-mail\n"
            "• <code>/listarcontas</code> - Ver seus E-mails no Banco\n\n"
            "<b>Gerenciamento dos Seus Clientes:</b>\n"
            "• <code>/autorizar ID_CLIENTE EMAIL DIAS_OU_DATA</code> - Liberar Cliente\n"
            "• <code>/listar</code> - Listar seus Clientes e Validades\n"
            "• <code>/editar ID_CLIENTE EMAIL DATA</code> - Alterar Data do Cliente\n"
            "• <code>/remover ID_CLIENTE EMAIL</code> - Remover Cliente"
        )

    await update.message.reply_text(texto_menu, parse_mode="HTML", reply_markup=teclado_customizado)

async def tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "tutorial":
        texto_tutorial = (
            "🤖 <b>COMO USAR O BOT (PASSO A PASSO):</b>\n\n"
            "1️⃣ Na sua TV, dê preferência para a opção <b>'Estou viajando'</b> ou <b>'Atualizar residência'</b> e clique em <b>Enviar e-mail/Código</b>.\n\n"
            "2️⃣ Digite <b>APENAS o e-mail exato</b> da conta aqui neste chat.\n\n"
            "3️⃣ Se receber um <b>código</b>, digite-o na TV. Se receber um <b>link em azul</b>, basta clicar nele para liberar a tela.\n\n"
            "📌 <i>Atenção: Digite a grafia exata do e-mail cadastrado (com os mesmos pontos e letras).</i>"
        )
        await query.message.reply_text(texto_tutorial, parse_mode="HTML")
    elif query.data == "tutorial_senhas":
        await senhas_comando(update, context)
    elif query.data == "reportar_erro":
        await reportar_erro_callback(update, context)
    elif query.data == "menu_admin":
        await menu_comando(update, context)

# ==========================================
# GERENCIAMENTO DE BOTS WHITE-LABEL (MASTER ADM)
# ==========================================
async def add_bot_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/addbot TOKEN ID_REVENDEDOR</code>\n\n"
            "Exemplo: <code>/addbot 123456789:ABCdefGHI... 7496198484</code>",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    token_bot = str(context.args[0]).strip()
    dono_revenda_id = str(context.args[1]).strip()

    bots = carregar_json("bots.json")
    bots[token_bot] = {"dono_id": dono_revenda_id}
    salvar_json("bots.json", bots)

    loop = asyncio.get_running_loop()
    loop.create_task(iniciar_instancia_bot(token_bot, dono_revenda_id))

    await update.message.reply_text(
        f"🤖 <b>Bot White-Label Ativado com Sucesso!</b>\n\n"
        f"👤 <b>Dono do Bot (Revenda):</b> <code>{dono_revenda_id}</code>\n"
        f"🔑 <b>Token:</b> <code>{token_bot[:10]}...</code>\n\n"
        f"Este bot agora está rodando e responderá aos clientes dessa revenda!",
        parse_mode="HTML",
        reply_markup=obter_teclado_usuario(user_id)
    )

async def del_bot_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/delbot TOKEN</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    token_target = str(context.args[0]).strip()
    bots = carregar_json("bots.json")

    if token_target in bots:
        del bots[token_target]
        salvar_json("bots.json", bots)

        if token_target in APLICACOES_BOTS:
            try:
                await APLICACOES_BOTS[token_target].stop()
                await APLICACOES_BOTS[token_target].shutdown()
                del APLICACOES_BOTS[token_target]
            except Exception as e:
                logging.error(f"Erro ao parar bot: {e}")

        await update.message.reply_text(f"🗑️ Bot removido e desativado com sucesso!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
    else:
        await update.message.reply_text("❌ Token de Bot não localizado.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def listar_bots_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    bots = carregar_json("bots.json")
    if not bots:
        await update.message.reply_text("📋 <b>Nenhum bot White-Label ativo no momento.</b>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    msg = "🤖 <b>Lista de Bots White-Label Ativos:</b>\n\n"
    for tok, info in bots.items():
        msg += f"👤 <b>Dono ID:</b> <code>{info.get('dono_id')}</code>\n └ 🔑 Token: <code>{tok[:15]}...</code>\n\n"

    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

# ==========================================
# GERENCIAMENTO DE REVENDEDORES (MASTER ADM)
# ==========================================
async def add_revenda_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/addrevenda ID_TELEGRAM DIAS_OU_DATA</code>\n\n"
            "Exemplos:\n"
            "• <code>/addrevenda 123456789 30</code>\n"
            "• <code>/addrevenda 123456789 28-11-2026</code>",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    novo_id_revenda = str(context.args[0]).strip()
    arg_tempo = str(context.args[1]).strip()

    hoje = datetime.now().date()
    if arg_tempo.isdigit():
        dias = int(arg_tempo)
        dt_expira = hoje + timedelta(days=dias)
    else:
        exp_date = converter_para_date(arg_tempo)
        if not exp_date:
            await update.message.reply_text("⚠️ Data inválida! Use dias ou formato <b>DD-MM-AAAA</b>.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
            return
        dt_expira = exp_date

    data_exp_str = dt_expira.strftime("%Y-%m-%d")
    data_exibicao = dt_expira.strftime("%d-%m-%Y")

    revendedores = carregar_json("revendedores.json")
    revendedores[novo_id_revenda] = {
        "cadastrado_em": hoje.strftime("%Y-%m-%d"),
        "expira_em": data_exp_str,
        "cadastrado_por": user_id,
        "alerta_expiracao_enviado": False
    }
    salvar_json("revendedores.json", revendedores)

    await update.message.reply_text(
        f"👑 <b>Revendedor Cadastrado com Sucesso!</b>\n\n"
        f"👤 <b>ID Revenda:</b> <code>{novo_id_revenda}</code>\n"
        f"📅 <b>Válido até:</b> {data_exibicao}\n\n"
        f"Agora este usuário pode cadastrar e-mails (<code>/addconta</code>) e gerenciar clientes (<code>/autorizar</code>).",
        parse_mode="HTML",
        reply_markup=obter_teclado_usuario(user_id)
    )

async def del_revenda_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/delrevenda ID_TELEGRAM</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    target_id = str(context.args[0]).strip()
    revendedores = carregar_json("revendedores.json")

    if target_id in revendedores:
        del revendedores[target_id]
        salvar_json("revendedores.json", revendedores)
        await update.message.reply_text(f"🗑️ Revendedor <code>{target_id}</code> removido!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
    else:
        await update.message.reply_text(f"❌ Revendedor <code>{target_id}</code> não encontrado.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def listar_revendas_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    revendedores = carregar_json("revendedores.json")
    if not revendedores:
        await update.message.reply_text("📋 <b>Nenhum revendedor cadastrado.</b>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    contas = carregar_json("contas.json")
    clientes = carregar_json("clientes.json")
    hoje = datetime.now().date()

    msg = "👑 <b>Lista de Revendedores Cadastrados:</b>\n\n"
    for r_id, info in revendedores.items():
        exp_date = converter_para_date(info.get("expira_em"))
        if exp_date:
            dias_restantes = (exp_date - hoje).days
            status_txt = f"🛑 <i>Expirado</i>" if dias_restantes < 0 else f"{exp_date.strftime('%d-%m-%Y')} ({dias_restantes}d)"
        else:
            status_txt = "Sem limite"

        total_contas = sum(1 for c in contas.values() if str(c.get("dono_id")) == r_id)
        total_clientes = sum(1 for cl in clientes.values() if str(cl.get("criado_por")) == r_id)
        msg += f"👤 <b>ID:</b> <code>{r_id}</code>\n ├ 📅 Validade: {status_txt}\n ├ 📧 Contas no Banco: <b>{total_contas}</b>\n └ 👥 Clientes Ativos: <b>{total_clientes}</b>\n\n"

    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def toggle_notificacoes_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if not context.args or context.args[0].lower() not in ["sim", "nao", "s", "n", "true", "false", "on", "off"]:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/notificacoes sim</code> ou <code>/notificacoes nao</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    opcao = context.args[0].lower() in ["sim", "s", "true", "on"]
    config = carregar_json("config.json")
    config["notificar_clientes"] = opcao
    salvar_json("config.json", config)

    status_txt = "ATIVADAS 🔔 (O bot avisará os clientes 1 dia antes do vencimento)" if opcao else "DESATIVADAS 🔕"
    await update.message.reply_text(f"⚙️ <b>Notificações de Clientes:</b> {status_txt}", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

# ==========================================
# GERENCIAMENTO DE CONTAS DO BANCO
# ==========================================
async def add_conta_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/addconta email@gmail.com SENHA_DO_APP</code>\n\n"
            "Exemplo: <code>/addconta cliente1@gmail.com abcd efgh ijkl mnop</code>\n\n"
            "<i>Caso tenha dúvidas sobre como gerar a Senha de App do Gmail, Outlook ou Yahoo, use <code>/senhas</code></i>",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    email_conta = str(context.args[0]).strip().lower()
    senha_conta = " ".join(context.args[1:]).strip()

    if not re.match(r"^[\w\.\+-]+@[\w\.-]+\.\w+$", email_conta):
        await update.message.reply_text("⚠️ Insira um e-mail válido.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    contas = carregar_json("contas.json")

    if email_conta in contas and not eh_admin_master(user_id):
        if str(contas[email_conta].get("dono_id")) != user_id:
            await update.message.reply_text("⛔ Este e-mail pertence a outro revendedor!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
            return

    host_imap = "imap.gmail.com"
    if "yahoo" in email_conta:
        host_imap = "imap.mail.yahoo.com"
    elif "outlook" in email_conta or "hotmail" in email_conta:
        host_imap = "outlook.office365.com"

    contas[email_conta] = {
        "senha": senha_conta,
        "host_imap": host_imap,
        "porta": 993,
        "email_login": email_conta,
        "dono_id": user_id
    }
    salvar_json("contas.json", contas)

    await update.message.reply_text(
        f"✅ <b>Conta Cadastrada no Banco com Sucesso!</b>\n\n"
        f"📧 <b>E-mail:</b> <code>{email_conta}</code>\n"
        f"🔑 <b>Senha cadastrada:</b> OK\n"
        f"👑 <b>Dono do Registro:</b> <code>{user_id}</code>\n\n"
        f"Agora você pode liberar acesso para seus clientes com <code>/autorizar ID_CLIENTE {email_conta} DIAS</code>",
        parse_mode="HTML",
        reply_markup=obter_teclado_usuario(user_id)
    )

async def del_conta_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/delconta email@gmail.com</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    email_target = str(context.args[0]).strip().lower()
    contas = carregar_json("contas.json")

    if email_target in contas:
        dono_id = str(contas[email_target].get("dono_id"))
        if not eh_admin_master(user_id) and dono_id != user_id:
            await update.message.reply_text("⛔ Você não tem permissão para remover esta conta.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
            return

        del contas[email_target]
        salvar_json("contas.json", contas)
        await update.message.reply_text(f"🗑️ Conta <code>{email_target}</code> removida do banco!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
    else:
        await update.message.reply_text(f"❌ Conta <code>{email_target}</code> não encontrada.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def listar_contas_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    contas = carregar_json("contas.json")
    is_master = eh_admin_master(user_id)

    contas_filtradas = {e: c for e, c in contas.items() if is_master or str(c.get("dono_id")) == user_id}

    if not contas_filtradas:
        await update.message.reply_text("📋 <b>Você não possui nenhuma conta cadastrada no banco.</b>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    msg = "📋 <b>Contas de Streaming Cadastradas:</b>\n\n"
    for email_c, info in contas_filtradas.items():
        dono_str = f" | Dono: <code>{info.get('dono_id')}</code>" if is_master else ""
        msg += f"📧 <code>{email_c}</code>{dono_str}\n"

    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

# ==========================================
# GERENCIAMENTO DE CLIENTES
# ==========================================
async def autorizar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    try:
        user_id_cliente = str(context.args[0]).strip()
        email_cliente_raw = context.args[1].strip().lower()
        arg_terceiro = str(context.args[2]).strip()

        contas = carregar_json("contas.json")
        if email_cliente_raw != "*" and not eh_admin_master(user_id):
            if email_cliente_raw not in contas or str(contas[email_cliente_raw].get("dono_id")) != user_id:
                await update.message.reply_text(
                    f"⛔ <b>Acesso Negado!</b>\n\nO e-mail <code>{email_cliente_raw}</code> não está cadastrado no seu banco. Adicione-o primeiro com <code>/addconta</code>.",
                    parse_mode="HTML",
                    reply_markup=obter_teclado_usuario(user_id)
                )
                return

        liberar_sensivel = False
        if len(context.args) > 3 and context.args[3].lower() in ["vip", "sensivel", "true", "todos", "sim"]:
            if eh_admin_master(user_id):
                liberar_sensivel = True

        hoje = datetime.now().date()

        if arg_terceiro.isdigit():
            dias = int(arg_terceiro)
            dt_expira = hoje + timedelta(days=dias)
        else:
            exp_date = converter_para_date(arg_terceiro)
            if not exp_date:
                await update.message.reply_text("⚠️ <b>Validade inválida!</b> Use número de dias (ex: <code>30</code>) ou data (ex: <code>28-11-2026</code>).", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
                return
            dt_expira = exp_date
            dias = (dt_expira - hoje).days

        data_validade_banco = dt_expira.strftime("%Y-%m-%d")
        data_validade_exibicao = dt_expira.strftime("%d-%m-%Y")

        clientes = carregar_json("clientes.json")

        if user_id_cliente not in clientes:
            clientes[user_id_cliente] = {
                "emails_permitidos": {},
                "permitir_sensivel": liberar_sensivel,
                "criado_por": user_id,
                "alertas_enviados": {}
            }
        else:
            if liberar_sensivel:
                clientes[user_id_cliente]["permitir_sensivel"] = True

        if "emails_permitidos" not in clientes[user_id_cliente]:
            clientes[user_id_cliente]["emails_permitidos"] = {}

        clientes[user_id_cliente]["emails_permitidos"][email_cliente_raw] = data_validade_banco

        if "alertas_enviados" in clientes[user_id_cliente]:
            clientes[user_id_cliente]["alertas_enviados"][email_cliente_raw] = False

        salvar_json("clientes.json", clientes)

        await update.message.reply_text(
            f"✅ <b>Acesso Concedido com Sucesso!</b>\n\n"
            f"👤 <b>ID Cliente:</b> <code>{user_id_cliente}</code>\n"
            f"📧 <b>E-mail Liberado:</b> <code>{email_cliente_raw}</code>\n"
            f"📅 <b>Válido até:</b> {data_validade_exibicao} ({dias} dias)\n",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
    except Exception as e:
        logging.error(f"Erro ao autorizar cliente: {e}")
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/autorizar ID_CLIENTE EMAIL DIAS_OU_DATA</code>\n\n"
            "Exemplo: <code>/autorizar 7219528497 email@gmail.com 30</code>",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )

async def vip_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_admin_master(user_id):
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/vip ID_CLIENTE sim/nao</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    target_id = str(context.args[0]).strip()
    status_vip = str(context.args[1]).strip().lower() in ["sim", "s", "true", "1", "vip", "on"]

    clientes = carregar_json("clientes.json")
    if target_id not in clientes:
        clientes[target_id] = {"emails_permitidos": {}, "permitir_sensivel": status_vip, "criado_por": str(ADMIN_ID), "alertas_enviados": {}}
    else:
        clientes[target_id]["permitir_sensivel"] = status_vip

    salvar_json("clientes.json", clientes)

    txt_status = "ATIVADO ⭐️" if status_vip else "DESATIVADO"
    await update.message.reply_text(f"👑 <b>Status VIP do ID <code>{target_id}</code>:</b> {txt_status}", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def editar_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    if len(context.args) < 3:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/editar ID_CLIENTE EMAIL DD-MM-AAAA</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    target_id = str(context.args[0]).strip()
    target_email = str(context.args[1]).strip().lower()
    nova_opcao = str(context.args[2]).strip().lower()

    clientes = carregar_json("clientes.json")
    if target_id not in clientes or target_email not in clientes[target_id].get("emails_permitidos", {}):
        await update.message.reply_text(f"❌ E-mail <code>{target_email}</code> não encontrado para este cliente.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    criado_por = str(clientes[target_id].get("criado_por", ADMIN_ID))
    if not eh_admin_master(user_id) and criado_por != user_id:
        await update.message.reply_text("⛔ Você não tem permissão para editar este cliente.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    if nova_opcao in ["sem_data", "semdata", "ilimitado"]:
        clientes[target_id]["emails_permitidos"][target_email] = "2030-12-31"
        data_exib = "Sem expiração (Ilimitado)"
    else:
        exp_date = converter_para_date(nova_opcao)
        if not exp_date:
            await update.message.reply_text("⚠️ Data inválida! Use o formato <b>DD-MM-AAAA</b>.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
            return
        clientes[target_id]["emails_permitidos"][target_email] = exp_date.strftime("%Y-%m-%d")
        data_exib = exp_date.strftime("%d-%m-%Y")

    if "alertas_enviados" in clientes[target_id]:
        clientes[target_id]["alertas_enviados"][target_email] = False

    salvar_json("clientes.json", clientes)
    await update.message.reply_text(f"✏️ Data atualizada para {data_exib}!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def remover_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/remover ID_CLIENTE EMAIL</code>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    target_id = str(context.args[0]).strip()
    target_email = str(context.args[1]).strip().lower()

    clientes = carregar_json("clientes.json")
    if target_id in clientes and target_email in clientes[target_id].get("emails_permitidos", {}):
        criado_por = str(clientes[target_id].get("criado_por", ADMIN_ID))
        if not eh_admin_master(user_id) and criado_por != user_id:
            await update.message.reply_text("⛔ Você não tem permissão para remover este cliente.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
            return

        del clientes[target_id]["emails_permitidos"][target_email]
        salvar_json("clientes.json", clientes)
        await update.message.reply_text(f"🗑️ E-mail <code>{target_email}</code> removido do ID <code>{target_id}</code>!", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
    else:
        await update.message.reply_text(f"❌ O e-mail não foi localizado no cadastro do cliente.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def listar_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    if not eh_revendedor(user_id):
        return

    clientes = carregar_json("clientes.json")
    is_master = eh_admin_master(user_id)

    lista_filtrada = {k: v for k, v in clientes.items() if is_master or str(v.get("criado_por")) == user_id}

    if not lista_filtrada:
        await update.message.reply_text("📋 <b>Nenhum cliente cadastrado por você.</b>", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    hoje = datetime.now().date()
    mensagem = "📋 <b>Sua Lista de Clientes Liberados:</b>\n\n"

    for id_cliente, dados in lista_filtrada.items():
        emails = dados.get("emails_permitidos", {})
        dono_tag = f" (Criado por: <code>{dados.get('criado_por')}</code>)" if is_master else ""
        mensagem += f"👤 <b>ID:</b> <code>{id_cliente}</code>{dono_tag}\n"

        for email_end, data_exp in emails.items():
            exp_date = converter_para_date(data_exp)
            if exp_date:
                dias_restantes = (exp_date - hoje).days
                status_dias = "🛑 <i>Expirado</i>" if dias_restantes < 0 else f"⏳ <i>Faltam {dias_restantes} dia(s)</i>"
                data_exibicao = exp_date.strftime("%d-%m-%Y")
            else:
                status_dias = "❓ Data inválida"
                data_exibicao = str(data_exp)

            mensagem += f" └ 📧 <code>{email_end}</code>\n 📅 Validade: {data_exibicao} ({status_dias})\n"
        mensagem += "\n"

    if len(mensagem) > 4000:
        for x in range(0, len(mensagem), 4000):
            await update.message.reply_text(mensagem[x:x+4000], parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
    else:
        await update.message.reply_text(mensagem, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

async def receber_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    texto_recebido = update.message.text.strip() if update.message.text else ""

    # GATILHOS DOS BOTÕES DO TECLADO PERSONALIZADO POR NÍVEL
    if texto_recebido in ["👑 Painel Master", "💼 Painel Revenda"]:
        await menu_comando(update, context)
        return

    elif texto_recebido == "📋 Meus Clientes":
        await listar_clientes(update, context)
        return

    elif texto_recebido == "📧 Minhas Contas":
        await listar_contas_comando(update, context)
        return

    elif texto_recebido == "💼 Revendedores":
        await listar_revendas_comando(update, context)
        return

    elif texto_recebido == "🤖 Bots White-Label":
        await listar_bots_comando(update, context)
        return

    elif texto_recebido in ["⚠️ Ver Erros", "⚠️ Ver Erros Geral"]:
        await listar_erros_comando(update, context)
        return

    elif texto_recebido in ["📖 Como Usar", "📖 Como Usar / Tutorial"]:
        texto_tutorial = (
            "🤖 <b>COMO USAR O BOT (PASSO A PASSO):</b>\n\n"
            "1️⃣ Na sua TV, dê preferência para a opção <b>'Estou viajando'</b> ou <b>'Atualizar residência'</b> e clique em <b>Enviar e-mail/Código</b>.\n\n"
            "2️⃣ Digite <b>APENAS o e-mail exato</b> da conta aqui neste chat.\n\n"
            "3️⃣ Se receber um <b>código</b>, digite-o na TV. Se receber um <b>link em azul</b>, basta clicar nele para liberar a tela."
        )
        await update.message.reply_text(texto_tutorial, parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    elif texto_recebido == "🔑 Senhas de App":
        if eh_revendedor(user_id):
            await senhas_comando(update, context)
        return

    elif texto_recebido == "⚠️ Reportar Erro / Print":
        await reportar_erro_callback(update, context)
        return

    # TRATAMENTO DE REPORTE DE ERROS (TEXTO OU FOTO/PRINT)
    if context.user_data.get("aguardando_reporte"):
        context.user_data["aguardando_reporte"] = False
        legenda_ou_texto = update.message.caption or update.message.text or "Sem descrição fornecida."
        tem_foto = bool(update.message.photo)

        salvar_novo_erro(user_id, legenda_ou_texto, tem_foto)

        clientes = carregar_json("clientes.json")
        dono_revenda_id = str(clientes.get(user_id, {}).get("criado_por", ADMIN_ID))

        notificacao_admin = (
            f"🚨 <b>NOVO PROBLEMA REPORTADO!</b>\n\n"
            f"👤 <b>Usuário ID:</b> <code>{user_id}</code>\n"
            f"📅 <b>Data:</b> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"💬 <b>Relato:</b> {legenda_ou_texto}"
        )

        for destino_id in set([ADMIN_ID, dono_revenda_id]):
            try:
                if tem_foto:
                    foto_file_id = update.message.photo[-1].file_id
                    await context.bot.send_photo(chat_id=int(destino_id), photo=foto_file_id, caption=notificacao_admin, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id=int(destino_id), text=notificacao_admin, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Erro ao notificar admin/revenda {destino_id}: {e}")

        await update.message.reply_text(
            "✅ <b>Seu relato e print foram enviados com sucesso ao nosso suporte!</b>\n\nMuito obrigado pelo aviso. Vamos analisar e corrigir o mais rápido possível.",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    # CONTINUAÇÃO DA BUSCA DE CÓDIGOS DE E-MAIL
    email_original = texto_recebido.lower()
    bot_dono_id = context.bot_data.get("dono_id")

    clientes = carregar_json("clientes.json")
    eh_admin = eh_admin_master(user_id)
    dados_cliente = clientes.get(user_id, {})

    if not eh_admin and (not dados_cliente or not dados_cliente.get("emails_permitidos")) and not dados_cliente.get("permitir_sensivel"):
        await update.message.reply_text(
            f"🔒 <b>Acesso Não Liberado!</b>\n\nSeu ID de Usuário: <code>{user_id}</code>\n\nEncaminhe ao Suporte/Revendedor para liberação.",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    if not re.match(r"^[\w\.\+-]+@[\w\.-]+\.\w+$", email_original):
        await update.message.reply_text("⚠️ Por favor, digite um <b>endereço de e-mail válido</b>.", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))
        return

    pode_acessar_sensivel = eh_admin or dados_cliente.get("permitir_sensivel", False)

    if not eh_admin:
        emails_permitidos = dados_cliente.get("emails_permitidos", {})
        hoje = datetime.now().date()

        tem_acesso = False
        email_expirado = False

        if dados_cliente.get("permitir_sensivel", False):
            tem_acesso = True
        else:
            for k, data_exp in emails_permitidos.items():
                if k == "*" or email_original == k.lower():
                    exp_date = converter_para_date(data_exp)
                    if exp_date and exp_date >= hoje:
                        tem_acesso = True
                        break
                    else:
                        email_expirado = True

        if not tem_acesso:
            if email_expirado:
                await update.message.reply_text(
                    f"🛑 <b>Acesso Expirado!</b>\n\nA autorização para o e-mail <code>{email_original}</code> já venceu. Entre em contato com seu revendedor.",
                    parse_mode="HTML",
                    reply_markup=obter_teclado_usuario(user_id)
                )
            else:
                await update.message.reply_text(
                    f"❌ Você não tem autorização para acessar o e-mail <code>{email_original}</code>.",
                    parse_mode="HTML",
                    reply_markup=obter_teclado_usuario(user_id)
                )
            return

    email_base = normalizar_email_gmail(email_original)
    contas = carregar_json("contas.json")
    conta_encontrada = None

    if isinstance(contas, dict):
        for chave_email, dados in contas.items():
            chave_norm = normalizar_email_gmail(chave_email)
            if chave_email.strip().lower() == email_original or chave_norm == email_base:
                dono_conta = str(dados.get("dono_id", ADMIN_ID))
                if bot_dono_id and not eh_admin and dono_conta != bot_dono_id:
                    continue
                conta_encontrada = dados if isinstance(dados, dict) else {"senha": dados}
                break

    if not conta_encontrada:
        await update.message.reply_text(
            f"❌ A conta <code>{email_original}</code> não possui credenciais salvas no banco de dados.",
            parse_mode="HTML",
            reply_markup=obter_teclado_usuario(user_id)
        )
        return

    email_login = conta_encontrada.get("email_login", email_base)

    msg_carregando = await update.message.reply_text(f"⏳ Buscando e-mail recente para <code>{email_original}</code>...", parse_mode="HTML", reply_markup=obter_teclado_usuario(user_id))

    loop = asyncio.get_running_loop()
    try:
        resultado = await loop.run_in_executor(
            None, 
            extrair_codigo_imap_wrapper, 
            {**conta_encontrada, "email_usuario": email_login, "email_destinatario": email_original}, 
            pode_acessar_sensivel
        )
    except Exception as err:
        logging.error(f"Erro na execução da busca: {err}")
        resultado = "❌ Ocorreu uma falha temporária ao consultar a caixa de e-mail."

    try:
        await msg_carregando.edit_text(resultado, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Erro ao editar mensagem: {e}")

# ==========================================
# MOTOR E INICIALIZAÇÃO DE MÚLTIPLOS BOTS
# ==========================================
def construir_aplicacao_bot(token: str, dono_id: str = None):
    app = ApplicationBuilder().token(token).build()
    if dono_id:
        app.bot_data["dono_id"] = str(dono_id)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_comando))
    app.add_handler(CommandHandler("admin", menu_comando))
    app.add_handler(CommandHandler("senhas", senhas_comando))
    app.add_handler(CommandHandler("tutorial_senhas", senhas_comando))
    app.add_handler(CommandHandler("erros", listar_erros_comando))
    app.add_handler(CallbackQueryHandler(tutorial_callback))

    # Comandos Master
    app.add_handler(CommandHandler("addbot", add_bot_comando))
    app.add_handler(CommandHandler("delbot", del_bot_comando))
    app.add_handler(CommandHandler("listarbots", listar_bots_comando))
    app.add_handler(CommandHandler("addrevenda", add_revenda_comando))
    app.add_handler(CommandHandler("delrevenda", del_revenda_comando))
    app.add_handler(CommandHandler("listarrevendas", listar_revendas_comando))
    app.add_handler(CommandHandler("notificacoes", toggle_notificacoes_comando))
    app.add_handler(CommandHandler("vip", vip_comando))

    # Comandos Revendedor
    app.add_handler(CommandHandler("addconta", add_conta_comando))
    app.add_handler(CommandHandler("delconta", del_conta_comando))
    app.add_handler(CommandHandler("listarcontas", listar_contas_comando))
    app.add_handler(CommandHandler("autorizar", autorizar_cliente))
    app.add_handler(CommandHandler("editar", editar_comando))
    app.add_handler(CommandHandler("remover", remover_comando))
    app.add_handler(CommandHandler("listar", listar_clientes))

    # Captura de Texto e Fotos (Prints)
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receber_mensagem_email))

    return app

async def iniciar_instancia_bot(token: str, dono_id: str = None):
    try:
        app = construir_aplicacao_bot(token, dono_id)
        APLICACOES_BOTS[token] = app
        await app.initialize()
        await app.start()
        await app.updater.start_polling(poll_interval=1.0, timeout=20)
        logging.info(f"✅ Bot White-Label iniciado com sucesso! (Dono: {dono_id})")
    except Exception as e:
        logging.error(f"Erro ao iniciar bot White-Label ({token[:10]}...): {e}")

async def post_init(application):
    asyncio.create_task(verificar_alertas_vencimento(application))

    bots_cadastrados = carregar_json("bots.json")
    for token_bot, info in bots_cadastrados.items():
        if token_bot != TOKEN_TELEGRAM_MASTER:
            asyncio.create_task(iniciar_instancia_bot(token_bot, info.get("dono_id")))

def main():
    Thread(target=run_flask, daemon=True).start()

    while True:
        try:
            app_master = (
                ApplicationBuilder()
                .token(TOKEN_TELEGRAM_MASTER)
                .post_init(post_init)
                .build()
            )

            app_master.add_handler(CommandHandler("start", start))
            app_master.add_handler(CommandHandler("menu", menu_comando))
            app_master.add_handler(CommandHandler("admin", menu_comando))
            app_master.add_handler(CommandHandler("senhas", senhas_comando))
            app_master.add_handler(CommandHandler("tutorial_senhas", senhas_comando))
            app_master.add_handler(CommandHandler("erros", listar_erros_comando))
            app_master.add_handler(CallbackQueryHandler(tutorial_callback))

            app_master.add_handler(CommandHandler("addbot", add_bot_comando))
            app_master.add_handler(CommandHandler("delbot", del_bot_comando))
            app_master.add_handler(CommandHandler("listarbots", listar_bots_comando))
            app_master.add_handler(CommandHandler("addrevenda", add_revenda_comando))
            app_master.add_handler(CommandHandler("delrevenda", del_revenda_comando))
            app_master.add_handler(CommandHandler("listarrevendas", listar_revendas_comando))
            app_master.add_handler(CommandHandler("notificacoes", toggle_notificacoes_comando))
            app_master.add_handler(CommandHandler("vip", vip_comando))

            app_master.add_handler(CommandHandler("addconta", add_conta_comando))
            app_master.add_handler(CommandHandler("delconta", del_conta_comando))
            app_master.add_handler(CommandHandler("listarcontas", listar_contas_comando))
            app_master.add_handler(CommandHandler("autorizar", autorizar_cliente))
            app_master.add_handler(CommandHandler("editar", editar_comando))
            app_master.add_handler(CommandHandler("remover", remover_comando))
            app_master.add_handler(CommandHandler("listar", listar_clientes))

            app_master.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receber_mensagem_email))

            print("🤖 Bot Master e Sistema Multi-Bot rodando com motor de busca original e botões atualizados!")
            app_master.run_polling(poll_interval=1.0, timeout=20)
        except Exception as e:
            logging.error(f"Ocorreu uma queda temporária no Bot: {e}. Reconectando em 5 segundos...")
            time.sleep(5)

if __name__ == "__main__":
    main()
