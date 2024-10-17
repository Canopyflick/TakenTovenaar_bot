from TelegramBot_Takentovenaar import client, notify_ben, conn, cursor, get_first_name, global_bot, is_ben_in_chat, notify_ben
from datetime import datetime
import json, asyncio, re, random

from telegram import Update
from telegram.ext import CallbackContext, ContextTypes


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

# nightly or catch-up reset        
async def reset_goal_status(context_or_application):
    # for the bot being able to send messages
    bot = context_or_application.bot if hasattr(context_or_application, 'bot') else context_or_application # Because application is passed from catchup, and context from job queue
    try:
        # Fetch all unique chat IDs from the users table
        cursor.execute("SELECT DISTINCT chat_id FROM users")
        chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]

        # Reset goal status for all users
        cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = ''")
        conn.commit()
        print("Goal status reset at      :", datetime.now())

        
        # Fetch all engager_ids for live boost engagements (⚡)
        cursor.execute('''
            SELECT DISTINCT engager_id, chat_id
            FROM engagements
            WHERE status = 'live'
        ''')
        live_engagers = cursor.fetchall()

        # Iterate over each live boost engagement
        # Process each engager for each type separately
        for engager_id, chat_id in live_engagers:
            engager_name = await get_first_name(context_or_application, user_id=engager_id)
            escaped_engager_name = escape_markdown_v2(engager_name)
            # Check if the engager has live boosts using fetch_live_engagements
            live_engagements = await fetch_live_engagements(engager_id=engager_id)
            if live_engagements:
                if "⚡" in live_engagements:
                    # If there are live boosts, return them to the engager
                    amount = live_engagements.count("⚡")
                    await add_special(user_id = engager_id, chat_id = chat_id, special_type = 'boosts', amount = amount)
                    amount_plus = f"+{amount}"
                    escaped_amount = escape_markdown_v2(amount_plus)
                    await bot.send_message(chat_id=chat_id, text =f"Boost van {escaped_engager_name} bleef gisteren {amount} maal onverzilverd 🧙‍♂️\n_{escaped_amount}⚡ terug naar [{escaped_engager_name}](tg://user?id={engager_id}_)"
                                   , parse_mode="MarkdownV2")
                pending_engagements = await fetch_live_engagements('pending', engager_id=engager_id)
                if "😈" in pending_engagements:
                    amount = pending_engagements.count("😈")
                    await add_special(user_id = engager_id, chat_id = chat_id, special_type = 'challenges', amount = amount)
                    amount_plus = f"+{amount}"
                    escaped_amount = escape_markdown_v2(amount_plus)
                    await bot.send_message(chat_id=chat_id, text =f"Challenge van {escaped_engager_name} werd gisteren {amount} maal niet geaccepteerd 🧙‍♂️\n_{escaped_amount}😈 terug naar [{escaped_engager_name}](tg://user?id={engager_id})_"
                                   , parse_mode="MarkdownV2")
                
                if "🔗" in live_engagements:
                    amount = live_engagements.count("🔗")
                    # different logic for links
                    await bot.send_message(chat_id=chat_id, text =f"Link van [{escaped_engager_name}](tg://user?id={engager_id}) niet ten tijde van 🧙‍♂️ Gevolgen moet ik nog implementeren 👀"
                                   , parse_mode="MarkdownV2")
                    print("! ! ! live links upon nightly reset\n\nnot yet implemented ! ! !")
                
    except Exception as e:
        print(f"Error resetting goal status: {e}")
        conn.rollback()
        
    try:
        cursor.execute('''
            UPDATE engagements
            SET status = 'archived_unresolved'
            WHERE status = 'live'           
        ''')
        conn.commit()
    except Exception as e:
        print(f"Error archiving engagements: {e}")
        conn.rollback()
        
    # Update the last reset time
    update_last_reset_time()

    # Send reset message to all active chats
        
    for chat_id in chat_ids:
        morning_emojis = ["🌅", "🌄"]
        random_morning_emoji = random.choice(morning_emojis)
        if random.random() < 0.03:
                random_morning_emoji = "🧙‍♂️"
        if random.random() < 0.03:
            random_morning_emoji = "🍆" 
        await bot.send_message(chat_id=chat_id, text=f"{random_morning_emoji}")
        await asyncio.sleep(5)  # To leave space for any live engagement resolve messages 
        await bot.send_message(chat_id=chat_id, text=f"✨_{get_random_philosophical_message()}_✨", parse_mode = "Markdown")
        await bot.send_message(chat_id=chat_id, text="*Dagelijkse doelen weggetoverd* 📢🧙‍♂️", parse_mode = "Markdown")

