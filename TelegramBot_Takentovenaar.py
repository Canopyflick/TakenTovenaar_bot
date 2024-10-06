import os
import random
from tempfile import TemporaryFile
from telegram import User, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes
from openai import OpenAI
import datetime
import asyncio
import re
import json
import psycopg2
import pytz
from datetime import datetime

berlin_tz = pytz.timezone('Europe/Berlin')
berlin_time = datetime.now(berlin_tz)


# Initialize the OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# For local development
LOCAL_DB_URL = "postgresql://username:password@localhost/your_database_name"

# Use environment variable for Heroku, fallback to local for development
DATABASE_URL = os.getenv('DATABASE_URL', LOCAL_DB_URL)

# Connect to the PostgreSQL database
# Use SSL only on Heroku
if 'DATABASE_URL' in os.environ:
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
else:
    conn = psycopg2.connect(DATABASE_URL)  # For local development, no SSL required

cursor = conn.cursor()


# Create the users table if it doesn't exists (for new users) and check if there's newly added columns (for existing users)
try:
    # Function to get existing columns
    def get_existing_columns(cursor, table_name):
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s AND table_schema = 'public';
        """, (table_name,))
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
    desired_columns = {
        'user_id': 'INTEGER',
        'chat_id': 'INTEGER',
        'total_goals': 'INTEGER DEFAULT 0',
        'completed_goals': 'INTEGER DEFAULT 0',
        'score': 'INTEGER DEFAULT 0',
        'today_goal_status': "TEXT DEFAULT 'not set'",
        'today_goal_text': "TEXT DEFAULT ''",
        'pending_challenge': 'TEXT DEFAULT \'{}\'',  # Escaped JSON string
        'first_name': 'TEXT'
    }

    # Create the table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            chat_id BIGINT,
            first_name TEXT,
            total_goals INTEGER DEFAULT 0,
            completed_goals INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            today_goal_status TEXT DEFAULT 'not set',
            today_goal_text TEXT DEFAULT '',
            pending_challenge TEXT DEFAULT '{}',
            PRIMARY KEY (user_id, chat_id)
        )
    ''')
    conn.commit()
    # Alter user_id and chat_id to BIGINT if needed
    try:
        cursor.execute("""
            ALTER TABLE users
            ALTER COLUMN user_id TYPE BIGINT,
            ALTER COLUMN chat_id TYPE BIGINT;
        """)
        conn.commit()
        print("Altered user_id and chat_id to BIGINT")
    except Exception as e:
        print(f"Error altering columns user_id and chat_id to BIGINT: {e}")
        conn.rollback()

    # Add missing columns
    add_missing_columns(cursor, 'users', desired_columns)
    conn.commit()


except Exception as e:
    print(f"Error updating database schema: {e}")
    conn.rollback()


try:
    cursor.execute('SELECT 1')
    print("Database connection successful")
except Exception as e:
    print(f"Database connection error: {e}")
    conn.rollback

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
        print("Columns result:", columns_result)  # Debugging
    else:
        print("No columns found for the users table.")
        columns = []
except Exception as e:
    print(f"Error fetching column date: {e}")
    conn.rollback


# Helper functions to reduce bloat/increase modularity
def fetch_goal_text(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    try:
        # Check if the user has a goal for today
        if has_goal_today(user_id, chat_id):
            cursor.execute('SELECT today_goal_text FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
            result = cursor.fetchone()
            
            if result and result[0]:
                print(f"Goal text found: {result[0]}")
                return result[0]  # Return the goal text if found
            else:
                print("No goal text found for today.")
                return ''  # Return empty string if no goal text is found
        else:
            print("The user has no goal for today.")
            return ''  # Return empty string if the user has no goal for today

    except Exception as e:
        print(f"Error fetching goal data: {e}")
        return ''  # Return empty string if an error occurs
    

        


def prepare_openai_messages(update, user_message, message_type, goal_text=None, bot_last_response=None):
    # Define system messages based on the message_type
    if message_type == 'classification':
        print("system prompt: classification message")
        system_message = (
            "Jij classificeert een berichtje van een gebruiker in een Telegramgroep "
            "in een van de volgende drie groepen: Doelstelling, Klaar of Overig. "
            "Elk bericht waaruit blijkt dat de gebruiker van plan is om iets (specifieks) te gaan doen vandaag, "
            "is een 'Doelstelling'. Als de gebruiker rapporteert dat het ingestelde doel helemaal gelukt is, "
            "dan is dat 'Klaar'. Alle andere gevallen zijn 'Overig'. Antwoord alleen met 'Doelstelling', 'Klaar' of 'Overig'."
        )
    elif message_type == 'other':
        print("system prompt: other message")
        system_message = (
            "Jij bent @TakenTovenaar_bot, de enige bot in een accountability-Telegramgroep van vrienden. "
            "Gedraag je cheeky en mysterieus, maar streef bovenal naar waarheid. "
            "Als de user een metavraag of -verzoek heeft over bijvoorbeeld een doel stellen in de appgroep, "
            "antwoord dan alleen dat ze het command /help kunnen gebruiken. "
            "Er zijn meer commando's, maar die ken jij allemaal niet. "
            "Je hebt nu alleen toegang tot dit bericht, niet tot volgende of vorige berichtjes. "
            "Een back-and-forth met de user is dus niet mogelijk."
        )
    elif message_type == 'sleepy':
        print("system prompt: sleepy message")
        system_message = ("Geef antwoord alsof je slaapdronken en verward bent, een beetje van het padje af misschien. Maximaal 3 zinnen.")
    else:
         raise ValueError("Invalid message_type. Must be 'classification' or 'other' or 'sleepy'.")
        
    # Create the message list with the appropriate system message
    messages = [{"role": "system", "content": system_message}]
    
    # Include the goal text if available
    if goal_text:
        print(f"user prompt: Het ingestelde doel van de gebruiker is: {goal_text}")
        messages.append({"role": "user", "content": f"Het ingestelde doel van de gebruiker is: {goal_text}"})
    
    # Include the user_message, confused bot gets less info
    if message_type == 'sleepy':
        user_content = user_message
    else:
        user_content = f"Een berichtje van {update.effective_user.first_name}: {user_message}"
    if bot_last_response:
        user_content += f" (Reactie op: {bot_last_response})"
    print(f"user prompt: {user_content}")
    messages.append({"role": "user", "content": user_content})
    
    return messages

async def send_openai_request(messages, model="gpt-4o-mini", temperature=None):
    try:
        request_params = {
            "model": model,
            "messages": messages
            }
        # only add temperature if it's provided (not None)
        if temperature is not None:
            request_params["temperature"] = temperature
            
        response = client.chat.completions.create(**request_params)
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error calling OpenAI: {e}")
        return None


# Asynchronous command functions
async def start_command(update, context):
    await update.message.reply_text('Hoi! 👋\n\nIk ben Taeke Toekema Takentovenaar. Stuur me een berichtje als je wilt, bijvoorbeeld om je dagdoel in te stellen of voortgang te rapporteren. Gebruik "@" met mijn naam \n\nKlik op >> /help << voor meer opties')
    
async def help_command(update, context):
    help_message = (
        'Hier zijn de beschikbare commando\'s:\n'
        '👋 /start - Begroeting\n'
        '❓/help - Dit lijstje\n'
        '📊 /stats - Je persoonlijke stats\n'
        '🤔 /reset - Pas je dagdoel aan\n'
        '🗑️ /wipe - Wis je gegevens hier'
    )
    await update.message.reply_text(help_message)

# Helper function to escape MarkdownV2 special characters
def escape_markdown_v2(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))
    
async def stats_command(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Fetch user stats from the database
    try:
        cursor.execute('''
            SELECT total_goals, completed_goals, score, today_goal_status, today_goal_text
            FROM users
            WHERE user_id = %s AND chat_id = %s
        ''', (user_id, chat_id))
    
        result = cursor.fetchone()
    except Exception as e:
        print(f"Error: {e} couldn't fetch user stats?'")
    

    if result:
        total_goals, completed_goals, score, today_goal_status, today_goal_text = result
        completion_rate = (completed_goals / total_goals * 100) if total_goals > 0 else 0
        
        stats_message = f"*Statistieken voor {escape_markdown_v2(update.effective_user.first_name)}*\n"
        stats_message += f"🏆 Score: {score} punten\n"
        stats_message += f"🎯 Doelentotaal: {total_goals}\n"
        stats_message += f"✅ Voltooid: {completed_goals} {escape_markdown_v2(f'({completion_rate:.1f}%)')}\n"
        
        # Check for the three possible goal statuses
        if today_goal_status == 'set':
            stats_message += f"📅 Dagdoel: Ingesteld\n📝 {escape_markdown_v2(today_goal_text)}"
        elif today_goal_status.startswith('Done'):
            completion_time = today_goal_status.split(' ')[3]  # Extracts time from "Done today at H:M"
            stats_message += f"📅 Dagdoel: Voltooid om {escape_markdown_v2(completion_time)}\n📝 ||{escape_markdown_v2(today_goal_text)}||"
        else:
            stats_message += '📅 Dagdoel: Nog niet ingesteld'
        try:       
            await update.message.reply_text(stats_message, parse_mode="MarkdownV2")
        except AttributeError as e:
            print("die gekke error weer (jaaa)")
    else:
        await update.message.reply_text(
        escape_markdown_v2("Je hebt nog geen statistieken. \nStuur me een berichtje met je dagdoel om te beginnen (gebruik '@') 🧙‍♂️"),
        parse_mode="MarkdownV2"
    )
  
async def reset_command(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if has_goal_today(user_id, chat_id):
        try:
            #Reset the user's goal status, subtract 1 point, and clear today's goal text
            cursor.execute('''
                           UPDATE users
                           SET today_goal_status = 'not set',
                           score = score - 1,
                           today_goal_text = '',
                           total_goals = total_goals - 1
                           WHERE user_id = %s AND chat_id = %s
                           ''', (user_id, chat_id))
            conn.commit()
        except Exception as e:
            print(f"Error resetting goal in database: {e}")
            conn.rollback
        
        await update.message.reply_text("Je doel voor vandaag is gereset 🧙‍♂️\n_-1 punt_", parse_mode="Markdown")
    else:
        await update.message.reply_text("Je hebt geen onvoltooid doel om te resetten 🧙‍♂️ \n(_Zie /stats voor je dagdoelstatus_).", parse_mode="Markdown")
        
async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    challenger = update.effective_user
    challenger_id = challenger.id
    challenger_name = challenger.first_name
    chat_id = update.effective_chat.id

    # Check if the command is a reply to another message
    if update.message.reply_to_message is None:
        await update.message.reply_text("Je moet deze command als reply gebruiken op het bericht van degene die je wilt uitdagen! 🧙‍♂️")
        return

    challenged = update.message.reply_to_message.from_user
    challenged_id = challenged.id
    challenged_name = challenged.first_name
    if challenger_id == challenged_id:
        await update.message.reply_text(f"🚫 BELANGENVERSTRENGELING! 🚫🧙‍♂️")
        return  # Stop further execution if the user is challenging themselves
    
    # Check if the challenger has enough points to challenge (needs at least 1 point)
    cursor.execute('SELECT score FROM users WHERE user_id = %s AND chat_id = %s', (challenger_id, chat_id))
    score = cursor.fetchone()
    if not score or score[0] < 1:
        await update.message.reply_text(f"🚫 {challenger_name}, je hebt niet genoeg punten om iemand uit te dagen! 🧙‍♂️\nJe hebt minstens 1 punt nodig (/stats)")
        return  # Stop further execution if the challenger has fewer than 1 point

    # Check if the challenger has already challenged the user
    # cursor.execute('SELECT COUNT(*) FROM challenges WHERE challenger_id = %s AND challenged_id = %s AND chat_id = %s',
    #                 (challenger_id, challenged_id, chat_id))
    # result = cursor.fetchone()
    # if result and result[0] > 0:
    # #     await update.message.reply_text(f"🚫 {challenger_name}, je hebt {challenged_name} vandaag al uitgedaagd!")
    # #     return  # Stop further execution if the challenger has already challenged the user today

    # Check if the challenged user has a goal set for today #moet ook werken als result leeg is 
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (challenged_id, chat_id))
        result = cursor.fetchone()
        if result is None:
            goal_status = 'not set'

        if goal_status == 'not set':
            await update.message.reply_text(f"🚫 {challenged_name} heeft vandaag nog geen doel ingesteld! 🧙‍♂️")
        if goal_status.startswith("Done"):
            await update.message.reply_text(f"🚫 {challenged_name} heeft vandaag het doel al behaald! 🧙‍♂️")
            return
    except Exception as e:
        print(f"Error selecting goal: {e}")
   
    
    # Vanaf hier is het menens
    await update.message.reply_text(f"confirmation message")

    # Subtract 1 point from the challenger's score
    try:
        cursor.execute('UPDATE users SET score = score - 1 WHERE user_id = %s AND chat_id = %s', (challenger_id, update.effective_chat.id))
        conn.commit
    except Exception as e:
        print(f"Error subtracting point (rolled back): {e}")
        conn.rollback
    

# Define a state for the conversation
CONFIRM_WIPE = range(1)

async def wipe_command(update, context):
    user_id = update.effective_user.id
    
    # Ask for confirmation
    await update.message.reply_text(
        "Weet je zeker dat je al je voortgang wilt laten wegtoveren? 🧙‍♂️\n\nTyp 'JA' om te bevestigen, of iets anders om te annuleren."
    )

    # Set the conversation state
    return CONFIRM_WIPE

async def confirm_wipe(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_response = update.message.text.strip().upper()
    
    if user_response == 'JA':
        try:
            cursor.execute('DELETE FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
            conn.commit()
        except Exception as e:
            print(f"Error wiping database after confirm_wipe: {e}")
            conn.rollback
        await update.message.reply_text("Je gegevens zijn gewist 🕳️")
    else:
        await update.message.reply_text("Wipe geannuleerd 🚷")
    
    return ConversationHandler.END
        

# Function to update user goal text to present or past tense in the database when Doelstelling or Klaar 
def update_user_goal(user_id, chat_id, goal_text):
    try:  
        cursor.execute('''
        INSERT INTO users (user_id, chat_id, today_goal_text)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, chat_id) DO UPDATE SET
        today_goal_text = EXCLUDED.today_goal_text
    ''', (user_id, chat_id, goal_text))
        conn.commit()
    except Exception as e:
        print(f"Error in update_user_goal: {e}")
        conn.rollback
    
# Function to check if user has set a goal today
def has_goal_today(user_id, chat_id):
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        return result and result[0] == 'set'
    except Exception as e:
        print(f"Error has_goal_today: {e}")

# Function to check if user has finished a goal today
def finished_goal_today(user_id, chat_id):
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        return result and result[0].startswith("Done")
    except Exception as e:
        print(f"Error finished_goal_today: {e}")

bot_message_ids = {}

# Function to analyze any chat message, and check whether it replies to the bot, mentions it, or neither
async def analyze_message(update, context):
    try:
        if update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot:
            await analyze_bot_reply(update, context)
            print("analyze_message > analyze_bot_reply")
        elif update.message and '@TakenTovenaar_bot' in update.message.text:
            await analyze_bot_mention(update, context)
            print("analyze_message > analyze_bot_mention")
        else:
            await analyze_regular_message(update, context)
            print("analyze_message > analyze_regular_message")
    except Exception as e:
        await update.message.reply_text("Er ging iets mis in analyze_message, probeer het later opnieuw.")
        print(f"Error: {e}")    

async def print_edit(update, context):
    print("Someone edited a message")
        
# Function to analyze replies to bot
async def analyze_bot_reply(update, context):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    bot_last_response = update.message.reply_to_message.text
    goal_text = fetch_goal_text(update)
    

    try:
        # Prepare and send OpenAI messages with bot_last_response
        messages = prepare_openai_messages(
            update, 
            user_message, 
            'classification', 
            goal_text=goal_text if goal_text else None, 
            bot_last_response=bot_last_response
        )
        assistant_response = await send_openai_request(messages, temperature=0.1)
        print(f"analyze_bot_reply > classification: {assistant_response}")
        # Handle the OpenAI response
        if assistant_response == 'Doelstelling' and finished_goal_today(user_id, chat_id):
            rand_value = random.random()
            # 4 times out of 5 (80%s)
            if rand_value < 0.80:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag. Morgen weer een dag! 🐝")
            # once every 6 times (16,67%s)
            elif rand_value >= 0.8333:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag... STREBER! 😘")
            # once every 30 times (3,33%s)    
            else:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag. Verspilling van moeite dit. En van geld. Graag €0,01 naar mijn schepper, B. ten Berge:\nDE13 1001 1001 2622 7513 46 💰")
        elif assistant_response == 'Doelstelling':
            await handle_goal_setting(update, user_id, chat_id)
            print("analyze_bot_reply > handle_goal_setting")
        elif assistant_response == 'Klaar' and has_goal_today(user_id, chat_id):
            await handle_goal_completion(update, context, user_id, chat_id, goal_text)
            print("analyze_bot_reply > handle_goal_completion")
        else:
            await handle_unclassified_mention(update)
            print("analyze_bot_reply > handle_unclassified_mention")

    except Exception as e:
        await update.message.reply_text("Er ging iets misss, probeer het later opnieuw.")
        print(f"Error: {e}")

# Function to analyze @TakenTovenaar_bot mentions via OpenAI Chat API
async def analyze_bot_mention(update, context):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        # Fetch goal_text if the user has a goal today, and use None if it's empty, for prepare_openai_messages
        goal_text = fetch_goal_text(update)
        goal_text=goal_text if goal_text else None

        # Prepare and send OpenAI messages
        messages = prepare_openai_messages(update, user_message, message_type='classification', goal_text=goal_text)
        print(messages)
        assistant_response = await send_openai_request(messages, temperature=0.1)
        print(f"analyze_bot_mention > classification: {assistant_response}")
        if assistant_response == 'Doelstelling' and finished_goal_today(user_id, chat_id):
            
            rand_value = random.random()
            # 4 times out of 5 (80%s)
            if rand_value < 0.80:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag. Morgen weer een dag! 🐝")
            # once every 6 times (16,67%s)
            elif rand_value >= 0.8333:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag... STREBER! 😘")
            # once every 30 times (3,33%s)    
            else:
                await update.message.reply_text("Je hebt je doel al gehaald vandaag. Verspilling van moeite dit. En van geld. Graag €0,01 naar mijn schepper, B. ten Berge:\nDE13 1001 1001 2622 7513 46 💰")
        elif assistant_response == 'Doelstelling':
            await handle_goal_setting(update, user_id, chat_id)
            print("analyze_bot_mention > handle_goal_setting")
        elif assistant_response == 'Klaar' and has_goal_today(user_id, chat_id):
            await handle_goal_completion(update, context, user_id, chat_id, goal_text)
            print("analyze_bot_mention > handle_goal_completion")
        else:
            await handle_unclassified_mention(update)
            print("analyze_bot_mention > unclassified_mention")

    except Exception as e:
        await update.message.reply_text("Er ging iets mis, probeer het later opnieuw.")
        print(f"Error: {e}")

# Analyze a regular message, to see if it's maybe trying to set or complete goals 
async def analyze_regular_message(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    
    # Prepare and send OpenAI messages
    # messages = prepare_openai_messages(update, user_message, 'classification', goal_text)
    # assistant_response = await send_openai_request(messages, temperature=0.1)
    # if assistant_response == "Doelstelling":
    #     # Later bouwen: goal_setting_confirmation
    #     await handle_goal_setting(update, user_id, chat_id)
    # elif assistant_response == "Klaar" and has_goal_today(user_id, chat_id):
    #     # Later bouwen: goal_completion_confirmation
    #     await handle_goal_completion(update, context, user_id, chat_id, goal_text)
    # else:
    await handle_regular_message(update, context)
    print("analyze_regular_message > handle_regular_message")

async def handle_regular_message(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    if len(user_message) > 11 and random.random() < 0.06:
        messages = prepare_openai_messages(update, user_message, 'sleepy')
        assistant_response = await send_openai_request(messages, "gpt-4o")
        await update.message.reply_text(assistant_response)
    # Random plek om de bot impromptu random berichtjes te laten versturen huehue
    # Reply to trigger    
    #elif user_message == '👀':
    #    await update.message.reply_text("@Anne-Cathrine, ben je al aan het lezen? 🧙‍♂️😘")
    # Send into the void
    elif user_message == 'oké en we zijn weer live':
        await context.bot.send_message(chat_id=update.message.chat_id, text="Database gereset hihi, allemaal ONvoLDoEnDe! 🧙‍♂️\n\nMaar we zijn weer live 🧙‍♂️", parse_mode="Markdown")
    elif user_message == 'whoops':
        await context.bot.send_message(chat_id=update.message.chat_id, text="*Ik ben voorlopig kapot. Tot later!* 🧙‍♂️", parse_mode="Markdown")

    # Dice-roll
    elif user_message.isdigit() and 1 <= int(user_message) <= 6:
        dice_roll(update, context)

    # Nightly reset simulation
    elif user_message.isdigit() and 666:    
        completion_time = datetime.datetime.now().strftime("%sH:%sM")
        # Reset goal status
        try:
            cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = ''")
            conn.commit()
            print("666 Goal status reset at", datetime.datetime.now())
            await context.bot.send_message(chat_id=update.message.chat_id, text="_EMERGENCY RESET COMPLETE_  🧙‍♂️", parse_mode="Markdown")
        except Exception as e:
            conn.rollback()  # Rollback the transaction on error
            print(f"Error: {e}")        

# Assistant_response == 'Doelstelling'          
async def handle_goal_setting(update, user_id, chat_id):
    if has_goal_today(user_id, chat_id):
        await update.message.reply_text('Je hebt vandaag al een doel doorgegeven! 🐝')
    else:
        user_message = update.message.text

        # Rephrase the goal in second person
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=1,
                messages=[
                    {"role": "system", "content": "Je herformuleert de dagelijkse doelstelling van de gebruiker naar een zin in de tweede persoon enkelvoud, zoiets van 'Vandaag ga jij [doel]', 'Jij bent vandaag van plan [doel]', etc. Zorg ervoor dat bij het omschrijven geen informatie verloren gaat."},
                    {"role": "user", "content": user_message}
                ]
            )
            goal_text = response.choices[0].message.content.strip()
        except Exception as e:
            await update.message.reply_text("Er ging iets mis bij het verwerken van je doel. Probeer het later opnieuw.")
            print(f"Error in rewording goal to 2nd person: {e}")
            return
        
        # Save the reworded goal in the database        
        update_user_goal(user_id, chat_id, goal_text)
        # Change goal status and total
        try:
            cursor.execute('''
                           UPDATE users 
                           SET today_goal_status = 'set',
                           total_goals = total_goals + 1,
                           score = score + 1
                           WHERE user_id = %s AND chat_id = %s
                           ''', (user_id, chat_id))
            conn.commit()
        except Exception as e:
            await update.message.reply_text("Doelstatusprobleempje. Probeer het later opnieuw.")
            print(f"Error updating today_goal_status: {e}")
            conn.rollback
            return
        
        # Send confirmation message
        if update.message.text.endswith(';)'):
            await update.message.reply_text('🥰 Succes schatje! 😚 \n_+1 punt_', parse_mode="Markdown")
        elif random.random() < 0.125:
            await update.message.reply_text('Staat genoteerd! 🧙‍♂️ Succes! 💖 \n_+1 punt_', parse_mode="Markdown")
        else:
            responses = [
                'Staat genoteerd! ✍️ \n_+1 punt_',
                'Staat genoteerd! 📝 \n_+1 punt_',
                'Staat genoteerd! 📋 \n_+1 punt_',
                'Staat genoteerd! ✒️ \n_+1 punt_',
                'Staat genoteerd! 🖊️ \n_+1 punt_',
                'Staat genoteerd! ✏️ \n_+1 punt_',
                'Staat genoteerd! 🧙‍♂️ \n_+1 punt_'
            ]
            reply = random.choice(responses)
            await update.message.reply_text(reply, parse_mode="Markdown")
            

async def check_goal_compatibility(update, goal_text, user_message):
    messages = [
                {"role": "system", "content": "Controleer of een bericht zou kunnen rapporteren over het behalen van een gesteld doel. Antwoord alleen met 'Ja' of 'Nee'."},
                {"role": "user", "content": f"Het gestelde doel is {goal_text} en het bericht is {user_message}"}
            ]
    assistant_response = await send_openai_request(messages, temperature=0.1)
    print(f"check_goal_compatibility: {messages}\n\n\nUitkomst check: {assistant_response}")
    return assistant_response

# Assistant_response == 'Klaar'             
async def handle_goal_completion(update, context, user_id, chat_id, goal_text):
    # Rephrase the goal in past tense
    user_message = update.message.text
    try:
        assistant_response = await check_goal_compatibility(update, goal_text, user_message)
        if assistant_response == 'Nee':
            await handle_unclassified_mention(update)
        else:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                messages=[
                    {"role": "system", "content": "Herformuleer naar een succesvol afgerond doel: tweede persoon enkelvoud, verleden tijd."},
                    {"role": "user", "content": goal_text}
                ]
            )
            goal_text = response.choices[0].message.content.strip()
            # Save the reworded past tense goal in the database        
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            update_user_goal(user_id, chat_id, goal_text)
        
        
            completion_time = datetime.datetime.now().strftime("%sH:%sM")
            # Update user's goal status and statistics
            cursor.execute('''
                UPDATE users 
                SET today_goal_status = %s, 
                    completed_goals = completed_goals + 1,
                    score = score + 4
                WHERE user_id = %s AND chat_id = %s
            ''', (f"Done today at {completion_time}", user_id, chat_id))
            conn.commit()
            await update.message.reply_text("Lekker bezig! ✅ \n_+4 punten_"
                                    , parse_mode="Markdown")
    except Exception as e:
        print(f"Error in goal_completion: {e}")
        conn.rollback
        return

# Assistant_response == 'Overig'  
async def handle_unclassified_mention(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    goal_text=goal_text if goal_text else None
    bot_last_response = update.message.reply_to_message.text if update.message.reply_to_message else None
    
    messages = prepare_openai_messages(update, user_message, 'other', goal_text, bot_last_response)
    assistant_response = await send_openai_request(messages, "gpt-4o")
    await update.message.reply_text(assistant_response)

        
async def reset_goal_status(context):
    try:
        # Fetch all unique chat IDs from the users table
        cursor.execute("SELECT DISTINCT chat_id FROM users")
        chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]

        # Reset goal status for all users
        cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = ''")
        conn.commit()
        print("Goal status reset at", datetime.datetime.now())

        # Send reset message to all active chats
        for chat_id in chat_ids:
            await context.bot.send_message(chat_id=chat_id, text="_Dagelijkse doelen gereset_  🧙‍♂️", parse_mode="Markdown")

    except Exception as e:
        print(f"Error resetting goal status: {e}")
        conn.rollback()

async def dice_roll(update, context):
    user_message = update.user_message
    try:
        # score = fetch_score()
        if 5>3:
            
            # Send the dice and capture the message object
            dice_message = await context.bot.send_dice(
            chat_id=update.message.chat_id,
            reply_to_message_id=update.message.message_id
        )
            # Extract the value that the user guessed
            user_guess = int(user_message)
    
            # Check the outcome of the dice roll
            rolled_value = dice_message.dice.value
            await asyncio.sleep(4)
    
            # Give a reply based on the rolled value
            if rolled_value == user_guess:
                await context.bot.send_message(
                chat_id=update.message.chat_id, 
                text=f"🎉",
                reply_to_message_id=update.message.message_id
            )
                await context.bot.send_message( 
                text=f"_+4 punten_", parse_mode="Markdown",
                reply_to_message_id=update.message.message_id
            )
            else:
                await context.bot.send_message(
                chat_id=update.message.chat_id, 
                text=f"nope.\n_-1 punt_", parse_mode="Markdown",
                reply_to_message_id=update.message.message_id
            )
        else:
            await context.bot.send_message(
            chat_id=update.message.chat_id, 
            text=f"Je hebt niet genoeg punten om te dobbelen 🧙‍♂️",
            reply_to_message_id=update.message.message_id
        )

    except Exception as e:
        print (f"Error: {e}")


# Schedule the job
async def schedule_goal_reset_job(application):
    job_queue = application.job_queue
    job_queue.run_repeating(reset_goal_status, interval=24*60*60, first=datetime.time(hour=2))

        

def main():
    print("Entering main function")
    try:
        # Fetch the API token from environment variables
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if token is None:
            raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")
    
        # Create the bot application with ApplicationBuilder
        application = ApplicationBuilder().token(token).build()
        print("main 1/6")
        # Bind the commands to their respective functions
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        print("main 2/6")
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(CommandHandler("challenge", challenge_command))
        print("main 3/6")
        wipe_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('wipe', wipe_command)],
            states={
                CONFIRM_WIPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_wipe)],
            },
            fallbacks=[],
            conversation_timeout=30
        )
        application.add_handler(wipe_conv_handler)
        print("main 4/6")
    
        # Bind the message analysis to any non-command text messages
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.UpdateType.MESSAGE, analyze_message))
        print("main 5/6")
        # Handler for edited messages
        application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND, print_edit))
    
        # Schedule the reset job using job_queue
        # job_queue = application.job_queue
        # job_queue.run_daily(reset_goal_status, time=datetime.time(hour=2, minute=0, second=0))
        print("main 6/6")
        # Start the bot
        application.run_polling()
        print("Exiting main function normally")
    except Exception as e:
        print(f"Error in main function: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print(traceback.format_exc())
if __name__ == '__main__':
    main()
