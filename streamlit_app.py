"""Scraper operations dashboard with allowlisted local Windows runners."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dashboard.azure_sql import load_settings, save_settings, test_connection
from dashboard.data import (
    active_master, available_masters, capture_dates, checkpoint_files,
    inventory_for_master, load_master_rows, log_events, metrics_by_retailer,
    quality_findings, recent_logs, safe_cli_command, safe_log_tail,
)

st.set_page_config(page_title="Scraper | Centro de control", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
.block-container {padding-top:1.5rem;padding-bottom:2rem}
[data-testid="stMetric"] {background:rgba(120,120,120,.06);padding:14px;border-radius:12px}
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=90, show_spinner="Leyendo master y auditoría…")
def load_report(path, mtime):
    phases, retailers, cases, facts = inventory_for_master(ROOT, Path(path))
    rows = load_master_rows(Path(path))
    return phases, retailers, cases, facts, rows

@st.cache_data(ttl=90, show_spinner=False)
def load_quality(path, mtime):
    from scripts.audit_master_phases import EXPECTED
    frame = load_master_rows(Path(path))
    return metrics_by_retailer(frame, EXPECTED), quality_findings(frame, EXPECTED)

def table(frame, height=380):
    st.dataframe(frame, use_container_width=True, hide_index=True, height=height)

def csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8-sig")

st.sidebar.title("Centro de control")
section = st.sidebar.radio("Módulo", [
    "Resumen ejecutivo", "Calidad de datos", "Fallas y diagnósticos",
    "Nueva ejecución", "Guía de comandos CLI", "Azure SQL", "Trazabilidad e histórico",
])
active, pointer = active_master(ROOT)
masters = available_masters(ROOT)
if active and active not in masters:
    masters.insert(0, active)
selected = (st.sidebar.selectbox(
    "Snapshot", masters, index=masters.index(active) if active in masters else 0,
    format_func=lambda p: p.name
) if masters else None)
if st.sidebar.button("Actualizar tablero"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Captura local con confirmación. Calidad, histórico y Azure SQL siguen independientes.")
if pointer:
    st.sidebar.caption("Fecha del puntero activo: " + str(pointer.get("capture_date", "N/D")))

st.title("Scraper · Centro de control")
st.caption("Calidad, fallas, operación e integración futura con Azure SQL")

phases = retailers = cases = rows = pd.DataFrame()
facts = {"warnings": []}
if selected and section in ("Resumen ejecutivo", "Calidad de datos", "Trazabilidad e histórico"):
    try:
        phases, retailers, cases, facts, rows = load_report(str(selected), selected.stat().st_mtime_ns)
    except Exception as error:
        st.error("No se pudo leer el master. Revisa las hojas Concentrado, Resumen y Control.")
        st.code(str(error))
elif not selected and section in ("Resumen ejecutivo", "Calidad de datos", "Trazabilidad e histórico"):
    st.warning("No hay master disponible; captura las fases desde CLI y vuelve a abrir el panel.")

if section == "Resumen ejecutivo":
    if not phases.empty:
        a, b, c, d = st.columns(4)
        a.metric("Registros", f"{facts['total_rows']:,}")
        b.metric("Retailers integrados", f"{facts['total_retailers']}/{int(phases['retailers_esperados'].sum())}")
        c.metric("Fases completas", f"{facts['phases_complete']}/{len(phases)}")
        d.metric("Casos COMPLETE", f"{int(phases['casos_complete'].sum())}/{int(phases['casos_esperados'].sum())}")
        if facts["warnings"]:
            st.warning("Auditoría con observaciones: " + " | ".join(map(str, facts["warnings"])))
        else:
            st.success("Master reconciliado: estados de fases y filas coinciden con Control.")
        st.subheader("Avance de las cinco fases")
        table(phases[["fase", "estado_calculado", "estado_control", "control_coincide",
                      "retailers_complete", "retailers_esperados", "filas_master",
                      "casos_complete", "casos_esperados"]], 235)
        st.subheader("Estado de cada retailer")
        table(retailers[["fase", "retailer", "estado", "filas_master", "casos_master",
                         "casos_esperados", "alcance"]], 470)
        st.download_button("Exportar inventario CSV", csv_bytes(retailers),
                           "inventario_retailers.csv", "text/csv")
    else:
        st.info("Selecciona un master válido.")

elif section == "Calidad de datos":
    st.header("Calidad de datos")
    st.caption("Indicadores medidos; la aprobación definitiva procede de los gates por retailer.")
    if not rows.empty:
        try:
            metrics, findings = load_quality(str(selected), selected.stat().st_mtime_ns)
            st.subheader("Completitud por retailer")
            st.caption("N/D significa que no hay evidencia del campo. No convierte ausencias opcionales en fallas productivas.")
            table(metrics, 480)
            st.download_button("Descargar métricas CSV", csv_bytes(metrics),
                               "calidad_por_retailer.csv", "text/csv")
            st.subheader("Observaciones informativas")
            st.caption("Máximo 500 filas. Estas observaciones no reemplazan las reglas particulares ni recalifican un retailer.")
            if findings.empty:
                st.success("Sin observaciones en los campos revisados.")
            else:
                table(findings, 330)
                st.download_button("Descargar hallazgos CSV", csv_bytes(findings),
                                   "observaciones_calidad.csv", "text/csv")
            st.subheader("Detalle de categorías")
            if not cases.empty:
                selected_phase = st.selectbox("Fase", ["Todas"] + [str(x) for x in sorted(cases["fase"].unique())])
                filtered = cases if selected_phase == "Todas" else cases.loc[cases["fase"].eq(int(selected_phase))]
                table(filtered, 380)
        except Exception as exc:
            st.error("No fue posible producir indicadores de calidad: " + str(exc))
    else:
        st.info("No hay filas productivas para analizar.")

elif section == "Fallas y diagnósticos":
    st.header("Fallas y diagnósticos")
    st.caption("Eventos históricos: una falla en logs anteriores no implica que la captura vigente esté bloqueada.")
    events = log_events(ROOT)
    if events.empty:
        st.info("No se encontraron errores o advertencias en los logs accesibles.")
    else:
        table(events, 360)
        st.download_button("Exportar eventos", csv_bytes(events), "eventos_scraper.csv", "text/csv")
    log_list = recent_logs(ROOT)
    if log_list:
        chosen_log = st.selectbox("Inspeccionar log", log_list, format_func=lambda p: str(p.relative_to(ROOT)))
        st.text_area("Últimos 64 KB del log", safe_log_tail(chosen_log), height=330, disabled=True)
    else:
        st.info("Aún no hay logs locales dentro de diagnostics/.")
    st.caption("La auditoría del master es autoritativa. Exit code 0 no significa que todos los retailers se hayan integrado.")

elif section == "Nueva ejecución":
    from dashboard.page_execution import render
    render(ROOT)

elif section == "Guía de comandos CLI":
    from integrate_complete_retailers import EXPECTED
    st.header("Guía de comandos CLI")
    st.info("Estos comandos siguen disponibles. Para lanzar procesos desde la interfaz utiliza **Nueva ejecución**.")
    a, b = st.columns([1, 2])
    phase = a.selectbox("Fase", sorted(EXPECTED))
    retailer = b.selectbox("Retailer", ["Toda la fase"] + list(EXPECTED[phase]))
    choice = None if retailer == "Toda la fase" else retailer
    st.subheader("Comando preparado para CLI")
    st.code(safe_cli_command(ROOT, phase, choice), language="powershell")
    if choice is not None and phase == 1 and "No hay runner" in safe_cli_command(ROOT, phase, choice):
        st.caption("Se recomienda utilizar el runner integral correspondiente para este retailer.")
    st.info(
        "Para todas las fases puedes elegir **Integrar capturas existentes** "
        "desde Nueva ejecución → Todas las tiendas, sin volver a descargar. "
        "Sólo se integran retailers validados del día; las fases previas "
        "deben estar COMPLETE. Fase 2 utiliza la rotación postal Soriana."
    )
    if phase == 2:
        st.info(
            "Fase 2: Soriana usa una sola rotación de 4 códigos postales. "
            "La integración exige un master del día con Fase 1 COMPLETE."
        )
        st.subheader("Auditoría independiente de Soriana (solo lectura)")
        st.code(
            "python .\\scripts\\audit_soriana_cp_rotation.py --day (Get-Date -Format yyyy-MM-dd)",
            language="powershell",
        )
        st.subheader("Integrar capturas existentes sin scraping")
        st.code("& .\\scripts\\integrate_phase2_postal.ps1 -Preview", language="powershell")
        st.caption("Para aplicar el cambio, usa Nueva ejecución → Fase 2 → "
                   "Todas las tiendas → Integrar capturas existentes.")
    elif phase == 6:
        st.info("YZA: 2 categorías del catálogo online, sin CP ni tienda verificados. Para capturar, Chrome CDP manual.")
        st.subheader("Previsualizar integración de Farmacias YZA")
        st.code("& .\\scripts\\integrate_phase6_yza.ps1 -Preview", language="powershell")
    else:
        st.subheader("Previsualizar integración de capturas existentes")
        st.code(f"& .\\scripts\\integrate_existing_phases.ps1 -Phase {phase} -Preview", language="powershell")
    if phase == 5:
        st.warning("Duero online: nunca se aprueba automáticamente desde el panel. La aprobación exige revisar evidencia y autorizarla por separado.")
    st.subheader("Checklist de operador (informativo)")
    st.checkbox("Confirmé la fecha y el master activo", key="check_master")
    st.checkbox("Confirmé Chrome/CDP y contexto de las tiendas", key="check_chrome")
    st.checkbox("Conozco qué scripts integran automáticamente", key="check_integration")
    st.caption("Marcar estas casillas no lanza ninguna captura.")

elif section == "Azure SQL":
    st.header("Azure SQL · Configuración")
    st.info("Preparación de conectividad. No se crean tablas, no se importan registros y no se modifica Excel.")
    saved = load_settings(ROOT)
    st.caption("Solo parámetros no sensibles en config/local/azure_sql.json, ignorado por Git. Contraseñas: nunca se guardan en archivos.")
    with st.form("azure_form"):
        server = st.text_input("Servidor Azure SQL", saved.get("server", ""),
                               placeholder="mi-servidor.database.windows.net")
        database = st.text_input("Base de datos", saved.get("database", ""))
        port = st.number_input("Puerto TCP", min_value=1, max_value=65535,
                               value=int(saved.get("port", 1433)), step=1)
        method = st.selectbox("Autenticación", ["SQL Password", "Microsoft Entra Interactive"],
                              index=0 if saved.get("authentication", "SQL Password") == "SQL Password" else 1)
        user = st.text_input("Usuario SQL o identidad Microsoft Entra", saved.get("username", ""))
        password = (st.text_input("Contraseña SQL (no persistente)", type="password")
                    if method == "SQL Password" else "")
        st.caption("ODBC 18 · Encrypt=yes · TrustServerCertificate=no · tiempo de espera 8 s.")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Guardar parámetros no sensibles")
        test = c2.form_submit_button("Probar conexión (solo SELECT)")
    cfg = {"server": server, "database": database, "port": int(port),
           "username": user, "authentication": method}
    if save:
        try:
            path = save_settings(ROOT, cfg)
            st.success("Configuración local guardada: " + str(path.relative_to(ROOT)))
        except Exception as exc:
            st.error("Parámetros inválidos: " + str(exc))
    if test:
        try:
            result = test_connection(cfg, password)
            st.success(f"Conexión confirmada a {result['database']} como {result['identity']} (solo SELECT).")
        except Exception:
            # Do not show driver's raw errors; some contain connection strings or identifiers.
            st.error("Conexión no confirmada. Comprueba servidor, base, usuario, contraseña, firewall, permisos de lectura y ODBC Driver 18.")
    st.divider()
    st.write("**Migración:** pendiente de definición del esquema, control de carga incremental y pruebas de recuperación.")

elif section == "Trazabilidad e histórico":
    st.header("Trazabilidad y snapshots")
    if not rows.empty:
        st.write("**Master consultado:**", str(selected))
        st.write("**Fechas presentes en la captura:**", ", ".join(capture_dates(rows)))
        st.warning("Mayorista Duero: PUBLIC_ONLINE_CATALOGUE_NO_POSTAL_PRICE_CERTIFICATION; no se atribuyen precios/stock a un CP.")
        st.subheader("Origen por fase, retailer y categoría")
        table(cases[["fase", "retailer", "category_id", "products_reported", "status",
                     "source_status", "integration_scope"]], 450)
        st.download_button("Exportar linaje de casos", csv_bytes(cases),
                           "linaje_categorias.csv", "text/csv")
    st.subheader("Snapshots del concentrado")
    snapshots = pd.DataFrame([
        {"archivo": p.name, "tamaño_MB": round(p.stat().st_size / (1024**2), 2),
         "modificado": pd.Timestamp(p.stat().st_mtime, unit="s").isoformat(timespec="seconds")}
        for p in masters
    ])
    if snapshots.empty:
        st.info("No hay snapshots fechados.")
    else:
        table(snapshots, 230)
    st.subheader("Checkpoints y respaldos")
    checkpoints = checkpoint_files(ROOT)
    if checkpoints.empty:
        st.info("No hay archivos de checkpoint en esta instalación.")
    else:
        table(checkpoints, 350)
    st.caption("La interfaz nunca modifica masters, punteros, checkpoints ni hojas Control.")
