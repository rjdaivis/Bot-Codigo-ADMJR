import os
import re
import json
import imaplib
import email
from email.header import decode_header
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from supabase import create_client, Client

# ==========================================
# CONFIGURAÇÕES E LOGS
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variáveis de Ambiente do Render
TELEGRAM_TOKEN = os.getenv("TOKEN_TELEGRAM")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TELEGRAM_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("ERRO: TOKEN_TELEGRAM, SUPABASE_URL ou SUPABASE_KEY não foram definidos no Render.")

# Inicialização do Cliente Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# FUNÇÕES AUXILIARES DE SEGURANÇA
# ==========================================

def eh_admin(user_id: int or str) -> bool:
    """Verifica se o usuário que executou o comando é o Administrador do bot."""
    if not ADMIN_ID:
        return True  # Se ADMIN_ID não foi definido, permite para evitar bloqueio acidental
    return str(user_id) == str(ADMIN_ID)

# ==========================================
# BANCO DE DADOS (SUPABASE) & MIGRAÇÃO
# ==========================================

def obter_cliente(telegram_id: str) -> dict:
    """Busca os dados de um cliente no Supabase pelo Telegram ID."""
    try:
        response = supabase.table("clientes_bot").select("*").eq("telegram_id", str(telegram_id)).execute()
        if response.data:
            return response.data[0]
        return {}
    except Exception as e:
        logger.error(f"Erro ao obter cliente {telegram_id}: {e}")
        return {}

def obter_todos_clientes() -> list:
    """Busca todos os clientes cadastrados no Supabase."""
    try:
        response = supabase.table("clientes_bot").select("*").execute()
        return response.data or []
    except Exception as e:
        logger.error(f"Erro ao obter todos os clientes: {e}")
        return []

def salvar_cliente(telegram_id: str, emails_dict: dict, permitir_sensivel: bool = False):
    """Salva ou atualiza os e-mails do cliente no Supabase."""
    try:
        payload = {
            "telegram_id": str(telegram_id),
            "emails_permitidos": emails_dict,
            "permitir_sensivel": permitir_sensivel,
            "updated_at": datetime.utcnow().isoformat()
        }
        supabase.table("clientes_bot").upsert(payload).execute()
        logger.info(f"Cliente {telegram_id} salvo com sucesso no Supabase.")
    except Exception as e:
        logger.error(f"Erro ao salvar cliente {telegram_id}: {e}")

def migrar_json_para_supabase():
    """Lê o arquivo clientes.json do GitHub e importa tudo automaticamente para o Supabase se ainda não existir."""
    caminho_json = "clientes.json"
    if os.path.exists(caminho_json):
        try:
            with open(caminho_json, "r", encoding="utf-8") as f:
                dados = json.load(f)
            
            logger.info("Verificando migração do clientes.json para o Supabase...")
            for telegram_id, info in dados.items():
                cliente_existente = obter_cliente(str(telegram_id))
                if not cliente_existente:
                    if isinstance(info, dict):
                        emails = info.get("emails", {})
                        if isinstance(emails, list):
                            emails = {e: None for e in emails}
                        permitir = info.get("permitir_sensivel", False)
                    elif isinstance(info, list):
                        emails = {e: None for e in info}
                        permitir = False
                    else:
                        continue

                    salvar_cliente(str(telegram_id), emails, permitir)

            logger.info("✅ Checagem/Migração do clientes.json concluída!")
        except Exception as e:
            logger.error(f"Erro na migração do clientes.json: {e}")

def validar_email_estrito(telegram_id: str, email_informado: str) -> bool:
    """
    REGRA ESTRITA: Valida o e-mail comparando exatamente a string informada
    contra as autorizações gravadas no Supabase. Mantém maiúsculas, minúsculas,
    pontos e símbolos sem aplicar qualquer normalização.
    """
    cliente = obter_cliente(telegram_id)
    emails_permitidos = cliente.get("emails_permitidos", {})
    
    if email_informado in emails_permitidos:
        expiracao = emails_permitidos[email_informado]
        if expiracao:
            try:
                data_exp = datetime.fromisoformat(expiracao)
                if datetime.utcnow() > data_exp:
                    return False
            except ValueError:
                pass
        return True
    return False

# ==========================================
# MOTOR DE BUSCA EM CONTAS (CONTAS.JSON)
# ==========================================

