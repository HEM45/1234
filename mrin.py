import html
import json
import logging
import traceback
import requests
import re
from typing import Optional
from telegram import Update, BotCommand, BotCommandScopeChat
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    PicklePersistence, filters
)
from telegram.error import Forbidden, Conflict
from config import BOT_TOKEN, DEVELOPER_ID, IS_BOT_PRIVATE

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def extract_tweet_ids(text: str) -> Optional[list[str]]:
    """Extract tweet IDs from message text."""
    unshortened_links = ''
    for link in re.findall(r"t\.co\/[a-zA-Z0-9]+", text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except Exception:
            pass
    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    tweet_ids = list(dict.fromkeys(tweet_ids))
    return tweet_ids or None

def scrape_tweet(tweet_id: int) -> dict:
    """Get tweet info (media + caption) from vxtwitter API."""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    return r.json()

async def reply_video_link(update: Update, video_url: str):
    await update.effective_message.reply_text(
        f"Direct video link:\n{video_url}"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'stats' not in context.bot_data:
        context.bot_data['stats'] = {'messages_handled': 0, 'media_downloaded': 0}
    context.bot_data['stats']['messages_handled'] += 1

    text = update.effective_message.text
    tweet_ids = extract_tweet_ids(text)
    if tweet_ids:
        for tweet_id in tweet_ids:
            try:
                tweet_info = scrape_tweet(tweet_id)
                media = tweet_info.get('media_extended', [])
                # Only process videos
                videos = [m for m in media if m.get("type") == "video"]
                if videos:
                    for video in videos:
                        await reply_video_link(update, video['url'])
                        context.bot_data['stats']['media_downloaded'] += 1
                else:
                    await update.effective_message.reply_text(
                        f'Tweet {tweet_id} has no video'
                    )
            except Exception:
                await update.effective_message.reply_text(
                    f'Error handling tweet {tweet_id}'
                )
    else:
        await update.effective_message.reply_text(
            'No supported tweet link found'
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_markdown_v2(
        fr'Hi {user.mention_markdown_v2()}\!' +
        '\n\nSend a tweet link here and I will reply with the direct video link for you.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        'Send a tweet link here and I will reply with the direct video link for you.'
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'stats' not in context.bot_data:
        context.bot_data['stats'] = {'messages_handled': 0, 'media_downloaded': 0}
    await update.effective_message.reply_markdown_v2(
        f'*Bot stats:*\nMessages handled: *{context.bot_data["stats"].get("messages_handled")}*'
        f'\nMedia links sent: *{context.bot_data["stats"].get("media_downloaded")}*'
    )

async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data['stats'] = {'messages_handled': 0, 'media_downloaded': 0}
    await update.effective_message.reply_text("Bot stats have been reset")

async def deny_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f'Access denied. Your id ({update.effective_user.id}) is not whitelisted'
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, Forbidden):
        return
    if isinstance(context.error, Conflict):
        logger.error("Telegram requests conflict")
        return
    if update is None:
        return
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f'#error_report\n'
        f'An exception was raised in runtime\n'
        f'<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n'
        f'<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n'
        f'<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n'
        f'<pre>{html.escape(tb_string)}</pre>'
    )
    from io import StringIO
    string_out = StringIO(message)
    await context.bot.send_document(
        chat_id=DEVELOPER_ID,
        document=string_out,
        filename='error_report.txt',
        caption='#error_report\nAn exception was raised during runtime\n'
    )
    if update and hasattr(update, "effective_message"):
        error_class_name = ".".join([context.error.__class__.__module__, context.error.__class__.__qualname__])
        await update.effective_message.reply_text(
            f'Error\n{error_class_name}: {str(context.error)}'
        )

async def set_commands(app):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Help message"),
        BotCommand("stats", "Get bot statistics"),
        BotCommand("resetstats", "Reset bot statistics"),
    ]
    await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(DEVELOPER_ID))

def main() -> None:
    from os import makedirs
    makedirs('data', exist_ok=True)
    persistence = PicklePersistence(filepath='data/persistence')
    application = ApplicationBuilder().token(BOT_TOKEN).persistence(persistence).build()

    admin_filter = filters.Chat(DEVELOPER_ID)
    text_filter = filters.TEXT & (~filters.COMMAND)

    if IS_BOT_PRIVATE:
        application.add_handler(CommandHandler("stats", stats_command, admin_filter))
        application.add_handler(CommandHandler("resetstats", reset_stats_command, admin_filter))
        application.add_handler(MessageHandler(~admin_filter, deny_access))
        application.add_handler(CommandHandler("start", start, admin_filter))
        application.add_handler(CommandHandler("help", help_command, admin_filter))
        application.add_handler(MessageHandler(text_filter & admin_filter, handle_message))
        application.post_init = set_commands
    else:
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(text_filter, handle_message))
        # Set commands for developer only
        async def set_dev_commands(app):
            public_commands = []
            dev_commands = public_commands + [
                BotCommand("stats", "Get bot statistics"),
                BotCommand("resetstats", "Reset bot statistics"),
            ]
            await app.bot.set_my_commands(public_commands)
            await app.bot.set_my_commands(dev_commands, scope=BotCommandScopeChat(DEVELOPER_ID))
        application.post_init = set_dev_commands

    application.add_error_handler(error_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
