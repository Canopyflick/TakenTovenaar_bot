from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, ConversationHandler, CallbackQueryHandler, CallbackContext
from utils import conn, cursor, check_use_of_special, add_special, send_openai_request, fetch_goal_status
from datetime import timedelta


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
    chat_id = update.effective_chat.id
    try:
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
    
    await add_special(engager_id, chat_id, "challenges", -1)   

    challenge_goal = update.message.text.partition(" ")[2]  # Get the goal part from the challenge command
    if user_mentioned:
        # Split the challenge_goal into words, remove the first word, and rejoin the remaining part
        challenge_goal = " ".join(challenge_goal.split()[1:])

    print(f"Received message text: {update.message.text}\n")

    print(f"dit is challenge_goal: {challenge_goal}")
    print(f"dit is engaged_id 2/3: {engaged_id}")
    # Call OpenAI API to rephrase the challenge goal
    messages=[
        {"role": "system", "content": "Herformuleer uitdagingen van {engager_name} aan {engaged_name} naar een opdracht voor vandaag."},
        {"role": "user", "content": "@{engaged_name} om me vandaag een massage te geven"},
        {"role": "assistant", "content": "Geef {engager_name} vandaag een massage."},
        {"role": "user", "content": "stuur een berichtje naar je vriendin"},
        {"role": "assistant", "content": "Stuur vandaag een berichtje naar je vriendin."},
        {"role": "user", "content": "weet je wat mij nou een goed idee lijkt? Als jij eindelijk eens ff reageert op die datumprikker die al 2 weken uitstaat (je bent de enige die nog niet heeft gereageerd xoxo)"},
        {"role": "assistant", "content": "Reageer vandaag op de datumprikker die al 2 weken uitstaat."},
        {"role": "user", "content": "verpot vandaag alle planten."},
        {"role": "assistant", "content": "Verpot vandaag alle planten."},
        {"role": "user", "content": "geef me een cadeautje"},
        {"role": "assistant", "content": "Geef vandaag een cadeautje aan {engager name}."},
        {"role": "user", "content": challenge_goal}
    ] 
    rephrased_goal = await send_openai_request(messages, temperature = 0.1)  # Extract the rephrased goal from OpenAI response
    print(f"****Rephrased challenge prompt: \n\n{messages}\n\nRephrased goal: {rephrased_goal}\n\n")
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
        
        challenge_message = f"😈 {engager_name} daagt {engaged_name} uit:\n\n_{rephrased_goal}_"
        # tag engaged here if they weren't tagged when engager initialized the challenge
        if not user_mentioned:
            challenge_message = f"😈 {engager_name} daagt [{engaged_name}](tg://user?id={engaged_id}) uit:\n\n_{rephrased_goal}_"
            
        await update.message.reply_text(challenge_message, parse_mode = "Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👿 Uitdaging intrekken", callback_data=f'retract_{engagement_id}')],
                [InlineKeyboardButton("👍 Accepteren", callback_data=f'accept_{engagement_id}'), InlineKeyboardButton("👎 Weigeren", callback_data=f'reject_{engagement_id}')]
            ])
        )
    else:
        await update.message.reply_text("Er is een fout opgetreden bij het herformuleren van de uitdaging 👿")
    


# async def confirm_challenge(update, context):
#     query = update.callback_query
#     await query.answer()
#     print(f"1/5\nButton pressed by {query.from_user.id}, we're inside confirm_challenge.\nCallback_data: {query.data}")
#     if not query:
#         print("No callback query found in the update.")
#         return ConversationHandler.END
#     user_id = query.from_user.id  # The ID of the user who pressed the button
#     chat_id = update.effective_chat.id
#     callback_data = query.data.split('_')  # This will contain something like 'accept_95'

#     # Validate that the callback data contains the expected format (action and engagement_id)
#     if len(callback_data) < 2:
#         await query.message.reply_text("Er is een fout opgetreden: Ongeldig callback-gegevensformaat.")
#         return ConversationHandler.END

#     # Store necessary data in context for handle_challenge_response
#     context.chat_data['current_action'] = callback_data[0]
#     context.chat_data['current_engagement_id'] = int(callback_data[1])
#     cursor.execute('''
#         SELECT COUNT(*) FROM engagements WHERE chat_id = %s
#     ''', (chat_id,))
#     engagement_count = cursor.fetchone()[0]
#     print(f"\n(((Currently active engagements: {engagement_count})))\n")
#     print(f"2/5\nnow we should enter HANDLE_RESPONSE ({query.data})")
#     return HANDLE_RESPONSE