def update_last_reset_time():
    try:
        current_time = datetime.now()
        cursor.execute("UPDATE bot_status SET last_reset_time = %s", (current_time,))
        conn.commit()
        print(f"last_reset_time updated to: {current_time}")
    except Exception as e:
        print(f"Error updating last reset time: {e}")
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
                engaged_name = await get_first_name(context, user_id=engaged_id)
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
                        print(f"The mentioned username ({username}) is not a user.")
                except Exception as e:
                    print(f"Error fetching user by username {username}: {e}")
                    engaged_id = None
               
                print(f"Username mentioned: {username}, engaged_name is {engaged_name}")
    print(f"\n\nUser mentioned: {user_mentioned}\n\n")            
    # In case of no mentions, replies are checked
                
    if not user_mentioned:
        if update.message.reply_to_message is None:
            await update.message.reply_text(f"🚫 Antwoord op iemands berichtje of gebruik een @-mention om ze te {special_type_verb}! 🧙‍♂️")
            print(f"{special_type_singular} couldn't be used by {engager_name}")
        else:
            engaged = update.message.reply_to_message.from_user
            engaged_id = engaged.id
            engaged_name = engaged.first_name
            print(f"Goed opletten nu, engaged is: {engaged}")
    
    if engaged_name == "TakenTovenaar_bot" or engaged_name == "TestTovenaar_bot":
        await update.message.reply_text(f"🚫 Y O U  SHALL  NOT  P A S S ! 🚫 🧙‍♂️\n_      a {special_type_singular} to me..._", parse_mode = "Markdown")
        print(f"{special_type_singular} couldn't be used by {engager_name}")
        return False
    elif engager_id == engaged_id:
        await update.message.reply_text(f"🚫 BELANGENVERSTRENGELING ! 🚫🧙‍♂️")
        print(f"{special_type_singular} couldn't be used by {engager_name}")
        return False  # Stop further execution if the user is engaging themselves

        # Check if the engager has sufficient inventory to engage
    if await check_special_balance(engager_id, chat_id, special_type) == "not enough":
        await update.message.reply_text(f"🚫 {engager_name}, je hebt niet genoeg {special_type}! 🧙‍♂️")
        return False
    elif await check_special_balance(engager_id, chat_id, special_type) == "no inventory":
        await update.message.reply_text(f"🚫 {engager_name}, je hebt je zaakjes nog niet voor elkaar. Stel anders eerst eventjes een doel in (/start) 🧙‍♂️")
        return False
    elif await check_identical_engagement(engager_id, engaged_id, special_type, chat_id):
        await update.message.reply_text(f"🚫 Je hebt al een {special_type_singular} uitstaan op {engaged_name}! 🧙‍♂️")
        return False

    
    # Check if the engaged already has a goal set today, not done
    try:
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (engaged_id, chat_id))
        result = cursor.fetchone()
        if result is not None:
            goal_status = result[0]
            if goal_status == 'not set' and special_type != "challenges":   # for challenges, unset goals are fine
                await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag nog geen doel ingesteld! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
                return False
            elif goal_status.startswith("Done"):
                await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag hun doel al behaald! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
                return False
    except Exception as e:
        print(f"Error selecting goal: {e}")

    print(f"check_use_of_special ...\n>>PASSED>>\n")
    if special_type == 'challenges':
        # Storing all the variables for challenge_command_2
        context.chat_data['engager_id'] = engager_id
        context.chat_data['engager_name'] = engager_name
        context.chat_data['engaged_id'] = engaged_id
        context.chat_data['engaged_name'] = engaged_name
        context.chat_data['user_mentioned'] = user_mentioned
        print(f"...terug naar challenge_command\n")
        return True     # < < < < < challenges go out here, and will complete_new_engagement separately, once the engaged user accepts 
    print(f"...complete_new_engagement\n")
    if await complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type):   # < < < < < boosts and links go in here
                
            emoji_mapping = {
                'boosts': '⚡',
                'links': '🔗',
                'challenges': '😈'
            }

            # Get the emoji for the given special_type
            special_type_emoji = emoji_mapping.get(special_type, '')
            await update.message.reply_text(f"{special_type_emoji}")
            escaped_engager_name = escape_markdown_v2(engager_name)
            escaped_engaged_name = escape_markdown_v2(engaged_name)
            await context.bot.send_message(chat_id=update.effective_chat.id, text =f"{escaped_engager_name} {special_type} [{escaped_engaged_name}](tg://user?id={engaged_id}) 🧙‍♂️"
                                            , parse_mode="MarkdownV2")
            # await update.message.reply_text(f"{engager_name} {special_type} {engaged_name}! 🧙‍♂️")
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
    

