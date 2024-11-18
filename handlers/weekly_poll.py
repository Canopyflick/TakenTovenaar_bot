from datetime import timedelta, datetime
from pydantic import BaseModel
from TelegramBot_Takentovenaar import client, get_first_name, get_database_connection, BERLIN_TZ
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
import asyncio, re



async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    from utils import check_chat_owner
    if await check_chat_owner(update, context):
        await create_weekly_goals_poll(context, chat_id)
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Geduld, ohh ngeduldige xx 🧙‍♂️", parse_mode = "Markdown"
        )
        


async def scheduled_weekly_poll(context):
    print(f"inside the scheduler... :D")
    conn = get_database_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT chat_id FROM users")
    chat_ids = [chat_id[0] for chat_id in cursor.fetchall()]
    for chat_id in chat_ids:
        print(f"chat_id: {chat_id}")
        await create_weekly_goals_poll(context, chat_id)
    cursor.close()
    conn.close()
        

dummy_40_goals = [
    "knuffelde de kerstman.",
    "deed de afwas voordat Anne-Cathrine thuis kwam.",
    "rende 4 km.",
    "schreef een lief kaartje voor de buren.",
    "kookte een driegangendiner voor vrienden.",
    "las een heel boek in één dag.",
    "maakte een schilderij van de zonsondergang.",
    "belde met opa om te horen hoe het met hem gaat.",
    "ging een uur wandelen in het bos.",
    "organiseerde een filmavond voor de familie.",
    "leerde een nieuw gerecht te maken.",
    "gaf geld aan een straatmuzikant.",
    "voltooide een moeilijke puzzel.",
    "speelde een bordspel met vrienden.",
    "gaf een presentatie zonder zenuwen.",
    "ging naar een museum en leerde iets nieuws.",
    "maakte een lijst van dingen waar ik dankbaar voor ben.",
    "schreef een blogpost over persoonlijke groei.",
    "ruimde de garage volledig op.",
    "zette de kerstboom op zonder te klagen.",
    "gaf mezelf een dagje rust en ontspanning.",
    "liep een halve marathon.",
    "bakte een taart voor de verjaardag van een vriend.",
    "leerde een nieuwe taal online.",
    "hielp een collega met een lastige taak.",
    "ging naar de sportschool drie keer deze week.",
    "organiseerde een picknick in het park.",
    "mediteerde elke ochtend deze week.",
    "las elke avond voor het slapen gaan.",
    "haalde mijn rijbewijs na veel oefenen.",
    "plantte bloemen in de tuin.",
    "maakte een fotoboek van de afgelopen vakantie.",
    "liet de hond elke ochtend uit.",
    "hielp een vriend verhuizen naar een nieuw huis.",
    "kocht een cadeau voor een vriendin zonder reden.",
    "probeerde een nieuwe hobby: pottenbakken.",
    "haalde het huis op tijd voor de lente schoonmaak.",
    "belde mijn ouders elke avond deze week.",
    "genoot van een dag zonder telefoon.",
    "maakte een roadtrip naar een onbekende stad."
]


async def prepare_weekly_goals_poll(chat_id):
    conn = get_database_connection()
    cursor = conn.cursor()
    # Get goals from the past week
    cursor.execute('''
        SELECT goal_text 
        FROM goal_history 
        WHERE chat_id = %s 
        AND completion_time >= CURRENT_TIMESTAMP - INTERVAL '7 days'
        ORDER BY RANDOM()
    ''', (chat_id,))
    
    goals = [row[0] for row in cursor.fetchall()]
    print(f"goals =\n\n{goals}\n")
    print(f"Number of goals = {len(goals)}")  # Debugging print

    if len(goals) <= 2:
        return 'too few goals this week' # If fewer than 3 goals, cancel the poll in create weekly_goals_poll function
        
    if len(goals) <= 10:    
        return goals  # If 10 or fewer goals, use all of them
    
    # If more than 10 goals, LLM selection
    goals_text = "\n".join(f"- {goal}" for goal in goals)
    print(f"goals text =\n\n{goals_text}\n")

    class SimpleArray(BaseModel):
        goals_array: list[str]  # An array of strings
    
    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """
                From the provided list of completed goals, select the 10 most impressive, inspiring, or funny ones.
                Return a JSON array. Ensure that each goal is a direct quote from the input list."""},
                {"role": "user", "content": goals_text}
            ],
            response_format=SimpleArray,
        )

        # Access the goals_array from the parsed response
        response = completion.choices[0].message.parsed
        goals_list = response.goals_array
        print(f"\ngoals_list:\n {goals_list}")

        # Validate we got a valid list and 8-12 goals
        if not isinstance(goals_list, list) or len(goals_list) < 8 or len(goals_list) > 12:
            print("Invalid response format from GPT")
            return goals[:10]
             
        return goals_list
        
    except Exception as e:
        print(f"Error in prepare_weekly_goals_poll: {e}")
        # If GPT selection fails, take 10 random goals as fallback
        return goals[:10]
    finally:
        cursor.close()
        conn.close()

