import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_states: dict[int, dict] = {}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info("start() called by %s", uid)
    sess = Session()
    user = sess.get(User, uid)
    if not user:
        user = User(id=uid, username=update.effective_user.username)
        sess.add(user)
        sess.commit()
    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    kb = []
    if owns:
        kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")],
    ]
    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.edit_message_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(kb))
    user_states.pop(uid, None)

async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    logger.info("menu_ajuda() called by %s", uid)
    q = update.callback_query; await q.answer()
    texto = (
        "👋 Use o bot para criar e gerenciar grupos de canais.\n"
        "• Crie um grupo e convide canais por @ ou link t.me\n"
        "• O dono do canal vê o nome do grupo e quem já está nele\n"
        "• Aceite ou recuse convite direto na mensagem\n"
        "• Posts são replicados automaticamente\n\n"
        "Use os botões ou /start para voltar ao início."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await q.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))

async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    logger.info("menu_criar_grupo() clicked by %s", uid)
    q = update.callback_query; await q.answer()
    user_states[uid] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]
    await q.edit_message_text("📌 Envie o *nome do grupo* que deseja criar:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    logger.info("menu_meus_grupos() called by %s", uid)
    q = update.callback_query; await q.answer()
    sess = Session()
    groups = sess.query(Group).filter_by(owner_id=uid).all()
    if not groups:
        return await q.edit_message_text(
            "🚫 Você não tem grupos. Crie um primeiro com ➕ Criar grupo.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        )
    buttons = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in groups]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Selecione um grupo para gerenciar:", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    gid = int(data.split("_")[-1])
    uid = update.callback_query.from_user.id
    logger.info("handle_grupo_actions() by %s for group %s", uid, gid)
    q = update.callback_query; await q.answer()
    g = Session().get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await q.edit_message_text(f"🎯 Gestão do grupo: *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    data = update.callback_query.data
    gid = int(data.split("_")[-1])
    logger.info("convite_canal() by %s for group %s", uid, gid)
    q = update.callback_query; await q.answer()
    user_states[uid] = {"state": "awaiting_channel_invite", "group_id": gid}
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]
    await q.edit_message_text("📥 Envie o *@username* ou *link t.me/* do canal:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message:
        return
    text = update.message.text.strip()
    st = user_states.get(uid)
    if not st:
        logger.info("handle_text_message(): no state for user %s", uid)
        return
    sess = Session()
    if st["state"] == "awaiting_group_name":
        logger.info("Creating group with name '%s' for user %s", text, uid)
        user = sess.get(User, uid) or User(id=uid, username=update.effective_user.username)
        sess.add(user); sess.flush()
        grupo = Group(name=text, owner_id=uid)
        sess.add(grupo); sess.commit()
        await update.message.reply_text(f"✅ Grupo *{text}* criado com sucesso!", parse_mode="Markdown")
        user_states.pop(uid, None)
        return
    if st["state"] == "awaiting_channel_invite":
        logger.info("Inviting channel '%s' for user %s", text, uid)
        match = re.search(r"@([A-Za-z0-9_]+)", text) or re.search(r"(?:t\.me/)([A-Za-z0-9_]+)", text)
        if not match:
            return await update.message.reply_text("❌ Formato inválido. Envie @username ou t.me/username")
        username = match.group(1)
        chat = await ctx.bot.get_chat(username)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner_id = admins[0].user.id
        channel = sess.get(Channel, chat.id) or Channel(id=chat.id, owner_id=chan_owner_id, username=username, title=chat.title)
        sess.add(channel); sess.flush()
        gid = st["group_id"]
        gc = GroupChannel(group_id=gid, channel_id=channel.id, accepted=None)
        sess.add(gc); sess.commit()
        group = sess.get(Group, gid)
        participantes = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
        lista = "\n".join(
            f"- [{sess.get(Channel, gc2.channel_id).title}](https://t.me/{sess.get(Channel, gc2.channel_id).username})"
            for gc2 in participantes if sess.get(Channel, gc2.channel_id).username
        ) or "nenhum canal ainda."
        kb = [
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{gid}_{channel.id}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{gid}_{channel.id}")
        ]
        await ctx.bot.send_message(
            chat_id=chan_owner_id,
            text=f"📨 Convite para o canal entrar no grupo *{group.name}*.\nCanais já integrantes:\n{lista}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([kb])
        )
        await update.message.reply_text("✅ Convite enviado!", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]
        ))
        user_states.pop(uid, None)
        return

