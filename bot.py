import json
import logging
import re
import imaplib
import email
import os
from threading import Thread
from flask import Flask
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM = "8621009761:AAF3vIBd5--2FDxSJsDCJSpGYcfSf64tpjc"
ADMIN_ID = 7496198484  # Seu ID numérico

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos Ativo"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

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

def extrair_codigo_imap_wrapper(dados_conta: dict) -> str:
    email_usuario = dados_conta.get("email_usuario")
    host = dados_conta.get("host_imap", "imap.gmail.com")
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap")

    try:
        mail = imaplib.IMAP4_SSL(host, porta)
        mail.login(email_usuario, senha)
        
        # Seleciona [Gmail]/All Mail para pegar e-mails de qualquer aba/pasta
        status, _ = mail.select('"[Gmail]/Todos os emails"')
        if status != 'OK':
            mail.select('"[Gmail]/All Mail"')
        if status != 'OK':
            mail.select("INBOX")

        # Busca por todos os e-mails
        _, messages = mail.search(None, "ALL")
        id_list = messages[0].split()

        if not id_list:
            mail.logout()
            return "❌ Nenhum e-mail foi encontrado nesta caixa de entrada."

        # Analisa os últimos 5 e-mails recebidos para encontrar o da Netflix
        ultimos_ids = id_list[-5:]
        ultimos_ids.reverse()

        for msg_id in ultimos_ids:
            _, data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            # Verifica data do e-mail
            data_email_header = msg.get("Date")
            if not data_email_header:
                continue

            data_email = parsedate_to_datetime(data_email_header)
            agora = datetime.now(timezone.utc)
            diferenca_tempo = agora - data_email

            # Pula se o e-mail for mais antigo que 15 minutos
            if diferenca_tempo > timedelta(minutes=15):
                continue

            # Extração de corpo/texto
            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if "attachment" not in content_disposition:
                        if content_type in ["text/plain", "text/html"]:
                            corpo += part.get_payload(decode=True).decode(errors="ignore") + "\n"
            else:
                corpo = msg.get_payload(decode=True).decode(errors="ignore")

            # Busca código de 4 a 8 dígitos
            match_codigo = re.search(r'\b\d{4,8}\b', corpo)
            match_link = re.search(r'https?://[^\s<>"]+(?:update-primary-location|confirm|verify|household|login|account|auth|code)[^\s<>"]*', corpo, re.IGNORECASE)

            if match_codigo:
                mail.logout()
                return (
                    f"✅ **Código Encontrado!**\n\n"
                    f"🔑 Seu código é: `{match_codigo.group(0)}`\n\n"
                    f"⏱️ *E-mail recebido há {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
                )
            elif match_link:
                mail.logout()
                return (
                    f"✅ **Link de Validação Encontrado!**\n\n"
                    f"🔗 Clique no link abaixo para liberar:\n{match_link.group(0)}"
                )

        mail.logout()
        return "⚠️ Nenhum código da Netflix recebido nos últimos 15 minutos foi encontrado."

    except Exception as e:
        logging.error(f"Erro IMAP: {e}")
        return "❌ Erro ao acessar a caixa de e-mail. Verifique a Senha de Aplicativo no sistema."

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
        f"Envie o **e-mail do seu login** para buscar o código recebido nos últimos 15 minutos.",
        parse_mode="Markdown"
    )

async def autorizar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id_cliente = str(context.args[0])
        email_cliente = context.args[1].strip().lower()
        dias = int(context.args[2])

        clientes = carregar_json("clientes.json")
        data_validade = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")

        if user_id_cliente not in clientes:
            clientes[user_id_cliente] = {"emails_permitidos": {}}

        clientes[user_id_cliente]["emails_permitidos"][email_cliente] = data_validade
        salvar_json("clientes.json", clientes)

        await update.message.reply_text(
            f"✅ **Acesso Concedido!**\n\n"
            f"👤 **ID Cliente:** `{user_id_cliente}`\n"
            f"📧 **E-mail:** `{email_cliente}`\n"
            f"📅 **Válido até:** {data_validade} ({dias} dias)",
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "⚠️ **Uso correto:** `/autorizar ID_CLIENTE EMAIL DIAS`",
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
    dados_cliente = clientes.get(user_id)

    if user_id != str(ADMIN_ID):
        if not dados_cliente or email_original not in dados_cliente.get("emails_permitidos", {}):
            await update.message.reply_text(
                f"❌ Você não tem autorização para acessar os códigos do e-mail `{email_original}`.",
                parse_mode="Markdown"
            )
            return

        data_expiracao_str = dados_cliente["emails_permitidos"][email_original]
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

    dados_completos = {
        **conta_encontrada,
        "email_usuario": email_base,
        "email_destinatario": email_original
    }

    msg_carregando = await update.message.reply_text(
        f"⏳ *Buscando código recente para:* `{email_original}`...\nAguarde alguns segundos.",
        parse_mode="Markdown"
    )

    loop = context.application.loop
    resultado = await loop.run_in_executor(None, extrair_codigo_imap_wrapper, dados_completos)

    await msg_carregando.edit_text(resultado, parse_mode="Markdown")

def main():
    Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("autorizar", autorizar_cliente))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_mensagem_email))

    print("🤖 Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