async def create_weekly_goals_poll(context, chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        print(f"\n\n|||||||||||| Creating weekly poll...\nfor chat_id     {chat_id}🧙‍♂️|||||||||||||||||||||||\n")            
        selected_goals = await prepare_weekly_goals_poll(chat_id)
        if selected_goals == 'too few goals this week':
            print("too few goals so far")
            return  # cancel the poll (verder niks, want dat zou spam zijn in groepen waar niks gebeurt)
        # Create poll options, adding number prefixes for easier reference
        poll_options = [f"{i+1}. {goal}" for i, goal in enumerate(selected_goals)]
        
        poll_message = await context.bot.send_poll(
            chat_id=chat_id,
            question="🏆🧙‍♂️ Stem op je favoriete doelen van afgelopen week!",
            options=poll_options,
            is_anonymous=True,
            allows_multiple_answers=True
        )
        
        # Extract poll_id and message_id from poll_message
        poll_id = poll_message.poll.id
        message_id = poll_message.message_id

        # Store the poll in bot_data & in database in case of bot outage
        context.bot_data[poll_id] = poll_message
        
        # Define the Berlin timezone
        current_time = datetime.now(tz=BERLIN_TZ)

        cursor.execute("""
            INSERT INTO polls (chat_id, poll_id, message_id, created_at)
            VALUES (%s, %s, %s, %s)
        """, (chat_id, poll_id, message_id, current_time))
        conn.commit()
        
        # Schedule a job to retrieve the poll results 1 hour later
        context.job_queue.run_once(
            retrieve_poll_results,
            timedelta(minutes=60),
            data={"chat_id": chat_id, "message_id": message_id, "poll_id": poll_id}
        )
        closing_time = datetime.now(tz=BERLIN_TZ) + timedelta(minutes=61)

        closing_time_formatted = closing_time.strftime('%H:%M')

        await asyncio.sleep(2)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(3)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"De poll zal sluiten om: *{closing_time_formatted}* 🧙‍♂️", parse_mode = "Markdown"
        )
    except Exception as e:
        print(f"Error creating weekly goals poll: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Weekly goals poll viel ergens in het water 🐛🧙‍♂️"
        )
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

# not used anymore
async def receive_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updates the poll data in bot_data when a poll update is received."""
    poll = update.poll
    current_time = datetime.now(tz=BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{current_time}] update poll pingy in receive_poll()")
    # Update the poll in bot_data
    context.bot_data[poll.id] = poll

    

async def retrieve_poll_results(update, context):
    print(f"we zitten nu te retrieven")
    job_data = context.job.data if context.job else None
    chat_id=None
    poll_id = None
    message_id = None
    
    # Check if job_data is available; if not, fall back to get the most recent poll
    if job_data:
        chat_id = job_data["chat_id"]
        poll_id = job_data["poll_id"]
        message_id = job_data["message_id"]
    else:
        # Fallback to retrieve poll data from the database
        try:
            conn = get_database_connection()
            cursor = conn.cursor()
            chat_id = update.effective_chat.id

            cursor.execute("""
                SELECT poll_id, message_id FROM polls
                WHERE chat_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (chat_id,))
            result = cursor.fetchone()
            if result:
                poll_id, message_id = result
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="P0lll kwijt... 🐛🧙‍♂️"
                )
                return
        except Exception as e:
            print(f"Error retrieving poll: {e}")
        finally:
            cursor.close()
            conn.close()
    # Check if the poll has already been processed
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT processed FROM polls WHERE poll_id = %s
        """, (poll_id,))
        result = cursor.fetchone()
        if result and result[0]:  # If processed is True
            await context.bot.send_message(
                chat_id=chat_id,
                text="_🚫 de pollresultaten zijn eerder al verwerkt 🧙‍♂️_", parse_mode = "Markdown"
            )
            return
    except Exception as e:
        print(f"Error checking poll processed status: {e}")
    finally:
        cursor.close()
        conn.close()
    
    # Notify users that the poll is closing
    await context.bot.send_message(
        chat_id=chat_id,
        text="*Laatste kans om te stemmen. De poll gaat over 1 minuut sluiten 🧙‍♂️*", parse_mode = "Markdown"
    )
    await asyncio.sleep(60)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(5)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Eèèèn... de poll is bij deze dan gesloten. Ik tel de stemmen."
    )
    await asyncio.sleep(2)
    await context.bot.send_message(
        chat_id=chat_id,
        text="🧙‍♂️"
    )
    # Stop the poll and retrieve results
    try:
        poll = await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"Error stopping the poll: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Poll kwijt 🐛🧙‍♂️"
        )
        return

    # Get options and filter out those with 0 votes
    voted_options = [option for option in poll.options if option.voter_count > 0]
    
    if not voted_options:
        await context.bot.send_message(
        chat_id=chat_id,
        text=f"🏆 ...\n\n0 stemmen in de poll ... 🧙‍♂️"
        )
        return
    
    sorted_options = sorted(voted_options, key=lambda x: x.voter_count, reverse=True)

    # Function to get top results up to a certain rank
    def get_top_results(u_counts, rank):
        # Get the voter counts up to the specified rank
        top_voter_counts = u_counts[:rank]
        # Include all options that have voter counts in top_voter_counts
        return [opt for opt in sorted_options if opt.voter_count in top_voter_counts]

    # Get a list of unique voter counts in descending order
    unique_voter_counts = sorted({opt.voter_count for opt in sorted_options}, reverse=True)

    # Try including up to top 3 voter counts
    top_results = get_top_results(unique_voter_counts, 3)
    print(f"these are the top results: {top_results}")

    await award_poll_rewards(context, chat_id, top_results)
    
    # After processing, update the poll as processed
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE polls SET processed = TRUE WHERE poll_id = %s
        """, (poll_id,))
        conn.commit()
    except Exception as e:
        print(f"Error updating poll processed status: {e}")
    finally:
        cursor.close()
        conn.close()

        
