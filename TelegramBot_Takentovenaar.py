from ast import Try
import os
from pickle import TRUE
import random
from tempfile import TemporaryFile
from telegram import User, Update, Bot, ChatMember
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, CallbackContext, filters, ContextTypes
from openai import OpenAI
import asyncio
import re
import json
import psycopg2
import pytz
from datetime import datetime, time, timedelta

global_bot = None

def initialize_bot(token):
    global global_bot
    global_bot = Bot(token)

local_flag = False

# berlin_tz = pytz.timezone('Europe/Berlin')
# berlin_time = datetime.now(berlin_tz)

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
# print(f"__________OpenAI API Key_________:'\n{api_key}\n")



# Use DATABASE_URL if available (Heroku), otherwise fallback to LOCAL_DB_URL
DATABASE_URL = os.getenv('DATABASE_URL', os.getenv('LOCAL_DB_URL'))

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
    desired_columns_users = {
        'user_id': 'BIGINT',
        'chat_id': 'BIGINT',
        'total_goals': 'INTEGER DEFAULT 0',
        'completed_goals': 'INTEGER DEFAULT 0',
        'score': 'INTEGER DEFAULT 0',
        'today_goal_status': "TEXT DEFAULT 'not set'",
        'set_time': 'TIMESTAMP',  
        'today_goal_text': "TEXT DEFAULT ''",
        'pending_challenge': "TEXT DEFAULT '{}'",
        'inventory': "JSONB DEFAULT '{\"boosts\": 2, \"links\": 2, \"challenges\": 2}'",
    }

    # Create the tables if they don't exist
    #1 users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            chat_id BIGINT,
            total_goals INTEGER DEFAULT 0,
            completed_goals INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            today_goal_status TEXT DEFAULT 'not set',
            set_time TIMESTAMP,       
            today_goal_text TEXT DEFAULT '',
            pending_challenge TEXT DEFAULT '{}',
            inventory JSONB DEFAULT '{"boosts": 2, "links": 2, "challenges": 2}',       
            PRIMARY KEY (user_id, chat_id)
        )
    ''')
    conn.commit()
    
    #2 bot table
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

    #3 engagements table
    try:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS engagements (
            id BIGSERIAL PRIMARY KEY,
            engager_id BIGINT NOT NULL,
            engaged_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            special_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending', -- either 'pending' or 'archived'
            UNIQUE (engager_id, engaged_id, special_type, chat_id),
            FOREIGN KEY (engager_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE,
            FOREIGN KEY (engaged_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE
        );

        ''')
        conn.commit()
    except Exception as e:
        print(f"Error creating engagements table: {e}")

    # Add missing columns
    add_missing_columns(cursor, 'users', desired_columns_users)
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
        print("Columns result:\n", columns_result)  # Debugging
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

#10
async def complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type):
    try:
        
        user_id = engager_id
        cursor.execute('''
            INSERT INTO engagements 
            (engager_id, engaged_id, chat_id, special_type, created_at, status)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, 'pending')
            ON CONFLICT (engager_id, engaged_id, special_type, chat_id)
            DO UPDATE SET 
                created_at = CURRENT_TIMESTAMP,
                status = 'pending'
            RETURNING id;
        ''', (engager_id, engaged_id, chat_id, special_type))
        # Update inventory to sutract 1 engagement
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
        return True
    except Exception as e:
        print(f"Error completing engagement: {e}")
        conn.rollback()
        return False


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
    
