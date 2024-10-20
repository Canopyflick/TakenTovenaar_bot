from telegram.ext import CommandHandler, MessageHandler, ConversationHandler, filters
from TelegramBot_Takentovenaar import get_database_connection



# Define a state for the conversation
CONFIRM_WIPE = range(1)

async def wipe_command(update, context):
    # Ask for confirmation
    await update.message.reply_text(
        "Weet je zeker dat je al je voortgang wilt laten wegtoveren? 🧙‍♂️\n\nTyp 'JA' om te bevestigen, of iets anders om te annuleren."
    )
    # Set the conversation state
    return CONFIRM_WIPE


async def confirm_wipe(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_response = update.message.text.strip()
    
    if user_response == 'JA':
        conn = get_database_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM engagements WHERE engager_id = %s AND chat_id = %s', (user_id, chat_id))
            cursor.execute('DELETE FROM engagements WHERE engaged_id = %s AND chat_id = %s', (user_id, chat_id))
            conn.commit()
            cursor.execute('DELETE FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
            conn.commit()
            await update.message.reply_text("Je gegevens zijn gewist 🕳️")
        except Exception as e:
            print(f"Error wiping database after confirm_wipe: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close() 
        return ConversationHandler.END


        # desired_columns = await desir
        # await add_missing_columns(update, context)
    else:
        await update.message.reply_text("Wipe geannuleerd 🚷")
        return ConversationHandler.END
        
def create_wipe_handler():
    return ConversationHandler(
            entry_points=[CommandHandler('wipe', wipe_command)],
            states={
                CONFIRM_WIPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_wipe)],
            },
            fallbacks=[],
            conversation_timeout=30
        )