async def award_poll_rewards(context, chat_id, top_results):
    goals_history = await fetch_goals_history(chat_id)
    if goals_history is None:
        # Handle the error appropriately
        await context.bot.send_message(
        chat_id=chat_id,
        text=f"🏆 Top 3 doelen uit de poll:\n\n{top_results}\n\n_punten uitdelen is niet gelukt, dat moet Ben dus maar zelf even doen 🧙‍♂️_",
        parse_mode="Markdown"
        )
        return

    # Convert to a list of dictionaries
    goals_history_list = []
    for row in goals_history:
        goal = {
            'id': row[0],
            'user_id': row[1],
            'goal_text': row[2],
            'goal_type': row[3],
            'challenge_from': row[4],
        }
        goals_history_list.append(goal)
        
    class PollOption(BaseModel):
        text: str                 # The goal text
        voter_count: int          # The number of votes this goal received
        position: int             # The position (1st, 2nd, 3rd, etc.) in the ranking
        user_id: int              # The user_id of the person who completed the goal
        challenge_from_id: int    # The user_id of the challenger (if applicable), otherwise 0

    class GoalMapping(BaseModel):
        poll_options: list[PollOption]

    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """
                You are given two lists:

                1. Top-voted PollOptions from a poll
                2. Original goals records submitted by users

                Your task is to complete the top-voted PollOptions list, by:
                - adding descending position keys to rank each PollOption, such that ties are the same position.
                - matching each top-voted goal with the original goals, by mapping it onto the corresponding user_id as found in the original goals list.
                - adding associated challenge_from_ids to the top-voted goals if available, otherwise default to 0. 

                Return the output as the correct JSON object"""},
                {"role": "user", "content": f"Top-voted goals from a poll:\n{top_results}\n\nOriginal goals submitted by users:\n{goals_history_list}"}
            ],
            response_format=GoalMapping,
        )

        response = completion.choices[0].message.parsed
        print(f"\n!!RESPONSE!!:\n {response}")
        poll_options = response.poll_options
        
        # Dictionary to map positions to points
        position_points = {1: 3, 2: 2, 3: 1}
        
        # Set to keep track of unique challenge_from_ids
        unique_challengers_ids = set()

        # List to keep track of awarded users for message preparation
        awarded_users = []
        
        for poll_option in poll_options:
            user_id = poll_option.user_id
            position = poll_option.position
            challenge_from_id = poll_option.challenge_from_id
            goal_text = poll_option.text
            points_awarded = position_points.get(position, 0)
            
            # Only proceed if points are awarded
            if points_awarded > 0:
                # Update the user's score in the database
                try:
                    conn = get_database_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE users
                        SET score = score + %s
                        WHERE user_id = %s AND chat_id = %s
                    ''', (points_awarded, user_id, chat_id))
                    conn.commit()
                except Exception as e:
                    print(f"Error updating score for user {user_id}: {e}")
                    conn.rollback()
                finally:
                    cursor.close()
                    conn.close()
                
                # Collect unique challenge_from_ids (excluding 0 or None)
                if challenge_from_id and challenge_from_id != 0:
                    unique_challengers_ids.add(challenge_from_id)

                # Add to awarded users list for message preparation
                awarded_users.append({
                    'user_id': user_id,
                    'points_awarded': points_awarded,
                    'goal_text': goal_text
                })
        
        # Prepare messages for awarded users
        for user in awarded_users:
            user_id = user['user_id']
            first_name = await get_first_name(context, user_id=user_id)
            user['first_name'] = first_name

        # Prepare first names for challengers
        challenger_names = []
        for challenger_id in unique_challengers_ids:
            first_name = await get_first_name(context, user_id=challenger_id)
            challenger_names.append(first_name)
            
        # Prepare the award messages
        award_messages = []
        for user in awarded_users:
            first_name = user['first_name']
            points_awarded = user['points_awarded']
            goal_text = user['goal_text']
            # Strip the number and period from the start of the goal_text
            goal_text = re.sub(r'^\d+\.\s*', '', goal_text)

            # Prepare the message
            award_message = f"*{first_name}* {goal_text}\n_+{points_awarded} punt{'en' if points_awarded != 1 else ''}_"
            award_messages.append(award_message)

        # Combine the messages
        awards_text = "*Punten zijn uitgedeeld aan de volgende vlijtige vlerkjes* 🧙‍♂️🏅\n\n" + "\n\n".join(award_messages)

        if challenger_names:
            if len(challenger_names) == 1:
                honorable_mentions_text = f"*Eervolle vermelding voor uw uitstekende uitdager* 🧙‍♂️🤝\n{challenger_names[0]} 😈"
            else:
                honorable_mentions_text = f"*Eervolle vermeldingen voor uw uitstekende uitdagers* 🧙‍♂️🤝\n" + ", ".join(challenger_names) + " 😈"
        else:
            honorable_mentions_text = "_(Uitdagers hadden ditmaal geen hand in de resultaten) 🧙‍♂️_"
        # Announcements, including pauses for effect        
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(1)
        await context.bot.send_message(chat_id=chat_id, text="...")
        await asyncio.sleep(2)
        await context.bot.send_message(chat_id=chat_id, text="3️⃣")
        await asyncio.sleep(1)
        await context.bot.send_message(chat_id=chat_id, text="2️⃣")
        await asyncio.sleep(1)
        await context.bot.send_message(chat_id=chat_id, text="1️⃣")
        await asyncio.sleep(1)
        await context.bot.send_message(
        chat_id=chat_id,
        text="🎊")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(1)
        await context.bot.send_message(
        chat_id=chat_id,
        text=f"{awards_text}", parse_mode = "Markdown")
        await asyncio.sleep(5)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(5)
        await context.bot.send_message(
        chat_id=chat_id,
        text=f"{honorable_mentions_text}", parse_mode = "Markdown")
        await asyncio.sleep(1)
        await context.bot.send_message(
        chat_id=chat_id,
        text="🎊")
        
    except Exception as e:
        print(f"Error in award_poll_rewards: {e}")
        return print(f'wh00ps')
    


async def fetch_goals_history(chat_id):
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT id, user_id, goal_text, 
        goal_type, challenge_from 
        FROM goal_history 
        WHERE chat_id = %s 
        AND completion_time >= CURRENT_TIMESTAMP - INTERVAL '7 days'
        ''', (chat_id,))
        results = cursor.fetchall()
    except Exception as e:
        print(f"\nError fetching goals_history: {e}\n")
        return None
    finally:
        cursor.close()
        conn.close()
    return results


async def get_most_recent_poll(context, chat_id):
    try:
        # Fetch recent messages in the chat (limit to, e.g., 100 recent messages)
        recent_messages = await context.bot.get_chat_history(chat_id, limit=200)

        # Iterate through messages to find the most recent poll
        for message in recent_messages:
            if message.poll:  # Check if the message contains a poll
                return message.poll, message.message_id  # Return poll and its message_id
            
        return None, None  # No poll found
    except Exception as e:
        print(f"Error fetching most recent poll: {e}")
        return None, None



dummy_goals_history = [
    (1, '955543456', "knuffelde de kerstman", 'personal', None),
    (2, '123334', "deed de afwas voordat Anne-Cathrine thuis kwam", 'personal', None),
    (3, '955543456', "rende 4 km", 'challenges', '786786'),
    (4, '666666', "schreef een lief kaartje voor de buren", 'personal', None)
    ]