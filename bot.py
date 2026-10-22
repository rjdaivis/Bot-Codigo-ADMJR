import os
import re
import json
import imaplib
import email
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL.strip(), SUPABASE_KEY.strip())
    except Exception as e:
        logger.error(f"Erro ao inicializar cliente Supabase: {e}")

# ==========================================
# SERVIDOR WEB PARA HEALTH CHECK (RENDER)
# ==========================================

class ServidorHealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot de Streamings Ativo")

def iniciar_servidor_web():
    """Inicia um servidor HTTP em segundo plano para o Render reconhecer que o serviço está Live."""
    porta = int(os.getenv("PORT", 8080))
    try:
        servidor = HTTPServer(("0.0.0.0", porta), ServidorHealthCheck)
        logger.info(f"Servidor de verificação do Render ativo na porta {porta}")
        servidor.serve_forever()
    except Exception as e:
        logger.error(f"Erro ao iniciar servidor de verificação HTTP: {e}")

# ==========================================
# FUNÇÕES AUXILIARES DE DATAS E SEGURANÇA
# ==========================================

def eh_admin(user_id) -> bool:
    """Verifica se o usuário que executou a ação é o Administrador do bot."""
    if not ADMIN_ID:
        return False
    return str(user_id) == str(ADMIN_ID)

def processar_data_entrada(data_str: str):
    """
    Tenta converter a data inserida (DD-MM-AAAA, DD/MM/AAAA, DD-MM-AA)
    para formato ISO de armazenamento.
    """
    formatos = ["%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"]
    for fmt in formatos:
        try:
            dt = datetime.strptime(data_str, fmt)
            return dt.isoformat(), dt.strftime("%d-%m-%Y")
        except ValueError:
            pass
    return None, None

def formatar_data_exibicao(iso_str: str) -> str:
    """Converte uma string ISO para DD-MM-AAAA para exibição no Telegram."""
    if not iso_str:
        return "Sem expiração"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d-%m-%Y")
    except Exception:
        try:
            dt = datetime.strptime(iso_str, "%Y-%m-%d")
            return dt.strftime("%d-%m-%Y")
        except Exception:
            return iso_str

# ==========================================
# BANCO DE DADOS (SUPABASE) & MIGRAÇÃO
# ==========================================

def obter_cliente(telegram_id: str):
    """Busca os dados de um cliente no Supabase pelo Telegram ID."""
    if not supabase:
        return None, "Cliente Supabase não inicializado (Verifique URL/KEY)."
    try:
        response = supabase.table("clientes_bot").select("*").eq("telegram_id", str(telegram_id)).execute()
        if response.data:
            return response.data[0], None
        return {}, None
    except Exception as e:
        logger.error(f"Erro ao obter cliente {telegram_id} no Supabase: {e}")
        return None, str(e)

def obter_todos_clientes():
    """Busca todos os clientes cadastrados no Supabase."""
    if not supabase:
        return None, "Cliente Supabase não inicializado (Verifique URL/KEY)."
    try:
        response = supabase.table("clientes_bot").select("*").execute()
        return response.data, None
    except Exception as e:
        logger.error(f"Erro ao obter todos os clientes do Supabase: {e}")
        return None, str(e)

def salvar_cliente(telegram_id: str, emails_dict: dict, permitir_sensivel: bool = False):
    """Salva ou atualiza os e-mails do cliente no Supabase. Retorna (sucesso, mensagem_erro)."""
    if not supabase:
        return False, "Conexão com Supabase não foi iniciada. Verifique as variáveis de ambiente."
    try:
        payload = {
            "telegram_id": str(telegram_id),
            "emails_permitidos": emails_dict,
            "permitir_sensivel": permitir_sensivel,
            "updated_at": datetime.utcnow().isoformat()
        }
        supabase.table("clientes_bot").upsert(payload).execute()
        logger.info(f"Cliente {telegram_id} salvo com sucesso no Supabase.")
        return True, None
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Erro ao salvar cliente {telegram_id} no Supabase: {err_msg}")
        return False, err_msg

