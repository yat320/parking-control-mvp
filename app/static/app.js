// Panel: consulta /api/estado y /api/eventos cada segundo y maneja los botones.
const $ = (id) => document.getElementById(id);
const money = (n) => n == null ? "—" : "$" + Number(n).toLocaleString("es-AR", { maximumFractionDigits: 2 });
const hora = (iso) => iso ? new Date(iso).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "medium" }) : "—";
const dur = (s) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
};
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

let tarifaCargada = false;

async function refrescar() {
  try {
    const [e, eventos] = await Promise.all([api("/api/estado"), api("/api/eventos?limit=30")]);
    const b = $("barrera");
    b.textContent = "BARRERA " + e.barrera.toUpperCase();
    b.className = "barrera " + e.barrera;
    $("progreso").style.width = (e.progreso * 100) + "%";
    $("btn-procesar").disabled = e.procesando;
    $("btn-detener").disabled = !e.procesando;
    $("btn-abrir").disabled = e.procesando;
    if (e.procesando) {
      $("sin-video").style.display = "none";
      if (!$("feed").getAttribute("src")) $("feed").src = "/video_feed?t=" + Date.now();  // se abrió a mitad de un video
    }
    $("mensaje").textContent = e.procesando
      ? (e.detectados ? `AUTO DETECTADO (${e.detectados})` : "procesando…")
      : (e.hay_video_salida ? "video procesado guardado en output/" : "");
    $("error").textContent = e.error || "";
    $("s-entradas").textContent = e.stats.entradas;
    $("s-salidas").textContent = e.stats.salidas;
    $("s-adentro").textContent = e.stats.adentro;
    $("s-recaudado").textContent = money(e.stats.recaudado);
    $("adentro").innerHTML = e.adentro.length
      ? e.adentro.map((a) => `<li><b>${esc(a.vehicle_id)}</b> desde ${esc(hora(a.timestamp))}</li>`).join("")
      : '<li class="vacio">Ninguno</li>';
    const sel = $("video");
    const nombres = e.videos.map((v) => v.nombre).join("|");
    if (sel.dataset.lista !== nombres) {  // rearmar solo si cambió la lista
      const elegido = sel.value || e.video;
      sel.innerHTML = e.videos.map((v) => `<option value="${esc(v.nombre)}">${esc(v.nombre)}</option>`).join("");
      if (e.videos.some((v) => v.nombre === elegido)) sel.value = elegido;
      sel.dataset.lista = nombres;
    }
    if (e.procesando) sel.value = e.video;
    sel.disabled = e.procesando;
    const info = e.videos.find((v) => v.nombre === sel.value);
    $("video-desc").textContent = info && info.descripcion ? info.descripcion + " ·" : "";
    if (!tarifaCargada) {
      $("tarifa-hora").value = e.tarifa.tarifa_hora;
      $("fraccion").value = e.tarifa.fraccion_min;
      tarifaCargada = true;
    }
    $("eventos").innerHTML = eventos.length ? eventos.map((ev) => `
      <tr>
        <td>${ev.id}</td>
        <td class="tipo-${ev.tipo_evento}">${ev.tipo_evento.toUpperCase()}</td>
        <td>${esc(hora(ev.timestamp))}</td>
        <td>${esc(ev.vehicle_id)}</td>
        <td>${ev.tracking_id ?? "—"}</td>
        <td>${esc(ev.estado_barrera)}</td>
        <td>${dur(ev.duracion_seg)}</td>
        <td>${money(ev.monto)}</td>
      </tr>`).join("") : '<tr><td colspan="8" class="vacio">Sin eventos todavía.</td></tr>';
  } catch (err) {
    $("error").textContent = "Sin conexión con el servidor: " + err.message;
  }
}

$("btn-procesar").onclick = async () => {
  try {
    // Reconectar el stream por si el navegador lo cortó.
    $("feed").src = "/video_feed?t=" + Date.now();
    await api("/api/procesar", { guardar_video: $("guardar").checked, video: $("video").value });
    refrescar();
  } catch (err) { $("error").textContent = err.message; }
};
$("video").onchange = refrescar;
$("btn-detener").onclick = () => api("/api/detener", {}).then(refrescar);
$("btn-abrir").onclick = () => api("/api/barrera/abrir", {}).then(refrescar).catch((err) => ($("error").textContent = err.message));
$("btn-reset").onclick = async () => {
  if (!confirm("¿Borrar todos los eventos de la demo?")) return;
  await api("/api/reset", {});
  $("feed").removeAttribute("src");
  $("sin-video").style.display = "";
  refrescar();
};
$("form-tarifa").onsubmit = async (ev) => {
  ev.preventDefault();
  const t = await api("/api/tarifa", { tarifa_hora: Number($("tarifa-hora").value), fraccion_min: Number($("fraccion").value) });
  $("tarifa-ok").textContent = `Guardada: ${money(t.tarifa_hora)}/h, fracción ${t.fraccion_min} min`;
  setTimeout(() => ($("tarifa-ok").textContent = ""), 3000);
};

refrescar();
setInterval(refrescar, 1000);
