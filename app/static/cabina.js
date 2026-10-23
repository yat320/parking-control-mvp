// Modo cabina (/cabina): los autos llegan solos y el jugador hace de cabinero. Cada auto frena
// en la barrera hasta que el jugador le abre (igual que en el video que arma el servidor con
// app/juego.py: "espera" + "abre_entrada"/"abre_salida"). Al terminar, el sistema procesa esa
// misma partida y se compara lo que cobró el humano con lo que cobró el sistema.
// El dibujo de la cancha es el mismo de juego.js y de app/demo_video.py.
const $ = (id) => document.getElementById(id);
const money = (n) => n == null ? "—" : "$" + Number(n).toLocaleString("es-AR", { maximumFractionDigits: 2 });
const minutos = (s) => {
  if (s == null) return "—";
  const m = Math.round(s / 60), h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
};
const reloj = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const limpia = (p) => String(p || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
const chapa = (p) => !p ? "" : p.length === 7 ? `${p.slice(0, 2)} ${p.slice(2, 5)} ${p.slice(5)}` : `${p.slice(0, 3)} ${p.slice(3)}`;

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ---------- reglas y geometría (las mismas que app/juego.py) ----------

const GAP = 2.0;            // separación mínima en un carril (con menos, el detector junta dos autos)
const LEAD = 1.0;           // el primer auto aparece en el segundo 1
const LLEGAN_HASTA = DURACION - 20;   // después ya no llegan autos nuevos
const H = C.alto, W = C.ancho, LINE = C.linea_y * H, V = C.velocidad;
const FRENA_IN = (LINE - C.frena_px) / V;            // de aparecer a frenar en la barrera
const FRENA_OUT = (H - LINE - C.frena_px) / V;
const PASA = (C.frena_px + C.auto_alto / 2) / V;     // de que le abren a cruzar la línea
const VIAJE = (H + C.auto_alto) / V;                 // de aparecer a salir del cuadro
const IMPACIENTE = 8;       // segundos esperando la barrera para que se note (bocina)
const COLORES = [["rojo", "#c82828"], ["azul", "#1e6ebe"], ["blanco", "#e1e1e1"], ["amarillo", "#e6c31e"],
                 ["verde", "#2e9e46"], ["gris", "#9a9a9a"], ["naranja", "#e67e22"]];

// Segundos de marcha de un auto que frena a los `frena` s y espera hasta `abre` (null: no le abrieron).
function marcha(dt, frena, inicio, abre) {
  if (dt <= frena) return dt;
  const espera = abre == null ? Infinity : Math.max(0, abre - inicio - frena);
  return dt - frena <= espera ? frena : dt - espera;
}

// ---------- estado ----------

let autos = [];       // {n, nombre, color, patente, llega, entra, abreIn, quiereSalir, sale, abreOut, ...}
let planilla = [];    // lo que anotó el jugador: {patente, hora, auto, salida, cobro}
let t0 = null, tFin = null, terminado = false;
let tarifa = { tarifa_hora: 1000, fraccion_min: 15 };
let proximaLlegada = LEAD;
const ahora = () => tFin ?? (t0 == null ? 0 : (performance.now() - t0) / 1000);

function cobro(seg) {  // mismo cálculo que app/pricing.py
  const { tarifa_hora: rate, fraccion_min: frac } = tarifa;
  if (rate <= 0) return 0;
  const fr = Math.max(1, Math.ceil(Math.max(0, seg) / 60 / frac - 1e-9));
  return Math.round(fr * frac / 60 * rate * 100) / 100;
}

function patenteNueva() {  // casi todas Mercosur (AB123CD), algunas viejas (ABC123); distintas entre sí
  const L = () => "ABCDEFGHJKLMNPRSTUVWXYZ"[Math.floor(Math.random() * 23)];
  const D = () => Math.floor(Math.random() * 10);
  for (;;) {
    const p = Math.random() < 0.8 ? `A${L()}${D()}${D()}${D()}${L()}${L()}` : `${L()}${L()}${L()}${D()}${D()}${D()}`;
    if (!autos.some((a) => a.patente === p)) return p;
  }
}

const enEntrada = () => autos.find((a) => a.entra != null && a.abreIn == null);   // en la barrera o llegando
const enSalida = () => autos.find((a) => a.sale != null && a.abreOut == null);
const esperandoIn = (a, t) => a && t >= a.entra + FRENA_IN;
const esperandoOut = (a, t) => a && t >= a.sale + FRENA_OUT;

// Cada tick: llegan autos nuevos (a la cola), entran al carril si está libre, y los que
// cumplieron su estadía piden salir.
function simular(t) {
  if (terminado) return;
  while (proximaLlegada <= t && proximaLlegada <= LLEGAN_HASTA && autos.length < C.max_autos) {
    const n = autos.length + 1;
    const [nombreColor, color] = COLORES[(n - 1) % COLORES.length];
    autos.push({ n, nombre: `Auto ${n} ${nombreColor}`, color, patente: patenteNueva(), llega: proximaLlegada,
                 entra: null, abreIn: null, quiereSalir: null, sale: null, abreOut: null });
    proximaLlegada += 4 + Math.random() * 4;   // uno cada 4 a 8 s
  }
  const ultimoIn = autos.filter((a) => a.entra != null).at(-1);
  const libreIn = !enEntrada() && (!ultimoIn || t >= Math.max(ultimoIn.entra, ultimoIn.abreIn) + GAP);
  const siguiente = autos.find((a) => a.entra == null);
  if (libreIn && siguiente) {
    siguiente.entra = +t.toFixed(2);
    siguiente.quiereSalir = null;
  }
  // Los estacionados (ya cruzaron) piden salir tras 15 a 35 s.
  for (const a of autos) {
    if (a.abreIn != null && a.quiereSalir == null) a.quiereSalir = a.abreIn + PASA + 15 + Math.random() * 20;
  }
  const ultimoOut = autos.filter((a) => a.sale != null).sort((x, y) => x.sale - y.sale).at(-1);
  const libreOut = !enSalida() && (!ultimoOut || t >= Math.max(ultimoOut.sale, ultimoOut.abreOut) + GAP);
  const quiere = autos.filter((a) => a.sale == null && a.quiereSalir != null && a.quiereSalir <= t)
    .sort((x, y) => x.quiereSalir - y.quiereSalir)[0];
  if (libreOut && quiere && t < DURACION - 6) quiere.sale = +t.toFixed(2);
  if (t >= DURACION) terminar();
}

// ---------- acciones del cabinero ----------

function aviso(t) { $("msg").innerHTML = t; }

function anotar(sinPatente) {
  const t = ahora(), a = enEntrada();
  if (terminado || !esperandoIn(a, t)) return;
  const escrita = limpia($("in-patente").value);
  if (!sinPatente && escrita.length < 6) return aviso("Escribí la patente (6 o 7 caracteres) o abrí sin anotar.");
  a.abreIn = +t.toFixed(2);
  if (sinPatente) {
    a.sinAnotar = true;
    aviso(`Abriste sin anotar: ${esc(a.nombre)} entró y no figura en tu planilla.`);
  } else {
    planilla.push({ patente: escrita, hora: a.abreIn, auto: a.n, salida: null, cobro: null });
    aviso(`Anotaste <b class="chapa">${chapa(escrita)}</b> a las ${reloj(a.abreIn)}.`);
  }
  $("in-patente").value = "";
  pintar(true);
}

function cobrar(i) {
  const t = ahora(), a = enSalida();
  if (terminado || !esperandoOut(a, t)) return;
  a.abreOut = +t.toFixed(2);
  if (i == null) {
    a.cobroHumano = 0;
    aviso(`Abriste sin cobrar: ${esc(a.nombre)} se fue gratis.`);
  } else {
    const e = planilla[i];
    e.salida = a.abreOut;
    e.cobro = cobro((a.abreOut - e.hora) * ESCALA);
    a.cobroHumano = e.cobro;
    a.cobradoComo = e.patente;
    aviso(`Cobraste ${money(e.cobro)} a <b class="chapa">${chapa(e.patente)}</b>.`);
  }
  pintar(true);
}

$("btn-anotar").addEventListener("click", () => anotar(false));
$("btn-sin-anotar").addEventListener("click", () => anotar(true));
$("in-patente").addEventListener("keydown", (e) => { if (e.key === "Enter") anotar(false); });
$("btn-sin-cobrar").addEventListener("click", () => cobrar(null));
$("out-planilla").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (b) cobrar(Number(b.dataset.i));
});

