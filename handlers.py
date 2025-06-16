import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, Forbidden
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

def safe_edit(q, text, markup=None, parse_mode="Markdown"):
    try:
        if markup:
            return q.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
        return q.edit_message_text(text, parse_mode=parse_mode)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        raise

# 1️⃣ Autenticação de canal
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel": return
    sess = Session()
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
        creator = next((a.user for a in admins if a.status=="creator" and not a.user.is_bot), None)
    except Exception as e:
        logger.error("Erro ao obter admins: %s", e)
        return
    if not creator:
        return
    sess.merge(User(id=creator.id, username=creator.username))
    ch = sess.get(Channel, msg.chat.id)
    if not ch:
        ch = Channel(
            id=msg.chat.id,
            owner_id=creator.id,
            username=msg.chat.username,
            title=msg.chat.title,
            authenticated=True
        )
        sess.add(ch)
    else:
        ch.owner_id = creator.id
        ch.authenticated = True
    sess.commit()
    logger.info("Canal autenticado: %s", msg.chat.title)

# 2️⃣ Menu principal
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    sess.merge(User(id=uid, username=update.effective_user.username))
    sess.commit()
    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0

    kb = []
    if owns: kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")]
    ]
    if participates: kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    markup = InlineKeyboardMarkup(kb)
    text = "Escolha uma opção:"
    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await update.callback_query.answer()
        await safe_edit(update.callback_query, text, markup)
    user_states.pop(uid, None)

# 3️⃣ Ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    txt = (
        "👋 Funcionalidades:\n"
        "• Criar grupos\n• Convidar canais (bot admin)\n"
        "• Explorar públicos\n• Solicitar entrada / Sair\n"
        "• Replicar posts\n\nUse /start para voltar"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
    await safe_edit(update.callback_query, txt, markup)

# 4️⃣ Criar grupo (nome)
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]])
    await safe_edit(update.callback_query, "📌 Envie o nome do novo grupo:", markup)

# 5️⃣ Criar canal via mensagem
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    st = user_states.get(uid)
    sess = Session()
    if st and st.get("state") == "awaiting_group_name":
        nome = update.message.text.strip()
        sess.add(Group(name=nome, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
    elif st and st.get("state") == "awaiting_channel_invite":
        match = re.search(r"@([\w\d_]+)", update.message.text) or re.search(r"t\.me/([\w\d_]+)", update.message.text)
        if not match:
            return await update.message.reply_text("❌ Envie @username ou t.me/username")
        username = match.group(1)
        try:
            chat = await ctx.bot.get_chat(username)
            admins = await ctx.bot.get_chat_administrators(chat.id)
            chan_owner = admins[0].user
        except Exception as e:
            return await update.message.reply_text(f"❌ Não foi possível acessar canal: {e}")
        channel = sess.get(Channel, chat.id)
        if not channel:
            channel = Channel(
                id=chat.id,
                owner_id=chan_owner.id,
                username=username,
                title=chat.title,
                authenticated=False
            )
            sess.add(channel)
        gc = GroupChannel(group_id=st["group_id"], channel_id=chat.id, inviter_id=uid, accepted=None)
        sess.add(gc); sess.commit()
        await update.message.reply_text("✅ Convite interno enviado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)
    else:
        return  # sem estado

# continue com as funções restantes: menu_meus_canais, menu_meus_grupos, gerenciar_grupo, explorar_grupos, ver_grupo, solicitar_entrada, handle_ext_response, prompt remoção, etc. 
# (Eles permanecem conforme disponibilizados acima)

# 1️⃣1️⃣ Replicar posts
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        for tgt in sess.get(Group, gc.group_id).channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# 🧭 Centralizador de callbacks
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Callback: %s", data)
    routing = {
        "start": start, "menu_ajuda": menu_ajuda, "criar_grupo": menu_criar_grupo,
        "menu_meus_canais": menu_meus_canais, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos, "menu_sair_grupo": None  # ajuste legível
    }
    if data in routing:
        return await routing[data](update, ctx)

    # demais rotas: convite_, vergrp_, solicit_, aceito_, recusar_, remover_, delete_, sair_, etc.
    # Certifique que todas as funções existem e são importadas
    if data.startswith("convite_"):
        uid = update.callback_query.from_user.id
        gid = int(data.split("_")[1])
        user_states[uid] = {"state": "awaiting_channel_invite", "group_id": gid}
        return await safe_edit(update.callback_query, "📥 Envie @username ou invite link:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]))

    # continue com demais prefixes conforme disponivel, respeitando os handlers implementados
    await update.callback_query.answer("⚠️ Ação não reconhecida.")
