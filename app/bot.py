# bot.py
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from app.services.pathfinder_bot_engine import (
    Pathfinder,
    DATA_FILE,
    COST_MAP_FILE,
    MAP_FILE,
)
from app.db.db_manager import init_db
from app.db.db_manager import init_db, get_session_factory
import traceback  # <-- ADD THIS IMPORT

# 1. Load Environment Variables (for your token)
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# 2. Define Bot Intents
# These are the permissions your bot needs to function.
intents = discord.Intents.default()
intents.message_content = True  # Allows the bot to read message content

# 3. Create the Bot Instance
# We will attach our pathfinder engine to this 'bot' object.
bot = commands.Bot(command_prefix="!", intents=intents)


# 4. Define the Asynchronous Setup Hook
# This is the modern way to handle startup tasks in discord.py
@bot.event
async def setup_hook():
    print("--- ⚔️  WesterosBot Initializing ⚔️  ---")
    print(f"--- ⚔️  BOT CWD: {os.getcwd()} ⚔️  ---")

    # 1. Connect to Database (Just connection, no world generation)
    print("Connecting to Database...")
    await init_db()

    # 2. Initialize Pathfinder Engine (The Map Logic)
    print("Initializing Pathfinder Engine...")
    bot.pathfinder = Pathfinder(
        data_file=DATA_FILE, cost_map_file=COST_MAP_FILE, map_file=MAP_FILE
    )
    print("✅ Pathfinder Engine Ready.")

    # =================================================================
    # THE CORRECTED, AGGRESSIVE LOGGING BLOCK
    # =================================================================
    # 3. Load Cogs (Commands)
    print("\n--- AGGRESSIVE COG LOADING ---")
    cogs_path = os.path.join(os.path.dirname(__file__), "cogs")
    print(f"Searching for cogs in: {os.path.abspath(cogs_path)}")

    if not os.path.isdir(cogs_path):
        print(f"FATAL: Cog directory not found at {cogs_path}")
        return

    for filename in os.listdir(cogs_path):
        if filename.endswith(".py") and not filename.startswith("__"):
            cog_name = f"app.cogs.{filename[:-3]}"
            print(f"  -> Attempting to load '{cog_name}'...")
            try:
                await bot.load_extension(cog_name)
                print(f"    ✅ SUCCESS: Cog '{cog_name}' loaded.")
            except Exception as e:
                print(f"    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                print(f"    ❌ FAILED to load cog '{cog_name}'.")
                print(f"    Error: {type(e).__name__} - {e}")
                # Print the full, detailed traceback to the console
                traceback.print_exc()
                print(f"    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print("--- FINISHED COG LOADING ---\n")
    # =================================================================

    print("--- ✅ Setup Complete ---")


# 5. Define the on_ready Event
# This fires after the bot has successfully connected to Discord.
@bot.event
async def on_ready():
    """Confirms that the bot is online and connected."""
    print(f"\nSUCCESS! Logged in as {bot.user}")
    print(f"Ready to accept commands in servers.")
    print("-----------------------------------")


# 6. Run the Bot
# This is the final step that starts the bot's connection loop.
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: BOT_TOKEN not found in .env file. Bot cannot start.")
