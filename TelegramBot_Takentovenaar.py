from ast import Try
import os
from pickle import TRUE
import random
from tempfile import TemporaryFile
from telegram import User, Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, CallbackContext, filters, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv
import asyncio
import re
import json
import psycopg2
import pytz
from datetime import datetime, time, timedelta

# berlin_tz = pytz.timezone('Europe/Berlin')
# berlin_time = datetime.now(berlin_tz)


# Load .env file if running locally, and let it take precedent over any other source for API keys (should still work in Heroku, cause there nothing will be loaded)
load_dotenv(override=True)

# Get OpenAI API key from environment variable (works in both local and Heroku)
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    raise ValueError("OPENAI_API_KEY not found! Ensure it's set in the environment.")
# Initialize the OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# For local development
LOCAL_DB_URL = "postgresql://postgres:OmtePosten@localhost/mydb"

# Use environment variable for Heroku, fallback to local for development
DATABASE_URL = os.getenv('DATABASE_URL', LOCAL_DB_URL)

# Use DATABASE_URL if available (Heroku), otherwise fallback to LOCAL_DB_URL
DATABASE_URL = os.getenv('DATABASE_URL', os.getenv('LOCAL_DB_URL'))

print(f"__________OpenAI API Key_________:'\n{api_key}\n")

