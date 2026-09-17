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

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Configuração de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM = "8621009761:AAF3vIBd5--2FDxSJsDCJSpGYcfSf64tpjc"

# Servidor Flask simples para manter o serviço ativo no plano Free (Web Service) do Render
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos do Telegram está ativo e rodando!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

def carregar_contas():
    try:
        with open("contas.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Erro ao carregar contas.json: {e}")
        return {}

def extrair_codigo_imap_wrapper(dados_conta: dict) -> str:
    email_usuario = dados_conta.get("email_usuario")
    host = dados_conta.get("host_imap", "imap.gmail.com")
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap")

    try:
        mail = imaplib.IMAP4_SSL(host, porta)
        mail.login(email_usuario, senha)
        mail.select("INBOX")

        _, messages = mail.search(None, "ALL")
        id_list = messages[0].split()

        if not id_list:
            mail.logout()
            return "❌ Nenhum e-mail foi encontrado nesta caixa de entrada."

        latest_id = id_list[-1]
        _, data = mail.fetch(latest_id, "(RFC822)")
        msg = email.message_from_bytes(data[0][1])

        data_email_header = msg.get("Date")
        if not data_email_header:
            mail.logout()
            return "❌ Não foi possível verificar o horário do último e-mail."

        data_email = parsedate_to_datetime(data_email_header)
        agora = datetime.now(timezone.utc)

        diferenca_tempo = agora - data_email
        if diferenca_tempo > timedelta(minutes=15):
            minutos_passados = int(diferenca_tempo.total_seconds() // 60)
            mail.logout()
            return (
                f"⚠️ **Código Expirado!**\n\n"
                f"O último e-mail nesta conta foi recebido há **{minutos_passados} minutos**.\n"
                f"Solicite a atualização de residência novamente no app da TV/Dispositivo."
            )

        corpo = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    corpo = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            corpo = msg.get_payload(decode=True).decode(errors="ignore")

        mail.logout()

        # Captura códigos numéricos comuns (4 a 8 dígitos) ou links de verificação
        match_codigo = re.search(r'\b\d{4,8}\b', corpo)
        match_link = re.search(r'https?://[^\s<>"]+(?:update-primary-location|confirm|verify|household)[^\s<>"]*', corpo)

        if match_codigo:
            return (
                f"✅ **Código Encontrado!**\n\n"
                f"🔑 Seu código é: `{match_codigo.group(0)}`\n\n"
                f"⏱️ *E-mail recebido há menos de {max(1, int(diferenca_tempo.total_seconds() // 60))} minuto(s).*"
            )
        elif match_link:
            return (
                f"✅ **Link de Validação Encontrado!**\n\n"
                f"🔗 Clique no link abaixo para liberar:\n{match_link.group(0)}"
            )
        else:
            return "⚠️ Um e-mail recente foi encontrado (no prazo de 15 min), mas o código não pôde ser lido automaticamente."

    except Exception as e:
        logging.error(f"Erro IMAP: {e}")
        return "❌ Erro ao acessar a caixa de e-mail. Verifique a senha de aplicativo ou as configurações."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Central de Liberação de Códigos**\n\n"
        "Envie uma mensagem digitando o **e-mail do seu login** para buscar o código de verificação recebido nos últimos 15 minutos.\n\n"
        "Exemplo: `seuemail@gmail.com`",
        parse_mode="Markdown"
    )

async def receber_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text.strip().lower()

    if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", texto_usuario):
        await update.message.reply_text(
            "⚠️ Por favor, digite um **endereço de e-mail válido**.\nExemplo: `exemplo@gmail.com`",
            parse_mode="Markdown"
        )
        return

    contas = carregar_contas()

    if texto_usuario not in contas:
        await update.message.reply_text(
            f"❌ O e-mail `{texto_usuario}` não está cadastrado em nosso sistema.\n"
            f"Verifique se digitou corretamente.",
            parse_mode="Markdown"
        )
        return

    info_conta = contas[texto_usuario]
    dados_completos = {**info_conta, "email_usuario": texto_usuario}

    msg_carregando = await update.message.reply_text(
        f"⏳ *Buscando código recente para:* `{texto_usuario}`...\nAguarde alguns segundos.",
        parse_mode="Markdown"
    )

    loop = context.application.loop
    resultado = await loop.run_in_executor(None, extrair_codigo_imap_wrapper, dados_completos)

    await msg_carregando.edit_text(resultado, parse_mode="Markdown")

def main():
    # Inicia o servidor Flask em background para satisfazer a porta HTTP no Render Free
    Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_mensagem_email))

    print("🤖 Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
