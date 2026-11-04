# The backend as one container, for a Hugging Face Docker Space or any host that runs an image.
# ffmpeg 8 tone-maps an HDR phone video in its scale filter and Debian's ffmpeg is older, so the
# binaries come from a static build of the version the backend was tested with.
FROM mwader/static-ffmpeg:8.1.2 AS ffmpeg

FROM python:3.13-slim
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

# Hugging Face runs the container as uid 1000, and authored skills are saved under skills/compiled.
RUN useradd -m -u 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --user -r requirements.txt -c requirements.lock
COPY --chown=user app app
COPY --chown=user skills skills

# One process: sessions and digest jobs live in its memory. The host's proxy terminates HTTPS.
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips '*'"]
