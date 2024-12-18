﻿import os
import psycopg2
from telegram import Bot, ChatMember
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, CallbackQueryHandler, PollHandler, ExtBot, CallbackContext
from openai import OpenAI
import asyncio, telegram
from datetime import datetime, time, timedelta, timezone
from typing import Union
from zoneinfo import ZoneInfo

# Define the Berlin timezone
BERLIN_TZ = ZoneInfo("Europe/Berlin")

print(f"python-telegram-bot version: {telegram.__version__}\n\n")

# Global bot instance
global_bot: ExtBot = None

def initialize_bot(token: str) -> None:
    bot = Bot(token)
    """Initialize the global bot instance with the given token."""  # I don't remember why I wanted this but just leaving it in
    global global_bot
    application = ApplicationBuilder().token(token).build()
    global_bot = application.bot
    # Ensure the bot is initialized
    if not global_bot:
        raise ValueError("Failed to initialize bot")

local_flag = False

# Only load dotenv if running locally (not on Heroku)
if not os.getenv('HEROKU_ENV'):  # Check if HEROKU_ENV is not set, meaning it's local
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        local_flag = True
    except ImportError:
        pass  # In case dotenv isn't installed, ignore this when running locally


# Get OpenAI API key from environment variable (works in both local and Heroku)
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    raise ValueError("OPENAI_API_KEY not found! Ensure it's set in the environment.")
# Initialize the OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))


# Connect to the PostgreSQL database
def get_database_connection():
    # Use DATABASE_URL if available (Heroku), otherwise fallback to LOCAL_DB_URL
    DATABASE_URL = os.getenv('DATABASE_URL', os.getenv('LOCAL_DB_URL'))

    if not DATABASE_URL:
        raise ValueError("Database URL not found! Ensure 'DATABASE_URL' or 'LOCAL_DB_URL' is set in the environment.")

    # Connect to the PostgreSQL database
    if os.getenv('HEROKU_ENV'):  # Running on Heroku
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    else:  # Running locally
        conn = psycopg2.connect(DATABASE_URL)  # For local development, no SSL required

    return conn


