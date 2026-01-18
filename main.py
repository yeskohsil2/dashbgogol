import os
import random
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode

# --- Конфигурация ---
TOKEN = "8569477881:AAGY3GMp6QbbnPT7KwM64KnNUcvUjQAPQMM"
REDIS_URL = "redis://default:tjepMmKSP7z0odtRo4fTaAAUu5R72saB@redis-11341.c256.us-east-1-2.ec2.cloud.redislabs.com:11341"

# Подключение к Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# --- Вспомогательные функции Redis ---
def get_chat_key(chat_id: int) -> str:
    return f"chat:{chat_id}"

def get_user_key(chat_id: int, user_id: int) -> str:
    return f"chat:{chat_id}:user:{user_id}"

def get_phrases_key(chat_id: int) -> str:
    return f"chat:{chat_id}:phrases"

def get_media_key(chat_id: int, media_type: str) -> str:
    return f"chat:{chat_id}:media:{media_type}"

def get_blacklist_key(chat_id: int) -> str:
    return f"chat:{chat_id}:blacklist"

def get_muted_key(chat_id: int) -> str:
    return f"chat:{chat_id}:muted"

def get_staff_key(chat_id: int) -> str:
    return f"chat:{chat_id}:staff"

# --- Загрузка файла с ответами ---
RESPONSE_DICT = {}
try:
    with open('stroki.txt', 'r', encoding='utf-8') as f:
        current_key = None
        for line in f:
            line = line.strip()
            if ' = {' in line:
                parts = line.split(' = {')
                current_key = parts[0].strip().lower()
                value_part = parts[1].rstrip('}')
                RESPONSE_DICT[current_key] = [v.strip().strip('"\'') for v in value_part.split(',') if v.strip()]
            elif current_key and line.endswith('}'):
                value_part = line.rstrip('}')
                RESPONSE_DICT[current_key].extend([v.strip().strip('"\'') for v in value_part.split(',') if v.strip()])
            elif current_key and line:
                RESPONSE_DICT[current_key].extend([v.strip().strip('"\'') for v in line.split(',') if v.strip()])
except FileNotFoundError:
    print("Файл stroki.txt не найден. Ответы на оскорбления не загружены.")

# --- Функции для развлекательной части ---
def save_phrase(chat_id: int, text: str):
    if len(text) > 10:
        redis_client.lpush(get_phrases_key(chat_id), text)
        redis_client.ltrim(get_phrases_key(chat_id), 0, 99)

def save_media_id(chat_id: int, media_type: str, file_id: str):
    redis_client.lpush(get_media_key(chat_id, media_type), file_id)
    redis_client.ltrim(get_media_key(chat_id, media_type), 0, 49)

def get_random_phrase(chat_id: int) -> Optional[str]:
    phrases = redis_client.lrange(get_phrases_key(chat_id), 0, -1)
    return random.choice(phrases) if phrases else None

def get_random_media(chat_id: int, media_type: str) -> Optional[str]:
    media_list = redis_client.lrange(get_media_key(chat_id, media_type), 0, -1)
    return random.choice(media_list) if media_list else None

def should_bot_respond(chat_id: int) -> bool:
    return random.random() < 0.05

# --- Функции для админки ---
def get_staff_role(chat_id: int, user_id: int) -> int:
    role = redis_client.hget(get_staff_key(chat_id), str(user_id))
    return int(role) if role else 0

def set_staff_role(chat_id: int, user_id: int, role: int):
    redis_client.hset(get_staff_key(chat_id), str(user_id), role)

def remove_staff_role(chat_id: int, user_id: int):
    redis_client.hdel(get_staff_key(chat_id), str(user_id))

def get_staff_list(chat_id: int) -> Dict[int, int]:
    data = redis_client.hgetall(get_staff_key(chat_id))
    return {int(k): int(v) for k, v in data.items()}

def is_blacklisted(chat_id: int, user_id: int) -> bool:
    return redis_client.sismember(get_blacklist_key(chat_id), str(user_id))

def add_to_blacklist(chat_id: int, user_id: int):
    redis_client.sadd(get_blacklist_key(chat_id), str(user_id))

def remove_from_blacklist(chat_id: int, user_id: int):
    redis_client.srem(get_blacklist_key(chat_id), str(user_id))

def mute_user(chat_id: int, user_id: int, minutes: int = 60):
    unmute_time = datetime.now() + timedelta(minutes=minutes)
    redis_client.hset(get_muted_key(chat_id), str(user_id), unmute_time.isoformat())

def unmute_user(chat_id: int, user_id: int):
    redis_client.hdel(get_muted_key(chat_id), str(user_id))

def is_muted(chat_id: int, user_id: int) -> bool:
    time_str = redis_client.hget(get_muted_key(chat_id), str(user_id))
    if not time_str:
        return False
    unmute_time = datetime.fromisoformat(time_str)
    if datetime.now() > unmute_time:
        redis_client.hdel(get_muted_key(chat_id), str(user_id))
        return False
    return True

