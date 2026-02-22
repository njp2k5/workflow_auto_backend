# Google Meet Processing Backend

A production-ready FastAPI backend service that automatically processes Google Meet meetings after they end. The system fetches transcripts, summarizes meetings using LLM, extracts action items, and creates Jira issues automatically.

## Features

- **Automatic Meeting Detection**: Polls Google Meet API for ended meetings with transcripts
- **AI-Powered Summarization**: Uses Groq's LLaMA model to generate meeting summaries
- **Task Extraction**: Automatically extracts action items with assignees and due dates
- **Jira Integration**: Creates Jira issues for each extracted task
- **Persistent Storage**: Stores meeting summaries, transcripts, and task data in PostgreSQL

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                           │
├──────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
│  │  Scheduler  │───>│ Meet Client │───>│  Pipeline   │              │
│  │  (Poller)   │    │  (Google)   │    │  (LangGraph)│              │
│  └─────────────┘    └─────────────┘    └──────┬──────┘              │
│                                               │                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    LangGraph Pipeline                        │    │
│  │  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌────────┐ │    │
│  │  │ Summarize │──>│  Extract  │──>│  Create   │──>│ Store  │ │    │
│  │  │  Meeting  │   │   Tasks   │   │   Jira    │   │Results │ │    │
│  │  └───────────┘   └───────────┘   └───────────┘   └────────┘ │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                               │                      │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
│  │ LLM Client  │    │ Jira Client │    │  PostgreSQL │              │
│  │   (Groq)    │    │   (Cloud)   │    │   Database  │              │
│  └─────────────┘    └─────────────┘    └─────────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
workflow_automation_backend/
├── main.py                 # FastAPI app entry point
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
├── Dockerfile             # Container configuration
├── app/
│   ├── __init__.py
│   ├── config.py          # Environment configuration
│   ├── db.py              # Database engine & session
│   ├── models.py          # SQLAlchemy models
│   ├── meet_client.py     # Google Meet API client
│   ├── jira_client.py     # Jira Cloud API client
│   ├── llm.py             # Groq LLM interface
│   ├── pipeline.py        # LangGraph workflow
│   └── scheduler.py       # Periodic polling scheduler
├── api/
│   └── routes/
│       ├── auth.py        # Authentication endpoints
│       └── health.py      # Health check endpoints
├── db/
│   ├── base.py           # SQLAlchemy base
│   └── session.py        # Database session
├── models/
│   └── user.py           # User model
└── schemas/
    └── auth.py           # Auth schemas
```

## Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Google Cloud Project with Meet API enabled
- Jira Cloud account with API access
- Groq API account

## Setup Instructions

### 1. Clone and Install Dependencies

```bash
cd workflow_automation_backend
python -m venv venv

# Windows
.\venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example file and configure your settings:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/meet_processor
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
POSTGRES_DB=meet_processor
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Google Meet API
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# OR use OAuth2:
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REFRESH_TOKEN=your_refresh_token

# Jira Cloud
JIRA_SERVER=https://your-domain.atlassian.net
JIRA_EMAIL=your_email@example.com
JIRA_API_TOKEN=your_api_token
JIRA_PROJECT_KEY=PROJ

# Groq LLM
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.1-8b-instant

# Polling
MEET_POLL_INTERVAL=60

# Application
APP_ENV=development
DEBUG=true
LOG_LEVEL=INFO
```

### 3. Set Up Google Meet API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select existing one
3. Enable the **Google Meet REST API**
4. Create credentials:
   - For service account: Download JSON key file
   - For OAuth2: Create OAuth client ID and get refresh token
5. Grant appropriate permissions to access meeting data

### 4. Set Up Jira API Token

1. Log in to [Atlassian](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Create a new API token
3. Note your Atlassian email and token
4. Ensure your Jira project exists with the specified key

### 5. Get Groq API Key

1. Sign up at [Groq Console](https://console.groq.com)
2. Create an API key
3. Add it to your `.env` file

### 6. Initialize Database

```bash
# Create PostgreSQL database
createdb meet_processor

# Tables are created automatically on startup
```

### 7. Run the Application

```bash
# Development
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Health & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/meet/health` | Check all service health |
| GET | `/api/meet/scheduler/status` | Get scheduler status |

### Scheduler Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/meet/scheduler/start` | Start the poller |
| POST | `/api/meet/scheduler/stop` | Stop the poller |
| POST | `/api/meet/scheduler/trigger` | Trigger immediate poll |
| POST | `/api/meet/cache/clear` | Clear processed cache |