conn = get_database_connection()
cursor = conn.cursor()
# Create the tables if they don't exist (for new users/tables) and check if there's newly added columns (for live dev updates)
try:
    # Function to get existing columns
    def get_existing_columns(cursor, table_name):
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s AND table_schema = 'public';
        """, (table_name,))
        conn.commit()
        return [info[0] for info in cursor.fetchall()]

    # Function to add missing columns
    def add_missing_columns(cursor, table_name, desired_columns):
        existing_columns = get_existing_columns(cursor, table_name)
        for column_name, column_definition in desired_columns.items():
            if column_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition};")
                    print(f"Added column {column_name} to {table_name}")
                except Exception as e:
                    print(f"Error adding column {column_name}: {e}")
                    conn.rollback()  # Roll back the specific column addition if it fails

    # Desired columns with definitions
    desired_columns_users = {
        'user_id': 'BIGINT',
        'chat_id': 'BIGINT',
        'first_name': "TEXT",
        'total_goals': 'INTEGER DEFAULT 0',
        'completed_goals': 'INTEGER DEFAULT 0',
        'weekly_goals_left': 'INTEGER DEFAULT 3', 
        'score': 'INTEGER DEFAULT 0',
        'today_goal_status': "TEXT DEFAULT 'not set'",
        'set_time': 'TIMESTAMP WITH TIME ZONE',  
        'today_goal_text': "TEXT DEFAULT ''",
        'live_challenge': "TEXT DEFAULT '{}'",  # not used (anymore)?
        'inventory': "JSONB DEFAULT '{\"boosts\": 1, \"challenges\": 1, \"links\": 1}'",
        'reminder_scheduled':" BOOLEAN DEFAULT False"
    }


    # Create the tables if they don't exist
    #1 users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            chat_id BIGINT,
            first_name TEXT,       
            total_goals INTEGER DEFAULT 0,
            completed_goals INTEGER DEFAULT 0,
            weekly_goals_left INTEGER DEFAULT 4 CHECK (weekly_goals_left >= 0),       
            score INTEGER DEFAULT 0,
            today_goal_status TEXT DEFAULT 'not set', -- either 'set', 'not set', or 'Done at TIMESTAMP WITH TIME ZONE'
            set_time TIMESTAMP WITH TIME ZONE,       
            today_goal_text TEXT DEFAULT '',
            live_challenge TEXT DEFAULT '{}',
            inventory JSONB DEFAULT '{"boosts": 1, "challenges": 1, "links": 1}',
            reminder_scheduled BOOLEAN DEFAULT False,
            PRIMARY KEY (user_id, chat_id)
        )
    ''')
    conn.commit()
    
    #2 bot table
    # Set 3:01 am as default last_reset_time
    now = datetime.now(tz=BERLIN_TZ)
    default_last_reset_time = now.replace(hour=3, minute=1, second=1, microsecond=1)

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bot_status (
        chat_id BIGINT PRIMARY KEY,
        last_reset_time TIMESTAMP WITH TIME ZONE
    )
    ''')
    conn.commit()

    # 3 engagements table
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS engagements (
                id BIGSERIAL PRIMARY KEY,
                engager_id BIGINT NOT NULL,
                engaged_id BIGINT,
                chat_id BIGINT NOT NULL,
                special_type TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'live', -- either 'pending', 'live', 'archived_done' or 'archived_unresolved'
                FOREIGN KEY (engager_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE,
                FOREIGN KEY (engaged_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE
            );
        ''')
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS unique_active_engagements
            ON engagements(engager_id, engaged_id, special_type, chat_id)
            WHERE status IN ('live', 'pending');
        ''')
        conn.commit()
    except Exception as e:
        print(f"Error creating engagements table or index: {e}")
        conn.rollback()


    # Add missing columns
    add_missing_columns(cursor, 'users', desired_columns_users)
    conn.commit()

    #4 goal history table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS goal_history (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT,
        chat_id BIGINT,
        goal_text TEXT NOT NULL,
        completion_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        goal_type TEXT NOT NULL DEFAULT 'personal', -- 'personal' or 'challenges'
        challenge_from BIGINT,  -- NULL for personal goals, user_id of challenger for challenges
        FOREIGN KEY (user_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE,
        FOREIGN KEY (challenge_from, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE
    );
    ''')
    conn.commit()
    
    #5 polls table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS polls (
        chat_id BIGINT NOT NULL,
        poll_id VARCHAR(255) NOT NULL,
        message_id BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        processed BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (poll_id)
    );
    ''')
    conn.commit()


except Exception as e:
    print(f"Error updating database schema: {e}")
    conn.rollback()
finally:
    cursor.close()
    conn.close()    


conn = get_database_connection()
cursor = conn.cursor()
try:
    cursor.execute('SELECT 1')
    print("Database connection successful")
except Exception as e:
    print(f"Database connection error: {e}")
    if conn:
        conn.rollback()  # Roll back the current transaction, if any

# Storing all column names of the users table in columns variable (eg: 'today_goal_text') 
# Fetch column names safely
try:    
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'users';
    """)
    columns_result = cursor.fetchall()

    if columns_result:
        columns = [column[0] for column in columns_result]  # Adjust if only one column per result
        print("User columns result:\n", columns_result)  # Debugging
    else:
        print("No columns found for the users table.")
        columns = []
except Exception as e:
    print(f"Error fetching column date: {e}")
    conn.rollback()
finally:
    cursor.close()
    conn.close() 
    

async def get_first_name(context_or_bot: Union[Bot, ExtBot, CallbackContext], user_id: int) -> str:
    global global_bot
    try:
        # Check if context_or_bot is a CallbackContext
        if isinstance(context_or_bot, CallbackContext):
            bot = context_or_bot.bot
        # If it's a Bot or ExtBot instance, use it directly
        elif isinstance(context_or_bot, (Bot, ExtBot)):
            bot = context_or_bot
        else:
            # Fallback to global bot if available
            if global_bot is None:
                raise ValueError("No bot instance available")
            bot = global_bot

        # Now, 'bot' is guaranteed to be a Bot or ExtBot instance
        chat_member = await bot.get_chat_member(user_id, user_id)
        return chat_member.user.first_name

    except Exception as e:
        print(f"Error fetching user details for user_id {user_id}: {e}")
        return "Lodewijk 🚨🐛"
    


# Security check: am I in the chat where the bot is used?
async def is_ben_in_chat(update, context):
    USER_ID = 1875436366
    chat_id = update.effective_chat.id
    try:
        # Get information about your status in the chat
        member = await context.bot.get_chat_member(chat_id, USER_ID)
        # Check if you're a member, administrator, or have any active role in the chat
        if member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            return True
        return False
    except Exception as e:
        print(f"Error checking chat member: {e}")
        # Send a message to notify about the issue
        await context.bot.send_message(chat_id, f"🚫 Kon even geen Ben-check doen. Probeer opnieuw 🧙‍♂️\n({e})")
        # Raise a custom exception to stop further execution
        raise RuntimeError("Ben check could not be completed due to an error.")

