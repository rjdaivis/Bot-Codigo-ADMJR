import json
import logging
import re
import imaplib
import email
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM = "8621009761:AAF3vIBd5--2FDxSJsDCJSpGYcfSf64tpjc"

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
                f"Solicite a atualização de residência novamente no app da TV."
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

        match_codigo = re.search(r'\b\d{4,8}\b', corpo)
        match_link = re.search(r'https?://[^\s<>"]+(?:update-primary-location|confirm|verify)[^\s<>"]*', corpo)

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
        return f"❌ Erro ao acessar a caixa de e-mail."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contas = carregar_contas()
    if not contas:
        await update.message.reply_text("Nenhuma conta cadastrada.")
        return

    keyboard = []
    for email_key, info in contas.items():
        nome_botao = info.get("nome_exibicao", email_key)
        keyboard.append([InlineKeyboardButton(f"📧 {nome_botao}", callback_data=f"check:{email_key}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 **Central de Códigos de Residência**\n\n"
        "Selecione a tela/e-mail para buscar o código de verificação recebido nos últimos 15 minutos:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("check:"):
        email_selecionado = data.split("check:")[1]
        contas = carregar_contas()

        if email_selecionado not in contas:
            await query.edit_message_text("❌ Conta não encontrada.")
            return

        info_conta = contas[email_selecionado]
        dados_completos = {**info_conta, "email_usuario": email_selecionado}

        await query.edit_message_text(
            f"⏳ *Buscando código recente para:* `{email_selecionado}`...\nAguarde alguns segundos.",
            parse_mode="Markdown"
        )

        loop = context.application.loop
        resultado = await loop.run_in_executor(None, extrair_codigo_imap_wrapper, dados_completos)

        keyboard = [[InlineKeyboardButton("🔄 Voltar ao Menu", callback_data="menu_principal")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            resultado,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    elif data == "menu_principal":
        contas = carregar_contas()
        keyboard = []
        for email_key, info in contas.items():
            nome_botao = info.get("nome_exibicao", email_key)
            keyboard.append([InlineKeyboardButton(f"📧 {nome_botao}", callback_data=f"check:{email_key}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👋 **Central de Códigos de Residência**\n\n"
            "Selecione a tela/e-mail para buscar o código de verificação recebido nos últimos 15 minutos:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

def main():
    app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("🤖 Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
  