// ---------- pantalla de la cabina ----------

// Lo que ve el cabinero del auto que espera: su color y la chapa (la "ve por la ventanilla").
function fichaAuto(a, t, frena, inicio) {
  if (!a) return `<span class="vacio">Nadie esperando.</span>`;
  if (t < inicio + frena) return `<span class="vacio">Llega un auto…</span>`;
  const esp = t - inicio - frena;
  return `<i style="background:${a.color}"></i><b class="chapa grande-chapa">${chapa(a.patente)}</b>
    <small class="${esp >= IMPACIENTE ? "impaciente" : ""}">espera ${Math.floor(esp)} s${esp >= IMPACIENTE ? " · ¡bocinazo!" : ""}</small>`;
}

let claveBotones = "";
function pintar(forzar) {
  const t = ahora();
  const a = enEntrada(), b = enSalida();
  const okIn = !terminado && esperandoIn(a, t), okOut = !terminado && esperandoOut(b, t);
  $("in-auto").innerHTML = fichaAuto(a, t, FRENA_IN, a?.entra);
  $("out-auto").innerHTML = fichaAuto(b, t, FRENA_OUT, b?.sale);
  $("in-patente").disabled = !okIn; $("btn-anotar").disabled = !okIn; $("btn-sin-anotar").disabled = !okIn;
  $("btn-sin-cobrar").disabled = !okOut;
  if (okIn && document.activeElement !== $("in-patente") && forzar !== true) {
    // al llegar un auto, el cursor va a la patente (en la PC; en el celular no abre el teclado solo)
    if (!matchMedia("(pointer: coarse)").matches) $("in-patente").focus();
  }
  // Los botones de la planilla se rehacen solo si cambia algo (si no, en el celular se pierde el toque).
  const abiertas = planilla.map((e, i) => [e, i]).filter(([e]) => e.salida == null);
  const clave = abiertas.map(([e, i]) => i).join(",") + (okOut ? "+" : "-");  // el monto se fija al llegar el auto
  if (clave !== claveBotones || forzar) {
    claveBotones = clave;
    $("out-planilla").innerHTML = abiertas.length ? abiertas.map(([e, i]) =>
      `<button data-i="${i}" ${okOut ? "" : "disabled"}><b class="chapa">${chapa(e.patente)}</b>
       <small>${money(cobro((t - e.hora) * ESCALA))}</small></button>`).join("")
      : `<span class="vacio">No hay nadie anotado adentro.</span>`;
  }
  const filas = planilla.map((e) => `<tr><td class="chapa">${chapa(e.patente)}</td><td>${reloj(e.hora)}</td>
    <td>${e.salida == null ? "—" : reloj(e.salida)}</td><td>${e.cobro == null ? "—" : money(e.cobro)}</td></tr>`).join("");
  const html = filas || `<tr><td colspan="4" class="vacio">Todavía no anotaste nada.</td></tr>`;
  if ($("planilla").innerHTML !== html) $("planilla").innerHTML = html;
  const cola = autos.filter((x) => x.llega <= t && x.entra == null).length;
  $("cola").textContent = cola ? `${cola} en la cola de entrada` : "";
}

