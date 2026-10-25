"""
Switchboard: one chat app for Claude and OpenAI, built with Streamlit.

Improvements over the official tutorial:
  - Claude (Anthropic) and OpenAI, switchable mid-conversation
  - Multiple conversations in a sidebar, saved to disk (survive refresh/restart)
  - Auto-titled chats, rename, delete, export to Markdown
  - System prompt, temperature, max tokens
  - Attach images, PDFs, and text/code files to a message
  - Regenerate the last reply, thumbs up/down feedback
  - Clear errors instead of crashes; API keys from secrets, env, or sidebar
"""

import base64
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------- config ----

st.set_page_config(page_title="Switchboard", page_icon="🎛️", layout="centered")

DATA_FILE = Path(__file__).parent / "chats.json"

PROVIDERS = {
    "Anthropic": {
        "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
        "secret": "ANTHROPIC_API_KEY",
    },
    "OpenAI": {
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
        "secret": "OPENAI_API_KEY",
    },
}

IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp"}
TEXT_TYPES = {"txt", "md", "py", "js", "ts", "json", "csv", "html", "css", "sql", "yaml", "yml"}
ALLOWED_TYPES = sorted(IMAGE_TYPES | TEXT_TYPES | {"pdf"})
DEFAULT_SYSTEM = "You are a helpful, concise assistant. Use Markdown when it helps."


# ----------------------------------------------------------- persistence ----

def load_chats() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            st.warning("chats.json was unreadable, so a fresh history was started.")
    return {}


def save_chats() -> None:
    DATA_FILE.write_text(json.dumps(st.session_state.chats, indent=2))


def new_chat() -> str:
    chat_id = uuid.uuid4().hex[:10]
    st.session_state.chats[chat_id] = {
        "title": "New chat",
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    }
    st.session_state.current = chat_id
    save_chats()
    return chat_id


def current_chat() -> dict:
    return st.session_state.chats[st.session_state.current]


if "chats" not in st.session_state:
    st.session_state.chats = load_chats()
if "current" not in st.session_state or st.session_state.current not in st.session_state.chats:
    # Reopen the most recent chat, or start one
    if st.session_state.chats:
        st.session_state.current = max(
            st.session_state.chats, key=lambda k: st.session_state.chats[k]["created"]
        )
    else:
        new_chat()


# --------------------------------------------------------------- helpers ----

def get_api_key(provider: str) -> str | None:
    name = PROVIDERS[provider]["secret"]
    typed = st.session_state.get(f"key_{provider}")
    if typed:
        return typed
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass  # no secrets.toml present
    return os.getenv(name)


def file_to_attachment(f) -> dict:
    """Turn an uploaded file into a JSON-serialisable attachment."""
    ext = f.name.rsplit(".", 1)[-1].lower()
    raw = f.getvalue()
    if ext in IMAGE_TYPES:
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        return {"name": f.name, "kind": "image", "mime": mime,
                "data": base64.b64encode(raw).decode()}
    if ext == "pdf":
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return {"name": f.name, "kind": "text", "text": text.strip() or "(no extractable text)"}
    return {"name": f.name, "kind": "text", "text": raw.decode("utf-8", errors="replace")}


def message_text_with_files(msg: dict) -> str:
    """User text plus any text-file contents, as one string for the model."""
    parts = [msg["content"]] if msg["content"] else []
    for a in msg.get("attachments", []):
        if a["kind"] == "text":
            parts.append(f'<file name="{a["name"]}">\n{a["text"]}\n</file>')
    return "\n\n".join(parts) or "(see attached)"


def to_anthropic(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": a["mime"], "data": a["data"]}}
            for a in m.get("attachments", []) if a["kind"] == "image"
        ]
        blocks.append({"type": "text", "text": message_text_with_files(m)})
        out.append({"role": m["role"], "content": blocks})
    return out


def to_openai(messages: list[dict], system: str) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        blocks = [{"type": "text", "text": message_text_with_files(m)}]
        blocks += [
            {"type": "image_url", "image_url": {"url": f'data:{a["mime"]};base64,{a["data"]}'}}
            for a in m.get("attachments", []) if a["kind"] == "image"
        ]
        out.append({"role": m["role"], "content": blocks})
    return out