### Meeting Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/meet/meetings` | List all meetings |
| GET | `/api/meet/meetings/{id}` | Get meeting details |
| POST | `/api/meet/process` | Manually process transcript |
| DELETE | `/api/meet/meetings/{id}` | Delete meeting record |

### Manual Processing Example

```bash
curl -X POST http://localhost:8000/api/meet/process \
  -H "Content-Type: application/json" \
  -d '{
    "conference_id": "abc123",
    "transcript": "John: We need to finish the report by Friday.\nJane: I will handle the data analysis.\nJohn: Great, let us meet again next Monday.",
    "meeting_title": "Project Sync",
    "participants": ["John", "Jane"]
  }'
```

## Database Schema

### meetings table

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| conference_id | VARCHAR(255) | Google Meet conference ID (unique) |
| meeting_title | VARCHAR(500) | Meeting title |
| summary | TEXT | LLM-generated summary |
| transcript | TEXT | Full transcript |
| jira_keys | JSON | Created Jira issue keys |
| tasks | JSON | Extracted action items |
| participants | JSON | Meeting participants |
| meeting_start_time | TIMESTAMP | Meeting start time |
| meeting_end_time | TIMESTAMP | Meeting end time |
| processed | BOOLEAN | Processing status |
| processing_error | TEXT | Error message if failed |
| created_at | TIMESTAMP | Record creation time |
| updated_at | TIMESTAMP | Last update time |

### processing_logs table

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| conference_id | VARCHAR(255) | Conference ID |
| step | VARCHAR(100) | Processing step |
| status | VARCHAR(50) | Step status |
| message | TEXT | Status message |
| metadata | JSON | Additional data |
| created_at | TIMESTAMP | Log timestamp |

## LangGraph Pipeline

The processing pipeline consists of four nodes:

1. **summarize_meeting**: Generates a concise summary using LLM
2. **extract_tasks**: Extracts action items with assignees and due dates
3. **create_jira_issues**: Creates Jira tickets for each task
4. **store_results**: Persists all data to PostgreSQL

```
┌─────────────────┐     ┌─────────────────┐
│    summarize    │────>│  extract_tasks  │
│     meeting     │     │                 │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │ (on error)            │
         │                       v
         │              ┌─────────────────┐
         │              │  create_jira    │
         │              │    issues       │
         │              └────────┬────────┘
         │                       │
         v                       v
┌─────────────────────────────────────────┐
│            store_results                 │
└─────────────────────────────────────────┘
```

## Task Extraction Format

The LLM extracts tasks in this format:

```json
{
  "tasks": [
    {
      "title": "Complete data analysis report",
      "assignee": "Jane",
      "due_date": "2024-01-15"
    },
    {
      "title": "Schedule follow-up meeting",
      "assignee": "John",
      "due_date": null
    }
  ]
}
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DATABASE_URL | Yes | - | PostgreSQL connection URL |
| GOOGLE_APPLICATION_CREDENTIALS | No* | - | Path to service account JSON |
| GOOGLE_CLIENT_ID | No* | - | OAuth2 client ID |
| GOOGLE_CLIENT_SECRET | No* | - | OAuth2 client secret |
| GOOGLE_REFRESH_TOKEN | No* | - | OAuth2 refresh token |
| JIRA_SERVER | Yes | - | Jira Cloud server URL |
| JIRA_EMAIL | Yes | - | Jira account email |
| JIRA_API_TOKEN | Yes | - | Jira API token |
| JIRA_PROJECT_KEY | Yes | PROJ | Project key for issues |
| GROQ_API_KEY | Yes | - | Groq API key |
| GROQ_MODEL | No | llama-3.1-8b-instant | LLM model to use |
| MEET_POLL_INTERVAL | No | 60 | Polling interval (seconds) |
| APP_ENV | No | development | Environment (development/production/test) |
| DEBUG | No | true | Enable debug mode |
| LOG_LEVEL | No | INFO | Logging level |

*Either service account or OAuth2 credentials required for Google Meet API

## Docker Deployment

```bash
# Build image
docker build -t meet-processor .

# Run container
docker run -d \
  --name meet-processor \
  -p 8000:8000 \
  --env-file .env \
  meet-processor
```

## Troubleshooting

### Google Meet API Issues

- Ensure Meet API is enabled in Google Cloud Console
- Verify credentials have proper scopes
- Check that meetings have transcription enabled

### Jira Connection Issues

- Verify API token is valid
- Check project key exists
- Ensure email matches Atlassian account

### LLM Issues

- Verify Groq API key is valid
- Check model name is correct
- Monitor rate limits

### Database Issues

- Ensure PostgreSQL is running
- Check connection string is correct
- Verify user has proper permissions

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit changes
4. Push to branch
5. Open a Pull Request
