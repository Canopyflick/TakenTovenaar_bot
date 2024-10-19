﻿from TelegramBot_Takentovenaar import get_first_name
from utils import add_special, conn, cursor, escape_markdown_v2, get_random_philosophical_message, show_inventory, check_chat_owner, check_use_of_special, fetch_live_engagements, fetch_goal_text, has_goal_today, send_openai_request, prepare_openai_messages, fetch_goal_status


# Asynchronous command functions
async def start_command(update, context):
        await update.message.reply_text('Hoi! 👋🧙‍♂️\n\nIk ben Taeke Toekema Takentovenaar. Stuur mij berichtjes, bijvoorbeeld om je dagdoel in te stellen of te voltooien, of me te vragen waarom bananen krom zijn. Antwoord op mijn berichten of tag me, bijvoorbeeld zo:\n\n"@TakenTovenaar_bot ik wil vandaag 420 gram groenten eten" \n\nDruk op >> /help << voor meer opties.')


async def help_command(update, context):
    help_message = (
        '*Dit zijn de beschikbare commando\'s*\n'
        '👋 /start - Begroeting\n'
        '❓/help - Dit lijstje\n'
        '📊 /stats - Je persoonlijke stats\n'
        '🤔 /reset - Pas je dagdoel aan\n'
        '🗑️ /wipe - Wis je gegevens in deze chat\n'
        '🎒 /inventaris - Acties paraat?\n'
        '🏹 /acties - Uitleg over acties\n'
        '💭 /filosofie - Laat je inspireren'
    )
    await update.message.reply_text(help_message, parse_mode="Markdown")
    

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

        stats_message = f"*Statistieken van {escaped_first_name}*\n"
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
            if await fetch_live_engagements(engaged_id = user_id):
                print(f"user_id: {user_id}")
                escaped_emoji_string = await fetch_live_engagements(engaged_id = user_id)
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
        if user_id == update.effective_user.id:
            await update.message.reply_text(
            escape_markdown_v2("Je hebt nog geen statistieken. \nStuur me een berichtje met je dagdoel om te beginnen 🧙‍♂️\n(/start)"),
            parse_mode="MarkdownV2")
        else:
            await update.message.reply_text(escape_markdown_v2(f"{first_name} heeft nog geen statistieken. \nBegin met het instellen van een doel 🧙‍♂️\n(/start)"),
            parse_mode="MarkdownV2")