def stream_reply(provider, model, messages, system, temperature, max_tokens):
    """Yield text chunks from whichever provider is selected."""
    key = get_api_key(provider)
    if not key:
        raise RuntimeError(f"No {provider} API key. Add one in the sidebar or in .streamlit/secrets.toml.")

    if provider == "Anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        with client.messages.stream(
            model=model, system=system or anthropic.NOT_GIVEN,
            messages=to_anthropic(messages),
            temperature=temperature, max_tokens=max_tokens,
        ) as stream:
            yield from stream.text_stream
    else:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        stream = client.chat.completions.create(
            model=model, messages=to_openai(messages, system),
            temperature=temperature, max_tokens=max_tokens, stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def auto_title(text: str) -> str:
    text = " ".join(text.split())
    return (text[:40] + "…") if len(text) > 40 else (text or "New chat")


def export_markdown(chat: dict) -> str:
    lines = [f"# {chat['title']}", f"_Started {chat['created']}_", ""]
    for m in chat["messages"]:
        who = "You" if m["role"] == "user" else f"Assistant ({m.get('model', '')})"
        lines += [f"**{who}:**", "", m["content"], ""]
        for a in m.get("attachments", []):
            lines.append(f"> attached: {a['name']}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------- sidebar ----

with st.sidebar:
    st.markdown("## 🎛️ Switchboard")
    if st.button("➕ New chat", use_container_width=True):
        new_chat()
        st.rerun()

    st.caption("Conversations")
    for cid, chat in sorted(st.session_state.chats.items(),
                            key=lambda kv: kv[1]["created"], reverse=True):
        is_current = cid == st.session_state.current
        if st.button(chat["title"], key=f"chat_{cid}", use_container_width=True,
                     type="primary" if is_current else "secondary"):
            st.session_state.current = cid
            st.rerun()

    st.divider()

    with st.expander("Model & settings", expanded=False):
        provider = st.radio("Provider", list(PROVIDERS), horizontal=True, key="provider")
        model_choice = st.selectbox("Model", PROVIDERS[provider]["models"] + ["Custom…"],
                                    key=f"model_{provider}")
        model = (st.text_input("Model name", key=f"custom_{provider}")
                 if model_choice == "Custom…" else model_choice)
        system = st.text_area("System prompt", DEFAULT_SYSTEM, height=100, key="system")
        temperature = st.slider("Temperature", 0.0, 1.0, 0.7, 0.05, key="temperature")
        max_tokens = st.slider("Max reply length (tokens)", 256, 8192, 2048, 256, key="max_tokens")
        if not get_api_key(provider):
            st.text_input(f"{provider} API key", type="password", key=f"key_{provider}",
                          help="Only kept for this session. Use secrets.toml to persist.")

    chat = current_chat()
    with st.expander("This chat", expanded=False):
        def rename():
            title = st.session_state.rename_box.strip()
            if title:
                current_chat()["title"] = title
                save_chats()
            st.session_state.rename_box = ""

        st.text_input("Rename", key="rename_box", placeholder=chat["title"], on_change=rename)
        st.download_button("Export as Markdown", export_markdown(chat),
                           file_name=f"{chat['title'][:30] or 'chat'}.md",
                           use_container_width=True)
        if st.button("Delete this chat", use_container_width=True):
            del st.session_state.chats[st.session_state.current]
            save_chats()
            del st.session_state.current
            st.rerun()


# ------------------------------------------------------------------ main ----

def render_message(i: int, m: dict) -> None:
    with st.chat_message(m["role"]):
        for a in m.get("attachments", []):
            if a["kind"] == "image":
                st.image(base64.b64decode(a["data"]), width=240)
            else:
                st.caption(f"📎 {a['name']}")
        st.markdown(m["content"])
        if m["role"] == "assistant":
            meta = m.get("model", "")
            if m.get("seconds"):
                meta += f" · {m['seconds']:.1f}s"
            st.caption(meta)
            fb = st.feedback("thumbs", key=f"fb_{st.session_state.current}_{i}")
            if fb is not None and m.get("feedback") != fb:
                m["feedback"] = fb
                save_chats()


def generate() -> None:
    """Stream an assistant reply to the current chat's history."""
    chat = current_chat()
    started = time.time()
    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(stream_reply(
                provider, model, chat["messages"], system, temperature, max_tokens))
        except Exception as e:
            st.error(f"{type(e).__name__}: {e}")
            return
    chat["messages"].append({
        "role": "assistant", "content": reply,
        "model": model, "seconds": time.time() - started,
    })
    save_chats()
    st.rerun()  # refresh sidebar title and feedback widgets


chat = current_chat()

if not chat["messages"]:
    st.markdown("### What can I help with?")
    st.caption(f"Talking to **{model}**. Attach images, PDFs, or code with the 📎 in the box below.")

for i, m in enumerate(chat["messages"]):
    render_message(i, m)

# Regenerate the last reply
if chat["messages"] and chat["messages"][-1]["role"] == "assistant":
    if st.button("🔄 Regenerate", key="regen"):
        chat["messages"].pop()
        save_chats()
        st.session_state.pending = True
        st.rerun()

if st.session_state.pop("pending", False):
    generate()

submission = st.chat_input(
    "Message…", accept_file="multiple", file_type=ALLOWED_TYPES,
)

if submission:
    attachments = []
    for f in submission.files:
        try:
            attachments.append(file_to_attachment(f))
        except Exception as e:
            st.error(f"Couldn't read {f.name}: {e}")
    user_msg = {"role": "user", "content": submission.text or "", "attachments": attachments}
    if user_msg["content"] or attachments:
        if not chat["messages"]:
            chat["title"] = auto_title(submission.text or attachments[0]["name"])
        chat["messages"].append(user_msg)
        save_chats()
        render_message(len(chat["messages"]) - 1, user_msg)
        generate()