// ---------- dibujo ----------

const fondo = document.createElement("canvas");
fondo.width = W; fondo.height = H;
(function dibujarFondo() {
  const g = fondo.getContext("2d");
  g.fillStyle = "rgb(60,110,70)"; g.fillRect(0, 0, W, H);
  g.fillStyle = "rgb(85,85,85)"; g.fillRect(170, 0, 300, H);
  g.fillStyle = "rgb(210,210,210)"; g.fillRect(319, 0, 2, H);
  g.fillStyle = "rgb(85,85,85)";
  for (let y = 0; y < H; y += 40) g.fillRect(319, y, 2, 20);
  g.fillStyle = "rgb(160,150,150)"; g.fillRect(490, 150, 110, 90);
  g.fillStyle = "rgb(120,160,190)"; g.fillRect(500, 160, 90, 30);
  g.font = "13px sans-serif";
  g.fillStyle = "rgb(40,40,40)"; g.fillText("CABINA", 505, 225);
  g.fillStyle = "rgb(230,230,230)"; g.fillText("ENTRADA", 205, 30); g.fillText("SALIDA", 355, H - 15);
})();

function dibujarAuto(g, cx, cy, color, haciaAbajo, n) {
  const x1 = cx - C.auto_ancho / 2, y1 = cy - C.auto_alto / 2, x2 = x1 + C.auto_ancho, y2 = y1 + C.auto_alto;
  g.fillStyle = "rgb(20,20,20)";
  for (const wy of [y1 + 22, y2 - 38]) { g.fillRect(x1 - 5, wy, 11, 22); g.fillRect(x2 - 6, wy, 11, 22); }
  g.fillStyle = color;
  g.fillRect(x1, y1 + 8, x2 - x1, y2 - y1 - 16);
  g.fillRect(x1 + 8, y1, x2 - x1 - 16, y2 - y1);
  const front = haciaAbajo ? y2 - 45 : y1 + 25, back = haciaAbajo ? y1 + 20 : y2 - 40;
  g.fillStyle = "rgb(40,50,60)";
  g.fillRect(x1 + 10, front, x2 - x1 - 20, 20);
  g.fillRect(x1 + 12, back, x2 - x1 - 24, 18);
  g.fillStyle = "rgba(0,0,0,.45)";
  const top = Math.min(front, back) + 22, bot = Math.max(front, back) - 4;
  g.fillRect(x1 + 12, top, x2 - x1 - 24, bot - top);
  g.fillStyle = "#fff"; g.font = "bold 18px sans-serif"; g.textAlign = "center";
  g.fillText(String(n), cx, (top + bot) / 2 + 6); g.textAlign = "start";
}

