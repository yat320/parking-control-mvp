// Simulador jugable (/juego). Los autos se mueven igual que en el video que arma el servidor
// (app/juego.py + app/demo_video.py): misma velocidad, mismos carriles, misma línea virtual.
// Tiempos en "segundos de video": el primer auto aparece en el segundo 1.
const $ = (id) => document.getElementById(id);
const money = (n) => n == null ? "—" : "$" + Number(n).toLocaleString("es-AR", { maximumFractionDigits: 2 });
const minutos = (s) => {
  if (s == null) return "—";
  const m = Math.round(s / 60), h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
};
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

const GAP = 2.0;          // separación mínima en un carril: con menos, el detector junta dos autos en uno
const GAP_COLADO = 1.0;   // con "permitir autos pegados"
const LEAD = 1.0;         // el primer auto aparece en el segundo 1 (el detector aprende el fondo vacío)
const H = C.alto, W = C.ancho, LINE = C.linea_y * H;
const CRUZA_ENTRADA = (LINE + C.auto_alto / 2) / C.velocidad;    // de aparecer a cruzar la línea
const CRUZA_SALIDA = (H + C.auto_alto / 2 - LINE) / C.velocidad;
const ESTACIONA = (H + C.auto_alto) / C.velocidad;                 // de aparecer a salir del cuadro
const COLORES = [["rojo", "#c82828"], ["azul", "#1e6ebe"], ["blanco", "#e1e1e1"], ["amarillo", "#e6c31e"],
                 ["verde", "#2e9e46"], ["gris", "#9a9a9a"], ["naranja", "#e67e22"]];

let autos = [];          // {n, nombre, color, patente, entra, sale}
let t0 = null;           // performance.now() del segundo 0 del video
let terminado = false;
let tarifa = { tarifa_hora: 1000, fraccion_min: 15 };

let tFin = null;          // al terminar, el reloj se congela
const ahora = () => tFin ?? (t0 == null ? 0 : (performance.now() - t0) / 1000);

// Mismo cálculo que app/pricing.py.
function cobro(seg) {
  const { tarifa_hora: rate, fraccion_min: frac } = tarifa;
  if (rate <= 0) return 0;
  const fr = Math.max(1, Math.ceil(Math.max(0, seg) / 60 / frac - 1e-9));
  return Math.round(fr * frac / 60 * rate * 100) / 100;
}
const estadia = (a, hasta) => (hasta - (a.entra + CRUZA_ENTRADA)) * ESCALA;  // segundos "reales"

// ---------- reglas ----------

function ultimo(campo) {
  return autos.reduce((m, a) => a[campo] != null && a[campo] > m ? a[campo] : m, -Infinity);
}
const tiempoAgotado = () => ahora() + C.cola_seg > C.max_seg - 0.5;

function puedeEntrar() {
  if (terminado) return "La partida terminó.";
  if (autos.length >= C.max_autos) return `Máximo ${C.max_autos} autos por partida.`;
  if (tiempoAgotado()) return "Se acabó el tiempo: mandá la partida al sistema.";
  const gap = $("colar").checked ? GAP_COLADO : GAP;
  const falta = ultimo("entra") + gap - ahora();
  return falta > 0 ? `Esperá ${falta.toFixed(1)} s: el carril de entrada está ocupado.` : "";
}

function puedeSalir(a) {
  if (terminado) return "La partida terminó.";
  if (tiempoAgotado()) return "Se acabó el tiempo: mandá la partida al sistema.";
  if (ahora() < a.entra + ESTACIONA) return `${a.nombre} todavía está entrando.`;
  const falta = ultimo("sale") + GAP - ahora();
  return falta > 0 ? `Esperá ${falta.toFixed(1)} s: el carril de salida está ocupado.` : "";
}

function entrar() {
  const no = puedeEntrar();
  if (no) return aviso(no);
  if (t0 == null) t0 = performance.now() - LEAD * 1000;
  const n = autos.length + 1;
  const [nombreColor, color] = COLORES[(n - 1) % COLORES.length];
  autos.push({ n, nombre: `Auto ${n} ${nombreColor}`, color, patente: patenteNueva(), entra: +ahora().toFixed(2), sale: null });
  $("frontal-on").disabled = true;  // con autos en juego ya no se cambia
  aviso(`Entró el auto ${n}. Cuando termine de pasar, tocá "Sacar" en la lista para que salga.`);
  $("btn-terminar").disabled = false;
  listas();
}