async def handle_challenge_response(update, context):
    query = update.callback_query
    await query.answer()
    print(f"1/4 ({query.data})\nButton pressed by {query.from_user.id}, we're inside handle_challenge_response.")
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    callback_data = query.data.split('_')  # ['accept', '88']
    print(f"callback data = {callback_data}")
    
    # Validate callback data
    if len(callback_data) < 2:
        await query.message.reply_text("Er is een fout opgetreden: Ongeldig callback-gegevensformaat.")
        return
    
    # Get the action and engagement_id
    action = callback_data[0]
    engagement_id = int(callback_data[1])
    print(f"\n2/4 ({query.data})\n unpacking action as: {action} | unpacking engagement_id as {engagement_id}")
    
    # Get engagement data from context
    engagement_data = context.chat_data.get(engagement_id)
    if not engagement_data:
        await query.message.reply_text("🚫 Er is een fout opgetreden bij het ophalen van de uitdaging, challenge afgebroken. Probeer het opnieuw 🦗🧙‍♂️")
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
        await query.answer()

        if action == 'retract':
            print(f"\n4/4 ({query.data})\nACTION = RETRACT\n\nCallback data:{query.data}")
            if user_id != engager_id:
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
                
                await add_special(engager_id, chat_id, "challenges")
                await query.edit_message_text(
                    f"{engager_name} heeft de uitdaging aan [{engaged_name}](tg://user?id={engaged_id}) ingetrokken\n_+1 challenge 😈_",
                    parse_mode="Markdown"
                )
                print(f"Engagement archived: chat_id={chat_id}, engagement_id={engagement_id}")
                
            except Exception as e:
                print(f"Error archiving challenge: {e}")
                await query.message.reply_text("Er is een fout opgetreden bij het intrekken van de uitdaging 🐛🧙‍♂️")
                conn.rollback()

        elif action == 'accept':
            print(f"\n4/4 ({query.data})\nACTION = ACCEPT\n\nCallback data:{query.data}")
            if user_id != engaged_id:
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
                score = 1 if await fetch_goal_status(update, engaged_id) != 'set' else 0  # don't award point for setting goal if already set
                total_goals_delta = 1 if await fetch_goal_status(update, engaged_id) != 'set' else 0  # don't record additional goal set if already set
                
                cursor.execute('''
                    UPDATE users
                    SET today_goal_status = 'set',
                        today_goal_text = %s,
                        set_time = CURRENT_TIMESTAMP,
                        total_goals = total_goals + %s,
                        score = score + %s       
                    WHERE user_id = %s AND chat_id = %s;
                ''', (goal_text, score, total_goals_delta, engaged_id, chat_id,))
                
                conn.commit()
                await query.edit_message_text(
                    f"{engaged_name} heeft de uitdaging van [{engager_name}](tg://user?id={engager_id}) geaccepteerd! 🧙‍♂️",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Error processing accept in database: {e}")
                await query.answer("Er is een fout opgetreden bij het verwerken van de acceptatie.")
                conn.rollback
                
        elif action == 'reject':
            print(f"\n4/4 ({query.data})\nACTION = REJECT\n\nCallback data:{query.data}")
            if user_id != engaged_id:
                print(f"Not allowed, rejection is only for engaged")
                return
            print(f"Valid rejection")
            # Return the challenge to the engager
            await add_special(user_id = engager_id, chat_id = chat_id, special_type = "challenges")
            await query.edit_message_text(
                f"{engaged_name} heeft de uitdaging afgewezen 🧙‍♂️\n_+1😈 voor [{engager_name}](tg://user?id={engager_id})_",
                parse_mode="Markdown"
            )

    except Exception as e:
        if "Query is too old" in str(e):
            await query.edit_message_text(f"De tijd om te reageren op deze uitdaging is bij deze dan voorbij ([{engaged_name}](tg://user?id={engaged_id}))🧙‍♂️", parse_mode = "Markdown")
        else:
            await query.edit_message_text(f"Er is een fout opgetreden: {e}. Probeer het opnieuw ofzo..? 🧙‍♂️")
            
    return ConversationHandler.END


async def handle_timeout(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if update.message:
        await update.message.reply_text(f"De tijd voor de uitdaging is bij deze dan verstreken xx 🧙‍♂️")
    else:
        await context.bot.send_message(chat_id, text=f"De tijd voor de uitdaging is bij deze dan verstreken xx 🧙‍♂️")
    return ConversationHandler.END
    # return challenge to engager
