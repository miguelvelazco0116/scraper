"""Streamlit manual integration of captured, validated same-day retailer data."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.execution import (
    ALL, RUNNERS, current_active_job, latest_jobs, local_day,
    read_run_log, start_existing_import,
)
from dashboard.existing_integration import inspect_existing_capture


def render(root: Path) -> None:
    st.header("Integrar captura existente")
    st.caption(
        "Agrega o actualiza UN retailer ya capturado hoy, sin volver a "
        "ejecutar el scraper. Respeta las fases previas y los controles de calidad."
    )
    a, b = st.columns([1, 2])
    phase = a.selectbox(
        "Fase de la captura", sorted(RUNNERS), key="existing_phase"
    )
    retailer = b.selectbox(
        "Retailer", [r for r in RUNNERS[phase] if r != ALL],
        key="existing_retailer",
    )
    selected = (phase, retailer, local_day())
    if st.session_state.get("existing_selected") != selected:
        st.session_state["existing_selected"] = selected
        st.session_state["existing_preview"] = None
        # Authorisation must be renewed for each retailer/date selection.
        st.session_state["existing_import_consent"] = False

    if retailer == "Mayorista Duero":
        st.warning(
            "Duero se integrará únicamente como catálogo público online. "
            "No se requiere validación de tienda ni código postal, y NO se "
            "certifican precios ni inventario para un domicilio/CP. "
            "Se mantienen las validaciones de categorías, paginación, SKU, "
            "precios, URLs, disponibilidad y empaque."
        )

    if st.button("1 · Verificar captura disponible de hoy", type="secondary"):
        try:
            result = inspect_existing_capture(root, phase, retailer)
            st.session_state["existing_preview"] = result
        except (ValueError, KeyError, OSError, TypeError) as exc:
            st.session_state["existing_preview"] = None
            st.error("Captura no integrable: " + str(exc))

    preview = st.session_state.get("existing_preview")
    if preview:
        st.success(
            f"Captura validada: {preview['retailer']} · "
            f"{preview['cases']} categorías · "
            f"{preview['products']:,} productos · {preview['capture_date']}"
        )
        st.code(preview["source"], language="text")
        if preview["scope"].startswith("PUBLIC_ONLINE"):
            st.caption("Alcance: catálogo público sin certificación de tienda ni CP.")
        st.caption(
            "La integración reemplaza los registros anteriores de este retailer "
            "en el master del día; no duplica filas. Se crean respaldos "
            "antes de escribir."
        )

    active = current_active_job(root)
    if active:
        st.warning(
            f"Existe un trabajo activo: Fase {active.get('phase')} · "
            f"{active.get('target')} · {active.get('status')}. "
            "No se permitirá integrar al mismo tiempo."
        )
    authorized = st.checkbox(
        "Autorizo integrar únicamente este retailer desde la captura "
        "validada de hoy y actualizar el master diario.",
        key="existing_import_consent",
    )
    if st.button(
        "2 · Integrar retailer al master",
        type="primary",
        disabled=preview is None or active is not None or not authorized,
        use_container_width=True,
    ):
        try:
            job = start_existing_import(root, phase, retailer)
            st.session_state["existing_preview"] = None
            st.success("Integración iniciada. Folio: " + job["run_id"][:12])
            st.rerun()
        except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
            st.error("No se pudo integrar: " + str(exc))

    st.divider()
    st.subheader("Integraciones recientes")

    @st.fragment(run_every="4s")
    def history():
        jobs = [
            item for item in latest_jobs(root, limit=100)
            if item.get("job_type") == "integracion_existente"
        ]
        if not jobs:
            st.caption("Aún no hay integraciones manuales desde Streamlit.")
            return
        for item in jobs[:5]:
            with st.expander(
                f"Fase {item.get('phase')} · {item.get('target')} · "
                f"{item.get('status')} · {item.get('run_id', '')[:12]}",
                expanded=item.get("status") in ("RUNNING", "PENDING", "ERROR", "REJECTED"),
            ):
                st.caption(
                    "El código de salida no certifica por sí solo la fase: "
                    "verifica Resumen ejecutivo y la auditoría del master."
                )
                if item.get("integrity_warning"):
                    st.error(item["integrity_warning"])
                if item.get("error"):
                    st.error(item["error"])
                st.code(
                    read_run_log(root, item["run_id"])[-16000:],
                    language="text",
                )

    history()
