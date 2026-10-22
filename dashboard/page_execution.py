"""Streamlit page for explicit local executions with a durable run monitor."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.execution import (
    ALL, RUNNERS, active_pointer, current_active_job, latest_jobs, local_day,
    prepare_run, public_command, read_run_log, start_run,
)

def render(root: Path):
    st.header("Ejecución de scraping")
    st.caption("Ejecución real desde Streamlit, mediante los scripts PowerShell existentes. "
               "Cada intento genera historial y un log independiente.")
    st.info("Se permite **una sola ejecución a la vez**. Los runners que usan Chrome "
            "requieren que las pestañas y las tiendas estén preparadas manualmente.")

    left, right = st.columns([1, 2])
    phase = left.selectbox("Fase a ejecutar", sorted(RUNNERS), key="exec_phase")
    target = right.selectbox("Alcance", list(RUNNERS[phase]), key="exec_target")

    if target == ALL:
        modes = ["Solo captura", "Captura + integración"] if phase != 1 else ["Captura + integración"]
    else:
        modes = ["Solo captura"]
    mode_label = st.radio("Modalidad", modes, horizontal=True, key="exec_mode")
    mode = "solo_captura" if mode_label == "Solo captura" else "captura_integracion"

    pointer = active_pointer(root)
    active_day = pointer.get("capture_date")
    if active_day:
        st.caption(f"Fecha local: {local_day()} | Master activo: {active_day}")
    else:
        st.warning("No se encontró puntero productivo. Las fases que requieren master del día se bloquearán.")

    if target == ALL and phase == 1:
        st.warning("El runner de Fase 1 integra automáticamente. "
                   "Para iniciar un nuevo día, revisa antes sus validaciones y respaldos.")
    elif mode == "captura_integracion":
        st.warning("Se ejecutará el integrador real y **podría modificar el master**. "
                   "Solo incorpora retailers que pasan sus gates, pero el exit code 0 no equivale a COMPLETE.")
    elif target != ALL:
        st.info("El runner por retailer genera una captura de prueba; no integra al master automáticamente.")
    else:
        st.info("El runner usará **-SkipIntegration**. No deberá modificar el master.")

    if phase == 5:
        st.caption("Mayorista Duero: la opción especial de catálogo online público requiere "
                   "aprobación explícita aparte. Esta pantalla **nunca** agrega "
                   "-ApproveDueroOnline de forma automática.")
    if phase in (2, 3, 4, 5) and (target == ALL or target in {"HEB", "Sam's Club", "Bodega Aurrera", "Walmart", "Farmacias San Pablo"}):
        st.caption("Para los retailers con CDP, habilita remote debugging en Chrome y abre los sitios "
                   "correspondientes antes de iniciar.")

    st.subheader("Comando que se ejecutará")
    st.code(public_command(root, phase, target, mode), language="powershell")

    try:
        spec = prepare_run(root, phase, target, mode)
        blocker = None
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        spec = None
        blocker = str(error)
        st.error("Ejecución bloqueada: " + blocker)

    active = current_active_job(root)
    if active:
        st.warning("Hay una ejecución en curso: "
                   f"Fase {active.get('phase')} · {active.get('target')} · "
                   f"{active.get('status')}. No se iniciará otra.")

    confirmation = st.checkbox(
        "Confirmo que revisé la fecha, el alcance, las pestañas necesarias "
        "y que autorizo ejecutar este scraper localmente.",
        key="exec_confirm"
    )
    if mode == "captura_integracion":
        allow_write = st.checkbox(
            "Autorizo la integración incremental y el eventual cambio del master de hoy.",
            key="exec_allow_master"
        )
    else:
        allow_write = True

    if st.button("▶ Iniciar ejecución", type="primary",
                 disabled=spec is None or active is not None or not confirmation or not allow_write,
                 use_container_width=True):
        try:
            job = start_run(root, phase, target, mode)
            st.success("Ejecución aceptada. Folio: " + job["run_id"][:12]
                       + ". Consulta el seguimiento debajo.")
            st.rerun()
        except (ValueError, RuntimeError, OSError) as exc:
            st.error("No se pudo iniciar: " + str(exc))

    st.divider()
    st.subheader("Seguimiento e historial")
    st.caption("Se actualiza automáticamente. El proceso continúa aunque cambies de sección.")

    @st.fragment(run_every="4s")
    def monitor():
        jobs = latest_jobs(root)
        if not jobs:
            st.info("Aún no hay ejecuciones iniciadas desde Streamlit.")
            return
        overview = pd.DataFrame([
            {
                "Fecha": x.get("created_at", ""),
                "Fase": x.get("phase"),
                "Retailer": x.get("target"),
                "Modo": x.get("mode"),
                "Estado": x.get("display_status", x.get("status")),
                "Exit code": x.get("exit_code"),
            } for x in jobs
        ])
        st.dataframe(overview, hide_index=True, use_container_width=True, height=min(340, 70 + len(overview) * 36))
        ids = [j["run_id"] for j in jobs]
        selected = st.selectbox(
            "Ver detalle de ejecución",
            ids,
            format_func=lambda x: next((
                f"{job.get('created_at', '')} · F{job.get('phase')} · "
                f"{job.get('target', '')} · {job.get('display_status', job.get('status'))}"
                for job in jobs if job.get("run_id") == x
            ), x),
            key="execution_detail_id",
        )
        run = next(j for j in jobs if j["run_id"] == selected)
        if run.get("integrity_warning"):
            st.error(run["integrity_warning"])
        elif run["status"] == "SUCCESS":
            st.success("Script finalizado con código cero. **Verifica la auditoría** antes de considerar la fase COMPLETE.")
        elif run["status"] == "ERROR":
            st.error("El script terminó con un error. Revisa el log.")
        elif run["status"] == "REJECTED":
            st.warning("Intento rechazado: " + str(run.get("error", "Sin detalle")))
        elif run.get("display_status"):
            st.error("Worker sin latido reciente. Verifica procesos antes de lanzar otra captura.")
        log = read_run_log(root, selected)
        st.code(log[-16000:], language="text")

    monitor()