def get_user_display(user: ChatMember) -> Tuple[str, str]:
    name = user.user.first_name or ""
    if user.user.last_name:
        name += f" {user.user.last_name}"
    if user.user.username:
        link = f"https://t.me/{user.user.username}"
    else:
        link = f"tg://openmessage?user_id={user.user.id}"
    if not name.strip():
        name = str(user.user.id)
    return name, link

# --- Обработчики ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.chat:
        return

    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    text = update.message.text or update.message.caption or ""

    # Проверка на мут
    if is_muted(chat_id, user_id):
        await update.message.delete()
        return

    # Сохранение данных для развлечения
    if text:
        save_phrase(chat_id, text)
        # Проверка на триггер из файла
        lower_text = text.lower().strip()
        if lower_text in RESPONSE_DICT and update.message.reply_to_message:
            reply_to = update.message.reply_to_message
            if reply_to.from_user.id == context.bot.id:
                response = random.choice(RESPONSE_DICT[lower_text])
                await update.message.reply_text(response)
                return

    # Сохранение медиа
    if update.message.photo:
        save_media_id(chat_id, "photo", update.message.photo[-1].file_id)
    elif update.message.video:
        save_media_id(chat_id, "video", update.message.video.file_id)
    elif update.message.voice:
        save_media_id(chat_id, "voice", update.message.voice.file_id)
    elif update.message.video_note:
        save_media_id(chat_id, "video_note", update.message.video_note.file_id)

    # Случайный ответ
    if should_bot_respond(chat_id):
        choice = random.random()
        if choice < 0.4:
            phrase = get_random_phrase(chat_id)
            if phrase:
                await update.message.reply_text(phrase)
        elif choice < 0.7:
            media_type = random.choice(["photo", "video", "voice"])
            file_id = get_random_media(chat_id, media_type)
            if file_id:
                if media_type == "photo":
                    await context.bot.send_photo(chat_id, file_id)
                elif media_type == "video":
                    await context.bot.send_video(chat_id, file_id)
                elif media_type == "voice":
                    await context.bot.send_voice(chat_id, file_id)
        else:
            await update.message.reply_text(random.choice(["Да.", "Нет.", "Возможно.", "Хмм..."]))

    # Ответ на сообщение бота
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        if random.random() < 0.5:
            phrase = get_random_phrase(chat_id)
            if phrase:
                await update.message.reply_text(phrase)

