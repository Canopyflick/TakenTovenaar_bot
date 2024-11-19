from TelegramBot_Takentovenaar import client, notify_ben, get_first_name, global_bot, is_ben_in_chat, notify_ben, get_database_connection, BERLIN_TZ
from datetime import datetime, timezone
import json, asyncio, re, random, pprint
from telegram import Update, MessageEntity
from telegram.ext import CallbackContext, ContextTypes
from typing import Literal
from pydantic import BaseModel
from handlers.weekly_poll import retrieve_poll_results
from telegram.constants import ChatAction
from telegram.error import TelegramError




# First orchestration: function to analyze any chat message, and check whether it replies to the bot, mentions it, or neither
async def analyze_message(update, context):
    try:
        if await is_ben_in_chat(update, context):
            # Check and add the user to the users table if missing, including first_name  
            chat_type = update.message.chat.type
            if chat_type in ['group', 'supergroup']:  # Only proceed if it's a group chat
                try:
                    user_id = update.message.from_user.id
                    chat_id = update.message.chat_id
                    await update_user_record(context, user_id, chat_id)
                except Exception as e:
                    print(f"Error checking user records in analyze_message: {e}")
            try:          
                if update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot:
                    print("analyze_message > analyze_message_to_bot (Reply)")
                    await analyze_message_to_bot(update, context, type='reply')          
                elif update.message and '@TakenTovenaar_bot' in update.message.text:
                    print("analyze_message > analyze_message_to_bot (Mention)")
                    await analyze_message_to_bot(update, context)           
                else:
                    print("analyze_message > analyze_regular_message")
                    await analyze_regular_message(update, context)
            except Exception as e:
                await update.message.reply_text("Er ging iets mis in analyze_message(), probeer het later opnieuw.")
                print(f"Error in analyze_message(): {e}")   
        else: 
            await update.message.reply_text("Uhh, hoi... Stiekem ben ik een beetje verlegen. Praat met me in een chat waar Ben bij zit, pas dan voel ik me op mijn gemak 🧙‍♂️\n\n\nPS: je kunt hier wel allerhande boodschappen ter feedback achterlaten, dan geef ik die door aan Ben (#privacy). Denk bijvoorbeeld aan feature requests, kwinkslagen, knuffelbedreigingen, valsspeelbiechten, slaapzakberichten etc.\nPPS: Die laatste verzon ChatGPT. En ik quote: 'Een heel lang bericht, waarin je jezelf zou kunnen verliezen alsof je in een slaapzak kruipt.'")
            await notify_ben(update, context)
            return
    except RuntimeError as e:
        # Catch the RuntimeError from is_ben_in_chat and stop further execution
        print(f"Runtime error in analyze_message: {e}")
        return

    
