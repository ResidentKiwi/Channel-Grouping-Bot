import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

# 1️⃣ Autenticação de canal admin
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel":
        return
    sess = Session()
    ch = sess.get(Channel, msg.chat.id)
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    except Exception as e:
        logger.error("Erro ao obter admins: %s", e)
        return
    creator = next((a.user for a in admins if a.status == "creator" and not a.user.is_bot), None)
    if not creator:
        return
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
    keyboard = InlineKeyboardMarkup(kb)

    if update.message:
        await update.message.reply_text("Escolha:", reply_markup=keyboard)
    else:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Escolha:", reply_markup=keyboard)
    user_states.pop(uid, None)

# 3️⃣ Ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    texto = (
        "👋 Funcionalidades:\n"
        "- Criar grupos\n"
        "- Convidar canais\n"
        "- Explorar grupos\n"
        "- Solicitar entrada\n"
        "- Replicar posts\n"
        "- Sair de grupos\n"
        "Volte ao início com /start"
    )
    await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 4️⃣ Criar grupo
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    await update.callback_query.edit_message_text("📌 Digite o nome do grupo:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]))

# 5️⃣ "Meus grupos"
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        await update.callback_query.edit_message_text("🚫 Sem grupos ainda.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text("📂 Seus grupos:", reply_markup=InlineKeyboardMarkup(kb))

# 6️⃣ Explore
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        await update.callback_query.edit_message_text("Ainda não há grupos.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    kb = [[InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text("🌐 Grupos:", reply_markup=InlineKeyboardMarkup(kb))

# 7️⃣ Ver grupo
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
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
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# 8️⃣ Solicitar
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        await update.callback_query.edit_message_text("Você já solicitou ou está no grupo.")
        return
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        await update.callback_query.edit_message_text("❌ Canal não autenticado.")
        return
    g = sess.get(Group, gid)
    dono = sess.get(User, g.owner_id)
    link = f"https://t.me/{ch.username}" if ch.username else f"ID:{ch.id}"
    await ctx.bot.send_message(dono.id,
        f"📩 {ch.title} quer entrar no grupo *{g.name}*\nLink: {link}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")
        ]])
    )
    await update.callback_query.edit_message_text("✅ Solicitação enviada ao dono.")

# 9️⃣ Resposta
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    action, gid, cid = update.callback_query.data.split("_")[1:]
    gid, cid = int(gid), int(cid)
    sess = Session()
    if action == "aceitar":
        sess.add(GroupChannel(group_id=gid, channel_id=cid, accepted=True))
        sess.commit()
        await update.callback_query.edit_message_text("✅ Canal aceito.")
        await ctx.bot.send_message(cid, f"✅ Seu canal foi aceito no grupo *{sess.get(Group, gid).name}*")
    else:
        await update.callback_query.edit_message_text("❌ Solicitação recusada.")

# 🔟 Texto do grupo
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    state = user_states.get(uid)
    if state and state.get("state") == "awaiting_group_name":
        nome = update.message.text.strip()
        Session().add(Group(name=nome, owner_id=uid))
        Session().commit()
        await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
        user_states.pop(uid)

# 🔁 Replicar posts
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        grp = sess.get(Group, gc.group_id)
        for tgt in grp.channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# 🔂 Roteador
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    logger.info("Callback: %s", data)
    routes = {
        "start": start, "menu_ajuda": menu_ajuda,
        "criar_grupo": menu_criar_grupo,
        "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos
    }
    for prefix, fn in routes.items():
        if data == prefix:
            return await fn(update, ctx)
    if data.startswith("gerenciar_"):
        return await menu_meus_grupos(update, ctx)
    if data.startswith("vergrp_"):
        return await ver_grupo(update, ctx)
    if data.startswith("solicit_"):
        return await solicitar_entrada(update, ctx)
    if data.startswith(("aceitar_ext_", "recusar_ext_")):
        return await handle_ext_response(update, ctx)