async def reset_command(update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if has_goal_today(user_id, chat_id):
        # don't allow resets if challenged
        if await fetch_live_engagements(engaged_id = user_id):
            if "😈" in await fetch_live_engagements(engaged_id = user_id):
                goal_text = fetch_goal_text(update, context)   
                await update.message.reply_text(f"Challenge reeds accepted. 😈 Er is geen weg meer terug 👻🧙‍♂️\n_{goal_text}_", parse_mode = "Markdown")        
                return False
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


async def inventory_command(update, context):
    await show_inventory(update, context)
    

async def acties_command(update, context):
    acties_message = (
        '*Alle beschikbare acties*\n'
        '⚡ */boost* - Boost andermans doel, verhoog de inzet!\n\n'
        '😈 */challenge* - Daag iemand uit: "Doe vandaag X!"\n\n'
        '🔗 */link* - Verbind je lot met een ander... (🚧)\n\n'
        '*Zo zet je ze in*\n'
        'Gebruik je actie door met het passende commando op een berichtje van je doelwit '
        'te reageren, of hen na het commando te taggen:\n\n'
        '- _/boost @Willem_\n\n'
        '- _/challenge @Josefietje om voor 11u weer thuis te zijn voor de koffie_\n\n'
        'Druk op >> /details << voor meer info over acties.\n\n'
        '🚧 _under construction, werkt nog niet_'
    )
    await update.message.reply_text(acties_message, parse_mode="Markdown")
    

async def details_command(update, context):
    details_message = (
        '*Extra uitleg over de acties*\n\n'
        '⚡ Met een *boost* krijgen jij en je doelwit *+1* punt als zij hun doel halen.\nHalen zij hun doel die dag niet, dan krijg jij je boost terug.\n\n'
        '😈 *Challenge* iemand om iets specifieks te doen vandaag. Jij krijgt *+1* punt zodra de uitdaging geaccepteerd wordt, en zij overschrijven hun dagdoel (als ze dat '
        'al ingesteld hadden.) De ander krijgt *+2* punten bij voltooiing.\nWordt de uitdaging niet geaccepteerd, dan krijg jij je challenge-actie terug.\n\n '
        '🔗(🚧) Een *link* verbindt jouw doel met dat van een ander. Nu moeten jullie allebei je dagdoel halen om *+2* punten bonus pp te verdienen.\nLukt dit '
        'een van beiden niet, dan verlies jij *-1* punt (en zij hun kans op bonus).\n\n'
        '🚧_under construction_'
    )
    await update.message.reply_text(details_message, parse_mode="Markdown")



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
            arg = context.args[0]
            if arg.isdigit():  # If the argument is a number, treat it as an amount
                amount = int(context.args[0])
                await handle_admin(update, context, 'steal', amount)
                return
            else: # If the arguments is a string, treat it as a special_type
                special_type = arg
                user_id = update.message.reply_to_message.from_user.id
                first_name = await get_first_name(context, user_id)
                chat_id = update.effective_chat.id
                print(f"Special type stolen = {special_type}")
                await add_special(user_id, chat_id, special_type, -1)
                await update.message.reply_text(f"Taeke Takentovenaar grist weg 🥷\n_-1 {special_type} van {first_name}_", parse_mode = "Markdown")
                return
    else:
        message = get_random_philosophical_message()
        await update.message.reply_text(message)
        
        
async def revert_goal_completion_command(update, context):
    if await check_chat_owner(update, context):
        user_id = None
        # Check the target's goal in case of reply
        if update.message.reply_to_message:
            user_id = update.message.reply_to_message.from_user.id
            if await fetch_goal_status(update, user_id) == 'Done':    
                await handle_admin(update, context, 'revert')
                return False
        if await fetch_goal_status(update) == 'Done':    
            await handle_admin(update, context, 'revert')
            return False
    else:
        message = get_random_philosophical_message()
        await update.message.reply_text(message)
        

async def handle_admin(update, context, type, amount=None):
    print(f"entering handle_admin_command")
    try:
        user_id = update.message.reply_to_message.from_user.id
    except Exception as e:
        print(f"Error. Uitdelen kan alleen als reply: {e}")
        return False
    first_name = update.message.reply_to_message.from_user.first_name
    chat_id = update.effective_chat.id
    valid_special_types = ['boosts', 'links', 'challenges']
    if type == 'gift':
        try:
            cursor.execute('''
                            UPDATE users 
                            SET score = score + %s
                            WHERE user_id = %s AND chat_id = %s
                            ''', (amount, user_id, chat_id))
            conn.commit()
            await update.message.reply_text(f"Taeke Takentovenaar deelt uit 🎁🧙‍♂️\n_+{amount} punt(en) voor {first_name}_", parse_mode = "Markdown")
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
            await update.message.reply_text(f"Taeke Takentovenaar grist weg 🥷\n_-{amount} punt(en) van {first_name}_", parse_mode = "Markdown")
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
            await update.message.reply_text(f"Whoops ❌ \nTaeke Takentovenaar draait voltooiing van {first_name} terug 🧙‍♂️\n_-4 punten, doelstatus weer: 'ingesteld'_"
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
                emoji_mapping = {
                    'boosts': '⚡',
                    'links': '🔗',
                    'challenges': '😈'
                }

                # Get the emoji for the given special_type
                special_type_emoji = emoji_mapping.get(special_type, '')
                await update.message.reply_text(f"Taeke Takentovenaar deelt uit 🧙‍♂️\n_+1{special_type_emoji} voor {first_name}_", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")
                conn.rollback()
                return


async def boost_command(update, context):
    await check_use_of_special(update, context, special_type="boosts")
    return


async def link_command(update, context):
    await update.message.reply_text("🚧 Links werken nog niet 🧙‍♂️")
    print("UNDER CONSTRUCTION")
    return
    # await check_use_of_special(update, context, 'links')
    # return


async def ranking_command(update, context):
    await update.message.reply_text("🚧 Wordt aan gewerkt 🧙‍♂️")
    print("UNDER CONSTRUCTION")
    return