function salir(n) {
  const a = autos.find((x) => x.n === n);
  if (!a || a.sale != null) return;
  const no = puedeSalir(a);
  if (no) return aviso(no);
  a.sale = +ahora().toFixed(2);
  aviso(`Sale ${a.nombre}: ${minutos(estadia(a, a.sale + CRUZA_SALIDA))}, ${money(cobro(estadia(a, a.sale + CRUZA_SALIDA)))}.`);
  listas();
}

function aviso(t) { $("msg").textContent = t; }

// Patente argentina al azar, distinta de las de la partida: casi todas Mercosur (AB123CD), algunas viejas (ABC123).
function patenteNueva() {
  const L = () => "ABCDEFGHJKLMNPRSTUVWXYZ"[Math.floor(Math.random() * 23)];
  const D = () => Math.floor(Math.random() * 10);
  for (;;) {
    const p = Math.random() < 0.8 ? `A${L()}${D()}${D()}${D()}${L()}${L()}` : `${L()}${L()}${L()}${D()}${D()}${D()}`;
    if (!autos.some((a) => a.patente === p)) return p;
  }
}
const chapa = (p) => !p ? "" : p.length === 7 ? `${p.slice(0, 2)} ${p.slice(2, 5)} ${p.slice(5)}` : `${p.slice(0, 3)} ${p.slice(3)}`;
const conPatentes = () => $("frontal-on").checked;

// ---------- listas ----------

// Los botones de "Estacionados" se arman solo cuando cambia quién está y en qué estado: si se
// rehicieran cada medio segundo, en el celular un toque que cae justo en el cambio se pierde.
let claveLista = "";
function listas() {
  const t = ahora();
  const est = autos.filter((a) => a.sale == null);
  const clave = est.map((a) => `${a.n}:${t < a.entra + ESTACIONA ? "e" : "p"}`).join(",") + (terminado ? "!" : "");
  if (clave !== claveLista) {
    claveLista = clave;
    $("estacionados").innerHTML = est.length ? est.map((a) => {
      const entrando = t < a.entra + ESTACIONA;
      return `<li><button class="auto" data-n="${a.n}" ${entrando || terminado ? "disabled" : ""}>
        <i style="background:${a.color}"></i>
        <span>${esc(a.nombre)}${conPatentes() ? `<br><b class="chapa">${chapa(a.patente)}</b>` : ""}</span>
        <small data-info="${a.n}"></small>
        <em>${entrando ? "entrando…" : terminado ? "" : "Sacar ▸"}</em></button></li>`;
    }).join("") : `<li class="vacio">${autos.length ? "No queda nadie adentro." : "Todavía no entró nadie."}</li>`;
  }
  for (const a of est) {
    const el = document.querySelector(`[data-info="${a.n}"]`);
    if (el) el.textContent = t < a.entra + ESTACIONA ? "" : `${minutos(Math.max(0, estadia(a, t)))} · ${money(cobro(Math.max(0, estadia(a, t))))}`;
  }
  const fue = autos.filter((a) => a.sale != null).sort((x, y) => x.sale - y.sale);
  const html = !fue.length ? `<li class="vacio">Nadie todavía.</li>` : fue.map((a) => {
    const seg = estadia(a, a.sale + CRUZA_SALIDA);
    return `<li class="auto-fue"><i style="background:${a.color}"></i>
      <span>${esc(a.nombre)}${conPatentes() ? ` <b class="chapa">${chapa(a.patente)}</b>` : ""}</span>
      <small>${minutos(seg)} · ${money(cobro(seg))}</small></li>`;
  }).join("");
  if ($("salieron").innerHTML !== html) $("salieron").innerHTML = html;
}

$("estacionados").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-n]");
  if (b) salir(Number(b.dataset.n));
});

// ---------- dibujo (el mismo de app/demo_video.py, en RGB) ----------