def carregar_contas_email() -> list:
    """Lê as contas de e-mail e senhas salvas no arquivo contas.json do GitHub."""
    caminho_contas = "contas.json"
    if not os.path.exists(caminho_contas):
        logger.warning("Arquivo contas.json não encontrado no repositório.")
        return []

    try:
        with open(caminho_contas, "r", encoding="utf-8") as f:
            dados = json.load(f)

        contas = []
        if isinstance(dados, dict):
            for email_acc, pass_acc in dados.items():
                if isinstance(pass_acc, dict):
                    contas.append({
                        "user": email_acc,
                        "pass": pass_acc.get("senha") or pass_acc.get("password"),
                        "server": pass_acc.get("server", "imap.gmail.com")
                    })
                else:
                    contas.append({"user": email_acc, "pass": pass_acc, "server": "imap.gmail.com"})
        elif isinstance(dados, list):
            for item in dados:
                if isinstance(item, dict):
                    contas.append({
                        "user": item.get("email") or item.get("user"),
                        "pass": item.get("senha") or item.get("password") or item.get("pass"),
                        "server": item.get("server", "imap.gmail.com")
                    })
        return contas
    except Exception as e:
        logger.error(f"Erro ao ler contas.json: {e}")
        return []

def extrair_corpo_email(msg) -> str:
    """Extrai o texto/HTML completo de uma mensagem de e-mail."""
    corpo = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type in ["text/plain", "text/html"] and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    corpo += payload.decode("utf-8", errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            corpo = payload.decode("utf-8", errors="ignore")
    return corpo

def buscar_codigos_streaming(email_destino: str) -> list:
    """
    Varre as caixas de entrada definidas em contas.json procurando e-mails
    destinados ao email_destino e extraindo códigos e links.
    """
    contas = carregar_contas_email()
    if not contas:
        return ["⚠️ Nenhuma conta de e-mail foi encontrada no arquivo `contas.json`."]

    resultados = []

    for conta in contas:
        imap_user = conta.get("user")
        imap_pass = conta.get("pass")
        imap_server = conta.get("server", "imap.gmail.com")

        if not imap_user or not imap_pass:
            continue

        try:
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(imap_user, imap_pass)
            mail.select("inbox")

            status, messages = mail.search(None, f'(TO "{email_destino}")')
            if status != "OK" or not messages[0]:
                status, messages = mail.search(None, f'TEXT "{email_destino}"')

            if not messages[0]:
                mail.logout()
                continue

            id_list = messages[0].split()
            ultimos_ids = id_list[-5:]

            for msg_id in reversed(ultimos_ids):
                res, msg_data = mail.fetch(msg_id, "(RFC822)")
                if res != "OK":
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        assunto, encoding = decode_header(msg.get("Subject", ""))[0]
                        if isinstance(assunto, bytes):
                            assunto = assunto.decode(encoding or "utf-8", errors="ignore")
                        
                        corpo = extrair_corpo_email(msg)

                        # 1. DISNEY+ (Código numérico de 6 dígitos)
                        if "disney" in msg.get("From", "").lower() or "disney" in assunto.lower():
                            codigos_disney = re.findall(r'\b\d{6}\b', corpo)
                            if codigos_disney:
                                resultados.append(f"✨ **Disney+**: Seu código de acesso é **{codigos_disney[0]}**")

                        # 2. NETFLIX (Residência principal, Acesso temporário/viagem ou Código)
                        elif "netflix" in msg.get("From", "").lower() or "netflix" in assunto.lower():
                            links_netflix = re.findall(r'https?://[^\s<>"]+netflix\.com[^\s<>"]+', corpo)
                            codigos_netflix = re.findall(r'\b\d{4,8}\b', corpo)

                            if links_netflix:
                                resultados.append(f"🔴 **Netflix (Link)**: [Clique aqui para confirmar/acessar]({links_netflix[0]})")
                            elif codigos_netflix:
                                resultados.append(f"🔴 **Netflix (Código)**: **{codigos_netflix[0]}**")

                        # 3. HBO MAX / MAX (Links de redefinição / Acesso)
                        elif "hbo" in msg.get("From", "").lower() or "max" in msg.get("From", "").lower() or "max" in assunto.lower():
                            links_hbo = re.findall(r'https?://[^\s<>"]+(?:max|hbomax)\.com[^\s<>"]+', corpo)
                            if links_hbo:
                                resultados.append(f"🟣 **HBO Max / MAX**: [Clique aqui para acessar/redefinir]({links_hbo[0]})")

                        # 4. GLOBO / GLOBOPLAY (Links de redefinição / Acesso)
                        elif "globo" in msg.get("From", "").lower() or "globo" in assunto.lower():
                            links_globo = re.findall(r'https?://[^\s<>"]+globo\.com[^\s<>"]+', corpo)
                            if links_globo:
                                resultados.append(f"🔵 **Globo / Globoplay**: [Clique aqui para acessar/redefinir]({links_globo[0]})")

            mail.logout()
        except Exception as e:
            logger.error(f"Erro ao consultar conta {imap_user}: {e}")

    return resultados

# ==========================================
# COMANDOS DO BOT TELEGRAM
# ==========================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando inicial do bot."""
    texto = (
        "👋 **Bem-vindo ao Bot de Atendimento de Streamings!**\n\n"
        "Envie o e-mail cadastrado exato para solicitar acessos, códigos ou redefinições:\n"
        "• **Netflix**: Confirmação de residência principal, links de viagem e códigos de acesso direto.\n"
        "• **Disney+**: Códigos numéricos de 6 dígitos.\n"
        "• **HBO Max / Globo**: Links de redefinição e acesso.\n\n"
        "⚠️ *Atenção: O e-mail deve ser digitado exatamente como foi cadastrado (respeitando maiúsculas, minúsculas e pontos).* "
    )
    await update.message.reply_text(texto, parse_mode="Markdown")

async def autorizar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Autoriza um e-mail para um ID do Telegram.
    Uso: /autorizar <telegram_id> <email> [AAAA-MM-DD]
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/autorizar <telegram_id> <email> [AAAA-MM-DD]`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]
    data_expira = context.args[2] if len(context.args) >= 3 else None

    # Formata data de expiração se fornecida
    exp_iso = None
    if data_expira:
        try:
            exp_iso = datetime.strptime(data_expira, "%Y-%m-%d").isoformat()
        except ValueError:
            await update.message.reply_text("⚠️ Formato de data inválido! Use `AAAA-MM-DD` (ex: 2026-12-31).")
            return

    cliente = obter_cliente(target_id)
    emails_dict = cliente.get("emails_permitidos", {})
    emails_dict[target_email] = exp_iso

    salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
    
    msg_data = f" (Expira em: `{data_expira}`)" if data_expira else " (Acesso vitalício/sem data)"
    await update.message.reply_text(
        f"✅ E-mail `{target_email}` cadastrado para o ID `{target_id}`{msg_data}!",
        parse_mode="Markdown"
    )

async def editar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Edita a data de expiração de um e-mail cadastrado.
    Uso: /editar <telegram_id> <email> <AAAA-MM-DD ou sem_data>
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 3:
        await update.message.reply_text("Uso correto: `/editar <telegram_id> <email> <AAAA-MM-DD ou sem_data>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]
    nova_opcao = context.args[2].lower()

    cliente = obter_cliente(target_id)
    emails_dict = cliente.get("emails_permitidos", {})

    if target_email not in emails_dict:
        await update.message.reply_text(f"❌ O e-mail `{target_email}` não foi encontrado no cadastro do ID `{target_id}`.", parse_mode="Markdown")
        return

    if nova_opcao in ["sem_data", "semdata", "none", "ilimitado", "0"]:
        emails_dict[target_email] = None
        msg_exp = "Removida a data (Acesso ilimitado)"
    else:
        try:
            exp_iso = datetime.strptime(nova_opcao, "%Y-%m-%d").isoformat()
            emails_dict[target_email] = exp_iso
            msg_exp = f"Nova data definida para `{nova_opcao}`"
        except ValueError:
            await update.message.reply_text("⚠️ Formato de data inválido! Use `AAAA-MM-DD` ou `sem_data`.")
            return

    salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
    await update.message.reply_text(
        f"✏️ **Atualizado!** E-mail `{target_email}` do ID `{target_id}`: {msg_exp}.",
        parse_mode="Markdown"
    )

