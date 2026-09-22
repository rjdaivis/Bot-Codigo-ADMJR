import json
import logging
import re
import imaplib
import email
import os
import socket
import asyncio
import html
import quopri
import time
from threading import Thread
from flask import Flask
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM = os.environ.get("TOKEN_TELEGRAM", "SEU_TOKEN_AQUI")
ADMIN_ID = 7496198484  # Substitua pelo seu ID numérico do Telegram

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos 100% Ativo e Online"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

# --- Gerenciamento JSON com Persistência Reforçada ---

def carregar_json(caminho: str) -> dict:
    try:
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Erro ao carregar {caminho}: {e}")
        return {}

def salvar_json(caminho: str, dados: dict):
    try:
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Erro ao salvar {caminho}: {e}")

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

def tratar_quopri_e_html(payload_bytes: bytes) -> str:
    if not payload_bytes:
        return ""
    try:
        payload_decodificado = quopri.decodestring(payload_bytes).decode('utf-8', errors='ignore')
    except Exception:
        payload_decodificado = payload_bytes.decode('utf-8', errors='ignore')

    texto_sem_quebras = re.sub(r'=\r?\n', '', payload_decodificado)
    texto_limpo = html.unescape(texto_sem_quebras)
    return texto_limpo

def extrair_apenas_texto_visivel(html_str: str) -> str:
    if not html_str:
        return ""
    texto = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r'<[^>]+>', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def limpar_link_url(url: str) -> str:
    if not url:
        return ""
    url_limpa = url.replace("&#x3D;", "=").replace("&amp;", "&")
    url_limpa = re.sub(r'#x3D;?$', '=', url_limpa, flags=re.IGNORECASE)
    url_limpa = re.sub(r'[\]\)\}\'"\>\.,;]+$', '', url_limpa)
    return url_limpa.strip()

# --- Extração IMAP Cronológica Unificada ---