def migrar_json_para_supabase():
    """Lê o arquivo clientes.json do GitHub e importa tudo para o Supabase se ainda não existir."""
    caminho_json = "clientes.json"
    if os.path.exists(caminho_json):
        try:
            with open(caminho_json, "r", encoding="utf-8") as f:
                dados = json.load(f)
            
            logger.info("Verificando migração do clientes.json para o Supabase...")
            for telegram_id, info in dados.items():
                cliente_existente, _ = obter_cliente(str(telegram_id))
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
    REGRA DE VALIDAÇÃO DE ACESSO:
    1. Administrador (ADMIN_ID): Acesso ilimitado a qualquer e-mail.
    2. Cliente VIP (permitir_sensivel = True): Acesso ilimitado a qualquer e-mail.
    3. Cliente Comum: Exige vínculo do e-mail exato e verificação de expiração.
    """
    if eh_admin(telegram_id):
        return True

    cliente, _ = obter_cliente(telegram_id)
    if not cliente:
        return False

    if cliente.get("permitir_sensivel"):
        return True

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
    user_id = update.effective_user.id
    texto = (
        "👋 **Bem-vindo ao Bot de Atendimento de Streamings!**\n\n"
        f"🆔 **Seu Telegram ID:** `{user_id}`\n\n"
        "Envie o e-mail cadastrado exato para solicitar acessos, códigos ou redefinições:\n"
        "• **Netflix**: Confirmação de residência principal, links de viagem e códigos de acesso direto.\n"
        "• **Disney+**: Códigos numéricos de 6 dígitos.\n"
        "• **HBO Max / Globo**: Links de redefinição e acesso.\n\n"
        "📌 *Primeiro acesso ou precisa liberar novo e-mail?*\n"
        f"Copie e envie o seu **ID (`{user_id}`)** e o login completo no PV do **ADM JR** para realizar a liberação no sistema!"
    )
    await update.message.reply_text(texto, parse_mode="Markdown")

async def autorizar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/autorizar <telegram_id> <email> [DD-MM-AAAA]`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]
    data_expira = context.args[2] if len(context.args) >= 3 else None

    exp_iso = None
    data_formatada_exibicao = None

    if data_expira:
        exp_iso, data_formatada_exibicao = processar_data_entrada(data_expira)
        if not exp_iso:
            await update.message.reply_text("⚠️ Formato de data inválido! Use `DD-MM-AAAA` (ex: 31-12-2026).")
            return

    cliente, erro_get = obter_cliente(target_id)
    cliente = cliente or {}
    emails_dict = cliente.get("emails_permitidos", {})
    emails_dict[target_email] = exp_iso

    sucesso, detalhe_erro = salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
    
    if sucesso:
        msg_data = f" (Expira em: `{data_formatada_exibicao}`)" if data_formatada_exibicao else " (Acesso vitalício/sem data)"
        await update.message.reply_text(
            f"✅ E-mail `{target_email}` cadastrado para o ID `{target_id}`{msg_data}!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ **Erro ao salvar no Supabase:**\n`{detalhe_erro}`\n\n"
            "Verifique as variáveis `SUPABASE_URL` e `SUPABASE_KEY` no Render.",
            parse_mode="Markdown"
        )

async def editar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 3:
        await update.message.reply_text("Uso correto: `/editar <telegram_id> <email> <DD-MM-AAAA ou sem_data>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]
    nova_opcao = context.args[2].lower()

    cliente, _ = obter_cliente(target_id)
    cliente = cliente or {}
    emails_dict = cliente.get("emails_permitidos", {})

    if target_email not in emails_dict:
        await update.message.reply_text(f"❌ O e-mail `{target_email}` não foi encontrado no cadastro do ID `{target_id}`.", parse_mode="Markdown")
        return

    if nova_opcao in ["sem_data", "semdata", "none", "ilimitado", "0"]:
        emails_dict[target_email] = None
        msg_exp = "Removida a data (Acesso ilimitado)"
    else:
        exp_iso, data_formatada_exibicao = processar_data_entrada(nova_opcao)
        if not exp_iso:
            await update.message.reply_text("⚠️ Formato de data inválido! Use `DD-MM-AAAA` (ex: 31-12-2026) ou `sem_data`.")
            return
        emails_dict[target_email] = exp_iso
        msg_exp = f"Nova data definida para `{data_formatada_exibicao}`"

    sucesso, detalhe_erro = salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
    if sucesso:
        await update.message.reply_text(
            f"✏️ **Atualizado!** E-mail `{target_email}` do ID `{target_id}`: {msg_exp}.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ **Erro ao atualizar no Supabase:**\n`{detalhe_erro}`", parse_mode="Markdown")

async def remover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/remover <telegram_id> <email>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    target_email = context.args[1]

    cliente, _ = obter_cliente(target_id)
    cliente = cliente or {}
    emails_dict = cliente.get("emails_permitidos", {})

    if target_email in emails_dict:
        del emails_dict[target_email]
        sucesso, detalhe_erro = salvar_cliente(target_id, emails_dict, cliente.get("permitir_sensivel", False))
        if sucesso:
            await update.message.reply_text(f"🗑️ E-mail `{target_email}` **removido com sucesso** do ID `{target_id}`!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ **Erro ao salvar no Supabase:**\n`{detalhe_erro}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ O e-mail `{target_email}` não estava cadastrado para o ID `{target_id}`.", parse_mode="Markdown")

async def listar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) >= 1:
        target_id = context.args[0]
        cliente, erro_get = obter_cliente(target_id)
        if erro_get:
            await update.message.reply_text(f"⚠️ **Erro no Supabase:**\n`{erro_get}`", parse_mode="Markdown")
            return
        if not cliente:
            await update.message.reply_text(f"❌ Nenhum registro encontrado para o Telegram ID `{target_id}`.", parse_mode="Markdown")
            return

        emails = cliente.get("emails_permitidos", {})
        vip = "Sim ⭐️ (Sem restrições de e-mail)" if cliente.get("permitir_sensivel") else "Não"
        
        texto = f"📋 **Cadastro do ID `{target_id}`** (VIP: {vip}):\n\n"
        if not emails:
            texto += "Nenhum e-mail vinculado."
        else:
            for em, exp in emails.items():
                data_str = formatar_data_exibicao(exp)
                texto += f"• `{em}` — Expira: *{data_str}*\n"

        await update.message.reply_text(texto, parse_mode="Markdown")
        return

    todos, erro_todos = obter_todos_clientes()
    if erro_todos:
        await update.message.reply_text(f"⚠️ **Erro de conexão com o Supabase:**\n`{erro_todos}`", parse_mode="Markdown")
        return

    if not todos:
        await update.message.reply_text("📂 Nenhum cliente cadastrado na base de dados.")
        return

    texto = f"📋 **Lista de Clientes Cadastrados ({len(todos)}):**\n\n"
    for c in todos:
        t_id = c.get("telegram_id")
        emails = c.get("emails_permitidos", {})
        vip = "⭐️ VIP (Livre)" if c.get("permitir_sensivel") else ""
        
        texto += f"👤 **ID:** `{t_id}` {vip}\n"
        if isinstance(emails, dict) and emails:
            for em, exp in emails.items():
                d_str = formatar_data_exibicao(exp)
                texto += f"   └── `{em}` ({d_str})\n"
        else:
            texto += "   └── *Sem e-emails vinculados*\n"
        texto += "\n"

    if len(texto) > 4000:
        for x in range(0, len(texto), 4000):
            await update.message.reply_text(texto[x:x+4000], parse_mode="Markdown")
    else:
        await update.message.reply_text(texto, parse_mode="Markdown")

async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **Acesso negado.** Apenas o administrador pode usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Uso correto: `/vip <telegram_id> <sim ou nao>`", parse_mode="Markdown")
        return

    target_id = context.args[0]
    opcao = context.args[1].lower()
    status_vip = opcao in ["sim", "s", "true", "1", "on"]

    cliente, _ = obter_cliente(target_id)
    cliente = cliente or {}
    emails_dict = cliente.get("emails_permitidos", {})

    sucesso, detalhe_erro = salvar_cliente(target_id, emails_dict, permitir_sensivel=status_vip)
    
    if sucesso:
        txt_status = "ATIVADO ⭐️\nEste ID agora pode pesquisar QUALQUER e-mail sem restrições!" if status_vip else "DESATIVADO\nEste ID voltou a depender de e-mails específicos cadastrados."
        await update.message.reply_text(f"👑 Status VIP do ID `{target_id}` foi **{txt_status}**", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ **Erro ao salvar no Supabase:**\n`{detalhe_erro}`", parse_mode="Markdown")

async def processar_mensagem_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "❌ **E-mail não autorizado ou incorreto.**\n\n"
            f"🆔 **Seu Telegram ID:** `{user_id}`\n\n"
            f"Caso seja seu primeiro acesso ou precisa autorizar um novo e-mail, envie o seu **ID (`{user_id}`)** e seu login/e-mail no PV do **ADM JR** para realizar a liberação!\n\n"
            "⚠️ *Se você já possui cadastro, verifique se digitou o e-mail exatamente igual ao cadastrado (respeitando maiúsculas, minúsculas e pontos).*",
            parse_mode="Markdown"
        )

# ==========================================
# INICIALIZAÇÃO DA APLICAÇÃO
# ==========================================

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TOKEN_TELEGRAM não configurado nas variáveis do ambiente.")

    threading.Thread(target=iniciar_servidor_web, daemon=True).start()
    migrar_json_para_supabase()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("autorizar", autorizar_command))
    app.add_handler(CommandHandler("editar", editar_command))
    app.add_handler(CommandHandler("remover", remover_command))
    app.add_handler(CommandHandler("listar", listar_command))
    app.add_handler(CommandHandler("vip", vip_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem_email))

    logger.info("Bot operando com exibição detalhada de erros...")
    app.run_polling()

if __name__ == "__main__":
    main()
