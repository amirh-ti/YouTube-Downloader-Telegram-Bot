# File: get_session.py
import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    print("=" * 50)
    print("   Telegram Session String Generator")
    print("=" * 50)
    
    # Get API_ID from user
    while True:
        try:
            api_id_input = input("\nEnter your API_ID (from my.telegram.org): ").strip()
            if not api_id_input:
                print("API_ID cannot be empty! Please try again.")
                continue
            API_ID = int(api_id_input)
            break
        except ValueError:
            print("Invalid API_ID! Please enter a number.")
    
    # Get API_HASH from user
    while True:
        API_HASH = input("Enter your API_HASH (from my.telegram.org): ").strip()
        if API_HASH:
            break
        print("API_HASH cannot be empty! Please try again.")
    
    print("\nYou will be asked to enter your phone number and verification code.")
    print("Make sure you have Telegram installed on your phone.\n")
    
    try:
        # Create client with StringSession
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.start()
        
        # Get session string
        session_string = client.session.save()
        
        # Display the session string
        print("\n" + "=" * 50)
        print("Your Session String:")
        print("=" * 50)
        print(f"\n{session_string}\n")
        print("=" * 50)
        
        # Save to file
        with open("session.txt", "w") as f:
            f.write(session_string)
        
        print("Session string saved to 'session.txt' file!")
        
        # Show file location
        file_path = os.path.abspath("session.txt")
        print(f"File location: {file_path}")
        
        # Get user info
        me = await client.get_me()
        print(f"\nLogged in as: {me.first_name} (@{me.username if me.username else 'no username'})")
        
    except Exception as e:
        print(f"\nError: {e}")
        print("Please check your API_ID, API_HASH, and internet connection.")
        return
    
    print("\n" + "=" * 50)
    print("Done! You can now use this session string in your bot.")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
