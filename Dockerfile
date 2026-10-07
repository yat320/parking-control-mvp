# Imagen para Hugging Face Spaces (sdk: docker). También sirve en cualquier
# servicio que corra contenedores:  docker build -t parking . && docker run -p 7860:7860 parking
FROM python:3.11-slim

# Hugging Face corre los contenedores con el usuario 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# Los videos se preparan al armar la imagen, así la app arranca al toque.
# Si no se puede bajar el video real, la app anda igual con el de prueba.
RUN python tools/generate_test_video.py \
 && (python tools/descargar_video_real.py || echo "No se pudo bajar el video real; queda solo el de prueba.")

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
