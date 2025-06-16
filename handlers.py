import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_states: dict[int, dict] = {}

# --- Início e menu principal ---
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
    if owns:
        kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
    ]
    if participates:
        kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.edit_message_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(kb))

    user_states.pop(uid, None)

# --- Ajuda ---
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    texto = (
        "👋 Use o bot para:\n"
        "• Criar e gerenciar grupos\n"
        "• Convidar canais por @link ou t.me/link\n"
        "• Sair de grupos de canais que participa\n"
        "• Receber posts replicados automaticamente\n"
        "Use os botões ou /start para voltar ao início."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await q.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))

# --- Criar grupo ---
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    q = update.callback_query; await q.answer()
    user_states[uid] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]
    await q.edit_message_text("📌 Envie o *nome do grupo*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# --- Listar meus grupos ---
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    q = update.callback_query; await q.answer()
    sess = Session()
    groups = sess.query(Group).filter_by(owner_id=uid).all()
    if not groups:
        return await q.edit_message_text(
            "🚫 Você não tem grupos.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        )
    buttons = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in groups]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(buttons))

# --- Gerenciar grupo ---
async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    gid = int(data.split("_")[-1]); sess = Session(); g = sess.get(Group, gid)
    q = update.callback_query; await q.answer()
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await q.edit_message_text(f"🎯 Gerenciar *{g.name}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# --- Convidar canal ao grupo ---
async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id; gid = int(update.callback_query.data.split("_")[-1])
    q = update.callback_query; await q.answer()
    user_states[uid] = {"state": "awaiting_channel_invite", "group_id": gid}
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]
    await q.edit_message_text("📥 Envie o @username ou link t.me/...", reply_markup=InlineKeyboardMarkup(kb))

# --- Sair de grupo (novo) ---
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    sess = Session()
    groups = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    q = update.callback_query; await q.answer()
    if not groups:
        return await q.edit_message_text("🚫 Você não está em nenhum grupo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_{gc.group_id}")] for gc in groups]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await q.edit_message_text("Escolha um grupo para sair:", reply_markup=InlineKeyboardMarkup(kb))

async def sair_grupo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = int(update.callback_query.data.split("_")[-1]); sess = Session()
    q = update.callback_query; await q.answer()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=update.callback_query.from_user.id).first()
    if gc:
        sess.delete(gc); sess.commit()
    await q.edit_message_text("✅ Você saiu do grupo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# --- Captura de texto (criação de grupo ou convite de canal) ---
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st: return
    text = update.message.text.strip(); sess = Session()
    if st["state"] == "awaiting_group_name":
        user = sess.get(User, uid) or User(id=uid, username=update.effective_user.username)
        sess.add(user); sess.flush()
        sess.add(Group(name=text, owner_id=uid)); sess.commit()
        await update.message.reply_text(f"✅ Grupo *{text}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
    elif st["state"] == "awaiting_channel_invite":
        match = re.search(r"@([\w_]+)|(t\.me/([\w_]+))", text)
        if not match: return await update.message.reply_text("❌ Formato inválido.")
        username = match.group(1) or match.group(3)
        chat = await ctx.bot.get_chat(username)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner_id = admins[0].user.id
        channel = sess.get(Channel, chat.id) or Channel(id=chat.id, owner_id=chan_owner_id, username=username, title=chat.title)
        sess.add(channel); sess.flush()
        gid = st["group_id"]
        sess.add(GroupChannel(group_id=gid, channel_id=channel.id, accepted=None)); sess.commit()
        # envia convite...
        await update.message.reply_text("✅ Convite enviado!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)

# --- Resposta ao convite ---
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, ac, gid, cid = q.data.split("_"); sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc: return await q.edit_message_text("❌ Convite inválido.")
    if ac == "aceitar":
        gc.accepted = True; sess.commit()
        await q.edit_message_text("✅ Você entrou no grupo!")
    else:
        sess.delete(gc); sess.commit()
        return await q.edit_message_text("❌ Convite recusado.")
    await q.edit_message_text("✅ Canal entrou no grupo.")

# --- Remover canal do grupo ---
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ...
    pass

# --- Apagar grupo ---
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ...
    pass

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ...
    pass

# --- Replicar posts entre canais ---
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    targets = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in targets:
        for tc in sess.query(GroupChannel).filter_by(group_id=gc.group_id, accepted=True).all():
            if tc.channel_id != msg.chat.id:
                await forward(msg.chat.id, tc.channel_id, msg.message_id)

# --- Callback router ---
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith(("aceitar_", "recusar_")): return await handle_convite_response(update, ctx)
    if data == "criar_grupo": return await menu_criar_grupo(update, ctx)
    if data == "menu_meus_grupos": return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_"): return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"): return await convite_canal(update, ctx)
    if data == "menu_sair_grupo": return await menu_sair_grupo(update, ctx)
    if data.startswith("sair_"): return await sair_grupo_confirm(update, ctx)
    # restantes: remover, apagar, voltar, etc.
    if data in ("start", "menu_ajuda"): return await start(update, ctx)
