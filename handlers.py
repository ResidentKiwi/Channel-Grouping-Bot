import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

def safe_edit(query, text, markup=None, parse_mode="Markdown"):
    try:
        if markup:
            return query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
        else:
            return query.edit_message_text(text, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Ignored unmodified message edit")
            return
        raise

# 1️⃣ Autenticação de canal quando posta (dev como admin)
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel": return
    sess = Session()
    ch = sess.get(Channel, msg.chat.id)
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    except Exception as e:
        logger.error("Erro ao obter admins: %s", e)
        return
    creator = next((a.user for a in admins if a.status == "creator" and not a.user.is_bot), None)
    if not creator: return
    if not sess.get(User, creator.id):
        sess.add(User(id=creator.id, username=creator.username))
    if not ch:
        ch = Channel(
            id=msg.chat.id,
            owner_id=creator.id,
            username=msg.chat.username,
            title=msg.chat.title,
            authenticated=True,
        )
        sess.add(ch)
    else:
        ch.authenticated = True
    sess.commit()
    logger.info("Canal autenticado: %s", msg.chat.title)

# 2️⃣ Menu principal
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    if not sess.get(User, uid):
        sess.add(User(id=uid, username=update.effective_user.username))
        sess.commit()
    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0
    kb = []
    if owns:
        kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")],
    ]
    if participates:
        kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])
    text = "Escolha uma opção:"
    markup = InlineKeyboardMarkup(kb)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await update.callback_query.answer()
        await safe_edit(update.callback_query, text, markup)
    user_states.pop(uid, None)

# 3️⃣ Tela de Ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    text = (
        "👋 Funcionalidades disponíveis:\n"
        "• Criar grupos\n"
        "• Convidar canais (bot precisa ser admin)\n"
        "• Explorar grupos públicos\n"
        "• Solicitar entrada / Sair de grupos\n"
        "• Replicar posts entre canais\n\n"
        "Volte ao menu com /start"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
    await safe_edit(update.callback_query, text, markup)

# 4️⃣ Criar grupo (nome)
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]])
    await safe_edit(update.callback_query, "📌 Envie o nome do novo grupo:", markup)

# 5️⃣ Listar seus grupos
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        return await safe_edit(update.callback_query, "🚫 Você ainda não criou nenhum grupo.", markup)
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# 📁 Gerenciar grupo específico
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session(); g = sess.get(Group, gid)
    text = f"🎯 Grupo: *{g.name}*"
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))

# 6️⃣ Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await safe_edit(update.callback_query, "Ainda não há grupos disponíveis.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [
        [InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")]
        for g in grps
    ]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# 7️⃣ Ver detalhes de grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session(); g = sess.get(Group, gid)
    participants = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:"
    for gc in participants:
        ch = sess.get(Channel, gc.channel_id)
        try:
            subs = await ctx.bot.get_chat_members_count(ch.id)
        except:
            subs = "?"
        link = f"https://t.me/{ch.username}" if ch.username else f"ID:{ch.id}"
        text += f"\n- [{ch.title}]({link}) — {subs}"
    kb = [
        [InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]
    ]
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))

# 8️⃣ Solicitação de entrada em grupo
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    # evita dupla solicitação
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        return await safe_edit(update.callback_query, "Você já está no grupo ou já solicitou entrada.")
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        return await safe_edit(update.callback_query, "❌ Seu canal ainda não foi autenticado.")
    g = sess.get(Group, gid)
    dono = sess.get(User, g.owner_id)
    link = f"https://t.me/{ch.username}" if ch.username else f"ID:{ch.id}"
    await ctx.bot.send_message(
        dono.id,
        f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*\n{link}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")
        ]])
    )
    await safe_edit(update.callback_query, "✅ Solicitação enviada para o dono do grupo.")

# 9️⃣ Responder solicitação externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session(); g = sess.get(Group, gid)
    ch = sess.get(Channel, cid)
    if action == "aceitar":
        sess.add(GroupChannel(group_id=gid, channel_id=cid, inviter_id=g.owner_id, accepted=True))
        sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito.")
        await ctx.bot.send_message(cid, f"✅ Seu canal foi aceito no grupo *{g.name}*")
    else:
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")

# 🔟 Receber texto (criar grupo via nome)
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    state = user_states.get(uid)
    if state and state.get("state") == "awaiting_group_name":
        nome = update.message.text.strip()
        sess = Session()
        sess.add(Group(name=nome, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
        user_states.pop(uid)

# Replicar posts para canais aceitos
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        grp = sess.get(Group, gc.group_id)
        for tgt in grp.channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# 🔁 Roteador de callbacks
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Button clicked: %s", data)
    mapping = {
        "start": start,
        "menu_ajuda": menu_ajuda,
        "criar_grupo": menu_criar_grupo,
        "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos,
    }
    for key, fn in mapping.items():
        if data == key:
            return await fn(update, ctx)
    if data.startswith("gerenciar_"):
        return await gerenciar_grupo(update, ctx)
    if data.startswith("vergrp_"):
        return await ver_grupo(update, ctx)
    if data.startswith("solicit_"):
        return await solicitar_entrada(update, ctx)
    if data.startswith(("aceitar_ext_", "recusar_ext_")):
        return await handle_ext_response(update, ctx)
    # Aqui você pode adicionar outras rotas: convite, remover, apagar, sair, etc.
    logger.warning("Sem rota definida para: %s", data)
