# Terri-Bot: An Intelligent, Memory-Enabled Bluesky Bot

Terri-Bot is a sophisticated, AI-powered bot for the Bluesky social network, built with Python and the AT Protocol. It's designed to be a conversational agent that can remember past interactions, learn new information, perform live searches, and even create original content on request.

This repository provides the complete, all-in-one script to run your own version of the bot.

**Vibe Coded With:** Claude 4 Sonnet and Gemini 2.5 Pro.

---

### Table of Contents
- [Features](#features)
- [Setup Instructions](#setup-instructions)
- [How It Works](#how-it-works)

---

## Features

* **Conversational Memory:** Remembers past interactions with users and can recall information from its knowledge base.
* **AI-Powered Actions:** Uses an AI model (via OpenRouter) to understand user intent and decide on the best course of action (reply, search, write a new post).
* **Direct Bluesky Search:** Can search for recent posts on any topic directly using the Bluesky API to provide real-time context.
* **Admin Commands:** A configurable list of admin DIDs have access to special commands:
    * `@handle post [content]`: Instructs the bot to write a new, top-level post.
    * `@handle directive [instruction]`: Updates the bot's core personality and response style.
* **Public Content Creation:** Any user can ask the bot to write a new thread on a topic (e.g., "@handle write a thread about atproto"). Includes an AI-powered safety check.
* **Smart Threading:** Automatically splits long responses into numbered, threaded replies.
* **Conversation Control:**
    * Users can tell the bot to "stop" or "end conversation" to make it leave a thread.
    * Automatically disengages from conversations after a set number of consecutive replies without being mentioned.

## Setup Instructions

### 1. Clone the Repository
First, clone this repository to your local machine or server (like a Raspberry Pi).
```bash
git clone https://github.com/j4ckxyz/terri-bot.git
cd terri-bot
```

### 2. Install Dependencies

It's recommended to use a Python virtual environment.

```bash
# Create a virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install the required packages
pip install -r requirements.txt
```

### 3. Create a `.env` File

The bot's credentials are managed using a `.env` file. Create this file in the `terri-bot` directory:

```bash
nano .env
```

Paste the following content into the file, replacing the placeholder values with your actual credentials.

```env
BLUESKY_HANDLE="your-bot-handle.bsky.social"
BLUESKY_PASSWORD="xxxx-xxxx-xxxx-xxxx"
OPENROUTER_API_KEY="your_openrouter_api_key"
```

* **`BLUESKY_HANDLE`**: The handle for the Bluesky account the bot will use.
* **`BLUESKY_PASSWORD`**: An **app password** for the bot's account. Never use your main password. You can create app passwords in Bluesky's settings.
* **`OPENROUTER_API_KEY`**: Your API key from [OpenRouter.ai](https://openrouter.ai/) for AI model access.

### 4. Configure the Bot

Open the `bot.py` script to configure your admin users.

```bash
nano bot.py
```

Find the `ADMIN_DIDS` list at the top of the file and replace the placeholder DID with your own Bluesky DID. You can find your DID in your Bluesky profile.

```python
# ----------- Configuration -----------
# Add the DIDs of trusted admin users here
ADMIN_DIDS = [
    "did:plc:your_did_here", 
    # "did:plc:another_admin_did",
]
```

### 5. Run the Bot

You can now run the bot directly from your terminal:

```bash
python3 bot.py
```

The bot will log in and start monitoring for notifications. For long-term use, it's recommended to run the script as a background service using `systemd` or a tool like `screen`.

## How It Works

The bot operates in a continuous loop:

1. It fetches the latest notifications (mentions and replies).
2. For each new notification, it analyzes the conversation context.
3. It makes an initial AI call to a "router" model, which decides the best action (`reply`, `bluesky_search`, `write_post`) and identifies relevant memories.
4. Based on the action, it may perform a Bluesky search or prepare to write a new post.
5. It constructs a detailed prompt containing the conversation history, memories, and any search results.
6. It makes a second AI call to generate the final response.
7. The response is automatically split into a thread if it exceeds the character limit and is posted to Bluesky.