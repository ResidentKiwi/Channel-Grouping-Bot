import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_states: dict[int, dict] = {}

# --- Start/Menu principal ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info("start() called by %s", uid)
    sess = Session()
    user = sess.get(User, uid)
    if not user:
        user = User(id=uid, username=update.effective_user.username)
        sess.add(user); sess.commit()

    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0

    kb = []
    if owns: kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
    ]
    if participates:
        kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    reply_markup = InlineKeyboardMarkup(kb)
    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text("Escolha uma opção:", reply_markup=reply_markup)

    user_states.pop(uid, None)


# --- Menu Ajuda ---
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    texto = (
        "👋 Use o bot para:\n"
        "• Criar e gerenciar grupos\n"
        "• Convidar canais por @username ou link t.me\n"
        "• Sair de grupos que participa\n"
        "• Posts são replicados entre canais do grupo\n"
        "Use /start ou botões para navegar."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await q.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))


# --- Criar Grupo ---
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]
    await q.edit_message_text("📌 Envie o *nome do grupo*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


# --- Meus Grupos (proprietário) ---
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    sess = Session()
    groups = sess.query(Group).filter_by(owner_id=uid).all()
    if not groups:
        return await q.edit_message_text(
            "🚫 Você não tem grupos.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]),
        )
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in groups]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Selecione um grupo:", reply_markup=InlineKeyboardMarkup(kb))


# --- Gerenciar Grupo (proprietário) ---
async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session(); g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await q.edit_message_text(f"🎯 Gerenciar *{g.name}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


# --- Convidar canal ---
async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    gid = int(q.data.split("_")[-1])
    user_states[uid] = {"state": "awaiting_channel_invite", "group_id": gid}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]
    await q.edit_message_text("📥 Envie o @username ou link t.me do canal:", reply_markup=InlineKeyboardMarkup(kb))


# --- Sair de grupo (canal proprietário) ---
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    sess = Session()
    grps = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not grps:
        return await q.edit_message_text(
            "🚫 Você não participa de nenhum grupo.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]),
        )
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_{gc.group_id}")] for gc in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Selecione um grupo para sair:", reply_markup=InlineKeyboardMarkup(kb))


async def sair_grupo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    uid = q.from_user.id
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first()
    if gc: sess.delete(gc); sess.commit()
    return await start(update, ctx)


# --- Handle mensagens de texto ---
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st:
        logger.info("handle_text_message(): sem estado para %s", uid)
        return

    text = update.message.text.strip()
    sess = Session()

    # Criar grupo
    if st["state"] == "awaiting_group_name":
        sess.add(User(id=uid, username=update.effective_user.username), flush=True)
        sess.add(Group(name=text, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{text}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
        return

    # Convidar canal
    if st["state"] == "awaiting_channel_invite":
        m = re.search(r"@([\w_]+)|t\.me/([\w_]+)", text)
        if not m:
            return await update.message.reply_text("❌ Formato inválido.")
        username = m.group(1) or m.group(2)
        chat = await ctx.bot.get_chat(username)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner = admins[0].user.id

        channel = sess.get(Channel, chat.id) or Channel(id=chat.id, owner_id=chan_owner, username=username, title=chat.title)
        sess.add(channel); sess.flush()

        gid = st["group_id"]
        sess.add(GroupChannel(group_id=gid, channel_id=chat.id, accepted=None))
        sess.commit()

        await update.message.reply_text(f"✅ Convite enviado para @{username}!", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]
        ))
        user_states.pop(uid)
        return


# --- Aceitar ou recusar convite ---
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, action, gid, cid = q.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await q.edit_message_text("❌ Convite inválido.")
    chan = sess.get(Channel, cid)
    group = sess.get(Group, gid)
    dono = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True
        sess.commit()
        await q.edit_message_text(f"✅ Canal *{chan.title}* entrou no grupo.")
        await ctx.bot.send_message(dono.id, f"✅ @{chan.username} aceitou o convite para *{group.name}*.")
    else:
        sess.delete(gc); sess.commit()
        await q.edit_message_text(f"❌ Canal *{chan.title}* recusou o convite.")
        await ctx.bot.send_message(dono.id, f"❌ @{chan.username} recusou o convite para *{group.name}*.")


# --- Remover canal (proprietário) ---
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session()
    canales = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not canales:
        return await q.edit_message_text("🚫 Sem canais no grupo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
    kb = [
        [InlineKeyboardButton(sess.get(Channel, c.channel_id).title, callback_data=f"remover_confirm_{gid}_{c.channel_id}")]
        for c in canales
    ]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await q.edit_message_text("Selecione canal para remover:", reply_markup=InlineKeyboardMarkup(kb))


async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, _, gid, cid = q.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc: sess.delete(gc); sess.commit()
    return await handle_grupo_actions(update, ctx)


# --- Apagar grupo ---
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    kb = [
        [InlineKeyboardButton("✅ Sim, apagar", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ Voltar", callback_data=f"gerenciar_{gid}")]
    ]
    await q.edit_message_text("⚠️ Confirmar apagar grupo?", reply_markup=InlineKeyboardMarkup(kb))


async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    gid = int(q.data.split("_")[-1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    await q.edit_message_text("✅ Grupo apagado.")
    return await menu_meus_grupos(update, ctx)


# --- Meus canais ---
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await q.edit_message_text("🚫 Sem canais.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    texto = "📋 Meus canais:"
    for c in chans:
        texto += f"\n• {c.title} @{c.username}"
    await q.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))


# --- Replicar posts ---
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    logger.info("new_post from %s", msg.chat.id)
    sess = Session()
    targets = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in targets:
        others = sess.query(GroupChannel).filter_by(group_id=gc.group_id, accepted=True).all()
        for tc in others:
            if tc.channel_id != msg.chat.id:
                await forward(msg.chat.id, tc.channel_id, msg.message_id)


# --- Callback handler ---
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    logger.info("CB: %s", data)
    mapping = {
        "start": start,
        "menu_ajuda": menu_ajuda,
        "criar_grupo": menu_criar_grupo,
        "menu_meus_grupos": menu_meus_grupos,
        "menu_meus_canais": menu_meus_canais,
        "menu_sair_grupo": menu_sair_grupo,
    }
    for prefix, handler in mapping.items():
        if data == prefix or data.startswith(prefix + "_"):
            return await handler(update, ctx)

    if data.startswith(("aceitar_", "recusar_")):
        return await handle_convite_response(update, ctx)
    if data.startswith("convite_"):
        return await convite_canal(update, ctx)
    if data.startswith("sair_"):
        return await sair_grupo_confirm(update, ctx)
    if data.startswith("remover_confirm_"):
        return await remover_confirm(update, ctx)
    if data.startswith("remover_"):
        return await remocao_canal(update, ctx)
    if data.startswith("delete_confirm_"):
        return await delete_confirm(update, ctx)
    if data.startswith("delete_"):
        return await prompt_delete_group(update, ctx)


# End of handlers.py
