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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

TOKEN_TELEGRAM = os.environ.get("TOKEN_TELEGRAM", "SEU_TOKEN_AQUI").strip()
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
        "permitir_sensivel": True
    },
    "6035184245": {
        "emails_permitidos": {"*": "2027-01-16"},
        "permitir_sensivel": True
    }
}

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
    """Converte qualquer string de data (ISO, YYYY-MM-DD, DD-MM-YYYY) para um objeto date."""
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
    """Converte a data para o formato visual DD-MM-AAAA."""
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
    return "Bot de Códigos Online"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

# ==========================================
# BANCO DE DADOS E PERSISTÊNCIA
# ==========================================
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
                    dados[t_id] = {
                        "emails_permitidos": emails,
                        "permitir_sensivel": permitir
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
                "permitir_sensivel": info_fixa.get("permitir_sensivel", True)
            }

    return dados

def salvar_clientes_db(dados: dict):
    try:
        caminho_tmp = "clientes.json.tmp"
        with open(caminho_tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace(caminho_tmp, "clientes.json")
    except Exception as e:
        logging.error(f"Erro ao salvar clientes.json local: {e}")

    if supabase:
        try:
            for telegram_id, info in dados.items():
                payload = {
                    "telegram_id": str(telegram_id).strip(),
                    "emails_permitidos": info.get("emails_permitidos", {}),
                    "permitir_sensivel": bool(info.get("permitir_sensivel", False)),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                supabase.table("clientes_bot").upsert(payload).execute()
        except Exception as e:
            logging.error(f"Erro ao salvar no Supabase: {e}")

def carregar_json(caminho: str) -> dict:
    if caminho == "clientes.json":
        return carregar_clientes_db()

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

    try:
        caminho_tmp = f"{caminho}.tmp"
        with open(caminho_tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        os.replace(caminho_tmp, caminho)
    except Exception as e:
        logging.error(f"Erro ao salvar {caminho}: {e}")

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
    """
    Procura links de botões de ação na Netflix (Residência, Viagem, Receber Código, Redefinição).
    Retorna (link, tipo) ou (None, None).
    """
    if not corpo_html:
        return None, None

    bloqueadas = ["unsubscribe", "help.netflix.com", "privacy", "terms", "facebook", "twitter", "instagram"]
    anchors = extrair_anchors_html(corpo_html)

    # 1. Pelo texto do BOTÃO (Anchor text)
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

    # 2. Pela estrutura da URL
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

    for href, text in anchors:
        link_lower = href.lower()
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

            # 1. DISNEY+: CÓDIGOS DE 6 DÍGITOS
            if eh_disney:
                codigos = re.findall(r'\b\d{6}\b', corpo_texto_puro)
                if not codigos:
                    codigos = re.findall(r'\b\d{4,6}\b', corpo_texto_puro)
                codigo_valido = next((c for c in codigos if not c.startswith("0800") and c != "2026"), None)
                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código Disney+ Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            # 2. NETFLIX: TRATAMENTO COMPLETO E FLEXÍVEL
            if eh_netflix:
                # PASSO 1: VERIFICAR SE EXISTE UM BOTÃO/LINK DE AÇÃO NO E-MAIL (Residência, Viagem, Receber Código, Redefinição)
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

                # PASSO 2: SE NÃO HOUVER BOTÃO DE AÇÃO, PROCURAR CÓDIGO NUMÉRICO IMPRESSO NO TEXTO
                texto_limpo = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '', corpo_texto_puro)
                texto_limpo = re.sub(r'\b\d{1,2}\s+de\s+[a-zç]+\b', '', texto_limpo, flags=re.IGNORECASE)

                codigos = re.findall(r'\b\d{4,6}\b', texto_limpo)
                codigo_valido = next((c for c in codigos if c not in ["2024", "2025", "2026", "2027", "1056"] and not c.startswith("0800")), None)

                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código de Acesso Netflix Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

                # PASSO 3: FALLBACK DE LINK GENÉRICO
                link_fallback = extrair_url_pura_href(corpo_html, "netflix.com")
                if link_fallback and not any(x in link_fallback for x in ["unsubscribe", "help", "privacy", "terms"]):
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ <b>Link Netflix Encontrado!</b>\n\n"
                        f"👉 <a href=\"{link_fallback}\">Clique aqui para acessar a solicitação da Netflix</a>\n\n"
                        f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                    )

            # 3. CÓDIGO NUMÉRICO DE ENTRADA / ACESSO GERAL
            eh_email_codigo = any(p in assunto_lower for p in ["código", "codigo", "temporário", "temporario", "autorização", "autorizacao", "verificação", "verificacao"])
            if eh_email_codigo:
                texto_limpo = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', '', corpo_texto_puro)
                codigos = re.findall(r'\b\d{4,6}\b', texto_limpo)
                codigo_valido = next((c for c in codigos if not c.startswith("0800") and c not in ["2024", "2025", "2026", "2027"]), None)
                if codigo_valido:
                    mail.close()
                    mail.logout()
                    return f"✅ <b>Código Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            # 4. SEGURANÇA VIP OUTROS STREAMINGS (REDEFINIÇÃO DE SENHA)
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

            # 5. CASO GENÉRICO DE FALLBACK DE CÓDIGO
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
# COMANDOS E RESPOSTAS DO BOT TELEGRAM
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    
    keyboard = [
        [InlineKeyboardButton("📖 Como Usar / Tutorial", callback_data="tutorial")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 <b>Central de Liberação de Códigos</b>\n\n"
        f"🆔 <b>Seu ID do Telegram:</b> <code>{user_id}</code>\n\n"
        f"Envie o e-mail cadastrado abaixo para buscar códigos/links recentes.\n\n"
        f"<i>Dúvidas de como usar? Clique no botão abaixo 👇</i>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

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

async def autorizar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    try:
        user_id_cliente = str(context.args[0]).strip()
        email_cliente_raw = context.args[1].strip().lower()
        arg_terceiro = str(context.args[2]).strip()

        liberar_sensivel = False
        if len(context.args) > 3 and context.args[3].lower() in ["vip", "sensivel", "true", "todos", "sim"]:
            liberar_sensivel = True

        hoje = datetime.now().date()

        if arg_terceiro.isdigit():
            dias = int(arg_terceiro)
            dt_expira = hoje + timedelta(days=dias)
        else:
            exp_date = converter_para_date(arg_terceiro)
            if not exp_date:
                await update.message.reply_text(
                    "⚠️ <b>Validade inválida!</b> Use número de dias (ex: <code>30</code>) ou data (ex: <code>05-11-2026</code>).",
                    parse_mode="HTML"
                )
                return
            dt_expira = exp_date
            dias = (dt_expira - hoje).days

        data_validade_banco = dt_expira.strftime("%Y-%m-%d")
        data_validade_exibicao = dt_expira.strftime("%d-%m-%Y")

        clientes = carregar_json("clientes.json")

        if user_id_cliente not in clientes:
            clientes[user_id_cliente] = {
                "emails_permitidos": {},
                "permitir_sensivel": liberar_sensivel
            }
        else:
            if liberar_sensivel:
                clientes[user_id_cliente]["permitir_sensivel"] = True

        if "emails_permitidos" not in clientes[user_id_cliente]:
            clientes[user_id_cliente]["emails_permitidos"] = {}

        clientes[user_id_cliente]["emails_permitidos"][email_cliente_raw] = data_validade_banco

        salvar_json("clientes.json", clientes)

        msg_vip = " (Acesso Total + VIP)" if clientes[user_id_cliente].get("permitir_sensivel") else ""
        await update.message.reply_text(
            f"✅ <b>Acesso Concedido com Sucesso!</b>\n\n"
            f"👤 <b>ID Cliente:</b> <code>{user_id_cliente}</code>\n"
            f"📧 <b>E-mail Liberado:</b> <code>{email_cliente_raw}</code>\n"
            f"📅 <b>Válido até:</b> {data_validade_exibicao} ({dias} dias){msg_vip}\n\n"
            f"⚠️ <b>Atenção:</b> Apenas a grafia exata (<code>{email_cliente_raw}</code>) terá acesso liberado.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Erro ao autorizar cliente: {e}")
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/autorizar ID_CLIENTE EMAIL DIAS_OU_DATA [vip]</code>\n\n"
            "Exemplos:\n"
            "• <code>/autorizar 7219528497 email@gmail.com 30</code>\n"
            "• <code>/autorizar 7219528497 email@gmail.com 05-11-2026</code>",
            parse_mode="HTML"
        )

async def vip_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /vip <telegram_id> sim/nao para liberar busca ilimitada de e-mails."""
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/vip ID_CLIENTE sim/nao</code>", parse_mode="HTML")
        return

    target_id = str(context.args[0]).strip()
    opcao = str(context.args[1]).strip().lower()
    status_vip = opcao in ["sim", "s", "true", "1", "vip", "on"]

    clientes = carregar_json("clientes.json")
    if target_id not in clientes:
        clientes[target_id] = {"emails_permitidos": {}, "permitir_sensivel": status_vip}
    else:
        clientes[target_id]["permitir_sensivel"] = status_vip

    salvar_json("clientes.json", clientes)

    txt_status = "ATIVADO ⭐️\nEste Telegram ID agora possui **busca ilimitada** para qualquer e-mail digitado!" if status_vip else "DESATIVADO\nEste Telegram ID voltou a depender de e-mails específicos autorizados."
    await update.message.reply_text(
        f"👑 <b>Status VIP do ID <code>{target_id}</code>:</b> {txt_status}",
        parse_mode="HTML"
    )

async def editar_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /editar <telegram_id> <email> <nova_data_DD-MM-AAAA ou sem_data>."""
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/editar ID_CLIENTE EMAIL DD-MM-AAAA</code> ou <code>/editar ID_CLIENTE EMAIL sem_data</code>",
            parse_mode="HTML"
        )
        return

    target_id = str(context.args[0]).strip()
    target_email = str(context.args[1]).strip().lower()
    nova_opcao = str(context.args[2]).strip().lower()

    clientes = carregar_json("clientes.json")
    if target_id not in clientes or target_email not in clientes[target_id].get("emails_permitidos", {}):
        await update.message.reply_text(f"❌ O e-mail <code>{target_email}</code> não está cadastrado para o ID <code>{target_id}</code>.", parse_mode="HTML")
        return

    if nova_opcao in ["sem_data", "semdata", "none", "ilimitado", "0"]:
        clientes[target_id]["emails_permitidos"][target_email] = "2030-12-31"
        data_exib = "Sem expiração (Ilimitado)"
    else:
        exp_date = converter_para_date(nova_opcao)
        if not exp_date:
            await update.message.reply_text("⚠️ Data inválida! Use o formato <b>DD-MM-AAAA</b>.", parse_mode="HTML")
            return
        data_salvar = exp_date.strftime("%Y-%m-%d")
        data_exib = exp_date.strftime("%d-%m-%Y")
        clientes[target_id]["emails_permitidos"][target_email] = data_salvar

    salvar_json("clientes.json", clientes)
    await update.message.reply_text(
        f"✏️ <b>Data atualizada com sucesso!</b>\n\n👤 <b>ID:</b> <code>{target_id}</code>\n📧 <b>E-mail:</b> <code>{target_email}</code>\n📅 <b>Nova Validade:</b> {data_exib}",
        parse_mode="HTML"
    )

async def remover_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /remover <telegram_id> <email>."""
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ <b>Uso correto:</b> <code>/remover ID_CLIENTE EMAIL</code>", parse_mode="HTML")
        return

    target_id = str(context.args[0]).strip()
    target_email = str(context.args[1]).strip().lower()

    clientes = carregar_json("clientes.json")
    if target_id in clientes and target_email in clientes[target_id].get("emails_permitidos", {}):
        del clientes[target_id]["emails_permitidos"][target_email]
        salvar_json("clientes.json", clientes)
        await update.message.reply_text(
            f"🗑️ E-mail <code>{target_email}</code> <b>removido com sucesso</b> do ID <code>{target_id}</code>!",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"❌ O e-mail <code>{target_email}</code> não foi localizado no cadastro do ID <code>{target_id}</code>.",
            parse_mode="HTML"
        )

async def limpar_nao_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    clientes = carregar_json("clientes.json")
    hoje = datetime.now().date()
    emails_removidos = 0

    for user_id, dados in list(clientes.items()):
        emails = dados.get("emails_permitidos", {})
        para_remover = []
        for email_end, data_exp in emails.items():
            if email_end != "*":
                exp_date = converter_para_date(data_exp)
                if exp_date and exp_date < hoje:
                    para_remover.append(email_end)
        for email_vencido in para_remover:
            del dados["emails_permitidos"][email_vencido]
            emails_removidos += 1

    salvar_json("clientes.json", clientes)

    await update.message.reply_text(
        f"🧹 <b>Limpeza Inteligente Concluída!</b>\n\n"
        f"Foram removidos <b>{emails_removidos}</b> e-mail(s) com data de validade vencida.\n\n"
        f"Todos os acessos de clientes ainda válidos foram 100% preservados!",
        parse_mode="HTML"
    )

async def listar_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    clientes = carregar_json("clientes.json")
    if not clientes:
        await update.message.reply_text("📋 <b>Nenhum cliente cadastrado no momento.</b>", parse_mode="HTML")
        return

    if context.args:
        target_id = str(context.args[0]).strip()
        if target_id not in clientes:
            await update.message.reply_text(f"❌ Nenhum registro encontrado para o ID <code>{target_id}</code>.", parse_mode="HTML")
            return
        lista_filtrada = {target_id: clientes[target_id]}
    else:
        lista_filtrada = clientes

    hoje = datetime.now().date()
    mensagem = "📋 <b>Lista de Clientes Liberados:</b>\n\n"

    for id_cliente, dados in lista_filtrada.items():
        emails = dados.get("emails_permitidos", {})
        vip = "Sim ⭐️" if dados.get("permitir_sensivel") else "Não"
        mensagem += f"👤 <b>ID:</b> <code>{id_cliente}</code> | <b>VIP:</b> {vip}\n"
        if not emails:
            mensagem += " └ ⚠️ Nenhum e-mail liberado.\n"
        else:
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
            await update.message.reply_text(mensagem[x:x+4000], parse_mode="HTML")
    else:
        await update.message.reply_text(mensagem, parse_mode="HTML")

async def receber_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    email_original = update.message.text.strip().lower()

    clientes = carregar_json("clientes.json")
    eh_admin = (user_id == str(ADMIN_ID).strip())
    dados_cliente = clientes.get(user_id, {})

    if not eh_admin and (not dados_cliente or not dados_cliente.get("emails_permitidos")) and not dados_cliente.get("permitir_sensivel"):
        await update.message.reply_text(
            f"🔒 <b>Acesso Não Liberado!</b>\n\nSeu ID de Usuário: <code>{user_id}</code>\n\nEncaminhe ao Suporte para liberação.",
            parse_mode="HTML"
        )
        return

    if not re.match(r"^[\w\.\+-]+@[\w\.-]+\.\w+$", email_original):
        await update.message.reply_text("⚠️ Por favor, digite um <b>endereço de e-mail válido</b>.", parse_mode="HTML")
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
                    f"🛑 <b>Acesso Expirado!</b>\n\nA autorização para o e-mail <code>{email_original}</code> já venceu. Entre em contato com o suporte para renovar.",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    f"❌ Você não tem autorização para acessar o e-mail <code>{email_original}</code>.",
                    parse_mode="HTML"
                )
            return

    email_base = normalizar_email_gmail(email_original)
    contas = carregar_json("contas.json")
    conta_encontrada = None
    
    if isinstance(contas, dict):
        for chave_email, dados in contas.items():
            chave_norm = normalizar_email_gmail(chave_email)
            if chave_email.strip().lower() == email_original or chave_norm == email_base:
                conta_encontrada = dados if isinstance(dados, dict) else {"senha": dados}
                break

    if not conta_encontrada:
        await update.message.reply_text(
            f"❌ A conta <code>{email_original}</code> não possui as credenciais cadastradas no <code>contas.json</code>.",
            parse_mode="HTML"
        )
        return

    email_login = conta_encontrada.get("email_login", email_base)

    msg_carregando = await update.message.reply_text(f"⏳ Buscando e-mail recente para <code>{email_original}</code>...", parse_mode="HTML")

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
# EXECUÇÃO DO BOT
# ==========================================
def main():
    Thread(target=run_flask, daemon=True).start()

    while True:
        try:
            app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("autorizar", autorizar_cliente))
            app.add_handler(CommandHandler("editar", editar_comando))
            app.add_handler(CommandHandler("remover", remover_comando))
            app.add_handler(CommandHandler("vip", vip_comando))
            app.add_handler(CommandHandler("limpar", limpar_nao_vip))
            app.add_handler(CommandHandler("limpar_nao_vip", limpar_nao_vip))
            app.add_handler(CommandHandler("listar", listar_clientes))
            
            app.add_handler(CallbackQueryHandler(tutorial_callback))
            
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_mensagem_email))

            print("🤖 Bot iniciado e rodando no Render com prioridade para links de botões da Netflix...")
            app.run_polling(poll_interval=1.0, timeout=20)
        except Exception as e:
            logging.error(f"Ocorreu uma queda temporária no Bot: {e}. Reconectando em 5 segundos...")
            time.sleep(5)

if __name__ == "__main__":
    main()
