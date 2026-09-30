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
from email.header import decode_header
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN_TELEGRAM = os.environ.get("TOKEN_TELEGRAM", "SEU_TOKEN_AQUI")
ADMIN_ID = 7496198484

CLIENTES_FIXOS_PADRAO = {
    "7496198484": {
        "emails_permitidos": {"*": "2030-12-31"},
        "permitir_sensivel": True
    },
    "6035184245": {
        "emails_permitidos": {"*": "2027-01-16"},
        "permitir_sensivel": True
    },
    "7955838907": {
        "emails_permitidos": {
            "pastos.gl.au.b.er@gmail.com": "2026-10-04",
            "mariana.l.a.s.t.os8.1@gmail.com": "2026-10-21"
        },
        "permitir_sensivel": False
    },
    "1399615731": {
        "emails_permitidos": {
            "mau.a.dan.ie.l.a@gmail.com": "2026-10-21"
        },
        "permitir_sensivel": False
    },
    "5804754899": {
        "emails_permitidos": {
            "ale62828adm.jr7maltes@gmail.com": "2026-12-31",
            "marianal.as.tos8.1@gmail.com": "2026-12-31",
            "marianna.admjr@outlook.com": "2026-12-31"
        },
        "permitir_sensivel": False
    },
    "7219528497": {
        "emails_permitidos": {
            "costalinhare.s325@gmail.com": "2026-12-31",
            "costalinhares325@gmail.com": "2026-12-31",
            "elimira.n.dael.lo@gmail.com": "2026-12-31"
        },
        "permitir_sensivel": False
    },
    "2082696204": {
        "emails_permitidos": {
            "adm.jr.mar.t.h.a@gmail.com": "2026-12-31",
            "cass.ia.a.d.m.jr@gmail.com": "2026-12-31",
            "marload.m.j.r2.00.0@gmail.com": "2026-12-31",
            "mario.admjr2000@gmail.com": "2026-12-31",
            "margareth_adm.jr_fariaz@yahoo.com": "2026-12-31"
        },
        "permitir_sensivel": False
    },
    "5692675330": {
        "emails_permitidos": {
            "polyad.mj.r8.2and@gmail.com": "2026-12-31",
            "polyadmjr.82and@gmail.com": "2026-12-31",
            "marianna.admjr@outlook.com": "2026-12-31"
        },
        "permitir_sensivel": False
    },
    "8096119496": {
        "emails_permitidos": {
            "elimiran.d.ael.l.o@gmail.com": "2026-12-31"
        },
        "permitir_sensivel": False
    }
}

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot de Códigos Online"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

def carregar_json(caminho: str) -> dict:
    dados = {}
    try:
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
    except Exception as e:
        logging.error(f"Erro ao carregar {caminho}: {e}")

    if caminho == "clientes.json":
        for id_fixo, info_fixa in CLIENTES_FIXOS_PADRAO.items():
            if id_fixo not in dados:
                dados[id_fixo] = info_fixa
            else:
                if "emails_permitidos" not in dados[id_fixo]:
                    dados[id_fixo]["emails_permitidos"] = {}
                for em, val in info_fixa.get("emails_permitidos", {}).items():
                    dados[id_fixo]["emails_permitidos"][em] = val
                if info_fixa.get("permitir_sensivel"):
                    dados[id_fixo]["permitir_sensivel"] = True
    return dados

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

def extrair_link_netflix_html(corpo_html: str) -> str:
    if not corpo_html:
        return ""
    
    matches = re.findall(r'href=["\'](https?://[^"\']+)["\']', corpo_html, re.IGNORECASE)
    
    # Lista de palavras que NUNCA devem estar no link principal
    palavras_bloqueadas = ["password", "travel", "help", "unsubscribe", "login", "manageaccountaccess"]

    # 1. Busca prioritária pelo botão principal de confirmação/residência
    for link in matches:
        link_lower = link.lower()
        if "netflix.com" in link_lower and any(p in link_lower for p in ["accountaccess", "update-primary-location", "nftoken", "verifyemail", "confirm"]):
            if not any(x in link_lower for x in palavras_bloqueadas):
                link_limpo = link.replace("&#x3D;", "=").replace("&amp;", "&").replace("\r", "").replace("\n", "")
                return link_limpo.strip()

    # 2. Busca secundária de backup
    for link in matches:
        link_lower = link.lower()
        if "netflix.com" in link_lower and not any(x in link_lower for x in palavras_bloqueadas + ["privacy", "terms", "twitter", "facebook"]):
            link_limpo = link.replace("&#x3D;", "=").replace("&amp;", "&").replace("\r", "").replace("\n", "")
            return link_limpo.strip()

    return ""