// Brazo de una barrera: cerrada cruza su carril sobre la línea; abierta, levantada.
function dibujarBarrera(g, x0, x1, abierta) {
  g.strokeStyle = "#fff"; g.lineWidth = 7; g.beginPath();
  g.moveTo(x1, LINE); g.lineTo(abierta ? x1 - 12 : x0, abierta ? LINE - 70 : LINE); g.stroke();
  g.strokeStyle = "#c62828"; g.lineWidth = 3; g.stroke();
  g.fillStyle = "#555"; g.fillRect(x1 - 6, LINE - 6, 12, 12);
}

const g = $("cancha").getContext("2d");
function cuadro() {
  const t = ahora();
  simular(t);
  g.drawImage(fondo, 0, 0);
  g.setLineDash([10, 8]); g.strokeStyle = "rgba(255,210,60,.9)"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(W * 0.05, LINE); g.lineTo(W * 0.95, LINE); g.stroke(); g.setLineDash([]);
  for (const a of autos) {
    if (a.entra != null) {
      const k = marcha(t - a.entra, FRENA_IN, a.entra, a.abreIn);
      if (t >= a.entra && k <= VIAJE) dibujarAuto(g, C.carril_entrada, -C.auto_alto / 2 + k * V, a.color, true, a.n);
    }
    if (a.sale != null) {
      const k = marcha(t - a.sale, FRENA_OUT, a.sale, a.abreOut);
      if (t >= a.sale && k <= VIAJE) dibujarAuto(g, C.carril_salida, H + C.auto_alto / 2 - k * V, a.color, false, a.n);
    }
  }
  // Las barreras se ven abiertas un ratito después de cada apertura.
  const abiertaIn = autos.some((a) => a.abreIn != null && t - a.abreIn < 2.5);
  const abiertaOut = autos.some((a) => a.abreOut != null && t - a.abreOut < 2.5);
  dibujarBarrera(g, 175, 318, abiertaIn);
  dibujarBarrera(g, 322, 466, abiertaOut);
  $("reloj").textContent = reloj(t);
  if (!terminado && t0 != null) $("progreso").style.width = Math.min(100, t / DURACION * 100) + "%";
  requestAnimationFrame(cuadro);
}
setInterval(() => {
  if (t0 == null || terminado) return;
  pintar();
  Vivo.mandar(partida());  // solo manda si cambió algo (llegó, le abrieron, pidió salir)
}, 250);
setInterval(() => { if (Vivo.activo && !terminado) Vivo.mandar(partida(), null, true); }, 10000);
setInterval(async () => {
  if (!Vivo.activo) return;
  try {
    const c = await Vivo.contar();
    $("sistema-vivo").textContent = `El sistema, en vivo: ${c.entradas} entradas y ${c.salidas} salidas vistas (también en el panel).`;
  } catch (e) {}
}, 1500);

// ---------- terminar: el sistema procesa la misma partida y se compara ----------

let baseId = 0;
const conPatentes = () => $("frontal-on").checked;

// El turno tal como lo manda al servidor (a /api/vivo/autos mientras se juega, o a /api/juego).
const partida = () => autos.filter((a) => a.entra != null).map((a) => ({ color: a.color, entra: a.entra, espera: true,
  patente: a.patente, abre_entrada: a.abreIn, sale: a.sale, abre_salida: a.abreOut }));