# Check if the engager already has a live or pending engage with the same user with the same special type
async def check_identical_engagement(engager_id, engaged_id, special_type, chat_id): 
    try:
        cursor.execute('''
            SELECT * FROM engagements 
            WHERE engager_id = %s 
            AND engaged_id = %s 
            AND special_type = %s 
            AND chat_id = %s 
            AND status IN ('live', 'pending')
        ''', (engager_id, engaged_id, special_type, chat_id))
        result = cursor.fetchone()
        print(f"\n\nDit is het result van check_identical_engagement: {result}")
        print(f"Data for identical check: engager_id={engager_id}, engaged_id={engaged_id}, chat_id={chat_id}, special_type={special_type}")
        if result:
            return True
        else:
            return False  
    except Exception as e:
        print(f'Error checking identical engagement: {e}')
        return     


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
                {"role": "user", "content": f"Het gestelde doel is: {goal_text} En het bericht is: {user_message}"}
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
            result = check_live_engagement(cursor, user_id, chat_id)
            if result is None:
                await update.message.reply_text("Lekker bezig! ✅ \n_+4 punten_"
                        , parse_mode="Markdown")
                return
            else:
                emojis = await fetch_live_engagements(engaged_id = user_id)
                unescaped_emojis = emojis.replace('\\', '')
                engaged_id = user_id
                engaged_bonus_total, engager_bonuses = await calculate_bonuses(update, engaged_id, chat_id)
                engaged_reward_total = engaged_bonus_total + 4
                print(f"\nengaged_reward_total = engaged_bonus_total + 4 | {engaged_reward_total} = {engaged_bonus_total} +4")
                print(f"\nengager_bonuses = {engager_bonuses}")
                engaged_name = update.effective_user.first_name
                completion_message = f"Lekker bezig! ✅ \n_+{engaged_reward_total} (4+{engaged_bonus_total}{unescaped_emojis}) punten voor {engaged_name}_"
                for engager_id, bonus in engager_bonuses.items():
                    engager_name = await get_first_name(context, engager_id)
                    if bonus >0:
                        completion_message += f"\n_+{bonus} voor {engager_name}_"  # Append each engager's name and bonus
                    
                await update.message.reply_text(completion_message, parse_mode = "Markdown")
    except Exception as e:
        print(f"Error in goal_completion: {e}")
        conn.rollback()
        return

def get_bonus_for_special_type(special_type):
    if special_type == 'boosts':
        return 1, 1, "⚡"
    elif special_type == 'links':
        return 2, 2, "🔗"
    elif special_type == 'challenges':
        return 0, 2, "😈"
    return 0, 0, ""    
    
