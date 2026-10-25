# 🎛️ Switchboard

One chat app for Claude and OpenAI, built with Streamlit.

## Features

- Claude (Anthropic) and OpenAI models, switchable mid-conversation
- Multiple conversations, saved locally and auto-titled
- Rename, delete, and export chats to Markdown
- Attach images, PDFs, and text/code files
- Custom system prompt, temperature, and reply length
- Regenerate replies and give thumbs up/down feedback

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your API key(s)
streamlit run app.py
```

You only need a key for the provider you use. You can also paste a key into the sidebar at runtime.

## Deploy

On [Streamlit Community Cloud](https://share.streamlit.io), point a new app at this repo's `app.py`
and add your keys under **Settings → Secrets** using the same format as `secrets.toml.example`.

Note: chat history is stored in `chats.json` on the server and shared by everyone using the app.
Swap in per-user storage before sharing it publicly.
