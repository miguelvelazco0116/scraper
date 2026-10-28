"""Streamlit page for explicit local executions with a durable run monitor."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.execution import (
    ALL, RUNNERS, active_pointer, current_active_job, latest_jobs, local_day,
    mode_labels, prepare_run, public_command, read_run_log, start_run, request_stop, stop_requested,
)

@st.cache_data(ttl=30, show_spinner=False)
def _audit_soriana_cached(root: Path, day: str) -> dict:
    from dashboard.soriana_rotation import audit_snapshot
    return audit_snapshot(root, day)


def render(root: Path):
    st.header("Ejecución de scraping")
    st.caption("Ejecución real desde Streamlit, mediante los scripts PowerShell existentes. "
               "Cada intento genera historial y un log independiente.")
    st.info("Se permite **una sola ejecución a la vez**. Los runners que usan Chrome "
            "requieren que las pestañas y las tiendas estén preparadas manualmente.")

    @st.fragment(run_every="3s")
    def stop_control():
        # Always visible near the page title, independently of the selected
        # historical log. Frequent refresh never reruns the capture itself.
        jobs = latest_jobs(root, limit=100)
        running = next(
            (job for job in jobs if job.get("status") in ("PENDING", "RUNNING")),
            None,
        )
        if running is None:
            st.caption("Sin ejecuciones activas desde Streamlit.")
            return

        run_id = running["run_id"]
        with st.container(border=True):
            st.markdown("**Ejecución activa · Control de paro**")
            st.caption(
                f"Fase {running.get('phase')} · {running.get('target')} · "
                f"{running.get('status')} · Folio {run_id[:12]}"
            )
            if running.get("display_status") == "SIN LATIDO - REVISAR":
                st.error(
                    "Sin señales recientes del worker. Verifica los procesos en "
                    "PowerShell antes de solicitar otro intento."
                )
                return
            if stop_requested(root, run_id):
                st.warning("Paro solicitado. No inicies otra ejecución hasta que aparezca STOPPED.")
                return
            if st.button(
                "⏹ Detener ejecución activa",
                key="active_stop_" + run_id,
                type="secondary",
                use_container_width=True,
            ):
                try:
                    request_stop(root, run_id)
                    st.warning("Paro solicitado. Se detendrá el árbol de procesos de esta ejecución.")
                    st.rerun()
                except (OSError, RuntimeError, ValueError) as exc:
                    st.error("No se pudo detener: " + str(exc))

    stop_control()

    left, right = st.columns([1, 2])
    phase = left.selectbox("Fase a ejecutar", sorted(RUNNERS), key="exec_phase")
    target = right.selectbox("Alcance", list(RUNNERS[phase]), key="exec_target")

    modes = mode_labels(phase, target)
    mode_label = st.radio("Modalidad", modes, horizontal=True, key="exec_mode")
    mode = {
        "Solo captura": "solo_captura",
        "Captura + integración": "captura_integracion",
        "Integrar capturas existentes": "solo_integracion",
    }[mode_label]

    pointer = active_pointer(root)
    active_day = pointer.get("capture_date")
    if active_day:
        st.caption(f"Fecha local: {local_day()} | Master activo: {active_day}")
    else:
        st.warning("No se encontró puntero productivo. Las fases que requieren master del día se bloquearán.")

    if mode == "solo_integracion":
        prereq = ("Fase 1 puede iniciar el master de hoy." if phase == 1 else
                  f"Requiere master de hoy con fases 1 a {phase - 1} COMPLETE.")
        st.info(
            f"Fase {phase}: integrar capturas existentes **sin abrir navegador ni volver "
            f"a descargar productos**. {prereq} Solo se admiten retailers COMPLETE "
            "del día. Se realizan respaldo, auditoría posterior y rollback ante error."
        )
    elif target == ALL and phase == 1:
        st.warning("El runner de Fase 1 integra automáticamente. "
                   "Para iniciar un nuevo día, revisa antes sus validaciones y respaldos.")
    elif mode == "captura_integracion":
        st.warning("Se capturará y luego se integrarán los Excel validados al master de hoy. "
                   "Las fuentes incompletas no se consideran COMPLETE.")
    elif target != ALL:
        st.info("El runner por retailer genera una captura de prueba; no integra al master automáticamente.")
    else:
        st.info("El runner usará **-SkipIntegration**. No deberá modificar el master.")

    if phase == 2:
        st.caption(
            "Fase 2 sin duplicados: Farmacias Guadalajara se captura una vez y "
            "Soriana se ejecuta una sola vez con rotación de los 4 CP y 6 categorías. "
            "Los checkpoints COMPLETE del día se reutilizan. "
            "Solo los modos con integración pueden modificar el master de hoy."
        )
    if phase == 5:
        st.caption("Mayorista Duero: la opción especial de catálogo online público requiere "
                   "aprobación explícita aparte. Esta pantalla **nunca** agrega "
                   "-ApproveDueroOnline de forma automática.")
    if phase == 6:
        st.caption(
            "Farmacias YZA: solo catálogo online, dos categorías "
            "(medicamentos respiratorio e higiene bucal). "
            "No certifica CP, tienda ni stock por localidad. "
            "Abre Chrome, habilita chrome://inspect/#remote-debugging y "
            "deja abierta una pestaña yza.mx antes de capturar."
        )
    if phase in (2, 3, 4, 5, 6) and mode != "solo_integracion" and (target == ALL or target in {"HEB", "Sam's Club", "Bodega Aurrera", "Walmart", "Farmacias San Pablo", "Farmacias YZA"}):
        st.caption("Para los retailers con CDP, habilita remote debugging en Chrome y abre los sitios "
                   "correspondientes antes de iniciar.")

    if phase == 1:
        st.subheader("Fase 1 · Estado real de captura de hoy")
        st.caption("Combina progreso productivo y checkpoints de tienda/categoría validados del mismo día. "
                   "Solo los casos que superan controles se muestran como COMPLETE.")
        try:
            from dashboard.phase1_state import phase1_inventory
            inventory, verified_cases = phase1_inventory(root)
            completed = inventory.loc[inventory["estado"].eq("COMPLETE")]
            c1, c2, c3 = st.columns(3)
            c1.metric("Casos COMPLETE", f"{len(completed)}/{len(inventory)}")
            c2.metric("Por reintentar", len(inventory) - len(completed))
            c3.metric("Productos validados", f"{int(completed['productos_validados'].sum()):,}")
            retailer_totals = (inventory.assign(completo=inventory["estado"].eq("COMPLETE").astype(int))
                               .groupby("retailer", as_index=False)
                               .agg(casos=("estado", "size"), completos=("completo", "sum"),
                                    productos=("productos_validados", "sum")))
            st.dataframe(retailer_totals, hide_index=True, use_container_width=True)
            # Capture success and integration are different states.
            # Use the authoritative master auditor, never the job exit code.
            from dashboard.data import active_master, inventory_for_master
            master_path, master_pointer = active_master(root)
            if master_path is not None and str(master_pointer.get("capture_date")) == local_day():
                try:
                    master_phases, master_retailers, _, _ = inventory_for_master(root, master_path)
                    phase_row = master_phases.loc[master_phases["fase"].eq(1)]
                    if not phase_row.empty:
                        state = str(phase_row.iloc[0]["estado_calculado"])
                        actual_rows = int(phase_row.iloc[0]["filas_master"])
                        st.caption(f"**Integración real del día** — Fase 1: {state} · "
                                   f"{actual_rows:,} productos en master.")
                        with st.expander("Retailers de Fase 1 integrados al master"):
                            st.dataframe(master_retailers.loc[master_retailers["fase"].eq(1)],
                                         hide_index=True, use_container_width=True)
                except Exception:
                    st.warning("No se pudo verificar el estado del master; "
                               "consulta el Resumen ejecutivo. No se infiere COMPLETE.")
            else:
                st.caption("No hay master activo del día para certificar integración de Fase 1.")
            if target == "Chedraui":
                st.success("Chedraui ejecutará únicamente los casos **REINTENTAR**. "
                           "Los COMPLETE del día se omiten sin abrir sus categorías.")
            elif target == ALL:
                st.info("La fase completa reutiliza los casos validados del día y procesa únicamente pendientes.")
            with st.expander("Ver los 30 casos por tienda y categoría", expanded=target == "Chedraui"):
                filtered = inventory if target == ALL else inventory.loc[inventory["retailer"].eq(target)]
                st.dataframe(filtered, hide_index=True, use_container_width=True, height=350)
        except Exception as exc:
            st.error("No fue posible consultar el avance de Fase 1. No recomendamos ejecutar "
                     "hasta validar el progreso/checkpoints. Detalle: " + str(exc))
            inventory = None
    else:
        inventory = None

    if phase == 2 and target in (ALL, "Soriana"):
        st.subheader("Fase 2 · Soriana — rotación de códigos postales")
        st.caption(
            "Mismo estándar que Chedraui: COMPLETE / REINTENTAR, tres intentos "
            "por categoría y CP, checkpoints válidos del día, diagnóstico por intento. "
            "La captura de las cuatro zonas se registra por separado y no modifica el master."
        )

        @st.fragment(run_every="5s")
        def postal_progress():
            from dashboard.soriana_rotation import read_progress
            day = local_day()
            current = read_progress(root, day)
            if current is None:
                st.info("Sin progreso de rotación completa registrado hoy. "
                        "La auditoría de Excel se muestra a continuación si existen archivos.")
                done = 0
            else:
                done = int(current["complete_cases"])
                total = int(current["total_cases"])
                metrics = st.columns(4)
                metrics[0].metric("Casos COMPLETE", f"{done}/{total}")
                metrics[1].metric("REINTENTAR / pendientes", int(current["pending_cases"]))
                metrics[2].metric("Productos validados", f"{int(current['products_validated']):,}")
                metrics[3].metric("Intentos totales", int(current["attempts_total"]))
                st.progress(done / total if total else 0)
                display = []
                attempts = []
                for case in current["cases"]:
                    history = case.get("retry_history") or []
                    display.append({
                        "CP": case["postal_code"],
                        "Categoría": case["category_id"],
                        "Estado": case["estado"],
                        "Acción": case["accion"],
                        "Productos": int(case["productos_validados"]),
                        "Intentos": int(case["attempts"]),
                        "Reintentos": max(0, len(history) - 1),
                    })
                    for attempt in history:
                        attempts.append({
                            "CP": case["postal_code"],
                            "Categoría": case["category_id"],
                            "Intento": attempt.get("attempt"),
                            "Estado": attempt.get("status"),
                            "Productos": attempt.get("rows"),
                            "Error": attempt.get("error") or "",
                            "Diagnóstico": attempt.get("diagnostic") or "",
                        })
                st.dataframe(pd.DataFrame(display), hide_index=True,
                             use_container_width=True, height=380)
                with st.expander("Historial por intento y diagnóstico"):
                    if attempts:
                        st.dataframe(pd.DataFrame(attempts), hide_index=True,
                                     use_container_width=True, height=260)
                    else:
                        st.caption("No se realizaron nuevos intentos; "
                                   "pueden haberse reutilizado checkpoints del día.")

            # Audit the independent four workbooks; process exit/progress are
            # not proof that 24 full, same-date, region-separated cases passed.
            if done == 24 or st.button(
                "Auditar Excel de Soriana (solo lectura)", key="soriana_audit_now"
            ):
                try:
                    audited = _audit_soriana_cached(root, day)
                    if audited["certified"]:
                        st.success(
                            f"Piloto auditado: {audited['complete']}/{audited['total']} "
                            f"casos COMPLETE, {audited['validated_products']:,} productos. "
                            "Master productivo: SIN CAMBIOS."
                        )
                        if current is not None and int(current["products_validated"]) != int(audited["validated_products"]):
                            st.warning("El acumulado del progreso no coincide con la auditoría. "
                                       "Inspecciona los Excel antes de promover esta captura.")
                    else:
                        st.warning(
                            f"Auditoría independiente pendiente: "
                            f"{audited['complete']}/{audited['total']} completos."
                        )
                        if audited["issues"]:
                            st.dataframe(pd.DataFrame(audited["issues"]),
                                         hide_index=True, use_container_width=True)
                except (OSError, ValueError, KeyError) as exc:
                    st.error("No se pudo auditar la rotación: " + str(exc))

        postal_progress()

    if mode == "solo_integracion" and target == ALL:
        st.subheader("Validar capturas existentes por retailer")
        st.caption(
            "Verificación de solo lectura: identifica quién ya existe en el "
            "master, quién tiene una captura validada y quién todavía no tiene "
            "datos. No abre Chrome ni modifica archivos."
        )
        if st.button("Verificar retailers sin modificar master", key="check_retailers_existing"):
            try:
                if phase == 2:
                    from scripts.integrate_phase2_postal import prepare_plan
                    preflight = prepare_plan(root, local_day())
                    statuses = preflight["retailers"]
                elif phase == 6:
                    from scripts.integrate_phase6_yza import preview as yza_preview
                    preflight = yza_preview(local_day())
                    statuses = {"Farmacias YZA": {
                        "status": preflight["state"],
                        "products": preflight["phase6_rows"],
                        "detail": preflight.get("detail", ""),
                    }}
                else:
                    from scripts.integrate_existing_phases import prepare as prepare_existing
                    preflight = prepare_existing(root, phase, local_day())
                    statuses = preflight["retailers"]
                rows = [{
                    "Retailer": name, "Estado": item["status"],
                    "Productos": item["products"], "Detalle": item.get("detail", ""),
                } for name, item in statuses.items()]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                if all(item["status"] in {"YA_EXISTE_EN_MASTER", "SIN_CAPTURA", "CAPTURA_NO_VALIDADA"}
                       for item in statuses.values()):
                    st.info("No hay retailers nuevos validados para integrar. Master SIN CAMBIOS.")
            except (ValueError, KeyError, OSError, TypeError) as exc:
                st.warning("Prevalidación bloqueada: " + str(exc))

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
        ("Confirmo que revisé la fecha, el master y los Excel ya descargados; "
         f"autorizo integrar Fase {phase} sin ejecutar scraper."
         if mode == "solo_integracion" else
         "Confirmo que revisé la fecha, el alcance, las pestañas necesarias "
         "y que autorizo ejecutar este scraper localmente."),
        key="exec_confirm"
    )
    if mode in {"captura_integracion", "solo_integracion"}:
        allow_write = st.checkbox(
            "Autorizo la integración incremental y el eventual cambio del master de hoy.",
            key="exec_allow_master"
        )
    else:
        allow_write = True

    if st.button("▶ Iniciar ejecución", type="primary",
                 disabled=spec is None or active is not None or not confirmation or not allow_write or (phase == 1 and inventory is None and mode != "solo_integracion"),
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
    st.caption("Se actualiza automáticamente. Para detener un trabajo activo usa el control visible al principio de esta pantalla o selecciona el folio.")

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
        if run["status"] in ("RUNNING", "PENDING"):
            if stop_requested(root, selected):
                st.warning("Paro solicitado. Espera a que el proceso libere el bloqueo antes de iniciar otro.")
            elif st.button("⏹ Detener ejecución", key="stop_" + selected,
                           type="secondary", use_container_width=True):
                try:
                    request_stop(root, selected)
                    st.warning("Orden de paro enviada. El árbol de procesos se detendrá; "
                               "los checkpoints COMPLETE anteriores se conservan.")
                    st.rerun()
                except (ValueError, RuntimeError, OSError) as exc:
                    st.error("No se logró solicitar el paro: " + str(exc))
        if run.get("integrity_warning"):
            st.error(run["integrity_warning"])
        elif run["status"] == "SUCCESS":
            st.success("Script finalizado con código cero. **Verifica la auditoría** antes de considerar la fase COMPLETE.")
        elif run["status"] == "STOPPED":
            st.warning("Ejecución detenida por el operador. Revisa qué casos quedaron validados antes de reanudar.")
        elif run["status"] == "ERROR":
            st.error("El script terminó con un error. Revisa el log.")
        elif run["status"] == "REJECTED":
            st.warning("Intento rechazado: " + str(run.get("error", "Sin detalle")))
        elif run.get("display_status") and "SIN LATIDO" in run["display_status"]:
            st.error("Worker sin latido reciente. Verifica procesos antes de lanzar otra captura.")
        log = read_run_log(root, selected)
        st.code(log[-16000:], language="text")

    monitor()
