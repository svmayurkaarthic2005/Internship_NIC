import asyncio
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.database import get_db
from backend.services.chatbot import process_chat

async def main():
    async for db in get_db():
        r = await process_chat("survey number correct ah irukka?", "00000000-0000-0000-0000-000000000001", None, db)
        print("INTENT:", r.get("intent"))
        print("LANG:", r.get("language"))
        print("RESPONSE:", r.get("response"))
        break

if __name__ == "__main__":
    asyncio.run(main())
