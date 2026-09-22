# Starts the API and the UI, each in its own window, using the existing venv and node_modules.
# One-time setup (venv, pip install, npm install) is in README.md; this script does not do it.
Start-Process powershell -WorkingDirectory "$PSScriptRoot\backend" `
  -ArgumentList '-NoExit', '-Command', '.\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000'
Start-Process powershell -WorkingDirectory "$PSScriptRoot\frontend" `
  -ArgumentList '-NoExit', '-Command', 'npm run dev'