def fetch_goal_status(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        simplified_goal_status = None
        if result:
            goal_status = result[0]
            if goal_status == 'set':
                print(f"Goal status: {goal_status}")
                return goal_status
            if goal_status == 'not set':
                print(f"Goal status: {goal_status}")
                return ''
            else:
                print(f"goal_status zoals in DB: {goal_status}")
                simplified_goal_status == 'Done'
                return simplified_goal_status
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
    await update.message.reply_text('Hoi! 👋🧙‍♂️\n\nIk ben Taeke Toekema Takentovenaar. Stuur me een berichtje als je wilt, bijvoorbeeld om je dagdoel in te stellen of te voltooien, of me te vragen waarom bananen krom zijn. Gebruik "@" met mijn naam, bijvoorbeeld zo:\n\n"@TakenTovenaar_bot ik wil vandaag 420 gram groenten eten" \n\nDruk op >> /help << voor meer opties.')

# Randomly pick a message
def get_random_philosophical_message():
    philosophical_messages = [
            "Hätte hätte, Fahrradkette",  # Message 1
            "千里之行，始于足下",        
            "Ask, believe, receive ✨",   
            "A few words on looking for things. When you go looking for something specific, "
    "your chances of finding it are very bad. Because, of all the things in the world, "
    "you're only looking for one of them. When you go looking for anything at all, "
    "your chances of finding it are very good. Because, of all the things in the world, "
    "you're sure to find some of them",
            "Je bent wat je eet",
            "If the human brain were so simple that we could understand it, we would be so simple that we couldn't",       
            "Believe in yourself",  
            "Hoge loofbomen, dik in het blad, overhuiven de weg",   
            "It is easy to find a logical and virtuous reason for not doing what you don't want to do",  
            "Our actions are like ships which we may watch set out to sea, and not know when or with what cargo they will return to port",
            "A sufficiently intimate understanding of mistakes is indistinguishable from mastery",
            "He who does not obey himself will be commanded",
            "Elke dag is er wel iets waarvan je zegt: als ik die taak nou eens zou afronden, "  
    "dan zou m'n dag meteen een succes zijn. Maar ik heb er geen zin in. Weet je wat, ik stel het "
    "me als doel in de Telegramgroep, en dan ben ik misschien wat gemotiveerder om het te doen xx 🙃",
            "All evils are due to a lack of Telegram bots",
            "Art should disturb the comfortable, and comfort the disturbed",
            "Genius is one per cent inspiration, ninety-nine per cent perspiration",
            "Don't wait. The time will never be just right",
            "If we all did the things we are capable of doing, we would literally astound ourselves",
            "Reflect on your present blessings, of which every woman has many; not on your past misfortunes, of which all men have some",
            "There's power in looking silly and not caring that you do", # Message 20
            "...",
            "Een goed begin is het halve werk",
            "De tering... naar! Daenerys zet in. \n(raad als eerste het Nederlandse spreekwoord waarvan dit is afgeleid, en win 1 punt)", # Message 23
            "Te laat, noch te vroeg, arriveert (n)ooit de takentovenaar" # Message 24
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
                await update.message.reply_text(f"Mijn grootvader zei altijd:\n✨_{grandpa_quote}_ 🧙‍♂️✨", parse_mode="Markdown")
        else:  
            await update.message.reply_text(f'_{philosophical_message}_', parse_mode="Markdown")
    except Exception as e:
        print(f"Error in filosofie_command: {e}")
 
async def help_command(update, context):
    help_message = (
        '*Dit zijn de beschikbare commando\'s*\n\n'
        '👋 /start - Begroeting\n\n'
        '❓/help - Dit lijstje\n\n'
        '📊 /stats - Je persoonlijke stats\n\n'
        '🤔 /reset - Pas je dagdoel aan\n\n'
        '🗑️ /wipe - Wis je gegevens in deze chat\n\n'
        '💭 /filosofie - Laat je inspireren (door opa)\n\n'
        '🎒 /inventaris - Bekijk of je speciale moves kunt maken'
    )
    await update.message.reply_text(help_message, parse_mode="Markdown")

# Helper function to escape MarkdownV2 special characters
def escape_markdown_v2(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

async def check_use_of_special(update, context, special_type):
    special_type_singular = special_type.rstrip('s')
    if special_type_singular.endswith('e'):
        special_type_verb = special_type_singular + 'n'
    else:
        special_type_verb = special_type_singular + 'en'
    chat_id = update.effective_chat.id
    engager = update.effective_user
    engager_id = engager.id
    engager_name = engager.first_name
    message = update.message
    entities = message.entities

    engaged = None
    engaged_id = None
    engaged_name = None
    
    print(f"Messages:\n{message}\nEntities:\n\n{entities}\n\n")
    
    # Check if the command is a reply to another message or a mention
    # Check if there are mentions in the message
    user_mentioned = False
    if message.entities:
        for entity in message.entities:
            print(f"{entity}\n\n we gaan erin")
            if entity.type == "text_mention":  # Detect if there's a direct user mention
                print(f"this is engaged_name in entity type text_mention: {engaged_name}")
                engaged = entity.user
                engaged_id = engaged.id  # Extract the mentioned user's ID
                engaged_name = await get_first_name(user_id=engaged_id)
                user_mentioned = True
                print(f"Mentioned User ID: {engaged_id}\nMentioned User Name: {engaged_name}")  
            elif entity.type == "mention":  # Detect if there's a mention of a user with a username
                username = message.text[entity.offset:entity.offset + entity.length]  # Extract the username from the message text
                username = username.lstrip('@')
                try:
                    # Use the get_chat method to get the user details
                    user = await global_bot.get_chat(username)  # This will return a Chat object
                    if user.type == "private":  # Ensure it's a user and not a group or channel
                        engaged_id = user.id  # Extract the user ID
                        engaged_name = user.first_name
                        print(f"Fetched User ID: {engaged_id}, Fetched Name: {engaged_name}")
                        user_mentioned = True
                    else:
                        engaged_id = None
                        print(f"The mentioned username is not a user.")
                except Exception as e:
                    print(f"Error fetching user by username {username}: {e}")
                    engaged_id = None
               
                print(f"Username mentioned: {username}, engaged_name is {engaged_name}")
    print(f"\n\n\n\nUser mentioned: {user_mentioned}\n\n\n\n")            
    # In case of no mentions, replies are checked
                
    if user_mentioned == False:
        if update.message.reply_to_message is None:
            await update.message.reply_text(f"🚫 Antwoord op iemands berichtje of gebruik een @-mention om ze te {special_type_verb}! 🧙‍♂️")
            print(f"{special_type_singular} couldn't be used by {engager_name}")
        else:
            engaged = update.message.reply_to_message.from_user
            engaged_id = engaged.id
            engaged_name = engaged.first_name
            print(f"Goed opletten nu, engaged is: {engaged}")
    
    if engaged_name == "TakenTovenaar_bot" or engaged_name == "TestTovenaar_bot":
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
                return False
            elif goal_status.startswith("Done"):
                await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag hun doel al behaald! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
                return False
    except Exception as e:
        print(f"Error selecting goal: {e}")

    # Check if the engager has sufficient inventory to engage
    if await check_special_balance(engager_id, chat_id, special_type) == "not enough":
        await update.message.reply_text(f"🚫 {engager_name}, je hebt niet genoeg {special_type}! 🧙‍♂️")
        return False
    elif await check_special_balance(engager_id, chat_id, special_type) == "no inventory":
        await update.message.reply_text(f"🚫 {engager_name}, je hebt geen inventory?! 🧙‍♂️")
        return False
    elif await check_identical_engagement(engager_id, engaged_id, special_type, chat_id):
        await update.message.reply_text(f"🚫 Je hebt al een {special_type_singular} uitstaan op {engaged_name}! 🧙‍♂️")
        return False
    else:
        print(f"check_use_of_special ...\n>>PASSED>>\n...complete_new_engagement\n")
        if await complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type):   # < < < < < 
                await update.message.reply_text(f"{engager_name} {special_type} {engaged_name}! 🧙‍♂️")
                print(f"\n\n*  *  *  Completing Engagement  *  *  *\n\n{engager_name} {special_type} {engaged_name}\n\n")
        else:
            await update.message.reply_text(f"🚫 Deze persoon staat (nog) niet in de database! 🧙‍♂️ \n(hij/zij moet eerst een doel stellen)")  #77
            print(f"Engagement Failed op de valreep.")

async def check_special_balance(engager_id, chat_id, special_type):
    try:    
        cursor.execute('SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s', (engager_id, chat_id))
        result = cursor.fetchone()
        if result:
            # Since the result is already a dictionary, we don't need json.loads()
            inventory = result[0]
            # Check if the engager has sufficient inventory for the special_type
            if inventory.get(special_type, 0) <= 0:  # Safely get the value, default to 0 if the key doesn't exist
                return "not enough"
            else:
                return True
        else:
            return "no inventory"
            
    except Exception as e:
        print(f'Error checking sufficient inventory: {e}')
        return

# Check if the engager has a pending engage with the same user with the same special type
async def check_identical_engagement(engager_id, engaged_id, special_type, chat_id): 
    try:
        cursor.execute('''
            SELECT * FROM engagements 
            WHERE engager_id = %s AND engaged_id = %s AND special_type = %s AND chat_id = %s AND status = 'pending'
        ''', (engager_id, engaged_id, special_type, chat_id))
        result = cursor.fetchone()
        print(f"{result}")
        if result:
            return True
        else:
            return False  
    except Exception as e:
        print(f'Error checking identical engagement: {e}')
        return     

async def ranking_command(update, context):
    update.message.reply_text("UNDER CONSTRUCTION")
    print("UNDER CONSTRUCTION")
    return


async def stats_command(update, context):
    message = update.message
    user_id = None
    first_name = None 

    # Check if there are mentions in the message
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention":  # Detect if it's a direct user mention
                mentioned_user_id = entity.user.id  # Extract the mentioned user's ID
                print(f"User ID: {mentioned_user_id}")
                user_id = mentioned_user_id
                first_name = entity.user.first_name
        
    # Fallback: If no mentions, use the command caller's ID and name
    if user_id is None:
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name
        
    # Escape first name for MarkdownV2
    escaped_first_name = escape_markdown_v2(first_name)
    
    chat_id = update.effective_chat.id 
    
    # Fetch user stats from the database
    result = None
    try:
        cursor.execute('''
            SELECT total_goals, completed_goals, score, today_goal_status, today_goal_text
            FROM users
            WHERE user_id = %s AND chat_id = %s
        ''', (user_id, chat_id))
    
        result = cursor.fetchone()
        print(f"Result is {result}")
    except Exception as e:
        print(f"Error: {e} couldn't fetch user stats?'")


    if result:
        total_goals, completed_goals, score, today_goal_status, today_goal_text = result
        completion_rate = (completed_goals / total_goals * 100) if total_goals > 0 else 0

        stats_message = f"*Statistieken voor {escaped_first_name}*\n"
        stats_message += f"🏆 Score: {score} punten\n"
        stats_message += f"🎯 Doelentotaal: {total_goals}\n"
        stats_message += f"✅ Voltooid: {escape_markdown_v2(str(completed_goals))} {escape_markdown_v2(f'({completion_rate:.1f}%)')}\n"
        
        # Check for the three possible goal statuses
        if today_goal_status == 'set':
            cursor.execute("SELECT set_time FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
            set_time = cursor.fetchone()
            if set_time:
                set_time = set_time[0]
                formatted_set_time = set_time.strftime("%H:%M")
            if await fetch_engagements(user_id):
                escaped_emoji_string = await fetch_engagements(user_id)
                stats_message += f"📅 Dagdoel: sinds {escape_markdown_v2(formatted_set_time)} {escaped_emoji_string}\n📝 {escape_markdown_v2(today_goal_text)}"
                
                
            else:
                stats_message += f"📅 Dagdoel: ingesteld om {escape_markdown_v2(formatted_set_time)}\n📝 {escape_markdown_v2(today_goal_text)}"
        elif today_goal_status.startswith('Done'):
            completion_time = today_goal_status.split(' ')[3]  # Extracts time from "Done today at H:M"
            stats_message += f"📅 Dagdoel: voltooid om {escape_markdown_v2(completion_time)}\n📝 ||{escape_markdown_v2(today_goal_text)}||"
        else:
            stats_message += '📅 Dagdoel: nog niet ingesteld'
        try:       
            await update.message.reply_text(stats_message, parse_mode="MarkdownV2")
        except AttributeError as e:
            print("die gekke error weer (jaaa)")
    else:
        await update.message.reply_text(
        escape_markdown_v2("Je hebt nog geen statistieken. \nStuur me een berichtje met je dagdoel om te beginnen (gebruik '@') 🧙‍♂️"),
        parse_mode="MarkdownV2"
    )
        
async def fetch_engagements(user_id):
    try:
        # Query to get pending engagements for the user, grouped by special_type
        cursor.execute('''
            SELECT special_type, COUNT(*)
            FROM engagements
            WHERE engager_id = %s AND status = 'pending'
            GROUP BY special_type;
        ''', (user_id,))

        # Fetch all results
        results = cursor.fetchall()
        
        # Initialize counts for each type
        boost_count = 0
        link_count = 0
        challenge_count = 0

        # Map the special types to their respective counts
        for row in results:
            special_type, count = row
            if special_type == 'boosts':
                boost_count = count
            elif special_type == 'links':
                link_count = count
            elif special_type == 'challenges':
                challenge_count = count
        
        # Build the final string of emojis
        engagement_string = '⚡' * boost_count + '🔗' * link_count + '😈' * challenge_count

        # Add parentheses around the emoji string
        if engagement_string:
            engagement_string = f"({engagement_string})"
        else:
            return False

        # Call the escape_markdown_v2() function
        escaped_string = escape_markdown_v2(engagement_string)

        # Return the escaped engagement string
        return escaped_string

    except Exception as e:
        print(f"Error fetching engagements: {e}")
        return "Error fetching engagements."


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
    await check_use_of_special(update, context, special_type="boosts")
    return

    # entities = update.message.entities  # List of entities in the message
    # for entity in entities:
    #     if entity.type == "mention": # @username
    #         username = update.message.text[entity.offset::entity.offset + entity.length]
    #         user = await context.bot.get_chat_member(update.effective_chat.id, username)
    #         engaged_id = user.user.id
    #         await handle_special_command(update, context, 'boosts', engaged_id)
    #         return
    #     elif entity.type == "invalid mention":
    #         await update.message.reply_text("🚫 No valid user mentioned 🧙‍♂️")
    # if update.message.reply_to_message:
    #     engaged = update.message.reply_to_message.from_user
    #     engaged_id = engaged.id
    #     await handle_special_command(update, context, 'boosts', engaged_id)        
    # else: 
    #     await update.message.reply_text("🚫 Antwoord op iemands berichtje of gebruik een @-mention 🧙‍♂️")
    #     return

async def link_command(update, context):
    await check_use_of_special(update, context, 'links')
    return

async def challenge_command(update, context):
    await check_use_of_special(update, context, 'challenges')
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
        if await fetch_goal_status == 'Done':    
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
                SET today_goal_status = 'set', 
                    completed_goals = completed_goals - 1,
                    score = score - 4
                WHERE user_id = %s AND chat_id = %s
            ''', (user_id, chat_id))
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
    user_response = update.message.text.strip()
    
    if user_response == 'JA':
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
    if await is_ben_in_chat(update, context):
        try:
            if update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot:
                print("analyze_message > analyze_bot_reply")
                await analyze_bot_reply(update, context)          
            elif update.message and '@TakenTovenaar_bot' in update.message.text:
                print("analyze_message > analyze_bot_mention")
                await analyze_bot_mention(update, context)           
            else:
                print("analyze_message > analyze_regular_message")
                await analyze_regular_message(update, context)
        except Exception as e:
            await update.message.reply_text("Er ging iets mis in analyze_message(), probeer het later opnieuw.")
            print(f"Error in analyze_message(): {e}")   
    else: 
        await update.message.reply_text("Stiekem ben ik een beetje verlegen. Praat met me in een chat waar Ben bij zit, pas dan voel ik me op mijn gemak 🧙‍♂️")
        await notify_ben(update, context)
        return

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
        return False

# Private message to Ben (test once then delete)
async def notify_ben(update,context):
        USER_ID = 1875436366
        first_name = update.effective_user.first_name
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        message = update.message.text
        notification_message = f"Iemand heeft me achter jouw rug om benaderd, ben je jaloers? 🧙‍♂️\n\nUser: {first_name}, {user_id}\nChat: {chat_id}\nMessage: {message}"
        print(f"! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! \n\n\n\nUnauthorized Access Detected\n\n\n\n! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !\nUser: {first_name}, {user_id}\nChat: {chat_id}\nMessage: {message}")
        await context.bot.send_message(chat_id=USER_ID, text=notification_message)
        
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
    message_id = update.message.message_id
    try:
        # reaction = random_emoji
        # await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)   # Error reacting to message: 'function' object is not iterable
        if random.random() < 0.05:
            if random.random() < 0.75:
                reaction = "👍" 
            else:
                reaction = "💯" 
            await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
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
    elif user_message == "Guess who's back...":
            await context.bot.send_message(chat_id=update.message.chat_id, text="Tovenaartje terug ✨🧙‍♂️", parse_mode="Markdown")        
    elif user_message == 'whoops..!':
        await context.bot.send_message(chat_id=update.message.chat_id, text="*Ik ben voorlopig kapot. Tot later!* 🧙‍♂️", parse_mode="Markdown")

    # Dice-roll
    elif user_message.isdigit() and 1 <= int(user_message) <= 6:
        await roll_dice(update, context)

    # bananen
    elif any(word in user_message.lower() for word in ["bananen", "banaan", "appel", "fruit", "apen", "aap", "ernie", "lekker", "Raven", "Nino"]):
        reaction = "🍌"
        if "krom" in user_message.lower():
            reaction = "👀"
            await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
            return
        else:
            await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
            return
        reaction = "🍌"
        await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)        

    # Nightly reset simulation
    elif user_message == '666':
        if await check_chat_owner(update, context):
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

    elif user_message.lower().startswith('giv') and user_message.endswith('s'):
        if 'boosts' in user_message:
            await giv_specials(update, context, 'boosts')
        if 'links' in user_message:
            await giv_specials(update, context, 'links')
        if 'challenges' in user_message:
            await giv_specials(update, context, 'challenges')            
            return
            
        


async def giv_specials(update, context, special_type):
    try:
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name
        chat_id = update.effective_chat.id
        
        # Dynamically construct the JSON path for the special_type
        path = '{' + special_type + '}'  # JSON path, e.g., '{boosts}'
        
        query = '''
            UPDATE users
            SET inventory = jsonb_set(
                inventory,
                %s,  -- The dynamic path in the JSON structure (passed as a parameter)
                (COALESCE(inventory->>%s, '0')::int + 10)::text::jsonb  -- Update the special_type count
            )
            WHERE user_id = %s AND chat_id = %s
            RETURNING inventory
        '''
        
        # Execute the query with proper parameterization
        cursor.execute(query, (path, special_type, user_id, chat_id))
        
        # Commit the transaction
        conn.commit()

        # Fetch the updated inventory result
        updated_inventory = cursor.fetchone()
        await update.message.reply_text(f"Taeke Takentovenaar deelt uit 🧙‍♂️\n_+10 {special_type} voor {first_name}_", parse_mode = "Markdown")
        return updated_inventory

    except Exception as e:
        print(f"Error updating specials: {e}")
        conn.rollback()
        return None

            

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
                'Staat genoteerd! ✏️ \n_+1 punt_'
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
            result = check_pending_engagement(cursor, user_id, chat_id)
            if result is None:
                await update.message.reply_text("Lekker bezig! ✅ \n_+4 punten_"
                        , parse_mode="Markdown")
                return
            else:
                try:
                    engagement_id, engager_id, special_type = result
                    print(f"Pending engagement found, ID: {engagement_id}, Engager ID: {engager_id}, Special Type: {special_type}")
                        # Define bonus points based on special_type
                    if special_type == 'boosts':
                        engager_bonus, engaged_bonus, emoji = 1, 1, "⚡"
                    elif special_type == 'links':
                        engager_bonus, engaged_bonus, emoji = 1, 1, "🔗"
                        print(f"link unhandled for now")
                    elif special_type == 'challenges':
                        engager_bonus, engaged_bonus, emoji = 0, 1, "😈"
                        print(f"challenge unhandled for now")
                    engaged_id = user_id
                    await resolve_engagement(chat_id, engagement_id, special_type, engaged_id, engager_id, engager_bonus)
                    engaged_total_award = 4 + engaged_bonus
                    engaged_name = update.effective_user.first_name
                    engager_name = await get_first_name(user_id=engager_id)
                    await update.message.reply_text(
                        f"Lekker bezig! ✅ \n_+{engaged_total_award} (4+{engaged_bonus}) punten voor {engaged_name}\n"
                        f"+{engager_bonus} punten voor {engager_name} {emoji}_",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"Error hier in de engagement completion: {e}")
    except Exception as e:
        print(f"Error in goal_completion: {e}")
        conn.rollback()
        return

    
async def get_first_name(user_id=None, username=None):
    try:
        # Fetch the user object from the bot
        user = await global_bot.get_chat(user_id)  # This gets the user object
        return user.first_name  # Return the first name of the user
    except Exception as e:
        print(f"Error fetching user details for user_id {user_id}: {e}")
        return None

    
    
async def resolve_engagement(chat_id, engagement_id, special_type, engaged_id, engager_id, engager_bonus):
    engager_bonus = engager_bonus or 0
    try:
        # archive engagement status
        cursor.execute('''
        UPDATE engagements 
        SET status = 'archived' 
        WHERE id = %s AND chat_id = %s
    ''', (engagement_id, chat_id))
        rows_updated = cursor.rowcount
        if rows_updated == 0:
            print("No engagements records were updated resolving engagement")
        # award points
        cursor.execute('''
        UPDATE users 
        SET score = score + %s 
        WHERE user_id = %s AND chat_id = %s
    ''', (engager_bonus, engager_id, chat_id))
        rows_updated = cursor.rowcount
        if rows_updated == 0:
            print("No users records were updated resolving engagement")
        conn.commit()
        return
    except Exception as e:
        print(f"Error in archiving/awarding (resolve_engagement): {e}")
        conn.rollback()
        raise

                
            

    
def check_pending_engagement(cursor, user_id, chat_id):  
    cursor.execute('''
        SELECT id, engager_id, special_type 
        FROM engagements 
        WHERE engaged_id = %s AND chat_id = %s AND status = 'pending'
    ''', (user_id, chat_id))
    pending_engagement = cursor.fetchone()
    return pending_engagement

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
        
def update_last_reset_time():
    try:
        current_time = datetime.now()
        cursor.execute("UPDATE bot_status SET last_reset_time = %s", (current_time,))
        conn.commit()
        print(f"last_reset_time updated to {current_time}")
    except Exception as e:
        print(f"Error updating last reset time: {e}")
        conn.rollback()

# nightly or catch-up reset        
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
            morning_emojis = ["🌅", "🌄", "🕓"]
            random_morning_emoji = random.choice()
            if random.random() < 0.03:
                    random_morning_emoji = "🧙‍♂️"
            if random.random() < 0.03:
                random_morning_emoji = "🍆" 
            await bot.send_message(chat_id=chat_id, text=f"{random_morning_emoji}")
            await bot.send_message(chat_id=chat_id, text=f"✨{get_random_philosophical_message()}✨")
            await bot.send_message(chat_id=chat_id, text="_Dagelijkse doelen weggetoverd_ 📢🧙‍♂️", parse_mode="Markdown")

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


# Setup function 
async def setup(application):
    try:
        # Schedule the reset job using job_queue
        job_queue = application.job_queue
        reset_time = time(hour=2, minute=0, second=0)
        job_queue.run_daily(reset_goal_status, time=reset_time)
        print(f"Job queue set up successfully at {reset_time}")

        # Check if reset is needed on startup
        last_reset = get_last_reset_time()
        now = datetime.now()
        print(f"\nLast reset time : {last_reset}")
        print(f"Current time    : {now}")

        # Define scheduled reset time (2:00 AM)
        reset_time_today = now.replace(hour=2, minute=0, second=0, microsecond=0)

        # If it's after 2:00 AM today, the fallback checks if last reset was before 2:00 AM today
        if now >= reset_time_today:
            if last_reset is None or last_reset < reset_time_today:
                # Perform the fallback reset
                print("^ Perform catch-up reset ^\n")
            else:
                print("^ No catch-up reset needed ^\n")  
        else:
            # If it's before 2:00 AM today, the fallback checks if last reset was before 2:00 AM yesterday
            reset_time_yesterday = reset_time_today - timedelta(days=1)
            if last_reset is None or last_reset < reset_time_yesterday:
                # Perform the fallback reset
                print("^ Performing catch-up reset ^")
                await reset_goal_status(application)

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
            token = os.getenv('LOCAL_TELEGRAM_BOT_TOKEN')
            token = os.getenv('LOCAL_TELEGRAM_BOT_TOKEN').strip()  # Strip any extra spaces or newlines
            print(f"Using testtovenaar: {token}\n")
        else:
            # Running on Heroku, use Heroku bot token
            token = os.getenv('TELEGRAM_BOT_TOKEN')
            print(f"\nUsing TakenTovenaar: {token}")
            print("Using Heroku Database\n")

        if token is None:
            raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")
        
        token = token.strip()
        initialize_bot(token)
        
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
        application.add_handler(CommandHandler(["boost", 'boosten', "boosting"], boost_command))
        application.add_handler(CommandHandler(["link", "links", "linken"], link_command))
        application.add_handler(CommandHandler(["inventaris", "inventory"], inventory_command))
        application.add_handler(CommandHandler(["gift", "give", "cadeautje", "foutje", "geef", "kadootje", "gefeliciteerd"], gift_command))
        application.add_handler(CommandHandler(["steal", "steel", "sorry", "oeps"], steal_command))
        application.add_handler(CommandHandler(["revert", "neee"], revert_goal_completion_command))
        application.add_handler(CommandHandler(["ranking", "tussenstand"], ranking_command))
        
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
