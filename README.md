<div align="center">
<img src="Icon.png" alt="Sullybase Icon" style="float: left; margin-right: 15px; width: 240px; height: 240px;">
</div>

# [Sullybase Local LLM Chat](https://sullydux.github.io/Sullybase-Local-LLM-Chat/)

A lightweight application for chatting with local LLMs via **Ollama**. Whether you're online or offline, Sullybase Local LLM Chat gives you full control over your AI conversations with privacy and speed.

---

## Features

### Sidebar (Left)
- **Chat History**: Browse all your past conversations  
- **Manage Chats**: Delete unwanted conversations anytime  
- **View-Only Mode**: Old chats open in read-only format to preserve history

### Chat Interface (Right)
- **Real-time Messaging**: Send messages and get instant responses from your selected Ollama model  
- **Stop Button**: Halt long responses mid-generation  
- **Versatile Q&A**: Usefull for math, science, coding, writing, and general knowledge questions  

---

## Requirements

- **Ollama** (must be installed and running locally)  
- Python 3.14.5+ (only if using the script version, not the bundled app)

---

## Setup

### Quick Start (macOS)

1. **Download** one of the following or clone the repository to you computer:
   - **Bundled App** (`.app` file)  
   - **Python Script** (`.py` file) — requires Python and dependencies

2. **Install Dependencies** (script version only):
   ```bash
   pip install -r requirements.txt
   ```

3. **Bypass Gatekeeper** (app version only):
   - The app is not able to be signed, so you'll need to bypass macOS Gatekeeper

4. **Run the App/Script**:
   - It will launch and show the interface  
   - A quick close-and-reopen at startup is **normal** — don't worry!

5. **Connect to Ollama**:
   - Open the Ollama app on your system  
   - Click **Refresh** at the top of the Sullybase interface to load available models  
   - Select a model from the dropdown and start chatting!

### Windows Support

Sullybase is primarily built for macOS. To run on Windows:
- Use the **Python script** instead of the bundled app  
- You may need to adjust file paths or system calls in the code  
- Ensure Ollama is installed and running on Windows

---

## Notes

- **AI-Assisted Development**: The Python scripts were coded with AI assistance  
- **Bundling**: The app was bundled using `py2app`  
- **Privacy**: All conversations stay on your device—no external data transmission  

---

## Future Updates

- **Markdown Chat Interface**: Cleaner, formatted rendering of codeblocks, equations, and tables  
- **Gemini API Support**: Optional cloud with Gemini API, you will need your own keys  

---

## Contributing

Contributions are welcome! Before adding features or fixing bugs:

1. **Ask First**: Open an issue or contact me via email  
2. **Discuss**: We'll talk through the idea to make sure it aligns with the project  
3. **Submit**: Fork, make changes, and open a pull request

Use **GitHub Issues** or **email** to notify me.

---

## License

This project is licensed under this [License](https://github.com/sullydux/Sullybase-Local-LLM-Chat/blob/main/LICENSE.txt).

---

<div align="center">
  Copyright © 2026 <strong>Sullydux</strong> on GitHub
</div>