def extrair_codigo_imap_wrapper(dados_conta: dict, pode_acessar_sensivel: bool = False) -> str:
    email_usuario = dados_conta.get("email_usuario")
    host = dados_conta.get("host_imap", "imap.gmail.com")
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap")
    email_solicitado = dados_conta.get("email_destinatario", "").lower()
    
    email_solicitado_norm = normalizar_email_gmail(email_solicitado)
    email_usuario_norm = normalizar_email_gmail(email_usuario)

    mail = None
    try:
        socket.setdefaulttimeout(10)
        mail = imaplib.IMAP4_SSL(host, porta)
        mail.login(email_usuario, senha)
        
        mail.select("INBOX")

        status, messages = mail.search(None, "ALL")

        if status != "OK" or not messages[0]:
            mail.close()
            mail.logout()
            return "⚠️ Nenhum e-mail encontrado na caixa de entrada."

        id_list = messages[0].split()
        ultimos_ids = id_list[-10:]
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

            # Tolerância estrita de 15 minutos
            if diferenca_segundos > 1200 or diferenca_segundos < -300:
                continue

            assunto = str(msg.get("Subject", "")).lower()
            remetente = str(msg.get("From", "")).lower()

            corpo_bruto = b""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if "attachment" not in content_disposition:
                        if content_type in ["text/plain", "text/html"]:
                            payload = part.get_payload(decode=True)
                            if payload:
                                corpo_bruto += payload + b"\n"
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    corpo_bruto = payload

            corpo_html = tratar_quopri_e_html(corpo_bruto)
            corpo_texto_puro = extrair_apenas_texto_visivel(corpo_html)
            
            texto_completo = (assunto + " " + remetente + " " + corpo_texto_puro).lower()
            destinatario_to = normalizar_email_gmail(str(msg.get("To", "")))

            if email_solicitado_norm and email_solicitado_norm != email_usuario_norm:
                if email_solicitado_norm not in normalizar_email_gmail(texto_completo) and email_solicitado_norm not in destinatario_to:
                    continue

            if any(termo in assunto for termo in ["novo login", "alerta de segurança", "dispositivo conectado", "new login"]):
                continue

            minutos = max(1, int(diferenca_segundos // 60)) if diferenca_segundos > 0 else 1

            # 1. CÓDIGOS NUMÉRICOS (HBO MAX, NETFLIX, DISNEY, GLOBO)
            match_codigo_contexto = re.search(
                r'(?:seu código único|seu código de acesso único|informe este código para entrar|informe o código abaixo|informe este código|use esse código de acesso|seu código de acesso|código único|código de verificação|seu código é|código:)[^\d]{1,100}(\d{4,8})\b', 
                corpo_texto_puro, 
                re.IGNORECASE
            )

            match_codigo_solto = None
            if any(k in remetente or k in assunto for k in ["hbomax", "max", "netflix", "disney", "globo"]):
                match_codigo_solto = re.search(r'\b(?!(?:19|20)\d{2}\b)\d{4,6}\b', corpo_texto_puro)

            codigo_encontrado = None
            if match_codigo_contexto:
                codigo_encontrado = match_codigo_contexto.group(1)
            elif match_codigo_solto and any(k in assunto for k in ["código", "code", "entrar", "acesso", "temporário"]):
                codigo_encontrado = match_codigo_solto.group(0)

            if codigo_encontrado:
                mail.close()
                mail.logout()
                return (
                    f"✅ **Código Encontrado!**\n\n"
                    f"🔑 Seu código é: `{codigo_encontrado}`\n\n"
                    f"⏱️ *E-mail recebido há {minutos} minuto(s).*"
                )

            # 2. NETFLIX - ATUALIZAÇÃO DE RESIDÊNCIA / "SIM, FUI EU"
            match_netflix_residencia = re.search(
                r'https?://[^\s<>"\'\]\);]+netflix\.com[^\s<>"\'\]\);]*(?:travel|update-primary-location|verify|household|confirm|code|accountaccess)[^\s<>"\'\]\);]*', 
                corpo_html, 
                re.IGNORECASE
            )

            eh_email_residencia = (
                "atualizar a residência" in assunto or 
                "atualizar a residência" in corpo_texto_puro.lower() or 
                "sim, fui eu" in corpo_texto_puro.lower() or
                "solicitação para atualizar a residência netflix" in corpo_texto_puro.lower()
            )

            if match_netflix_residencia or eh_email_residencia:
                if match_netflix_residencia:
                    link_limpo = limpar_link_url(match_netflix_residencia.group(0))
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ **Link de Atualização de Residência Netflix Encontrado!**\n\n"
                        f"🔗 Clique no link abaixo para confirmar:\n{link_limpo}\n\n"
                        f"⏱️ *E-mail recebido há {minutos} minuto(s).*"
                    )

            # 3. LINK DE REDEFINIÇÃO DE SENHA (VIP REQUERIDO)
            match_redefinicao = (
                re.search(r'https?://[^\s<>"\'\]\);]+netflix\.com[^\s<>"\'\]\);]*(?:password|reset-password)[^\s<>"\'\]\);]*', corpo_html, re.IGNORECASE) or
                re.search(r'https?://[^\s<>"\'\]\);]*(?:hbomax\.com|max\.com)[^\s<>"\'\]\);]*(?:reset-password|password|token|reset|recover|alteracao)[^\s<>"\'\]\);]*', corpo_html, re.IGNORECASE) or
                re.search(r'https?://[^\s<>"\'\]\);]*globo\.com[^\s<>"\'\]\);]*(?:recuperacaoSenha|recuperacao|senha|login|token)[^\s<>"\'\]\);]*', corpo_html, re.IGNORECASE) or
                re.search(r'https?://[^\s<>"\'\]\);]*paramountplus\.com[^\s<>"\'\]\);]*(?:reset-password|password|token|reset)[^\s<>"\'\]\);]*', corpo_html, re.IGNORECASE) or
                re.search(r'https?://[^\s<>"\'\]\);]+(?:recuperacaosenha|reset-password|password-reset)[^\s<>"\'\]\);]*', corpo_html, re.IGNORECASE)
            )

            is_assunto_redefinicao = any(t in assunto for t in ["redefinição de senha", "redefinir sua senha", "recuperar senha", "reset password", "recuperacaosenha"])

            if is_assunto_redefinicao or match_redefinicao:
                if not pode_acessar_sensivel:
                    mail.close()
                    mail.logout()
                    return "🚫 **Solicitação Não Permitida!**\n\nO seu usuário não possui permissão VIP para visualizar links de redefinição de senha."

                if match_redefinicao:
                    link_limpo = limpar_link_url(match_redefinicao.group(0))
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ **Link de Redefinição de Senha Encontrado!**\n\n"
                        f"🔗 Clique no link abaixo para redefinir:\n{link_limpo}\n\n"
                        f"⏱️ *E-mail recebido há {minutos} minuto(s).*"
                    )

        mail.close()
        mail.logout()
        return "⚠️ Nenhum e-mail recente (últimos 15 minutos) com código ou link válido foi localizado nesta caixa."

    except socket.timeout:
        return "❌ O servidor de e-mail demorou muito para responder (Timeout). Tente novamente em instantes."
    except Exception as e:
        logging.error(f"Erro IMAP: {e}")
        return "❌ Erro de conexão com a caixa de e-mail. Verifique as credenciais no contas.json."
    finally:
        try:
            if mail:
                mail.logout()
        except Exception:
            pass