# Connect to the PostgreSQL database
if os.getenv('DATABASE_URL'):  # Running on Heroku
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
else:  # Running locally
    conn = psycopg2.connect(DATABASE_URL)  # For local development, no SSL required



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
        'user_id': 'BIGINT',
        'chat_id': 'BIGINT',
        'total_goals': 'INTEGER DEFAULT 0',
        'completed_goals': 'INTEGER DEFAULT 0',
        'score': 'INTEGER DEFAULT 0',
        'today_goal_status': "TEXT DEFAULT 'not set'",
        'set_time': 'TIMESTAMP',  
        'today_goal_text': "TEXT DEFAULT ''",
        'pending_challenge': "TEXT DEFAULT '{}'",
        'inventory': "JSONB DEFAULT '{\"boosts\": 2, \"links\": 0, \"challenges\": 0}'",
        'first_name': 'TEXT'
    }

    # Create the tables if they don't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            chat_id BIGINT,
            first_name TEXT,
            total_goals INTEGER DEFAULT 0,
            completed_goals INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            today_goal_status TEXT DEFAULT 'not set',
            set_time TIMESTAMP,       
            today_goal_text TEXT DEFAULT '',
            pending_challenge TEXT DEFAULT '{}',
            inventory JSONB DEFAULT '{"boosts": 2, "links": 0, "challenges": 0}',       
            PRIMARY KEY (user_id, chat_id)
        )
    ''')
    conn.commit()
    
    # Calculate 2:01 AM last night to set default last_reset_time
    now = datetime.now()
    unformatted_time = now.replace(hour=2, minute=1, second=0, microsecond=0) - timedelta(days=1)
    two_am_last_night = unformatted_time.strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bot_status (
        last_reset_time TIMESTAMP DEFAULT %s
        )
    ''', (two_am_last_night,))
    conn.commit()

    # Alter user_id and chat_id to BIGINT if needed
    try:
        cursor.execute("""
            ALTER TABLE users
            ALTER COLUMN user_id TYPE BIGINT,
            ALTER COLUMN chat_id TYPE BIGINT
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
    conn.rollback()

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
    conn.rollback()

# Helper functions to reduce bloat/increase modularity
def get_inventory(user_id, chat_id):
    try:
        cursor.execute("SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
        result = cursor.fetchone()
        return result[0]
    except Exception as e:
        print(f"Error getting inventory: {e}")
        
# Function to check if the engager has sufficient inventory to engage
async def check_special_inventory(update, context, engager_id, chat_id, special_type):
    engager_name = update.effective_user.first_name
    
    # Check if the engager has sufficient inventory to engage 
    cursor.execute('SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s', (engager_id, chat_id))
    result = cursor.fetchone()
    
    if result is None:
        await update.message.reply_text(f"🚫 {engager_name}, je hebt geen inventory! Dit is een bug 🐛.")
        return False

    inventory = json.loads(result[0]) # Parse the JSON string into a Python dictionary
    special_count = inventory.get(special_type, 0)
    
    if inventory[special_type] <= 0:
        await update.message.reply_text(f"{engager_name}, je hebt geen {special_type} meer in je inventaris!")
        return False
    
    if special_count < 1:
        await update.message.reply_text(f"🚫 {engager_name}, je hebt geen {special_type} meer! 🧙‍♂️\n_Zie (/inventory)_", parse_mode="Markdown")
        return False  # Stop further execution if the engager has fewer than 1 of the special_type

    return True  # The engager has sufficient inventory

async def use_special(user_id, chat_id, special_type):
    # Dynamically construct the JSON path string
    path = '{' + special_type + '}'
    
    # Build the SQL query, using safe parameterization for user data
    query = '''
        UPDATE users
        SET inventory = jsonb_set(
            inventory,
            %s,  -- The path in the JSON structure
            (COALESCE(inventory->>%s, '0')::int - 1)::text::jsonb  -- Update the special_type count
        )
        WHERE user_id = %s AND chat_id = %s AND (inventory->>%s)::int > 0
        RETURNING inventory
    '''
    
    # Execute the query, passing the dynamic path and safe parameters
    cursor.execute(query, (path, special_type, user_id, chat_id, special_type))
    conn.commit()

    return cursor.fetchone() is not None


def add_special(user_id, chat_id, special_type, amount=1):
    try:
        # Dynamically construct the JSON path string
        path = '{' + special_type + '}'
    
        # Build the SQL query, using safe parameterization for user data
        query = '''
            UPDATE users
            SET inventory = jsonb_set(
                inventory,
                %s,  -- The path in the JSON structure
                (COALESCE(inventory->>%s, '0')::int + %s)::text::jsonb  -- Update the special_type count
            )
            WHERE user_id = %s AND chat_id = %s AND (inventory->>%s)::int > 0
            RETURNING inventory
        '''
    
        # Execute the query, passing the dynamic path and safe parameters
        cursor.execute(query, (path, special_type, amount, user_id, chat_id, special_type))
        conn.commit()
    except Exception as e:
        print(f"Error add_special: {e}")
        return

async def show_inventory(update, context):
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name
        inventory = get_inventory(user_id, chat_id)
        # Define a dictionary to map items to their corresponding emojis
        emoji_mapping = {
            "boosts": "⚡",
            "links": "🔗",
            "challenges": "🏆"
        }
        inventory_text = "\n".join(
            f"{emoji_mapping.get(item, '')} {item}: {count}"
            for item, count in inventory.items()
        )
        await update.message.reply_text(f"*Inventaris van {first_name}*\n{inventory_text}", parse_mode="Markdown")
    except Exception as e:
        print(f"Error showing inventory: {e}")

def fetch_goal_text(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        cursor.execute('SELECT today_goal_text FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        if result:
            goal_text = result[0]
            if goal_text != '':
                print(f"Goal text found: {goal_text}")
                return goal_text # Goal is set
            else:
                print("No goal set for today.")
                return ''
        else:
            print("Goal text not found.")
            return None  # Return empty string if no goal text is found
    except Exception as e:
        print(f"Error fetching goal data: {e}")
        return ''  # Return empty string if an error occurs
    
def fetch_score(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        cursor.execute('SELECT score FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        if result is not None:
            return result[0]  # Extracting the score from the tuple
        else:
            return 0  # Return a default value if the score is not found
    except Exception as e:
        print(f"Error fetching score: {e}")
        return 0  # Return a default value in case of error


def prepare_openai_messages(update, user_message, message_type, goal_text=None, bot_last_response=None):
    # Define system messages based on the message_type
    first_name = update.effective_user.first_name
    if message_type == 'classification':
        print("system prompt: classification message")
        system_message = (
            "Jij classificeert een berichtje van een gebruiker in een Telegramgroep "
            "in een van de volgende drie groepen: Doelstelling, Klaar of Overig. "
            "Elk bericht waaruit blijkt dat de gebruiker van plan is om iets (specifieks) te gaan doen vandaag, "
            "is een 'Doelstelling'. Als de gebruiker rapporteert dat het ingestelde doel met succes is afgerond, "
            "dan is dat 'Klaar'. Alle andere gevallen zijn 'Overig'. Antwoord alleen met 'Doelstelling', 'Klaar' of 'Overig'."
        )
    elif message_type == 'other':
        print("system prompt: other message")
        system_message = (
            "Jij bent @TakenTovenaar_bot, de enige bot in een accountability-Telegramgroep van vrienden. "
            "Gedraag je cheeky en mysterieus, maar streef bovenal naar waarheid. "
            "Als de gebruiker een metavraag of -verzoek heeft over bijvoorbeeld een doel stellen in de appgroep, "
            "antwoord dan alleen dat ze het command /help kunnen gebruiken. "
            "Er zijn meer commando's, maar die ken jij allemaal niet. "
            "Je hebt nu alleen toegang tot dit bericht, niet tot volgende of vorige berichtjes. "
            "Een back-and-forth met de gebruike is dus niet mogelijk."
        )
    elif message_type == 'sleepy':
        print("system prompt: sleepy message")
        system_message = ("Geef antwoord alsof je slaapdronken en verward bent, een beetje van het padje af misschien. Maximaal 3 zinnen.")
    elif message_type == 'grandpa quote':
        print("system prompt: grandpa quote message")
        system_message = ("Je bent een beetje cheeky, diepzinnig, mysterieus en bovenal wijs. Verzin een uitspraak die je opa zou kunnen hebben over een gegeven doel.")
        messages = [{"role": "system", "content": system_message}]
        messages.append({"role": "user", "content": f"{goal_text}"})
        return messages
    else:
         raise ValueError("Invalid message_type. Must be 'classification' or 'other' or 'sleepy' or 'grandpa quote'.")
    messages = [{"role": "system", "content": system_message}]        
    # Include the goal text if available
    if goal_text:
        print(f"user prompt: Het ingestelde doel van {first_name} is: {goal_text}")
        messages.append({"role": "user", "content": f"Het ingestelde doel van {first_name} is: {goal_text}"})
    
    # Include the user_message, confused bot gets less info
    if message_type == 'sleepy':
        user_content = user_message
    else:
        user_content = f"Een berichtje van {first_name}: {user_message}"
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
    await update.message.reply_text('Hoi! 👋\n\nIk ben Taeke Toekema Takentovenaar. Stuur me een berichtje als je wilt, bijvoorbeeld om je dagdoel in te stellen of af te sluiten. Gebruik "@" met mijn naam, bijvoorbeeld zo:\n\n"@TakenTovenaar_bot ik wil vandaag 420 gram groenten eten" \n\nKlik op >> /help << voor meer opties')

# Randomly pick a message
def get_random_philosophical_message():
    philosophical_messages = [
            "Hätte hätte, Fahrradkette 🧙‍♂️",  # Message 1
            "千里之行，始于足下 🧙‍♂️",        
            "Ask, believe, receive 🧙‍♂️",   
            "A few words on looking for things. When you go looking for something specific, "
    "your chances of finding it are very bad. Because, of all the things in the world, "
    "you're only looking for one of them. When you go looking for anything at all, "
    "your chances of finding it are very good. Because, of all the things in the world, "
    "you're sure to find some of them 🧙‍♂️",
            "Je bent wat je eet 🧙‍♂️",
            "If the human brain were so simple that we could understand it, we would be so simple that we couldn't 🧙‍♂️",       
            "Believe in yourself 🧙‍♂️",  
            "Hoge loofbomen, dik in het blad, overhuiven de weg 🧙‍♂️",   
            "It is easy to find a logical and virtuous reason for not doing what you don't want to do 🧙‍♂️",  
            "Our actions are like ships which we may watch set out to sea, and not know when or with what cargo they will return to port 🧙‍♂️",
            "A sufficiently intimate understanding of mistakes is indistinguishable from mastery 🧙‍♂️",
            "He who does not obey himself will be commanded 🧙‍♂️",
            "Elke dag is er wel iets waarvan je zegt: als ik die taak nou eens zou afronden, "  
    "dan zou m'n dag meteen een succes zijn. Maar ik heb er geen zin in. Weet je wat, ik stel het "
    "me als doel in de Telegramgroep, en dan ben ik misschien wat gemotiveerder om het te doen xx 🙃",
            "All evils are due to a lack of Telegram bots 🧙‍♂️",
            "Art should disturb the comfortable, and comfort the disturbed 🧙‍♂️",
            "Genius is one per cent inspiration, ninety-nine per cent perspiration 🧙‍♂️",
            "Don't wait. The time will never be just right 🧙‍♂️",
            "If we all did the things we are capable of doing, we would literally astound ourselves 🧙‍♂️",
            "There's power in looking silly and not caring that you do 🧙‍♂️", # Message 20
            "... 🧙‍♂️",
            "Te laat, noch te vroeg, arriveert (n)ooit de takentovenaar 🧙‍♂️" # Message 22
        ]
    return random.choice(philosophical_messages)


async def filosofie_command(update, context):
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        goal_text = fetch_goal_text(update)
        philosophical_message = get_random_philosophical_message()
        if goal_text != '' and goal_text != None:
                messages = prepare_openai_messages(update, user_message="onzichtbaar", message_type = 'grandpa quote', goal_text=goal_text)
                grandpa_quote = await send_openai_request(messages, "gpt-4o")    
                await update.message.reply_text(f"Mijn grootvader zei altijd:\n✨{grandpa_quote}✨", parse_mode="Markdown")
        else:  
            await update.message.reply_text(philosophical_message)
    except Exception as e:
        print(f"Error in filosofie_command: {e}")
 
async def help_command(update, context):
    help_message = (
        'Hier zijn de beschikbare commando\'s:\n'
        '👋 /start - Begroeting\n'
        '❓/help - Dit lijstje\n'
        '📊 /stats - Je persoonlijke stats\n'
        '🤔 /reset - Pas je dagdoel aan\n'
        '🗑️ /wipe - Wis je gegevens in deze chat\n'
        '💭 /filosofie - De gedachten erachter'
        '🎒 /inventaris - Bekijk of je speciale moves kunt maken'
    )
    await update.message.reply_text(help_message)

# Helper function to escape MarkdownV2 special characters
def escape_markdown_v2(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

async def check_use_of_special(update, context, special_type):
    chat_id = update.effective_chat.id
    engager = update.effective_user
    engager_id = engager.id
    engager_name = engager.first_name
    
    # Check if the command is a reply to another message
    if update.message.reply_to_message is None:
        await update.message.reply_text(f"Je moet deze command als reply gebruiken op het bericht van degene die je wilt {special_type_verb}! 🧙‍♂️")
        print(f"{special_type_singular} couldn't be used by {engager_name}")
        return False
    
    engaged = update.message.reply_to_message.from_user
    engaged_id = engaged.id
    engaged_name = engaged.first_name

    special_type_singular = special_type.rstrip('s')
    if special_type_singular.endswith('e'):
        special_type_verb = special_type_singular + 'n'
    else:
        special_type_verb = special_type_singular + 'en'
    
    if engaged.first_name == "TestTovenaar_bot":
        await update.message.reply_text(f"🚫 Y O U  S H A L L  N O T  P A S S ! 🚫🧙‍♂️\n_      a {special_type_singular} to me..._", parse_mode = "Markdown")
        print(f"{special_type_singular} couldn't be used by {engager_name}")
        return False
    elif engager_id == engaged_id:
        await update.message.reply_text(f"🚫 BELANGENVERSTRENGELING ! 🚫🧙‍♂️")
        print(f"{special_type_singular} couldn't be used by {engager_name}")
        return False  # Stop further execution if the user is engaging themselves
    
    # Check if the engaged already has a goal set today, not done
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (engaged_id, chat_id))
        result = cursor.fetchone()
        if result is not None:
            goal_status = result[0]
            if goal_status == 'not set':
                await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag nog geen doel ingesteld! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
            elif goal_status.startswith("Done"):
                await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag hun doel al behaald! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
                return False
    except Exception as e:
        print(f"Error selecting goal: {e}")

    # Check if the engager has sufficient inventory to engage
    cursor.execute('SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s', (engager_id, chat_id))
    result = cursor.fetchone()

    if result:
        # Since the result is already a dictionary, we don't need json.loads()
        inventory = result[0]
    
        # Check if the engager has sufficient inventory for the special_type
        if inventory.get(special_type, 0) <= 0:  # Safely get the value, default to 0 if the key doesn't exist
            await update.message.reply_text(f"🚫 {engager_name}, je hebt niet genoeg {special_type}!")
            return False
        else:
            # Proceed with the rest of the logic
            pass
    else:
        await update.message.reply_text(f"🚫 {engager_name}, je hebt geen inventory gevonden!")






    # inventory = result[0]
    # if inventory[special_type] <= 0:
    #     await update.message.reply_text(f"🚫 {engager_name}, je hebt niet genoeg {special_type} om iemand te {special_type_verb}! 🧙‍♂️\n_Zie (/inventory)_", parse_mode="Markdown")
    #     return False    
    # return True

    
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
            cursor.execute("SELECT set_time FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
            set_time = cursor.fetchone()
            if set_time:
                set_time = set_time[0]
                formatted_set_time = set_time.strftime("%H:%M")
            stats_message += f"📅 Dagdoel: Ingesteld om {escape_markdown_v2(formatted_set_time)}\n📝 {escape_markdown_v2(today_goal_text)}"
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
                           set_time = NULL,
                           score = score - 1,
                           today_goal_text = '',
                           total_goals = total_goals - 1
                           WHERE user_id = %s AND chat_id = %s
                           ''', (user_id, chat_id))
            conn.commit()
        except Exception as e:
            print(f"Error resetting goal in database: {e}")
            conn.rollback()
        
        await update.message.reply_text("Je doel voor vandaag is gereset 🧙‍♂️\n_-1 punt_", parse_mode="Markdown")
    else:
        await update.message.reply_text("Je hebt geen onvoltooid doel om te resetten 🧙‍♂️ \n(_Zie /stats voor je dagdoelstatus_).", parse_mode="Markdown")
        
async def boost_command(update, context):
    # Extract user_id from mention
    entities = update.message.entities  # List of entities in the message
    for entity in entities:
        if entity.type == "mention": # @username
            username = update.message.text[entity.offset::entity.offset + entity.length]
            user = await context.bot.get_chat_member(update.effective_chat.id, username)
            engaged_id = user.user.id
            await handle_special_command(update, context, 'boosts', engaged_id)
            return
        elif entity.type == "invalid mention":
            await update.message.reply_text("🚫 No valid user mentioned 🧙‍♂️")
    if update.message.reply_to_message:
        engaged = update.message.reply_to_message.from_user
        engaged_id = engaged.id
        await handle_special_command(update, context, 'boosts', engaged_id)        
    else: 
        await update.message.reply_text("🚫 Antwoord op iemands berichtje of gebruik een @-mention 🧙‍♂️")
        return

async def link_command(update, context):
    await handle_special_command(update, context, 'links')
    return

async def challenge_command(update, context):
    await handle_special_command(update, context, 'challenges')
    return


async def handle_special_command(update, context, special_type, mention_id=None):
    print(f"entering handle_special_command")
    engager = update.effective_user
    engager_id = engager.id
    engager_name = engager.first_name
    chat_id = update.effective_chat.id
    
    if await check_use_of_special(update, context, special_type) is False:
        print(f"{special_type} couldn't be used by {engager_name}")
        return
    # exclude links and challenges for now
    elif special_type == 'links' or special_type == 'challenges':
        print(f"valid {special_type} tried by {engager_name}")
        await update.message.reply_text(f"Nog eventjes geduld alstublieft, {special_type} werken nog niet 🧙‍♂️")
        return
    else: # truly valid actions from here
        engaged = update.message.reply_to_message.from_user 
        engaged_id = engaged.id
        engaged_name = engaged.first_name
        
        special_type_singular = special_type.rstrip('s')
        await use_special(engager_id, chat_id, special_type)
        await update.message.reply_text(f"Je hebt een {special_type_singular} op {engaged_name} gebruikt! 🧙‍♂️")
        return
    
async def gift_command(update, context):
    if await check_chat_owner(update, context):
        if len(context.args) > 0:  # Check if an argument (like a number) was provided
            arg = context.args[0]
            if arg.isdigit():  # If the argument is a number, treat it as an amount
                amount = int(context.args[0])
                await handle_admin(update, context, 'gift', amount)
                return
            else: # If the arguments is a string, treat it as a special_type
                special_type = arg
                print(f"Special type gifted = {special_type}")
                await handle_admin(update, context, special_type)
                return
        else:
            amount = 1
            await handle_admin(update, context, 'gift', amount)
            return
    else:
        await update.message.reply_text(f"nice try 💝🧙‍♂️")
        return
        
async def steal_command(update, context):
    if await check_chat_owner(update, context):
        if len(context.args) > 0:  # Check if an argument (like a number) was provided
            amount = int(context.args[0])
            await handle_admin(update, context, 'steal', amount)
        else:
            amount = 1
            await handle_admin(update, context, 'steal', amount)
            return
    else:
        message = get_random_philosophical_message()
        await update.message.reply_text(message)
        
async def revert_goal_completion_command(update, context):
    if await check_chat_owner(update, context):
            await handle_admin(update, context, 'revert')
            return
    else:
        message = get_random_philosophical_message()
        await update.message.reply_text(message)

async def handle_admin(update, context, type, amount=None):
    print(f"entering handle_admin_command")
    user_id = update.message.reply_to_message.from_user.id
    first_name = update.message.reply_to_message.from_user.first_name
    chat_id = update.effective_chat.id
    valid_special_types = ['boost', 'link', 'challenge']
    if type == 'gift':
        try:
            cursor.execute('''
                            UPDATE users 
                            SET score = score + %s
                            WHERE user_id = %s AND chat_id = %s
                            ''', (amount, user_id, chat_id))
            conn.commit()
            await update.message.reply_text(f"Taeke Takentovenaar deelt uit 🧙‍♂️\n_+{amount} punt(en) voor {first_name}_", parse_mode = "Markdown")
            return
        except Exception as e:
            print(f"Error updating user score handling admin: {e}")
            conn.rollback()
    elif type == 'steal':
        try:
            cursor.execute('''
                            UPDATE users 
                            SET score = score - %s
                            WHERE user_id = %s AND chat_id = %s
                            ''', (amount, user_id, chat_id))
            conn.commit()
            await update.message.reply_text(f"Taeke Takentovenaar grist weg 🧙‍♂️\n_-{amount} punt(en) van {first_name}_", parse_mode = "Markdown")
            return
        except Exception as e:
            print(f"Error updating user score handling admin: {e}")
            conn.rollback()
    elif type == 'revert':
        try:
            cursor.execute('''
                UPDATE users 
                SET today_goal_status = %s, 
                    completed_goals = completed_goals - 1,
                    score = score - 4
                WHERE user_id = %s AND chat_id = %s
            ''', (f"set", user_id, chat_id))
            conn.commit()
            await update.message.reply_text(f"Whoops ❌ \nTaeke Takentovenaar grist weer 4 punten weg van {first_name} 🧙‍♂️\n_-4 punten, doel teruggezet naar 'ingesteld'_"
                                    , parse_mode="Markdown")
        except Exception as e:
            print(f"Error updating user score handling admin: {e}")    
            conn.rollback()
    else:
       special_type = type
       if special_type not in valid_special_types:
           print(f"Invalid special_type gift: {special_type}")
           return
       else:
           try:
                path = '{' + special_type + '}'
                cursor.execute('''
                    UPDATE users 
                    SET inventory = jsonb_set(
                        inventory,
                        %s,  -- The path in the JSON structure
                        (COALESCE(inventory->>%s, '0')::int + 1)::text::jsonb  -- Add 1 to the special_type count
                    )
                    WHERE user_id = %s AND chat_id = %s
                ''', (path, special_type, user_id, chat_id))
                conn.commit()
                await update.message.reply_text(f"Taeke Takentovenaar deelt uit 🧙‍♂️\n_+1 {special_type} voor {first_name}_", parse_mode = "Markdown")
           except Exception as e:
                await update.message.reply_text(f"Error: {e}")
                return

           


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
    user_response = update.message.text.strip().upper()
    
    if user_response == 'JA':
        try:
            cursor.execute('DELETE FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
            conn.commit()
        except Exception as e:
            print(f"Error wiping database after confirm_wipe: {e}")
            conn.rollback()
        await update.message.reply_text("Je gegevens zijn gewist 🕳️")
        # desired_columns = await desir
        # await add_missing_columns(update, context)
    else:
        await update.message.reply_text("Wipe geannuleerd 🚷")
    
    return ConversationHandler.END
        
async def inventory_command(update, context):
    await show_inventory(update, context)
    
async def check_chat_owner(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Get chat administrators
    admins = await context.bot.get_chat_administrators(chat_id)
    
    # Check if the user is the owner (creator)
    for admin in admins:
        if admin.user.id == user_id and admin.status == 'creator':
            return True
    return False



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
        conn.rollback()
    
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

# First orchestration: function to analyze any chat message, and check whether it replies to the bot, mentions it, or neither
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
    first_name = update.effective_user.first_name
    if len(update.message.text) > 1600:
        await update.message.reply_text(f"Hmpff {first_name}... TL;DR aub? 🧙‍♂️")
        return
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
    first_name = update.effective_user.first_name
    if len(update.message.text) > 1600:
        await update.message.reply_text(f"Hmpff {first_name}... TL;DR aub? 🧙‍♂️")
        return
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
    first_name = update.effective_user.first_name
    if len(update.message.text) > 1600:
        await update.message.reply_text(f"Hmpff {first_name}... TL;DR aub? 🧙‍♂️")
        return
    
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

def random_emoji():
    emojis = ['😈', "👍", "🔥", "⚡"]
    return random.choice(emojis)         
              
async def handle_regular_message(update, context):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    message_id = update.message.message_id
    try:
        reaction = random_emoji
        await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
        # if random.random() < 1:
        #     if random.random() < 0.:
        #         reaction = "👍" 
        #     else:
        #         reaction = "💯" 
        #     await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
    except Exception as e:
        print(f"Error reacting to message: {e}")
    if len(user_message) > 11 and random.random() < 0.02:
        messages = prepare_openai_messages(update, user_message, 'sleepy')
        assistant_response = await send_openai_request(messages, "gpt-4o")
        await update.message.reply_text(assistant_response)
    # Random plek om de bot impromptu random berichtjes te laten versturen huehue
    # Reply to trigger    
    #elif user_message == '👀':
    #    await update.message.reply_text("@Anne-Cathrine, ben je al aan het lezen? 🧙‍♂️😘")
    # Send into the void
    elif user_message == 'oké en we zijn weer live':
        await context.bot.send_message(chat_id=update.message.chat_id, text="Database gereset hihi, allemaal ONvoLDoEnDe! 🧙‍♂️\n\nMaar nu werk ik weer 🧙‍♂️", parse_mode="Markdown")
    elif user_message == "Guess who's back":
            await context.bot.send_message(chat_id=update.message.chat_id, text="Tovenaartje terug 🧙‍♂️", parse_mode="Markdown")        
    elif user_message == 'whoops..!':
        await context.bot.send_message(chat_id=update.message.chat_id, text="*Ik ben voorlopig kapot. Tot later!* 🧙‍♂️", parse_mode="Markdown")

    # Dice-roll
    elif user_message.isdigit() and 1 <= int(user_message) <= 6:
        await roll_dice(update, context)

    # Nightly reset simulation
    elif user_message == '666':
        # Reset goal status
        try:
            cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = ''")
            conn.commit()
            print(f"666 Goal status reset at", datetime.now())
            await context.bot.send_message(chat_id=update.message.chat_id, text="_SCORE STATUS RESET COMPLETE_  🧙‍♂️", parse_mode="Markdown")
        except Exception as e:
            conn.rollback()  # Rollback the transaction on error
            print(f"Error: {e}")
    # Special_type drop 
    elif user_message == 'givboosts':
        # Reset goal status
        try:
            special_type = 'boosts'
        # Build the SQL query, hardcoding the JSON path for 'boosts'
            query = '''
                UPDATE users
                SET inventory = jsonb_set(
                    inventory,
                    '{boosts}',  -- The path in the JSON structure (hardcoded as 'boosts')
                    (COALESCE(inventory->>'boosts', '0')::int + 10)::text::jsonb  -- Update the special_type count
                )
                WHERE user_id = %s AND chat_id = %s
                RETURNING inventory
            '''
        
            # Execute the query, passing the safe parameters
            cursor.execute(query, (user_id, chat_id))
            conn.commit()

            print(f"givboosts", datetime.now())
            await context.bot.send_message(chat_id=update.message.chat_id, text=f"_+ 10 boosts voor {first_name}_ 🧙‍♂️", parse_mode="Markdown")
        except Exception as e:
            conn.rollback()  # Rollback the transaction on error
            print(f"Error givboosts: {e}")
            

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
            set_time = datetime.now()
            cursor.execute('''
                           UPDATE users 
                           SET today_goal_status = 'set',
                           set_time = %s,
                           total_goals = total_goals + 1,
                           score = score + 1
                           WHERE user_id = %s AND chat_id = %s
                           ''', (set_time, user_id, chat_id))
            conn.commit()
        except Exception as e:
            await update.message.reply_text("Doelstatusprobleempje. Probeer het later opnieuw.")
            print(f"Error updating today_goal_status: {e}")
            conn.rollback()
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
                {"role": "system", "content": "Controleer of een bericht zou kunnen rapporteren over het succesvol uitvoeren van een gesteld doel. Antwoord alleen met 'Ja' of 'Nee'."},
                {"role": "user", "content": f"Het gestelde doel is: {goal_text} en het bericht is: {user_message}"}
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

            completion_time = datetime.now().strftime("%H:%M")
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
        conn.rollback()
        return

# Assistant_response == 'Overig'  
async def handle_unclassified_mention(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    goal_text = goal_text if goal_text else None
    bot_last_response = update.message.reply_to_message.text if update.message.reply_to_message else None
    
    messages = prepare_openai_messages(update, user_message, 'other', goal_text, bot_last_response)
    assistant_response = await send_openai_request(messages, "gpt-4o")
    await update.message.reply_text(assistant_response)
    
async def roll_dice(update, context):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        score = fetch_score(update)
        if score > 1:
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
                try:
                    # subtract 1 point
                    cursor.execute('''
                                   UPDATE users
                                   SET score = score + 5
                                   WHERE user_id = %s AND chat_id = %s
                                   ''', (user_id, chat_id))
                    conn.commit()
                except Exception as e:
                    print(f"Error adding 5 to score in database: {e}")
                    conn.rollback()
                await context.bot.send_message(
                chat_id=update.message.chat_id, 
                text=f"🎉",
                reply_to_message_id=update.message.message_id
            )
                await context.bot.send_message( 
                chat_id=update.message.chat_id, 
                text=f"_+5 punten_", parse_mode="Markdown",
                reply_to_message_id=update.message.message_id
            )
            else:
                try:
                    # subtract 1 point
                    cursor.execute('''
                                   UPDATE users
                                   SET score = score - 1
                                   WHERE user_id = %s AND chat_id = %s
                                   ''', (user_id, chat_id))
                    conn.commit()
                except Exception as e:
                    print(f"Error subtracting 1 point in database: {e}")
                    conn.rollback()
                    
                await context.bot.send_message(
                chat_id=update.message.chat_id, 
                text=f"nope.\n_-1 punt_", parse_mode="Markdown",
                reply_to_message_id=update.message.message_id
            )
        else:
            await context.bot.send_message(
            chat_id=update.message.chat_id, 
            text=f"Je hebt niet genoeg punten om te dobbelen 🧙‍♂️\n_minimaal 2 punten nodig_", parse_mode="Markdown",
            reply_to_message_id=update.message.message_id
        )

    except Exception as e:
        print (f"Error: {e}")       

# nightly reset        
async def reset_goal_status(context_or_application):
    try:
        # Fetch all unique chat IDs from the users table
        cursor.execute("SELECT DISTINCT chat_id FROM users")
        chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]

        # Reset goal status for all users
        cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = ''")
        conn.commit()
        print("Goal status reset at", datetime.now())

        # Update the last reset time
        update_last_reset_time()

        # Send reset message to all active chats
        bot = context_or_application.bot if hasattr(context_or_application, 'bot') else context_or_application # Because application is passed from catchup, and context from job queue
        for chat_id in chat_ids:
            await bot.send_message(chat_id=chat_id, text="_Dagelijkse doelen gereset_  🧙‍♂️", parse_mode="Markdown")

    except Exception as e:
        print(f"Error resetting goal status: {e}")
        conn.rollback()
        
def get_last_reset_time():
    try:
        cursor.execute("SELECT last_reset_time FROM bot_status")
        result = cursor.fetchone()
        if result is None:
            # If no record exists, insert a default value
            now = datetime.now()
            default_time = now.replace(hour=2, minute=1, second=0, microsecond=0)
            cursor.execute("INSERT INTO bot_status (last_reset_time) VALUES (%s)", (default_time,))
            conn.commit()
            return default_time
        return result[0]
    except Exception as e:
        print(f"Error getting last reset time: {e}")
        return None

def update_last_reset_time():
    try:
        current_time = datetime.now()
        cursor.execute("UPDATE bot_status SET last_reset_time = %s", (current_time,))
        conn.commit()
        print(f"last_reset_time updated to {current_time}")
    except Exception as e:
        print(f"Error updating last reset time: {e}")
        conn.rollback()

# Setup function 
async def setup(application):
    print("Setting up job queue")
    try:
        # Schedule the reset job using job_queue
        job_queue = application.job_queue
        reset_time = time(hour=2, minute=0, second=0)
        print(f"Scheduling daily reset for {reset_time}")
        job_queue.run_daily(reset_goal_status, time=reset_time)
        print("Job queue set up successfully")

        # Check if reset is needed on startup
        last_reset = get_last_reset_time()
        now = datetime.now()
        print(f"Last reset time : {last_reset}")
        print(f"Current time    : {now}")
        
        if last_reset is None or (now - last_reset).total_seconds() >= 24 * 60 * 60 + 60: # 2:01 AM
            print("Performing catch-up reset")
            await reset_goal_status(application)

        else:
            print("No catch-up reset needed")

    except Exception as e:
        print(f"Error setting up job queue: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print(traceback.format_exc())        

def main():
    print("Entering main function")
    try:
        # Check if running locally or on Heroku
        if DATABASE_URL == LOCAL_DB_URL:
            print("using local DB")
            # Running locally, use local bot token
            token = os.getenv('LOCAL_TELEGRAM_BOT_TOKEN')
            token = os.getenv('LOCAL_TELEGRAM_BOT_TOKEN').strip()  # Strip any extra spaces or newlines
        else:
            # Running on Heroku, use Heroku bot token
            token = os.getenv('TELEGRAM_BOT_TOKEN')

        if token is None:
            raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")
        # Initialize the bot with the selected token
        print(f"Using token: {token}")
    
        # Create the bot application with ApplicationBuilder
        application = ApplicationBuilder().token(token).build()
        asyncio.get_event_loop().run_until_complete(setup(application))

        
        # Bind the commands to their respective functions
        application.add_handler(CommandHandler(["start", "begroeting", "begin"], start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("filosofie", filosofie_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(CommandHandler(["challenge", "uitdagen"], challenge_command))
        application.add_handler(CommandHandler("boost", boost_command))
        application.add_handler(CommandHandler(["link", "links", "linken"], link_command))
        application.add_handler(CommandHandler("inventaris", inventory_command))
        application.add_handler(CommandHandler(["gift", "cadeautje", "foutje", "geef", "kadootje"], gift_command))
        application.add_handler(CommandHandler(["steal", "steel", "sorry", "oeps"], steal_command))
        application.add_handler(CommandHandler(["revert", "bijna"], revert_goal_completion_command))
        
        wipe_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('wipe', wipe_command)],
            states={
                CONFIRM_WIPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_wipe)],
            },
            fallbacks=[],
            conversation_timeout=30
        )
        application.add_handler(wipe_conv_handler)
    
        # Bind the message analysis to any non-command text messages
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.UpdateType.MESSAGE, analyze_message))
        # Handler for edited messages
        application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND, print_edit))
        
        print("********************* END OF MAIN *********************")
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
