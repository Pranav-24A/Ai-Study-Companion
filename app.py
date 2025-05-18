from flask import Flask, request, jsonify
import json
from flask_cors import CORS
import requests
import PyPDF2
import os
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
OPENROUTER_API_KEY = "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"
MODEL = "anthropic/claude-3-haiku"
PDF_PATH = "uploads/test2.pdf"
CHAT_HISTORY_FILE = "chat_history.json"

class StudyAssistant:
    def __init__(self, pdf_path):
        self.pdf_text = self.extract_pdf_text(pdf_path)
        self.chat_sessions = {}
        self.load_chat_history()

    def extract_pdf_text(self, pdf_path):
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                return " ".join([page.extract_text() or '' for page in reader.pages])
        except Exception as e:
            print(f"[PDF Error]: {e}")
            return ""

    def load_chat_history(self):
        try:
            if os.path.exists(CHAT_HISTORY_FILE):
                with open(CHAT_HISTORY_FILE, 'r') as f:
                    self.chat_sessions = json.load(f)
            else:
                # Create a default chat if none exists
                self.create_new_session()
        except Exception as e:
            print(f"Error loading chat history: {e}")
            self.chat_sessions = {}
            self.create_new_session()

    def save_chat_history(self):
        try:
            with open(CHAT_HISTORY_FILE, 'w') as f:
                json.dump(self.chat_sessions, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving chat history: {e}")

    def create_new_session(self):
        session_id = str(uuid.uuid4())
        self.chat_sessions[session_id] = {
            'id': session_id,
            'title': 'New Chat',
            'messages': [],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        self.save_chat_history()
        return session_id

    def add_message_to_session(self, session_id, role, content):
        if session_id not in self.chat_sessions:
            return False
        
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        }
        
        self.chat_sessions[session_id]['messages'].append(message)
        self.chat_sessions[session_id]['updated_at'] = datetime.now().isoformat()
        
        # Update title if first user message
        if role == 'user' and len(self.chat_sessions[session_id]['messages']) == 1:
            title = content[:50] + ("..." if len(content) > 50 else "")
            self.chat_sessions[session_id]['title'] = title
        
        self.save_chat_history()
        return True

    def verify_pdf_content(self, response):
        if not self.pdf_text:
            return False
        
        # Check if any significant words from response appear in PDF
        response_words = set(word.lower() for word in response.split() if len(word) > 4)
        pdf_words = set(self.pdf_text.lower().split())
        
        matching_words = response_words & pdf_words
        return len(matching_words) >= 3  # At least 3 matching significant words

    def chat_with_ai(self, message, session_id=None):
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:3000",
            "X-Title": "Smart Study Assistant",
            "Content-Type": "application/json"
        }

        system_msg = {
            "role": "system",
            "content": f"""You are a helpful study assistant. Follow these rules:
1. If the answer is directly found in the provided PDF content, start your response with "[PDF] "
2. For general knowledge answers, start with "[General] "
3. Be concise but thorough in your answers
4. When quoting from PDF, keep the original meaning but make it clear
"""
        }
        
        # Get previous messages from this session if it exists
        messages = [system_msg]
        if session_id and session_id in self.chat_sessions:
            for msg in self.chat_sessions[session_id]['messages'][-6:]:  # Last 6 messages for context
                messages.append({"role": msg['role'], "content": msg['content']})
        
        messages.append({"role": "user", "content": message})

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": MODEL,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 1500
                },
                timeout=45
            )
            response.raise_for_status()
            data = response.json()

            if "choices" not in data or len(data["choices"]) == 0:
                return "[General] Error: Invalid response from AI"

            reply = data["choices"][0]["message"]["content"].strip()

            # Ensure response has proper prefix
            if not (reply.startswith("[PDF]") or reply.startswith("[General]")):
                if self.verify_pdf_content(reply):
                    reply = "[PDF] " + reply
                else:
                    reply = "[General] " + reply

            return reply

        except requests.exceptions.RequestException as e:
            return f"[General] API Error: {str(e)}"
        except Exception as e:
            return f"[General] Unexpected Error: {str(e)}"

    def generate_response(self, message, session_id=None):
        if not session_id or session_id not in self.chat_sessions:
            session_id = self.create_new_session()
        
        response = self.chat_with_ai(message, session_id)
        
        if response.startswith("[PDF]"):
            content = response[5:].strip()
            source = "pdf"
        elif response.startswith("[General]"):
            content = response[9:].strip()
            source = "general"
        else:
            content = response.strip()
            source = "chat"
        
        # Save messages to session
        self.add_message_to_session(session_id, "user", message)
        self.add_message_to_session(session_id, "assistant", content)
        
        return {
            "content": content,
            "source": source,
            "session_id": session_id
        }

# Initialize assistant
if not os.path.exists(PDF_PATH):
    raise FileNotFoundError(f"PDF file not found at {PDF_PATH}")

assistant = StudyAssistant(PDF_PATH)

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    message = data.get('message')
    session_id = data.get('session_id')
    
    if not message or not isinstance(message, str):
        return jsonify({"success": False, "message": "Invalid message provided"}), 400

    try:
        response = assistant.generate_response(message, session_id)
        return jsonify({"success": True, "response": response})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/chats', methods=['GET'])
def get_chats():
    try:
        chats = list(assistant.chat_sessions.values())
        chats.sort(key=lambda x: x['updated_at'], reverse=True)
        return jsonify({"success": True, "chats": chats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/chat/<session_id>', methods=['GET'])
def get_chat(session_id):
    if session_id not in assistant.chat_sessions:
        return jsonify({"success": False, "message": "Chat not found"}), 404
    
    return jsonify({
        "success": True,
        "chat": assistant.chat_sessions[session_id]
    })

@app.route('/api/chat/<session_id>', methods=['DELETE'])
def delete_chat(session_id):
    if session_id in assistant.chat_sessions:
        try:
            del assistant.chat_sessions[session_id]
            assistant.save_chat_history()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False, "message": "Chat not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
