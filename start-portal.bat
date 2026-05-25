@echo off
chcp 65001 >nul
echo Starting MP Agent Portal Server...

set PYTHONPATH=D:\XZY\RAGFLOW_Develop_Project\ragflow-main\ragflow-main
set RAGFLOW_API_KEY=ragflow-QdGCG7vYP4h27T9dRF1ynx6SiAYwvEIacjntUM15lUU
set RAGFLOW_BASE_URL=http://127.0.0.1:9380
set PORTAL_DB_PATH=D:\XZY\RAGFLOW_Develop_Project\ragflow-main\ragflow-main\extensions\qms_agent_backend\data\portal_auth.sqlite3

cd /d D:\XZY\RAGFLOW_Develop_Project\ragflow-main\ragflow-main
D:\XZY\RAGFLOW_Develop_Project\.venv\Scripts\python.exe -m extensions.qms_agent_backend.portal_server
pause
