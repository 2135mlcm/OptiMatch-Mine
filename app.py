import hashlib
import sqlite3
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ==============================================================================
# 1. CONFIGURACIÓN DE PÁGINA Y BASE DE DATOS (optimatch.db)
# ==============================================================================
st.set_page_config(
    page_title="OptiMatch-Mine | Prescripción Pre-Turno",
    page_icon="⛏️",
    layout="wide",
)


def init_db():
    conn = sqlite3.connect("optimatch.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS usuarios 
                 (username TEXT PRIMARY KEY, password TEXT, rol TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS asignaciones_log 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                  frente TEXT, n_palas INT, n_camiones INT, match_factor REAL, opex_usd_ton REAL, estado TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS fin_turno_log 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, inicio TEXT, fin TEXT,
                  ton_mineral REAL, ton_esteril REAL, gasto_carguio REAL, gasto_transporte REAL,
                  costo_mina_unit REAL, presupuesto REAL, cumple TEXT)"""
    )
    c.execute(
        "INSERT OR IGNORE INTO usuarios VALUES (?, ?, ?)",
        (
            "jefe_mina",
            hashlib.sha256("admin123".encode()).hexdigest(),
            "Jefe de Turno",
        ),
    )
    conn.commit()
    conn.close()


init_db()

# ==============================================================================
# 2. MOTOR ALGORÍTMICO (DSM / MATCH FACTOR)
# ==============================================================================


def calcular_match_factor(
    n_palas,
    n_camiones,
    t_pase,
    n_pases,
    t_transito,
    t_maniobras,
    cap_tolva,
    costo_diesel_l_h,
    costo_operativo_base,
):
    t_carguio = n_pases * t_pase  # min
    t_ciclo_camion = t_carguio + t_transito + t_maniobras  # min

    # Match Factor
    mf = (n_camiones * t_carguio) / (n_palas * t_ciclo_camion)

    # Tasa Efectiva (Ton/h)
    cap_carguio_h = (n_palas * (60 / t_carguio)) * cap_tolva
    cap_transporte_h = (n_camiones * (60 / t_ciclo_camion)) * cap_tolva
    tasa_efectiva = min(cap_carguio_h, cap_transporte_h)

    # Evaluación Lean Mining (Semaforización +/-8%)
    if 0.92 <= mf <= 1.08:
        estado_lean = "VERDE"
        color_hex = "#28a745"
    elif (0.85 <= mf < 0.92) or (1.08 < mf <= 1.15):
        estado_lean = "AMARILLO"
        color_hex = "#ffc107"
    else:
        estado_lean = "ROJO"
        color_hex = "#dc3545"

    # Estimación de OPEX (USD/Ton)
    factor_ralenti = 1.0 + (mf - 1.08) * 0.4 if mf > 1.08 else 1.0
    costo_total_h = (
        n_camiones * costo_diesel_l_h * factor_ralenti
    ) + costo_operativo_base
    opex_usd_ton = costo_total_h / tasa_efectiva if tasa_efectiva > 0 else 0

    return (
        mf,
        tasa_efectiva,
        opex_usd_ton,
        estado_lean,
        color_hex,
        t_carguio,
        t_ciclo_camion,
    )


# ==============================================================================
# 3. BARRA LATERAL (PARAMETRIZACIÓN DEL CIRCUITO)
# ==============================================================================
st.title("⛏️ OptiMatch-Mine | Plataforma Prescriptiva de Pre-Turno")
st.markdown(
    "**Optimizador de Flota Carguío-Transporte para Mediana Minería** (Turno 10 Horas - Ley N° 21.561)"
)

st.sidebar.header("⚙️ Parámetros del Circuito")
frente = st.sidebar.text_input("Frente de Carguío", "Frente Norte - Chancado")
n_palas = st.sidebar.number_input("Número de Palas (N_palas)", 1, 5, 1)
n_camiones = st.sidebar.number_input("Número de Camiones (N_camiones)", 1, 20, 8)

st.sidebar.subheader("Tiempos de Ciclo (Matriz DSM)")
t_pase = st.sidebar.number_input("Tiempo por Pase (min)", 0.1, 2.0, 0.55)
n_pases = st.sidebar.number_input("Número de Pases", 1, 10, 4)
t_transito = st.sidebar.number_input("Tránsito Acarreo+Retorno (min)", 1.0, 60.0, 14.2)
t_maniobras = st.sidebar.number_input("Maniobras y Volteo (min)", 0.5, 10.0, 2.3)
cap_tolva = st.sidebar.number_input("Capacidad Tolva (Ton)", 10.0, 200.0, 44.6)

# Cálculo de variables principales
(
    mf,
    tasa,
    opex,
    estado,
    color,
    t_carguio,
    t_ciclo_camion,
) = calcular_match_factor(
    n_palas,
    n_camiones,
    t_pase,
    n_pases,
    t_transito,
    t_maniobras,
    cap_tolva,
    45.0,
    800.0,
)

# ==============================================================================
# 4. MÉTRICAS PRINCIPALES Y SEMÁFORO
# ==============================================================================
col1, col2, col3, col4 = st.columns(4)
col1.metric("Match Factor (MF)", f"{mf:.2f}")
col2.metric("Tasa Efectiva", f"{tasa:.1f} Ton/h")
col3.metric("OPEX Estimado", f"${opex:.2f} USD/Ton")
col4.markdown(
    f"<h3 style='color:{color}; text-align:center;'>Estado: {estado}</h3>",
    unsafe_allow_html=True,
)

# ==============================================================================
# 5. MÓDULO DE SIMULACIÓN CINEMÁTICA DE DOS VÍAS (CON TRIGGER DE INICIO)
# ==============================================================================
st.markdown("---")
st.subheader("🗺️ Módulo de Simulación Cinemática de Dos Vías")
st.markdown(
    "Visualización prescriptiva de flujo de flota (Postura inicial en cola -> Acarreo cargado -> Retorno vacío)."
)

# Estado global del turno en Streamlit
if "turno_iniciado" not in st.session_state:
    st.session_state["turno_iniciado"] = False

col_trig1, col_trig2 = st.columns([2, 1])
with col_trig1:
    if st.button("🚀 CONFIRMAR PRIMER BALDE E INICIAR TURNO (TRIGGER)"):
        st.session_state["turno_iniciado"] = True
        st.success(
            "✅ Primer balde verificado por el Jefe de Turno. Flota en movimiento cinemático."
        )

with col_trig2:
    if st.button("🔄 Reiniciar Postura de Flota"):
        st.session_state["turno_iniciado"] = False

# --- ESCENARIO A: PRE-INICIO (Camiones alineados en fila en el Frente de Carguío) ---
if not st.session_state["turno_iniciado"]:
    st.info(
        "📍 **ESTADO PRE-TURNO:** Flota parqueada en fila de espera en el Frente de Carguío. Presione el botón verde para iniciar la jornada."
    )

    pos_x_inicial = [0.0 - (i * 0.08) for i in range(n_camiones)]
    pos_y_inicial = [0.15] * n_camiones
    etiquetas_iniciales = [
        f"C{i+1}: 0 Ton (En Espera)" for i in range(n_camiones)
    ]

    fig_init = go.Figure()
    fig_init.add_trace(
        go.Scatter(
            x=[0, 3.2],
            y=[0.15, 0.15],
            mode="lines",
            line=dict(color="darkgray", width=4, dash="dash"),
            name="Vía Ida (Cargado)",
        )
    )
    fig_init.add_trace(
        go.Scatter(
            x=[0, 3.2],
            y=[-0.15, -0.15],
            mode="lines",
            line=dict(color="gray", width=4, dash="solid"),
            name="Vía Retorno (Vacío)",
        )
    )

    fig_init.add_trace(
        go.Scatter(
            x=pos_x_inicial,
            y=pos_y_inicial,
            mode="markers+text",
            marker=dict(size=18, color="#ffc107", symbol="square"),
            text=etiquetas_iniciales,
            textposition="top center",
            name="Flota en Fila",
        )
    )

    fig_init.add_trace(
        go.Scatter(
            x=[0],
            y=[0],
            mode="markers+text",
            marker=dict(size=24, color="blue", symbol="diamond"),
            text=["Pala / Frente Carguío"],
            textposition="bottom left",
            name="Pala",
        )
    )
    fig_init.add_trace(
        go.Scatter(
            x=[3.2],
            y=[0],
            mode="markers+text",
            marker=dict(size=24, color="green", symbol="square"),
            text=["Chancador / Botadero"],
            textposition="bottom right",
            name="Destino",
        )
    )

    fig_init.update_layout(
        title="<b>Postura Inicial de Flota en Pre-Turno</b> (Esperando Confirmación de Primer Balde)",
        xaxis=dict(
            title="Distancia en Ruta de Acarreo (km)",
            range=[-1.0, 3.5],
            gridcolor="lightgray",
        ),
        yaxis=dict(range=[-0.5, 0.5], showticklabels=False),
        height=380,
    )

    st.plotly_chart(fig_init, use_container_width=True)

# --- ESCENARIO B: TURNO EN MARCHA (Animación de Dos Vías) ---
else:
    grafico_placeholder = st.empty()

    t_ida_s = (t_transito / 2) * 60
    t_retorno_s = (t_transito / 2) * 60
    t_ciclo_total_s = t_ciclo_camion * 60

    desfase_camiones = np.linspace(
        0, t_ciclo_total_s, n_camiones, endpoint=False
    )

    for t_sim in range(0, 100, 2):
        pos_x, pos_y, etiquetas, colores = [], [], [], []

        for i in range(n_camiones):
            t_relativo = (t_sim * 10 + desfase_camiones[i]) % t_ciclo_total_s

            if t_relativo < t_ida_s:
                x = (t_relativo / t_ida_s) * 3.2
                y = 0.15
                toneladas = cap_tolva
                txt = f"C{i+1}: {toneladas:.1f} Ton"
                color_c = "#28a745"  # Verde (Cargado)
            else:
                x = 3.2 - ((t_relativo - t_ida_s) / t_retorno_s) * 3.2
                x = max(0, x)
                y = -0.15
                toneladas = 0.0
                txt = f"C{i+1}: {toneladas:.0f} Ton"
                color_c = "#dc3545"  # Rojo (Vacío)

            pos_x.append(x)
            pos_y.append(y)
            etiquetas.append(txt)
            colores.append(color_c)

        fig_dyn = go.Figure()

        fig_dyn.add_trace(
            go.Scatter(
                x=[0, 3.2],
                y=[0.15, 0.15],
                mode="lines",
                line=dict(color="darkgray", width=4, dash="dash"),
                name="Vía Ida (Cargado)",
            )
        )
        fig_dyn.add_trace(
            go.Scatter(
                x=[0, 3.2],
                y=[-0.15, -0.15],
                mode="lines",
                line=dict(color="gray", width=4, dash="solid"),
                name="Vía Retorno (Vacío)",
            )
        )

        fig_dyn.add_trace(
            go.Scatter(
                x=pos_x,
                y=pos_y,
                mode="markers+text",
                marker=dict(size=18, color=colores, symbol="square"),
                text=etiquetas,
                textposition="top center",
                name="Flota CAEX",
            )
        )

        fig_dyn.add_trace(
            go.Scatter(
                x=[0],
                y=[0],
                mode="markers+text",
                marker=dict(size=22, color="blue", symbol="diamond"),
                text=["Pala / Frente Carguío"],
                textposition="bottom left",
                name="Pala",
            )
        )
        fig_dyn.add_trace(
            go.Scatter(
                x=[3.2],
                y=[0],
                mode="markers+text",
                marker=dict(size=22, color="green", symbol="square"),
                text=["Chancador / Botadero"],
                textposition="bottom right",
                name="Destino",
            )
        )

        fig_dyn.update_layout(
            title=f"<b>Simulación Dinámica de Dos Vías en Tiempo Real</b> (Tiempo Transcurrido: {t_sim*10} s)",
            xaxis=dict(
                title="Distancia en Ruta de Acarreo (km)",
                range=[-0.5, 3.5],
                gridcolor="lightgray",
            ),
            yaxis=dict(range=[-0.5, 0.5], showticklabels=False),
            height=380,
        )

        grafico_placeholder.plotly_chart(fig_dyn, use_container_width=True)
        time.sleep(0.1)

# Guardar Asignación Prescriptiva
if st.button("💾 Guardar y Validar Asignación de Pre-Turno"):
    conn = sqlite3.connect("optimatch.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO asignaciones_log (frente, n_palas, n_camiones, match_factor, opex_usd_ton, estado) VALUES (?,?,?,?,?,?)",
        (frente, n_palas, n_camiones, mf, opex, estado),
    )
    conn.commit()
    conn.close()
    st.success("Asignación de pre-turno registrada en base de datos optimatch.db")

# ==============================================================================
# 6. MÓDULO DE RECONCILIACIÓN Y FIN DE TURNO (ESTÁNDAR SQM NUEVA VICTORIA)
# ==============================================================================
st.markdown("---")
st.header("📋 Reconciliación de Fin de Turno (Reporte Operacional Mina)")
st.markdown(
    "Módulo de auditoría de costos reales ejecutados vs. presupuesto meta de faena (Inspirado en SQM Nueva Victoria)."
)

col_f1, col_f2 = st.columns(2)
with col_f1:
    fecha_turno = st.date_input("Fecha del Turno", datetime.now())
    inicio_turno = st.time_input(
        "Inicio Turno", datetime.strptime("08:00", "%H:%M").time()
    )
    fin_turno = st.time_input(
        "Fin Turno", datetime.strptime("18:00", "%H:%M").time()
    )

with col_f2:
    ton_mineral = st.number_input(
        "Toneladas Mineral Reales (ton)",
        min_value=0.0,
        value=11430.0,
        step=100.0,
    )
    ton_esteril = st.number_input(
        "Toneladas Estéril Reales (ton)",
        min_value=0.0,
        value=1143.0,
        step=100.0,
    )

ton_total_movida = ton_mineral + ton_esteril

st.subheader("💵 Desagregación de Gastos Operativos Directos")
col_c1, col_c2, col_c3 = st.columns(3)

with col_c1:
    gasto_carguio_usd = st.number_input(
        "Gasto Carguío Real (USD)", min_value=0.0, value=4617.70
    )
    costo_carguio_unit = (
        gasto_carguio_usd / ton_mineral if ton_mineral > 0 else 0
    )
    st.caption(f"Costo Carguío: **USD {costo_carguio_unit:.4f} / ton mineral**")

with col_c2:
    gasto_transporte_usd = st.number_input(
        "Gasto Transporte Real (USD)", min_value=0.0, value=7680.90
    )
    costo_transporte_unit = (
        gasto_transporte_usd / ton_mineral if ton_mineral > 0 else 0
    )
    st.caption(
        f"Costo Transporte: **USD {costo_transporte_unit:.4f} / ton mineral**"
    )

with col_c3:
    gasto_total_mina = gasto_carguio_usd + gasto_transporte_usd
    costo_mina_total = (
        gasto_total_mina / ton_mineral if ton_mineral > 0 else 0
    )
    st.markdown(f"### Gasto Total: **USD {gasto_total_mina:,.2f}**")
    st.markdown(
        f"### COSTO MINA: **USD {costo_mina_total:.4f} / ton mineral**"
    )

st.subheader("🎯 Comparativo Presupuestario Target")
presupuesto_mina_usd_ton = st.number_input(
    "Presupuesto Mina Target (USD/ton)", min_value=0.0, value=1.1500, step=0.01
)

cumple_presupuesto = costo_mina_total <= presupuesto_mina_usd_ton
estado_causa = "CUMPLE" if cumple_presupuesto else "SOBRECOSTO OPERACIONAL"

if cumple_presupuesto:
    st.success(
        f"✅ Cumple Costo Mina: **SI** | Causa Presupuesto: **{estado_causa}**"
    )
else:
    st.error(
        f"❌ Cumple Costo Mina: **NO** | Causa Presupuesto: **{estado_causa}**"
    )

if st.button("💾 Guardar Reporte de Cierre de Turno"):
    conn = sqlite3.connect("optimatch.db")
    c = conn.cursor()
    c.execute(
        """INSERT INTO fin_turno_log 
                 (fecha, inicio, fin, ton_mineral, ton_esteril, gasto_carguio, gasto_transporte, costo_mina_unit, presupuesto, cumple)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            str(fecha_turno),
            str(inicio_turno),
            str(fin_turno),
            ton_mineral,
            ton_esteril,
            gasto_carguio_usd,
            gasto_transporte_usd,
            costo_mina_total,
            presupuesto_mina_usd_ton,
            "SI" if cumple_presupuesto else "NO",
        ),
    )
    conn.commit()
    conn.close()
    st.success(
        "Reporte de Fin de Turno guardado exitosamente en la base de datos optimatch.db"
    )
