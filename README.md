# **Aura Bluesky Bot**

Code forked from [https://github.com/j4ckxyz/terri-bot](https://github.com/j4ckxyz/terri-bot)

<img src="https://github.com/4uffin/aura-bot/blob/main/auraboticon.jpg?raw=true" width="200" height="auto" alt="Aura Bluesky Bot Icon">

## **Introduction**

Aura is an intelligent, context-aware bot designed to interact on Bluesky. It leverages advanced AI models to engage in natural conversations, provide information, generate new posts, and manage its interactions based on a sophisticated memory system and configurable directives. Aura aims to be a helpful, engaging, and supportive presence on the platform.

## **Key Features**

### **1\. Intelligent & Context-Aware Replies**

Aura can understand the context of conversations, drawing upon its internal memory and external search capabilities to generate relevant and coherent replies.

* **Contextual Understanding:** Analyzes the full thread history to provide appropriate responses.  
* **Memory Integration:** Incorporates user-specific memories and general knowledge into its replies.  
* **Emoji Usage:** Aura is configured to use emojis when directly replying to posts, adding personality and conveying tone, but maintains a formal, emoji-free style for new, top-level posts.

### **2\. Bluesky Search Integration**

Aura can perform live searches on Bluesky to gather real-time information related to user queries, enriching its responses.

* Responds to queries like "what are people saying about X?" by searching Bluesky posts.  
* Synthesizes search results into its replies, including links to original posts.

### **3\. New Post Generation**

Aura can generate new top-level posts (threads) on specific topics, either initiated by an admin or requested by a user.

* **User-Initiated:** Users can request Aura to "write a post/thread about X."  
* **Contextual Research:** Gathers information from Bluesky searches before composing the post.  
* **Threaded Output:** Automatically splits long content into multiple posts, forming a cohesive thread.  
* **Safety Checks:** Topics are vetted for safety and appropriateness before posting.

### **4\. Admin Commands**

Specific trusted admin users can issue commands to Aura to control its behavior and content.

* **@aurabot.bsky.social post \[content\]**: Instructs Aura to create a new top-level post with the specified content.  
* **@aurabot.bsky.social directive \[instruction\]**: Updates Aura's core personality and response guidelines. New directives are merged with existing ones, with the latest instruction taking precedence.

### **5\. Conversation Management**

Aura employs strategies to manage conversation flow and prevent excessive replies.

* **Reply Streaks:** Tracks consecutive replies within a thread. If a user is not mentioning the bot directly, the streak increments.  
* **Conversation Stop List:** If a conversation streak reaches a configurable limit (CONVERSATION\_STREAK\_LIMIT), or if a user explicitly requests the bot to stop, Aura will cease replying to that specific thread.

### **6\. Robust Memory System**

Aura maintains a persistent memory using an SQLite database (aura\_memory.db) to enhance its intelligence and contextual awareness.

* **User Memories (user\_memories):** Stores key-value pairs of information related to specific user handles. Only the respective user can update their own memories.  
* **General Knowledge (general\_knowledge):** Stores broader information, facts, and explanations, categorized by topic and tags.  
* **Post History (post\_history):** Records every post that mentions the bot, along with its thread context, for historical reference and summarization.  
* **Summarized Knowledge (summarized\_knowledge):** AI-generated summaries of interactions or knowledge, used to provide concise context to the main AI model. This is periodically updated.

### **7\. Safety Features**

To ensure responsible interaction, Aura includes a blocklist for sensitive content.

* **Blocklist (blocklist):** Prevents Aura from processing or generating replies containing predefined sensitive or inappropriate words.

## **How Aura Works (High-Level Overview)**

1. **Initialization:** On startup, Aura initializes its SQLite database, loads previously processed URIs, and logs into Bluesky.  
2. **Monitoring:** Aura continuously checks for new notifications (mentions and replies) and, if configured, actively searches for posts mentioning its SEARCH\_TERM.  
3. **Context Gathering:** For each relevant interaction, Aura fetches the complete thread history to understand the conversation's context.  
4. **Decision Making (First AI Call):** A preliminary AI call determines the primary action:  
   * reply: For standard conversational responses.  
   * bluesky\_search: If the user's query suggests a need for external Bluesky information.  
   * write\_post: If an admin or user explicitly requests a new post.  
     This call also identifies relevant memory blocks (users, topics, tags) to focus the subsequent AI response.  
5. **Context Building:** Based on the decision, Aura compiles a focused context, pulling relevant information from user memories, general knowledge, and potentially performing a live Bluesky search.  
6. **Response Generation (Second AI Call):** The compiled context, thread history, and personality directives are fed to the main AI model to generate the final reply or post content.  
7. **Post Processing:**  
   * Replies are checked against the blocklist.  
   * Long replies or posts are automatically split into multiple chunks to form a thread on Bluesky.  
   * Facets are created for mentions and links to ensure proper Bluesky formatting.  
8. **Memory Update:** New information identified during the conversation is extracted and saved into Aura's general knowledge database. User conversation summaries are periodically updated.

## **Configuration**

Aura relies on environment variables and internal constants for configuration.

### **Environment Variables (Required)**

* BLUESKY\_HANDLE: Your bot's Bluesky handle (e.g., yourbot.bsky.social).  
* BLUESKY\_PASSWORD: Your bot's Bluesky app password.  
* OPENROUTER\_API\_KEY: Your API key for OpenRouter.ai, which Aura uses for AI model access.

### **Internal Configuration (in bot.py)**

* ADMIN\_DIDS: A Python list of Bluesky DIDs (Decentralized Identifiers) for trusted administrators. Only DIDs in this list can issue admin commands.  
* REPLY\_TO\_ALL\_MENTIONS: Set to True if the bot should actively search for and respond to general mentions of its SEARCH\_TERM in addition to direct notifications. Set to False for only responding to direct mentions/replies.  
* SEARCH\_TERM: The specific term (e.g., your bot's handle) that Aura will search for if REPLY\_TO\_ALL\_MENTIONS is True.  
* MAX\_CONTEXT\_CHARS: Maximum character limit for the context provided to the AI.  
* POST\_MAX\_LENGTH: Bluesky's character limit for a single post (currently 300).  
* CONVERSATION\_STREAK\_LIMIT: The maximum number of consecutive replies Aura will send in a thread without being directly mentioned again before it stops replying to that thread.  
* MENTION\_CHECK\_INTERVAL\_SECONDS: How often Aura checks for new notifications and mentions (in seconds).  
* NOTIFICATION\_FETCH\_LIMIT: The number of notifications to fetch in each check.  
* SEARCH\_LIMIT: The number of search results to fetch when performing a Bluesky search.
