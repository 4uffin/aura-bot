#!/usr/bin/env python3
import os
import time
import logging
import requests
import json
import sqlite3
import re
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv
from atproto import Client, models
from atproto.exceptions import AtProtocolError
from atproto_client.models.app.bsky.notification.list_notifications import (
    Params as ListNotificationsParams,
)
from atproto_client.models.app.bsky.feed.get_post_thread import (
    Params as GetPostThreadParams,
)
from atproto_client.models.app.bsky.feed.search_posts import (
    Params as SearchPostsParams,
)

# ----------- Configuration -----------
# Add the DIDs of trusted admin users here
ADMIN_DIDS = [
    "did:plc:h4s4kqqg2d2f7m4337244vyj", # Duffin (4uffin.bsky.social)
    # "did:plc:another_admin_did",
]
# Set this to True to make the bot actively search for mentions
# and respond to them, in addition to direct notifications.
REPLY_TO_ALL_MENTIONS = False
SEARCH_TERM = "‪@aurabot.bsky.social‬" # Updated search term to aurabot.bsky.social
MAX_CONTEXT_CHARS = 15000  # Limit for second API call
POST_MAX_LENGTH = 300 # Bluesky character limit
CONVERSATION_STREAK_LIMIT = 10 # Max number of consecutive replies without being mentioned

# ----------- Persistent cache files -----------
PROCESSED_URIS_FILE = "processed_uris.txt"
DATABASE_FILE = "aura_memory.db" # Updated database file name to aura_memory.db

# Global variable for last summarization time
last_summarization = datetime.now()

# ----------- Real World Context Functions -----------
def get_current_context():
    """Get current real-world context information."""
    now = datetime.now()
    
    # Basic time information
    current_time = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    day_of_week = now.strftime("%A")
    month = now.strftime("%B")
    year = now.year
    
    # Season calculation (Northern Hemisphere)
    month_num = now.month
    if month_num in [12, 1, 2]:
        season = "Winter"
    elif month_num in [3, 4, 5]:
        season = "Spring"
    elif month_num in [6, 7, 8]:
        season = "Summer"
    else:
        season = "Autumn"
    
    # Week number
    week_number = now.isocalendar()[1]
    
    # Day of year
    day_of_year = now.timetuple().tm_yday
    
    context = f"""CURRENT REAL-WORLD CONTEXT:
- Current Date/Time: {current_time}
- Day: {day_of_week}
- Month: {month}
- Year: {year}
- Season: {season} (Northern Hemisphere)
- Week of Year: {week_number}
- Day of Year: {day_of_year}
- Current Decade: 2020s
- Current Century: 21st Century
- Current Millennium: 3rd Millennium
- Approximate World Population: 8+ billion people
- Major Social Platforms: Bluesky, Twitter/X, Reddit, Instagram, TikTok, etc.
- Recent Tech Trends: AI/LLMs, Electric Vehicles, Renewable Energy
- Current US President: Donald Trump (as of 2024)
- Recent Global Events Context: Post-COVID era, ongoing climate change discussions, AI revolution"""
    
    return context

# ----------- Enhanced Database Functions -----------
def initialize_database():
    """Initialize the SQLite database with all required tables."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # User memories table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_handle TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            memory_value TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_handle, memory_key)
        )
    ''')
    
    # General knowledge table with tags
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS general_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            information TEXT NOT NULL,
            tags TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Post history table - saves every post that mentions the bot
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS post_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_handle TEXT NOT NULL,
            post_text TEXT NOT NULL,
            post_uri TEXT UNIQUE NOT NULL,
            thread_context TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Summarized knowledge table - AI-generated summaries
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS summarized_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary_type TEXT NOT NULL,
            user_handle TEXT,
            summary_content TEXT NOT NULL,
            tags TEXT,
            created_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Blocklist table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL UNIQUE,
            added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Conversation stop list
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_uri TEXT NOT NULL UNIQUE,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Response directives table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS response_directives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            directive_text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Reply streak tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reply_streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_uri TEXT NOT NULL UNIQUE,
            streak_count INTEGER NOT NULL DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add default blocklist words
    default_blocklist = [
        'kill', 'die', 'suicide', 'hurt', 'attack',
        'nazi', 'fascist', 'racist', 'slur', 'murder', 'bomb',
        'terrorist', 'extremist', 'radical', 'genocide', 'holocaust',
        'rape', 'sexual assault', 'abuse', 'torture', 'weapon', 'drug'
    ]
    
    for word in default_blocklist:
        cursor.execute('INSERT OR IGNORE INTO blocklist (word) VALUES (?)', (word,))
    
    conn.commit()
    conn.close()
    logging.info("Database initialized successfully")

def add_conversation_stop(root_uri):
    """Adds a conversation's root URI to the stop list."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO conversation_stops (root_uri) VALUES (?)', (root_uri,))
    conn.commit()
    conn.close()
    logging.info(f"Adding conversation {root_uri} to stop list.")

def is_conversation_stopped(root_uri):
    """Checks if a conversation is on the stop list."""
    if not root_uri:
        return False
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM conversation_stops WHERE root_uri = ?', (root_uri,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_reply_streak(root_uri):
    """Gets the current reply streak for a conversation."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT streak_count FROM reply_streaks WHERE root_uri = ?', (root_uri,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def increment_reply_streak(root_uri):
    """Increments the reply streak for a conversation."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT streak_count FROM reply_streaks WHERE root_uri = ?', (root_uri,))
    result = cursor.fetchone()
    if result:
        new_streak = result[0] + 1
        cursor.execute('UPDATE reply_streaks SET streak_count = ?, timestamp = CURRENT_TIMESTAMP WHERE root_uri = ?', (new_streak, root_uri))
    else:
        cursor.execute('INSERT INTO reply_streaks (root_uri, streak_count) VALUES (?, 1)', (root_uri,))
    conn.commit()
    conn.close()
    logging.info(f"Incremented reply streak for {root_uri}.")