# Calculate and award the bonuses for both the engager and the engaged upon goal completion of the engaged
async def calculate_bonuses(update, engaged_id, chat_id):
    try:
        # Fetch all engagers for the engaged user and the specific special_type
        cursor.execute('''
            SELECT engager_id, engaged_id, special_type
            FROM engagements
            WHERE engaged_id = %s AND chat_id = %s AND status = 'live'
        ''', (engaged_id, chat_id))
    
        engagements = cursor.fetchall()

        # Track only the bonus reward for the engaged user (regular 4p. reward was already added separately)
        engaged_bonus_total = 0     # 
        # Dictionary to track bonuses for each engager (engager_id: total_bonus)
        engager_bonus_dict = {}

        # Process each engagement type
        for engagement in engagements:
            engager_id, engaged_id, special_type = engagement
            engager_bonus, engaged_bonus, emoji = get_bonus_for_special_type(special_type)
            print(f"Live engagement found, Engager ID: {engager_id}, Special Type: {special_type}{emoji}")
            # Accumulate the bonus for the engager
            if engager_id in engager_bonus_dict:
                engager_bonus_dict[engager_id] += engager_bonus
                print(f"engager bonus: {engager_bonus}")
            else:
                engager_bonus_dict[engager_id] = engager_bonus
                print(f"engager bonus: {engager_bonus}")
                
            # Accumulate the bonus for the engaged user
            engaged_bonus_total += engaged_bonus
            print(f"engaged bonus: {engaged_bonus}")

        # Update the score for each engager
        for engager_id, total_bonus in engager_bonus_dict.items():
            cursor.execute('''
                UPDATE users
                SET score = score + %s
                WHERE user_id = %s AND chat_id = %s;
            ''', (total_bonus, engager_id, chat_id))

        # Update the score for the engaged user with their total bonus
        cursor.execute('''
            UPDATE users
            SET score = score + %s
            WHERE user_id = %s AND chat_id = %s;
        ''', (engaged_bonus_total, engaged_id, chat_id))

        # Archive all the engagements after processing
        cursor.execute('''
            UPDATE engagements
            SET status = 'archived_done'
            WHERE engaged_id = %s AND chat_id = %s AND status = 'live';
        ''', (engaged_id, chat_id))
        
        conn.commit()
        print(f"engaged bonus total: {engaged_bonus_total} | engager bonus dict: {engager_bonus_dict}")
        return engaged_bonus_total, engager_bonus_dict

    except Exception as e:
        print(f"Error processing engagement completion: {e}")
        await update.message.reply_text("Uhhh... 🧙‍♂️\n\n🐛")
        conn.rollback()



    