async def remover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Remove um e-mail autorizador de um Telegram ID.
    Uso: /remover <telegram_id> <email>
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/remover <telegram_id> <email>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]

    cliente = obter_cliente(target_id)
    emails_dict = cliente.get("emails_permitidos", {})

    if target_email in emails_dict:
        del emails_dict[target_email]
        salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
        await update.message.reply_text(f"🗑️ E-mail `{target_email}` **removido com sucesso** do ID `{target_id}`!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ O e-mail `{target_email}` não estava cadastrado para o ID `{target_id}`.", parse_mode="Markdown")

async def listar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lista todos os clientes e seus e-mails autorizados, ou consulta um ID específico.
    Uso: /listar [telegram_id]
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    # Se informou um ID específico
    if len(context.args) >= 1:
        target_id = context.args[0]
        cliente = obter_cliente(target_id)
        if not cliente:
            await update.message.reply_text(f"❌ Nenhum registro encontrado para o Telegram ID `{target_id}`.", parse_mode="Markdown")
            return

        emails = cliente.get("emails_permitidos", {})
        vip = "Sim ⭐️" if cliente.get("permitir_sensivel") else "Não"
        
        texto = f"📋 **Cadastro do ID `{target_id}`** (VIP: {vip}):\n\n"
        if not emails:
            texto += "Nenhum e-mail autorizado."
        else:
            for em, exp in emails.items():
                data_str = exp.split("T")[0] if exp else "Sem expiração"
                texto += f"• `{em}` — Expira: *{data_str}*\n"

        await update.message.reply_text(texto, parse_mode="Markdown")
        return

    # Se não informou ID, lista todos os cadastrados no Supabase
    todos = obter_todos_clientes()
    if not todos:
        await update.message.reply_text("📂 Nenhum cliente cadastrado na base de dados.")
        return

    texto = f"📋 **Lista de Clientes Cadastrados ({len(todos)}):**\n\n"
    for c in todos:
        t_id = c.get("telegram_id")
        emails = c.get("emails_permitidos", {})
        vip = "⭐️ VIP" if c.get("permitir_sensivel") else ""
        
        texto += f"👤 **ID:** `{t_id}` {vip}\n"
        if isinstance(emails, dict) and emails:
            for em, exp in emails.items():
                d_str = exp.split("T")[0] if exp else "Sem expiração"
                texto += f"   └── `{em}` ({d_str})\n"
        else:
            texto += "   └── *Sem e-mails*\n"
        texto += "\n"

    # Caso a mensagem seja muito longa para o Telegram, divide em partes
    if len(texto) > 4000:
        for x in range(0, len(texto), 4000):
            await update.message.reply_text(texto[x:x+4000], parse_mode="Markdown")
    else:
        await update.message.reply_text(texto, parse_mode="Markdown")

