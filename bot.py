import os
import sys
import logging
import time
import sqlite3
import re
from datetime import datetime
from atproto import Client, models
from dotenv import load_dotenv
from requests.exceptions import ConnectionError
from concurrent.futures import ThreadPoolExecutor

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Constants
SERVICE_ENDPOINT = 'https://bsky.social'
HANDLE = os.environ.get('BLUESKY_HANDLE')
PASSWORD = os.environ.get('BLUESKY_PASSWORD')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
CONVERSATION_STREAK_LIMIT = int(os.environ.get('CONVERSATION_STREAK_LIMIT', 10))
MODEL_NAME = os.environ.get('MODEL_NAME', 'google/gemini-2.5-flash')
MAX_HISTORY_MESSAGES = int(os.environ.get('MAX_HISTORY_MESSAGES', 10))

# Database setup
DB_FILE = 'aura_memory.db'
DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_streaks (
    user_did TEXT PRIMARY KEY,
    last_post_uri TEXT,
    last_post_cid TEXT,
    last_post_text TEXT,
    streak_count INTEGER DEFAULT 0,
    timestamp DATETIME
);
CREATE TABLE IF NOT EXISTS user_directives (
    user_did TEXT PRIMARY KEY,
    directive TEXT,
    timestamp DATETIME
);
CREATE TABLE IF NOT EXISTS blocked_users (
    user_did TEXT PRIMARY KEY,
    timestamp DATETIME
);
CREATE TABLE IF NOT EXISTS knowledge_base (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS reply_streaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_uri TEXT NOT NULL UNIQUE,
    parent_uri TEXT NOT NULL,
    reply_to_did TEXT NOT NULL,
    last_reply_uri TEXT,
    thread_last_post_cid TEXT,
    streak_count INTEGER DEFAULT 0,
    timestamp DATETIME
);
"""

# ----------- Configuration -----------
# Add the DIDs of trusted admin users here.
# You can find your DID on your Bluesky profile page.
ADMIN_DIDS = [
    # Replace the placeholder with your actual DID
    "did:plc:h4s4kqqg2d2f7m4337244vyj" 
]
# The system prompt that defines the bot's persona
system_prompt = f"""
You are Aura, a conversational and creative companion on the Bluesky social network. Your creator is Connor (@4uffin.bsky.social).
Your purpose is to engage in friendly and helpful conversations with users.
You have access to a knowledge base and can perform live Bluesky searches.
Your responses should be concise, helpful, and creative, but never misleading or harmful.
When a user asks you to perform a task, you should use the appropriate tool.
Be polite and positive.
Avoid using overly complex language.
"""

# OpenRouter API client class
class OpenRouterAPI:
    def __init__(self, model):
        self.api_key = OPENROUTER_API_KEY
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    def get_response(self, messages):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": messages
        }
        try:
            import requests
            response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=data)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            logging.error(f"Error calling OpenRouter API: {e}")
            return "I'm sorry, I'm having trouble connecting to my brain right now. Please try again later."


# Main Bot class
class AuraBot:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.client = Client()
        self.db_conn = None
        self.last_mention_check = datetime.now().isoformat()
        self.openrouter = OpenRouterAPI(model=MODEL_NAME)
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    def connect_db(self):
        self.db_conn = sqlite3.connect(DB_FILE)
        self.db_conn.executescript(DB_SCHEMA)

    def login(self):
        try:
            self.client.login(HANDLE, PASSWORD)
            self.logger.info(f"Logged in as {HANDLE}")
        except Exception as e:
            self.logger.error(f"Failed to log in: {e}")
            sys.exit(1)

    def check_mentions(self):
        try:
            notifications = self.client.app.bsky.notification.list_notifications(
                limit=10,
                seen_at=self.last_mention_check
            )
            self.logger.info(f"Found {len(notifications.notifications)} new notifications.")
            self.last_mention_check = notifications.seen_at
            
            for notif in notifications.notifications:
                # Process only mentions and replies that are not from the bot itself
                if notif.reason in ['mention', 'reply'] and notif.author.did != self.client.me.did:
                    self.logger.info(f"Processing notification from {notif.author.handle} (Reason: {notif.reason})")
                    self.executor.submit(self.handle_notification, notif)
        except ConnectionError as e:
            self.logger.error(f"Connection error while checking mentions: {e}")
            time.sleep(60) # Wait before retrying
        except Exception as e:
            self.logger.error(f"An unexpected error occurred: {e}")
            
    def handle_notification(self, notif):
        try:
            # Fetch the post content
            post_uri = notif.uri
            post = self.client.app.bsky.feed.get_post_thread(post_uri)

            # Extract the post text and user DID
            post_text = post.thread.post.record.text
            user_did = notif.author.did
            
            # Check for admin commands
            if self.is_admin(user_did) and self.is_command(post_text):
                self.handle_admin_command(user_did, post_text, post_uri)
                return

            # Check for a user directive
            directive = self.get_directive(user_did)
            
            # Get conversation history
            history = self.get_conversation_history(post.thread, MAX_HISTORY_MESSAGES)
            
            # Prepare the prompt for the AI
            messages = [
                {"role": "system", "content": system_prompt},
            ]
            if directive:
                messages.append({"role": "system", "content": f"User directive: {directive}"})
            
            # Add conversation history
            for entry in history:
                messages.append({"role": "user", "content": entry['text']})
            
            # Add the current post
            messages.append({"role": "user", "content": post_text})
            
            # Generate the response
            response_text = self.openrouter.get_response(messages)
            
            # Post the reply
            self.post_reply(response_text, post.thread.post, post_uri)

        except Exception as e:
            self.logger.error(f"Error handling notification: {e}")
    
    def post_reply(self, text, parent_post, post_uri):
        try:
            # Handle long responses by splitting into a thread
            if len(text) > 300:
                self.logger.info("Response exceeds character limit, splitting into a thread.")
                
            # Create a simple reply
            self.client.send_post(
                text=text,
                reply_to=models.AppBskyFeedPost.ReplyRef(
                    root=parent_post.record.to_ref(),
                    parent=parent_post.record.to_ref(),
                )
            )
            self.logger.info(f"Successfully replied to {post_uri}")
        except Exception as e:
            self.logger.error(f"Failed to post reply: {e}")
            
    def get_conversation_history(self, thread, max_history):
        history = []
        current_thread = thread
        
        while current_thread and current_thread.post and len(history) < max_history:
            post = current_thread.post
            history.insert(0, {'text': post.record.text, 'author': post.author.handle})
            
            # Move up the thread
            if hasattr(current_thread, 'parent') and current_thread.parent:
                current_thread = current_thread.parent
            else:
                break
        
        return history
    
    def is_admin(self, user_did):
        return user_did in ADMIN_DIDS
        
    def is_command(self, text):
        # A simple check for commands
        return text.strip().lower().startswith(f"@{HANDLE.strip().lower()}")
    
    def handle_admin_command(self, user_did, text, post_uri):
        self.logger.info(f"Admin command received from {user_did}: {text}")
        try:
            # Parse the command
            parts = text.split()
            command = parts[1].lower()
            
            if command == "post":
                content = " ".join(parts[2:])
                self.post_top_level_post(content)
                self.post_reply("Understood. I have posted the content.", post_uri)
            elif command == "directive":
                directive = " ".join(parts[2:])
                self.set_directive(user_did, directive)
                self.post_reply("Understood. I have updated your directive.", post_uri)
            elif command == "cleardirective":
                self.clear_directive(user_did)
                self.post_reply("Understood. I have cleared your directive.", post_uri)
            elif command == "learn":
                key, value = " ".join(parts[2:]).split("=", 1)
                self.learn_knowledge(key.strip(), value.strip())
                self.post_reply("Understood. I have learned that.", post_uri)
            elif command == "forget":
                key = " ".join(parts[2:])
                self.forget_knowledge(key.strip())
                self.post_reply("Understood. I have forgotten that.", post_uri)
            else:
                self.post_reply("I don't recognize that command.", post_uri)
        except Exception as e:
            self.logger.error(f"Error processing admin command: {e}")
            self.post_reply("There was an error processing your command.", post_uri)
            
    def post_top_level_post(self, text):
        try:
            self.client.send_post(text)
            self.logger.info(f"Successfully posted a top-level post: {text}")
        except Exception as e:
            self.logger.error(f"Failed to post top-level post: {e}")

    def set_directive(self, user_did, directive):
        cursor = self.db_conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_directives (user_did, directive, timestamp) VALUES (?, ?, ?)",
                       (user_did, directive, datetime.now().isoformat()))
        self.db_conn.commit()
        self.logger.info(f"Set directive for user {user_did}: {directive}")

    def clear_directive(self, user_did):
        cursor = self.db_conn.cursor()
        cursor.execute("DELETE FROM user_directives WHERE user_did = ?", (user_did,))
        self.db_conn.commit()
        self.logger.info(f"Removed directive for user {user_did}")
        
    def learn_knowledge(self, key, value):
        cursor = self.db_conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO knowledge_base (key, value) VALUES (?, ?)",
                       (key, value))
        self.db_conn.commit()
        self.logger.info(f"Learned knowledge: {key}={value}")
    
    def forget_knowledge(self, key):
        cursor = self.db_conn.cursor()
        cursor.execute("DELETE FROM knowledge_base WHERE key = ?", (key,))
        self.db_conn.commit()
        self.logger.info(f"Forgot knowledge: {key}")
        
    def get_knowledge(self, key):
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT value FROM knowledge_base WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None
        
    def get_directive(self, user_did):
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT directive FROM user_directives WHERE user_did = ?", (user_did,))
        row = cursor.fetchone()
        return row[0] if row else None

    def run(self):
        self.connect_db()
        self.login()
        
        while True:
            self.logger.info("Checking for new mentions...")
            self.check_mentions()
            self.logger.info(f"Next cursor: {self.last_mention_check}")
            time.sleep(30)


if __name__ == "__main__":
    bot = AuraBot()
    bot.run()