async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    staff_data = get_staff_list(chat_id)
    
    # Получаем данные о владельце
    try:
        owner_list = await update.effective_chat.get_administrators()
        owner = None
        for admin in owner_list:
            if admin.status == 'creator':
                owner = admin
                break
    except Exception:
        owner = None

    # Группируем по ролям
    roles = {5: [], 4: [], 3: [], 2: [], 1: []}
    for user_id, role in staff_data.items():
        if role in roles:
            try:
                member = await update.effective_chat.get_member(user_id)
                name, link = get_user_display(member)
                roles[role].append(f"[{name}]({link})")
            except Exception:
                continue

    # Формируем сообщение
    lines = ["*⭐⭐⭐⭐⭐ СОЗДАТЕЛИ*"]
    if owner:
        name, link = get_user_display(owner)
        lines.append(f"🥎 [{name}]({link})")
    
    for role_val, title in [(4, "⭐⭐⭐⭐ СТАРШИЕ АДМИНЫ"), (3, "⭐️⭐️⭐️ МЛАДШИЕ АДМИНЫ"), 
                            (2, "⭐⭐ МОДЕРАТОРЫ"), (1, "⭐ МЛАДШИЕ МОДЕРАТОРЫ")]:
        if roles[role_val]:
            lines.append(f"*{title}*")
            for user_str in roles[role_val]:
                lines.append(f"🏐 {user_str}")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    args = context.args
    
    # Проверка прав
    caller_role = get_staff_role(chat_id, user_id)
    if caller_role < 3:
        await update.message.reply_text("❌ Недостаточно прав.")
        return
    
    # Определяем цель
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    elif args and len(args) > 1:
        # Обработка аргументов вида "повысить 3 @username"
        try:
            if args[1].startswith('@'):
                username = args[1][1:]
                # Здесь нужно найти пользователя по username
                # Упрощенная версия - требуем ответ на сообщение
                await update.message.reply_text("❌ Укажите пользователя ответом на сообщение.")
                return
        except:
            pass
    
    if not target_user:
        await update.message.reply_text("❌ Укажите пользователя ответом на сообщение.")
        return
    
    target_id = target_user.id
    
    # Определяем новую роль
    new_role = 1
    if args and args[0].isdigit():
        role_arg = int(args[0])
        if 1 <= role_arg <= 5:
            new_role = role_arg
    
    # Проверка возможности повышения
    max_promotion = {3: 1, 4: 3, 5: 5}
    max_allowed = max_promotion.get(caller_role, 0)
    if new_role > max_allowed:
        await update.message.reply_text(f"❌ Вы можете повышать максимум до {max_allowed} уровня.")
        return
    
    set_staff_role(chat_id, target_id, new_role)
    await update.message.reply_text(f"✅ Пользователь {target_user.first_name} повышен до уровня {new_role}.")

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if get_staff_role(chat_id, user_id) < 2:
        return
    
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    
    if not target_user:
        await update.message.reply_text("❌ Укажите пользователя ответом на сообщение.")
        return
    
    try:
        await update.effective_chat.ban_member(target_user.id)
        await update.effective_chat.unban_member(target_user.id)
        name, link = get_user_display(await update.effective_chat.get_member(target_user.id))
        await update.message.reply_text(f"❎ [{name}]({link}) исключён.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка Telegram.")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if get_staff_role(chat_id, user_id) < 3:
        return
    
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    
    if not target_user:
        await update.message.reply_text("❌ Укажите пользователя ответом на сообщение.")
        return
    
    try:
        await update.effective_chat.ban_member(target_user.id)
        add_to_blacklist(chat_id, target_user.id)
        name, link = get_user_display(await update.effective_chat.get_member(target_user.id))
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"Вернуть @{target_user.id}", callback_data=f"unban_{chat_id}_{target_user.id}")
        ]])
        await update.message.reply_text(
            f"⛔ [{name}]({link}) забанен в этом чате\n"
            f"ℹ️ Если хотите его вернуть в чат напишите\n— вернуть @{target_user.id}",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка Telegram.")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if get_staff_role(chat_id, user_id) < 2:
        return
    
    target_user = None
    minutes = 60
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        if context.args and context.args[0].isdigit():
            minutes = int(context.args[0])
    
    if not target_user:
        await update.message.reply_text("❌ Укажите пользователя ответом на сообщение.")
        return
    
    try:
        mute_until = datetime.now() + timedelta(minutes=minutes)
        await update.effective_chat.restrict_member(
            target_user.id,
            permissions=ChatPermissions(),
            until_date=mute_until
        )
        mute_user(chat_id, target_user.id, minutes)
        name, link = get_user_display(await update.effective_chat.get_member(target_user.id))
        await update.message.reply_text(f"🔇 [{name}]({link}) заглушен на {minutes} минут.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка Telegram.")

async def unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("unban_"):
        _, chat_id_str, user_id_str = data.split("_")
        chat_id = int(chat_id_str)
        user_id = int(user_id_str)
        
        # Проверка прав
        caller_role = get_staff_role(chat_id, query.from_user.id)
        if caller_role < 3:
            await query.edit_message_text("❌ Недостаточно прав.")
            return
        
        try:
            await context.bot.unban_chat_member(chat_id, user_id)
            remove_from_blacklist(chat_id, user_id)
            await query.edit_message_text(f"✅ Пользователь @{user_id} успешно разбанен, можете добавлять его в чат.")
        except Exception as e:
            await query.edit_message_text("⚠️ Ошибка Telegram.")

async def new_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if is_blacklisted(update.effective_chat.id, member.id):
            try:
                await update.effective_chat.ban_member(member.id)
            except Exception:
                pass

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if get_staff_role(chat_id, user_id) < 3:
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("❌ Укажите ID пользователя: /вернуть 123456789")
        return
    
    target_id = int(args[0])
    
    try:
        await update.effective_chat.unban_member(target_id)
        remove_from_blacklist(chat_id, target_id)
        await update.message.reply_text(f"✅ Пользователь @{target_id} успешно разбанен, можете добавлять его в чат.")
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка Telegram.")

# --- Главная функция ---
def main():
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.VOICE | filters.VIDEO_NOTE, message_handler))
    
    # Команды админки
    staff_filters = filters.Regex(r"^(?i)(!|\.|/)?(staff|стафф|админы|кто админ|адм)$")
    application.add_handler(MessageHandler(staff_filters, staff_command))
    
    promote_filters = filters.Regex(r"^(?i)(!|\.|/)?(повысить|promote)$")
    application.add_handler(MessageHandler(promote_filters, promote_command))
    
    kick_filters = filters.Regex(r"^(?i)(!|\.|/)?(кик|kick)$")
    application.add_handler(MessageHandler(kick_filters, kick_command))
    
    ban_filters = filters.Regex(r"^(?i)(!|\.|/)?(бан|ban)$")
    application.add_handler(MessageHandler(ban_filters, ban_command))
    
    mute_filters = filters.Regex(r"^(?i)(!|\.|/)?(мут|mute)$")
    application.add_handler(MessageHandler(mute_filters, mute_command))
    
    unban_filters = filters.Regex(r"^(?i)(!|\.|/)?(вернуть|unban)$")
    application.add_handler(MessageHandler(unban_filters, unban_command))
    
    # Колбэки
    application.add_handler(CallbackQueryHandler(unban_callback, pattern=r"^unban_"))
    
    # Новые участники
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_member_handler))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
