# QMS Agent Portal

Siemens Healthineers QMS (Quality Management System) Agent Portal — a RAGFlow-based intelligent Q&A platform with knowledge base management, multi-department sharing, and direct LLM chat capabilities.

## Features

### Knowledge Base Management
- **Upload & Embed**: Upload documents (PDF, DOCX, XLSX, etc.) to create knowledge bases. Real-time progress tracking shows file upload percentage and vectorization progress.
- **Multi-Department Sharing**: Share knowledge bases to departments with granular R(read)/W(write)/S(share) permission control.
- **Point-to-Point Sharing**: Share to individual users with approval workflow. Permission hierarchy enforcement ensures sharers cannot grant permissions beyond their own.
- **Share Recall Dashboard**: Owners can view all department and individual shares in a dashboard and revoke specific shares individually.
- **Permission Hierarchy**: Bitmask-based permission system (READ=4, WRITE=2, SHARE=1) with cascading enforcement — a user with only R+S cannot grant W to others.

### Chat & Q&A
- **Direct LLM Mode**: Chat directly with GPT-5.4 (Azure OpenAI) without any agent constraints.
- **Agent Mode**: Use pre-configured QMS agents with specialized system prompts for structured Q&A.
- **Knowledge-Grounded Answers**: Both modes support binding knowledge bases for retrieval-augmented generation.
- **Multi-Tenant Retrieval**: Queries across multiple knowledge bases owned by different users, using per-user RAGFlow tokens.

### User & Department Management
- **Department Hierarchy**: MP (root) > MP-Q, MP-PLM, MP-AP, MP-MC, MP-US&DX
- **Role-Based Access**: superadmin, dept_admin, member roles with resource policies
- **Session Privacy**: Toggle chat sessions between public and private

### Security & Compliance
- **Terms of Use**: Users must accept Terms of Use before accessing the system
- **HMAC-SHA256 Authentication**: Custom token-based auth with configurable TTL
- **Audit Logging**: All operations logged with request ID, user, department, action, and status

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.10+ / Flask |
| Frontend | Vanilla HTML + CSS + JavaScript (SPA) |
| Database | SQLite (portal_auth.sqlite3) |
| LLM | Azure OpenAI GPT-5.4 |
| RAG Engine | RAGFlow SDK |
| Auth | HMAC-SHA256 signed tokens |

## Setup

### Prerequisites
- Python 3.10+
- RAGFlow server running (default: `http://127.0.0.1:9380`)
- Azure OpenAI API access (for direct chat mode)

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RAGFLOW_API_KEY` | Yes | - | RAGFlow API key |
| `RAGFLOW_BASE_URL` | No | `http://127.0.0.1:9380` | RAGFlow server URL |
| `PORTAL_DB_PATH` | No | `./data/portal_auth.sqlite3` | SQLite database path |
| `PORTAL_AUTH_SECRET` | No | auto-derived | HMAC signing secret |
| `DIRECT_CHAT_URL` | No | Azure endpoint | OpenAI-compatible API URL |
| `DIRECT_CHAT_API_KEY` | No | - | API key for direct chat |
| `DIRECT_CHAT_MODEL` | No | `gpt-5.4` | Model name |
| `PORTAL_TOKEN_TTL_SECONDS` | No | `28800` | Token expiry (8h) |

### Start

```bash
# Set environment variables
export PYTHONPATH=/path/to/ragflow-main
export RAGFLOW_API_KEY="your-api-key"
export RAGFLOW_BASE_URL="http://your-ragflow-server:9380"

# Start portal server (port 9391)
python -m extensions.qms_agent_backend.portal_server
```

Access at `http://localhost:9391/`

### Default Credentials
- Username: `MP` / Password: `12345678` (superadmin)
- Register new accounts via the login page

## Project Structure

```
extensions/qms_agent_backend/
  portal_server.py          # Flask gateway (API routes + PortalGateway class)
  portal_store.py           # SQLite store (users, depts, policies, shares, sessions)
  qms_agent_service.py      # RAGFlow agent integration
  memory_store.py           # User memory & conversation history
  agent_system_prompt.md    # QMS agent system prompt (Chinese)
  server.py                 # Standalone QMS agent HTTP server (port 9390)
  data/
    portal_auth.sqlite3     # Portal database
    qms_memory.sqlite3      # Memory database
  portal_web/
    index.html              # SPA frontend
    app.js                  # Application logic
    styles.css              # Styles
```

## API Endpoints

### Authentication
- `POST /portal/v1/auth/login` — Login
- `POST /portal/v1/auth/register` — Register
- `GET /portal/v1/me` — Current user info

### Knowledge Base
- `POST /portal/v1/kbs` — Upload KB (returns doc_ids for progress tracking)
- `GET /portal/v1/kbs/parse-status` — Poll embedding progress
- `POST /portal/v1/kbs/<id>/share-to-depts` — Share to departments
- `POST /portal/v1/kbs/<id>/share-to-user` — Share to individual user
- `GET /portal/v1/kbs/my-shares` — List all shares (for recall dashboard)
- `POST /portal/v1/kbs/<id>/revoke-selective` — Revoke specific share

### Chat
- `POST /portal/v1/sessions` — Create session
- `POST /portal/v1/chat` — Send question
- `GET /portal/v1/sessions/<id>/messages` — Get history

### Admin
- `GET /portal/v1/departments` — List departments
- `GET /portal/v1/users/search` — Search users (fuzzy match)
- `GET /portal/v1/share-requests` — Pending share requests
- `POST /portal/v1/share-requests/<id>/approve` — Approve request
- `POST /portal/v1/share-requests/<id>/reject` — Reject request
