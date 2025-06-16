from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward
import re

# Estado temporário de criação/convite
user_states: dict[int, dict] = {}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=update.effective_user.id).count() > 0
    kb = []
    if owns:
        kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")],
    ]
    await update.message.reply_text("Escolha:", reply_markup=InlineKeyboardMarkup(kb))
    user_states.pop(update.effective_user.id, None)

async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    texto = (
        "👋 Use o bot para criar e gerenciar grupos de canais.\n"
        "• Crie um grupo e libere o convite de canais por @ ou link.\n"
        "• O dono do canal vê o nome do grupo e canais já no mesmo antes de aceitar.\n"
        "• Posts são replicados automaticamente.\n"
        "Use os botões ou /start para começar."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await q.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))

async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    user_states[update.effective_user.id] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await q.edit_message_text("📌 Envie o nome do grupo:", reply_markup=InlineKeyboardMarkup(kb))

async def receive_group_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st or st.get("state") != "awaiting_group_name":
        return

    nome = update.message.text.strip()
    sess = Session()
    user = sess.get(User, uid) or User(id=uid, username=update.effective_user.username)
    if not user.id:
        sess.add(user); sess.commit()

    grupo = Group(name=nome, owner_id=uid)
    sess.add(grupo); sess.commit()

    await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
    user_states.pop(uid, None)

async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=q.from_user.id).all()
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in owns]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session()
    g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await q.edit_message_text(f"Grupo: *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    user_states[q.from_user.id] = {"state": "awaiting_channel_invite", "group_id": gid}
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]
    await q.edit_message_text(
        "📥 Envie o @username ou link privado do canal que deseja convidar:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def receive_channel_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st or st.get("state") != "awaiting_channel_invite":
        return

    text = update.message.text.strip()
    match = re.search(r"@([A-Za-z0-9_]+)", text) or re.search(r"t\.me/([A-Za-z0-9_]+)", text)
    if not match:
        return await update.message.reply_text("❌ Formato inválido. Envie @username ou link t.me/...")
    username = match.group(1)

    sess = Session()
    chat = await ctx.bot.get_chat(username)
    chan_owner_id = chat.get_member(chat.id).user.id
    channel = sess.get(Channel, chat.id) or Channel(id=chat.id, owner_id=chan_owner_id, username=username, title=chat.title)
    sess.add(channel); sess.commit()

    gid = st["group_id"]
    gc = GroupChannel(group_id=gid, channel_id=channel.id, accepted=None)
    sess.add(gc); sess.commit()

    # mensagem ao dono do canal
    group = sess.get(Group, gid)
    participantes = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    lista = "\n".join(
        f"- [{sess.get(Channel, gc2.channel_id).title}](https://t.me/{sess.get(Channel, gc2.channel_id).username})"
        for gc2 in participantes
    ) or "nenhum canal ainda."
    kb = [
        InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{gid}_{channel.id}"),
        InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{gid}_{channel.id}")
    ]
    await ctx.bot.send_message(
        chat_id=chan_owner_id,
        text=(
            f"📨 *Convite para entrar no grupo* *{group.name}*.\n"
            f"Canais já no grupo:\n{lista}"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([kb])
    )

    await update.message.reply_text("✅ Convite enviado ao canal.")
    user_states.pop(uid, None)

async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, action, gid, cid = q.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await q.edit_message_text("❌ Convite inválido.")
    group = sess.get(Group, gid)
    chan = sess.get(Channel, cid)
    owner = update.effective_user

    if action == "aceitar":
        gc.accepted = True
        sess.commit()
        await q.edit_message_text(f"✅ Canal *{chan.title}* entrou no grupo *{group.name}*.", parse_mode="Markdown")

        dono = sess.get(User, group.owner_id)
        await ctx.bot.send_message(dono.id, f"✅ Canal {chan.title} aceitou convite no grupo *{group.name}*.")
    else:
        sess.delete(gc); sess.commit()
        await q.edit_message_text("❌ Convite recusado.")
        dono = sess.get(User, group.owner_id)
        await ctx.bot.send_message(dono.id, f"❌ Canal {chan.title} recusou convite para o grupo *{group.name}*.")

async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=q.from_user.id).all()
    text = "📋 *Seus canais:*"
    for c in chans:
        text += f"\n• [{c.title}](https://t.me/{c.username}) — ID:{c.id}"
    await q.edit_message_text(text, parse_mode="Markdown")

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    targets = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in targets:
        grupo = sess.get(Group, gc.group_id)
        for tc in grupo.channels:
            if tc.accepted and tc.channel_id != msg.chat.id:
                await forward(msg.chat.id, tc.channel_id, msg.message_id)

async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith("aceitar_") or data.startswith("recusar_"):
        return await handle_convite_response(update, ctx)
    if data.startswith("criar_grupo"):
        return await menu_criar_grupo(update, ctx)
    if data.startswith("menu_meus_grupos"):
        return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_"):
        return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"):
        return await convite_canal(update, ctx)
    if data.startswith("remover_"):
        return await remocao_canal(update, ctx)
    if data.startswith("delete_"):
        return await prompt_delete_group(update, ctx)
    if data.startswith("delete_confirm_"):
        return await delete_confirm(update, ctx)
    if data == "menu_meus_canais":
        return await menu_meus_canais(update, ctx)
    if data == "menu_ajuda":
        return await menu_ajuda(update, ctx)
    if data == "start":
        return await start(update, ctx)
