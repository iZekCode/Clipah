# Environment Setup Guide

## API Keys Configuration

This application requires API keys from two services:

### 1. AssemblyAI API Key
- **Purpose**: Audio transcription and speaker diarization
- **Get your key**: Visit [AssemblyAI Dashboard](https://www.assemblyai.com/dashboard/)
- **Environment variable**: `ASSEMBLYAI_API_KEY`

### 2. Google Gemini AI API Key
- **Purpose**: Video content analysis and clip generation
- **Get your key**: Visit [Google AI Studio](https://aistudio.google.com/app/apikey)
- **Environment variable**: `GROQ_API_KEY`

## Setup Instructions

### Step 1: Set up Python Virtual Environment (Recommended)

A virtual environment isolates your project dependencies from your system Python installation.

**For Windows (PowerShell/Command Prompt):**
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# To deactivate later (when you're done working)
deactivate
```

**For macOS/Linux:**
```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# To deactivate later (when you're done working)
deactivate
```

**Verify virtual environment is active:**
- Your terminal prompt should show `(venv)` at the beginning
- Run `python --version` to confirm you're using the correct Python version

### Step 2: Configure Environment Variables

1. **Copy the environment template**:
   ```bash
   cp .env.example .env
   ```

2. **Edit the `.env` file** and replace the placeholder values with your actual API keys:
   ```env
   ASSEMBLYAI_API_KEY=your_actual_assemblyai_key_here
   GROQ_API_KEY=your_actual_groq_api_key_here
   ```

### Step 3: Install Python Dependencies

Make sure your virtual environment is activated (you should see `(venv)` in your terminal prompt), then install the required packages:

```bash
pip install -r requirements.txt
```

### Step 4: Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` by default.

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASSEMBLYAI_API_KEY` | ✅ Yes | - | AssemblyAI API key for transcription |
| `GROQ_API_KEY` | ✅ Yes | - | Groq API key |
| `FLASK_DEBUG` | No | True | Enable/disable Flask debug mode |
| `FLASK_ENV` | No | development | Flask environment (development/production) |
| `FLASK_HOST` | No | 0.0.0.0 | Flask server host |
| `FLASK_PORT` | No | 5000 | Flask server port |

## Security Notes

- ⚠️ **Never commit your `.env` file to version control**
- The `.env` file is already included in `.gitignore`
- Keep your API keys secure and rotate them regularly
- Use different API keys for different environments (dev/staging/prod)

## Virtual Environment Best Practices

### Why use a virtual environment?
- **Isolation**: Keeps project dependencies separate from system Python
- **Version Control**: Ensures consistent package versions across different machines
- **Clean Development**: Prevents conflicts between different projects
- **Easy Deployment**: Makes it easier to replicate the environment on production servers

### Managing your virtual environment:
```bash
# Always activate before working on the project
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# Check installed packages
pip list

# Update requirements.txt if you install new packages
pip freeze > requirements.txt

# Deactivate when you're done working
deactivate
```

## Troubleshooting

### Common Issues:

**1. Python command not found:**
- Make sure Python is installed and added to your system PATH
- Try `python3` instead of `python` on macOS/Linux

**2. Virtual environment activation fails:**
- On Windows, you might need to change execution policy:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

**3. Permission denied errors:**
- Run terminal as administrator (Windows) or use `sudo` (macOS/Linux) if needed
- Make sure you have write permissions in the project directory

**4. Package installation fails:**
- Upgrade pip first: `pip install --upgrade pip`
- If specific packages fail, try installing them individually

**5. API key errors:**
- Double-check that your `.env` file is in the project root directory
- Ensure there are no extra spaces around the `=` sign in your `.env` file
- Verify your API keys are valid and have the necessary permissions