const fondo = document.createElement("canvas");
fondo.width = W; fondo.height = H;
(function dibujarFondo() {
  const g = fondo.getContext("2d");
  g.fillStyle = "rgb(60,110,70)"; g.fillRect(0, 0, W, H);                 // pasto
  g.fillStyle = "rgb(85,85,85)"; g.fillRect(170, 0, 300, H);               // asfalto
  g.fillStyle = "rgb(210,210,210)"; g.fillRect(319, 0, 2, H);              // división de carriles
  g.fillStyle = "rgb(85,85,85)";
  for (let y = 0; y < H; y += 40) g.fillRect(319, y, 2, 20);
  g.fillStyle = "rgb(160,150,150)"; g.fillRect(490, 150, 110, 90);         // cabina
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
  g.fillStyle = "rgba(0,0,0,.45)";  // techo más oscuro
  const top = Math.min(front, back) + 22, bot = Math.max(front, back) - 4;
  g.fillRect(x1 + 12, top, x2 - x1 - 24, bot - top);
  g.fillStyle = "#fff"; g.font = "bold 18px sans-serif"; g.textAlign = "center";
  g.fillText(String(n), cx, (top + bot) / 2 + 6); g.textAlign = "start";
}

// Cámara frontal (la misma de app/demo_plates.py): el que entra viene por la izquierda y crece;
// el que sale va por la derecha y se achica. Se dibuja en vivo para ver qué lee el sistema.
const F = C.frontal;
const gf = $("frontal").getContext("2d");
function dibujarFrontal(t) {
  gf.fillStyle = "rgb(105,100,95)"; gf.fillRect(0, 0, F.ancho, F.alto);
  gf.fillStyle = "rgb(120,140,150)"; gf.fillRect(0, 0, F.ancho, 110);   // pared al fondo
  const viaje = (F.alto + F.auto_alto) / F.velocidad;
  const vistos = [];
  for (const a of autos) {
    for (const [ini, abajo] of [[a.entra, true], [a.sale, false]]) {
      if (ini == null) continue;
      const k = t - ini;
      if (k < 0 || k > viaje) continue;
      const cy = abajo ? -F.auto_alto / 2 + k * F.velocidad : F.alto + F.auto_alto / 2 - k * F.velocidad;
      vistos.push([a, abajo, cy]);
    }
  }
  for (const [a, abajo, cy] of vistos) {
    const s = 0.7 + 0.5 * Math.min(1, Math.max(0, cy / F.alto));
    const cw = F.auto_ancho * s, ch = F.auto_alto * s, cx = F.ancho / 2 + (abajo ? -F.carril : F.carril);
    const x = cx - cw / 2, y = cy - ch / 2, u = (v) => v * s;
    gf.fillStyle = a.color; gf.fillRect(x, y, cw, ch);
    gf.fillStyle = "rgb(65,60,55)"; gf.fillRect(x + u(35), y + u(18), cw - u(70), u(77));        // parabrisas
    gf.fillStyle = "rgb(240,230,210)"; gf.fillRect(x + u(18), y + u(120), u(57), u(30));         // faros
    gf.fillRect(x + cw - u(75), y + u(120), u(57), u(30));
    gf.fillStyle = "rgb(30,30,30)"; gf.fillRect(x + u(60), y + u(205), cw - u(120), u(20));      // paragolpes
    const pw = u(100), ph = u(32), px = cx - pw / 2, py = y + u(165);                           // chapa
    const mercosur = a.patente.length === 7;
    gf.fillStyle = mercosur ? "#fff" : "#191919"; gf.fillRect(px, py, pw, ph);
    if (mercosur) { gf.fillStyle = "rgb(0,60,150)"; gf.fillRect(px, py, pw, ph * 0.22); }
    gf.fillStyle = mercosur ? "#0a0a0a" : "#f0f0f0";
    gf.font = `bold ${Math.round(u(17))}px sans-serif`; gf.textAlign = "center";
    gf.fillText(chapa(a.patente), cx, py + ph * 0.85); gf.textAlign = "start";
  }
  gf.fillStyle = "rgba(255,255,255,.85)"; gf.font = "bold 26px sans-serif"; gf.fillText("CÁMARA FRONTAL", 20, 40);
}

const g = $("cancha").getContext("2d");
function cuadro() {
  const t = ahora();
  g.drawImage(fondo, 0, 0);
  g.setLineDash([10, 8]); g.strokeStyle = "rgba(255,210,60,.9)"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(W * 0.05, LINE); g.lineTo(W * 0.95, LINE); g.stroke(); g.setLineDash([]);
  g.fillStyle = "rgba(255,210,60,.9)"; g.font = "12px sans-serif"; g.fillText("línea virtual", W * 0.05, LINE - 6);
  for (const a of autos) {
    const yIn = -C.auto_alto / 2 + (t - a.entra) * C.velocidad;
    if (yIn > -C.auto_alto && yIn < H + C.auto_alto) dibujarAuto(g, C.carril_entrada, yIn, a.color, true, a.n);
    if (a.sale != null) {
      const yOut = H + C.auto_alto / 2 - (t - a.sale) * C.velocidad;
      if (t >= a.sale && yOut > -C.auto_alto) dibujarAuto(g, C.carril_salida, yOut, a.color, false, a.n);
    }
  }
  if (conPatentes() && !$("frontal").hidden) dibujarFrontal(t);
  const s = Math.floor(t);
  $("reloj").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  $("hora-sim").textContent = t0 == null ? "" : `≈ ${minutos(t * ESCALA)} de cochera`;
  $("btn-entra").disabled = terminado || !!puedeEntrar() && tiempoAgotado();
  if (!terminado) $("progreso").style.width = Math.min(100, (t + C.cola_seg) / C.max_seg * 100) + "%";
  requestAnimationFrame(cuadro);
}

// refresca las listas (minutos y montos) cada medio segundo
setInterval(() => { if (!terminado) listas(); }, 500);

// ---------- mandar la partida al sistema y comparar ----------

let baseId = 0;

async function terminar() {
  if (!autos.length || terminado) return;
  tFin = ahora();
  terminado = true;
  $("btn-terminar").disabled = true; $("btn-entra").disabled = true; $("colar").disabled = true;
  listas();
  $("error").textContent = "";
  try {
    // Si quedaron autos "adentro" de otra prueba, el sistema les cobraría a ellos las salidas
    // de esta partida (empareja con la entrada más vieja): se arranca de cero.
    const est = await api("/api/estado");
    if (est.procesando) throw new Error("el sistema está procesando otro video. Esperá a que termine y probá de nuevo.");
    if (est.adentro.length) {
      await api("/api/reset", {});
      $("estado-proc").textContent = "Se borraron los eventos de pruebas anteriores para que las cuentas den.";
    }
    const ultimos = await api("/api/eventos?limit=1");
    baseId = ultimos.length ? ultimos[0].id : 0;
    aviso("Armando el video de la partida…");
    aviso(conPatentes() ? "Armando los videos de las dos cámaras…" : "Armando el video de la partida…");
    const { nombre } = await api("/api/juego", {
      patentes: conPatentes(),
      autos: autos.map(({ color, entra, sale, patente }) => ({ color, entra, sale, patente })),
    });
    await api("/api/procesar", { video: nombre, guardar_video: false });
    aviso("");
    $("cancha").hidden = true; $("frontal").hidden = true;
    const feed = $("feed"); feed.hidden = false; feed.src = "/video_feed?" + Date.now();
    esperar(nombre, false);
  } catch (e) {
    $("error").textContent = "No se pudo procesar la partida: " + e.message;
    $("btn-otra-err") || $("error").insertAdjacentHTML("afterend",
      `<button id="btn-otra-err" onclick="location.reload()">Jugar otra partida</button>`);
  }
}

async function esperar(nombre, visto) {
  try {
    const e = await api("/api/estado");
    if (e.error) throw new Error(e.error);
    $("progreso").style.width = (e.progreso * 100) + "%";
    $("estado-proc").textContent = e.procesando
      ? `El sistema está mirando la partida (${Math.round(e.progreso * 100)}%)… ${e.ultimo_mensaje || ""}` +
        (conPatentes() ? " Leer patentes es lento en un servidor chico: puede tardar varias veces lo que duró la partida." : "")
      : "";
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
  const evs = (await api("/api/eventos?limit=500")).filter((e) => e.id > baseId).sort((a, b) => a.id - b.id);
  const sisEntradas = evs.filter((e) => e.tipo_evento === "entrada");
  const sisSalidas = evs.filter((e) => e.tipo_evento === "salida");

  // Salidas reales en el orden en que cruzaron, marcando si no salió el más viejo de los de adentro.
  const salidas = autos.filter((a) => a.sale != null).sort((x, y) => x.sale - y.sale);
  const adentro = autos.slice().sort((x, y) => x.entra - y.entra);
  const filas = [];
  let ok = 0, fueraDeOrden = 0, arrastra = false;
  const total = Math.max(salidas.length, sisSalidas.length);
  for (let i = 0; i < total; i++) {
    const a = salidas[i], s = sisSalidas[i];
    let real = null, debe = null, saltea = false;
    if (a) {
      real = estadia(a, a.sale + CRUZA_SALIDA);
      debe = cobro(real);
      const primero = adentro.find((x) => x.entra <= a.sale && !(x.sale != null && x.sale < a.sale));
      saltea = primero && primero !== a;
      if (saltea) fueraDeOrden++;
    }
    // Bien = mismo monto y la misma estadía (con 30 s de margen por el redondeo de los cuadros).
    const montoOk = a && s && Math.abs((s.monto ?? 0) - debe) < 0.01;
    const estadiaOk = a && s && Math.abs((s.duracion_seg ?? 0) - real) <= 30;
    const patenteOk = !conPatentes() || (a && s && s.patente === a.patente);
    const bien = montoOk && estadiaOk && patenteOk;
    if (bien) ok++;
    const nota = !a ? "el sistema vio una salida que no hubo"
      : !s ? "el sistema no vio esta salida"
      : bien ? (saltea ? "salió fuera de orden y lo cobró bien por la patente" : "")
      : !patenteOk ? (s.patente ? `leyó otra patente (${chapa(s.patente)})` : "no leyó la patente")
      : montoOk ? `monto bien de casualidad: ${saltea ? "le asignó la salida al que entró primero" : arrastra ? "arrastra el cruce de una salida anterior" : "la estadía no coincide"}`
      : saltea ? "le cobró al que entró primero" : arrastra ? "arrastra el cruce de una salida anterior" : "no coincide";
    if (saltea) arrastra = true;
    filas.push(`<tr class="${bien ? "bien" : "mal"}"><td>${i + 1}</td>
      <td>${a ? `<i class="pin" style="background:${a.color}"></i>${esc(a.nombre)}` : "—"}</td>
      ${conPatentes() ? `<td><b class="chapa">${a ? chapa(a.patente) : "—"}</b></td><td><b class="chapa">${s?.patente ? chapa(s.patente) : "—"}</b></td>` : ""}
      <td>${minutos(real)}</td><td>${money(debe)}</td>
      <td>${s ? minutos(s.duracion_seg) : "—"}</td><td>${s ? money(s.monto) : "—"}</td>
      <td>${bien ? "✓" : "✗"} <small>${nota}</small></td></tr>`);
  }
  $("comparacion").innerHTML = filas.join("") || `<tr><td colspan="9" class="vacio">No salió ningún auto en la partida.</td></tr>`;

  const recaudoDebe = salidas.reduce((s, a) => s + cobro(estadia(a, a.sale + CRUZA_SALIDA)), 0);
  const recaudoSis = sisSalidas.reduce((s, e) => s + (e.monto || 0), 0);
  $("conteo").innerHTML = `
    <div><b>${sisEntradas.length} / ${autos.length}</b><span>entradas vistas</span></div>
    <div><b>${sisSalidas.length} / ${salidas.length}</b><span>salidas vistas</span></div>
    <div><b>${money(recaudoSis)}</b><span>cobró el sistema</span></div>
    <div><b>${money(recaudoDebe)}</b><span>debía cobrar</span></div>`;

  const notas = [];
  if (sisEntradas.length < autos.length)
    notas.push(`El detector vio ${sisEntradas.length} de ${autos.length} entradas: dos autos muy pegados se cuentan como uno (el segundo entra gratis).`);
  if (sisEntradas.length > autos.length || sisSalidas.length > salidas.length)
    notas.push("El detector contó de más: vio cruces que no hubo.");
  if (fueraDeOrden && conPatentes() && ok === total)
    notas.push(`${fueraDeOrden === 1 ? "Una salida fue" : `${fueraDeOrden} salidas fueron`} fuera de orden y el sistema igual cobró bien: leyó las patentes.`);
  else if (fueraDeOrden)
    notas.push(`${fueraDeOrden === 1 ? "Una salida fue" : `${fueraDeOrden} salidas fueron`} de un auto que no era el primero en entrar. ` +
      (conPatentes() ? "Si alguna salió mal, revisá qué patente leyó." : "Sin patentes, el sistema le cobra la salida al que entró primero: activá la cámara frontal."));
  if (!notas.length && ok === total)
    notas.push(total ? "Todo bien: el sistema vio todos los autos y cobró exacto cada salida." : "El sistema vio todas las entradas.");
  $("conclusion").innerHTML = notas.map(esc).join("<br>");
  $("resultado").hidden = false;
  $("resultado").scrollIntoView({ behavior: "smooth" });
}

// ---------- arranque ----------

function columnas() {
  $("th-patentes").hidden = !conPatentes();
  $("frontal").hidden = !conPatentes();
  document.querySelectorAll(".th-chapa").forEach((th) => { th.hidden = !conPatentes(); });
  claveLista = ""; listas();
}
$("frontal-on").addEventListener("change", columnas);
columnas();
$("btn-entra").addEventListener("click", entrar);
$("btn-terminar").addEventListener("click", terminar);
$("btn-otra").addEventListener("click", () => location.reload());
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "e" || e.key === "E") entrar();
  const n = Number(e.key);
  if (n >= 1 && n <= 9) {  // 1..9: sale el auto que está en ese lugar de la lista
    const est = autos.filter((a) => a.sale == null);
    if (est[n - 1]) salir(est[n - 1].n);
  }
});
api("/api/estado").then((e) => {
  tarifa = e.tarifa;
  $("tarifa").textContent = `${money(tarifa.tarifa_hora)} por hora, por fracción de ${tarifa.fraccion_min} min`;
}).catch(() => {});
requestAnimationFrame(cuadro);
