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

TOKEN_TELEGRAM = "8621009761:AAF3vIBd5--2FDxSJsDCJSpGYcfSf64tpjc"
ADMIN_ID = 7496198484  # Substitua pelo seu ID numérico do Telegram

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos 100% Ativo e Online"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

# --- Gerenciamento JSON ---

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
    partes = email_str.strip().lower().split('@')
    if len(partes) != 2:
        return email_str.strip().lower()
    usuario, dominio = partes
    if dominio == "gmail.com":
        usuario = usuario.split('+')[0]
        usuario = usuario.replace(".", "")
        return f"{usuario}@{dominio}"
    return email_str.strip().lower()

def limpar_e_decodificar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto_decodificado = html.unescape(texto)
    texto_limpo = re.sub(r'=\r?\n', '', texto_decodificado)
    return texto_limpo

# --- Leitura IMAP de Alta Estabilidade ---

def extrair_codigo_imap_wrapper(dados_conta: dict, pode_acessar_sensivel: bool = False) -> str:
    email_usuario = dados_conta.get("email_usuario")
    host = dados_conta.get("host_imap", "imap.gmail.com")
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap")
    email_solicitado = dados_conta.get("email_destinatario", "").lower()

    mail = None
    try:
        socket.setdefaulttimeout(12)
        mail = imaplib.IMAP4_SSL(host, porta)
        mail.login(email_usuario, senha)
        
        mail.select("INBOX")

        status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages[0]:
            mail.close()
            mail.logout()
            return "❌ Nenhum e-mail foi encontrado nesta caixa de entrada."

        id_list = messages[0].split()
        ultimos_ids = id_list[-10:]
        ultimos_ids.reverse()

        for msg_id in ultimos_ids:
            _, data = mail.fetch(msg_id, "(RFC822)")
            if not data or not data[0]:
                continue
                
            msg = email.message_from_bytes(data[0][1])

            data_email_header = msg.get("Date")
            if not data_email_header:
                continue

            data_email = parsedate_to_datetime(data_email_header)
            agora = datetime.now(timezone.utc)
            diferenca_tempo = agora - data_email

            if diferenca_tempo > timedelta(minutes=15):
                continue

            assunto = str(msg.get("Subject", "")).lower()

            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if "attachment" not in content_disposition:
                        if content_type in ["text/plain", "text/html"]:
                            payload = part.get_payload(decode=True)
                            if payload:
                                corpo += payload.decode(errors="ignore") + "\n"
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    corpo = payload.decode(errors="ignore")

            corpo_processado = limpar_e_decodificar_texto(corpo)
            texto_analise = (assunto + " " + corpo_processado).lower()

            if email_solicitado and email_solicitado not in email_usuario:
                if email_solicitado not in texto_analise and email_solicitado not in str(msg.get("To", "")).lower():
                    continue

            # --- VERIFICAÇÃO DE SEGURANÇA PARA CLIENTES ---
            if not pode_acessar_sensivel:
                termos_proibidos = [
                    "redefinir sua senha", "redefinir a sua senha", "reset password", 
                    "alterar sua senha", "alterar o e-mail", "alterar email",
                    "troca de e-mail", "troca de email", "change email",
                    "solicitação de redefinição", "atualize seu e-mail", "recuperação de conta",
                    "recuperacaosenha"
                ]

                for termo in termos_proibidos:
                    if termo in texto_analise:
                        mail.close()
                        mail.logout()
                        return (
                            "🚫 **Solicitação Não Permitida!**\n\n"
                            "Este e-mail trata-se de uma alteração de senha ou mudança de e-mail da conta.\n"
                            "Por motivos de segurança, esses links/códigos não são exibidos para o seu usuário."
                        )

            # 1. PRIORIDADE 1: Links de Redefinição Direta (Globo, etc.)
            match_globo = re.search(r'https://login\.globo\.com/recuperacaoSenha/[^\s<>"\'\);]+', corpo_processado, re.IGNORECASE)
            match_reset_especifico = re.search(r'https?://[^\s<>"\'\);]+(?:recuperacaosenha|reset-password|password-reset)[^\s<>"\'\);]*', corpo_processado, re.IGNORECASE)

            if match_globo:
                mail.close()
                mail.logout()
                link_limpo = match_globo.group(0).rstrip('.,;)')
                return (
                    f"✅ **Link de Redefinição Globo Encontrado!**\n\n"
                    f"🔗 Clique no link abaixo para criar a nova senha:\n{link_limpo}\n\n"
                    f"⏱️ *E-mail recebido há {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
                )
            elif match_reset_especifico:
                mail.close()
                mail.logout()
                link_limpo = match_reset_especifico.group(0).rstrip('.,;)')
                return (
                    f"✅ **Link de Redefinição Encontrado!**\n\n"
                    f"🔗 Clique no link abaixo para alterar a senha:\n{link_limpo}\n\n"
                    f"⏱️ *E-mail recebido há {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
                )

            # 2. PRIORIDADE 2: Código numérico de 4 a 8 dígitos (Netflix, Disney, etc.)
            match_codigo = re.search(r'\b\d{4,8}\b', corpo_processado)
            if match_codigo:
                mail.close()
                mail.logout()
                return (
                    f"✅ **Código Encontrado!**\n\n"
                    f"🔑 Seu código é: `{match_codigo.group(0)}`\n\n"
                    f"⏱️ *E-mail recebido há {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
                )

            # 3. PRIORIDADE 3: Links genéricos de acesso
            match_link_geral = re.search(r'https?://[^\s<>"\'\);]+(?:update-primary-location|confirm|verify|household|auth|code)[^\s<>"\'\);]*', corpo_processado, re.IGNORECASE)
            if match_link_geral:
                mail.close()
                mail.logout()
                link_limpo = match_link_geral.group(0).rstrip('.,;)')
                return (
                    f"✅ **Link de Validação/Acesso Encontrado!**\n\n"
                    f"🔗 Clique no link abaixo para acessar:\n{link_limpo}\n\n"
                    f"⏱️ *E-mail recebido há {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
                )

        mail.close()
        mail.logout()
        return "⚠️ Nenhum e-mail com código ou link recebido nos últimos 15 minutos foi localizado nesta caixa."

    except socket.timeout:
        return "❌ O servidor de e-mail demorou muito para responder (Timeout). Tente novamente."
    except Exception as e:
        logging.error(f"Erro IMAP: {e}")
        return "❌ Erro de conexão com a caixa de e-mail. Tente novamente em instantes."
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
    await update.message.reply_text(
        f"👋 **Central de Liberação de Códigos**\n\n"
        f"Seu ID do Telegram: `{user_id}`\n\n"
        f"Envie o **e-mail do seu login** para buscar o código/link recebido nos últimos 15 minutos.",
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
        if len(context.args) > 3 and context.args[3].lower() in ["vip", "sensivel", "true", "todos"]:
            liberar_sensivel = True

        clientes = carregar_json("clientes.json")
        data_validade = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")

        if user_id_cliente not in clientes:
            clientes[user_id_cliente] = {
                "emails_permitidos": {},
                "permitir_sensivel": liberar_sensivel
            }
        else:
            clientes[user_id_cliente]["permitir_sensivel"] = liberar_sensivel

        clientes[user_id_cliente]["emails_permitidos"][email_cliente] = data_validade
        salvar_json("clientes.json", clientes)

        msg_vip = " (Acesso Total + Links de Redefinição)" if liberar_sensivel else ""
        await update.message.reply_text(
            f"✅ **Acesso Concedido!**\n\n"
            f"👤 **ID Cliente:** `{user_id_cliente}`\n"
            f"📧 **E-mail:** `{email_cliente}`\n"
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
    email_original = update.message.text.strip().lower()

    if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email_original):
        await update.message.reply_text(
            "⚠️ Por favor, digite um **endereço de e-mail válido**.",
            parse_mode="Markdown"
        )
        return

    clientes = carregar_json("clientes.json")
    dados_cliente = clientes.get(user_id, {})
    eh_admin = (user_id == str(ADMIN_ID))
    
    pode_acessar_sensivel = eh_admin or dados_cliente.get("permitir_sensivel", False)

    if not eh_admin:
        emails_permitidos = dados_cliente.get("emails_permitidos", {})
        tem_acesso = "*" in emails_permitidos or email_original in emails_permitidos

        if not dados_cliente or not tem_acesso:
            await update.message.reply_text(
                f"❌ Você não tem autorização para acessar os códigos do e-mail `{email_original}`.",
                parse_mode="Markdown"
            )
            return

        data_expiracao_str = emails_permitidos.get(email_original) or emails_permitidos.get("*")
        data_expiracao = datetime.strptime(data_expiracao_str, "%Y-%m-%d").date()

        if datetime.now().date() > data_expiracao:
            await update.message.reply_text(
                f"⚠️ **Assinatura Expirada!** Venceu em **{data_expiracao_str}**.",
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
    resultado = await loop.run_in_executor(None, extrair_codigo_imap_wrapper, dados_completos, pode_acessar_sensivel)

    await msg_carregando.edit_text(resultado, parse_mode="Markdown")

def main():
    Thread(target=run_flask, daemon=True).start()

    # Loop Infinito com Auto-Reconexão em caso de desconexão de rede
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