# --- Handlers Telegram ---

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    clientes = carregar_json("clientes.json")

    if user_id not in clientes and user_id != str(ADMIN_ID):
        clientes[user_id] = {
            "emails_permitidos": {},
            "permitir_sensivel": False
        }
        salvar_json("clientes.json", clientes)

    await update.message.reply_text(
        f"👋 **Central de Liberação de Códigos**\n\n"
        f"🆔 **Seu ID do Telegram:** `{user_id}`\n\n"
        f"📍 **Se for o seu primeiro acesso:**\n"
        f"Envie o seu ID (`{user_id}`) ao **Suporte ADM JR** para liberar a sua assinatura no sistema.\n\n"
        f"✉️ **Já possui assinatura ativa?**\n"
        f"Basta digitar o **e-mail do seu login** abaixo para buscar o código/link recebido nos últimos 15 minutos.",
        parse_mode="Markdown"
    )

async def autorizar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id_cliente = str(context.args[0])
        email_cliente = context.args[1].strip().lower()
        dias = int(context.args[2])
        
        liberar_sensivel = False
        if len(context.args) > 3 and context.args[3].lower() in ["vip", "sensivel", "true", "todos", "sim"]:
            liberar_sensivel = True

        clientes = carregar_json("clientes.json")
        data_validade = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")

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

        clientes[user_id_cliente]["emails_permitidos"][email_cliente] = data_validade
        salvar_json("clientes.json", clientes)

        msg_vip = " (Acesso Total + Links de Redefinição)" if clientes[user_id_cliente].get("permitir_sensivel") else ""
        await update.message.reply_text(
            f"✅ **Acesso Concedido!**\n\n"
            f"👤 **ID Cliente:** `{user_id_cliente}`\n"
            f"📧 **E-mail Liberado:** `{email_cliente}`\n"
            f"📅 **Válido até:** {data_validade} ({dias} dias){msg_vip}",
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "⚠️ **Uso correto:** `/autorizar ID_CLIENTE EMAIL DIAS [vip]`",
            parse_mode="Markdown"
        )

