FROM semgrep/semgrep:latest

# Install FastAPI wrapper dependencies (Alpine-based image)
RUN python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn

COPY server.py /app/server.py
COPY rules/ /rules/

WORKDIR /app

EXPOSE 9090

CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9090"]