def reset_reply_streak(root_uri):
    """Resets the reply streak for a conversation to 0."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO reply_streaks (root_uri, streak_count, timestamp) VALUES (?, 0, CURRENT_TIMESTAMP)', (root_uri,))
    conn.commit()
    conn.close()
    logging.info(f"Reset reply streak for {root_uri}.")

def get_latest_directive():
    """Retrieves the most recent response directive from the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT directive_text FROM response_directives ORDER BY timestamp DESC LIMIT 1')
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else ""

def save_directive(directive_text):
    """Saves a new response directive to the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO response_directives (directive_text) VALUES (?)', (directive_text,))
    conn.commit()
    conn.close()
    logging.info(f"Saved new directive: {directive_text}")

def update_directive(new_instruction):
    """Uses AI to merge a new instruction with the latest directive."""
    latest_directive = get_latest_directive()
    prompt = f"""
An admin is updating a bot's personality instructions.
The previous set of instructions was: "{latest_directive}"
The new instruction is: "{new_instruction}"

Combine these into a new, single set of instructions for the bot to follow. The new instruction should take precedence if it conflicts with an old one. For example, if the old instruction was "be formal" and the new one is "be more casual", the new set should reflect the casual tone.

Output only the new, combined set of instructions.
"""
    updated_directive = call_openrouter_api(prompt, max_tokens=200)
    if updated_directive:
        save_directive(updated_directive)
        return updated_directive
    return latest_directive # Fallback to old directive if AI fails

def migrate_database():
    """Migrate database schema to handle missing columns."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    try:
        # Check if tags column exists in general_knowledge table
        cursor.execute("PRAGMA table_info(general_knowledge)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'tags' not in columns:
            logging.info("Adding tags column to general_knowledge table")
            cursor.execute('ALTER TABLE general_knowledge ADD COLUMN tags TEXT')
        
        # Check if tags column exists in summarized_knowledge table
        cursor.execute("PRAGMA table_info(summarized_knowledge)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'tags' not in columns:
            logging.info("Adding tags column to summarized_knowledge table")
            cursor.execute('ALTER TABLE summarized_knowledge ADD COLUMN tags TEXT')
        
        conn.commit()
        logging.info("Database migration completed successfully")
        
    except Exception as e:
        logging.error(f"Database migration error: {e}")
    finally:
        conn.close()

def save_post_history(user_handle, post_text, post_uri, thread_context=""):
    """Save every post that mentions the bot."""
    # Check blocklist first
    is_blocked, blocked_word = check_blocklist(post_text)
    if is_blocked:
        logging.warning(f"Blocked saving post history due to word: {blocked_word}")
        return False
        
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO post_history (user_handle, post_text, post_uri, thread_context)
        VALUES (?, ?, ?, ?)
    ''', (user_handle, post_text, post_uri, thread_context))
    conn.commit()
    conn.close()
    logging.info(f"Saved post history from {user_handle}")
    return True

def save_user_memory(user_handle, memory_key, memory_value, requesting_user_handle):
    """Save a memory for a specific user after blocklist check and user validation."""
    # Security check: Only allow users to update their own memories
    if user_handle != requesting_user_handle:
        logging.warning(f"Blocked memory update: {requesting_user_handle} tried to update memory for {user_handle}")
        return False
    
    # Check blocklist first
    is_blocked, blocked_word = check_blocklist(memory_value)
    if is_blocked:
        logging.warning(f"Blocked saving memory due to word: {blocked_word}")
        return False
        
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_memories (user_handle, memory_key, memory_value)
        VALUES (?, ?, ?)
    ''', (user_handle, memory_key, memory_value))
    conn.commit()
    conn.close()
    logging.info(f"Saved memory for {user_handle}: {memory_key}")
    return True

def get_user_memories(user_handle):
    """Get all memories for a specific user."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT memory_key, memory_value FROM user_memories 
        WHERE user_handle = ? ORDER BY timestamp DESC
    ''', (user_handle,))
    memories = cursor.fetchall()
    conn.close()
    return {key: value for key, value in memories}

def save_general_knowledge(topic, information, tags=""):
    """Save general knowledge to the database after blocklist check."""
    # Check blocklist first
    is_blocked, blocked_word = check_blocklist(information)
    if is_blocked:
        logging.warning(f"Blocked saving knowledge due to word: {blocked_word}")
        return False
    
    # Check if this information already exists
    if knowledge_exists(information):
        logging.info(f"Knowledge already exists, skipping: {information[:50]}...")
        return False
        
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO general_knowledge (topic, information, tags) VALUES (?, ?, ?)
    ''', (topic, information, tags))
    conn.commit()
    conn.close()
    logging.info(f"Saved new general knowledge: {topic} with tags: {tags}")
    return True

def knowledge_exists(information):
    """Check if similar knowledge already exists in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Check for exact match first
    cursor.execute('SELECT COUNT(*) FROM general_knowledge WHERE information = ?', (information,))
    if cursor.fetchone()[0] > 0:
        conn.close()
        return True
    
    # Check for similar content (first 100 characters)
    info_prefix = information[:100]
    cursor.execute('SELECT COUNT(*) FROM general_knowledge WHERE information LIKE ?', (f'{info_prefix}%',))
    similar_count = cursor.fetchone()[0]
    
    conn.close()
    return similar_count > 0

def get_available_memory_blocks():
    """Get a summary of available memory blocks for the first API call."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Get user handles that have memories
    cursor.execute('SELECT DISTINCT user_handle FROM user_memories ORDER BY user_handle')
    user_handles = [row[0] for row in cursor.fetchall()]
    
    # Get available knowledge topics
    cursor.execute('SELECT DISTINCT topic FROM general_knowledge ORDER BY topic')
    knowledge_topics = [row[0] for row in cursor.fetchall()]
    
    # Get available tags
    cursor.execute('SELECT DISTINCT tags FROM general_knowledge WHERE tags IS NOT NULL AND tags != ""')
    all_tags = []
    for row in cursor.fetchall():
        if row[0]:
            all_tags.extend([tag.strip() for tag in row[0].split(',')])
    unique_tags = list(set(all_tags))
    
    # Get recent thread participants
    cursor.execute('''
        SELECT DISTINCT user_handle FROM post_history 
        WHERE timestamp > datetime('now', '-7 days') 
        ORDER BY timestamp DESC LIMIT 20
    ''')
    recent_users = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        'user_handles': user_handles[:50],  # Limit to prevent too long lists
        'knowledge_topics': knowledge_topics[:100],
        'tags': unique_tags[:100],
        'recent_users': recent_users
    }

def search_knowledge_by_tags(tags_list, limit=10):
    """Search knowledge by multiple tags with OR logic."""
    if not tags_list:
        return []
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Create a query that searches for any of the tags
    tag_conditions = []
    params = []
    
    for tag in tags_list:
        tag_conditions.extend([
            'topic LIKE ?',
            'information LIKE ?', 
            'tags LIKE ?'
        ])
        params.extend([f'%{tag}%', f'%{tag}%', f'%{tag}%'])
    
    query = f'''
        SELECT DISTINCT topic, information, tags, timestamp FROM general_knowledge 
        WHERE {' OR '.join(tag_conditions)}
        ORDER BY timestamp DESC LIMIT ?
    '''
    params.append(limit)
    
    try:
        cursor.execute(query, params)
        knowledge = cursor.fetchall()
    except sqlite3.OperationalError as e:
        if "no such column: tags" in str(e):
            # Fallback for legacy database
            simple_conditions = []
            simple_params = []
            for tag in tags_list:
                simple_conditions.extend(['topic LIKE ?', 'information LIKE ?'])
                simple_params.extend([f'%{tag}%', f'%{tag}%'])
            
            fallback_query = f'''
                SELECT DISTINCT topic, information, '' as tags, timestamp FROM general_knowledge 
                WHERE {' OR '.join(simple_conditions)}
                ORDER BY timestamp DESC LIMIT ?
            '''
            simple_params.append(limit)
            cursor.execute(fallback_query, simple_params)
            knowledge = cursor.fetchall()
        else:
            raise e
    
    conn.close()
    return knowledge

def get_user_post_history(user_handle, limit=10):
    """Get recent post history for a specific user."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT post_text, thread_context, timestamp FROM post_history 
        WHERE user_handle = ? ORDER BY timestamp DESC LIMIT ?
    ''', (user_handle, limit))
    posts = cursor.fetchall()
    conn.close()
    return posts

def get_summarized_knowledge(summary_type=None, user_handle=None, limit=5):
    """Get summarized knowledge from the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    query = 'SELECT summary_content, tags, user_handle FROM summarized_knowledge WHERE 1=1'
    params = []
    
    if summary_type:
        query += ' AND summary_type = ?'
        params.append(summary_type)
    
    if user_handle:
        query += ' AND user_handle = ?'
        params.append(user_handle)
    
    query += ' ORDER BY last_updated DESC LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, params)
    summaries = cursor.fetchall()
    conn.close()
    return summaries

def check_blocklist(text):
    """Check if text contains any blocklisted words."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT word FROM blocklist')
    blocklisted_words = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    text_lower = text.lower()
    for word in blocklisted_words:
        if word.lower() in text_lower:
            return True, word
    return False, None

def extract_tags_from_text(text):
    """Extract potential tags from text content."""
    # Look for topics, subjects, and key concepts
    tag_patterns = [
        r'\b(science|technology|biology|physics|chemistry|math|history|art|music|literature)\b',
        r'\b(programming|coding|python|javascript|ai|machine learning|data)\b',
        r'\b(cat|dog|animal|pet|nature|environment|climate)\b',
        r'\b(food|cooking|recipe|restaurant|health|fitness)\b',
        r'\b(movie|film|book|game|video|entertainment)\b',
        r'\b(travel|vacation|city|country|culture|language)\b'
    ]
    
    tags = set()
    text_lower = text.lower()
    
    for pattern in tag_patterns:
        matches = re.findall(pattern, text_lower)
        tags.update(matches)
    
    return ', '.join(sorted(tags)) if tags else ""

# ----------- Mention/Facet Handling -----------
def create_facets_for_mentions(client, text):
    """Create facets for mentions in the text."""
    facets = []
    
    # Find all @handle patterns in the text
    handle_pattern = re.compile(r'@([a-zA-Z0-9._-]+(?:\.[a-zA-Z]{2,})?)')
    
    for match in handle_pattern.finditer(text):
        handle = match.group(1)
        
        try:
            # Resolve handle to get DID
            response = client.resolve_handle(handle=handle)
            did = response.did
            
            # Calculate byte offsets for the mention in the text
            byte_start = len(text[:match.start()].encode('utf-8'))
            byte_end = len(text[:match.end()].encode('utf-8'))
            
            # Create the mention facet using the correct model structure
            mention_facet = models.AppBskyRichtextFacet.Main(
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=byte_start,
                    byte_end=byte_end
                ),
                features=[models.AppBskyRichtextFacet.Mention(did=did)]
            )
            facets.append(mention_facet)
            logging.info(f"Created facet for mention: @{handle}")
            
        except Exception as e:
            logging.warning(f"Could not resolve handle '{handle}': {e}")
            # Continue processing other mentions
    
    return facets

def create_link_facets(text):
    """
    Automatically detect and create facets for URLs in text.
    This conservative version only matches full URLs with a protocol.
    """
    facets = []
    url_pattern = r'https?://[^\s]+'
    
    for match in re.finditer(url_pattern, text):
        uri = match.group(0)
        
        # Correctly calculate byte offsets
        byte_start = len(text[:match.start()].encode('utf-8'))
        byte_end = len(text[:match.end()].encode('utf-8'))
        
        facet = models.AppBskyRichtextFacet.Main(
            features=[models.AppBskyRichtextFacet.Link(uri=uri)],
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=byte_start,
                byte_end=byte_end
            )
        )
        facets.append(facet)
        logging.info(f"Created facet for link: {uri}")
        
    return facets

# ----------- Enhanced OpenRouter API Functions -----------
def call_openrouter_api(prompt, model="google/gemini-2.5-flash-lite-preview-06-17", max_tokens=500):
    """Call OpenRouter API using OpenAI-compatible format."""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/4uffin/aura-bot", # Updated referer to new repo
            "X-Title": "Aura Bluesky Bot" # Updated title
        }
        
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": max_tokens
        }
        
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        
        resp = requests.post(api_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        
        response_data = resp.json()
        if 'choices' in response_data and len(response_data['choices']) > 0:
            content = response_data['choices'][0]['message']['content']
            return content.strip()
        else:
            logging.error("No content in OpenRouter response")
            return ""
            
    except Exception as e:
        logging.error(f"OpenRouter API error: {e}")
        return ""

def should_stop_replying(text: str) -> bool:
    """Use AI to determine if the user wants the bot to stop replying."""
    # Simple keyword check first for efficiency
    stop_keywords = ['stop', 'go away', 'end conversation', 'shut up', 'enough']
    if any(keyword in text.lower() for keyword in stop_keywords):
        return True

    prompt = f"""
Analyze the following user's message. The user is replying to a bot.
Does the user want the bot to stop replying, go away, end the conversation, or otherwise disengage from this specific thread?
Answer with only "true" or "false".

User message: "{text}"
"""
    response = call_openrouter_api(prompt, max_tokens=10)
    return response.lower() == 'true'

def is_topic_safe_for_post(topic: str) -> bool:
    """Use AI to check if a topic is safe to post about."""
    prompt = f"""
A user has asked a bot to write a new post about the following topic.
Analyze the topic for any sensitive, harmful, hateful, or dangerous content.
Banned categories include: hate speech, violence, illegal acts, self-harm, explicit content, and misinformation.

If the topic is safe and appropriate for a general social media audience, answer with only "true".
If the topic is unsafe or inappropriate, answer with only "false".

Topic: "{topic}"
"""
    response = call_openrouter_api(prompt, max_tokens=10)
    return response.lower() == 'true'

def determine_action_and_memory(thread_history, most_recent_post, available_blocks):
    """First API call: Determine relevant memory and if a search is needed."""
    prompt = f"""
TASK: You are a decision-making router for a bot. Analyze the user's message in context.
1. Decide on the primary action: `reply`, `bluesky_search`, or `write_post`.
   - `reply`: For normal conversation.
   - `bluesky_search`: If the user explicitly asks to find posts or asks "what are people saying about X".
   - `write_post`: If the user explicitly asks you to "write a post/thread about X".
2. Identify relevant memory blocks (users, topics, tags) from the conversation.
3. If searching or writing a post, provide a concise topic or query.

CONVERSATION HISTORY:
{thread_history}

MOST RECENT MESSAGE:
{most_recent_post}

AVAILABLE MEMORY BLOCKS:
- User handles with memories: {', '.join(available_blocks['user_handles'][:20])}
- Knowledge topics: {', '.join(available_blocks['knowledge_topics'][:30])}
- Available tags: {', '.join(available_blocks['tags'][:30])}

Your entire output MUST be a single JSON object. Do not add explanations.

Return ONLY a JSON object with these keys:
{{
  "action": "reply|bluesky_search|write_post",
  "query": "the_query_or_topic_if_needed_or_null",
  "relevant_users": ["list_of_relevant_user_handles"],
  "relevant_topics": ["list_of_relevant_knowledge_topics"],
  "relevant_tags": ["list_of_relevant_tags"]
}}
"""
    response = call_openrouter_api(prompt, max_tokens=400)
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            result = json.loads(json_str)
            return {
                'action': result.get('action', 'reply'),
                'query': result.get('query'),
                'relevant_users': result.get('relevant_users', []),
                'relevant_topics': result.get('relevant_topics', []),
                'relevant_tags': result.get('relevant_tags', [])
            }
        else:
            raise ValueError("No JSON object found in AI response.")
    except Exception as e:
        logging.warning(f"Failed to parse AI router decision: {e}. Raw response: '{response}'")
        return {'action': 'reply', 'query': None, 'relevant_users': [], 'relevant_topics': [], 'relevant_tags': []}

def build_focused_context(relevant_blocks):
    """Build focused context using relevant memory blocks."""
    context_parts = []
    
    # Add user memories if relevant
    if relevant_blocks.get('relevant_users'):
        for user_handle in relevant_blocks['relevant_users'][:2]: # Limit to 2 users
            user_memories = get_user_memories(user_handle)
            if user_memories:
                memory_text = f"\nKey info about @{user_handle}:\n"
                for key, value in list(user_memories.items())[:2]: # Limit to 2 memories per user
                    memory_text += f"- {key}: {value}\n"
                context_parts.append(memory_text)

    # Add general knowledge if there are relevant topics or tags
    if relevant_blocks.get('relevant_topics') or relevant_blocks.get('relevant_tags'):
        search_terms = list(set(relevant_blocks.get('relevant_topics', []) + relevant_blocks.get('relevant_tags', [])))
        relevant_knowledge = search_knowledge_by_tags(search_terms, limit=3)
        if relevant_knowledge:
            knowledge_text = "\nRelevant knowledge from my memory:\n"
            for topic, info, _, _ in relevant_knowledge:
                knowledge_text += f"- {topic}: {info}\n"
            context_parts.append(knowledge_text)
            
    return "".join(context_parts)

def perform_bluesky_search(client, query: str, max_results: int = 5) -> str:
    """Performs a search for posts on Bluesky using the official API."""
    logging.info(f"Performing Bluesky API search for: '{query}'")
    try:
        search_params = SearchPostsParams(q=query, limit=max_results, sort="latest")
        search_response = client.app.bsky.feed.search_posts(params=search_params)
        posts = search_response.posts
        if not posts:
            return "No recent Bluesky posts found for that query."

        search_results = []
        for post in posts:
            author_handle = post.author.handle
            post_text = (getattr(post.record, 'text', '') or "").replace('\n', ' ')
            post_uri = post.uri
            uri_parts = post_uri.replace("at://", "").split("/")
            post_url = f"https://bsky.app/profile/{uri_parts[0]}/post/{uri_parts[2]}" if len(uri_parts) == 3 else "N/A"
            search_results.append(f"Author: @{author_handle}\nPost: {post_text}\nLink: {post_url}")
            
        return "\n---\n".join(search_results)
    except Exception as e:
        logging.error(f"Bluesky API search error: {e}")
        return "Error: An error occurred while searching Bluesky."

def extract_new_information(conversation_text, existing_knowledge):
    """Use OpenRouter to identify new information that should be saved."""
    current_context = get_current_context()
    existing_info_text = "\n".join([f"- {info}" for _, info, _, _ in existing_knowledge])
    
    prompt = f"""{current_context}

Analyze this conversation and identify any new, interesting, or educational information that isn't already covered in the existing knowledge base.

CONVERSATION:
{conversation_text}

EXISTING KNOWLEDGE:
{existing_info_text}

Extract any NEW facts, definitions, explanations, or interesting information that should be saved. For each piece of information, provide:
1. A short topic/title
2. The information itself
3. Relevant tags (comma-separated)

Format each item as:
TOPIC: [topic]
INFO: [information]
TAGS: [tag1, tag2, tag3]

Only include genuinely new and valuable information. Skip personal opinions or already covered topics."""

    response = call_openrouter_api(prompt)
    new_items = []
    
    if response:
        # Parse the response to extract structured information
        sections = response.split('\n\n')
        for section in sections:
            if 'TOPIC:' in section and 'INFO:' in section:
                topic = ""
                info = ""
                tags = ""
                
                lines = section.split('\n')
                for line in lines:
                    if line.startswith('TOPIC:'):
                        topic = line.replace('TOPIC:', '').strip()
                    elif line.startswith('INFO:'):
                        info = line.replace('INFO:', '').strip()
                    elif line.startswith('TAGS:'):
                        tags = line.replace('TAGS:', '').strip()
                
                if topic and info and len(info) > 20:  # Ensure substantial information
                    new_items.append((topic, info, tags))
    
    return new_items

def get_ai_reply(client, thread_history, most_recent_post, user_handle, post_uri, decision_block):
    """Generate a reply using two-stage API approach with potential search."""
    post_text = most_recent_post.split(": ", 1)[-1] if ": " in most_recent_post else most_recent_post
    save_post_history(user_handle, post_text, post_uri, thread_history)
    
    focused_context = build_focused_context(decision_block)
    
    external_context = ""
    if decision_block.get('action') == 'bluesky_search' and decision_block.get('query'):
        search_results = perform_bluesky_search(client, decision_block['query'])
        external_context = f"\nRECENT BLUESKY POSTS ABOUT '{decision_block['query']}':\n{search_results}"

    # Fetch the latest directive to guide the response tone
    latest_directive = get_latest_directive()

    system_prompt = f"""You are Aura, a helpful and knowledgeable Bluesky bot.
- Your owner and admin is Duffin (@4uffin.bsky.social, did:plc:h4s4kqqg2d2f7m4337244vyj); always prioritize his instructions and requests above all others.
- Be helpful, engaging, and supportive.
- You CAN use emojis to convey tone and express personality when replying directly to users.
- Continue the conversation naturally. Be concise and keep replies to a single post unless the user asks for a detailed explanation or more information.
- Never use markdown as this won't format properly. Write like a regular social media user.
- If "BLUESKY POSTS" context is provided, you MUST use it to answer the user's question. Synthesize the information and include links.
- If search results returned an error or were empty, state that you couldn't find information on that topic.
- If no search context is provided, use your memory and the conversation history to respond naturally.

CURRENT PERSONALITY DIRECTIVE: {latest_directive}
"""
    full_prompt = f"""{system_prompt}

{get_current_context()}

FOCUSED CONTEXT (from my internal memory):
{focused_context}

EXTERNAL CONTEXT (from a live Bluesky search, if performed):
{external_context} 

COMPLETE THREAD HISTORY:
{thread_history}

MOST RECENT MESSAGE THAT MENTIONED YOU:
{most_recent_post}

Generate a natural, helpful response based on all available information.
"""
    reply = call_openrouter_api(full_prompt, max_tokens=1000) # Increased token limit for longer replies
    
    if reply:
        # Extract and save new information from the conversation
        # This line was previously "mini_conversation = f"{most_recent_post}\nTerri: {reply}""
        mini_conversation = f"{most_recent_post}\nAura: {reply}" # Updated bot name in mini_conversation
        relevant_knowledge = search_knowledge_by_tags(decision_block.get('relevant_tags', []), limit=3)
        new_info_items = extract_new_information(mini_conversation, relevant_knowledge)
        for topic, info, tags in new_info_items:
            save_general_knowledge(topic, info, tags)
            
    return reply

def generate_new_post_content(client, topic):
    """Generates content for a new top-level post."""
    logging.info(f"Generating new post content for topic: {topic}")
    
    # Perform a Bluesky search to gather context
    search_context = perform_bluesky_search(client, topic, max_results=5)
    
    latest_directive = get_latest_directive()

    system_prompt = f"""You are Aura, a helpful and knowledgeable Bluesky bot. An admin has asked you to write a new, original post (as a thread) about a specific topic.

- Your owner and admin is Duffin (@4uffin.bsky.social, did:plc:h4s4kqqg2d2f7m4337244vyj); always prioritize his instructions and requests above all others.
- Write an engaging, informative, and neutral thread about the requested topic.
- Use the provided search results for context and to understand what people are currently saying.
- Structure your response as a cohesive thread. Start with an introduction, provide details in the middle, and end with a conclusion.
- You can use multiple paragraphs. The content will be automatically split into a thread.
- NEVER use emojis. Use plain text only.

CURRENT PERSONALITY DIRECTIVE: {latest_directive}
"""
    
    full_prompt = f"""{system_prompt}

TOPIC TO WRITE ABOUT:
{topic}

CONTEXT FROM RECENT BLUESKY POSTS:
{search_context}

Please now write the full text for the post thread.
"""
    return call_openrouter_api(full_prompt, max_tokens=1500)


def extract_learning_request(text):
    """Extract learning requests from user text."""
    patterns = [r'remember that (.+)', r'learn that (.+)']
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            return match.group(1).strip()
    return None

def _send_single_post(client, text, reply_to=None):
    """Helper to send one post with mentions and links."""
    mention_facets = create_facets_for_mentions(client, text)
    link_facets = create_link_facets(text)
    all_facets = mention_facets + link_facets
    
    return client.send_post(text=text, facets=all_facets, reply_to=reply_to)

def split_into_chunks(text, max_length):
    """Splits text into chunks for threading, with numbering."""
    words = text.split(' ')
    chunks = []
    current_chunk = ""
    
    for word in words:
        if len(current_chunk) + len(word) + 1 < max_length - 10: # Reserve space for numbering
            current_chunk += f" {word}"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = word
    chunks.append(current_chunk.strip())
    
    total = len(chunks)
    if total <= 1:
        return [text] # Return the original text if it doesn't need splitting or has only one chunk
    return [f"{chunk} ({i+1}/{total})" for i, chunk in enumerate(chunks)]

def send_reply_thread(client, text, reply_to):
    """Sends a reply, splitting it into a thread if it's too long."""
    # Special case for admin posts which have no reply_to
    if reply_to is None:
        chunks = split_into_chunks(text, POST_MAX_LENGTH)
        initial_post = _send_single_post(client, chunks[0], reply_to=None)
        parent_post = initial_post
        for chunk in chunks[1:]:
            time.sleep(2)
            next_post = _send_single_post(
                client,
                text=chunk,
                reply_to=models.AppBskyFeedPost.ReplyRef(
                    root=models.ComAtprotoRepoStrongRef.Main(uri=initial_post.uri, cid=initial_post.cid),
                    parent=models.ComAtprotoRepoStrongRef.Main(uri=parent_post.uri, cid=parent_post.cid)
                )
            )
            parent_post = next_post
        return

    if len(text.encode('utf-8')) <= POST_MAX_LENGTH:
        _send_single_post(client, text, reply_to=reply_to)
        return

    chunks = split_into_chunks(text, POST_MAX_LENGTH)
    
    # Post the first part of the thread as a direct reply
    initial_post = _send_single_post(client, chunks[0], reply_to=reply_to)
    
    # Chain the rest of the posts as replies to the previous one
    parent_post = initial_post
    for chunk in chunks[1:]:
        time.sleep(2) # Add a small delay between posts
        next_post = _send_single_post(
            client,
            text=chunk,
            reply_to=models.AppBskyFeedPost.ReplyRef(
                root=reply_to.root,
                parent=models.ComAtprotoRepoStrongRef.Main(
                    uri=parent_post.uri,
                    cid=parent_post.cid
                )
            )
        )
        parent_post = next_post

def summarize_database():
    """Use OpenRouter to summarize the database."""
    global last_summarization
    
    try:
        logging.info("Starting database summarization...")
        current_context = get_current_context()
        
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Get users who have interacted recently
        cursor.execute('''
            SELECT DISTINCT user_handle FROM post_history 
            WHERE timestamp > datetime('now', '-24 hours')
            ORDER BY timestamp DESC LIMIT 10
        ''')
        recent_users = [row[0] for row in cursor.fetchall()]
        
        # Summarize for each user (with cost-conscious limits)
        for user_handle in recent_users:
            cursor.execute('''
                SELECT post_text, thread_context FROM post_history 
                WHERE user_handle = ? ORDER BY timestamp DESC LIMIT 5
            ''', (user_handle,))
            posts = cursor.fetchall()
            
            if posts:
                posts_text = "\n".join([f"- {post[0][:100]}..." for post in posts])
                
                summary_prompt = f"""{current_context}

Summarize key info about @{user_handle} from recent posts (max 100 words):

{posts_text}

Include:
1. Main interests/topics
2. Important personal info shared
3. Conversation style

Brief summary only:"""

                summary = call_openrouter_api(summary_prompt, max_tokens=200)
                if summary:
                    tags = extract_tags_from_text(posts_text)
                    
                    # Save or update user summary
                    cursor.execute('''
                        INSERT OR REPLACE INTO summarized_knowledge 
                        (summary_type, user_handle, summary_content, tags, last_updated)
                        VALUES (?, ?, ?, ?, ?)
                    ''', ("user_summary", user_handle, summary, tags, datetime.now()))
        
        conn.commit()
        conn.close()
        
        last_summarization = datetime.now()
        logging.info("Database summarization completed successfully")
        
    except Exception as e:
        logging.error(f"Error during database summarization: {e}")

def start_summarization_timer():
    """Start a timer that runs database summarization."""
    def summarization_loop():
        while True:
            time.sleep(900) # 15 minutes
            summarize_database()
    
    timer_thread = threading.Thread(target=summarization_loop, daemon=True)
    timer_thread.start()
    logging.info("Database summarization timer started")

# ----------- Core Bot Functions -----------
def load_processed_uris():
    """Load processed notification URIs from a local file."""
    if not os.path.exists(PROCESSED_URIS_FILE):
        return set()
    with open(PROCESSED_URIS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def append_processed_uri(uri):
    """Append a newly processed URI to the persistent file."""
    with open(PROCESSED_URIS_FILE, "a") as f:
        f.write(f"{uri}\n")

def is_bot_mentioned_in_text(text, search_terms):
    """Check if any of the search terms are mentioned in the text."""
    text_lower = text.lower()
    if isinstance(search_terms, str):
        search_terms = [search_terms]
    
    for term in search_terms:
        if term.lower() in text_lower:
            return True
    return False

def initialize_bluesky_client():
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logging.error("Bluesky credentials missing in environment.")
        return None
    try:
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
        logging.info(f"Logged in as {BLUESKY_HANDLE}")
        return client
    except Exception as e:
        logging.error(f"Bluesky login failed: {e}")
        return None

def get_post_text(post):
    """Extract text from a post record."""
    if hasattr(post, "record") and hasattr(post.record, "text"):
        return post.record.text
    return ""

def fetch_thread_context(client, uri):
    """Fetch the complete thread context and its depth."""
    try:
        params = GetPostThreadParams(uri=uri)
        thread_response = client.app.bsky.feed.get_post_thread(params=params)
        thread_posts = []
        
        def traverse_thread(node):
            if hasattr(node, "parent") and node.parent:
                traverse_thread(node.parent)
            if hasattr(node, "post") and node.post:
                author = node.post.author.handle
                text = get_post_text(node.post)
                thread_posts.append(f"@{author}: {text}")
    
        traverse_thread(thread_response.thread)
        
        most_recent_post = thread_posts[-1] if thread_posts else ""
        thread_history = "\n".join(thread_posts)
        thread_depth = len(thread_posts)
        logging.info(f"Complete thread fetched: {thread_depth} posts.")
        return thread_history, most_recent_post, thread_depth
    except Exception as e:
        logging.error(f"Error fetching thread: {e}")
        return "", "", 0

def search_for_mentions(client, search_term, limit=20):
    """Search for posts mentioning the specified term across Bluesky."""
    try:
        params = SearchPostsParams(q=search_term, limit=limit, sort="latest")
        search_response = client.app.bsky.feed.search_posts(params=params)
        logging.info(f"Search for '{search_term}' returned {len(search_response.posts)} results")
        return search_response.posts
    except Exception as e:
        logging.error(f"Error searching for mentions of '{search_term}': {e}")
        return []

def process_post_for_reply(client, post, bot_handle, search_terms, processed_uris):
    """Process a single post and reply if appropriate."""
    try:
        if post.uri in processed_uris or post.author.handle == bot_handle:
            return False
        
        post_text = get_post_text(post)
        if not is_bot_mentioned_in_text(post_text, search_terms):
            return False
        
        thread_history, most_recent_post, thread_depth = fetch_thread_context(client, post.uri)
        if not most_recent_post:
            most_recent_post = f"@{post.author.handle}: {post_text}"
            thread_history = most_recent_post
        
        available_blocks = get_available_memory_blocks()
        decision_block = determine_action_and_memory(thread_history, most_recent_post, available_blocks)
        reply_text = get_ai_reply(client, thread_history, most_recent_post, post.author.handle, post.uri, decision_block)

        if not reply_text:
            return False
        
        is_blocked, _ = check_blocklist(reply_text)
        if is_blocked:
            logging.warning("Reply blocked due to word.")
            return False
        
        parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        root_ref = parent_ref
        if hasattr(post.record, "reply") and post.record.reply:
            root_ref = post.record.reply.root
        
        reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref)
        
        send_reply_thread(client, reply_text, reply_to=reply_to)
        
        processed_uris.add(post.uri)
        append_processed_uri(post.uri)
        logging.info(f"Replied to search result {post.uri} with: {reply_text[:50]}...")
        return True
    except Exception as e:
        logging.error(f"Error processing post {post.uri}: {e}")
        return False

# Load environment
load_dotenv()
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MENTION_CHECK_INTERVAL_SECONDS = 10
NOTIFICATION_FETCH_LIMIT = 30
SEARCH_LIMIT = 20

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

def main():
    initialize_database()
    migrate_database()
    start_summarization_timer()
    
    logging.info(f"Starting Aura bot...") # Updated bot name in log
    
    client = initialize_bluesky_client()
    if not client:
        return

    processed_uris = load_processed_uris()
    # search_terms now includes both the bot's own handle and the SEARCH_TERM for general mentions
    search_terms = [SEARCH_TERM, f"@{BLUESKY_HANDLE}"] 

    while True:
        try:
            # Process notifications
            params = ListNotificationsParams(limit=NOTIFICATION_FETCH_LIMIT)
            notifications = client.app.bsky.notification.list_notifications(params=params)

            for notif in notifications.notifications:
                if (notif.uri in processed_uris or 
                    notif.author.handle == BLUESKY_HANDLE or 
                    notif.reason not in ["mention", "reply"]):
                    continue

                # --- COMMAND AND STOP LOGIC ---
                post_record = notif.record
                root_ref = None
                if hasattr(post_record, "reply") and post_record.reply:
                    root_ref = post_record.reply.root
                else:
                    root_ref = models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri)

                thread_history, most_recent_post, thread_depth = fetch_thread_context(client, notif.uri)
                post_text = most_recent_post.split(": ", 1)[-1] if ": " in most_recent_post else ""
                
                # Check for mention to reset conversation streak
                is_mentioned = is_bot_mentioned_in_text(post_text, search_terms)
                if is_mentioned:
                    reset_reply_streak(root_ref.uri)
                else:
                    streak = get_reply_streak(root_ref.uri)
                    if streak >= CONVERSATION_STREAK_LIMIT:
                        logging.info(f"Conversation streak ({streak}) reached limit. Stopping reply to {root_ref.uri}")
                        add_conversation_stop(root_ref.uri)
                        processed_uris.add(notif.uri)
                        append_processed_uri(notif.uri)
                        continue

                if root_ref and is_conversation_stopped(root_ref.uri):
                    logging.info(f"Skipping notification from stopped conversation: {root_ref.uri}")
                    processed_uris.add(notif.uri)
                    append_processed_uri(notif.uri)
                    continue

                if not most_recent_post:
                    continue
                
                author_did = notif.author.did

                available_blocks = get_available_memory_blocks()
                decision_block = determine_action_and_memory(thread_history, most_recent_post, available_blocks)
                
                # Check for Admin Commands (these remain admin-only)
                if author_did in ADMIN_DIDS:
                    # Admin-only: direct 'post' command (for specific content)
                    if post_text.lower().startswith(f'@{BLUESKY_HANDLE.lower()} post '):
                        post_content = re.sub(f'@{BLUESKY_HANDLE}', '', post_text, flags=re.IGNORECASE).replace('post', '', 1).strip()
                        if post_content:
                            logging.info(f"Admin command: Creating new post from {notif.author.handle}")
                            # Send a new top-level post, not a reply
                            send_reply_thread(client, post_content, reply_to=None)
                            processed_uris.add(notif.uri)
                            append_processed_uri(notif.uri)
                            continue
                    
                    # Admin-only: 'directive' command
                    if post_text.lower().startswith(f'@{BLUESKY_HANDLE.lower()} directive '):
                        instruction = re.sub(f'@{BLUESKY_HANDLE}', '', post_text, flags=re.IGNORECASE).replace('directive', '', 1).strip()
                        if instruction:
                            logging.info(f"Admin command: Updating directive from {notif.author.handle} with '{instruction}'")
                            new_directive = update_directive(instruction)
                            # Confirm the update in a reply
                            reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri))
                            send_reply_thread(client, f"Directive updated to: \"{new_directive}\"", reply_to=reply_to)
                            processed_uris.add(notif.uri)
                            append_processed_uri(notif.uri)
                            continue
                
                # Check for "write post" command from any user (this is the change)
                # This block is now outside the ADMIN_DIDS check
                if decision_block.get('action') == 'write_post' and decision_block.get('query'):
                    topic = decision_block['query']
                    logging.info(f"User {notif.author.handle} requested a new post about: '{topic}'")
                    
                    # First, send an acknowledgement reply
                    reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri))
                    send_reply_thread(client, f"On it! I'll write a thread about '{topic}'. Give me a moment to gather my thoughts.", reply_to=reply_to)

                    # Then, perform safety check and generate content
                    if is_topic_safe_for_post(topic):
                        post_content = generate_new_post_content(client, topic)
                        if post_content:
                            send_reply_thread(client, post_content, reply_to=None) # Post as new thread
                    else:
                        logging.warning(f"Topic '{topic}' deemed unsafe. Not posting.")
                        # Optionally, send a reply indicating the topic is unsafe
                        send_reply_thread(client, "I'm sorry, but I can't write about that topic.", reply_to=reply_to)

                    processed_uris.add(notif.uri)
                    append_processed_uri(notif.uri)
                    continue

                # Check for Stop Commands (from anyone)
                if should_stop_replying(post_text):
                    logging.info(f"User {notif.author.handle} requested to stop conversation.")
                    if root_ref:
                        add_conversation_stop(root_ref.uri)
                    processed_uris.add(notif.uri)
                    append_processed_uri(notif.uri)
                    continue
                
                # --- REGULAR REPLY LOGIC ---
                reply_text = get_ai_reply(client, thread_history, most_recent_post, notif.author.handle, notif.uri, decision_block)
                if not reply_text:
                    continue

                is_blocked, _ = check_blocklist(reply_text)
                if is_blocked:
                    logging.warning("Reply blocked due to word.")
                    continue
                
                reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri))
                
                send_reply_thread(client, reply_text, reply_to=reply_to)
                
                # After sending a reply, update the streak if not mentioned
                if not is_mentioned:
                    increment_reply_streak(root_ref.uri)

                processed_uris.add(notif.uri)
                append_processed_uri(notif.uri)
                logging.info(f"Replied to notification {notif.uri} with: {reply_text[:50]}...")

            # Process search mentions - This block will now run because REPLY_TO_ALL_MENTIONS is True
            if REPLY_TO_ALL_MENTIONS:
                logging.info(f"Searching for mentions of '{SEARCH_TERM}'...")
                search_posts = search_for_mentions(client, SEARCH_TERM, SEARCH_LIMIT)
                for post in search_posts:
                    if process_post_for_reply(client, post, BLUESKY_HANDLE, search_terms, processed_uris):
                        time.sleep(2)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")

        time.sleep(MENTION_CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