async function terminar() {
  if (terminado || t0 == null) return;
  tFin = ahora();
  terminado = true;
  pintar(true);
  $("btn-terminar").disabled = true;
  $("frontal-on").disabled = true;
  $("error").textContent = "";
  const jugados = autos.filter((a) => a.entra != null);
  if (!jugados.length) return aviso("No llegó a entrar ningún auto. Tocá Jugar otro turno.");
  if (Vivo.activo) {  // el sistema ya estuvo mirando el turno: solo faltan los últimos cruces
    baseId = Vivo.baseId;
    await Vivo.mandar(partida(), +tFin.toFixed(2));
    aviso("El sistema termina de mirar los últimos autos…");
    $("cancha").hidden = true;
    const feed = $("feed"); feed.hidden = false; feed.src = "/video_feed?" + Date.now();
    return esperar(Vivo.NOMBRE, true);
  }
  try {
    const est = await api("/api/estado");
    if (est.procesando) throw new Error("el sistema está procesando otro video. Esperá a que termine y probá de nuevo.");
    if (est.adentro.length) await api("/api/reset", {});  // si quedaron autos de otra prueba, las cuentas no dan
    const ultimos = await api("/api/eventos?limit=1");
    baseId = ultimos.length ? ultimos[0].id : 0;
    aviso(conPatentes() ? "Armando los videos de las dos cámaras…" : "Armando el video del turno…");
    const { nombre } = await api("/api/juego", { patentes: conPatentes(), duracion: +tFin.toFixed(2), autos: partida() });
    await api("/api/procesar", { video: nombre, guardar_video: false });
    aviso("El sistema está mirando el turno, en vivo…");
    $("cancha").hidden = true;
    const feed = $("feed"); feed.hidden = false; feed.src = "/video_feed?" + Date.now();
    esperar(nombre, false);
  } catch (e) {
    $("error").textContent = "No se pudo procesar el turno: " + e.message;
  }
}

async function esperar(nombre, visto) {
  try {
    const e = await api("/api/estado");
    if (e.error) throw new Error(e.error);
    $("progreso").style.width = (e.progreso * 100) + "%";
    $("estado-proc").textContent = !e.procesando ? ""
      : nombre === Vivo.NOMBRE ? `El sistema termina de mirar el turno… ${e.ultimo_mensaje || ""}`
      : `El sistema va por el ${Math.round(e.progreso * 100)}% del turno… ${e.ultimo_mensaje || ""}`;
    visto = visto || e.procesando;
    if (e.video === nombre && !e.procesando && (visto || e.progreso >= 0.99)) return comparar();
  } catch (err) {
    $("error").textContent = err.message;
    return;
  }
  setTimeout(() => esperar(nombre, visto), 700);
}

