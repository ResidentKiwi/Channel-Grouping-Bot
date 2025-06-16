import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
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
            logger.debug("Ignore not modified")
            return
        raise

# 1️⃣ Autenticar canal quando poste (bot como admin)
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel": return
    sess = Session()
    ch = sess.get(Channel, msg.chat.id)
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    except Exception as e:
        logger.error("get_chat_administrators error: %s", e)
        return
    creator = next((a.user for a in admins if a.status == "creator" and not a.user.is_bot), None)
    if not creator: return
    sess.merge(User(id=creator.id, username=creator.username))
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
        ch.owner_id = creator.id
        ch.authenticated = True
    sess.commit()
    logger.info("Canal autenticado: %s", msg.chat.title)

# 2️⃣ Menu principal e registro usuário
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
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")],
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
        "• Criar grupos\n"
        "• Convidar canais (bot admin)\n"
        "• Explorar/grupos públicos\n"
        "• Solicitar entrada / Sair de grupos\n"
        "• Replicar posts\n\n"
        "Use /start para voltar"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
    await safe_edit(update.callback_query, txt, markup)

# 4️⃣ Criar grupo
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]])
    await safe_edit(update.callback_query, "📌 Envie o nome do novo grupo:", markup)

# Handle create via text
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st: return

    sess = Session()
    if st.get("state") == "awaiting_group_name":
        nome = update.message.text.strip()
        sess.add(Group(name=nome, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
    elif st.get("state") == "awaiting_channel_invite":
        text = update.message.text.strip()
        match = re.search(r"@([A-Za-z0-9_]+)", text) or re.search(r"t\.me/([A-Za-z0-9_]+)", text)
        if not match:
            return await update.message.reply_text("❌ Formato inválido. Envie @username ou t.me/...")

        username = match.group(1)
        chat = await ctx.bot.get_chat(username)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner_id = admins[0].user.id if admins else None
        channel = sess.get(Channel, chat.id) or Channel(
            id=chat.id,
            owner_id=chan_owner_id,
            username=username,
            title=chat.title,
            authenticated=False,
        )
        sess.add(channel)
        sess.flush()

        gid = st["group_id"]
        gc = GroupChannel(group_id=gid, channel_id=channel.id, inviter_id=uid, accepted=None)
        sess.add(gc)
        sess.commit()

        group = sess.get(Group, gid)
        participantes = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
        lista = "\n".join(f"- {sess.get(Channel, gc2.channel_id).title}" for gc2 in participantes) or "nenhum canal."
        kb = [
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{gid}_{channel.id}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{gid}_{channel.id}")
        ]
        await ctx.bot.send_message(
            chat_id=chan_owner_id,
            text=f"📨 *Convite* — grupo *{group.name}*\nCanais no grupo:\n{lista}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([kb])
        )
        await update.message.reply_text("✅ Convite enviado ao canal.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)

# 5️⃣ Meus canais
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Você não tem canais.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 6️⃣ Meus grupos – listar e gerenciar
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await safe_edit(update.callback_query, "🚫 Você não criou grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# 5.1️⃣ Gerenciar um grupo específico
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    text = f"🎯 Grupo: *{g.name}*"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))

# 7️⃣ Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    all_grps = sess.query(Group).all()
    if not all_grps:
        return await safe_edit(update.callback_query, "Sem grupos ainda.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")] for g in all_grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# 8️⃣ Ver detalhes de grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    participants = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    txt = f"📁 *{g.name}*\nCanais:"
    for gc in participants:
        ch = sess.get(Channel, gc.channel_id)
        try:
            subs = await ctx.bot.get_chat_members_count(ch.id)
        except:
            subs = "?"
        link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
        txt += f"\n- [{ch.title}]({link}) — {subs}"
    kb = [[InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
          [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]]
    await safe_edit(update.callback_query, txt, InlineKeyboardMarkup(kb))

# 9️⃣ Solicitar entrada
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        return await safe_edit(update.callback_query, "Você já está ou solicitou.")
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        return await safe_edit(update.callback_query, "❌ Canal não autenticado.")
    g = sess.get(Group, gid)
    dono = sess.get(User, g.owner_id)
    link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
    await ctx.bot.send_message(
        dono.id,
        f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*\n{link}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")
        ]])
    )
    await safe_edit(update.callback_query, "✅ Solicitação enviada ao dono.")

# 1️⃣0️⃣ Resposta solicitação externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    g = sess.get(Group, gid)
    if action == "aceitar":
        sess.add(GroupChannel(group_id=gid, channel_id=cid, inviter_id=g.owner_id, accepted=True))
        sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito.")
        await ctx.bot.send_message(cid, f"✅ Seu canal foi aceito no grupo *{g.name}*")
    else:
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")

# 1️⃣1️⃣ Replicar posts
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        grp = sess.get(Group, gc.group_id)
        for tgt in grp.channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# 🧭 Rotas principais
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Button clicked: %s", data)
    routes = {
        "start": start, "menu_ajuda": menu_ajuda,
        "criar_grupo": menu_criar_grupo, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos
    }
    for key, fn in routes.items():
        if data == key:
            return await fn(update, ctx)
    if data.startswith("gerenciar_"):
        return await gerenciar_grupo(update, ctx)
    if data.startswith("convite_"):
        uid = update.callback_query.from_user.id
        gid = int(data.split("_")[1])
        user_states[uid] = {"state":"awaiting_channel_invite", "group_id":gid}
        return await safe_edit(update.callback_query, "📥 Envie @username ou link do canal:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]))
    if data.startswith("vergrp_"):
        return await ver_grupo(update, ctx)
    if data.startswith("solicit_"):
        return await solicitar_entrada(update, ctx)
    if data.startswith(("aceitar_ext_", "recusar_ext_")):
        return await handle_ext_response(update, ctx)
    logger.warning("Sem rota definida para: %s", data)
    await update.callback_query.answer("⚠️ Ação não reconhecida.", show_alert=True)
