import aiohttp
import json
import os
import random
from dotenv import load_dotenv

load_dotenv()
# CHANGE THIS TO YOUR GROQ KEY
API_KEY = os.getenv("GROQ_API_KEY")

FALLBACKS = [""]


async def generate_battle_narration(attacker, defender, terrain, outcome, unit_type):
    # """
    # Asynchronously sends battle data to Groq (LPU Inference) for instant results.
    # """
    # if not API_KEY:
    #     return random.choice(FALLBACKS)

    # prompt = f"""
    # You are a gritty historian writing about a brutal war in Westeros.
    # Write ONE vivid, atmospheric sentence (max 30 words) describing a specific moment in a battle.

    # CONTEXT:
    # - Attacker: House {attacker}
    # - Defender: House {defender}
    # - Location: {terrain}
    # - Key Unit: {unit_type}
    # - Outcome: {outcome}

    # STYLE: No flowery intro. Just the action. Gritty, cinematic, and dark.
    # Example: 'The mud of the Trident turned red as Stark vanguards crashed into the Lannister center.'
    # """

    # # Strict timeout (Groq is usually done in < 1 second)
    # timeout = aiohttp.ClientTimeout(total=4)

    # try:
    #     async with aiohttp.ClientSession(timeout=timeout) as session:
    #         async with session.post(
    #             url="https://api.groq.com/openai/v1/chat/completions",
    #             headers={
    #                 "Authorization": f"Bearer {API_KEY}",
    #                 "Content-Type": "application/json",
    #             },
    #             json={
    #                 # CHANGE THIS LINE
    #                 "model": "llama-3.1-8b-instant",
    #                 "messages": [{"role": "user", "content": prompt}],
    #                 "temperature": 0.7,
    #                 "max_tokens": 60,
    #             },
    #         ) as response:
    #             if response.status == 200:
    #                 result = await response.json()
    #                 narration = (
    #                     result["choices"][0]["message"]["content"].strip().strip('"')
    #                 )

    #                 if narration:
    #                     return narration
    #                 else:
    #                     return random.choice(FALLBACKS)
    #             else:
    #                 print(f"Groq Error: {response.status}")
    #                 return random.choice(FALLBACKS)

    # except Exception as e:
    #     # print(f"API Request Failed: {e}")
    #     return random.choice(FALLBACKS)

    return ""