async def receber_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    texto_recebido = update.message.text.strip()
    email_original = texto_recebido.lower()

    clientes = carregar_json("clientes.json")
    eh_admin = (user_id == str(ADMIN_ID))

    dados_cliente = clientes.get(user_id, {})
    if not eh_admin and (not dados_cliente or not dados_cliente.get("emails_permitidos")):
        if user_id not in clientes:
            clientes[user_id] = {
                "emails_permitidos": {},
                "permitir_sensivel": False
            }
            salvar_json("clientes.json", clientes)

        await update.message.reply_text(
            f"🔒 **Acesso Não Liberado!**\n\n"
            f"Identificamos que você ainda não possui uma assinatura ativa no bot.\n\n"
            f"👤 **Seu ID de Usuário:** `{user_id}`\n\n"
            f"👉 Encaminhe o seu ID acima ao **Suporte ADM JR** para realizar o seu cadastro e solicitar a liberação do sistema.",
            parse_mode="Markdown"
        )
        return

    if not re.match(r"^[\w\.\+-]+@[\w\.-]+\.\w+$", email_original):
        await update.message.reply_text(
            "⚠️ Por favor, digite um **endereço de e-mail válido**.",
            parse_mode="Markdown"
        )
        return

    pode_acessar_sensivel = eh_admin or dados_cliente.get("permitir_sensivel", False)

    if not eh_admin:
        emails_permitidos = dados_cliente.get("emails_permitidos", {})
        
        tem_acesso = "*" in emails_permitidos or email_original in emails_permitidos or normalizar_email_gmail(email_original) in [normalizar_email_gmail(e) for e in emails_permitidos.keys()]

        if not tem_acesso:
            await update.message.reply_text(
                f"❌ Você não tem autorização para acessar os códigos do e-mail `{email_original}`.\n\n"
                f"Solicite a liberação deste e-mail para o seu ID: `{user_id}` junto ao Suporte.",
                parse_mode="Markdown"
            )
            return

        data_expiracao_str = emails_permitidos.get(email_original) or emails_permitidos.get("*")
        if not data_expiracao_str:
            for k, v in emails_permitidos.items():
                if normalizar_email_gmail(k) == normalizar_email_gmail(email_original):
                    data_expiracao_str = v
                    break

        data_expiracao = datetime.strptime(data_expiracao_str, "%Y-%m-%d").date()

        if datetime.now().date() > data_expiracao:
            await update.message.reply_text(
                f"⚠️ **Assinatura Expirada!** Venceu em **{data_expiracao_str}**.\nEntre em contato com o Suporte para renovar.",
                parse_mode="Markdown"
            )
            return

    email_base = normalizar_email_gmail(email_original)
    contas = carregar_json("contas.json")
    
    conta_encontrada = None
    for chave_email, dados in contas.items():
        if chave_email.strip().lower() == email_original or normalizar_email_gmail(chave_email) == email_base:
            conta_encontrada = dados
            break

    if not conta_encontrada:
        await update.message.reply_text(
            f"❌ A conta `{email_original}` não possui as credenciais cadastrais no servidor.",
            parse_mode="Markdown"
        )
        return

    email_login = conta_encontrada.get("email_login", email_base)

    dados_completos = {
        **conta_encontrada,
        "email_usuario": email_login,
        "email_destinatario": email_original
    }

    msg_carregando = await update.message.reply_text(
        f"⏳ *Buscando link/código recente para:* `{email_original}`...\nAguarde alguns segundos.",
        parse_mode="Markdown"
    )

    loop = asyncio.get_running_loop()
    try:
        resultado = await asyncio.wait_for(
            loop.run_in_executor(None, extrair_codigo_imap_wrapper, dados_completos, pode_acessar_sensivel),
            timeout=15.0
        )
    except asyncio.TimeoutError:
        resultado = "❌ O servidor de e-mail demorou muito a responder. Tente novamente em instantes."

    await msg_carregando.edit_text(resultado, parse_mode="Markdown")

def main():
    Thread(target=run_flask, daemon=True).start()

    while True:
        try:
            app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("autorizar", autorizar_cliente))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_mensagem_email))

            print("🤖 Bot iniciado e rodando com alta estabilidade...")
            app.run_polling(poll_interval=1.0, timeout=20)
        except Exception as e:
            logging.error(f"Ocorreu uma queda temporária no Bot: {e}. Reconectando em 5 segundos...")
            time.sleep(5)

if __name__ == "__main__":
    main()
