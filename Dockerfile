# Imagen para Render (render.yaml) y Hugging Face Spaces (sdk: docker). También sirve en
# cualquier servicio que corra contenedores:  docker build -t parking . && docker run -p 7860:7860 parking
FROM python:3.11-slim

# Hugging Face corre los contenedores con el usuario 1000.
RUN useradd -m -u 1000 user
USER user
# MALLOC_ARENA_MAX: con varios hilos (video, patentes, web), glibc reserva memoria de más por
# hilo y no la devuelve; en Render (512 MB) eso termina en reinicios.
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# El video de prueba se genera al armar la imagen, así la app arranca al toque.
# El video de la calle real (tools/descargar_video_real.py) no va en la versión
# publicada: su fuente no declara licencia. Se usa solo localmente.
RUN python tools/generate_test_video.py && python -m app.demo_plates --par videos/

# Dos videos reales de estacionamiento (Intel, CC BY 4.0: se pueden publicar citando la
# fuente, que figura en la descripción de cada uno). Si no se pueden bajar, la app anda igual.
RUN python tools/descargar_videos_intel.py || echo "No se pudieron bajar los videos de Intel."

# Los modelos de patentes (unos 10 MB) se bajan al armar la imagen: así la primera
# lectura no espera la descarga, y la app no depende de GitHub para arrancar.
# Los modelos por defecto y los que piden los videos de prueba con chapas dibujadas.
RUN python -c "from app import plates, demo_plates as d; \
assert plates.create_reader() and plates.create_reader(d.DRAWN_OCR, d.DRAWN_DETECTOR), 'no se pudieron bajar los modelos'"

EXPOSE 7860
# Render (y otros servicios) dicen el puerto en $PORT; sin eso, 7860 como en Hugging Face.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
