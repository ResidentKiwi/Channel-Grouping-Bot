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
        if "not modified" in str(e).lower(): return
        logger.exception("Erro ao editar mensagem")
        return

# 1️⃣ Autenticação de canal
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    creator = next((a.user for a in admins if a.status=="creator" and not a.user.is_bot), None)
    if not creator: return
    sess.merge(User(id=creator.id, username=creator.username))
    ch = sess.get(Channel, msg.chat.id)
    if not ch:
        ch = Channel(id=msg.chat.id, owner_id=creator.id,
                     username=msg.chat.username, title=msg.chat.title,
                     authenticated=True)
        sess.add(ch)
    else:
        ch.owner_id = creator.id
        ch.authenticated = True
    sess.commit()
    logger.info("🔐 Canal autenticado: %s", msg.chat.title)

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
        "• Criar grupos\n• Convidar canais (bot admin)\n"
        "• Explorar grupos públicos\n• Solicitar entrada ou sair\n"
        "• Replicar posts\n\nUse /start para voltar."
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

# 5️⃣ Criar grupo (texto)
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
            return await update.message.reply_text("❌ Envie @username ou t.me/...")
        username = match.group(1)
        try:
            chat = await ctx.bot.get_chat(username)
            admins = await ctx.bot.get_chat_administrators(chat.id)
            chan_owner = admins[0].user
        except Exception as e:
            return await update.message.reply_text(f"❌ Erro: {e}")

        sess.merge(Channel(id=chat.id, owner_id=chan_owner.id,
                           username=username, title=chat.title,
                           authenticated=False))
        sess.flush()
        gc = GroupChannel(group_id=st["group_id"], channel_id=chat.id, inviter_id=uid, accepted=None)
        sess.add(gc)
        sess.commit()
        await update.message.reply_text("✅ Convite enviado ao canal.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)

# 6️⃣ Meus canais
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Você não tem canais autenticados.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 7️⃣ Meus grupos
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await safe_edit(update.callback_query, "🚫 Você não criou nenhum grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# 8️⃣ Gerenciar grupo
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session(); g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await safe_edit(update.callback_query, f"🎯 Grupo: *{g.name}*", InlineKeyboardMarkup(kb))

# 9️⃣ Convite interno
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    g = sess.get(Group, gid)
    ch = sess.get(Channel, cid)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, f"✅ {ch.title} entrou no grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        await ctx.bot.send_message(g.owner_id, f"✅ Canal {ch.title} aceitou convite.")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, f"❌ {ch.title} recusou.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        await ctx.bot.send_message(g.owner_id, f"❌ Canal {ch.title} recusou convite.")

# 🔟 Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await safe_edit(update.callback_query, "Ainda sem grupos.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# 1️⃣1️⃣ Ver grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session(); g = sess.get(Group, gid)
    participants = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:"
    for gc in participants:
        ch = sess.get(Channel, gc.channel_id)
        try: subs = await ctx.bot.get_chat_members_count(ch.id)
        except: subs="?"
        link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
        text += f"\n- [{ch.title}]({link}) — {subs}"
    kb = [
        [InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]
    ]
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))

# 1️⃣2️⃣ Solicitar entrada externa
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
    sess.add(GroupChannel(group_id=gid, channel_id=uid, inviter_id=uid, accepted=None))
    sess.commit()
    await ctx.bot.send_message(g.owner_id, f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*",
                               parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([[
                                   InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
                                   InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")
                               ]]))
    await safe_edit(update.callback_query, "✅ Solicitação enviada.")

# 1️⃣3️⃣ Responder solicitação externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    g = sess.get(Group, gid)
    ch = sess.get(Channel, cid)
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if action == "aceitar_ext":
        gc.accepted = True
        sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito.")
        await ctx.bot.send_message(cid, f"✅ Seu canal foi aceito no grupo *{g.name}*")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")

# 1️⃣4️⃣ Remover canal
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Nenhum canal para remover.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
    kb = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title, callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await safe_edit(update.callback_query, "Escolha canal para remover:", InlineKeyboardMarkup(kb))

async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await gerenciar_grupo(update, ctx)

# 1️⃣5️⃣ Apagar grupo
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim, apagar", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"gerenciar_{gid}")]
    ])
    await safe_edit(update.callback_query, "⚠️ Tem certeza?", markup)

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    return await menu_meus_grupos(update, ctx)

# 1️⃣6️⃣ Sair de grupo
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Não está em nenhum grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_confirm_{gc.group_id}_{uid}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "Escolha grupo para sair:", InlineKeyboardMarkup(kb))

async def sair_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await menu_sair_grupo(update, ctx)

# 1️⃣7️⃣ Replicar posts
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        for tgt in sess.get(Group, gc.group_id).channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# 🔁 Central de callbacks
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Callback: %s", data)
    mapping = {
        "start": start, "menu_ajuda": menu_ajuda, "criar_grupo": menu_criar_grupo,
        "menu_meus_canais": menu_meus_canais, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos, "menu_sair_grupo": menu_sair_grupo
    }
    if data in mapping:
        return await mapping[data](update, ctx)
    prefix = data.split("_")[0]
    route_map = {
        "gerenciar": gerenciar_grupo, "convite": handle_convite_response,
        "aceitar": handle_convite_response, "recusar": handle_convite_response,
        "vergrp": ver_grupo, "solicit": solicitar_entrada,
        "aceitar_ext": handle_ext_response, "recusar_ext": handle_ext_response,
        "remover": remocao_canal, "remover_confirm": remover_confirm,
        "delete": prompt_delete_group, "delete_confirm": delete_confirm,
        "sair_confirm": sair_confirm
    }
    if prefix in route_map:
        return await route_map[prefix](update, ctx)
    await update.callback_query.answer("⚠️ Ação não reconhecida.")