async function comparar() {
  $("estado-proc").textContent = "";
  aviso("");
  const evs = (await api("/api/eventos?limit=500")).filter((e) => e.id > baseId).sort((a, b) => a.id - b.id);
  const sisEntradas = evs.filter((e) => e.tipo_evento === "entrada");
  const sisSalidas = evs.filter((e) => e.tipo_evento === "salida");

  // Lo que pasó de verdad: cada auto cruza la línea PASA s después de que le abren.
  const entraron = autos.filter((a) => a.abreIn != null);
  const salieron = autos.filter((a) => a.abreOut != null).sort((x, y) => x.abreOut - y.abreOut);
  const estadia = (a) => (a.abreOut - a.abreIn) * ESCALA;
  const debe = (a) => cobro(estadia(a));

  // Humano: patentes bien anotadas, autos sin anotar, cobros.
  const bienAnotadas = planilla.filter((e) => autos[e.auto - 1].patente === e.patente).length;
  const sinAnotar = entraron.filter((a) => a.sinAnotar).length;

  const filas = [];
  let okH = 0, okS = 0;
  const total = Math.max(salieron.length, sisSalidas.length);
  for (let i = 0; i < total; i++) {
    const a = salieron[i], s = sisSalidas[i];
    const d = a ? debe(a) : null;
    const h = a ? a.cobroHumano : null;
    const bienH = a && Math.abs(h - d) < 0.01;
    const bienS = a && s && Math.abs((s.monto ?? 0) - d) < 0.01 && (!conPatentes() || s.patente === a.patente);
    if (bienH) okH++;
    if (bienS) okS++;
    const notaH = !a ? "" : bienH ? "" : a.cobradoComo == null ? "abrió sin cobrar"
      : a.cobradoComo !== a.patente ? `le cobró como ${chapa(a.cobradoComo)}` : "monto distinto";
    const notaS = !a ? "vio una salida que no hubo" : !s ? "no vio esta salida" : bienS ? ""
      : conPatentes() && s.patente !== a.patente ? (s.patente ? `leyó ${chapa(s.patente)}` : "no leyó la patente")
      : "monto distinto";
    filas.push(`<tr><td>${i + 1}</td>
      <td>${a ? `<i class="pin" style="background:${a.color}"></i>${esc(a.nombre)}` : "—"}</td>
      <td class="chapa">${a ? chapa(a.patente) : "—"}</td>
      <td>${money(d)} <small>${a ? minutos(estadia(a)) : ""}</small></td>
      <td class="${bienH ? "si" : "no"}">${a ? money(h) : "—"} ${bienH ? "✓" : "✗"} <small>${esc(notaH)}</small></td>
      <td class="${bienS ? "si" : "no"}">${s ? money(s.monto) : "—"} ${bienS ? "✓" : "✗"} <small>${esc(notaS)}</small></td></tr>`);
  }
  $("comparacion").innerHTML = filas.join("") || `<tr><td colspan="6" class="vacio">No salió ningún auto en el turno.</td></tr>`;

  const debido = salieron.reduce((s, a) => s + debe(a), 0);
  const cobroH = salieron.reduce((s, a) => s + (a.cobroHumano || 0), 0);
  const cobroS = sisSalidas.reduce((s, e) => s + (e.monto || 0), 0);
  const esperas = [...entraron.map((a) => a.abreIn - a.entra - FRENA_IN), ...salieron.map((a) => a.abreOut - a.sale - FRENA_OUT)]
    .map((x) => Math.max(0, x));
  const prom = esperas.length ? esperas.reduce((s, x) => s + x, 0) / esperas.length : 0;
  const nunca = autos.filter((a) => a.entra != null && a.abreIn == null).length + autos.filter((a) => a.entra == null && a.llega <= tFin).length;
  $("conteo").innerHTML = `
    <div><b>${money(debido)}</b><span>había que cobrar</span></div>
    <div><b>${money(cobroH)}</b><span>cobraste vos (${okH}/${salieron.length} bien)</span></div>
    <div><b>${money(cobroS)}</b><span>cobró el sistema (${okS}/${salieron.length} bien)</span></div>
    <div><b>${prom.toFixed(1)} s</b><span>esperó cada auto en tu barrera</span></div>`;

  const notas = [];
  notas.push(`Entraron ${entraron.length} autos: anotaste bien ${bienAnotadas}` +
    (planilla.length > bienAnotadas ? `, mal ${planilla.length - bienAnotadas}` : "") +
    (sinAnotar ? ` y dejaste pasar ${sinAnotar} sin anotar` : "") +
    `; el sistema contó ${sisEntradas.length}.`);
  if (esperas.length) notas.push(`La espera más larga en tu barrera fue de ${Math.max(...esperas).toFixed(1)} s` +
    (nunca ? `, y ${nunca} ${nunca === 1 ? "auto se quedó" : "autos se quedaron"} sin entrar.` : "."));
  if (cobroH < debido - 0.01) notas.push(`Vos dejaste de cobrar ${money(debido - cobroH)}.`);
  if (Math.abs(cobroS - debido) > 0.01) notas.push(`El sistema cobró ${money(cobroS)} contra ${money(debido)}` +
    (conPatentes() ? "." : ": sin la cámara frontal le cobra la salida al que entró primero."));
  notas.push("El sistema abre la barrera solo apenas detecta el auto: no hace esperar a nadie.");
  $("conclusion").innerHTML = notas.map(esc).join("<br>");
  $("resultado").hidden = false;
  $("resultado").scrollIntoView({ behavior: "smooth" });
}

// ---------- arranque ----------

$("btn-empezar").addEventListener("click", () => {
  if (t0 != null) return;
  t0 = performance.now();
  Vivo.empezar(conPatentes(), 0).then((ok) => {
    if (ok) $("frontal-on").disabled = true;
    else $("sistema-vivo").textContent = "El sistema está ocupado con otro video: va a mirar el turno cuando termines.";
  });
  $("btn-empezar").disabled = true;
  $("btn-terminar").disabled = false;
  aviso("Turno abierto. Ya llega el primer auto.");
});
$("btn-terminar").addEventListener("click", terminar);
$("btn-otra").addEventListener("click", () => location.reload());
api("/api/estado").then((e) => {
  tarifa = e.tarifa;
  $("tarifa").textContent = `${money(tarifa.tarifa_hora)} por hora, por fracción de ${tarifa.fraccion_min} min`;
}).catch(() => {});
requestAnimationFrame(cuadro);