async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Define o status VIP (permissão sensível) de um cliente.
    Uso: /vip <telegram_id> <sim ou nao>
    """
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/vip <telegram_id> <sim ou nao>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    opcao = context.args[1].lower()
    status_vip = opcao in ["sim", "s", "true", "1", "on"]

    cliente = obter_cliente(target_id)
    emails_dict = cliente.get("emails_permitidos", {})

    salvar_cliente(target_id, emails_dict, permitir_sensivel=status_vip)
    
    txt_status = "ATIVADO ⭐️" if status_vip else "DESATIVADO"
    await update.message.reply_text(f"👑 Status VIP do ID `{target_id}` foi **{txt_status}**!", parse_mode="Markdown")

async def processar_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o e-mail enviado pelo usuário e realiza a busca de acessos."""
    user_id = str(update.effective_user.id)
    email_recebido = update.message.text.strip()

    if validar_email_estrito(user_id, email_recebido):
        msg_wait = await update.message.reply_text(
            f"🔎 **E-mail confirmado:** `{email_recebido}`\n"
            "Consultando caixa de entrada para Netflix, Disney+, HBO Max e Globo...",
            parse_mode="Markdown"
        )
        
        resultados = buscar_codigos_streaming(email_recebido)

        if resultados:
            resposta = "📬 **Resultados encontrados:**\n\n" + "\n\n".join(resultados)
            await msg_wait.edit_text(resposta, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await msg_wait.edit_text(
                f"ℹ️ Nenhum código ou link recente foi encontrado para `{email_recebido}` nos últimos minutos.\n\n"
                "Certifique-se de ter solicitado o código/link na plataforma de streaming antes de consultar aqui.",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "❌ **E-mail não autorizado ou digitado incorretamente.**\n\n"
            "Verifique se você digitou o e-mail **exatamente** como cadastrado (respeitando maiúsculas, minúsculas, pontos e símbolos).",
            parse_mode="Markdown"
        )

# ==========================================
# INICIALIZAÇÃO DA APLICAÇÃO
# ==========================================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TOKEN_TELEGRAM não configurado nas variáveis do ambiente.")

    # Executa a migração automática do clientes.json para o Supabase ao iniciar
    migrar_json_para_supabase()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registra os Handlers de Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("autorizar", autorizar_command))
    app.add_handler(CommandHandler("editar", editar_command))
    app.add_handler(CommandHandler("remover", remover_command))
    app.add_handler(CommandHandler("listar", listar_command))
    app.add_handler(CommandHandler("vip", vip_command))

    # Handler para mensagens de texto (Validação e Busca de E-mails)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem_email))

    logger.info("Bot operando com integração Supabase e todos os comandos ativos...")
    app.run_polling()

if __name__ == "__main__":
    main()
