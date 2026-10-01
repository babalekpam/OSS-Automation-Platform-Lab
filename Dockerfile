FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV OSS_LAB_RELEASE=n
EXPOSE 8080
CMD ["uvicorn", "oss_lab.main:app", "--host", "0.0.0.0", "--port", "8080"]
