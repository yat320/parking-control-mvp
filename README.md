# Parking Control MVP

Sistema inicial para controlar el ingreso y egreso de autos en una cochera a partir de video:
detecta vehículos, registra entradas y salidas cuando cruzan una línea virtual, simula la
barrera, calcula la estadía y el monto, guarda todo en SQLite y lo muestra en una web local.

Fuera de alcance en esta etapa: lectura de patentes (OCR), pagos reales y hardware real
(la barrera está simulada, con un punto de enganche para conectarla después).

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
- el video procesado, el resumen y la base SQLite para descargar en **Artifacts → demo-procesada**.

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
- el formulario de tarifa (precio por hora y fracción en minutos),
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
| `app/pricing.py` | Tarifa: se cobra por fracción iniciada, mínimo una. |
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

Tabla `config`: `tarifa_hora` y `fraccion_min`.

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
| POST | `/api/tarifa` | `{"tarifa_hora": 1000, "fraccion_min": 15}` |
| POST | `/api/barrera/abrir` | apertura manual |
| POST | `/api/reset` | borra los eventos y cierra la barrera |

Documentación interactiva: http://localhost:8000/docs

## Próximos pasos sugeridos

1. Probar con video real de la cochera y ajustar `PARKING_LINE` y `PARKING_MIN_AREA`.
2. Cámara en vivo: `cv2.VideoCapture("rtsp://usuario:clave@ip/stream")` en lugar del archivo.
3. YOLO + ByteTrack para descartar personas y autos quietos.
4. OCR de patentes para emparejar entrada y salida por patente.
5. Barrera real en `_actuate()` y cobro real.
