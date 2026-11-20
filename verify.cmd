@echo off
REM S5-GW-025 一键复跑：在 PostgreSQL 16 上执行 alembic 与 pytest，输出写入 RUN_LOG.txt
REM 用法：设置 PY 为解释器（可选）、REVIEW_DB_DSN 为 PG16 空库连接串（可选），然后双击本文件。
setlocal
if not defined PY set "PY=C:\Users\yang\asst1837-venv\Scripts\python.exe"
if not defined REVIEW_DB_DSN set "REVIEW_DB_DSN=postgresql+psycopg://postgres@127.0.0.1:5432/review2149"
"%PY%" run_verify.py
endlocal