def extrair_url_pura_href(corpo_html: str, termo_busca: str = "http") -> str:
    if not corpo_html:
        return ""
    matches = re.findall(r'href=["\'](https?://[^"\']+)["\']', corpo_html, re.IGNORECASE)
    for link in matches:
        if termo_busca in link.lower() and not any(x in link.lower() for x in ["password", "unsubscribe", "help", "privacy", "terms", "manageaccountaccess"]):
            link_limpo = link.replace("&#x3D;", "=").replace("&amp;", "&").replace("\r", "").replace("\n", "")
            return link_limpo.strip()
    return ""

def extrair_codigo_imap_wrapper(dados_conta: dict, pode_acessar_sensivel: bool = False) -> str:
    email_usuario = dados_conta.get("email_usuario")
    
    if "yahoo" in email_usuario:
        host = dados_conta.get("host_imap", "imap.mail.yahoo.com")
    elif "outlook" in email_usuario or "hotmail" in email_usuario:
        host = dados_conta.get("host_imap", "outlook.office365.com")
    else:
        host = dados_conta.get("host_imap", "imap.gmail.com")
        
    porta = dados_conta.get("porta", 993)
    senha = dados_conta.get("senha_imap")

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

            minutos = max(1, int(diferenca_segundos // 60)) if diferenca_segundos > 0 else 1

            # 1. SEGURANÇA VIP (REDEFINIÇÃO DE SENHA)
            if "redefinir" in assunto_lower or "recuperar senha" in assunto_lower or "reset-password" in corpo_html.lower():
                if not pode_acessar_sensivel:
                    mail.close()
                    mail.logout()
                    return "🚫 <b>Solicitação Não Permitida!</b>\n\nEste e-mail trata de redefinição de senha ou alteração de dados."

            # 2. VERIFICAÇÃO DE ENDEREÇO / RESIDÊNCIA -> RETORNA O LINK FORMATADO EM HTML
            eh_email_link = any(p in assunto_lower for p in ["confirme seu endereço", "verifique seu endereço", "residência", "endereço de email", "atualizar", "fora da sua"])
            if eh_email_link:
                link_netflix = extrair_link_netflix_html(corpo_html)
                if link_netflix:
                    mail.close()
                    mail.logout()
                    return (
                        f"✅ <b>Link de Verificação / Residência Netflix Encontrado!</b>\n\n"
                        f"👉 <a href=\"{link_netflix}\">Clique aqui para confirmar no site da Netflix</a>\n\n"
                        f"🔗 <b>URL Completa:</b>\n<code>{link_netflix}</code>\n\n"
                        f"⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
                    )

            # 3. CÓDIGO DE ENTRADA / ACESSO -> RETORNA O CÓDIGO
            eh_email_codigo = any(p in assunto_lower for p in ["código de acesso", "código de verificação"])
            if eh_email_codigo:
                codigos = re.findall(r'\b\d{4,6}\b', corpo_texto_puro)
                for cod in codigos:
                    if not cod.startswith("0800"):
                        mail.close()
                        mail.logout()
                        return f"✅ <b>Código Encontrado!</b>\n\n🔑 Seu código é: <code>{cod}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"

            # 4. CASO GENÉRICO DE FALLBACK
            codigos_encontrados = re.findall(r'\b\d{4,6}\b', corpo_texto_puro)
            codigo_valido = next((c for c in codigos_encontrados if not c.startswith("0800")), None)
            link_encontrado = extrair_link_netflix_html(corpo_html) or extrair_url_pura_href(corpo_html, "http")

            if codigo_valido and "código" in assunto_lower:
                mail.close()
                mail.logout()
                return f"✅ <b>Código Encontrado!</b>\n\n🔑 Seu código é: <code>{codigo_valido}</code>\n\n⏱️ <i>E-mail recebido há {minutos} minuto(s).</i>"
            elif link_encontrado:
                mail.close()
                mail.logout()
                return (
                    f"✅ <b>Link Encontrado!</b>\n\n"
                    f"👉 <a href=\"{link_encontrado}\">Clique aqui para acessar</a>\n\n"
                    f"🔗 <b>URL Completa:</b>\n<code>{link_encontrado}</code>\n\n"
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

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id).strip()
    clientes = carregar_json("clientes.json")

    if user_id not in clientes and user_id != str(ADMIN_ID).strip():
        clientes[user_id] = {"emails_permitidos": {}, "permitir_sensivel": False}
        salvar_json("clientes.json", clientes)

    await update.message.reply_text(
        f"👋 <b>Central de Liberação de Códigos</b>\n\n"
        f"🆔 <b>Seu ID do Telegram:</b> <code>{user_id}</code>\n\n"
        f"Envie o e-mail cadastrado abaixo para buscar códigos/links recentes.",
        parse_mode="HTML"
    )

async def autorizar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    try:
        user_id_cliente = str(context.args[0]).strip()
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

        msg_vip = " (Acesso Total + VIP)" if clientes[user_id_cliente].get("permitir_sensivel") else ""
        await update.message.reply_text(
            f"✅ <b>Acesso Concedido!</b>\n\n"
            f"👤 <b>ID Cliente:</b> <code>{user_id_cliente}</code>\n"
            f"📧 <b>E-mail Liberado:</b> <code>{email_cliente}</code>\n"
            f"📅 <b>Válido até:</b> {data_validade} ({dias} dias){msg_vip}",
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(
            "⚠️ <b>Uso correto:</b> <code>/autorizar ID_CLIENTE EMAIL DIAS [vip]</code>",
            parse_mode="HTML"
        )

async def listar_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id).strip() != str(ADMIN_ID).strip():
        return

    clientes = carregar_json("clientes.json")
    if not clientes:
        await update.message.reply_text("📋 <b>Nenhum cliente cadastrado no momento.</b>", parse_mode="HTML")
        return

    hoje = datetime.now().date()
    mensagem = "📋 <b>Lista de Clientes Liberados:</b>\n\n"

    for id_cliente, dados in clientes.items():
        emails = dados.get("emails_permitidos", {})
        vip = "Sim" if dados.get("permitir_sensivel") else "Não"
        
        mensagem += f"👤 <b>ID:</b> <code>{id_cliente}</code> | <b>VIP:</b> {vip}\n"
        
        if not emails:
            mensagem += "   └ ⚠️ Nenhum e-mail liberado.\n"
        else:
            for email_end, data_exp in emails.items():
                try:
                    exp_date = datetime.strptime(data_exp, "%Y-%m-%d").date()
                    dias_restantes = (exp_date - hoje).days
                    status_dias = "🛑 <i>Expirado</i>" if dias_restantes < 0 else f"⏳ <i>Faltam {dias_restantes} dia(s)</i>"
                except Exception:
                    status_dias = "❓ Data inválida"

                mensagem += f"   └ 📧 <code>{email_end}</code>\n      📅 Validade: {data_exp} ({status_dias})\n"
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

    if not eh_admin and (not dados_cliente or not dados_cliente.get("emails_permitidos")):
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
        email_original_norm = normalizar_email_gmail(email_original)
        chaves_norm = [normalizar_email_gmail(k) for k in emails_permitidos.keys()]

        tem_acesso = "*" in emails_permitidos or email_original in emails_permitidos or email_original_norm in chaves_norm

        if not tem_acesso:
            await update.message.reply_text(f"❌ Você não tem autorização para acessar o e-mail <code>{email_original}</code>.", parse_mode="HTML")
            return

    email_base = normalizar_email_gmail(email_original)
    contas = carregar_json("contas.json")
    
    conta_encontrada = None
    for chave_email, dados in contas.items():
        chave_norm = normalizar_email_gmail(chave_email)
        if chave_email.strip().lower() == email_original or chave_norm == email_base:
            conta_encontrada = dados
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

def main():
    Thread(target=run_flask, daemon=True).start()

    while True:
        try:
            app = ApplicationBuilder().token(TOKEN_TELEGRAM).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("autorizar", autorizar_cliente))
            app.add_handler(CommandHandler("listar", listar_clientes))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_mensagem_email))

            print("🤖 Bot iniciado e rodando com alta estabilidade...")
            app.run_polling(poll_interval=1.0, timeout=20)
        except Exception as e:
            logging.error(f"Ocorreu uma queda temporária no Bot: {e}. Reconectando em 5 segundos...")
            time.sleep(5)

if __name__ == "__main__":
    main()