# resolve engagement aka archive and award engager_bonus (upon goal completion and potentially nightly reset)     
async def resolve_engagement(chat_id, engagement_id, special_type, engaged_id, engager_id, engager_bonus):
    engager_bonus = engager_bonus or 0
    try:
        # archive engagement status
        cursor.execute('''
        UPDATE engagements 
        SET status = 'archived_done' 
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

    
def check_live_engagement(cursor, user_id, chat_id):  
    cursor.execute('''
        SELECT id, engager_id, special_type 
        FROM engagements 
        WHERE engaged_id = %s AND chat_id = %s AND status = 'live'
    ''', (user_id, chat_id))
    live_engagement = cursor.fetchone()
    return live_engagement

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



async def complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type, status='live'):
    try:
        
        user_id = engager_id
        cursor.execute('''
            INSERT INTO engagements 
            (engager_id, engaged_id, chat_id, special_type, created_at, status)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            ON CONFLICT (engager_id, engaged_id, special_type, chat_id)
            DO UPDATE SET 
                created_at = CURRENT_TIMESTAMP,
                status = 'live'
            RETURNING id;
        ''', (engager_id, engaged_id, chat_id, special_type, status))
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
        if not inventory:
            await update.message.reply_text(f"Je hebt nog geen inventory. Begin met het instellen van een doel (/start) 🧙‍♂️", parse_mode="Markdown")
        else:
            # Define a dictionary to map items to their corresponding emojis
            emoji_mapping = {
                "boosts": "⚡",
                "links": "🔗",
                "challenges": "😈"
            }
            inventory_text = "\n".join(
                f"{emoji_mapping.get(item, '')} {item}: {count}"
                for item, count in inventory.items()
            )
            await update.message.reply_text(f"*Moves van {first_name}*\n{inventory_text}", parse_mode="Markdown")
    except Exception as e:
        print(f"Error showing inventory: {e}")
        

def get_inventory(user_id, chat_id):
    try:
        cursor.execute("SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
        result = cursor.fetchone()
        if result is None:
            return None
        else:
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


async def add_special(user_id, chat_id, special_type, amount=1):
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
    
async def fetch_goal_status(update, user_id = None):
    if user_id is None:
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
                simplified_goal_status = 'Done'
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



# Currently handles EITHER engager OR engaged        
async def fetch_live_engagements(status = 'live', engager_id = None, engaged_id = None):
    try:
        results = []
        if engager_id:
            # Query to get live engagements for the user, grouped by special_type
            cursor.execute('''
                SELECT special_type, COUNT(*)
                FROM engagements
                WHERE engager_id = %s AND status = %s
                GROUP BY special_type;
            ''', (engager_id, status))

            # Fetch all results
            results.extend(cursor.fetchall())
        if engaged_id:
            # Query to get live engagements for the user, grouped by special_type
            cursor.execute('''
                SELECT special_type, COUNT(*)
                FROM engagements
                WHERE engaged_id = %s AND status = %s
                GROUP BY special_type;
            ''', (engaged_id, status))

            # Fetch all results
            results.extend(cursor.fetchall())
        
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

        escaped_string = escape_markdown_v2(engagement_string)

        # Return the escaped engagement string
        return escaped_string

    except Exception as e:
        print(f"Error fetching engagements: {e}")
        return False
        
    
    
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
    await handle_regular_message(update, context)
    print("analyze_regular_message > handle_regular_message")
    
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
        await context.bot.send_message(chat_id=update.message.chat_id, text="Database gereset hihi, allemaal ONvoLDoEnDe!\n\nMaar nu werk ik weer 🧙‍♂️", parse_mode="Markdown")
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
            chat_id = update.effective_chat.id
            # Reset goal status
            try:
                cursor.execute("UPDATE users SET today_goal_status = 'not set', today_goal_text = '' WHERE chat_id = %s", (chat_id))
                # Delete all engagements
                cursor.execute('DELETE FROM engagements WHERE chat_id = %s', (chat_id))
                
                conn.commit()
                print(f"666 Goal status reset at", datetime.now())
                await context.bot.send_message(chat_id=update.message.chat_id, text="_SCORE STATUS RESET COMPLETE_  🧙‍♂️", parse_mode="Markdown")
            except Exception as e:
                conn.rollback()  # Rollback the transaction on error
                print(f"Error: {e}")

    elif user_message == '777':
        await reset_to_testing_state(update, context)
                
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

def random_emoji():
    emojis = ['😈', "👍", "🔥", "⚡"]
    return random.choice(emojis)      


def escape_markdown_v2(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))


async def reset_to_testing_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_chat_owner(update, context):
        # Before DELETE, log the current engagements
        chat_id = update.effective_chat.id
        cursor.execute('''
            SELECT COUNT(*) FROM engagements WHERE chat_id = %s
        ''', (chat_id,))
        engagement_count_before = cursor.fetchone()[0]
        print(f"\n***Engagements before reset: {engagement_count_before}\n")
        try:

            # Reset all users to have 1 live goal with specified text
            cursor.execute('''
                UPDATE users
                SET today_goal_status = 'set',
                    today_goal_text = 'Vandaag wil jij broccoli eten',
                    set_time = CURRENT_TIMESTAMP
                WHERE chat_id = %s
            ''', (chat_id,))

            # Delete all engagements
            cursor.execute('DELETE FROM engagements WHERE chat_id = %s', (chat_id,))

            # Commit the changes
            conn.commit()

            print(f"Testing state reached at {datetime.now()}")
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="*RESET TO TESTING STATE* 🧙‍♂️",
                parse_mode="Markdown"
            )
        except Exception as e:
            conn.rollback()  # Rollback the transaction on error
            print(f"Error: {e}")
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"Error occurred while resetting to testing state: {e}"
            )
        # After DELETE, log the engagement count
        cursor.execute('''
            SELECT COUNT(*) FROM engagements WHERE chat_id = %s
        ''', (chat_id,))
        engagement_count_after = cursor.fetchone()[0]
        print(f"\n***Engagements after reset: {engagement_count_after}\n")
        return