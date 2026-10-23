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

Fuera de alcance en esta etapa: pagos reales y hardware real
(la barrera está simulada, con un punto de enganche para conectarla después).

## Usarlo online con un link fijo (Render)

La app se publica en [Render](https://render.com), gratis: un link que se abre desde la PC o el
celular, sin Codespace ni terminal. Se configura una sola vez:

1. Entrá a https://dashboard.render.com/register y registrate con **GitHub**.
2. **New → Blueprint** → conectá GitHub si lo pide y elegí este repo (`parking-control-mvp`).
3. Render lee `render.yaml` (plan **Free**, sin tarjeta) → **Deploy Blueprint**.
4. La primera vez tarda unos minutos en armarse. Al terminar, el link de la app aparece arriba
   en el servicio: `https://parking-control-mvp.onrender.com` (o parecido, si el nombre ya
   estaba tomado).

Desde ahí, cada cambio que se sube a `main` se publica solo. Cualquiera con el link lo abre, sin
cuenta. La versión publicada trae solo el video de prueba (el de la calle real no declara
licencia). En el plan gratis la app se duerme si nadie la usa por 15 minutos, y la primera visita
tarda un minuto en despertar. Los eventos se guardan mientras la app está despierta: al dormirse o
reiniciarse, la base arranca vacía. Todos los que abren el link ven la misma barrera y los mismos
eventos.

La imagen es el `Dockerfile` de este repo; sirve también para cualquier servicio que corra
contenedores (`docker build -t parking . && docker run -p 7860:7860 parking`; el puerto se cambia
con la variable `PORT`).

Hugging Face Spaces también la corre, pero desde octubre de 2026 los Spaces con Docker piden la
suscripción PRO. Con PRO: secreto `HF_TOKEN` (token de escritura) en **Settings → Secrets and
variables → Actions**, y **Actions → Publicar en Hugging Face → Run workflow**.

## Jugar al simulador

En la web, **🎮 Jugar al simulador** (o `/juego`): vos manejás los autos. **Entra un auto** hace
entrar uno; cuando termina de pasar aparece en *Estacionados* y tocándolo sale. Al final,
**Terminar y mandar al sistema** convierte la partida en un video de cámara cenital (el mismo dibujo
que el video de prueba), lo procesa el detector de verdad y muestra, salida por salida, cuánto
**debía cobrar** y cuánto **cobró el sistema**, con la estadía de cada uno.

- **Cámara frontal** (tildada por defecto): cada auto lleva una patente argentina al azar y abajo
  de la cancha se ve la cámara frontal en vivo. Se arman los dos videos (barrera + frontal, como en
  *Dos cámaras*) y la tabla muestra la patente de cada auto y la que leyó el sistema. En la frontal
  el que entra viene por la izquierda y crece, y el que sale va por la derecha y se achica: así el
  sistema separa a uno que entra de otro que sale al mismo tiempo. La partida se procesa en vivo
  (tarda lo mismo que duró). Con la cámara frontal hace falta más procesador: en Render gratis
  (0,1 CPU) se pierden autos y patentes; con el plan Starter (0,5 CPU) sale bien.
- 1 s de juego = 1 minuto de estadía (`PARKING_TIME_SCALE`). Hasta 120 s y 40 autos por partida.
- Entre dos autos del mismo carril tienen que pasar 2 s: con menos, el detector de movimiento los
  junta y cuenta uno solo. **Permitir autos pegados** deja colarse a 1 s, para ver esa falla.
- Si sale un auto que no era el primero en entrar, sin patentes el sistema le cobra la salida al
  más viejo: el juego lo marca aunque el monto coincida de casualidad.
- Para que las cuentas den, si quedaron autos adentro de otra prueba se borran los eventos antes de
  procesar. Se guardan las últimas 3 partidas en `videos/juego-*.mp4` (con su `-frontal.mp4`).
  Los videos se escriben con un nombre oculto (`.juego-...`) y se renombran al terminar: un video a
  medio armar no aparece en la lista (no se podría abrir).
- Teclado: `E` entra un auto, `1` a `9` saca al de ese lugar de la lista.

## En vivo y servidores chicos

En vivo (la web, una cámara) el sistema nunca va más lento que el video: si el procesador no
alcanza, saltea cuadros. Para que alcance, el detector trabaja sobre una copia de 320 px de ancho
(`PARKING_DETECT_WIDTH`) y las patentes se leen en otro hilo, con menos prioridad, solo cuando la
cámara de la barrera ve un auto: si el procesador no da, se leen menos patentes, pero la barrera
y los conteos siguen en vivo. Medido con el límite de cada plan de Render:

| Procesador | Sin patentes | Dos cámaras con patentes |
|---|---|---|
| 0,1 CPU (Render gratis) | en vivo, todos los eventos | se pierden autos y patentes |
| 0,5 CPU (Render Starter) | en vivo, todos los eventos | en vivo, todos los eventos |
| 1 CPU | en vivo, todos los eventos | en vivo, todos los eventos |

Procesando por consola (sin `--realtime`) no se saltea nada y las patentes se leen en el mismo
hilo: el resultado es siempre el mismo.

## Probar con tu video

En la web, debajo del video, **Probar con tu video**: desde el celular se elige un video de la
galería o se graba uno en el momento. Se elige cómo cruzan los autos (de arriba hacia abajo, con
la línea acostada, o de costado, con la línea parada; y si entrar es al revés) y **Subir y
procesar**: se sube con barra de progreso y arranca solo.

- Hasta 80 MB y 90 segundos (`PARKING_UPLOAD_MB`, `PARKING_UPLOAD_SECONDS`). Rinde más un video
  de 20 a 40 segundos con el celular quieto, en una entrada de cochera o una calle.
- Se guarda en `videos/subido-AAAAMMDD-HHMMSS.<ext>` con su `.json` de línea (ver *Ajustes por
  video*), y queda en el selector. Solo se guardan los últimos 5 (`PARKING_UPLOAD_KEEP`).
- Es una demo compartida: lo que se sube lo ven todos los que entran, y en Render se borra
  cuando la app se reinicia o se duerme.
- Los videos de más de 640 px de ancho se achican antes de detectar (`PARKING_MAX_WIDTH`), así un
  video de celular en 1080p no tarda una eternidad en un servidor chico.
- Si un video de iPhone no se puede leer: Ajustes → Cámara → Formatos → *Más compatible*.

## Leer patentes (cámara frontal)

Con una cámara mirando el frente de los autos en la barrera, cada evento guarda la patente
(`AB 123 CD` o `ABC 123`) y la foto recortada, y **la salida se cobra a la entrada de esa misma
patente**, no a la más vieja. En la web: tildar **Es la cámara frontal: leer las patentes** al
subir el video. Por consola: `python -m app.video_processor --video X.mp4 --patentes`. En un
`.json` de video: `"patentes": true`. Para todos los videos: `PARKING_PLATES=1`.

- Usa [fast-alpr](https://github.com/ankandrew/fast-alpr) (detector YOLO + OCR argentino en ONNX,
  sin GPU, ~80 ms por cuadro de 1280x720). Los modelos se bajan la primera vez (en Docker, al armar la imagen).
- Lee cada 3 cuadros mientras hay autos (`PARKING_PLATES_EVERY`), le da cada lectura al auto que la
  contiene y decide con sus últimas 6 lecturas (`PARKING_PLATES_WINDOW`), pesadas por confianza.
  Solo acepta formato argentino; una lectura como `AB123C` no cuenta.
- Si el auto cruza antes de que se le lea la chapa, el evento se completa si se la lee en los 3 s
  siguientes (`PARKING_PLATES_LATE`). Si no se lee, queda con su número (`V0003`) como antes.
- Una salida con patente sin entrada de esa patente se empareja con una entrada que no tenga
  patente leída; nunca con la de otra patente.
- `python -m app.demo_plates` arma un video de prueba (patentes dibujadas): entran 3 autos y salen
  en otro orden. Sirve para probar el circuito, no para medir cuánto acierta con chapas reales.
- Medido con datos reales (ver *Cuánto acierta*, abajo): el detector encontró la chapa en 222 de
  222 fotos de autos en la calle, y el OCR argentino leyó bien 96,6% de 735 patentes argentinas
  leyendo una sola vez cada una.
- Las patentes son datos personales (ley 25.326): en un uso real, cartel de zona filmada y borrar
  los registros viejos.

### Dos cámaras: barrera + frontal

Lo normal en una cochera: una cámara arriba de la barrera cuenta las entradas y salidas, y otra
de frente lee las patentes. La frontal no ve la línea: anota cada patente con la hora en que la
vio, y cada evento de la barrera se lleva la patente vista entre 5 s antes y 2 s después
(`PARKING_PAIR_BEFORE`, `PARKING_PAIR_AFTER`), la más cercana en el tiempo, una vez cada una.
Si la patente llega después de una salida que ya se había emparejado por orden de llegada, la
salida pasa a la entrada de esa patente y se recalcula el cobro.

- En la web: **Agregar la cámara frontal** al subir, con el **desfase**: segundos que la frontal
  empezó a grabar antes que la de la barrera (negativo si empezó después). Con el video de prueba,
  un desfase mal cargado por hasta 2,5 s sigue cobrando bien.
- En un `.json`: `{"frontal": "frente.mp4", "desfase": 2.5}` al lado del video de la barrera (las
  dos en la misma carpeta). La frontal no aparece sola en el selector.
- `python -m app.demo_plates --par videos/ [--desfase 2.5]` arma el par de prueba
  (`par_barrera.mp4` + `par_frontal.mp4`); la imagen de Docker ya lo trae.
- El video procesado muestra la frontal en un recuadro, para ver que van sincronizadas.

Con un carril de cada lado, la frontal ve a la vez al que entra y al que sale: si la chapa crece
el auto se acerca (entra) y si se achica se aleja (sale), y una entrada no se lleva una chapa que se
aleja (`PARKING_PAIR_TREND`; si la frontal mira hacia adentro, `PARKING_FRONTAL_ADENTRO=1`). Si
varias se estaban viendo justo al cruzar, gana la que apareció primero: los autos pasan en fila.
El OCR suele errar una sola letra (AA310VF por AA310WF): esas lecturas se juntan con su vista, y al
cobrar se acepta la única entrada que difiere en una letra (si hay dos parecidas, ninguna).

### Cuánto acierta (medido)

- **OCR**, 735 patentes argentinas reales (validación de `arg_plate_dataset` de fast-plate-ocr),
  una lectura por chapa: `argentinian-plates-cnn-synth-model` 96,6% (93% de noche, ~2 ms),
  `cct-s-v1-global-model` 96,1% (~24 ms), `cct-xs-v1-global-model` 92,8%.
- **Detector**, 222 fotos reales de autos en la calle (benchmarks de OpenALPR, Brasil y Europa):
  encontró la chapa en todas, a ~40 ms por foto.
- **Las dos cámaras de punta a punta**, con fotos reales de autos con patentes argentinas reales
  pegadas, animadas acercándose y alejándose (6 autos entran y salen en otro orden; ese armado usa
  datos AGPL y no está en el repo): salidas cobradas a la entrada correcta, de 6:

  | caso | 6/6 | caso | resultado |
  |---|---|---|---|
  | normal, poca luz, borroso | 6/6 | muy poca luz, muy borroso | 5/6 (la otra queda sin entrada, no a otro auto) |
  | autos cada 3 s o cada 2 s | 6/6 | chapa a ~10% del ancho | 6/6 con el detector de 640 (2/6 con el de 384) |
  | | | chapa a ~6% del ancho | 2/6: la cámara está muy lejos |

  Cuando no lee la patente, la salida se empareja por orden de llegada y puede cobrarle a otro
  auto: la cámara frontal tiene que ver la chapa grande (cerca, o con zoom).

Con cámaras en vivo es lo mismo con la hora del reloj (sin desfase): está en la rama
`camara-en-vivo`, que falta juntar con esta.

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
- `min_area`: área mínima en píxeles del video tal como se procesa (los de más de 640 px de ancho
  se achican primero; bajala en videos chicos).

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
| `app/database.py` | SQLite: tabla `eventos` (con la patente) y tabla `config` (tarifa). |
| `app/plates.py` | Patentes: lector (fast-alpr), formato argentino y votos por auto. |
| `app/demo_plates.py` | Videos de prueba de cámara frontal, con patentes dibujadas, y el par barrera + frontal. |
| `app/pairing.py` | Dos cámaras: patentes de la frontal con su hora y a qué evento de la barrera van. |
| `app/uploads.py` | Videos subidos desde la web: validar, guardar con su línea y recortar. |
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
| `PARKING_MAX_WIDTH` (`640`) | ancho máximo: los videos más anchos se achican antes de detectar (0 = no) |
| `PARKING_UPLOAD_MB` (`80`) / `PARKING_UPLOAD_SECONDS` (`90`) | límites de "Probar con tu video" |
| `PARKING_UPLOAD_KEEP` (`5`) / `PARKING_UPLOAD_MIN_AREA` (`1500`) | videos subidos que se guardan y su área mínima |
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
| GET | `/api/eventos?limit=50` | últimos eventos (con `patente` y `foto`) |
| GET | `/api/patente/{id}.jpg` | foto recortada de la patente de un evento |
| POST | `/api/procesar` | `{"guardar_video": true, "video": "nombre"}` arranca el procesamiento |
| POST | `/api/subir` | form `video` (archivo), `orientacion` (`horizontal`/`vertical`), `invertir`, `patentes`: sube un video propio |
| POST | `/api/detener` | corta el procesamiento |
| POST | `/api/tarifa` | `{"tarifa_hora": 1000, "fraccion_min": 15}` |
| POST | `/api/barrera/abrir` | apertura manual |
| POST | `/api/reset` | borra los eventos y cierra la barrera |

Documentación interactiva: http://localhost:8000/docs

## Próximos pasos sugeridos

1. Probar con video real de la cochera y ajustar `PARKING_LINE` y `PARKING_MIN_AREA`.
2. Cámara en vivo: `cv2.VideoCapture("rtsp://usuario:clave@ip/stream")` en lugar del archivo.
3. YOLO + ByteTrack para descartar personas y autos quietos.
4. Probar la lectura de patentes con video real de la cámara frontal y elegir el modelo de OCR
   (`PARKING_PLATES_OCR`; por defecto `argentinian-plates-cnn-synth-model`).
5. Barrera real en `_actuate()` y cobro real.
