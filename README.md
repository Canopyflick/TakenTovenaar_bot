# 💩 After cloning, to install on rpi:
- Step 1: Oh, I forgot
- Step 2: Ask ChatGPT
- Step 3: Fuck around
- Step 4: Look for profit
- Step 5: ???
- Step 6: Reverse steps 4 and 5, then edit step 5
- Step 7: Find out
- Step 8: Repeat

# ✍️ At some point between step 1 and 7, you need to:
- create a venv: python3 -m venv venv
- activate it: source venv/bin/activate
- Install Postgres: sudo apt install libpq-dev
- Install requirements from venv: pip install -r requirements.txt
- Create .env: nano .env
- Add secretes: OPENAI_API_KEY, LOCAL_DATABASE_URL, LOCAL_TELEGRAM_BOT_TOKEN
- Enter Postgres: sudo -u postgres psql
- Add mydb: CREATE DATABASE mydb;
- ALTER USER postgres PASSWORD 'yourpassword';
- exit postgres ('exit')
- python TakenTovenaar_bot.py 🧙