# Private message to Ben (test once then delete)
async def notify_ben(update,context):
        USER_ID = 1875436366
        first_name = update.effective_user.first_name
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        message = update.message.text
        notification_message = f"You've got mail ✉️🧙‍♂️\n\nUser: {first_name}, {user_id}\nChat: {chat_id}\nMessage:\n{message}"
        print(f"! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! \n\n\n\nUnauthorized Access Detected\n\n\n\n! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !\nUser: {first_name}, {user_id}\nChat: {chat_id}\nMessage: {message}")
        await context.bot.send_message(chat_id=USER_ID, text=notification_message)
        
async def print_edit(update, context):
    print("Someone edited a message")
    

def reset_reminders_on_startup():
    conn = get_database_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users
        SET reminder_scheduled = False
    ''')
    conn.commit()
    conn.close()
    cursor.close()
    print(f"🕳️ Reset all scheduled reminders flags")


def register_handlers(application):
    from handlers.challenge_handler import challenge_command, handle_challenge_response
    application.add_handler(CallbackQueryHandler(handle_challenge_response, pattern=r"^(retract|accept|reject)_\d+$"))

    from handlers.reminders import handle_goal_completion_reminder_response
    application.add_handler(CallbackQueryHandler(handle_goal_completion_reminder_response, pattern=r"^(klaar|nee)_\d+_.+$"))

    from handlers.wipe_handler import create_wipe_handler
    wipe_conv_handler = create_wipe_handler()
    application.add_handler(wipe_conv_handler)

    from handlers.commands import start_command, help_command, stats_command, reset_command, filosofie_command, inventory_command, acties_command, gift_command, steal_command, revert_goal_completion_command, ranking_command, boost_command, link_command, details_command
    # Bind the commands to their respective functions
    # The ones that show up in /help:
    application.add_handler(CommandHandler(["start", "begroeting", "begin"], start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler(["challenge", "uitdagen", "komdanjonge", "waarheiddoenofdurven"], challenge_command))
    # wipe is in handlers/wipe_handler.py
    application.add_handler(CommandHandler("filosofie", filosofie_command))
    application.add_handler(CommandHandler(["inventaris", "inventory"], inventory_command))
    application.add_handler(CommandHandler(["moves", "engagements", "specials", "engagement", "engoggos", "acties", "actie"], acties_command))
    application.add_handler(CommandHandler(["details", "movesdetails"], details_command))
    # the admin commands
    application.add_handler(CommandHandler(["gift", "give", "cadeautje", "foutje", "geef", "kadootje", "gefeliciteerd", "goedzo"], gift_command))
    application.add_handler(CommandHandler(["steal", "steel", "sorry", "oeps"], steal_command))
    application.add_handler(CommandHandler(["revert", "neee", "oftochniet"], revert_goal_completion_command))
    application.add_handler(CommandHandler(["ranking", "tussenstand", "eindstand", "puntenoverzicht"], ranking_command))

    # Simple engagements: boosts
    application.add_handler(CommandHandler(["boost", 'boosten', "boosting"], boost_command))
    # Complexer engagements
    application.add_handler(CommandHandler(["link", "links", "linken"], link_command))
        
    # (Weekly) goals poll
    from utils import analyze_message
    from handlers.weekly_poll import receive_poll, poll_command
    application.add_handler(PollHandler(receive_poll))
    application.add_handler(CommandHandler("poll", poll_command))
        
    from handlers.dispute_handler import fittie_command
    application.add_handler(CommandHandler(["fittie", "oneens", "wachteensff", "nietzosnel"], fittie_command))
        
    # Register the CallbackQueryHandler to handle the trashbin click
    from handlers.commands import handle_trashbin_click
    application.add_handler(CallbackQueryHandler(handle_trashbin_click, pattern="delete_stats"))

    # Bind the message analysis to any non-command text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.UpdateType.MESSAGE, analyze_message))
    # Handler for edited messages
    application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND, print_edit))


# Setup function 
async def setup(application):
    try:
        bot_name = await application.bot.get_me()
        print(f"Initialized bot: {bot_name.username}\n")
    except Exception as e:
        print(f"Bot token issue: {e}")
    try:
        reset_reminders_on_startup()
        
        from utils import scheduled_daily_reset
        # Schedule the daily reset job 
        job_queue = application.job_queue
        reset_time = time(hour=3, minute=0, second=0, tzinfo=BERLIN_TZ)  
        job_queue.run_daily(scheduled_daily_reset, time=reset_time)
        print(f"\nDaily reset job queue set up successfully at {reset_time}")
        
        # Schedule the daily reminders jobs 
        from handlers.reminders import prepare_daily_reminders
        reminder_time_early = time(hour=18, minute=18, second=00, tzinfo=BERLIN_TZ)  
        reminder_time_late = time(hour=21, minute=21, second=00, tzinfo=BERLIN_TZ)     
        job_queue.run_daily(prepare_daily_reminders, time=reminder_time_early)
        job_queue.run_daily(prepare_daily_reminders, time=reminder_time_late)
        print(f"\nDaily reminders job queues set up successfully at {reminder_time_early} & {reminder_time_late}")

        from handlers.weekly_poll import scheduled_weekly_poll
        # Schedule the weekly poll job 
        poll_time = time(hour=7, minute=7, tzinfo=BERLIN_TZ)
        job_queue.run_daily(
            scheduled_weekly_poll, 
            time=poll_time, 
            days=(5,)  #6 = Sunday??
        )
        print(f"\nWeekly goals poll job queue set up successfully at {poll_time} every Saturday\n")

        from utils import get_last_reset_time
        now = datetime.now(tz=BERLIN_TZ)

        # Define scheduled reset time 
        reset_time_today = now.replace(hour=3, minute=0, second=0, microsecond=0, tzinfo=BERLIN_TZ)

        # Retrieve all chat IDs from the database
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT chat_id FROM users")
        chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]
        # Ensure each chat_id is initialized in `bot_status`
        for chat_id in chat_ids:
            cursor.execute(
                """
                INSERT INTO bot_status (chat_id, last_reset_time)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO NOTHING
                """,
                (chat_id, reset_time_today)  # Initialize as if it was just reset today, to avoid triggering a catch-up reset
            )
        conn.commit()
        cursor.close()
        conn.close()

        # (set correct timezone, then:) Determine if any chats need a catch-up reset
        for chat_id in chat_ids:
            last_reset = get_last_reset_time(chat_id)
            print(f"CHAT: {chat_id}")
            if last_reset is not None and last_reset.tzinfo is None:
                # Assign UTC timezone if tzinfo is missing (assume stored in UTC)
                last_reset = last_reset.replace(tzinfo=ZoneInfo("UTC"))
                # Convert to Berlin timezone
                last_reset = last_reset.astimezone(BERLIN_TZ)

            print(f"Last reset time : {last_reset}")
            print(f"Current time    : {now}")

            # Determine if a catch-up reset is needed for each chat
            if now >= reset_time_today:
                # If now is after 3:00 AM today, check if last reset was before 3:01 AM today
                if last_reset is None or last_reset < reset_time_today:
                    print(f"^ Performing catch-up reset for chat {chat_id} ^\n")
                    await scheduled_daily_reset(application, chat_id)
                else:
                    print(f"^ No catch-up reset needed for chat {chat_id} ^\n")
            else:
                # If now is before 3:00 AM today, check if last reset was before 3:00 AM yesterday
                reset_time_yesterday = reset_time_today - timedelta(days=1)
                if last_reset is None or last_reset < reset_time_yesterday:
                    print(f"^ Performing catch-up reset for chat {chat_id} ^")
                    await scheduled_daily_reset(application, chat_id)

    except Exception as e:
        print(f"Error setting up job queue: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print(traceback.format_exc())        

def main():
    print("\nEntering main function\n")
    try:
        # Check if running locally or on Heroku
        if local_flag == True:
            print("Using local Database")
            # Running locally, use local bot token
            token = os.getenv('LOCAL_TELEGRAM_BOT_TOKEN').strip()  # Strip any extra spaces or newlines
        else:
            # Running on Heroku, use Heroku bot token
            token = os.getenv('TELEGRAM_BOT_TOKEN')
            print("Using Heroku Database\n")

        if token is None:
            raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")
        
        token = token.strip()
        initialize_bot(token)
        
        # Create the bot application with ApplicationBuilder
        application = ApplicationBuilder().token(token).build()
        asyncio.get_event_loop().run_until_complete(setup(application))

        # Set up handlers
        register_handlers(application)

        # Start the bot
        application.run_polling()
        print("********************* END OF MAIN *********************")
    except Exception as e:
        print(f"Error in main function: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print(traceback.format_exc())
if __name__ == '__main__':
    main()
