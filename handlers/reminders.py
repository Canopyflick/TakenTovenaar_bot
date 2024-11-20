from pydantic import BaseModel, Field
from TelegramBot_Takentovenaar import client, get_database_connection, BERLIN_TZ
from telegram.constants import ChatAction
from datetime import datetime, time, timedelta, timezone
from typing import List, Literal, Optional
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from utils import handle_goal_completion
from zoneinfo import ZoneInfo
import pytz
import asyncio, random

from utils import check_chat_owner, fetch_goal_text


async def prepare_daily_reminders(context, chat_id=None):
    chat_ids = [] 
    conn = get_database_connection()
    cursor = conn.cursor()
    # grab all chat_ids if called from setup
    try:
        if not chat_id:
            cursor.execute("SELECT DISTINCT chat_id FROM users")
            chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]
            for chat_id in chat_ids:
                print(f"chat_id: {chat_id}")
        else:
            chat_ids = [chat_id]
    except Exception as e:
        print(f"Error fetching chat_ids in prepare_daily_reminders: {e}")
        return

    time_now = datetime.now(tz=BERLIN_TZ)
    
    # Do the goal_setters nudges first, unless it's after 8, then skip
    if time_now < time_now.replace(hour=20, minute=00, second=0, microsecond=0):
        print(f"Yep het is vroeg genoeg")
        # 2. Fetch goal setters (only selecting user_id) and send them a hardcoded reminder immediately
        for chat_id in chat_ids: 
            if chat_id > 0:
                continue
            print(f"chat_id")
            goal_setters = await fetch_goal_setters(cursor, chat_id)
            if goal_setters:
                await send_daily_reminder(context, chat_id, goal_setters)
    if time_now > time_now.replace(hour=20, minute=00, second=0, microsecond=0):
        print(f"Te laat om goal_setters nog te triggeren")                
    
    # Then gather info for the goal_completion nudges
    goal_completers = None
    for chat_id in chat_ids:
        try:
            # 1. Fetch goal completers data
            conn = get_database_connection()
            cursor = conn.cursor()
            goal_completers = await fetch_goal_completers(context, cursor, chat_id)
            # Skip if no goal completers
            if not goal_completers:
                print(f"No goal completers found for chat_id {chat_id}")
                continue
            goal_completers_data = [
                {
                    "user_id": row[0],
                    "first_name": row[1],
                    "today_goal_text": row[2],
                    "set_time": (row[3].astimezone(BERLIN_TZ) if isinstance(row[3], datetime) else row[3]).isoformat()
                }
                for row in goal_completers
            ]
            print(f"goal_completers_data is : {goal_completers_data}")

        except Exception as e:
            print(f"Error 1 in prepare_daily_reminders: {e}")
            return
            
        # 3. query gpt-4o-mini with this data, asking it to output DailyReminders per chat_id
        class Reminder(BaseModel):
            user_id: int
            first_name: str
            goal_progress_inquiry: str
            send_now: bool
            send_later: Optional[str] = Field(None, description="ISO format datetime string for scheduled reminders")


        class DailyReminders(BaseModel):
            reminders: list[Reminder]
        
        time_now = datetime.now(tz=BERLIN_TZ)

        try:
            first_name='first_name'
            user_id='user_id'
            messages = [
                {
                    "role": "system",
                    "content": f"""
                    Jij bent TakenTovenaar_bot, actief in een telegramgroep om het stellen van doelen te faciliteren. Je bent cheeky, mysterieus, en bovenal wijs. 
                    Bekijk de aangeleverder data van een of meer gebruikers die eerder vandaag een doel instelden, en bereid per gebruiker een gepersonaliseerde reminder voor ze voor (goal_progress_inquiry), om ze te vragen of ze al klaar zijn met hun doel vandaag. Verwerk hierin altijd een 🧙‍♂️-emoji en een tag van de user: '[{first_name}](tg://user?id={user_id})'.
                    Los van een passend reminderberichtje, in lijn met het doel dat ze zich vandaag gesteld hadden (today_goal_text), bepaal je ook een geschikte timing voor deze reminder. Ofwel nu meteen (send_now), ofwel later vandaag (send_later), direct nadat je denkt dat de gebruiker het doel af moet hebben. Stuur de reminder alleen later als er een specifieke tijd of dagdeel in het doel staat, en het nu nog vroeger is dan dat moment. Bij twijfel: verstuur de reminder gewoon meteen.
                    """
                },
                {
                    "role": "user",
                    "content": f"""
                    Het is nu {time_now.isoformat()}.
                    *Dit zijn de gegevens van de gebruiker{'s' if len(goal_completers) > 1 else ''} die een reminder moet{'en' if len(goal_completers) > 1 else ''} krijgen om eventuele voltooiing te rapporteren:*
                    {goal_completers_data}
                    """
                }
            ]
            
            if not goal_completers_data:
                    print(f"No goal completers data to process for chat_id {chat_id}")
                    continue

            completion = client.beta.chat.completions.parse(
                model="gpt-4o",
                messages=messages,
                response_format=DailyReminders,
            )
            print(f"messages: {messages}")
            goal_completion_reminders = completion.choices[0].message.parsed
            print(f"\n\nDaily Reminders response: \n{goal_completion_reminders}\n\n")

        except Exception as e:
            print(f"Error 2 in prepare_daily_reminders: {e}")
            return
    
        # 4. Schedule reminders using Jobqueue
        try:
            for completion_reminder in goal_completion_reminders.reminders:
                if completion_reminder.send_now:
                    await send_daily_reminder(context, chat_id, completion_reminder=completion_reminder)
                else:
                    # Convert ISO string back to datetime for scheduling
                    BERLIN_TZ = ZoneInfo("Europe/Berlin")
                    schedule_time = datetime.fromisoformat(completion_reminder.send_later).astimezone(BERLIN_TZ) if completion_reminder.send_later else None
                    await schedule_daily_reminder(context, chat_id, completion_reminder, schedule_time)
            
        except Exception as e:
            print(f"Error 3 in prepare_daily_reminders: {e}")
            return
        finally:
            cursor.close()
            conn.close() 
    