async def handle_convite_response(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("handle_convite_response() %s", update.callback_query.data)
    q = update.callback_query; await q.answer()
    _, action, gid, cid = q.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await q.edit_message_text("❌ Convite inválido.")
    group = sess.get(Group, gid)
    chan = sess.get(Channel, cid)
    dono = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True
        sess.commit()
        await q.edit_message_text(f"✅ Canal *{chan.title}* entrou no grupo *{group.name}*.", parse_mode="Markdown")
        await ctx.bot.send_message(dono.id, f"✅ Canal {chan.title} aceitou o convite no grupo *{group.name}*.")
    else:
        sess.delete(gc); sess.commit()
        await q.edit_message_text(f"❌ Canal *{chan.title}* recusou o convite para o grupo *{group.name}*.", parse_mode="Markdown")
        await ctx.bot.send_message(dono.id, f"❌ Canal {chan.title} recusou o convite para o grupo *{group.name}*.")

async def remocao_canal(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("remocao_canal() %s", update.callback_query.data)
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session()
    canais = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not canais:
        return await q.edit_message_text("🚫 Nenhum canal para remover.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
    buttons = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title, callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in canais]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await q.edit_message_text("Escolha o canal para remover do grupo:", reply_markup=InlineKeyboardMarkup(buttons))

async def remover_confirm(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("remover_confirm() %s", update.callback_query.data)
    q = update.callback_query; await q.answer()
    _, _, gid, cid = q.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await handle_grupo_actions(update, ctx)

async def prompt_delete_group(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("prompt_delete_group() %s", update.callback_query.data)
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    kb = [
        [InlineKeyboardButton("✅ Sim, apagar", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"gerenciar_{gid}")],
    ]
    await q.edit_message_text(
        "⚠️ Tem certeza que deseja apagar esse grupo? Esta ação é permanente.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )

async def delete_confirm(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("delete_confirm() %s", update.callback_query.data)
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    await q.edit_message_text("✅ Grupo apagado com sucesso.")
    return await menu_meus_grupos(update, ctx)

async def menu_meus_canais(update: Update, ctx: ContextTypes.DefaultTYPE):
    uid = update.callback_query.from_user.id
    logger.info("menu_meus_canais() called by %s", uid)
    q = update.callback_query; await q.answer()
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await q.edit_message_text(
            "🚫 Você não tem canais registrados.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        )
    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else f"ID:{c.id}"
        text += f"\n• {c.title} — {link}"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

async def new_post(update: Update, ctx: ContextTypes.DefaultTYPE):
    msg = update.channel_post
    if not msg:
        return
    logger.info("new_post() from channel %s", msg.chat.id)
    sess = Session()
    targets = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in targets:
        grupo = sess.get(Group, gc.group_id)
        for tc in grupo.channels:
            if tc.accepted and tc.channel_id != msg.chat.id:
                await forward(msg.chat.id, tc.channel_id, msg.message_id)

async def handle_callback_query(update: Update, ctx: ContextTypes.DefaultTYPE):
    logger.info("handle_callback_query() %s", update.callback_query.data)
    data = update.callback_query.data
    if data.startswith(("aceitar_", "recusar_")):
        return await handle_convite_response(update, ctx)
    if data == "criar_grupo":
        return await menu_criar_grupo(update, ctx)
    if data == "menu_meus_grupos":
        return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_"):
        return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"):
        return await convite_canal(update, ctx)
    if data.startswith("remover_"):
        return await remocao_canal(update, ctx)
    if data.startswith("remover_confirm_"):
        return await remover_confirm(update, ctx)
    if data.startswith("delete_") and "confirm" not in data:
        return await prompt_delete_group(update, ctx)
    if data.startswith("delete_confirm_"):
        return await delete_confirm(update, ctx)
    if data == "menu_meus_canais":
        return await menu_meus_canais(update, ctx)
    if data == "menu_ajuda":
        return await menu_ajuda(update, ctx)
    if data == "start":
        return await start(update, ctx)