async def update_user_record(context, user_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        # Check if the user already exists in the users table
        cursor.execute("SELECT first_name FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
        result = cursor.fetchone()

        if result is None:
            # User doesn't exist, insert with first_name
            first_name = await get_first_name(context, user_id)
            cursor.execute("""
                INSERT INTO users (user_id, chat_id, first_name)
                VALUES (%s, %s, %s)
            """, (user_id, chat_id, first_name))
            print(f"Inserted new user with user_id: {user_id}, chat_id: {chat_id}, first_name: {first_name}")
        elif result[0] is None:
            # User exists but first_name is missing, update it
            first_name = await get_first_name(context, user_id)
            cursor.execute("""
                UPDATE users
                SET first_name = %s
                WHERE user_id = %s AND chat_id = %s
            """, (first_name, user_id, chat_id))
            print(f"Updated first_name for user_id: {user_id}, chat_id: {chat_id} to {first_name}")
        else: 
            return
        conn.commit()

    except Exception as e:
        print(f"Error updating user record: {e}")
    finally:
        cursor.close()
        conn.close() 
    
    

# nightly or catch-up reset        
async def reset_goal_status(bot, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        # Reset goal status for all users
        cursor.execute(
            "UPDATE users SET today_goal_status = 'not set', today_goal_text = '' WHERE chat_id = %s",
            (chat_id,)
        )
        conn.commit()
        print("Goal status reset at      :", datetime.now(tz=BERLIN_TZ))

        
        # Fetch all engager_ids for live boost engagements (⚡)
        cursor.execute('''
            SELECT DISTINCT engager_id
            FROM engagements
            WHERE status = 'live' AND chat_id = %s
        ''', (chat_id,))
        live_engagers = cursor.fetchall()

        # Iterate over each live boost engagement
        # Process each engager for each type separately
        for engager_id_tuple in live_engagers:
            engager_id = engager_id_tuple[0]
            print('UHHH 🐛🐛🐛🐛🐛🐛🐛🐛🐛🐛')
            print(dir(bot))
            engager_name = await get_first_name(bot, user_id=engager_id)
            escaped_engager_name = escape_markdown_v2(engager_name)
            # Check if the engager has live engagements using fetch_live_engagements
            live_engagements = await fetch_live_engagements(chat_id, engager_id=engager_id)
            if live_engagements:
                if "⚡" in live_engagements:
                    # If there are live boosts, return them to the engager
                    amount = live_engagements.count("⚡")
                    await add_special(user_id = engager_id, chat_id = chat_id, special_type = 'boosts', amount = amount)
                    amount_plus = f"+{amount}"
                    escaped_amount = escape_markdown_v2(amount_plus)
                    await bot.send_message(chat_id=chat_id, text =f"Boost van {escaped_engager_name} bleef gisteren {amount} maal onverzilverd 🧙‍♂️\n_{escaped_amount}⚡ terug naar [{escaped_engager_name}](tg://user?id={engager_id})_", parse_mode="MarkdownV2")
                pending_engagements = await fetch_live_engagements(chat_id, 'pending', engager_id=engager_id)
                if pending_engagements:
                    if "😈" in pending_engagements:
                        amount = pending_engagements.count("😈")
                        await add_special(user_id = engager_id, chat_id = chat_id, special_type = 'challenges', amount = amount)
                        amount_plus = f"+{amount}"
                        escaped_amount = escape_markdown_v2(amount_plus)
                        await bot.send_message(chat_id=chat_id, text =f"Challenge van {escaped_engager_name} werd gisteren {amount} maal niet geaccepteerd 🧙‍♂️\n_{escaped_amount}😈 terug naar [{escaped_engager_name}](tg://user?id={engager_id})_"
                                       , parse_mode="MarkdownV2")
                    amount = 0
                if "🤝" in live_engagements or (pending_engagements is not None and "🤝" in pending_engagements): 
                    amount = live_engagements.count("🤝") + pending_engagements.count("🤝")
                    escaped_amount = escape_markdown_v2(amount)
                    deduction = amount *-1
                    escaped_deduction = escape_markdown_v2(deduction)
                    # different logic for links
                    await bot.send_message(chat_id=chat_id, text =f"Link van [{escaped_engager_name}](tg://user?id={engager_id}) is helaas verbroken 🧙‍♂️\n_{escaped_deduction} punt{'en' if amount != 1 else ''}_"
                                   , parse_mode="MarkdownV2")
                    try:
                        cursor.execute('''
                            UPDATE users
                            SET score = score - %s
                            WHERE user_id = %s AND chat_id = %s           
                        ''', (amount, engager_id, chat_id,))
                        conn.commit()
                    except Exception as e:
                        print(f"Error subtracting points for pending and live links for {chat_id}: {e}")
                        conn.rollback()
                    print("! ! ! live links upon nightly reset\n\nwerken ze ?!???!")
                
    except Exception as e:
        print(f"Error resetting goal for {chat_id} status: {e}")
        conn.rollback()   
    try:
        cursor.execute('''
            UPDATE engagements
            SET status = 'archived_unresolved'
            WHERE status IN ('live', 'pending') AND chat_id = %s           
        ''', (chat_id,))
        conn.commit()
    except Exception as e:
        print(f"Error archiving engagements for {chat_id}: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()    
        
    # Update the last reset time
    update_last_reset_time(chat_id)

    # Send reset message
    morning_emojis = ["🌅", "🌄"]
    random_morning_emoji = random.choice(morning_emojis)
    if random.random() < 0.05:
            random_morning_emoji = "🧙‍♂️"
    if random.random() < 0.02:
        random_morning_emoji = "🍆" 
    await bot.send_message(chat_id=chat_id, text=f"{random_morning_emoji}")
    await bot.send_chat_action(chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(5)  # To leave space for any live engagement resolve messages 
    message = get_random_philosophical_message(prize_only=True)
    await bot.send_message(chat_id=chat_id, text=message, parse_mode = "Markdown")
    await asyncio.sleep(3)
    await bot.send_message(chat_id=chat_id, text="*Dagelijkse doelen weggetoverd* 📢🧙‍♂️", parse_mode = "Markdown")


def update_last_reset_time(chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        current_time = datetime.now(tz=BERLIN_TZ)
        cursor.execute(
            """
            INSERT INTO bot_status (chat_id, last_reset_time) VALUES (%s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET last_reset_time = EXCLUDED.last_reset_time
            """,
            (chat_id, current_time)
        )
        conn.commit()
        print(f"last_reset_time updated for chat {chat_id} to: {current_time}")
    except Exception as e:
        print(f"Error updating last reset time: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close() 

        
def get_last_reset_time(chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT last_reset_time FROM bot_status WHERE chat_id = %s", (chat_id,))
        result = cursor.fetchone()
        if result is None:
            # If no record exists, insert a default value
            now = datetime.now(tz=BERLIN_TZ)
            default_time = now.replace(hour=2, minute=1, second=0, microsecond=0)
            cursor.execute(
                "INSERT INTO bot_status (chat_id, last_reset_time) VALUES (%s, %s)",
                (chat_id, default_time)
            )            
            conn.commit()
            return default_time
        return result[0]
    except Exception as e:
        print(f"Error getting last reset time: {e}")
        return None
    finally:
        cursor.close()
        conn.close() 

async def check_use_of_special(update, context, special_type):
    print(f"Full update object {json.dumps(update.to_dict(), indent=2)}")
    # Log relevant parts of the context object
    print(f"Chat data:\n{context.chat_data}")
    print(f"User data:\n{context.user_data}")
    print(f"Bot data:\n{context.bot_data}")
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
    
    pprint.pprint(update.to_dict())
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
            elif entity.type == MessageEntity.MENTION:  # Detect if there's a mention of a user with a username
                username = message.text[entity.offset + 1:entity.offset + entity.length]
                if username == context.bot.username:  # Check if the bot is mentioned
                    engaged_id = context.bot.id
                    engaged_name = context.bot.username
                    print(f"Bot Mentioned! ID: {engaged_id}, Name: {engaged_name}")
                    user_mentioned = True
                else:
                    try:
                        # Use the get_chat method to get the user details
                        user = await context.bot.get_chat(f'@{username}')  # This will return a Chat object
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
    
    open_challenge = False

    # In case of no mentions, replies are checked
    if not user_mentioned:
        if update.message.reply_to_message is None:
            if special_type == "challenges":
                open_challenge = True
                warning_message = await update.message.reply_text(
                f"⚠️ _Je stuurt een open uitdaging, zonder tag en niet als antwoord op andermans berichtje.\nTrek je uitdaging in als dit niet de bedoeling was_ 🧙‍♂️", 
                parse_mode = "Markdown"
                )
                await asyncio.sleep(1)
                # Schedule the deletion of the message as a background task
                asyncio.create_task(delete_message(context, chat_id, warning_message.message_id))
            else:    
                await update.message.reply_text(f"🚫 Antwoord op iemands berichtje of gebruik een @-mention om ze te {special_type_verb}! 🧙‍♂️")
                print(f"{special_type_singular} couldn't be used by {engager_name}")
                return False
        else:
            engaged = update.message.reply_to_message.from_user
            engaged_id = engaged.id
            engaged_name = engaged.first_name
    
    if engaged_name == "TakenTovenaar_bot" or engaged_name == "testtovenaar_bot":
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
        await update.message.reply_text(f"🚫 {engager_name}, je hebt je zaakjes nog niet voor mekaar. Of beter gezegd: je loopt op je zaakjes vooruit (...allicht beter dan 'je zaakje achterna'..?).\n\nLang verhaal kort: stel eerst eventjes een doel in (/start) 🧙‍♂️")
        return False
    elif await check_identical_engagement(engager_id, engaged_id, special_type, chat_id):
        await update.message.reply_text(f"🚫 Je hebt al een {special_type_singular} uitstaan op {engaged_name}! 🧙‍♂️")
        return False

    
    # Check if the engaged already has a goal set today, not done
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 
        
    print(f"check_use_of_special ...\n>>PASSED>>\n")
    if special_type == 'challenges':
        # Storing all the variables for challenge_command_2
        context.chat_data['engager_id'] = engager_id
        context.chat_data['engager_name'] = engager_name
        # Storing a different engaged_id in case of an open challenge
        if open_challenge:
            context.chat_data['engaged_id'] = None  
        if not open_challenge:
            context.chat_data['engaged_id'] = engaged_id
            live_engagements = await fetch_live_engagements(chat_id, engaged_id=engaged_id)
            if live_engagements:
                if "😈" in live_engagements:
                    await update.message.reply_text(f"🚫 {engaged_name} heeft vandaag al een andere uitdaging geaccepteerd 🧙‍♂️") #88
                    return False
        context.chat_data['engaged_name'] = engaged_name
        context.chat_data['user_mentioned'] = user_mentioned
        print(f"...terug naar challenge_command\n")
        return True     # < < < < < challenges go out here, and will complete_new_engagement separately, once the engaged user accepts 
    print(f"...complete_new_engagement\n")
    if await complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type):    # < < < < < boosts and links go in here      
        emoji_mapping = {
            'boosts': '⚡',
            'challenges': '😈',
            'links': '🤝'
        }

        # Get the emoji for the given special_type
        special_type_emoji = emoji_mapping.get(special_type, '')
        await update.message.reply_text(f"{special_type_emoji}")
        escaped_engager_name = escape_markdown_v2(engager_name)
        escaped_engaged_name = escape_markdown_v2(engaged_name)
        await context.bot.send_message(chat_id=chat_id, text =f"{escaped_engager_name} {special_type} [{escaped_engaged_name}](tg://user?id={engaged_id}) 🧙‍♂️"
                                        , parse_mode="MarkdownV2")
        # if special_type == 'links':
        #     await context.bot.send_message(chat_id=chat_id, text =f"_Beide winnen 2 bonuspunten als beide hun doel halen. Zo niet, dan verliest {escaped_engager_name} 1 punt_", parse_mode = "Markdown")
        
        print(f"\n\n*  *  *  Completing Engagement  *  *  *\n\n{engager_name} {special_type} {engaged_name}\n\n")
    else:
        await update.message.reply_text(f"🚫 Uhhh.. staat deze persoon misschien nog niet in de database? 🧙‍♂️ \n_(hij/zij moet in dat geval eerst even wat zeggen in deze chat)_", parse_mode="Markdown")  #77
        print(f"Engagement Failed op de valreep.")
  
        
async def delete_message(context, chat_id, message_id, delay=9):
    await asyncio.sleep(delay)
    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    
    
async def check_special_balance(engager_id, chat_id, special_type):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 
        

# Check if the engager already has a live or pending engage with the same user with the same special type
async def check_identical_engagement(engager_id, engaged_id, special_type, chat_id): 
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 
        

async def fetch_weekly_goals_left(chat_id, user_id):
    try:
        conn = get_database_connection() 
        cursor = conn.cursor()
        cursor.execute('''
            SELECT weekly_goals_left FROM users
            WHERE user_id = %s 
            AND chat_id = %s
        ''', (user_id, chat_id,))
        result = cursor.fetchone()
        weekly_goals_left = result[0]
        conn.commit()
        return weekly_goals_left
    except Exception as e:
        print (f"Error fetching weekly goal: {e}")
    finally:
        cursor.close()
        conn.close()


# Assistant_response == 'Doelstelling'          
async def handle_goal_setting(update, user_id, chat_id):
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
        conn = get_database_connection()
        cursor = conn.cursor()
        set_time = datetime.now(tz=BERLIN_TZ)
        cursor.execute('''
                        UPDATE users 
                        SET today_goal_status = 'set',
                        set_time = %s,
                        total_goals = total_goals + 1,
                        score = score + 1,
                        weekly_goals_left = weekly_goals_left - 1
                        WHERE user_id = %s AND chat_id = %s
                        ''', (set_time, user_id, chat_id))
        conn.commit()
    except Exception as e:
        await update.message.reply_text("Doelstatusprobleempje. Probeer het later opnieuw.")
        print(f"Error updating today_goal_status: {e}")
        conn.rollback()
        return
    finally:
        cursor.close()
        conn.close() 
            
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
                {"role": "system", "content": "Controleer of een bericht zou kunnen rapporteren over het succesvol uitvoeren van een gesteld doel. Zijn doel en bericht compatibel met elkaar? Het bericht 'Klaar' is bijvoorbeeld compatibel met (bijna) ieder doel. Maar het bericht 'De bananen zijn gepeld' is niet compatibel met het doel 'Ik wil vandaag m'n huiswerk maken', maar wel met het doel 'Ik wil vandaag bananen pellen'. Antwoord alleen met 'Ja' of 'Nee'."},
                {"role": "user", "content": f"Het gestelde doel is: {goal_text} En het bericht is: {user_message}"}
            ]
    assistant_response = await send_openai_request(messages, temperature=0.1)
    print(f"check_goal_compatibility: {messages}\n\n\nUitkomst check: {assistant_response}")
    return assistant_response

# Assistant_response == 'Klaar'             
async def handle_goal_completion(update, context, user_id, chat_id, goal_text, from_button=False, first_name=None):
    # Rephrase the goal in past tense
    user_message = None
    if not from_button:
        user_message = update.message.text
    
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        assistant_response = 'Ja'
        # ie: if not from_button (reminder completion button)
        if user_message:
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

            completion_time = datetime.now(tz=BERLIN_TZ).strftime("%H:%M")
            # Update user's goal status and statistics

            cursor.execute('''
                UPDATE users 
                SET today_goal_status = %s, 
                    completed_goals = completed_goals + 1,
                    score = score + 4
                WHERE user_id = %s AND chat_id = %s
            ''', (f"Done today at {completion_time}", user_id, chat_id))
            conn.commit()
            engaged_emojis = await fetch_live_engagements(chat_id, engaged_id=user_id)
            if engaged_emojis is None:
                if from_button:
                    await context.bot.send_message(
                    chat_id=chat_id, 
                    text=f"Lekker bezig, {first_name}! ✅ \n_+4 punten_", parse_mode="Markdown")
                if not from_button:
                    await update.message.reply_text("Lekker bezig! ✅ \n_+4 punten_"
                        , parse_mode="Markdown")
                # record non-challenge goal
                try:
                    await record_goal(user_id, chat_id, goal_text)
                except Exception as e:
                    print(f"Error recording goal: {e}")
                # Lastly, transition any pending links to live - for completions without any existing live engagements
                await advance_links_status(cursor, conn, chat_id, user_id)
                return
            
            elif "😈" in engaged_emojis:
                try:
                    cursor.execute('''
                        SELECT engager_id 
                        FROM engagements 
                        WHERE engaged_id = %s 
                        AND status = 'live' 
                        AND chat_id = %s
                        AND special_type = 'challenges'           
                    ''', (user_id, chat_id))
                    result = cursor.fetchone()
                    engager_id = result[0] if result else None
                    # record goal if challenge
                    await record_goal(user_id, chat_id, goal_text, engager_id, is_challenge=True)
                except Exception as e:
                    print(f"Error recording goal: {e}")   
            unescaped_emojis = engaged_emojis.replace('\\(', '').replace('\\)', '')
            engaged_id = user_id
            engaged_bonus_total, engager_bonuses = await calculate_bonuses(update, engaged_id, chat_id)
            engaged_reward_total = engaged_bonus_total + 4
            print(f"\nengaged_reward_total = engaged_bonus_total + 4 | {engaged_reward_total} = {engaged_bonus_total} +4")
            print(f"\nengager_bonuses = {engager_bonuses}")
            engaged_name = update.effective_user.first_name
            completion_message = f"Lekker bezig! ✅ \n_+{engaged_reward_total} (4 + {engaged_bonus_total}{unescaped_emojis}) punten voor {engaged_name}_"
            for engager_id, bonus in engager_bonuses.items():
                engager_name = await get_first_name(context, engager_id)
                if bonus >0:
                    completion_message += f"\n_+{bonus} voor {engager_name}_"  # Append each engager's name and bonus
            if from_button:
                    await context.bot.send_message(chat_id, text=completion_message, parse_mode="Markdown")
            if not from_button:
                await update.message.reply_text(completion_message, parse_mode = "Markdown")
            # Lastly, transition any pending links to live - for completions with existing live engagements
            await advance_links_status(cursor, conn, chat_id, user_id)

    except Exception as e:
        print(f"Error in goal_completion: {e}")
        conn.rollback()
        return
    finally:
        cursor.close()
        conn.close() 
        

async def record_goal(user_id, chat_id, goal_text, engager_id=None, is_challenge=False):
    # Rephrase the goal for recording
    rephrased_goal = goal_text  # In case rephrasing fails, just use original goal_text
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
        messages=[
            {"role": "system", "content": "Herformuleer prestaties naar compacte derde persoon enkelvoud, verleden tijd.'"},
            {"role": "user", "content": "Vandaag had jij voor 11 uur het huis opgeruimd."},
            {"role": "assistant", "content": "ruimde voor 11 uur het huis op."},
            {"role": "user", "content": "Je gaf Anne-Cathrine vandaag een massage."},
            {"role": "assistant", "content": "gaf Anne-Cathrine een massage."},
            {"role": "user", "content": goal_text}
        ] 
        rephrased_goal = await send_openai_request(messages, temperature=0.1)  # Extract the rephrased goal from OpenAI response
        print(f"*Rephrased goal for recording prompt: \n\n{messages}\n\nRephrased goal: {rephrased_goal}\n\n")
    except Exception as e:
        print(f"Error rephrasing goal_text for recording: {e}")
        
    # Record the completed goal in history
    cursor.execute('''
        INSERT INTO goal_history 
            (user_id, chat_id, goal_text, goal_type, challenge_from)
        VALUES 
            (%s, %s, %s, %s, %s)
    ''', (
        user_id, 
        chat_id, 
        rephrased_goal,
        'challenges' if is_challenge else 'personal',
        engager_id
    ))
    conn.commit()
    cursor.close()
    conn.close() 
    print(f"\n📚 DOEL OPGESLAGEN VOOR HET NAGESLACHT\n")



async def advance_links_status(cursor, conn, chat_id, user_id):
    try:
        pending_emojis_1 = await fetch_live_engagements(chat_id, 'pending', engaged_id = user_id)
        print(f"pending_emojis_1: {pending_emojis_1}")
        if pending_emojis_1 is not None and "🤝" in pending_emojis_1:
            # update link status from pending to live
            cursor.execute('''
                UPDATE engagements
                SET status = 'live'
                WHERE engaged_id = %s AND chat_id = %s AND status = 'pending' AND special_type = 'links';
            ''', (user_id, chat_id)) 
            conn.commit()
            print(f"🚨Now transitioning a link to live status because engaged finished their goal first")
        pending_emojis_2 = await fetch_live_engagements(chat_id, 'pending', engager_id = user_id)
        print(f"pending_emojis_2: {pending_emojis_2}")
        if pending_emojis_2 is not None and "🤝" in pending_emojis_2:
            # update link status from pending to live
            cursor.execute('''
                UPDATE engagements
                SET status = 'live'
                WHERE engager_id = %s AND chat_id = %s AND status = 'pending' AND special_type = 'links';
            ''', (user_id, chat_id)) 
            conn.commit()
            print(f"🚨Now transitioning a link to live status because engager finished their goal first")
    except Exception as e:
        print(f"Error transitioning pending link to live: {e}")
    


def get_bonus_for_special_type(special_type):
    if special_type == 'boosts':
        return 1, 1, "⚡"
    elif special_type == 'links':
        return 2, 2, "🤝"
    elif special_type == 'challenges':
        return 0, 2, "😈"
    return 0, 0, ""    
    
# Calculate and award the bonuses for both the engager and the engaged upon goal completion of the engaged
async def calculate_bonuses(update, engaged_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        # Fetch all engagers for the engaged user and the specific special_type
        cursor.execute('''
            SELECT engager_id, engaged_id, special_type
            FROM engagements
            WHERE engaged_id = %s AND chat_id = %s AND status = 'live'
        ''', (engaged_id, chat_id))
        engagements = cursor.fetchall()
        print(f"🤝😈⚡Engaged ID: {engaged_id}, Chat ID: {chat_id}, Engagements: {engagements}")
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
        return 0
    finally:
        cursor.close()
        conn.close() 


    


# resolve engagement aka archive and award engager_bonus (upon goal completion and potentially nightly reset)     
async def resolve_engagement(chat_id, engagement_id, special_type, engaged_id, engager_id, engager_bonus):
    engager_bonus = engager_bonus or 0
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 
    
def check_live_engagement(cursor, user_id, chat_id, special_type=None):
    # Base query
    query = '''
        SELECT id, engager_id, special_type 
        FROM engagements 
        WHERE engaged_id = %s AND chat_id = %s AND status = 'live'
    '''
    
    # Parameters for the query
    params = [user_id, chat_id]
    
    # If special_type is provided, add it to the query
    if special_type is not None:
        query += " AND special_type = %s"
        params.append(special_type)
    
    # Execute the query with the appropriate parameters
    cursor.execute(query, params)
    
    # Fetch and return the result
    live_engagement = cursor.fetchone()
    return live_engagement


# Assistant_response == 'Meta'  (goal_text contains empty string if the user doesn't have one)
async def handle_meta_remark(update, context, user_id, chat_id, goal_text, bot_last_response=None):
    print("👀👀👀 Inside handle_meta_remark")
    # user_has_goal == True
    # if not goal_text:
    #     user_has_goal = False
    user_message = update.message.text
    if bot_last_response:
        bot_last_response_text = bot_last_response.text if bot_last_response.text else "geen tekst"
        if "uitreiking door Ben" in bot_last_response_text:
            print(f"A blocked meta-reponse to:\n {bot_last_response_text}")
            return
    messages = await prepare_openai_messages(update, user_message, 'meta', goal_text, bot_last_response)
    assistant_response = await send_openai_request(messages, 'gpt-4o')
    await update.message.reply_text(assistant_response)
    


# Assistant_response == 'Overig'  
async def handle_unclassified_mention(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_message = update.message.text
    goal_text = fetch_goal_text(update)
    goal_text = goal_text if goal_text else None
    bot_last_response = update.message.reply_to_message if update.message.reply_to_message else None
    # Make sure Taeke doesn't respond if user responds to a prize message
    if bot_last_response:
        bot_last_response_text = bot_last_response.text if bot_last_response.text else "geen tekst"
        if "uitreiking door Ben" in bot_last_response_text:
            print(f"A blocked other-reponse to:\n {bot_last_response_text}")
            return
    messages = await prepare_openai_messages(update, user_message, 'other', goal_text, bot_last_response)
    assistant_response = await send_openai_request(messages, "gpt-4o")
    await update.message.reply_text(assistant_response)
    
async def roll_dice(update, context):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 


async def complete_new_engagement(update, engager_id, engaged_id, chat_id, special_type, status='live'):
    if special_type == "links":
        status = "pending"
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        user_id = engager_id
        cursor.execute('''
            INSERT INTO engagements 
            (engager_id, engaged_id, chat_id, special_type, created_at, status)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
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

        # Convert the error to a string and check for specific text
        error_message = str(e)
        if "unique constraint" in error_message:
            await update.message.reply_text(
                "🚫 Er staat waarschijnlijk ten onrechte al/nog een zelfde actie op deze persoon uit 🐛🧙‍♂️\n(moet Ben fiksen)"
            )
        return False

    finally: 
        cursor.close()
        conn.close()
    

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
                "challenges": "😈",
                "links": "🤝"
            }

            # Ensure the order is maintained
            ordered_items = ['boosts', 'challenges', 'links']
            inventory_text = "\n".join(
                f"{emoji_mapping.get(item, '')} {item}: {inventory.get(item, 0)}"
                for item in ordered_items if item in inventory
            )    
            await update.message.reply_text(f"*Acties van {first_name}*\n{inventory_text}", parse_mode="Markdown")
    except Exception as e:
        print(f"Error showing inventory: {e}")
        

def get_inventory(user_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT inventory FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
        result = cursor.fetchone()
        if result is None:
            return None
        else:
            return result[0]
    except Exception as e:
        print(f"Error getting inventory: {e}")
    finally:
        cursor.close()
        conn.close() 

        
# Function to check if the engager has sufficient inventory to engage
async def check_special_inventory(update, context, engager_id, chat_id, special_type):
    engager_name = update.effective_user.first_name
    conn = get_database_connection()
    cursor = conn.cursor()
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
    cursor.close()
    conn.close() 
    return True  # The engager has sufficient inventory


async def add_special(user_id, chat_id, special_type, amount=1):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
        print(f"Added {amount} {special_type} for {user_id}")
        conn.commit()
    except Exception as e:
        print(f"Error add_special: {e}")
        return
    finally:
        cursor.close()
        conn.close() 


def fetch_goal_text(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 


async def fetch_goal_status(update, user_id = None):
    if user_id is None:
        user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 


def fetch_score(update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT score FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        if result is not None:
            return result[0]  # Extracting the score from the tuple
        else:
            return 0  # Return a default value if the score is not found
    except Exception as e:
        print(f"Error fetching score: {e}")
        return 0  # Return a default value in case of error
    finally:
        cursor.close()
        conn.close() 

async def prepare_openai_messages(update, user_message, message_type, goal_text=None, bot_last_response=None):
    # Define system messages based on the message_type
    first_name = update.effective_user.first_name
    if message_type == 'classification':
        print("system prompt: classification message")
        system_message = system_message = """
        # Opdracht
        Jij bent @TakenTovenaar_bot in een telegramgroep over het samen stellen en behalen van doelen. 
        Je classificeert een berichtje van een gebruiker in een van de volgende vier categorieën: 
        Doelstelling, Klaar, Meta of Overig.

        ## Doelstelling
        Elk bericht waaruit blijkt dat de gebruiker van plan is om iets (specifieks) te gaan doen vandaag.

        ## Klaar
        Als de gebruiker rapporteert dat hun ingestelde doel met succes is afgerond, zoals: 'klaar!', 
        'Gelukt!'. Een eventueel ingesteld doel is ook voorhanden: beoordeel dus of het ingestelde dagdoel compatibel is met hun bericht.
        Als hun doel bijvoorbeeld was om de afwas te doen, dan moet je 'het afdruiprekje is vol' als 'Klaar' classificeren. Maar als hun doel was om 10km te hardlopen, dan niet.
        Let op: het is NIET jouw taak om te beoordelen of de gebruiker z'n doel af heeft of niet. Kijk alleen wat ze hier zelf over zeggen.

        ## Meta
        Als de gebruiker een vraag stelt over jou zelf als bot of over hoe dingen werken in de chatgroep. Voorbeelden van meta-vragen: 
        'Wie heeft de meeste punten?', 'Waarom weet jij niks over Fitrie's doel?', of 'Wanneer krijgen we weer nieuwe boosts/links/challenges?'.

        ## Overig
        Alle gevallen die niet duidelijk onder bovenstaande drie groepen vallen, zijn 'Overig'. Voorbeelden van overig: 
        'Wat vind jij van kapucijners?', of 'Ik heb heel veel zin in m'n avondeten.', of: 'Wie was de laatste president van Amerika?' 

        # Antwoord
        Antwoord door eerst een beredenering te geven, die de voorhanden zijnde gegevens logisch op een rijtje zet. Daarna komt je classificatie: 'Doelstelling', 'Klaar', 'Meta', of 'Overig'.
        """

    elif message_type == 'other':
        print("system prompt: other message")
        if any(word in user_message.lower() for word in ['bananen', 'banaan']):
            print(f"We zijn nu in other-bananenland!\n ({user_message})")
            system_message = """
            Jij bent @TakenTovenaar_bot, en je kunt niet ophouden met praten over BANANEN!
            Je houdt in het bijzonder van hun NEGATIEVE GEOTROPIE! Houd het wel kort, maximaal 4 zinnen. (Je bananenobsessie komt trouwens doordat je als kind ooit in een bananenconcentraatsmoothie bent gevallen).
            """
        else:
            system_message = (
                "Jij bent @TakenTovenaar_bot, de enige bot in een accountability-Telegramgroep van vrienden. "
                "Gedraag je **cheeky**, **vaderlijk**, **mysterieus** en **wijs**. Maar streef bovenal naar waarheid, als de gebruiker een feitelijke vraag heeft. "
                "Als de gebruiker een metavraag of -verzoek heeft over bijvoorbeeld een doel stellen in de appgroep, "
                "antwoord dan alleen dat ze het command /help kunnen gebruiken. "
                "Er zijn meer commando's, maar die ken jij allemaal niet. "
                "Je hebt nu alleen toegang tot dit bericht, niet tot volgende of vorige berichtjes. "
                "Een back-and-forth met de gebruiker is dus niet mogelijk."
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
    elif message_type == 'meta':
        print("system prompt: meta message")
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        group_name = update.effective_chat.title
        meta_data = await collect_meta_data(user_id, chat_id)
        print(f"\n\n⏺️⏺️⏺️Dit is de metadata in de prompt-variabele: {meta_data}\n")
        system_message = f"""
        # Opdracht

        Jij bent @TakenTovenaar_bot in een Telegram-appgroep over het samen stellen en behalen van doelen. Je krijgt nu een berichtje van een gebruiker in de groep, die een meta-vraag heeft. De vraag gaat ofwel over jou als TakenTovenaar, ofwel over de mogelijkheden in de groep en hoe de dingen die jij als bot doet en kan allemaal werken, ofwel over de eigen voortgang van de gebruiker op dit moment. Zie de bijgeleverde Documentatie en Gebruikersgegevens, en gebruik deze om een relevante reactie te geven op de vraag van de gebruiker.


        # Documentatie

        Hier volgt alle informatie over jou als bot en je taken en functionaliteiten binnen de appgroep. 

        ## Achtergrondinformatie

        Je bent actief in een appgroep genaamd {group_name}.  De leden zijn vrienden van elkaar. Een van de leden is je oprichter: Ben, hij heeft jou in Python geprogrammeerd. Ben woont in Leipzig, maar komt uit Leiden. Het doel van deze groep is om elkaar te motiveren doelen te halen, via gamification, accountability  en gezonde onderlinge competitie. Leden kunnen elke week op 4 dagen naar keuze een dagdoel instellen, steeds maar 1 per dag. De leden rapporteren bij jou wanneer ze hun dagdoel instellen of hun dagdoel gehaald hebben, door op een berichtje van jou te reageren of @TakenTovenaar_bot te taggen. Maar momenteel hebben ze je dus een metavraag gesteld. Verder kunnen de groepsleden acties/engagements gebruiken op elkaar, van 3 verschillende special_types: boosts (⚡), challenges (😈), en links (🤝).


        ## Jouw persona en taken

        @TakenTovenaar_bot is cheeky, mysterieus, en bovenal wijs. Heel af en toe reageer je ook verward en slaperig: namelijk willekeurig (via random.random()) op 1% van de berichtjes die leden in de groep sturen waarbij ze jou NIET getagd hebben. Momenteel hebben ze dat juist wel gedaan, met een metavraag dus, en is je doel vooral om ze zo beknopt mogelijk te assisteren bij hun vraag.

        ## Beschikbare commands

        Naast het stellen van open vragen, kunnen de leden ook verschillende /commands sturen. Hier volgen alle opties.

        ### /start command
        async def start_command(update, context):
                await update.message.reply_text('Hoi! 👋🧙‍♂️\n\nIk ben Taeke Toekema Takentovenaar. Stuur mij berichtjes, bijvoorbeeld om je dagdoel in te stellen of te voltooien, of me te vragen waarom bananen krom zijn. Antwoord op mijn berichten of tag me, bijvoorbeeld zo:\n\n"@TakenTovenaar_bot ik wil vandaag 420 gram groenten eten" \n\nDruk op >> /help << voor meer opties.')

        ### /help command
         help_message = (
             '*Dit zijn de beschikbare commando\'s*\n'
             '👋 /start - Uitleg om te beginnen\n'
             '❓/help - Dit lijstje\n'
             '📊 /stats - Je persoonlijke stats\n'
             '🤔 /reset - Pas je dagdoel aan\n'
             '🗑️ /wipe - Wis je gegevens in deze chat\n'
             '🎒 /inventaris - Bekijk je acties\n'
             '🏹 /acties - Uitleg over acties\n'
             '🏹 /fittie - Uitleg over acties\n'
             '💭 /filosofie - Laat je inspireren'
         )

        ### /stats command
        Statistieken van <user>🔸
        🏆 Score: <score> punten
        🎯 Doelentotaal: <total_goals>
        ✅ Voltooid: <completed_goals> (<completion_rate:.1f>)
        📅 Dagdoel: ingesteld om <formatted_set_time> <emoji_string_of_pending_engagements> /OF: Dagdoel: nog niet ingesteld /OF: Dagdoel: voltooid om <completion_time>:
        📝 <today_goal_text> (IF they've set a goal today)

        ### /reset command
        Resets the user's goal status and clears today's goal text (point subtraction is net 0 impact, assuming they will set a new goal, gaining the point back)
        cursor.execute('''
                       UPDATE users
                       SET today_goal_status = 'not set',
                       set_time = NULL,
                       score = score - 1,
                       today_goal_text = '',
                       total_goals = total_goals - 1
                       WHERE user_id = %s AND chat_id = %s
                       ''', (user_id, chat_id))

        ### /wipe command
        De gebruiker kan niet per ongeluk met één klik z'n voortgang wissen. Na het wipe-command volgt eerst een vraag om bevestiging.
            if user_response == 'JA':  
                conn = get_database_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute('DELETE FROM engagements WHERE engager_id = %s AND chat_id = %s', (user_id, chat_id))
                    cursor.execute('DELETE FROM engagements WHERE engaged_id = %s AND chat_id = %s', (user_id, chat_id))
                    cursor.execute('DELETE FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))

        ### /inventory command
                   Define a dictionary to map items to their corresponding emojis
                   emoji_mapping = dict.:
                       "boosts": "⚡",
                       "challenges": "😈",
                       "links": "🤝"
                   
                   inventory_text = "\n".join(
                       f"emoji_mapping to get the item and corresponding count"
                       for item, count in inventory.items()
                   )
                   await update.message.reply_text(f"*Acties van <first_name>*\n<inventory_text>", parse_mode="Markdown")

        ### /acties command
        acties_message = (
            '*Alle beschikbare acties* 🧙‍♂️\n'
            '⚡ */boost* - Boost andermans doel, verhoog de inzet!\n\n'
            '😈 */challenge* - Daag iemand uit om iets specifieks te doen."\n\n'
            '🤝 */link* - Verbind je lot met een ander... \n\n'
            '*Zo zet je ze in*\n'
            'Gebruik je actie door met het passende commando op een berichtje van je doelwit '
            'te reageren, of hen na het commando te taggen:\n\n'
            '- _/boost @Willem_\n\n'
            '- _/challenge @Josefietje om voor 11u weer thuis te zijn voor de koffie_\n\n'
            'Druk op >> /details << voor meer info over acties.\n\n'
            
        ### /fittie
        De gebruiker kan bezwaar maken over iets waar ze het niet mee eens zijn, door /fittie te reageren op een relevant chatberichtje, en/of na /fittie hun case te brengen. Bijvoorbeeld: /fittie ik geloof niet dat Pietje z'n taak echt gedaan heeft.
        Hierna maakt TakenTovenaar_bot een poll en een openingsstatement, waar de leden democratisch kunnen stemmen over de afloop van deze onenigheid. Het is verder aan de leden zelf om eventuele consequenties daadwerkelijk door te voeren. Hiertoe kan Ben zijn adminrechten gebruiken (punten herverdelen en dergelijke).

        ### /details
        details_message = (
            '*Extra uitleg over de acties* 🧙‍♂️\n\n'
            '⚡ *Boost* je andermans doel, dan krijgen jij en je doelwit *+1* punt als ze het halen.\nHalen zij hun doel die dag niet, dan krijg jij je boost terug.\n\n'
            '😈 *Challenge* iemand om iets specifieks te doen vandaag. Jij krijgt *+1* punt zodra de uitdaging geaccepteerd wordt, en zij overschrijven hun dagdoel (als ze dat '
            'al ingesteld hadden). Bij voltooiing krijgen zij *+2* punten. Als je met je challenge niemand tagt of niemands berichtje beantwoordt, stuur je een open uitdaging. Die kan kan dan door iedereen '
            'worden geaccepteerd.\nWordt je uitdaging niet geaccepteerd dan kun je hem terugtrekken, of wachten, dan krijg je hem einde dag vanzelf weer terug.\n\n '
            '🤝 *Link* jouw doel met dat van een ander. Nu moeten jullie allebei je dagdoel halen om *+2* punten bonus pp te verdienen.\nLukt dit '
            'een van beiden niet, betekent dat *-1* punt voor jou (en voor hen geen bonus).\n\n'
        )

        ### /filosofie
        Als de gebruiker vandaag nog geen doel heeft ingesteld, krijgen ze een 'random_philosophical_message()', uit een verzameling van ~30 favoriete quotes van Ben.
        Als de gebruiker vandaag al wel een doel heeft ingesteld, krijgen ze een custom-reactie van TakenTovenaars grootvader, op basis van hun doel.

        
        ## Databases
        
        Dit zijn momenteel de 4 databases die per chat onderhouden worden: 
        ### users table:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT,
                chat_id BIGINT,
                total_goals INTEGER DEFAULT 0,
                completed_goals INTEGER DEFAULT 0,
                score INTEGER DEFAULT 0,
                today_goal_status TEXT DEFAULT 'not set', -- either 'set', 'not set', or 'Done at TIMESTAMP'
                set_time TIMESTAMP,       
                today_goal_text TEXT DEFAULT '',
                live_challenge TEXT DEFAULT '<dictrionary>',
                inventory JSONB DEFAULT '"boosts": 2, "challenges": 2, "links": 2',       
                PRIMARY KEY (user_id, chat_id)
            )
        ''')

        ### bot table:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_status (
            last_reset_time TIMESTAMP DEFAULT %s
            )
        ''', (two_am_last_night,))

        ### engagements table:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS engagements (
            id BIGSERIAL PRIMARY KEY,
            engager_id BIGINT NOT NULL,
            engaged_id BIGINT,
            chat_id BIGINT NOT NULL,
            special_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'live', -- either 'pending', 'live', 'archived_done' or 'archived_unresolved'
            UNIQUE (engager_id, engaged_id, special_type, chat_id),
            FOREIGN KEY (engager_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE,
            FOREIGN KEY (engaged_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS unique_active_engagements
        ON engagements(engager_id, engaged_id, special_type, chat_id)
        WHERE status IN ('live', 'pending');               

        ''')

        ### goal history table:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS goal_history (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            goal_text TEXT NOT NULL,
            completion_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            goal_type TEXT NOT NULL DEFAULT 'personal', -- 'personal' or 'challenges'
            challenge_from BIGINT,  -- NULL for personal goals, user_id of challenger for challenges
            FOREIGN KEY (user_id, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE,
            FOREIGN KEY (challenge_from, chat_id) REFERENCES users(user_id, chat_id) ON DELETE CASCADE
        );
        ''')


        # Gebruikersgegevens

        Hier volgen alle gegevens die zojuist met database queries zijn opgehaald over de stand van zaken in deze appgroep, met name voor {first_name}:
    
        {meta_data}  

        # Je antwoord nu
        Houd het kort en to-the-point. Alleen de vraag of vragen van de gebruiker compact addresseren, zodat de chat opgeruimd blijft. Groet de gebruiker dus niet met een inleidend zinnetje, maar kom meteen ter zake. Verwerk altijd een 🧙‍♂️-emoji in je antwoord. 
        Als {first_name} (user_id: {user_id}) een vraag heeft over de ranking, wees dan geheimzinnig. Licht een tipje van de sluier op, maar geef niet alles prijs.
        """
        
        if any(word in user_message.lower() for word in ['bananen', 'banaan']):
            print(f"We zijn nu in meta-bananenland!\n ({user_message})")
            system_message += """
            \n\n\n P.S: Ohja... je kunt trouwens niet ophouden met praten over BANANEN!
            Je houdt in het bijzonder van hun NEGATIEVE GEOTROPIE! (Je bananenobsessie komt doordat je als kind ooit in een blender met banaansmoothie (+ bananenconcentraat) bent gevallen).
            """

    user_content = f"# Het berichtje van de gebruiker\n{first_name} zegt: {user_message}"

    # No bot_last_response for confused bot, so it gets less info
    if message_type == 'sleepy':
        user_content = user_message

        
    if bot_last_response:
        print("!er is een bot_last_response!")
        bot_last_response_user = bot_last_response.from_user.first_name if bot_last_response.from_user else "onbekend"
        bot_last_response_text = bot_last_response.text if bot_last_response.text else "geen tekst"
        user_content += f" (Reactie op een eerder bericht van {bot_last_response_user}: {bot_last_response_text})"
    print(f"\n!!!!\n\n!!!!!!\n\nuser prompt: {user_content}")
    
    # Add system and user messages to the OpenAI messages list
    messages = [{"role": "system", "content": system_message}]
    # Include the goal text if available
    if goal_text and message_type != 'meta':
        print(f"user prompt: Het ingestelde doel van {first_name} is: {goal_text}")
        messages.append({"role": "user", "content": f"# Het ingestelde doel van de gebruiker\n{first_name}'s doel is: {goal_text}"})
    messages.append({"role": "user", "content": user_content})

    print(f"\n!!!!\n\n!!!!!!\nFinal messages:\n {messages}")
    return messages


async def collect_meta_data(user_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()

        # 1. Query to fetch user stats from the users table
        cursor.execute('''
            SELECT total_goals, completed_goals, score, today_goal_status, today_goal_text, inventory, weekly_goals_left
            FROM users
            WHERE user_id = %s AND chat_id = %s;
        ''', (user_id, chat_id))
        user_data = cursor.fetchone()
        
        if user_data:
            total_goals, completed_goals, score, today_goal_status, today_goal_text, inventory, weekly_goals_left = user_data
        else:
            return "No user data found."

        # 2. Query to get live engagements (like challenges, links, boosts) for the user
        cursor.execute('''
            SELECT engager_id, special_type, status
            FROM engagements
            WHERE engaged_id = %s AND chat_id = %s AND status IN ('live', 'pending');
        ''', (user_id, chat_id))
        live_engagements = cursor.fetchall()

        # 3. Query to get the last reset time from the bot_status table
        cursor.execute('''
            SELECT last_reset_time
            FROM bot_status;
        ''')
        last_reset_time = cursor.fetchone()[0]

        # 4. Query to get goal history for the user
        cursor.execute('''
            SELECT goal_text, completion_time, goal_type, challenge_from
            FROM goal_history
            WHERE user_id = %s AND chat_id = %s
            ORDER BY completion_time DESC
            LIMIT 5;
        ''', (user_id, chat_id))
        recent_goals = cursor.fetchall()

        # 5. Query to rank all users by score in this chat
        cursor.execute('''
            SELECT user_id, score
            FROM users
            WHERE chat_id = %s
            ORDER BY score DESC;
        ''', (chat_id,))
        score_ranking = cursor.fetchall()

        # Find the current user's rank
        user_rank = None
        for rank, (uid, score) in enumerate(score_ranking, start=1):
            if uid == user_id:
                user_rank = rank
                break

        # Construct a meta-data dictionary
        meta_data = {
            "total_goals": total_goals,
            "completed_goals": completed_goals,
            "score": score,
            "today_goal_status": today_goal_status,
            "today_goal_text": today_goal_text,
            "inventory": inventory,
            "weekly_goals_left": weekly_goals_left,
            "live_engagements": live_engagements,
            "last_reset_time": last_reset_time,
            "recent_goals": recent_goals,
            "score_ranking": score_ranking,
            "user_rank": user_rank
        }
        print(f"\n\n⏺️⏺️⏺️Dit is de metadata in de collect-functie: {meta_data}\n")
        return meta_data

    except Exception as e:
        print(f"Error collecting meta data: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# Function to update user goal text to present or past tense in the database when Doelstelling or Klaar 
def update_user_goal(user_id, chat_id, goal_text):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
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
    finally:
        cursor.close()
        conn.close() 


# Function to check if user has set a goal today
def has_goal_today(user_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        return result and result[0] == 'set'
    except Exception as e:
        print(f"Error has_goal_today: {e}")
    finally:
        cursor.close()
        conn.close() 


# Function to check if user has finished a goal today
def finished_goal_today(user_id, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT today_goal_status FROM users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        result = cursor.fetchone()
        return result and result[0].startswith("Done")
    except Exception as e:
        print(f"Error finished_goal_today: {e}")
    finally:
        cursor.close()
        conn.close() 


# Currently handles EITHER engager OR engaged        
async def fetch_live_engagements(chat_id, status = 'live', engager_id=None, engaged_id=None, plain=False):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        results = []
        if engager_id:
            # Query to get live engagements for the user, grouped by special_type
            cursor.execute('''
                SELECT special_type, COUNT(*)
                FROM engagements
                WHERE engager_id = %s AND status = %s AND chat_id = %s
                GROUP BY special_type;
            ''', (engager_id, status, chat_id))

            # Fetch all results
            results.extend(cursor.fetchall())
        if engaged_id:
            # Query to get live engagements for the user, grouped by special_type
            cursor.execute('''
                SELECT special_type, COUNT(*)
                FROM engagements
                WHERE engaged_id = %s AND status = %s AND chat_id = %s
                GROUP BY special_type;
            ''', (engaged_id, status, chat_id))

            # Fetch all results
            results.extend(cursor.fetchall())
        
        # Initialize counts for each type
        boost_count = 0
        link_count = 0
        challenge_count = 0

        # Map the special types to their respective counts
        for row in results:
            special_type, count = row
            print(f"🤝😈⚡In fetch_live_engagements, fetching: {special_type}")
            if special_type == 'boosts':
                boost_count = count
            elif special_type == 'links':
                link_count = count
            elif special_type == 'challenges':
                challenge_count = count
        
        # Build the final string of emojis
        engagement_string = '⚡' * boost_count + '🤝' * link_count + '😈' * challenge_count
        if not engagement_string:
            return None

        # Add parentheses around the emoji string
        if engagement_string:
            engagement_string = f"({engagement_string})"
        else:
            return ''

        escaped_string = escape_markdown_v2(engagement_string)

        # Return the (escaped) engagement string
        if not plain:
            return escaped_string
        else:
            return engagement_string

    except Exception as e:
        print(f"Error fetching engagements: {e}")
        return ''
    finally:
        cursor.close()
        conn.close()     
    
    
# Randomly pick a message
def get_random_philosophical_message(normal_only = False, prize_only = False):
    normal_messages = [
            "Hätte hätte, Fahrradkette",  # Message 1
            "千里之行，始于足下",        
            "Ask, believe, receive ✨",   
            "A few words on looking for things. When you go looking for something specific, "
            "your chances of finding it are very bad. Because, of all the things in the world, "
            "you're only looking for one of them. When you go looking for anything at all, "
            "your chances of finding it are very good. Because, of all the things in the world, "
            "you're sure to find some of them",
            "Je kan het best, de tijd gaat met je mee",
            "If the human brain were so simple that we could understand it, we would be so simple that we couldn't",       
            "Ik hoop maar dat er roze koeken zijn",  
            "Hoge loofbomen, dik in het blad, overhuiven de weg",   
            "It is easy to find a logical and virtuous reason for not doing what you don't want to do",  
            "Our actions are like ships which we may watch set out to sea, and not know when or with what cargo they will return to port",
            "A sufficiently intimate understanding of mistakes is indistinguishable from mastery",
            "He who does not obey himself will be commanded",
            "Elke dag is er wel iets waarvan je zegt: als ik die taak nou eens zou afronden, "  
            "dan zou m'n dag meteen een succes zijn. Maar ik heb er geen zin in. Weet je wat, ik stel het "
            "me als doel bij Taeke, en dan ben ik misschien wat gemotiveerder om het te doen xx 🙃",
            "All evils are due to a lack of Telegram bots",
            "Art should disturb the comfortable, and comfort the disturbed",
            "Begin de dag met tequila",
            "Don't wait. The time will never be just right",
            "If we all did the things we are capable of doing, we would literally astound ourselves",
            "There's power in looking silly and not caring that you do",                                        # Message 20
            "...",
            "Een goed begin is de halve dwerg",
            "En ik lach in mezelf want de sletten ik breng",
            "Bij nader inzien altijd achteraf",
            "Sometimes we live no particular way but our own",                                                  # Message 25
            "If it is to be said, so it be, so it is",                                                          # Message 26
            "Te laat, noch te vroeg, arriveert (n)ooit de Takentovenaar"                                        # Message 27
        ]
    
    prize_messages = [
        {
            "message": "Als je muisjes op je mouwen knoeit, katten ze niet",
            "prize": "raad het Nederlandse spreekwoord waarvan dit is... afgeleid..?, en win 2 punten"
        },
        {
            "message": "Ik schaam me een beetje dat ik niet met een turkencracker overweg kan",
            "prize": "raad het keukengerei dat hier bedoeld wordt, en win 1 punt"
        },
        {
            "message": "De oom uit de eik batsen",
            "prize": "raad het Nederlandse spreekwoord waarvan dit nogal afleidt, en win 1 punt"
        },
        {
            "message": "Je niet laten prikken door dat stekelige mythische wezen, maar andersom!",
            "prize": "raad het Nederlandse spreekwoord dat hier zo'n beetje is omgedraaid, en win 1 punt"
        },
        {
            "message": "De kastanjes in het vuur flikkeren",                                                                            # Message 5
            "prize": "raad het Nederlandse spreekwoord waarvan dit is afgeleid, en verlies 1 punt"
        },
        {
            "message": "Inwoners uit deze gemeente zijn het staan zat",
            "prize": "raad de verborgen gemeente, en win 2 punten"
        },
        {
            "message": "Welke rij planten zit er in dit huidvraagje verscholen?",                                                       # Message 7
            "prize": "raad de plantenrij, en win 1 punt"
        },
        {
            "message": "De Total Expense Ratio, ING... naar! Daenerys zet in.",                                                         # Message 8
            "prize": "raad het Nederlandse spreekwoord waarvan dit toch echt enigszins is afgeleid, en win 2 punten"
        }
    ]
    
    # New message to append to each prize submessage
    additional_message = "\n(uitreiking door Ben)"

    # Loop through each dictionary in the list and modify the 'prize' value
    for prize_message in prize_messages:
        prize_message["prize"] += additional_message
    
    # Combine all messages for random selection
    all_messages = [
        *normal_messages,
        *[f"{msg['message']}\n\n{msg['prize']}" for msg in prize_messages]
    ]

    # Combine prize messages into a single list of formatted strings
    formatted_prize_messages = [
        f"{msg['message']}\n\n{msg['prize']}" for msg in prize_messages
    ]
    
    selected = random.choice(all_messages)
    
    if normal_only:
        selected = random.choice(normal_messages)
        
    if prize_only:
        selected = random.choice(formatted_prize_messages)

    # For prize messages, only wrap the philosophical part in italics
    for prize_msg in prize_messages:
        full_prize_msg = f"{prize_msg['message']}\n\n{prize_msg['prize']}"
        if selected == full_prize_msg:
            return f"✨_{prize_msg['message']}_✨\n\n{prize_msg['prize']}"
    
    # If it's a normal message, wrap the entire thing
    return f"✨_{selected}_✨"


async def send_openai_request(messages, model="gpt-4o-mini", temperature=None, response_format=None):
    try:
        request_params = {
            "model": model,
            "messages": messages
            }
        # only add temperature if it's provided (not None)
        if temperature is not None:
            request_params["temperature"] = temperature
        if response_format is not None:
            request_params['response_format'] = response_format
        if response_format:
            response = client.beta.chat.completions.parse(**request_params)
            return response.choices[0].message.parsed
        else:
            response = client.chat.completions.create(**request_params)
            return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error calling OpenAI: {e}")
        return None


async def check_chat_owner(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        # Get chat administrators
        admins = await context.bot.get_chat_administrators(chat_id)
    
        # Check if the user is the owner (creator)
        for admin in admins:
            if admin.user.id == user_id and admin.status == 'creator':
                return True
        return False
    except TelegramError as e:
        print(f"Error fetching chat admins, returned False as a fallback (retry if Ben =)): {e}")
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(
                "🚫 Ik kon ffkes niet checken of je de eigenaar van deze chat bent. Probeer het later opnieuw 🧙‍♂️"
            )
        else:
            print("No message object available to send a reply.")
        return False


# Function to analyze replies and mentions to bot
async def analyze_message_to_bot(update, context, type='mention'):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name
    
    if len(user_message) > 1600:
        await update.message.reply_text(f"Hmpff {first_name}... TL;DR aub? 🧙‍♂️")
        return
        
    try:
        # Fetch goal_text if the user has a goal today
        goal_text = fetch_goal_text(update)
        goal_text = goal_text if goal_text else None
        
        # Check if the message is a reply 
        bot_last_response = update.message.reply_to_message if update.message.reply_to_message else None
        print(f'🐛🐛🐛🐛🐛🐛\nthis is bot_last_response before sending classification message: {bot_last_response}\n🐛🐛🐛🐛🐛🐛')
        
        # Prepare and send OpenAI messages
        class Classificatie(BaseModel):
            beredenering: str
            classificatie: Literal['Doelstelling', 'Klaar', 'Meta', 'Overig']
            
        messages = await prepare_openai_messages(
            update, 
            user_message, 
            'classification', 
            goal_text=goal_text if goal_text else None, 
            bot_last_response=bot_last_response
        )
        
        # Prepare and send OpenAI messages
        print(messages)
        assistant_response = await send_openai_request(messages, temperature=0.1, response_format=Classificatie)
        print(f"analyze_message_to_bot > classification: {assistant_response}")
        classificatie = assistant_response.classificatie
        print(f"analyze_message_to_bot > classification: {assistant_response}")
        
        # Handle the OpenAI response
        goals_remaining = await fetch_weekly_goals_left(chat_id, user_id)
        print(f'goals_remaining: {goals_remaining}')
        
        if classificatie == 'Doelstelling' and finished_goal_today(user_id, chat_id):
            rand_value = random.random()
            # 4 times out of 5 (80%)
            if rand_value < 0.80:
                await update.message.reply_text("🚫 Je hebt je doel al gehaald vandaag. Morgen weer een dag! 🐝")
            # once every 6 times (16,67%)
            elif rand_value >= 0.8333:
                await update.message.reply_text("🚫 Je hebt je doel al gehaald vandaag... STREBER! 😘")
            # once every 30 times (3,33%)    
            else:
                await update.message.reply_text("🚫 Je hebt je doel al gehaald vandaag. Verspilling van moeite dit. En van geld. Graag €0,01 naar mijn schepper, B. ten Berge:\nDE13 1001 1001 2622 7513 46 💰")
        
        elif classificatie == 'Doelstelling' and has_goal_today(user_id, chat_id):
            await update.message.reply_text('🚫 Je hebt vandaag al een doel doorgegeven! 🐝')
        elif classificatie == 'Doelstelling' and goals_remaining == 0:
            await update.message.reply_text("🚫 Je kunt deze week geen doelen meer instellen 🧙‍♂️\n_zondag weer vier nieuwe_", parse_mode = "Markdown")
            return
        elif classificatie == 'Doelstelling':
            if goals_remaining == 1:
                warning_message = await update.message.reply_text("⚠️ Dit is je laatste dagdoel deze week 🧙‍♂️\n_zondag weer vier nieuwe_", parse_mode = "Markdown")
                await asyncio.sleep(1)
                # Schedule the deletion of the message as a background task
                asyncio.create_task(delete_message(context, chat_id, warning_message.message_id, delay=15))
            await handle_goal_setting(update, user_id, chat_id)
            print("analyze_message_to_bot > handle_goal_setting")
        elif classificatie == 'Klaar' and has_goal_today(user_id, chat_id):
            await handle_goal_completion(update, context, user_id, chat_id, goal_text)
            print("analyze_message_to_bot > handle_goal_completion")
        elif classificatie == 'Meta':
            await handle_meta_remark(update, context, user_id, chat_id, goal_text, bot_last_response)
            print("analyze_message_to_bot > handle_meta_remark")   
        else:
            await handle_unclassified_mention(update)
            print("analyze_message_to_bot > handle_unclassified_mention")

    except Exception as e:
        await update.message.reply_text("Er ging iets misss, probeer het later opnieuw.")
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
    if len(user_message) > 11 and random.random() < 0.015:
        messages = await prepare_openai_messages(update, user_message, 'sleepy')
        assistant_response = await send_openai_request(messages, "gpt-4o")
        await update.message.reply_text(assistant_response)
    # Random plek om de bot impromptu random berichtjes te laten versturen huehue
    # Reply to trigger    
    #elif user_message == '👀':
    #    await update.message.reply_text("@Anne-Cathrine, ben je al aan het lezen? 🧙‍♂️😘")
    # Send into the void
    elif user_message == 'oké en we zijn weer live':
        if await check_chat_owner(update, context):
            await context.bot.send_message(chat_id=update.message.chat_id, text="Database gereset hihi, allemaal ONvoLDoEnDe!\n\nMaar nu werk ik weer 🧙‍♂️", parse_mode="Markdown")
    elif user_message == "Guess who's back...":
        if await check_chat_owner(update, context):
            await context.bot.send_message(chat_id=update.message.chat_id, text="Tovenaartje terug ✨🧙‍♂️", parse_mode="Markdown")        
    elif user_message == 'whoops..!':
        if await check_chat_owner(update, context):
            await context.bot.send_message(chat_id=update.message.chat_id, text="*Ik ben voorlopig kapot. Tot later!* 🧙‍♂️", parse_mode="Markdown")

    # Dice-roll
    elif user_message.isdigit() and 1 <= int(user_message) <= 6:
        await roll_dice(update, context)

    # bananen
    elif any(word in user_message.lower() for word in ["bananen", "banaan", "appel", "fruit", "apen", "aap", "ernie", "lekker", "Raven", "Nino", "dan", "오년"]):
        reaction = "🍌"
        if "krom" in user_message.lower():
            reaction = "👀"
            await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
            print(reaction)
            return
        else:
            await context.bot.setMessageReaction(chat_id=chat_id, message_id=message_id, reaction=reaction)
            print(reaction)
            return

    # Nightly reset simulation
    elif user_message == '666':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await scheduled_daily_reset(context, chat_id)
    
    elif user_message == '1111':
        if await check_chat_owner(update, context):
            from handlers.reminders import prepare_daily_reminders
            chat_id = update.effective_chat.id
            await prepare_daily_reminders(context, chat_id)
                
    # reset to testing state: no engagements, 1 broccoli goal            
    elif user_message == '777':
        chat_type = update.effective_chat.type
        # Check if the chat is private or group/supergroup
        if chat_type == 'private':
            chat_id = update.effective_chat.id
            await reset_to_testing_state(update, context)
        else:
            # If it's not a private chat, proceed with checking for chat ownership
            if await check_chat_owner(update, context):
                chat_id = update.effective_chat.id
                await reset_to_testing_state(update, context)


    # Run poll
    elif user_message == 'tijdvooreenpoll':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            try:
                from handlers.weekly_poll import create_weekly_goals_poll
                await create_weekly_goals_poll(context, chat_id)
            except Exception as e:
                print(f"Error running 888-poll: {e}")
                
    # currently works only WHERE chat_id = -4591388020;
    elif user_message == 'voegneppedoelentoe':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            try:
                conn = get_database_connection()
                cursor = conn.cursor()
                # Delete existing entries
                cursor.execute('''
                    DELETE FROM goal_history
                    WHERE chat_id = -4591388020;
                ''')
                cursor.execute('''
                    INSERT INTO goal_history (user_id, chat_id, goal_text, completion_time, goal_type, challenge_from)
                    SELECT 
                        CASE WHEN random() < 0.5 THEN 1875436366 ELSE 279184266 END as user_id,
                        -4591388020 as chat_id,
                        goals.goal_text,
                        CURRENT_TIMESTAMP - (random() * INTERVAL '7 days') as completion_time,
                        CASE WHEN random() < 0.7 THEN 'personal' ELSE 'challenges' END as goal_type,
                        CASE 
                            WHEN random() < 0.7 THEN NULL 
                            ELSE (
                                CASE 
                                    WHEN (CASE WHEN random() < 0.5 THEN 1875436366 ELSE 279184266 END) = 1875436366 
                                    THEN 279184266 
                                    ELSE 1875436366 
                                END
                            )
                        END as challenge_from
                    FROM (
                    VALUES 
                        -- 30 saaie prestaties
                        ('ging naar het werk zonder koffie te drinken.'),
                        ('schoot geen boodschappenwagen weg tijdens het winkelen.'),
                        ('maakte de bedden op zonder er later in te springen.'),
                        ('beantwoordde een e-mail binnen 24 uur.'),
                        ('las een heel boek zonder de bladwijzer kwijt te raken.'),
                        ('maakte ontbijt en lunch zonder iets te verbranden.'),
                        ('maakte een to-do lijst en volgde die op.'),
                        ('klikte een hele dag geen advertenties aan.'),
                        ('dronk 8 glazen water vandaag.'),
                        ('liep naar de brievenbus en terug zonder afgeleid te worden.'),
                        ('zat een vergadering uit zonder op mijn telefoon te kijken.'),
                        ('nam de trap in plaats van de lift.'),
                        ('parkeerde perfect tussen de lijnen op de parkeerplaats.'),
                        ('maakte mijn bed op voordat ik de deur uitging.'),
                        ('vergeet geen verjaardag deze week.'),
                        ('at drie maaltijden op tijd vandaag.'),
                        ('ging slapen vóór middernacht.'),
                        ('opende geen nieuwe tabbladen tijdens het werken.'),
                        ('ging een dag zonder sociale media.'),
                        ('schoof mijn stoel netjes onder het bureau na het eten.'),
                        ('waste de vaat meteen af na het eten.'),
                        ('ruimde meteen de boodschappen op na thuiskomst.'),
                        ('begon met een project zonder het uit te stellen.'),
                        ('was mijn handen vóór het eten.'),
                        ('wandelde 10 minuten buiten zonder op mijn telefoon te kijken.'),
                        ('borg mijn was netjes op dezelfde dag dat ik het waste.'),
                        ('gebruikte geen snooze-knop bij het opstaan.'),
                        ('waste mijn handen na elke badkamerbezoek.'),
                        ('deed het licht meteen uit toen ik de kamer verliet.'),
                        ('borg mijn schoenen op in de kast in plaats van bij de deur.'),
                        -- 10 indrukwekkende prestaties
                        ('liep een marathon zonder te stoppen.'),
                        ('won een nationale quizcompetitie.'),
                        ('klom de hoogste berg van Europa.'),
                        ('voltooide een doctoraat in kwantumfysica.'),
                        ('ontdekte een nieuwe diersoort in het Amazonewoud.'),
                        ('sloeg een homerun in een professionele honkbalwedstrijd.'),
                        ('ontwierp een gebouw dat een architectuurprijs won.'),
                        ('schreef een bestseller in één jaar.'),
                        ('leerde vloeiend 5 talen spreken in 3 jaar.'),
                        ('gaf een presentatie voor een publiek van 10.000 mensen.'),
                        -- 10 inspirerende prestaties
                        ('bekeek elke dag het positieve in elke situatie.'),
                        ('hielp een onbekende zonder iets terug te verwachten.'),
                        ('stopte met roken na 20 jaar verslaving.'),
                        ('leerde weer lopen na een ernstig ongeluk.'),
                        ('voltooide een ultramarathon voor het goede doel.'),
                        ('deed dagelijks vrijwilligerswerk om daklozen te helpen.'),
                        ('begon een mentorprogramma voor kansarme jongeren.'),
                        ('verlegde persoonlijke grenzen en overwon angst voor spreken in het openbaar.'),
                        ('herstel van een burn-out en vond balans in het leven.'),
                        ('ondersteunde een vriend door een moeilijke tijd en maakte een verschil.'),
                        -- 10 grappige prestaties
                        ('slaagde erin om een sandwich te maken zonder de kaas om te laten vallen.'),
                        ('verloor een wedstrijd tegen mijn eigen schaduw.'),
                        ('ving mijn telefoon drie keer op rij voordat die de grond raakte.'),
                        ('ontsnapte uit een kamer door het raam, terwijl de deur open was.'),
                        ('probeerde de hond uit te laten maar werd zelf uitgelaten.'),
                        ('stak mijn hand op in een online meeting, per ongeluk... in het echt.'),
                        ('versloeg een mug met ninja-bewegingen midden in de nacht.'),
                        ('nam een selfie die er echt goed uitzag op de eerste poging.'),
                        ('bracht een dag door met het testen van verschillende manieren om pizza te eten.'),
                        ('deed alsof ik mijn koffiemok motiveerde voor betere prestaties.')
                    ) AS goals(goal_text);
                ''')
                conn.commit()
                print("Successfully inserted dummy goals")
            except Exception as e:
                print(f"Error inserting dummy goals: {e}")
                conn.rollback()
            finally:
                cursor.close()
                conn.close()

                
    # Special_type drop 

    elif user_message.lower().startswith('giv') and user_message.endswith('s'):
        if await check_chat_owner(update, context):
            if 'boosts' in user_message:
                await giv_specials(update, context, 'boosts')
            if 'links' in user_message:
                await giv_specials(update, context, 'links')
            if 'challenges' in user_message:
                await giv_specials(update, context, 'challenges')            
                return       
            
    elif user_message.endswith('4all'):
         if await check_chat_owner(update, context):
            if 'boosts4all' in user_message:
                await giv_specials(update, context, 'boosts', for_all = True)
            if 'links4all' in user_message:
                await giv_specials(update, context, 'links', for_all = True)
            if 'challenges4all' in user_message:
                await giv_specials(update, context, 'challenges', for_all = True)
                return
            if 'all4all':
                await giv_specials(update, context, 'all', for_all = True)
                return
            
    elif user_message == 'resultatenp0llx':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await retrieve_poll_results(update, context)
            
    elif user_message == 'zondagmydude(tte)s':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await add_weekly_goals(update, chat_id)

    elif user_message == '!prijsvraag':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await post_prize(update, context, chat_id)

    elif user_message == '?prijsvraag':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await post_prize(update, context, chat_id, kastanjes=False)

    elif user_message == 'Koreaanse prijsvraag??':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await post_prize(update, context, chat_id, Jimin=True)
            
    elif user_message == 'hoelangiseenui?' or user_message == 'Hoe lang duurt het schillen van een ui?':
        if await check_chat_owner(update, context):
            chat_id = update.effective_chat.id
            await post_prize(update, context, chat_id, ui=True)

async def post_prize(update, context, chat_id, kastanjes=True, Jimin=False, ui=False):
    if ui:
        await asyncio.sleep(2)
        await context.bot.send_message(chat_id=chat_id, text="오년", parse_mode="Markdown")
        return
    await context.bot.send_message(chat_id=chat_id, text="🧙‍♂️")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    if Jimin:
        await asyncio.sleep(4)
        await context.bot.send_message(chat_id=chat_id, text="✨_양파 하나 까는 데 얼마나 대단한 시간 필요해요?_✨", parse_mode="Markdown")
        return
    for i in range(10):
        message = get_random_philosophical_message(prize_only=True)
        if kastanjes or "kastanjes" not in message:
            break
    await asyncio.sleep(4)
    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")


async def add_weekly_goals(update, chat_id, amount = 4):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET weekly_goals_left = weekly_goals_left + %s
            WHERE chat_id = %s
        """, (amount, chat_id,))
        conn.commit()
        print(f"Successfully added {amount} weekly goals for all users in chat {chat_id}.")
        await update.message.reply_text("Iedereen 4 dagdoelen erbij 🧙‍♂️")
    except Exception as e:
        print(f"Error adding weekly goals amount: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

        
async def giv_specials(update, context, special_type, for_all=False):
    try:
        chat_id = update.effective_chat.id
        conn = get_database_connection()
        cursor = conn.cursor()

        if for_all:
            # Fetch all user IDs in the chat from the 'users' table
            cursor.execute('''
                SELECT user_id FROM users WHERE chat_id = %s
            ''', (chat_id,))
            user_ids = [row[0] for row in cursor.fetchall()]

            # If no users are found, inform the sender
            if not user_ids:
                await update.message.reply_text("Geen gebruikers gevonden in deze chat 🧙‍♂️")
                return

            special_types = []
            if special_type == 'all':
                special_types = ['boosts', 'challenges', 'links']
            else:
                special_types = [special_type]
                
            # Dynamically construct the JSON path for the special_type
            for special_type in special_types:
                path = '{' + special_type + '}'

                # Update inventory for all users in the chat
                query = '''
                    UPDATE users
                    SET inventory = jsonb_set(
                        inventory,
                        %s,
                        (COALESCE(inventory->>%s, '0')::int + 1)::text::jsonb
                    )
                    WHERE user_id = ANY(%s) AND chat_id = %s
                    RETURNING user_id, inventory
                '''
                cursor.execute(query, (path, special_type, user_ids, chat_id))

                # Fetch updated inventories
                updated_inventories = cursor.fetchall()
                print(f"updated inventories for chat {chat_id}: {updated_inventories}")
                
            conn.commit()

            emoji_mapping = {
                'boosts': '⚡',
                'challenges': '😈',
                'links': '🤝'
            }
            
            emojis = ''
            types_message = ''
            
            # Get the emoji for the given special_type, then: enkelvoud
            for special_type in special_types:
                emoji = emoji_mapping.get(special_type, '')
                emojis += emoji
                special_type = special_type[:-1]
                types_message += f"\n_+1{emoji} {special_type} voor elk_"

            # Send a message to the chat
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Taeke Takentovenaar strooit rond 🧙‍♂️" + types_message,
                parse_mode="Markdown"
            )
            return

        else:
            user_id = update.effective_user.id
            first_name = update.effective_user.first_name

            # Dynamically construct the JSON path for the special_type
            path = '{' + special_type + '}'

            query = '''
                UPDATE users
                SET inventory = jsonb_set(
                    inventory,
                    %s,  -- The dynamic path in the JSON structure
                    (COALESCE(inventory->>%s, '0')::int + 10)::text::jsonb  -- Update the special_type count
                )
                WHERE user_id = %s AND chat_id = %s
                RETURNING inventory
            '''
            cursor.execute(query, (path, special_type, user_id, chat_id))
            conn.commit()

            # Fetch the updated inventory result
            updated_inventory = cursor.fetchone()
            await update.message.reply_text(
                f"Taeke Takentovenaar deelt uit 🧙‍♂️\n_+10 {special_type} voor {first_name}_",
                parse_mode="Markdown"
            )
            return updated_inventory

    except Exception as e:
        print(f"Error updating specials: {e}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        conn.close()

        

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
        conn = get_database_connection()
        cursor = conn.cursor()
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

            print(f"Testing state reached at {datetime.now(tz=BERLIN_TZ)}")
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
        cursor.close()
        conn.close()
        print(f"\n***Engagements after reset: {engagement_count_after}\n")
        return


async def scheduled_daily_reset(context_or_application, chat_id=None):
    try:
        if hasattr(context_or_application, 'bot'):
            # It's a context object from JobQueue
            bot = context_or_application.bot
        elif hasattr(context_or_application, 'get_bot'):
            # It's an application object from setup
            bot = context_or_application.get_bot()
        else:
            raise ValueError("Invalid context or application passed to scheduled_daily_reset")
        
        print(f"Type of bot in scheduled_daily_reset: {type(bot)}")
        # currently always called iterating over specific chat_ids. (But I want to keep the option open to just call it without chat_id, and perform the iteration inside this function)
        if chat_id:
            await reset_goal_status(bot, chat_id)
            return
        else:
            conn = get_database_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT chat_id FROM users")
            chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]
            cursor.close()
            conn.close()
            for chat_id in chat_ids:
                # Exclude private chats (positive chat IDs)
                if chat_id > 0:
                    print(f"Skipping private chat for scheduled daily reset {chat_id}")
                    continue
                await reset_goal_status(bot, chat_id)
    except Exception as e:
        print(f"Error in scheduled_daily_reset: {e}")
        return

