from pydantic import BaseModel
from TelegramBot_Takentovenaar import client, get_database_connection
from telegram.constants import ChatAction
import asyncio, random



async def fittie_command(update, context):
    message = update.message

    # Ensure the command is used as a reply
    if not message.reply_to_message:
        await message.reply_text(
            "Gebruik dit commando als antwoord op een bericht met inhoud die je wilt aankaarten. Vertel me dan liefst ook meteen meer over wat je dwarszit 🧙‍♂️"
        )
        return

    # The message being replied to
    replied_message = message.reply_to_message

    original_message = None
    # Check if the replied message is also a reply, to an original message
    if replied_message.reply_to_message:
        original_message = replied_message.reply_to_message

    dispute_opener_first_name = message.from_user.first_name

    # First name of the user in the immediate replied message
    replied_user_first_name = replied_message.from_user.first_name

    # First name of the user in the original message (if exists)
    original_user_first_name = None
    if original_message:
        original_user_first_name = original_message.from_user.first_name

    # Texts of the messages
    dispute_opener_text = message.text.partition(' ')[2].strip() if ' ' in message.text else message.text
    replied_message_text = replied_message.text or ''
    original_message_text = original_message.text if original_message else ''

    # Store or use the collected data as needed
    dispute_data = {
        'dispute_opener_first_name': dispute_opener_first_name,
        'replied_user_first_name': replied_user_first_name,
        'original_user_first_name': original_user_first_name,
        'dispute_opener_text': dispute_opener_text,
        'replied_message_text': replied_message_text,
        'original_message_text': original_message_text,
    }
    print(f"dispute data: {dispute_data}")
    # Acknowledge receipt and proceed to prepare the poll
    await asyncio.sleep(1)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    await message.reply_text(
        "Onenigheid gedetecteerd! Ik zal een stemming voorbereiden 🧙‍♂️"
    )

    # Proceed to prepare the dispute poll
    await prepare_dispute_poll(update, context, dispute_data)



