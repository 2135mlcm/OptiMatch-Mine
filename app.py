import hashlib
import io
import sqlite3
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA Y TEMA VISUAL
# ==========================================
st.set_page_config(
    page_title="OptiMatch-Mine",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilos CSS industriales y Sidebar oscuro (#1e293b)
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        background-color: #1e293b;
        color: #ffffff;
    }
    [data-testid="stSidebar"] * {
        color: #ffffff !important;
    }
    .main {
        background-color: #f1f5f9;
    }
    .stMetric {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    </style>
""",
    unsafe_allow_html=True,
)


# ==========================================
# 2. CONTROL DE ACCESO (LOGIN) Y BASE DE DATOS
# ==========================================
def init_db():
    conn = sqlite3.connect("optimatch.db")
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS usuarios (username TEXT PRIMARY KEY, password TEXT, rol TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS asignaciones_log (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, frente TEXT, n_palas INT, n_camiones INT, match_factor REAL, opex_usd_ton REAL, estado TEXT)"
    )

    # Usuarios autorizados
    pass_hash_admin = hashlib.sha256("admin123".encode()).hexdigest()
    pass_hash_mina = hashlib.sha256("mina2026".encode()).hexdigest()

    c.execute(
        "INSERT OR IGNORE INTO usuarios VALUES ('jefe_mina', ?, 'Jefe de Turno')",
        (pass_hash_admin,),
    )
    c.execute(
        "INSERT OR IGNORE INTO usuarios VALUES ('avidela', ?, 'Jefe de Turno')",
        (pass_hash_mina,),
    )
    c.execute(
        "INSERT OR IGNORE INTO usuarios VALUES ('ddaines', ?, 'Jefe de Turno')",
        (pass_hash_mina,),
    )

    conn.commit()
    conn.close()


init_db()

# Manejo de estado de sesión
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("⛏️ OptiMatch-Mine | Iniciar Sesión")
    st.markdown(
        "Plataforma Prescriptiva de Pre-Turno - Agendamiento Operacional de Flota"
    )

    col1, col2 = st.columns([1, 2])
    with col1:
        user = st.text_input("Usuario")
        pwd = st.text_input("Contraseña", type="password")
        if st.button("Ingresar al Sistema"):
            conn = sqlite3.connect("optimatch.db")
            c = conn.cursor()
            c.execute(
                "SELECT * FROM usuarios WHERE username=? AND password=?",
                (user, hashlib.sha256(pwd.encode()).hexdigest()),
            )
            res = c.fetchone()
            conn.close()

            # Validación con BD o accesos directos
            if (
                res
                or (user == "jefe_mina" and pwd == "admin123")
                or (user in ["avidela", "ddaines"] and pwd == "mina2026")
            ):
                st.session_state["logged_in"] = True
                st.session_state["user"] = user
                st.rerun()
            else:
                st.error("Credenciales incorrectas")
    st.stop()

# ==========================================
# 3. INTERFAZ PRINCIPAL Y BARRA LATERAL
# ==========================================
with st.sidebar:
    st.title("OptiMatch Mine")
    st.caption("Optimization & Matching For Mining")
    st.write(f"👤 **Usuario:** {st.session_state.get('user', 'jefe_mina')}")
    if st.button("Cerrar Sesión"):
        st.session_state["logged_in"] = False
        st.rerun()

    st.markdown("---")
    st.header("⚙️ Parámetros de Pre-Turno")
    frente = st.selectbox(
        "Frente de Carguío",
        ["Frente Norte - Chancado", "Frente Sur - Botadero", "Stockpile"],
    )

    n_palas = st.number_input("Palas Activas", 1, 5, 1)
    n_camiones = st.number_input("Camiones CAEX", 1, 20, 8)

    st.subheader("Matriz DSM / Tiempos")
    t_pase = st.number_input("Tiempo Pase (min)", 0.1, 2.0, 0.55)
    n_pases = st.number_input("N° Pases", 1, 10, 4)
    t_transito = st.number_input("Acarreo+Retorno (min)", 1.0, 60.0, 14.2)
    t_maniobras = st.number_input("Maniobras (min)", 0.5, 10.0, 2.3)
    cap_tolva = st.number_input("Capacidad Tolva (Ton)", 10.0, 200.0, 44.6)

# ==========================================
# 4. MOTOR ALGORÍTMICO Y MÉTRICAS
# ==========================================
t_carguio = n_pases * t_pase
t_ciclo_camion = t_carguio + t_transito + t_maniobras
mf = (n_camiones * t_carguio) / (n_palas * t_ciclo_camion)

cap_carguio_h = (n_palas * (60 / t_carguio)) * cap_tolva
cap_transporte_h = (n_camiones * (60 / t_ciclo_camion)) * cap_tolva
tasa_efectiva = min(cap_carguio_h, cap_transporte_h)

# Semaforización Lean Mining
if 0.92 <= mf <= 1.08:
    estado = "VERDE"
    color = "#28a745"
elif (0.85 <= mf < 0.92) or (1.08 < mf <= 1.15):
    estado = "AMARILLO"
    color = "#ffc107"
else:
    estado = "ROJO"
    color = "#dc3545"

factor_ralenti = 1.0 + (mf - 1.08) * 0.4 if mf > 1.08 else 1.0
opex_usd_ton = ((n_camiones * 45.0 * 1.10 * factor_ralenti) + 800.0) / (
    tasa_efectiva if tasa_efectiva > 0 else 1
)

st.title("⛏️ OptiMatch-Mine | Panel Prescriptivo de Pre-Turno")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Match Factor (MF)", f"{mf:.2f}")
m2.metric("Tasa Efectiva", f"{tasa_efectiva:,.1f} Ton/h")
m3.metric("OPEX Estimado", f"${opex_usd_ton:.2f} USD/Ton")
m4.markdown(
    f"<h3 style='color:{color}; text-align:center;'>Estado: {estado}</h3>",
    unsafe_allow_html=True,
)

# ==========================================
# 5. REPORTE CSV Y AUDITORÍA
# ==========================================
st.markdown("---")
c_act1, c_act2 = st.columns(2)

with c_act1:
    if st.button("💾 Guardar Asignación en Base de Datos"):
        conn = sqlite3.connect("optimatch.db")
        c = conn.cursor()
        c.execute(
            "INSERT INTO asignaciones_log (frente, n_palas, n_camiones, match_factor, opex_usd_ton, estado) VALUES (?,?,?,?,?,?)",
            (frente, n_palas, n_camiones, mf, opex_usd_ton, estado),
        )
        conn.commit()
        conn.close()
        st.success("Asignación guardada con éxito en optimatch.db")

with c_act2:
    df_export = pd.DataFrame(
        [
            {
                "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Frente": frente,
                "Palas": n_palas,
                "Camiones": n_camiones,
                "Match_Factor": mf,
                "Tasa_Ton_h": tasa_efectiva,
                "OPEX_USD_Ton": opex_usd_ton,
                "Estado": estado,
            }
        ]
    )
    csv_bytes = df_export.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Exportar Plan de Pre-Turno (CSV)",
        data=csv_bytes,
        file_name=f"plan_preturno_{frente}.csv",
        mime="text/csv",
    )

st.subheader("📋 Historial de Registros Guardados")
conn = sqlite3.connect("optimatch.db")
df_hist = pd.read_sql_query("SELECT * FROM asignaciones_log", conn)
conn.close()
st.dataframe(df_hist, use_container_width=True)
