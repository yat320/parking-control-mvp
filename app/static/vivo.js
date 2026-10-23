// Partida en vivo (app/vivo.py): el sistema procesa el simulador mientras se juega, así los
// eventos aparecen en el panel apenas un auto cruza la línea. La usan juego.js y cabina.js.
// Si el sistema está ocupado con otro video, la página sigue como antes (procesa al terminar).
const Vivo = {
  NOMBRE: "partida en vivo",
  activo: false,
  baseId: 0,          // los eventos de esta partida son los de id mayor a este
  cola: Promise.resolve(),
  ultimo: "",

  async post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },

  // Arranca a procesar. `t`: segundo del juego en el que está la página ahora.
  async empezar(patentes, t) {
    try {
      const est = await (await fetch("/api/estado")).json();
      if (est.procesando) return false;
      // Si quedaron autos "adentro" de otra prueba, el sistema les cobraría a ellos: de cero.
      if (est.adentro.length) await this.post("/api/reset", {});
      const ultimos = await (await fetch("/api/eventos?limit=1")).json();
      this.baseId = ultimos.length ? ultimos[0].id : 0;
      await this.post("/api/vivo/empezar", { patentes, t });
      this.activo = true;
      return true;
    } catch (e) {
      return false;
    }
  },

  // Manda la partida hasta ahora (solo si cambió, salvo `fin`). Los envíos van en orden.
  mandar(autos, fin = null, forzar = false) {
    if (!this.activo) return this.cola;
    const cuerpo = JSON.stringify({ autos, fin });
    if (cuerpo === this.ultimo && !forzar) return this.cola;
    this.ultimo = cuerpo;
    this.cola = this.cola.then(() => this.post("/api/vivo/autos", { autos, fin })).catch(() => {});
    return this.cola;
  },

  // Lo que va viendo el sistema, para mostrarlo mientras se juega.
  async contar() {
    const evs = (await (await fetch("/api/eventos?limit=200")).json()).filter((e) => e.id > this.baseId);
    return { entradas: evs.filter((e) => e.tipo_evento === "entrada").length,
             salidas: evs.filter((e) => e.tipo_evento === "salida").length };
  },
};
