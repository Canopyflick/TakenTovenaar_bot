from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ConversationHandler, CallbackContext
from utils import check_identical_engagement, fetch_live_engagements, check_use_of_special, add_special, send_openai_request, fetch_goal_status, get_database_connection
import asyncio


HANDLE_RESPONSE = 1


async def challenge_command(update: Update, context):
    print(f"Challenging ... step 1\n")
    print(f"Update context: {update.message.text}")
    if await check_use_of_special(update, context, 'challenges'):
        return await challenge_command_2(update, context, context.chat_data['engager_id'], context.chat_data['engager_name'], 
                                         context.chat_data['engaged_id'], context.chat_data['engaged_name'], 
                                         context.chat_data['user_mentioned'])



# Once here, the challenge is valid, and can be saved as a pending engagement, subtracted from inventory, and broadcast to engaged
async def challenge_command_2(update, context, engager_id, engager_name, engaged_id, engaged_name, user_mentioned):
    print(f"Challenging ... step 1\n")
    challenge_goal = update.message.text.partition(" ")[2]  # Get the goal part from the challenge command
    if user_mentioned:
        # Split the challenge_goal into words, remove the first word, and rejoin the remaining part
        challenge_goal = " ".join(challenge_goal.split()[1:])
    if challenge_goal == '':
        await update.message.reply_text(f"🚫 Voeg iets specifieks toe. Wat wil je van ze? 🧙‍♂️\n_(zie voorbeelduitdaging aan Josefietje in /acties_)", parse_mode = "Markdown")
        return
    chat_id = update.effective_chat.id
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO engagements (engager_id, engaged_id, chat_id, special_type, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        ''', (engager_id, engaged_id, chat_id, 'challenges', 'pending'))
        engagement_id = cursor.fetchone()[0]
        
        print(f"Engagement created: engager_id={engager_id}, engaged_id={engaged_id}, chat_id={chat_id}, engagement_id={engagement_id} 1/2")
        
        conn.commit()
    except Exception as e:
        print(f"Error creating engagement: {e}")
        await update.message.reply_text("Er is een fout opgetreden bij het uitdagen 🧙‍♂️🐛")
        conn.rollback()
        return
    finally:
        cursor.close()
        conn.close()
    
    await add_special(engager_id, chat_id, "challenges", -1)   

    print(f"Received message text: {update.message.text}\n")

    print(f"dit is challenge_goal: {challenge_goal}")
    print(f"dit is engaged_id 2/3: {engaged_id}")
    # Call OpenAI API to rephrase the challenge goal
    messages=[
        {"role": "system", "content": "Herformuleer uitdagingen van {engager_name} aan {engaged_name} naar een opdracht voor vandaag."},
        {"role": "user", "content": "om me een massage te geven"},
        {"role": "assistant", "content": "Geef {engager_name} vandaag een massage."},
        {"role": "user", "content": "potje armworstelen tot de dood!"},
        {"role": "assistant", "content": "Doe vandaag een potje armworstelen tot de dood met {engager_name}."},
        {"role": "user", "content": "weet je wat mij nou een goed idee lijkt? Als jij eindelijk eens ff reageert op die datumprikker die al 2 weken uitstaat (je bent de enige die nog niet heeft gereageerd xoxo)"},
        {"role": "assistant", "content": "Reageer vandaag op de datumprikker die al 2 weken uitstaat."},
        {"role": "user", "content": "wie het langst z'n adem in kan houden"},
        {"role": "assistant", "content": "Doe vandaag een wedstrijdje wie het langst z'n adem in kan houden met {engager_name}."},
        {"role": "user", "content": "verpot alle planten"},
        {"role": "assistant", "content": "Verpot vandaag alle planten."},
        {"role": "user", "content": "geef me een cadeautje"},
        {"role": "assistant", "content": "Geef vandaag een cadeautje aan {engager_name}."},
        {"role": "user", "content": challenge_goal}
    ] 
    rephrased_goal = await send_openai_request(messages, temperature=0.1)  # Extract the rephrased goal from OpenAI response
    print(f"Rephrased goal: {rephrased_goal}\n\n")
    if rephrased_goal:
        # Dynamically replace the placeholders in the rephrased goal with actual values
        rephrased_goal = rephrased_goal.format(engager_name=engager_name, engaged_name=engaged_name)

        context.chat_data[engagement_id] = {
            'goal': rephrased_goal,
            'engager_id': engager_id,
            'engager_name': engager_name,
            'engaged_id': engaged_id,
            'engaged_name': engaged_name
        }
        
        challenge_message = f"😈 {engager_name} daagt {engaged_name} uit:\n\n*{rephrased_goal}*"
        # tag engaged here if they weren't tagged when engager initialized the challenge
        if not user_mentioned:
            if not engaged_id:
                challenge_message = f"😈 {engager_name} laat een uitdaging vallen:\n\n*{rephrased_goal}*"
            else:
                challenge_message = f"😈 {engager_name} daagt [{engaged_name}](tg://user?id={engaged_id}) uit:\n\n*{rephrased_goal}*"
            
        # add a lil overwrite current goal reminder if engaged already set a day goal, or if it's an open challenge (no engaged_id) 
        if engaged_id:
            if await fetch_goal_status(update, engaged_id) == 'set':
                challenge_message += "\n\n_een eventueel reeds ingesteld dagdoel wordt overschreven als je deze uitdaging aanneemt_"
        if not engaged_id:
            challenge_message += "\n\n_een eventueel reeds ingesteld dagdoel wordt overschreven als je deze uitdaging aanneemt_"
            
        # Adjust buttons for open challenges
        buttons = [
            [InlineKeyboardButton("👿 Uitdaging intrekken", callback_data=f'retract_{engagement_id}')],
        ]
        if not engaged_id:  # Open challenge, only accept is available
            buttons.append([InlineKeyboardButton("👍 Aannemen", callback_data=f'accept_{engagement_id}')])
        else:  # Direct challenge, both accept and reject available
            buttons.append([InlineKeyboardButton("👍 Aannemen", callback_data=f'accept_{engagement_id}'), InlineKeyboardButton("👎 Weigeren", callback_data=f'reject_{engagement_id}')])
        
        await update.message.reply_text(challenge_message, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text("Er is een fout opgetreden bij het herformuleren van de uitdaging 👿")
    

async def handle_challenge_response(update, context):
    query = update.callback_query
    print(f"1/4 ({query.data})\nButton pressed by {query.from_user.id}, we're inside handle_challenge_response.")
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    callback_data = query.data.split('_')  # ['accept', '88']
    print(f"callback data = {callback_data}")
    
    # Validate callback data
    if len(callback_data) < 2:
        await query.message.reply_text(f"Er is een fout opgetreden: Ongeldig callback-gegevensformaat 🧙‍♂️\n_+1😈 voor {engager_name}_", parse_mode = "Markdown")
        await add_special(engager_id, chat_id, "challenges")
        return ConversationHandler.END
    
    # Get the action and engagement_id
    action = callback_data[0]
    engagement_id = int(callback_data[1])
    print(f"\n2/4 ({query.data})\n unpacking action as: {action} | unpacking engagement_id as {engagement_id}")
    
    # Get engagement data from context
    engagement_data = context.chat_data.get(engagement_id)
    if not engagement_data:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="🚫 *Foutje!* 🐛\n\nJe drukte op een verlopen knop. Deze en eerdere 'pending challenges' (uitdagingen die nog niet geaccepteerd, afgewezen, of ingetrokken waren) zijn kapot.\nProbeer het met een nieuwe(re) nog eens 🧙‍♂️\n\n_NB: de uitdager is z'n 😈 kwijt, moet Ben ff teruggeven als hij dat nog niet gedaan heeft xx_",
            parse_mode="Markdown"
        )
        # await add_special(engager_id, chat_id, "challenges")  # dit kan nog niet, want variabelen gaan verloren bij deze fout (herstart app)
        return ConversationHandler.END
    
    # Extract engagement data
    engager_id = engagement_data['engager_id']
    engager_name = engagement_data['engager_name']
    engaged_id = engagement_data['engaged_id']
    engaged_name = engagement_data['engaged_name']
    goal_text = engagement_data['goal']

    print(f"3/4 ({query.data})\nButton presser is: {user_id}\nExtracted engagement data: engager_id:{engager_id} | engaged_id: {engaged_id} | engagement_id: {engagement_id} | Callback data:{query.data}")

    # Handle acceptance or rejection or retraction
    try:
        conn = get_database_connection()
        cursor = conn.cursor()
        if action == 'retract':
            print(f"\n4/4 ({query.data})\nACTION = RETRACT\n\nCallback data:{query.data}")
            if user_id != engager_id:
                await query.answer(text="Dat mag jij niet xx 🧙‍♂️", show_alert=True)
                print(f"Not allowed, retracting is only for engager")
                return
            try:
                cursor.execute('''
                UPDATE engagements
                SET status = 'archived_unresolved'
                WHERE id = %s
                AND chat_id = %s;
                ''', (engagement_id, chat_id))
                conn.commit()

                print(f"Engagement archived: chat_id={chat_id}, engagement_id={engagement_id}")
                
                await add_special(engager_id, chat_id, "challenges")
                if not engaged_id:
                    await query.edit_message_text(
                    f"{engager_name} heeft de uitdaging aan _niemand in het bijzonder_ ingetrokken\n_+1 😈_",
                    parse_mode="Markdown"
                    )
                else:
                    await query.edit_message_text(
                        f"{engager_name} heeft de uitdaging aan [{engaged_name}](tg://user?id={engaged_id}) ingetrokken\n_+1 😈_",
                        parse_mode="Markdown"
                    )
                
                
            except Exception as e:
                print(f"Error archiving challenge: {e}")
                await query.message.reply_text("Er is een fout opgetreden bij het intrekken van de uitdaging 🐛🧙‍♂️")
                conn.rollback()

        elif action == 'accept':
            print(f"\n4/4 ({query.data})\nACTION = ACCEPT\n\nCallback data:{query.data}")
            if not engagement_data['engaged_id']:   # different logic for open challenges
                if user_id == engager_id:
                    await query.answer(text="Dat mag jij niet xx 🧙‍♂️", show_alert=True)
                    return
                else:
                    # Update the engagement with the accepting user's id
                    engaged_id = user_id
                    engaged_name = query.from_user.first_name
                    if await check_identical_engagement(engager_id, engaged_id, "challenges", chat_id):
                        emojis = await fetch_live_engagements(chat_id, engaged_id=engaged_id)
                        if "😈" in emojis:
                            await query.answer(text=f"🚫 Jij hebt vandaag al een uitdaging geaccepteerd, no takesie backsies 🧙‍♂️ (zie /stats voor je doel)", show_alert=True)
                            return
                        else:
                            await query.answer(text=f"🚫 Er staat nog een persoonlijk uitdagingsverzoek van {engager_name} naar jou uit, dat je eerst moet afwijzen voordat je deze open uitdaging kunt aannemen 🧙‍♂️", show_alert=True)
                            return
                    live_engagements = await fetch_live_engagements(chat_id, engaged_id=engaged_id)
                    if live_engagements:
                        if "😈" in live_engagements:
                            await query.answer(text=f"🚫 {engaged_name} heeft vandaag al een andere uitdaging geaccepteerd 🧙‍♂️") #88
                            return
                    try:
                        cursor.execute('''
                            UPDATE engagements
                            SET engaged_id = %s
                            WHERE id = %s AND chat_id = %s;
                        ''', (engaged_id, engagement_id, chat_id))
                    except Exception as e:
                        print(f"Error processing open challenge accept in database: {e}")
                        await query.answer("Er is een fout opgetreden bij het verwerken van de acceptatie.")
                        conn.rollback
            if user_id != engaged_id:
                await query.answer(text="Dat mag jij niet xx 🧙‍♂️", show_alert=True)
                print(f"Not allowed, accepting is only for engaged")
                return
                
            # Mark the challenge as "live" using goal, defined above, also check for existing goal and reset that
            try:
                cursor.execute('''
                    UPDATE engagements
                    SET status = 'live'
                    WHERE id = %s AND chat_id = %s;
                ''', (engagement_id, chat_id,))

                # Update the goal stats for the engaged user in the users table
                score = 1 if await fetch_goal_status(update, engaged_id) != 'set' else 0                # don't award point for setting goal if already set
                total_goals_delta = 1 if await fetch_goal_status(update, engaged_id) != 'set' else 0    # don't record additional goal set if already set
                weekly_goals_delta = 1 if await fetch_goal_status(update, engaged_id) != 'set' else 0   # don't charge additional weekly goal if already set
                cursor.execute('''
                    UPDATE users
                    SET today_goal_status = 'set',
                        today_goal_text = %s,
                        set_time = CURRENT_TIMESTAMP,
                        total_goals = total_goals + %s,
                        weekly_goals_left = weekly_goals_left - %s,
                        score = score + %s       
                    WHERE user_id = %s AND chat_id = %s;
                ''', (goal_text, score, weekly_goals_delta, total_goals_delta, engaged_id, chat_id,))
                
                conn.commit()
                await query.answer()
                await query.message.reply_text("😈")
                await query.edit_message_text(
                f"{engaged_name} heeft de uitdaging van [{engager_name}](tg://user?id={engager_id}) geaccepteerd! 🧙‍♂️\n_+1 punt voor {engager_name}_",
                parse_mode="Markdown"
                )
                
            except Exception as e:
                print(f"Error processing accept in database: {e}")
                await query.answer("Er is een fout opgetreden bij het verwerken van de acceptatie.")
                conn.rollback
                # award 1 point for challenger
                cursor.execute('''
                    UPDATE users
                    SET score = score + 1       
                    WHERE user_id = %s AND chat_id = %s;
                ''', (engager_id, chat_id,))
                
                conn.commit()
                
        elif action == 'reject':
            print(f"\n4/4 ({query.data})\nACTION = REJECT\n\nCallback data:{query.data}")
            if user_id != engaged_id:
                print(f"Not allowed, rejection is only for engaged")
                await query.answer(text="Dat mag jij niet xx 🧙‍♂️", show_alert=True)
                return
            print(f"Valid rejection")
            # Return the challenge to the engager + archive
            await add_special(user_id = engager_id, chat_id = chat_id, special_type = "challenges")
            cursor.execute('''
                UPDATE engagements
                SET status = 'archived_unresolved'
                WHERE id = %s AND chat_id = %s;
            ''', (engagement_id, chat_id,))
            conn.commit()
            await query.edit_message_text(
                f"{engaged_name} heeft de uitdaging afgewezen 🧙‍♂️\n_+1😈 voor_ [{engager_name}](tg://user?id={engager_id})",
                parse_mode="Markdown"
            )

    except Exception as e:
        if "Query is too old" in str(e):
            await query.edit_message_text(f"De tijd om te reageren op deze uitdaging is bij deze dan voorbij ([{engaged_name}](tg://user?id={engaged_id}))🧙‍♂️", parse_mode = "Markdown")
        else:
            await query.answer(text="Hm foutje 🧙‍♂️ ???", show_alert=True)
            print(f"Er is een fout opgetreden accepterende: {e}")
    finally:
        cursor.close()
        conn.close()        
    return ConversationHandler.END


async def handle_timeout(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if update.message:
        await update.message.reply_text(f"De tijd voor de uitdaging is bij deze dan verstreken xx 🧙‍♂️")
    else:
        await context.bot.send_message(chat_id, text=f"De tijd voor de uitdaging is bij deze dan verstreken xx 🧙‍♂️")
    return ConversationHandler.END
    # return challenge to engager
