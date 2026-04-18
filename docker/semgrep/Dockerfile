FROM semgrep/semgrep:latest

# Install FastAPI wrapper dependencies
RUN pip install --no-cache-dir fastapi uvicorn

COPY server.py /app/server.py
COPY rules/ /rules/

WORKDIR /app

EXPOSE 9090

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9090"]
