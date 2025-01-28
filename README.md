# 📸 Smart Photo Journal MCP Server

**Smart Photo Journal** is an MCP server designed to help you search and analyze your photo library with powerful, intuitive tools. Whether you're reminiscing about family moments or looking for a specific photo with friends, this server has got you covered! 🎉

> **Inspired by:** [burningion/video-editing-mcp](https://github.com/burningion/video-editing-mcp)
> A huge shoutout to [@burningion](https://x.com/burningion) for the innovative idea of using MCP for creative media management!

<a href="https://glama.ai/mcp/servers/51jiworg5k"><img width="380" height="200" src="https://glama.ai/mcp/servers/51jiworg5k/badge" alt="Smart Photo Journal Server MCP server" /></a>

## 🎯 Features

- **Location Search:** Find photos from specific places with ease. 🌍
- **Label Search:** Search photos by keywords or labels like "Birthday," "Beach," or "Vacation." 🎉
- **People Search:** Quickly locate photos featuring specific people. 👥
- **Photo Analysis:** Discover fun insights like the most popular times and days for your photo shoots. 🕰️
- **Fuzzy Matching:** Not sure of the exact name? Don't worry! The server supports fuzzy matching for flexibility. 🔍

## 🚀 Getting started

### Prerequisites

1. Ensure you have macOS with a Photos library.
2. Install [uv](https://docs.astral.sh/uv/) to manage dependencies and run the server.

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/Siddhant-K-code/memory-journal-mcp-server.git
   cd memory-journal-mcp-server
   ```

2. Install dependencies using `uv`:

   ```bash
   uv sync
   ```

3. Configure the MCP server. Update your `claude_desktop_config.json` with the following configuration:

   ```json
   {
     "mcpServers": {
       "smart-photo-journal": {
         "command": "/Users/<YOUR_DEVICE_USERNAME>/.local/bin/uv",
         "args": [
           "--directory",
           "/Users/<PATH_TO_CLONED_DIR>/memory-journal-mcp-server",
           "run",
           "server.py"
         ]
       }
     }
   }
   ```

4. Start the server with following command or just open Claude Desktop:
   ```bash
   uv run server.py
   ```

> **Note:** Replace `<YOUR_DEVICE_USERNAME>` and `<PATH_TO_CLONED_DIR>` with your actual device username and the path to the cloned directory.
> You will get a popup to authorize the server to access your photos. It will be in local only, and no data will be shared with anyone except Claude services.

### MCP Server Initialization

When the server starts, you'll see:

```
Starting Smart Photo Journal MCP server.
```

It's now ready to process your photo queries! 🎉

---

## 🛠️ Usage

### Available Tools

1. **Location Search**

   - Description: Find photos taken in a specific location.
   - Input Example:
     ```json
     {
       "location": "Udaipur"
     }
     ```
   - Expected Output:
     ```
     Found 5 photos from Udaipur:
     📷 IMG_1234.jpg
     ...
     ```

2. **Label Search**

   - Description: Search for photos by labels or keywords.
   - Input Example:
     ```json
     {
       "label": "Birthday"
     }
     ```
   - Expected Output:
     ```
     Photos labeled as 'Birthday' (3 found):
     📷 IMG_5678.jpg
     ...
     ```

3. **People Search**

   - Description: Find photos containing specific people.
   - Input Example:
     ```json
     {
       "person": "Maa"
     }
     ```
   - Expected Output:
     ```
     Photos with Maa (10 found):
     📷 IMG_9101.jpg
     ...
     ```

4. **Photo Analysis**
   - Description: Analyze patterns in your photo library, such as the most common times or days for photo shoots.
   - Input Example:
     ```json
     {}
     ```
   - Expected Output:
     ```
     📸 Photo Taking Patterns:
     Total Photos: 200
     ...
     ```

---

## 📚 Example Use-Cases

### 1. **Family & Friends Album Organizer**

Want to gather all your family moments in one place? Use the `people-search` tool with names like "Papa" or "Mom" or "Any Friend" to find photos with specific people.

### 2. **Vacation Highlights**

Search for photos from your vacation destination using the `location-search` tool.

### 3. **Throwback Fun**

Curious about your past birthday photos? Use `label-search` with "Birthday" and relive the fun!

### 4. **Understand Your Photography Habits**

Use the `photo-analysis` tool to understand when and where you take most of your photos. Plan your next shoot accordingly!

---

## ⚡ Tips for Best Results

- Ensure your Photos library is loaded in macOS.
- Be as specific as possible with search queries for more accurate results.
- Use fuzzy matching for flexibility when you're unsure of the exact name.
