# Образ стенда ШЕКАРЫ: API протокола (online/api.py) и дашборд (dashboard/app.py).
# Обучение и эксперименты в образ не входят — они запускаются локально (README).
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "numpy>=1.26" "polars>=1.0" "duckdb>=0.10" "pyarrow>=15" \
    "fastapi>=0.110" "uvicorn>=0.29" "streamlit>=1.33" "pynacl>=1.5"

COPY protocol/ protocol/
COPY trace/ trace/
COPY online/ online/
COPY data/__init__.py data/amlworld.py data/split.py data/stats.py data/labels.py data/channel_map.py data/
COPY data/split_decision.json data/
COPY demo/__init__.py demo/pick_case.py demo/
COPY dashboard/ dashboard/

EXPOSE 8000 8501
CMD ["python", "-m", "online.api", "--banks", "006,011,01292", "--host", "0.0.0.0"]