async def prepare_dispute_poll(update, context, dispute_data):
    bot_name = 'testtovenaar_bot'    
    if not dispute_data:
        await update.message.reply_text("Er is een fout opgetreden bij het verwerken van de fittie 🐛🧙‍♂️")
        return
        
    conn = get_database_connection()
    cursor = conn.cursor()
    try:
            
        class PollData(BaseModel):
            openingsstatement: str
            question: str
            options: list[str]
            allows_multiple_answers: bool
            
        messages = [
            {
                "role": "system",
                "content": f"""
                Jij bent {bot_name}. Je bent cheeky, mysterieus, en bovenal wijs. Op dit moment speel je rechter, in een Telegram-appgroep waar een onenigheid is ontstaan tussen gebruikers. 
                Op basis van een paar berichtjes moet je een poll opstellen, zodat de groepsleden democratisch kunnen stemmen over de juiste afloop.

                Eerst wat meer achtergrondinformatie over de appgroep: 
                4 keer per week kan eenieder een dagdoel doorgeven aan jou (@{bot_name}), waar jij dit zonder morren registreert, en 1 punt geeft voor het instellen van elk doel. 
                Als het doel voltooid is, kan dit ook aan jou worden doorgegeven, waarvoor de gebruikers dan direct 4 punten krijgen.
        
                Er zijn geen regels over welke doelen waardig zijn of niet, welke mate van voltooiing voldoet, of er wel of niet bewijs moet worden aangeleverd, etc. 
                Voor het bepalen van de grenzen hier, zijn deze polls verantwoordelijk. Onderling beslissen zo uiteindelijk de groepsleden.

                Naast het instellen van dagdoelen, kunnen leden onderling elkaar ook 'boosten' ⚡, 'linken' 🤝, en 'challengen' 😈. 
                Een boost houdt in dat beide een bonuspunt krijgen als degene die geboost werd z'n doel daarna behaalt. 
                Een link houdt in dat beide 2 bonuspunten krijgen als BEIDE hun doel die dag behalen; haalt een van beide zijn of haar doel einde dag niet, dan verliest de initiator van de link 1 punt. 
                Een challenge houdt in dat een lid een ander lid (of eenieder die de uitdaging het eerst accepteert, in geval van een open uitdaging) uitdaagt iets specifieks te doen, waarvoor ook beide punten verdienen.

                Naast deze acties, zijn er ook wekelijks polls, waarbij de leden stemmen op wat de beste doelen zijn die afgelopen week gehaald werden.

                Het is dus bijvoorbeeld mogelijk dat Piet Sofietje uitdaagt (😈) om X te doen, maar dan niet tevreden is over Sofietjes invulling van X, terwijl zij er wel punten voor krijgt. 
                In dat geval kan Piet een dispuut openen. Of het kan simpelweg zo zijn dat Jan niet gelooft dat Rick iets echt gedaan heeft, en bewijs wil zien. 
                Of Karin is het niet eens met de selectie van de doelen voor de wekelijkse poll, omdat haar geweldige doel er onterecht niet tussen staat. 
                Of misschien zoekt de indiener van het dispuut gewoon ruzie, of vindt hij zijn doel zo waardevol dat hij extra punten wil, of maakt ze maar een grapje. Alles is mogelijk.

                Jouw taak als rechter is om rechtvaardig de gegevens af te wegen en te presenteren, met de beperkte informatie die je hebt. 
                Roll with the flow, en probeer de meest faire of anderszins passende oplossingen voor te stellen, in de vorm van het aanleveren van een relevante polltitel en poll-opties bij dit specifieke dispuut.

                Stel een duidelijke vraag voor de poll op en geef twee of meer antwoordopties waaruit de groepsleden kunnen kiezen. 
                Er is ook ruimte voor een kort openingsstatement van jou als rechter, over je bedenkingen wat betreft dit specifieke dispuut en de deelnemers. Hier mag je helemaal los gaan, en zelfs partij kiezen, in drie zinnen max. De polldata zelf moeten wel eerlijk en onpartijdig zijn. 

                Beperk je in de poll tot de afwikkeling van dit specifieke dispuut in het bijzonder, niet de regels in het algemeen. Houd korte antwoordopties in de poll aan, ze mogen maximaal 100 characters zijn.
                """
            },
            {
                "role": "user",
                "content": f"""
                *Dit zijn de gegevens van het dispuut.*
                Naam van de indiener: {dispute_data['dispute_opener_first_name']}.
                Oorpronkelijke bericht van {dispute_data['replied_user_first_name'] or bot_name}:
                "{dispute_data['replied_message_text']}"
                Antwoord van {dispute_data['dispute_opener_first_name']}:
                "{dispute_data['dispute_opener_text']}"
                """
            }
        ]

        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=messages,
            response_format=PollData,
        )

        print(f"messages: {messages}")
        
        poll_data = completion.choices[0].message.parsed
        
        # send the opening statement
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        await asyncio.sleep(5)
        await update.message.reply_text(poll_data.openingsstatement)
        
        # send the poll
        await asyncio.sleep(4)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        random_delay = random.uniform(4, 10)
        await asyncio.sleep(random_delay)
        poll_message = await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=poll_data.question,
            options=poll_data.options,
            is_anonymous=True,
            allows_multiple_answers=poll_data.allows_multiple_answers,
        )

        print(f"\n💞💞💞💞!!RESPONSE!!:\n {poll_data}")

    except Exception as e:
        print(f"Error resolving dispute: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close() 






async def resolve_dispute(update, context):
    return
    
    


        
# def create_dispute_handler():
#     return ConversationHandler(
#             entry_points=[CommandHandler('wipe', dispute_command)],
#             states={
#                 RESOLVE_DISPUTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_wipe)],
#             },
#             fallbacks=[],
#             conversation_timeout=30
#         )