async def send_daily_reminder(context, chat_id, goal_setters=None, completion_reminder=None):
    try:
        from TelegramBot_Takentovenaar import get_first_name
        if goal_setters:
            # implement goal setting reminder logic
            tagged_users = ''
            for user in goal_setters:
                user_id = user
                first_name = await get_first_name(context, user_id)
                tagged_users += f"[{first_name}](tg://user?id={user_id}), "
            
            tagged_users = tagged_users.rstrip(", ")
            reminder_messages = [
                f"Welnu, {tagged_users}. Goeiedag zeg. Wil{'len' if len(goal_setters) > 1 else ''} j{'ulli' if len(goal_setters) > 1 else ''}e vandaag nog een doeltje instellen? 🧙‍♂️",
                f"Herinnering aan {tagged_users}. Is er iets wat j{'ulli' if len(goal_setters) > 1 else ''}e vandaag eigenlijk nog zou{'den' if len(goal_setters) > 1 else ''} willen doen? 🧙‍♂️",
                f"Wil{'len' if len(goal_setters) > 1 else ''} {tagged_users} vandaag nog een doel instellen? 🧙‍♂️",
                f"Hmmm... 👃 Ruik je dat? Echt een dag om nog iets gedaan te krijgen, {tagged_users} 🧙‍♂️",
                f"({tagged_users}). Een taakje, allicht? 🧙‍♂️\n",
                f"Het is echt nergens voor nodig, maar {tagged_users} zou{'den' if len(goal_setters) > 1 else ''} nog een dagdoel in kunnen stellen 🧙‍♂️",
                f"Het zal mij verder aan m'n tovenaarshoed oxideren, maar {tagged_users} zou{'den' if len(goal_setters) > 1 else ''} nog een dagdoel in kunnen stellen 🧙‍♂️",
                f"{tagged_users} h{'ebben' if len(goal_setters) > 1 else 'eeft'} nog geen dagdoel staan. Chill 🧙‍♂️",
                f"{tagged_users}. J{'ulli' if len(goal_setters) > 1 else ''}e prestaties zijn eindig. OP = OP 🧙‍♂️",
                f"Wilde{'n' if len(goal_setters) > 1 else ''} {tagged_users} nog wat doen hier?\nZo ja: rep je! Zo nee: oké! 🧙‍♂️",
                f"Hey, {tagged_users}, pssst..! Doeltje instellen..? 🧙‍♂️"
                f"{tagged_users}, hoi daar. Ik hoorde dat de omstandigheden voor het instellen van een doeltje bovengemiddeld zijn op dit moment 🧙‍♂️"
            ]
            reminder_message = random.choice(reminder_messages)
            print(f"REMINDER: {reminder_message}")
            await context.bot.send_message(chat_id, text="🔔")
            await context.bot.send_chat_action(chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(2)
            await context.bot.send_message(chat_id, text=reminder_message, parse_mode="Markdown")
        # in case of completion_reminder
        if completion_reminder:
            await context.bot.send_message(chat_id, text="👀")
            # implement goal completions reminder logic
            user_id = completion_reminder.user_id
            first_name = completion_reminder.first_name
            inquiry_text = completion_reminder.goal_progress_inquiry
            # Create the buttons
            button_complete = InlineKeyboardButton(text="✅ Klaar!", callback_data=f"klaar_{user_id}_{first_name}")
            button_no = InlineKeyboardButton(text="❌ (Nog) niet", callback_data=f"nee_{user_id}_{first_name}")
            # Add both buttons in the same row
            keyboard = InlineKeyboardMarkup([[button_complete, button_no]])
            await context.bot.send_chat_action(chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(2)
            await context.bot.send_message(chat_id, text=inquiry_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        print(f"Error sending daily reminder: {e}")
        return
        
                
async def handle_goal_completion_reminder_response(update, context):
    query = update.callback_query
    print(f"{query.data})\nButton pressed by {query.from_user.id}, we're inside handle_goal_completion_reminder_response.")
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    callback_data = query.data.split('_')  # ['completion', user_id]
    print(f"callback data = {callback_data}")
    target_user_id = int(callback_data[1])
    targeted_first_name = callback_data[2]
    
    # Initialize or fetch the counter for the "Nee" button
    if user_id not in context.bot_data.get("nee_press_count", {}):
        context.bot_data.setdefault("nee_press_count", {})[user_id] = 0

    if user_id == target_user_id:    # or await check_chat_owner(update, context):
        # button presser == targeted user, or chat owner
        goal_text = fetch_goal_text(update)
        if callback_data[0] == 'klaar':
            await query.answer(text=f"🎉", show_alert=True)
            await asyncio.sleep(2)
            await query.message.delete()
            await handle_goal_completion(update, context, target_user_id, chat_id, goal_text, from_button=True, first_name=targeted_first_name)
        elif callback_data[0] == 'nee':
            context.bot_data["nee_press_count"][user_id] += 1  # Increment the counter
            if context.bot_data["nee_press_count"][user_id] > 4:
                return
            if context.bot_data["nee_press_count"][user_id] > 3:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(12)
                await context.bot.send_message(chat_id=chat_id, text=f"Ik ben er (wel) klaar mee 🧙‍♂️", parse_mode="Markdown")
            # 1st-3rd time 
            else:
                from utils import delete_message, prepare_openai_messages, send_openai_request
                messages = await prepare_openai_messages(update, user_message=None, message_type="grandpa quote", goal_text=goal_text)
                grandpa_quote = await send_openai_request(messages, "gpt-4o")
                grandpa_quote = grandpa_quote.strip('"')
                random_delay = random.uniform(1, 10)
                
                if context.bot_data["nee_press_count"][user_id] == 1:
                    hot_tip = await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="(Als je wilt, kun je met /reset nog van doel wisselen vandaag 🧙‍♂️)"
                    )
                    await asyncio.sleep(1)
                    await context.bot.send_message(chat_id=chat_id, text=f"({targeted_first_name} heeft hun doel nog niet af)\n\nGeen zorgen, doe het morgen 🧙‍♂️\n\nOf, zoals mijn opa zou zeggen:", parse_mode="Markdown")
                    # Schedule the deletion of the message as a background task
                    asyncio.create_task(delete_message(context, chat_id, hot_tip.message_id, delay=20))               
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
                await asyncio.sleep(random_delay)
                await context.bot.send_message(chat_id=chat_id, text=f"✨_{grandpa_quote}_✨", parse_mode="Markdown")
        else:
            print(f"iets goed mis met callback-data")
    # button presser != targeted user, ## nor chat owner
    else:
        #if not await check_chat_owner(update, context):
        await query.answer(text=f"Mjeh dat moet {targeted_first_name} toch zelf doen xx 🧙‍♂️", show_alert=True)
        print(f"{user_id} probeerde handle_goal_completion_reminder_response te gebruiken voor iemand anders, namelijk: {targeted_first_name}({target_user_id})")
        return
                

    
    
async def fetch_goal_completers(context, cursor, chat_id):
    query = '''
        SELECT user_id, today_goal_text, set_time
        FROM users
        WHERE chat_id = %s AND today_goal_status = 'set'
    '''
    cursor.execute(query, (chat_id,))
    result = cursor.fetchall()
    updated_result = []
    from TelegramBot_Takentovenaar import get_first_name
    for user in result:
        user_id, today_goal_text, set_time = user
        first_name = await get_first_name(context, user_id)
        updated_result.append((user_id, first_name, today_goal_text, set_time))
    
    return updated_result


async def fetch_goal_setters(cursor, chat_id):
    """
    Fetch users who need to set new goals based on the criteria:
    - No pending goal for today.
    - Remaining goals this week >= remaining days this week until Sunday (aka: should set a goal every day if they wanna finish their max amount of daily goals this week).
    - Have set a goal in the past 3 days. (aka: active users)
    """
    print(f"\n\nEn we zijn in fetch_goal_setters\n\n")
    today = datetime.now(tz=BERLIN_TZ).date()
    last_3_days = today - timedelta(days=3)
    print(f"last_3_days: {last_3_days}")
    # Calculate remaining days in the week (Sunday inclusive)
    remaining_days = 7 - today.weekday()  # Monday is 0
    print(f"\n\nRemaining days = {remaining_days}\n\n")

    query = '''
        SELECT user_id
        FROM users
        WHERE chat_id = %s
        AND weekly_goals_left >= %s
        AND set_time >= %s
        AND today_goal_status = 'not set'
    '''
    cursor.execute(query, (chat_id, remaining_days, last_3_days))
    # 3 users would look like this: [(123456,), (789012,), (345678,)]
    
    result = cursor.fetchall()
    # Convert list of tuples to list of user_ids
    user_ids = [row[0] for row in result]
    print(f"\n\nDit is de query: {query}\n\n")
    print(f"\n\nEn het result is {result}\n\n")
    return user_ids

    

async def schedule_daily_reminder(context, chat_id, completion_reminder, schedule_time):
    user_id = completion_reminder.user_id
    first_name = completion_reminder.first_name
    if await is_reminder_scheduled(user_id, chat_id):
        print(f"Deze dude(tte) {first_name} heeft al een pending scheduled reminder.")
        return
    else:
        # Schedule the individual reminder
        # Access the job queue from the application
        job_queue = context.application.job_queue
        
        # Schedule the individual reminder with run_once or run_daily
        job_queue.run_daily(
            send_daily_reminder,
            time=schedule_time,
            context={'chat_id': chat_id, 'user_id': user_id, 'first_name': first_name}
        )

    
        # Get and print the current device (local) time and UTC time
        local_time = datetime.now()
        berlin_time = datetime.now(tz=BERLIN_TZ)
        print(f"\nIndividual daily reminder job scheduled successfully in chat {chat_id} for {first_name}, today at {schedule_time}")
        print(f"(currently: local time {local_time.strftime('%Y-%m-%d %H:%M:%S')}, Berlin time {berlin_time.strftime('%Y-%m-%d %H:%M:%S')})\n")


async def is_reminder_scheduled(user_id, chat_id):
    conn = get_database_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT reminder_scheduled
        FROM users
        WHERE user_id = %s AND chat_id = %s
    ''', (user_id, chat_id))
    
    result = cursor.fetchone()
    conn.close()
    cursor.close()
    return result[0] if result else False


