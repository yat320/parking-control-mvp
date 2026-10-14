# Imagen para Render (render.yaml) y Hugging Face Spaces (sdk: docker). También sirve en
# cualquier servicio que corra contenedores:  docker build -t parking . && docker run -p 7860:7860 parking
FROM python:3.11-slim

# Hugging Face corre los contenedores con el usuario 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# El video de prueba se genera al armar la imagen, así la app arranca al toque.
# El video de la calle real (tools/descargar_video_real.py) no va en la versión
# publicada: su fuente no declara licencia. Se usa solo localmente.
RUN python tools/generate_test_video.py

EXPOSE 7860
# Render (y otros servicios) dicen el puerto en $PORT; sin eso, 7860 como en Hugging Face.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
