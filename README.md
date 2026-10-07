---
title: Parking Control MVP
emoji: 🚗
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Control de ingreso y egreso de autos con video
---

# Parking Control MVP

Sistema inicial para controlar el ingreso y egreso de autos en una cochera a partir de video:
detecta vehículos, registra entradas y salidas cuando cruzan una línea virtual, simula la
barrera, calcula la estadía y el monto, guarda todo en SQLite y lo muestra en una web local.

Fuera de alcance en esta etapa: lectura de patentes (OCR), pagos reales y hardware real
(la barrera está simulada, con un punto de enganche para conectarla después).

## Usarlo online con un link fijo (Hugging Face)

La app se publica en [Hugging Face Spaces](https://huggingface.co/spaces): un link que se abre
desde la PC o el celular, sin Codespace ni terminal. Se configura una sola vez:

1. Creá una cuenta gratis en https://huggingface.co/join.
2. Entrá a https://huggingface.co/settings/tokens → **Create new token** → tipo **Write** →
   nombre `github` → **Create token**, y copiá el token (empieza con `hf_`).
3. En GitHub, en este repo: **Settings → Secrets and variables → Actions → New repository
   secret**. Nombre: `HF_TOKEN`; valor: el token. **Add secret**.
4. **Actions → Publicar en Hugging Face → Run workflow**. Al terminar, el resumen de la corrida
   muestra el link de la app (`https://<tu-usuario>-parking-control-mvp.hf.space`). La primera vez
   tarda unos minutos en armarse.

Desde ahí, cada cambio que se sube a `main` se publica solo. El Space se crea **privado**: para
verlo tenés que estar logueado en Hugging Face; para mostrárselo a otros, en el Space →
**Settings → Change visibility**. En el plan gratis la app se duerme si nadie la usa por un
tiempo, y la primera visita tarda un minuto en despertar. Los eventos se guardan mientras la app
está despierta: al dormirse o reiniciarse, la base arranca vacía. Todos los que abren el link ven
la misma barrera y los mismos eventos.

La imagen es el `Dockerfile` de este repo; sirve también para cualquier servicio que corra
contenedores (`docker build -t parking . && docker run -p 7860:7860 parking`).

## Usarlo desde GitHub, sin instalar nada

### Panel web en un Codespace

[![Abrir en GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/yat320/parking-control-mvp?quickstart=1)

1. Tocá el botón de arriba (o en el repo: **Code → Codespaces → Create codespace on main**).
2. Esperá un par de minutos la primera vez: instala las dependencias y arranca el servidor solo.
3. Se abre el panel en otra pestaña. Si no se abre: pestaña **PORTS** de abajo → puerto **8000** → ícono del globo.
4. Tocá **Procesar video**.

Funciona desde el navegador de la PC o del celular. El Codespace se apaga solo después de 30 min
sin uso; para volver: https://github.com/codespaces y elegir el de este repo (el servidor arranca
de nuevo al abrirlo). Las cuentas personales tienen horas gratis por mes; ver el uso en
https://github.com/settings/billing.

### Demo automática en Actions

En cada push, y cuando quieras desde **Actions → Demo → Run workflow**, GitHub corre las pruebas,
procesa el video de prueba y deja:

- la tabla de eventos en el resumen de la corrida,
- el video procesado, el resumen y la base SQLite para descargar en **Artifacts → demo-procesada**
  (también el video real procesado, `calle_real_procesado.mp4`).

## Probar con un video real

```bash
python tools/descargar_video_real.py
```

Baja `videos/calle_real.avi`: 12,5 s de una calle real vista desde arriba (320×176), con 5 autos
que cruzan de izquierda a derecha. El sistema registra las 5 entradas (verificado cuadro por
cuadro); no hay salidas porque ningún auto cruza en el otro sentido. En el Codespace se baja solo.
En la web elegilo en el selector de al lado de **Procesar video**; por consola:

```bash
python -m app.video_processor --video videos/calle_real.avi --reset
```

Fuente: dataset del proyecto [simple_vehicle_counting](https://github.com/andrewssobral/simple_vehicle_counting)
de Andrews Sobral. Ese repo no declara licencia, por eso el video no se sube a este repositorio:
el script lo baja para pruebas locales.

### Ajustes por video

Cada video puede tener al lado un `.json` con el mismo nombre (`videos/calle_real.json`) que
cambia la configuración solo para ese video. En ese video los autos cruzan de costado, así que
la línea es vertical:

```json
{
  "descripcion": "Texto que se muestra en la web",
  "line": [0.5, 0.95, 0.5, 0.05],
  "entry_direction": "down",
  "min_area": 300
}
```

- `line`: `x1, y1, x2, y2` en fracciones del ancho y alto. Regla: parado en (x1,y1) mirando
  hacia (x2,y2), pasar hacia tu **derecha** es **entrada**. Con una línea horizontal de izquierda
  a derecha, entrar es cruzar hacia abajo; con una vertical dibujada de abajo hacia arriba,
  entrar es cruzar hacia la derecha. `entry_direction: "up"` lo invierte.
- `min_area`: área mínima en píxeles del video original (bajala en videos chicos).

Con tu propio video: copialo a `videos/`, creale su `.json` con la línea donde está la barrera y
elegilo en la web. Los videos de menos de 640 px de ancho se agrandan para dibujar, así los
textos se leen.

## Requisitos (para correrlo en una PC)

- Python 3.10 o más nuevo
- No hace falta GPU, internet ni servicios pagos.

## Instalación en una PC

```bash
cd parking-control-mvp
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Correr la demo

### Opción A: con la interfaz web

```bash
uvicorn app.main:app --reload
```

Abrir http://localhost:8000 y tocar **Procesar video**. Si no existe `videos/video_test.mp4`,
el servidor genera uno de prueba al arrancar (40 s: 3 autos entran y los 3 salen).

En la web se ve:

- el video procesado en vivo (línea virtual, cajas de los autos, "AUTO DETECTADO",
  "BARRERA ABIERTA/CERRADA", "EVENTO REGISTRADO"),
- el estado de la barrera arriba a la derecha,
- entradas, salidas, autos adentro y total recaudado,
- la tabla de últimos eventos (hora, vehículo, tracking, barrera, estadía, monto),
- el formulario de tarifa (precio por hora, fracción, tolerancia sin cargo y tope por día),
- **Descargar todo (CSV)** con todos los eventos para planilla,
- **Resetear demo** (borra los eventos y cierra la barrera; la tarifa se mantiene),
- **Abrir barrera (manual)**, como el botón de la cabina.

Si está tildado "Guardar video procesado", al terminar queda en `output/processed_video.mp4`.

### Opción B: por consola, sin web

```bash
python -m app.video_processor --reset
```

Procesa el video lo más rápido posible, imprime cada evento y cada cambio de la barrera, guarda
`output/processed_video.mp4` y muestra un resumen. Salida esperada:

```
[BARRERA] ABIERTA (entrada track 1)
[EVENTO] t=  4.50s EVENTO REGISTRADO: ENTRADA V0001
[BARRERA] CERRADA (tiempo cumplido)
...
[EVENTO] t= 17.20s EVENTO REGISTRADO: SALIDA V0001 12m 42s $250
...
Resumen: 6 eventos en este video | entradas 3 | salidas 3 | adentro 0 | recaudado $1000.00
```

Otras opciones:

```bash
python -m app.video_processor --video videos/mi_video.mp4   # otro video
python -m app.video_processor --no-output                   # sin guardar video
python -m app.video_processor --realtime                    # a la velocidad del video
python -m app.video_processor --show                        # ventana (requiere opencv-python, no headless)
python tools/generate_test_video.py --seconds 60            # regenerar el video de prueba
```

### Pruebas

```bash
pip install httpx   # solo para las pruebas de la API
python -m unittest discover tests -v
```

Prueban la tarifa, la barrera, la línea virtual y el recorrido completo con el video de prueba
(tienen que salir exactamente 3 entradas y 3 salidas, todas cobradas). Usan una base temporal:
no tocan `data/parking.db`.

## Cómo funciona

```
video ──> detector ──> tracker ──> ¿cruzó la línea? ──> barrera.open() ──> SQLite ──> web
          (motion|yolo)  (ids)       entrada / salida      se cierra sola      eventos     /api/estado
```

| Archivo | Qué hace |
|---|---|
| `app/main.py` | Servidor FastAPI: panel web, API y stream MJPEG del video procesado. |
| `app/video_processor.py` | Bucle por frame: detecta, trackea, detecta cruces, registra eventos, dibuja y guarda el video. También es el comando de consola. |
| `app/detection.py` | Detectores. `MotionDetector` (sustracción de fondo) y `YoloDetector` (opcional). |
| `app/tracker.py` | Tracker por centroides: le da un `tracking_id` estable a cada auto. |
| `app/barrier_controller.py` | Barrera simulada: abierta/cerrada, se cierra sola a los N segundos. |
| `app/pricing.py` | Tarifa: fracción iniciada (mínimo una), tolerancia sin cargo y tope por día. |
| `app/database.py` | SQLite: tabla `eventos` y tabla `config` (tarifa). |
| `app/demo_video.py` | Genera el video sintético de prueba. |
| `app/config.py` | Configuración por variables de entorno. |

### Estrategia de detección (simple)

`MotionDetector` usa sustracción de fondo MOG2 de OpenCV: con la cámara fija, aprende cómo se ve
la entrada vacía y marca lo que cambia. Se descartan las sombras, se limpia el ruido con
operaciones morfológicas y cada mancha de al menos `PARKING_MIN_AREA` píxeles es un vehículo.
El tracker une las manchas de un frame con las del anterior por cercanía, y cuando el centro de
un auto pasa de un lado al otro de la línea virtual se registra el evento:

- hacia abajo (de afuera hacia la cochera): **entrada**,
- hacia arriba: **salida**.

Un auto tiene que verse al menos 3 frames para contar como "válido", y cada track registra a lo
sumo una entrada y una salida, así el temblequeo sobre la línea no duplica eventos.

Limitaciones conocidas: cualquier cosa grande que se mueva cuenta (una persona cerca de la cámara,
un perro); un auto detenido mucho tiempo termina siendo "fondo"; con luz muy cambiante (sol y
nubes) aparecen falsos positivos. Para el video de prueba y una cámara fija bien ubicada alcanza.

### Cómo se empareja una salida con su entrada

Sin patentes no hay forma de saber qué auto es cuál, así que la salida se empareja con la
**entrada abierta más vieja** (FIFO). Cuando se agregue OCR, alcanza con guardar la patente en
`vehicle_id` y emparejar por patente en `database.register_exit()`.

### Escala de tiempo de la demo

En un video de 40 s las estadías durarían segundos y el monto sería siempre el mínimo. Por eso
la demo usa `PARKING_TIME_SCALE=60`: **1 segundo de video = 1 minuto de estadía**, y la hora de
cada evento es la hora de inicio del procesamiento más ese tiempo escalado. Con una cámara real
hay que usar `PARKING_TIME_SCALE=1`.

### Barrera

La barrera recibe el tiempo del video, no el reloj de la PC: así, aunque el video se procese
más rápido que en tiempo real, se cierra a los N segundos de video y en el video de salida se ve
igual que en vivo. Para conectar una barrera real (relé, PLC, GPIO de una Raspberry) hay que
completar `BarrierController._actuate()`.

## Base de datos

`data/parking.db` se crea sola al arrancar.

Tabla `eventos`:

| Columna | Descripción |
|---|---|
| `id` | autoincremental |
| `tipo_evento` | `entrada` o `salida` |
| `timestamp` | hora del evento (ISO 8601) |
| `vehicle_id` | `V0001`, `V0002`… (la salida lleva el de su entrada) |
| `tracking_id` | id del tracker en el video |
| `estado_barrera` | estado de la barrera al registrar el evento |
| `monto` | importe cobrado (solo salidas) |
| `duracion_seg` | estadía en segundos (solo salidas) |
| `entrada_id` | en una salida, la entrada que cierra |

Tabla `config`: `tarifa_hora`, `fraccion_min`, `tolerancia_min` y `tope_diario`.

Para mirarla a mano: `sqlite3 data/parking.db "SELECT * FROM eventos"`.

## Configuración

Todo por variables de entorno (valores por defecto entre paréntesis):

| Variable | Para qué |
|---|---|
| `PARKING_VIDEO` (`videos/video_test.mp4`) | video a procesar |
| `PARKING_OUTPUT` (`output/processed_video.mp4`) | video procesado |
| `PARKING_DB` (`data/parking.db`) | base SQLite |
| `PARKING_DETECTOR` (`motion`) | `motion` o `yolo` |
| `PARKING_LINE` (`0.05,0.55,0.95,0.55`) | línea virtual `x1,y1,x2,y2` en fracciones del frame |
| `PARKING_ENTRY_DIRECTION` (`down`) | sentido de entrada: `down` o `up` |
| `PARKING_BARRIER_SECONDS` (`3`) | segundos que queda abierta la barrera |
| `PARKING_MIN_AREA` (`2500`) | área mínima en píxeles para considerar un vehículo |
| `PARKING_TIME_SCALE` (`60`) | segundos de estadía por segundo de video (1 = real) |
| `PARKING_RATE` (`1000`) / `PARKING_FRACTION` (`15`) | tarifa inicial (después se cambia en la web) |
| `PARKING_TOLERANCE` (`0`) / `PARKING_DAILY_CAP` (`0`) | minutos sin cargo y tope por cada 24 h (0 = desactivado) |

Ejemplo con un video propio cuya entrada está a un tercio de la altura y en tiempo real:

```bash
PARKING_VIDEO=videos/cochera.mp4 PARKING_LINE=0,0.33,1,0.33 PARKING_TIME_SCALE=1 uvicorn app.main:app
```

## Pasar a YOLO

El resto del sistema solo usa la interfaz `detect(frame) -> list[Detection]`, así que el cambio
es de configuración:

```bash
pip install ultralytics
PARKING_DETECTOR=yolo uvicorn app.main:app
```

`YoloDetector` (en `app/detection.py`) usa `yolov8n.pt`, que se descarga solo la primera vez, y
se queda con autos, motos, colectivos y camiones de COCO. Con YOLO conviene cambiar el tracker por
ByteTrack (`model.track(...)` de ultralytics): solo tiene que devolver el mismo `dict` de tracks.

## API

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/` | panel web |
| GET | `/video_feed` | video procesado en vivo (MJPEG) |
| GET | `/api/estado` | barrera, contadores, autos adentro, tarifa, progreso |
| GET | `/api/eventos?limit=50` | últimos eventos |
| POST | `/api/procesar` | `{"guardar_video": true}` arranca el procesamiento |
| POST | `/api/detener` | corta el procesamiento |
| GET | `/api/eventos.csv` | todos los eventos en CSV (separador `;`, se abre con Excel) |
| POST | `/api/tarifa` | `{"tarifa_hora": 1000, "fraccion_min": 15, "tolerancia_min": 10, "tope_diario": 8000}` |
| POST | `/api/barrera/abrir` | apertura manual |
| POST | `/api/reset` | borra los eventos y cierra la barrera |

Documentación interactiva: http://localhost:8000/docs

## Próximos pasos sugeridos

1. Probar con video real de la cochera y ajustar `PARKING_LINE` y `PARKING_MIN_AREA`.
2. Cámara en vivo: `cv2.VideoCapture("rtsp://usuario:clave@ip/stream")` en lugar del archivo.
3. YOLO + ByteTrack para descartar personas y autos quietos.
4. OCR de patentes para emparejar entrada y salida por patente.
5. Barrera real en `_actuate()` y cobro real.
