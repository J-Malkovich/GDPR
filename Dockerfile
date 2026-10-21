FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY gdpr.py test_gdpr.py ./
# a failing test fails the build
RUN python -m unittest test_gdpr
RUN useradd -r -u 10001 gdpr
USER gdpr
CMD ["python","-u","gdpr.py"]
