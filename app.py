# ==============================================================================
# 1. IMPORTS Y LIBRERÍAS ESTÁNDAR / TERCEROS (PEP 8)
# ==============================================================================
import base64
from datetime import datetime
import json
import os
import random
import sqlite3
import threading
import time
import urllib.request

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

# ==============================================================================
# 2. CONFIGURACIÓN DE PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="OptiMatch Mine v3.0 — Control Prescriptivo",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# 3. MÓDULO KEEP-ALIVE: MANTIENE EL SERVIDOR ACTIVO 24/7
# ==============================================================================
def keep_alive_ping():
    while True:
        time.sleep(900)
        _ = datetime.now()

if "keep_alive_started" not in st.session_state:
    st.session_state.keep_alive_started = True
    thread = threading.Thread(target=keep_alive_ping, daemon=True)
    thread.start()

# ==============================================================================
# 4. MATRICES TÉCNICAS: MATERIAL, MERMA POR TRASLADO Y PERFIL DE RAMPAS
# ==============================================================================
FACTORES_MATERIAL = {
    "Roca Gruesa (80%)": {"fl": 0.80, "merma_base_pct": 2.5},
    "Estándar (88%)":    {"fl": 0.88, "merma_base_pct": 1.2},
    "Fino / Seco (92%)": {"fl": 0.92, "merma_base_pct": 3.0},
}

PERFIL_RAMPAS = {
    "Plano / Pista Recta (0% - 3%)":        {"vel_adj": 1.00, "f_consumo": 1.00, "f_merma": 1.0},
    "Rampa Moderada / Curvas (4% - 7%)":    {"vel_adj": 0.85, "f_consumo": 1.25, "f_merma": 1.4},
    "Rampa Severa / Explotación Pit (>8%)": {"vel_adj": 0.70, "f_consumo": 1.55, "f_merma": 1.8},
}

# ==============================================================================
# 5. INICIALIZACIÓN Y MIGRACIÓN AUTOMÁTICA DE LA BD (optimatch.db)
# ==============================================================================
DB_FILE = "optimatch.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nombre_completo TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    """)

    c.execute("DELETE FROM usuarios")

    usuarios_oficiales = [
        ("mcepeda", "admin2026", "Mauricio L. Cepeda Mondaca", "Administrador"),
        ("avidela", "mina2026", "Andy Videla Obregón", "Supervisor Mina"),
        ("ddaines", "mina2026", "Daniel Daines Araya", "Supervisor Mina"),
        ("cnikulin", "uah2026", "Dr. Christopher Nikulin", "Gerente Operaciones / Evaluador"),
        ("cperez", "uah2026", "Dr. Camilo Pérez", "Gerente Operaciones / Evaluador"),
    ]
    c.executemany(
        "INSERT INTO usuarios (username, password, nombre_completo, rol) VALUES (?, ?, ?, ?)",
        usuarios_oficiales,
    )
    conn.commit()

    c.execute("""
        CREATE TABLE IF NOT EXISTS historico_agendamientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            num_agendamiento TEXT,
            fecha_registro TEXT,
            hora_registro TEXT,
            faena TEXT,
            turno TEXT,
            regimen_guardia TEXT,
            jefe_turno TEXT,
            ton_movidas REAL,
            ton_efectivas REAL,
            merma_ton REAL,
            consumo_diesel_lts REAL,
            costo_diesel_usd REAL,
            opex_total_usd REAL,
            costo_ton_usd REAL,
            beneficio_neto_usd REAL,
            match_factor REAL,
            disponibilidad_fisica REAL,
            factor_llenado REAL,
            prescripcion_aceptada INTEGER
        )
    """)
    conn.commit()

    c.execute("PRAGMA table_info(historico_agendamientos)")
    columnas = [column[1] for column in c.fetchall()]
    if "regimen_guardia" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN regimen_guardia TEXT")
        conn.commit()
    if "prescripcion_aceptada" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN prescripcion_aceptada INTEGER DEFAULT 1")
        conn.commit()
    if "disponibilidad_fisica" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN disponibilidad_fisica REAL DEFAULT 100.0")
        conn.commit()
    if "factor_llenado" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN factor_llenado REAL DEFAULT 0.88")
        conn.commit()
    if "ton_efectivas" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN ton_efectivas REAL DEFAULT 0.0")
        conn.commit()
    if "merma_ton" not in columnas:
        c.execute("ALTER TABLE historico_agendamientos ADD COLUMN merma_ton REAL DEFAULT 0.0")
        conn.commit()

    conn.close()

init_db()

# ==============================================================================
# 6. MOTOR DE SIMULACIÓN ANALÍTICA ESTOCÁSTICA MULTIMODELO (MF UNIFICADO)
# ==============================================================================
def ejecutar_simulacion_analitica(
    caex_activos_df,
    n_palas=1,
    cv=0.3,
    duracion_horas=10.0,
    costo_pala_h=441.0,
    costo_camion_h=290.0,
    fl_factor=0.88,
    merma_base_pct=1.2,
    perfil_rampa_key="Plano / Pista Recta (0% - 3%)",
    distancia_km=3.2,
    vel_cargado_base=18.0,
    vel_vacio_base=30.0,
    seed=42,
):
    n_camiones = len(caex_activos_df)
    t_carguio_medio = 2.20
    t_maniobras_medio = 2.30

    rampa_info = PERFIL_RAMPAS.get(perfil_rampa_key, PERFIL_RAMPAS["Plano / Pista Recta (0% - 3%)"])
    vel_cargado_efectiva = vel_cargado_base * rampa_info["vel_adj"]
    vel_vacio_efectiva = vel_vacio_base

    t_ida = ((distancia_km / vel_cargado_efectiva) * 60.0) if vel_cargado_efectiva > 0 else 10.67
    t_retorno = ((distancia_km / vel_vacio_efectiva) * 60.0) if vel_vacio_efectiva > 0 else 6.40
    t_transito_medio = t_ida + t_retorno

    if n_camiones > 0 and "Cap_Ton" in caex_activos_df.columns:
        cap_tolva_nominal = caex_activos_df["Cap_Ton"].mean()
    else:
        cap_tolva_nominal = 90.0

    cap_tolva_efectiva = cap_tolva_nominal * fl_factor

    t_ciclo_base = t_carguio_medio + t_transito_medio + t_maniobras_medio
    
    # FÓRMULA ESTÁNDAR EXACTA DE MATCH FACTOR
    mf = (n_camiones * t_carguio_medio) / (n_palas * t_ciclo_base) if (n_palas * t_ciclo_base) > 0 else 0.0

    if mf <= 0.94:
        espera_promedio_cola = 2.0 * (mf / 0.94) if mf > 0 else 0.0
    else:
        espera_promedio_cola = 2.0 + 8.5 * ((mf - 0.94) ** 1.3)

    t_ciclo_efectivo = t_ciclo_base + espera_promedio_cola
    vueltas_turno = (duracion_horas * 60.0) / t_ciclo_efectivo if t_ciclo_efectivo > 0 else 0.0
    
    toneladas_cargadas = n_camiones * vueltas_turno * cap_tolva_efectiva

    merma_efectiva_pct = merma_base_pct * rampa_info["f_merma"]
    toneladas_merma = toneladas_cargadas * (merma_efectiva_pct / 100.0)
    toneladas_efectivas = toneladas_cargadas - toneladas_merma

    horas_transito = (vueltas_turno * t_ciclo_base) / 60.0
    horas_ralenti = (vueltas_turno * espera_promedio_cola) / 60.0
    litros_totales = ((n_camiones * horas_transito * 45.0) + (n_camiones * horas_ralenti * 28.0)) * rampa_info["f_consumo"]
    
    consumo_especifico = (litros_totales / toneladas_efectivas) if toneladas_efectivas > 0 else 0.0

    costo_flota = n_camiones * costo_camion_h * duracion_horas
    costo_pala_tot = n_palas * costo_pala_h * duracion_horas
    costo_opex = costo_flota + costo_pala_tot
    costo_unitario = (costo_opex / toneladas_efectivas) if toneladas_efectivas > 0 else 0.0

    return {
        "MF": mf,
        "cola_min": espera_promedio_cola,
        "t_ciclo_min": t_ciclo_efectivo,
        "t_ida_min": t_ida,
        "t_retorno_min": t_retorno,
        "vel_cargado_efectiva": vel_cargado_efectiva,
        "ton_cargadas": toneladas_cargadas,
        "ton_merma": toneladas_merma,
        "merma_pct": merma_efectiva_pct,
        "ton_totales": toneladas_efectivas,
        "litros_totales": litros_totales,
        "consumo_especifico_l_ton": consumo_especifico,
        "costo_opex": costo_opex,
        "costo_unitario_usd_ton": costo_unitario,
        "t_ciclo_base": t_ciclo_base
    }

# ==============================================================================
# 7. FUNCIONES AUXILIARES Y API EN VIVO
# ==============================================================================
@st.cache_data(ttl=3600)
def obtener_indicadores_mercado():
    try:
        url = "https://mindicador.cl/api"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            usd_clp = data["dolar"]["valor"]
            diesel_industrial_usd = round(1080.0 / usd_clp, 2)
            return usd_clp, diesel_industrial_usd
    except Exception:
        return 940.0, 1.15

def obtener_siguiente_agendamiento():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM historico_agendamientos")
    total = c.fetchone()[0]
    conn.close()
    siguiente_num = total + 1
    anio_actual = datetime.now().year
    return f"AGN-{anio_actual}-{siguiente_num:03d}"

def validar_usuario(usr, pwd):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, nombre_completo, rol FROM usuarios WHERE username = ? AND password = ?", (usr, pwd))
    res = c.fetchone()
    conn.close()
    return res

def guardar_agendamiento_db(num_ag, fecha, hora, faena, turno, regimen, jefe, ton_cargadas, ton_efectivas, merma_ton, lts_diesel, costo_diesel, opex, costo_ton, beneficio, mf, df_val, fl_val, prescripcion_aceptada=1):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO historico_agendamientos (
            num_agendamiento, fecha_registro, hora_registro, faena, turno, regimen_guardia, jefe_turno,
            ton_movidas, ton_efectivas, merma_ton, consumo_diesel_lts, costo_diesel_usd, opex_total_usd,
            costo_ton_usd, beneficio_neto_usd, match_factor, disponibilidad_fisica, factor_llenado, prescripcion_aceptada
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (num_ag, fecha, hora, faena, turno, regimen, jefe, ton_cargadas, ton_efectivas, merma_ton, lts_diesel, costo_diesel, opex, costo_ton, beneficio, mf, df_val, fl_val, prescripcion_aceptada))
    conn.commit()
    conn.close()

def borrar_historico_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM historico_agendamientos")
    conn.commit()
    conn.close()

def fmt_num(val, dec=0):
    if dec == 0:
        return f"{val:,.0f}".replace(",", ".")
    else:
        formatted = f"{val:,.{dec}f}"
        main_part, dec_part = formatted.split(".")
        main_part = main_part.replace(",", ".")
        return f"{main_part},{dec_part}"

def obtener_base64_img(nombre_archivo):
    posibles_rutas = [
        nombre_archivo,
        os.path.join(os.getcwd(), nombre_archivo),
        os.path.join(os.path.dirname(__file__), nombre_archivo) if '__file__' in globals() else nombre_archivo,
        os.path.join(os.getcwd(), "static", nombre_archivo),
    ]
    for r in posibles_rutas:
        if os.path.exists(r):
            try:
                with open(r, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode()
                    ext = r.split(".")[-1].lower()
                    mime = "png" if ext in ["png", "gif"] else "jpeg"
                    return f"data:image/{mime};base64,{encoded}"
            except Exception:
                pass
    return None

# ==============================================================================
# 8. ESTILOS VISUALES MEJORADOS Y COMPACTACIÓN DE ESPACIOS (SIDEBAR + DERECHA)
# ==============================================================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1.5rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }
    
    .stApp { 
        background-color: #F1F5F9 !important; 
        color: #0F172A !important; 
    }
    
    section[data-testid="stSidebar"] { 
        background-color: #0F172A !important; 
        border-right: 2px solid #F59E0B !important; 
        box-shadow: 4px 0px 15px rgba(0,0,0,0.15) !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
    }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3, 
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span, section[data-testid="stSidebar"] p {
        color: #F8FAFC !important; font-weight: 700 !important;
        margin-top: 2px !important; margin-bottom: 2px !important;
    }
    section[data-testid="stSidebar"] hr {
        margin-top: 8px !important;
        margin-bottom: 8px !important;
        border-color: #334155 !important;
    }
    section[data-testid="stSidebar"] input {
        background-color: #1E293B !important; color: #FFFFFF !important; border: 1px solid #334155 !important;
        border-radius: 8px !important; text-align: center !important; font-weight: bold !important;
        padding: 4px 8px !important;
    }
    section[data-testid="stSidebar"] input:focus {
        border-color: #F59E0B !important;
    }
    
    .dark-card {
        background-color: #1E293B; border: 1px solid #334155; border-radius: 12px; padding: 10px 14px !important;
        margin-bottom: 8px !important; box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.1);
    }
    .amber-card {
        background-color: #1E293B; border: 2px solid #F59E0B; border-radius: 12px; padding: 8px 12px !important;
        margin-bottom: 8px !important; box-shadow: 0px 0px 10px rgba(245, 158, 11, 0.25);
    }
    
    div[data-baseweb="select"], div[data-baseweb="select"] *, div[data-baseweb="select"] > div,
    div[data-baseweb="select"] div[role="button"] {
        background-color: #1E293B !important; color: #FFFFFF !important; border-color: #F59E0B !important;
    }
    div[data-baseweb="select"] > div { border: 1px solid #F59E0B !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span, div[data-baseweb="select"] p, div[data-baseweb="select"] div {
        color: #FFFFFF !important; font-weight: 800 !important; font-size: 13px !important;
    }
    div[data-baseweb="select"] svg { fill: #F59E0B !important; color: #F59E0B !important; }
    ul[data-baseweb="menu"], div[data-baseweb="popover"] > div, div[data-baseweb="popover"] * {
        background-color: #0F172A !important; color: #FFFFFF !important;
    }
    li[data-baseweb="option"]:hover, li[data-baseweb="option"]:hover * {
        background-color: #F59E0B !important; color: #000000 !important; font-weight: 900 !important;
    }
    
    .selector-label-centered {
        color: #F59E0B !important; font-size: 11px !important; font-weight: 900 !important;
        text-align: center !important; display: block !important; margin-bottom: 2px !important; text-transform: uppercase; letter-spacing: 0.5px;
    }
    .auto-box {
        background-color: #1E293B; border: 1px solid #F59E0B; border-radius: 8px; padding: 4px 8px;
        text-align: center; font-size: 13px; font-weight: 800; color: #FFFFFF !important; margin-bottom: 4px;
    }
    
    section[data-testid="stSidebar"] button, section[data-testid="stSidebar"] button *,
    section[data-testid="stSidebar"] button p, section[data-testid="stSidebar"] button span {
        background-color: #F59E0B !important; color: #000000 !important; -webkit-text-fill-color: #000000 !important;
        font-weight: 900 !important; font-size: 13px !important; border-radius: 8px !important;
    }
    
    .main-title-card {
        background: #FFFFFF; padding: 14px 22px; border-radius: 14px; border: 1px solid #E2E8F0;
        box-shadow: 0px 8px 20px -5px rgba(0, 0, 0, 0.05); text-align: center; width: 100%; margin: 0px auto 12px auto;
        position: relative; overflow: hidden;
    }
    .main-title-card::before {
        content: ""; position: absolute; top: 0; left: 0; right: 0; height: 4px;
        background: linear-gradient(90deg, #F59E0B 0%, #0284C7 50%, #10B981 100%);
    }
    
    div[data-testid="stMetric"] {
        background-color: #FFFFFF !important; border: 1px solid #E2E8F0 !important;
        border-radius: 10px !important; padding: 10px 12px !important;
        box-shadow: 0px 2px 8px rgba(0, 0, 0, 0.03) !important; text-align: center !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0px 6px 14px rgba(0, 0, 0, 0.06) !important;
        border-color: #CBD5E1 !important;
    }
    div[data-testid="stMetricLabel"] p { color: #64748B !important; font-weight: 800 !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.5px; }
    div[data-testid="stMetricValue"] div { color: #0F172A !important; font-size: 18px !important; font-weight: 900 !important; white-space: nowrap !important; }

    .mf-label { font-size: 16px !important; font-weight: 800 !important; color: #0F172A !important; margin-bottom: 2px !important; }
    .mf-value { font-size: 34px !important; font-weight: 900 !important; color: #0284C7 !important; margin-top: 0px !important; }
    .highlight-red-large { color: #DC2626 !important; font-size: 14px !important; font-weight: 800 !important; margin-bottom: 4px !important; }
    .adh-green-large { color: #10B981 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }
    .adh-red-large { color: #EF4444 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }
    
    div[data-testid="stDataFrame"] { background-color: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 10px; box-shadow: 0px 2px 6px rgba(0,0,0,0.02); }
    
    div.stButton > button[kind="primary"] {
        background-color: #EF4444 !important; color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important;
        border: none !important; outline: none !important; font-weight: 900 !important;
        font-size: 13px !important; border-radius: 8px !important; height: 38px !important; padding: 0px 14px !important;
        box-shadow: 0px 4px 10px rgba(239, 68, 68, 0.25) !important;
    }
    div.stButton > button[kind="primary"] p, div.stButton > button[kind="primary"] span {
        color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important; font-weight: 900 !important;
    }
    div.stButton > button[kind="primary"]:hover { background-color: #DC2626 !important; }

    hr { margin-top: 12px !important; margin-bottom: 12px !important; }
    </style>
""", unsafe_allow_html=True)

LOGO_PATH = "Logo_OptiMatch.png"
LOGO_ATACAMA_PATH = "Logo_Atacama_Norte.png"

# ==============================================================================
# 9. SISTEMA DE AUTENTICACIÓN PRIVADO CON DISEÑO TAILWIND
# ==============================================================================
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        st.markdown("<br>", unsafe_allow_html=True)
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=320)
        else:
            st.markdown("""
                <div style="text-align: center; background-color: #0F172A; padding: 25px; border-radius: 16px; border: 2px solid #F59E0B; box-shadow: 0px 10px 25px rgba(0,0,0,0.2);">
                    <h1 style="color: #F59E0B; font-size: 34px; margin-bottom: 0px; font-weight: 900;">⛏️ OptiMatch Mine</h1>
                    <h3 style="color: #F8FAFC; margin-top: 5px; font-size: 16px; font-weight: 600;">Control Prescriptivo Pre-Turno</h3>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("<p style='text-align: center; font-weight: 700; font-size: 13px; color: #64748B; margin-top: 10px;'>Acceso Restringido por Perfil | Universidad Alberto Hurtado</p>", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        with st.form("login_form_secure", clear_on_submit=True):
            st.markdown('<p style="font-weight: 800; font-size: 13px; color: #0F172A;">Usuario / Responsable:</p>', unsafe_allow_html=True)
            usuario = st.text_input("", value="", placeholder="Ingrese usuario...", key="usr_field_clean", autocomplete="off")

            st.markdown('<p style="font-weight: 800; font-size: 13px; color: #0F172A;">Contraseña de Acceso:</p>', unsafe_allow_html=True)
            clave = st.text_input("", type="password", value="", placeholder="Ingrese contraseña...", key="pwd_field_clean", autocomplete="new-password")

            st.markdown("<br>", unsafe_allow_html=True)
            btn_ingresar = st.form_submit_button("🔑 INGRESAR A LA PLATAFORMA", use_container_width=True)

            if btn_ingresar:
                datos_val = validar_usuario(usuario.strip(), clave.strip())
                if datos_val:
                    st.session_state.autenticado = True
                    st.session_state.user_id = datos_val[0]
                    st.session_state.usuario_activo = datos_val[1]
                    st.session_state.rol_activo = datos_val[2]
                    st.session_state.hora_ingreso = datetime.now()
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña no registrados en el sistema.")
    st.stop()

# ==============================================================================
# 10. CARÁTULA Y BARRA LATERAL (SIDEBAR) REORGANIZADA Y SIN ETIQUETAS SOBRANTES
# ==============================================================================
if os.path.exists(LOGO_PATH):
    c_hdr1, c_hdr2, c_hdr3 = st.columns([1, 1.2, 1])
    with c_hdr2:
        st.image(LOGO_PATH, use_container_width=True)

st.markdown("""
    <div class="main-title-card">
        <h1 style="color: #0F172A; margin: 0; font-size: 24px; font-weight: 900; tracking-tight;">OptiMatch Mine — Control Prescriptivo v3.0</h1>
        <p style="color: #0284C7; margin: 2px 0 0 0; font-size: 12px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">
            SISTEMA DE SOPORTE A LA DECISIÓN PRE-TURNO PARA LA MEDIANA MINERÍA
        </p>
        <p style="color: #64748B; margin: 2px 0 0 0; font-size: 11px; font-weight: 600;">
            Optimización del Match Carguío-Transporte & Control de Rentabilidad OPEX | Universidad Alberto Hurtado
        </p>
    </div>
""", unsafe_allow_html=True)

st.sidebar.header("Registro Operativo Mina")

st.sidebar.markdown('<span class="selector-label-centered">Nombre de la Mina / Faena</span>', unsafe_allow_html=True)
nombre_mina = st.sidebar.text_input("", value="Mina Atacama Norte", key="input_nombre_mina_side", label_visibility="collapsed")

num_agendamiento_auto = obtener_siguiente_agendamiento()

st.sidebar.markdown('<span class="selector-label-centered">N° de Agendamiento Correlativo</span>', unsafe_allow_html=True)
num_agendamiento = st.sidebar.text_input("", value=num_agendamiento_auto, key="input_num_ag_side", label_visibility="collapsed")

st.sidebar.markdown("---")

now_dt = datetime.now()
fecha_str = now_dt.strftime("%d/%m/%Y")
dias_semana_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
nombre_dia_actual = dias_semana_es[now_dt.weekday()]

st.sidebar.markdown("<label style='font-size:11px; font-weight:700;'>Fecha de Agendamiento</label>", unsafe_allow_html=True)
st.sidebar.markdown(f'<div class="auto-box">{nombre_dia_actual}, {fecha_str}</div>', unsafe_allow_html=True)
st.sidebar.markdown("<label style='font-size:11px; font-weight:700;'>Hora de Agendamiento</label>", unsafe_allow_html=True)

with st.sidebar:
    components.html("""
        <div id="reloj_vivo" style="background-color: #1E293B; border: 1px solid #F59E0B; border-radius: 6px; padding: 4px; text-align: center; font-size: 14px; font-weight: 800; color: #F59E0B; font-family: sans-serif;"></div>
        <script>
            function actualizarReloj() {
                var now = new Date();
                var hrs = String(now.getHours()).padStart(2, '0');
                var mins = String(now.getMinutes()).padStart(2, '0');
                var secs = String(now.getSeconds()).padStart(2, '0');
                document.getElementById('reloj_vivo').innerHTML = hrs + ':' + mins + ':' + secs;
            }
            setInterval(actualizarReloj, 1000); actualizarReloj();
        </script>
    """, height=38)

hora_str = now_dt.strftime("%H:%M:%S")

st.sidebar.markdown(f"""
    <div style="background-color: #1E293B; padding: 6px 8px; border-radius: 8px; border: 1px solid #334155; margin-top: 4px; margin-bottom: 6px; text-align: center;">
        <span style="color: #F59E0B !important; font-size: 9px; font-weight: 800; display: block; text-transform: uppercase;">USUARIO RESPONSABLE</span>
        <span style="color: #FFFFFF !important; font-size: 13px; font-weight: 900; display: block;">👤 {st.session_state.get('usuario_activo', 'Mauricio L. Cepeda Mondaca')}</span>
        <span style="color: #F59E0B !important; font-size: 9px; font-weight: 800; display: block;">Perfil: {st.session_state.get('rol_activo', 'Administrador')}</span>
    </div>
""", unsafe_allow_html=True)

st.sidebar.markdown('<span class="selector-label-centered">RÉGIMEN Y GUARDIA DE TRABAJO</span>', unsafe_allow_html=True)
tipo_turno_sel = st.sidebar.selectbox("", ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"], key="select_regimen_box")
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"

st.sidebar.markdown('<span class="selector-label-centered">SELECCIONAR TURNO OPERATIVO</span>', unsafe_allow_html=True)
turno_seleccionado = st.sidebar.selectbox("", ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"], key="select_turno_box")

horas_turno = st.sidebar.number_input("Horas Efectivas Turno", value=10.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.header("⚙️️ Presets de Terreno y Granulometría")
preset_fl = st.sidebar.select_slider(
    "Tipo de Material & Llenado Balde/Tolva",
    options=["Roca Gruesa (80%)", "Estándar (88%)", "Fino / Seco (92%)"],
    value="Estándar (88%)"
)

fl_valor = FACTORES_MATERIAL[preset_fl]["fl"]
merma_base_valor = FACTORES_MATERIAL[preset_fl]["merma_base_pct"]

st.sidebar.markdown("---")
st.sidebar.header("Pistas de Acarreo")
perfil_rampa_sel = st.sidebar.selectbox("Pendiente y Calidad de Camino", list(PERFIL_RAMPAS.keys()), key="select_rampa_box")

st.sidebar.markdown("---")
st.sidebar.header("⛏️ Plan de Producción")
target_mineral_num = st.sidebar.number_input("Objetivo Mineral (Ton)", value=18000, step=1000)
target_esteril_num = st.sidebar.number_input("Objetivo Estéril (Ton)", value=12000, step=1000)

st.sidebar.markdown("---")
tc_mercado, diesel_mercado = obtener_indicadores_mercado()

st.sidebar.markdown(f"""
    <div style="background-color: #1E293B; padding: 6px; border-radius: 6px; border: 1px solid #0284C7; text-align: center; margin-bottom: 6px;">
        <span style="color: #38BDF8 !important; font-size: 9px; font-weight: 800; display: block; text-transform: uppercase;">🌐 MERCADO EN VIVO (CNE / BCO CENTRAL)</span>
        <span style="color: #FFFFFF !important; font-size: 11px; font-weight: 700;">USD/CLP: ${fmt_num(tc_mercado, 1)} | Diésel Ref: ${diesel_mercado} USD/L</span>
    </div>
""", unsafe_allow_html=True)

tipo_mineral = st.sidebar.selectbox("Tipo de Operación / Mineral", [
    "Caliche / Yodo", "Cobre (Cu)", "Oro (Au)", "Plata (Ag)", "Hierro (Fe)",
    "Litio (Li / LCE)", "Carbón / Energéticos", "No Metálicos / Canteras", "Movimiento de Tierras / Obras Civiles"
], key="select_mineral_box")

unidades_map = {
    "Caliche / Yodo": {"razon": "Ton Caliche / kg Yodo", "costo": "USD / Ton Caliche", "val_razon": 3.91, "val_usd": 9.079},
    "Cobre (Cu)": {"razon": "Ton Mineral / Ton Cu Fino", "costo": "USD / Ton Mineral Cu", "val_razon": 120.0, "val_usd": 15.50},
    "Oro (Au)": {"razon": "Ton Mineral / Oz Au", "costo": "USD / Ton Mineral Au", "val_razon": 1.5, "val_usd": 18.20},
    "Plata (Ag)": {"razon": "Ton Mineral / Oz Ag", "costo": "USD / Ton Mineral Ag", "val_razon": 0.8, "val_usd": 12.00},
    "Hierro (Fe)": {"razon": "Ton Mineral / Ton Concentrado Fe", "costo": "USD / Ton Mineral Fe", "val_razon": 1.8, "val_usd": 8.50},
    "Litio (Li / LCE)": {"razon": "Ton Salmuera-Roca / Ton LCE", "costo": "USD / Ton Material Li", "val_razon": 50.0, "val_usd": 22.00},
    "Carbón / Energéticos": {"razon": "Ton ROM / Ton Carbón Limpio", "costo": "USD / Ton Carbón", "val_razon": 1.3, "val_usd": 7.00},
    "No Metálicos / Canteras": {"razon": "Ton Brutas / Ton Roca Comercial", "val_razon": 1.1, "val_usd": 5.00},
    "Movimiento de Tierras / Obras Civiles": {"razon": "m³ o Ton / Unidad Avance", "costo": "USD / Ton o m³ Movido", "val_razon": 1.0, "val_usd": 4.50},
}

label_razon = unidades_map[tipo_mineral]["razon"]
label_costo = unidades_map[tipo_mineral]["costo"]
default_razon = unidades_map[tipo_mineral]["val_razon"]
default_usd = unidades_map[tipo_mineral]["val_usd"]

precio_diesel = st.sidebar.number_input("Precio Diésel (USD / Litro Contrato)", value=float(diesel_mercado), step=0.01)
factor_yodo = st.sidebar.number_input(f"{label_razon}", value=float(default_razon), step=0.01)
valor_ton_usd = st.sidebar.number_input(f"{label_costo}", value=float(default_usd), step=0.001)

st.sidebar.markdown("---")
st.sidebar.header("🚛 Parámetros Físicos de Acarreo")
distancia_acarreo_km = st.sidebar.number_input("Distancia Promedio Acarreo (km)", value=3.2, step=0.1)
vel_cargado_kmh = st.sidebar.number_input("Velocidad Base Ida (km/h)", value=18.0, step=1.0)
vel_vacio_kmh = st.sidebar.number_input("Velocidad Retorno Vacío (km/h)", value=30.0, step=1.0)

st.sidebar.markdown("---")

# ==============================================================================
# 11. INICIALIZACIÓN DE FLOTA MULTIMODELO (60T, 90T, 140T)
# ==============================================================================
if "palas_df" not in st.session_state:
    st.session_state.palas_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "PA622", "Modelo": "Liebherr R9200", "Horómetro Entrada": 14250.0, "Operador": "Carlos Araya", "Rend_TonH": 1216, "Consumo_LtsH": 120.0, "Costo_USDH": 441.00},
        {"Item": 2, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "PA623", "Modelo": "CAT 6020B", "Horómetro Entrada": 11800.5, "Operador": "Sin Asignar", "Rend_TonH": 1216, "Consumo_LtsH": 115.0, "Costo_USDH": 420.00},
        {"Item": 3, "Agendar": False, "Estado": "🟢 Disponible", "ID": "PA624", "Modelo": "Komatsu PC2000", "Horómetro Entrada": 9500.0, "Operador": "Hernán Gómez", "Rend_TonH": 1216, "Consumo_LtsH": 118.0, "Costo_USDH": 430.00},
        {"Item": 4, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "PA625", "Modelo": "Hitachi EX2600", "Horómetro Entrada": 16120.0, "Operador": "Sin Asignar", "Rend_TonH": 1300, "Consumo_LtsH": 128.0, "Costo_USDH": 460.00},
    ])

if "cf_df" not in st.session_state:
    st.session_state.cf_df = pd.DataFrame([
        {"Item": 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "CF437", "Modelo": "Komatsu WA900", "Horómetro Entrada": 8400.0, "Operador": "Sin Asignar", "Rend_TonH": 685, "Consumo_LtsH": 75.0, "Costo_USDH": 342.50},
        {"Item": 2, "Agendar": False, "Estado": "🟢 Disponible", "ID": "CF438", "Modelo": "CAT 993K", "Horómetro Entrada": 10250.0, "Operador": "Manuel Torres", "Rend_TonH": 720, "Consumo_LtsH": 82.0, "Costo_USDH": 360.00},
        {"Item": 3, "Agendar": False, "Estado": "🟢 Disponible", "ID": "CF439", "Modelo": "LeTourneau L-1850", "Horómetro Entrada": 13100.0, "Operador": "Roberto Marín", "Rend_TonH": 900, "Consumo_LtsH": 90.0, "Costo_USDH": 395.00},
        {"Item": 4, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CF440", "Modelo": "CAT 992K", "Horómetro Entrada": 7900.0, "Operador": "Sin Asignar", "Rend_TonH": 650, "Consumo_LtsH": 70.0, "Costo_USDH": 325.00},
    ])

if "caex_df" not in st.session_state:
    st.session_state.caex_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA319", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 12450.0, "Operador": "Pedro Morales", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 2, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA320", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 11200.5, "Operador": "Luis Tapia", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 3, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA321", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 12241.5, "Operador": "Andrés Castro", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 4, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA322", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 15300.2, "Operador": "Diego Rojas", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 5, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA323", "Modelo": "CAT 777F", "Cap_Ton": 90.0, "Horómetro Entrada": 8400.0, "Operador": "Gonzalo Vera", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 6, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA324", "Modelo": "CAT 777F", "Cap_Ton": 90.0, "Horómetro Entrada": 10120.0, "Operador": "Felipe Salinas", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 7, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA325", "Modelo": "CAT 785D", "Cap_Ton": 140.0, "Horómetro Entrada": 13400.0, "Operador": "Jaime Silva", "Rend_TonH": 320, "Consumo_LtsH": 65.0, "Costo_USDH": 380.00},
        {"Item": 8, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA326", "Modelo": "CAT 785D", "Cap_Ton": 140.0, "Horómetro Entrada": 9150.0, "Operador": "Marcelo Soto", "Rend_TonH": 320, "Consumo_LtsH": 65.0, "Costo_USDH": 380.00},
        {"Item": 9, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "CA327", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 11800.0, "Operador": "Javier Fuentes", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 10, "Agendar": False, "Estado": "🟢 Disponible", "ID": "CA328", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 7600.0, "Operador": "Cristian Muñoz", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 11, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CA329", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 14500.0, "Operador": "Sin Asignar", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 12, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CA330", "Modelo": "CAT 785D", "Cap_Ton": 140.0, "Horómetro Entrada": 16200.0, "Operador": "Sin Asignar", "Rend_TonH": 320, "Consumo_LtsH": 65.0, "Costo_USDH": 380.00},
    ])

if "Cap_Ton" not in st.session_state.caex_df.columns:
    st.session_state.caex_df["Cap_Ton"] = 90.0

# ==============================================================================
# 12. TABLAS DE GESTIÓN Y SINCRONIZACIÓN AUTOMÁTICA DE TALLER / ESTADOS MINA
# ==============================================================================
b64_logo = obtener_base64_img(LOGO_PATH) or obtener_base64_img("Logo_OptiMatch.png")
img_tag_logo = f'<img src="{b64_logo}" style="height: 32px; width: auto; vertical-align: middle; margin-right: 8px;">' if b64_logo else ''

st.markdown(f"""
    <div style="text-align: center; width: 100%; margin-top: 0px; margin-bottom: 10px; padding: 0px;">
        <div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
            {img_tag_logo}
            <h2 style="margin: 0; padding: 0; color: #0F172A; font-size: 20px; font-weight: 800; line-height: 1.1;">
                Estado y Agendamiento de Flota Operativa
            </h2>
        </div>
        <p style="color: #64748B; font-weight: 600; margin: 2px 0px 0px 0px; font-size: 12px; text-align: center;">
            Selección de disponibilidad mecánica, horómetros, capacidad real de tolva (60T, 90T, 140T) y asignación para el turno
        </p>
    </div>
""", unsafe_allow_html=True)

opciones_estado = ["🟢 Disponible", "🟡 Mantenimiento / Resguardo", "🔴 Falla Mecánica"]

def reindexar_flota(df):
    if not df.empty:
        df = df.reset_index(drop=True)
        df["Item"] = df.index + 1
    return df

col_t1, col_t2, col_t3 = st.columns(3)

with col_t1:
    c_img, c_txt = st.columns([1, 2])
    with c_img:
        if os.path.exists("Gif Pala.jpg"):
            st.image("Gif Pala.jpg", width=70)
    with c_txt:
        st.markdown("### Pala de Carguío")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("➖ Eliminar", key="del_pala", use_container_width=True):
            if len(st.session_state.palas_df) > 0:
                st.session_state.palas_df = st.session_state.palas_df.iloc[:-1]
                st.session_state.palas_df = reindexar_flota(st.session_state.palas_df)
                st.rerun()
    with btn_col2:
        if st.button("➕ Agregar", key="add_pala", use_container_width=True):
            nueva_pala = {
                "Item": len(st.session_state.palas_df) + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo",
                "ID": f"PA{620 + len(st.session_state.palas_df) + 1}", "Modelo": "Liebherr R9200", "Horómetro Entrada": 10000.0,
                "Operador": "Sin Asignar", "Rend_TonH": 1216, "Consumo_LtsH": 120.0, "Costo_USDH": 441.00,
            }
            st.session_state.palas_df = pd.concat([st.session_state.palas_df, pd.DataFrame([nueva_pala])], ignore_index=True)
            st.rerun()

    ed_palas = st.data_editor(
        st.session_state.palas_df,
        column_config={
            "Item": st.column_config.NumberColumn("N° Item", disabled=True),
            "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado),
            "Horómetro Entrada": st.column_config.NumberColumn("Horómetro Entrada", min_value=0.0, format="%.1f")
        },
        hide_index=True, key="editor_palas", num_rows="fixed",
    )
    st.session_state.palas_df = reindexar_flota(ed_palas)

with col_t2:
    c_img, c_txt = st.columns([1, 2])
    with c_img:
        if os.path.exists("Gif Cargador Frontal.jpg"):
            st.image("Gif Cargador Frontal.jpg", width=70)
    with c_txt:
        st.markdown("### Cargador Frontal")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("➖ Eliminar", key="del_cf", use_container_width=True):
            if len(st.session_state.cf_df) > 0:
                st.session_state.cf_df = st.session_state.cf_df.iloc[:-1]
                st.session_state.cf_df = reindexar_flota(st.session_state.cf_df)
                st.rerun()
    with btn_col2:
        if st.button("➕ Agregar", key="add_cf", use_container_width=True):
            nuevo_cf = {
                "Item": len(st.session_state.cf_df) + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo",
                "ID": f"CF{435 + len(st.session_state.cf_df) + 1}", "Modelo": "CAT 993K", "Horómetro Entrada": 8000.0,
                "Operador": "Sin Asignar", "Rend_TonH": 700, "Consumo_LtsH": 80.0, "Costo_USDH": 350.00,
            }
            st.session_state.cf_df = pd.concat([st.session_state.cf_df, pd.DataFrame([nuevo_cf])], ignore_index=True)
            st.rerun()

    ed_cf = st.data_editor(
        st.session_state.cf_df,
        column_config={
            "Item": st.column_config.NumberColumn("N° Item", disabled=True),
            "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado),
            "Horómetro Entrada": st.column_config.NumberColumn("Horómetro Entrada", min_value=0.0, format="%.1f")
        },
        hide_index=True, key="editor_cf", num_rows="fixed",
    )
    st.session_state.cf_df = reindexar_flota(ed_cf)

with col_t3:
    c_img, c_txt = st.columns([1, 2])
    with c_img:
        if os.path.exists("Camión CAEX Vacío.png"):
            st.image("Camión CAEX Vacío.png", width=70)
        elif os.path.exists("Gif Camión Minero.jpg"):
            st.image("Gif Camión Minero.jpg", width=70)
    with c_txt:
        st.markdown("### Camión CAEX")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("➖ Eliminar", key="del_caex", use_container_width=True):
            if len(st.session_state.caex_df) > 0:
                st.session_state.caex_df = st.session_state.caex_df.iloc[:-1]
                st.session_state.caex_df = reindexar_flota(st.session_state.caex_df)
                st.rerun()
    with btn_col2:
        if st.button("➕ Agregar", key="add_caex", use_container_width=True):
            nuevo_caex = {
                "Item": len(st.session_state.caex_df) + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo",
                "ID": f"CA{318 + len(st.session_state.caex_df) + 1}", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 10000.0,
                "Operador": "Sin Asignar", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00,
            }
            st.session_state.caex_df = pd.concat([st.session_state.caex_df, pd.DataFrame([nuevo_caex])], ignore_index=True)
            st.rerun()

    ed_caex = st.data_editor(
        st.session_state.caex_df,
        column_config={
            "Item": st.column_config.NumberColumn("N° Item", disabled=True),
            "Cap_Ton": st.column_config.NumberColumn("Capacidad (Ton)", min_value=10.0, max_value=400.0, format="%.0f Ton"),
            "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado),
            "Horómetro Entrada": st.column_config.NumberColumn("Horómetro Entrada", min_value=0.0, format="%.1f")
        },
        hide_index=True, key="editor_caex", num_rows="fixed",
    )
    st.session_state.caex_df = reindexar_flota(ed_caex)

total_caex = len(ed_caex)
caex_disponibles = len(ed_caex[ed_caex["Estado"] == "🟢 Disponible"])
disponibilidad_fisica_val = (caex_disponibles / total_caex * 100.0) if total_caex > 0 else 0.0

st.markdown("---")
st.markdown("<h3 style='text-align: center;'>TABLA CONTROL ESTADOS EQUIPOS MINA</h3>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# SINCRONIZACIÓN AUTOMÁTICA DE EQUIPOS EN TALLER / MANTENIMIENTO
# -----------------------------------------------------------------------------
equipos_no_disponibles = []

lista_fechas_horas_opciones = [
    f"{fecha_str} 06:00", f"{fecha_str} 07:00", f"{fecha_str} 08:00", f"{fecha_str} 09:00",
    f"{fecha_str} 10:00", f"{fecha_str} 11:00", f"{fecha_str} 12:00", f"{fecha_str} 14:00",
    f"{fecha_str} 18:00", f"{fecha_str} 20:00", f"{fecha_str} 22:00", f"{fecha_str} 23:59",
    "04-10-2026 08:00", "04-10-2026 12:00", "04-10-2026 17:30", "04-10-2026 22:00",
    "05-10-2026 06:00", "05-10-2026 12:00", "05-10-2026 18:00"
]
lista_logistica_turno_opciones = ["Mecánica / Turno A", "Contratista / Turno B", "Logística / Turno A", "Logística / Turno B"]

for _, r in ed_palas[ed_palas["Estado"] != "🟢 Disponible"].iterrows():
    equipos_no_disponibles.append({
        "ID- Equipo": r["ID"],
        "Tipo / Flota": "Pala Eléctrica",
        "Ubicación Actual": "Taller Central - Bahía 1",
        "Estado de Mantención": "Programada (PM 500 hrs)" if "Mantenimiento" in r["Estado"] else "Correctivo (Emergencia)",
        "Tipo de Falla / Trabajo": "Inspección y mantenimiento preventivo" if "Mantenimiento" in r["Estado"] else "Falla mecánica reportada en terreno",
        "Inicio Detención": f"{fecha_str} 08:00",
        "Estimado de Salida (ETR)": f"{fecha_str} 20:00",
        "Logística / Turno": "Mecánica / Turno A",
        "Plazo Extra Días": 2,
        "Quien Autoriza": "Jefe Turno Mina (A)"
    })

for _, r in ed_cf[ed_cf["Estado"] != "🟢 Disponible"].iterrows():
    equipos_no_disponibles.append({
        "ID- Equipo": r["ID"],
        "Tipo / Flota": "Cargador Frontal",
        "Ubicación Actual": "Taller de Neumáticos",
        "Estado de Mantención": "Programada (PM 500 hrs)" if "Mantenimiento" in r["Estado"] else "Correctivo (Emergencia)",
        "Tipo de Falla / Trabajo": "Cambio de neumáticos y fluidos" if "Mantenimiento" in r["Estado"] else "Reparación de transmisión",
        "Inicio Detención": f"{fecha_str} 10:30",
        "Estimado de Salida (ETR)": f"{fecha_str} 22:00",
        "Logística / Turno": "Contratista / Turno B",
        "Plazo Extra Días": 1,
        "Quien Autoriza": "Jefe Taller"
    })

for _, r in ed_caex[ed_caex["Estado"] != "🟢 Disponible"].iterrows():
    equipos_no_disponibles.append({
        "ID- Equipo": r["ID"],
        "Tipo / Flota": "Camión CAEX",
        "Ubicación Actual": "Taller Central - Bahía 3",
        "Estado de Mantención": "Programada (PM 500 hrs)" if "Mantenimiento" in r["Estado"] else "Correctivo (Emergencia)",
        "Tipo de Falla / Trabajo": "Mantención preventivo 500 hrs" if "Mantenimiento" in r["Estado"] else "Falla en sistema de frenos / motor",
        "Inicio Detención": f"{fecha_str} 07:00",
        "Estimado de Salida (ETR)": f"{fecha_str} 18:00",
        "Logística / Turno": "Mecánica / Turno A",
        "Plazo Extra Días": 2,
        "Quien Autoriza": "Jefe Oper. Mina"
    })

columnas_control_estandar = ["ID- Equipo", "Tipo / Flota", "Ubicación Actual", "Estado de Mantención", "Tipo de Falla / Trabajo", "Inicio Detención", "Estimado de Salida (ETR)", "Logística / Turno", "Plazo Extra Días", "Quien Autoriza"]

if "control_estados_mina_df" not in st.session_state:
    st.session_state.control_estados_mina_df = pd.DataFrame(equipos_no_disponibles)
else:
    df_previo = st.session_state.control_estados_mina_df
    if "Logística / Turno A" in df_previo.columns and "Logística / Turno" not in df_previo.columns:
        df_previo = df_previo.rename(columns={"Logística / Turno A": "Logística / Turno"})
        
    nuevos_rows = []
    for eq in equipos_no_disponibles:
        eq_id = eq["ID- Equipo"]
        match_prev = df_previo[df_previo["ID- Equipo"] == eq_id]
        if not match_prev.empty:
            row_dict = match_prev.iloc[0].to_dict()
            row_dict["Estado de Mantención"] = eq["Estado de Mantención"]
            nuevos_rows.append(row_dict)
        else:
            nuevos_rows.append(eq)
    st.session_state.control_estados_mina_df = pd.DataFrame(nuevos_rows) if nuevos_rows else pd.DataFrame(columns=columnas_control_estandar)

for col_nec in columnas_control_estandar:
    if col_nec not in st.session_state.control_estados_mina_df.columns:
        st.session_state.control_estados_mina_df[col_nec] = ""

equipos_vencidos = []
equipos_hoy = []

if not st.session_state.control_estados_mina_df.empty:
    for idx, row in st.session_state.control_estados_mina_df.iterrows():
        etr_val = str(row["Estimado de Salida (ETR)"])
        try:
            etr_dt = datetime.strptime(etr_val[:10], "%d-%m-%Y")
            now_dt_parsed = datetime.strptime(fecha_str, "%d/%m/%Y")
            
            if etr_dt.date() < now_dt_parsed.date():
                equipos_vencidos.append(row["ID- Equipo"])
            elif etr_dt.date() == now_dt_parsed.date():
                equipos_hoy.append(row["ID- Equipo"])
        except Exception:
            pass

if equipos_vencidos:
    st.markdown(f'<div style="background-color: #DC2626; color: #FFFFFF; padding: 10px; border-radius: 8px; font-weight: 800; margin-bottom: 8px;">🔴 ALERTA DE VENCIMIENTO CRÍTICO: Los equipos [{", ".join(equipos_vencidos)}] tienen su ETR vencido (Anterior a {fecha_str}). Revise taller urgentemente.</div>', unsafe_allow_html=True)

if equipos_hoy:
    st.markdown(f'<div style="background-color: #F59E0B; color: #0F172A; padding: 10px; border-radius: 8px; font-weight: 800; margin-bottom: 8px;">🟡 ALERTA DE VENCIMIENTO HOY: Los equipos [{", ".join(equipos_hoy)}] vencen su ETR durante la jornada actual ({fecha_str}). Planifique relevo con el Jefe de Turno.</div>', unsafe_allow_html=True)

# ==============================================================================
# TABLA CONTROL ESTADOS EQUIPOS MINA - VERSIÓN DEFINITIVA
# ==============================================================================

# Estilo CSS para asegurar títulos de columnas en negrita y color negro
st.markdown("""
<style>
div[data-testid="stDataFrame"] th {
    font-weight: 900 !important;
    color: #0F172A !important;
}
</style>
""", unsafe_allow_html=True)

# Lista completa de horas desde las 00:00 hasta las 24:00
lista_horas_opciones = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)] + ["24:00"]

# Lista actualizada de logística y turnos con especialidades solicitadas
lista_logistica_turno_opciones = [
    "Mecánica / Turno A", "Mecánica / Turno B", 
    "Contratista / Turno A", "Contratista / Turno B",
    "Eléctrico Turno A", "Eléctrico Turno B", 
    "Electrónico A", "Electrónico B", "Telecomunicaciones"
]

if not st.session_state.control_estados_mina_df.empty:
    df_editable = st.session_state.control_estados_mina_df.copy()
    
    # Aseguramos columnas de hora separadas si el DataFrame no las trae
    if "Hora Inicio" not in df_editable.columns:
        df_editable.insert(
            df_editable.columns.get_loc("Inicio Detención") + 1 if "Inicio Detención" in df_editable.columns else 0, 
            "Hora Inicio", 
            "08:00"
        )
    if "Hora Salida" not in df_editable.columns:
        df_editable.insert(
            df_editable.columns.get_loc("Estimado de Salida (ETR)") + 1 if "Estimado de Salida (ETR)" in df_editable.columns else len(df_editable.columns), 
            "Hora Salida", 
            "18:00"
        )

    ed_control_estados = st.data_editor(
        df_editable,
        column_config={
            "Inicio Detención": st.column_config.TextColumn(
                "Fecha Inicio Detención", 
                help="Ingrese manualmente la fecha (Ej: 05-10-2026)"
            ),
            "Hora Inicio": st.column_config.SelectboxColumn(
                "Hora Inicio", 
                options=lista_horas_opciones,
                help="Seleccione la hora de inicio"
            ),
            "Estimado de Salida (ETR)": st.column_config.TextColumn(
                "Fecha Est. Salida (ETR)", 
                help="Ingrese manualmente la fecha de entrega por Mantención (Ej: 06-10-2026)"
            ),
            "Hora Salida": st.column_config.SelectboxColumn(
                "Hora Salida", 
                options=lista_horas_opciones,
                help="Seleccione la hora estimada de entrega"
            ),
            "Logística / Turno": st.column_config.SelectboxColumn(
                "Logística / Turno", 
                options=lista_logistica_turno_opciones
            ),
            "Plazo Extra Días": st.column_config.SelectboxColumn(
                "Plazo Extra Días", 
                options=[i for i in range(31)]
            ),
            "Quien Autoriza": st.column_config.SelectboxColumn(
                "Quien Autoriza", 
                options=["Gerente Mina", "Jefe Oper. Mina", "Jefe Turno Mina (A)", "Jefe Turno (B)", "Jefe de Taller", "AdC Minera"]
            )
        },
        hide_index=True,
        key="editor_control_estados_mina",
        use_container_width=True
    )
    st.session_state.control_estados_mina_df = ed_control_estados
else:
    st.info("🟢 Todos los equipos de la flota se encuentran Disponibles. No hay equipos en mantenimiento o taller actualmente.")

# ==============================================================================
# 14. DASHBOARD DE RESULTADOS Y CONTROL VISUAL HEADER (UNIFICACIÓN TOTAL MF)
# ==============================================================================
st.markdown("---")
st.markdown(f"<h2 style='text-align: center;'>Resumen de Agendamiento Pre-Turno: {num_agendamiento}</h2>", unsafe_allow_html=True)

b64_logo_atacama = obtener_base64_img(LOGO_ATACAMA_PATH)

if b64_logo_atacama:
    faena_header_html = f"""
        <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 10px; text-align: center;">
            <img src="{b64_logo_atacama}" style="height: 38px; width: auto; vertical-align: middle; object-fit: contain;">
            <h3 style="margin: 0; padding: 0; color: #0F172A; font-size: 16px; font-weight: 800; display: inline-block;">
                Faena: {nombre_mina} | Fecha y Hora: {nombre_dia_actual}, {fecha_str} {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})
            </h3>
        </div>
    """
else:
    faena_header_html = f"""
        <h3 style="margin: 0 0 10px 0; padding: 0; color: #0F172A; font-size: 16px; font-weight: 800; text-align: center;">
            Faena: {nombre_mina} | Fecha y Hora: {nombre_dia_actual}, {fecha_str} {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})
        </h3>
    """

st.markdown(faena_header_html, unsafe_allow_html=True)

# ==============================================================================
# SECCIÓN KPI / MATCH FACTOR SEGURO
# ==============================================================================
delta_texto = "Banda Lean OK" if (0.92 <= match_factor <= 1.08) else "Fuera de Rango"
delta_color_val = "normal" if (0.92 <= match_factor <= 1.08) else "inverse"

k2.metric(
    "Match Factor (MF)", 
    f"{match_factor:.2f}", 
    delta=delta_texto,
    delta_color=delta_color_val
)
)
k1.metric("Disp. Física (DF)", f"{disponibilidad_fisica_val:.1f}%", delta=f"{caex_disponibles}/{total_caex} CAEX Activos")
k2.metric("Match Factor (MF)", f"{fmt_num(match_factor, 2)}", delta="Banda Lean OK" if 0.92 <= match_factor <= 1.08 else "Fuera de Rango")
k3.metric("Ton Entregadas Netas", f"{fmt_num(tonelaje_efectivo, 0)} Ton", delta=f"Merma: {merma_pct_real:.1f}% ({fmt_num(tonelaje_merma, 0)}T)")
k4.metric("Consumo Diésel Total", f"{fmt_num(litros_diesel_turno, 0)} Lts")
k5.metric("Costo Real/Ton", f"${fmt_num(costo_unitario_ton, 2)} USD/Ton")
k6.metric("Beneficio Neto", f"${fmt_num(beneficio_neto_usd, 2)} USD")

st.markdown("---")

col_eval1, col_eval2 = st.columns(2)

with col_eval1:
    st.markdown("### ⛽ Evaluación Económica, Merma y Ruta")
    st.markdown(f'<p class="highlight-red-large">• Pérdida en Ruta (Merma): {fmt_num(tonelaje_merma, 0)} Ton ({merma_pct_real:.1f}% del total cargado)</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Velocidad Efectiva Subida: {fmt_num(vel_cargado_efectiva_kmh, 1)} km/h ({perfil_rampa_sel})</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Costo Combustible / Ton Efectiva: ${fmt_num(costo_diesel_por_ton, 2)} USD/Ton</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Consumo Específico Diésel Real: {fmt_num(consumo_especifico_lts_ton, 3)} Lts/Ton Entregada</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Tiempo en Cola Estimado: {fmt_num(tiempo_cola_promedio, 1)} min/ciclo</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Huella CO₂ Operativa: {fmt_num(co2_por_ton, 2)} kg CO₂/Ton ({fmt_num(emisiones_co2_kg, 0)} kg CO₂ total)</p>', unsafe_allow_html=True)

    total_objetivo = target_mineral_num + target_esteril_num
    cumplimiento = (tonelaje_efectivo / total_objetivo * 100) if total_objetivo > 0 else 0
    st.markdown(f'<p class="highlight-red-large">• Cumplimiento Plan de Mina (Masa Efectiva): {fmt_num(cumplimiento, 1)}% de {fmt_num(total_objetivo, 0)} Ton Target</p>', unsafe_allow_html=True)
    st.progress(min(cumplimiento / 100.0, 1.0))

with col_eval2:
    st.markdown("### 🚦 Semáforo Prescriptivo de Balance de Flota")
    st.markdown('<p class="mf-label">Match Factor Calculado (Físico):</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="mf-value">{fmt_num(match_factor, 2)}</p>', unsafe_allow_html=True)

    mf_eval = round(match_factor, 2)

    if 0.92 <= mf_eval <= 1.08:
        st.success(f"🟢 **AGENDAMIENTO ÓPTIMO Y RENTABLE EN BANDA VERDE (Match Factor: {fmt_num(mf_eval, 2)})** — *Flota acoplada, desperdicio de diésel por ralentí minimizado y costo unitario optimizado.*")
    elif 0.85 <= mf_eval < 0.92 or 1.08 < mf_eval <= 1.15:
        st.warning(f"🟡 **DESCALCE LEVE EN BANDA AMARILLA (Match Factor: {fmt_num(mf_eval, 2)})** — *Alerta preventiva: evalúe ajustar 1 CAEX según prioridad de tonelaje vs costo.*")
    elif mf_eval < 0.85:
        st.error(f"🔴 **DESCALCE SEVERO POR SUB-TRANSPORTE (Match Factor: {fmt_num(mf_eval, 2)})** — *Prescripción: subutilización de la unidad de carguío.*")
    else:
        st.error(f"🔴 **DESCALCE SEVERO POR SOBREDIMENSIONAMIENTO / EXCESO DE CAMIONES (Match Factor: {fmt_num(mf_eval, 2)})** — *Prescripción: exceso de CAEX generando colas severas en la única pala.*")

# ==============================================================================
# 15. MÓDULO DE SEGUIMIENTO ESPACIAL (PLANO DE MINA CON CONDICIONAL DE IMAGEN)
# ==============================================================================
st.markdown("---")

img_plano_b64 = obtener_base64_img("Plano_Mina.png") or obtener_base64_img("mapa_mina.png") or obtener_base64_img("plano_mina.png")

if img_plano_b64:
    header_monitoreo_html = f"""
        <div style="display: flex; align-items: center; justify-content: center; gap: 8px; text-align: center;">
            <img src="{img_plano_b64}" style="height: 28px; width: auto; vertical-align: middle;">
            <h3 style="margin: 0; padding: 0; color: #0F172A; font-size: 18px; font-weight: 800;">
                Monitoreo Espacial del Circuito y Control de Fallas en Vivo
            </h3>
        </div>
    """
    st.markdown(header_monitoreo_html, unsafe_allow_html=True)
else:
    st.markdown("<h3 style='text-align: center;'>Monitoreo Espacial del Circuito y Control de Fallas en Vivo</h3>", unsafe_allow_html=True)

st.markdown(
    f"<div style='text-align: center;'>💡 <b>Ciclo Operacional Calculado:</b> <b>{fmt_num(tiempo_ciclo_efectivo_min, 2)} min</b> "
    f"(Carga: 2.2m | Ida @ {fmt_num(vel_cargado_efectiva_kmh, 1)} km/h: {fmt_num(t_ida_min, 2)}m | "
    f"Descarga: 2.3m | Retorno @ {vel_vacio_kmh} km/h: {fmt_num(t_retorno_min, 2)}m)</div>", unsafe_allow_html=True
)

if "acarreo_iniciado" not in st.session_state:
    st.session_state.acarreo_iniciado = False

col_trig1, col_trig2, col_trig3 = st.columns([1.8, 3.5, 1.5])

with col_trig1:
    btn_trig = st.button("🔴 INICIO DE ACARREO", type="primary")
    if btn_trig:
        st.session_state.acarreo_iniciado = True
        st.success("✅ Acarreo iniciado por confirmación VHF.")

with col_trig2:
    st.markdown("""
        <div style="padding: 4px 0px; text-align: center;">
            <span style="color: #0F172A !important; font-weight: 800 !important; font-size: 12px !important; display: block;">
                📻 <b>AVISO RADIAL OPERADOR PALA - SIMULACIÓN BASADA EN VELOCIDADES REALES Y CICLO FÍSICO</b>
            </span>
        </div>
    """, unsafe_allow_html=True)

with col_trig3:
    if st.button("🔄 Reiniciar Postura", use_container_width=True):
        st.session_state.acarreo_iniciado = False

img_caex_cargado_b64 = obtener_base64_img("Camion_CAEX_Cargado.png") or obtener_base64_img("Camión CAEX Cargado.png") or obtener_base64_img("camion_caex_cargado.png")
img_caex_vacio_b64 = obtener_base64_img("Camion_CAEX_Vacio.png") or obtener_base64_img("Camión CAEX Vacío.png") or obtener_base64_img("camion_caex_vacio.png")
img_pala_b64 = obtener_base64_img("Gif Pala.jpg") or obtener_base64_img("image_859ef9.png")
img_cf_b64 = obtener_base64_img("Gif Cargador Frontal.jpg") or obtener_base64_img("image_859f19.png")

caex_agendados = ed_caex[(ed_caex["Agendar"] == True) & (ed_caex["Estado"] == "🟢 Disponible")]
lista_caex_js = []
for _, r in caex_agendados.iterrows():
    lista_caex_js.append({
        "id": str(r.get("ID", "CAEX")),
        "modelo": str(r.get("Modelo", "CAEX")),
        "capTon": float(r.get("Cap_Ton", 90.0)),
        "operador": str(r.get("Operador", "Sin Operador")),
        "rend": float(r.get("Rend_TonH", 210.0)),
    })

palas_activas_js = []
for _, r in ed_palas[(ed_palas["Agendar"] == True) & (ed_palas["Estado"] == "🟢 Disponible")].iterrows():
    palas_activas_js.append({
        "id": str(r.get("ID", "PALA")),
        "modelo": str(r.get("Modelo", "R9200")),
        "operador": str(r.get("Operador", "Sin Operador")),
        "rend": float(r.get("Rend_TonH", 1216.0)),
    })

cf_activos_js = []
for _, r in ed_cf[(ed_cf["Agendar"] == True) & (ed_cf["Estado"] == "🟢 Disponible")].iterrows():
    cf_activos_js.append({
        "id": str(r.get("ID", "CF")),
        "modelo": str(r.get("Modelo", "WA900")),
        "operador": str(r.get("Operador", "Sin Operador")),
        "rend": float(r.get("Rend_TonH", 700.0)),
    })

caex_json_str = json.dumps(lista_caex_js)
palas_json_str = json.dumps(palas_activas_js)
cf_json_str = json.dumps(cf_activos_js)
acarreo_activo_bool = "true" if st.session_state.acarreo_iniciado else "false"

# VALOR MAESTRO EXACTO PASADO DIRECTAMENTE DESDE PYTHON A JS (SIN RE-CÁLCULOS DIVERGENTES)
mf_base_exacto = f"{match_factor:.2f}"

html_gps_canvas = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ margin: 0; padding: 0; background-color: #F8FAFC; font-family: Arial, sans-serif; overflow: hidden; }}
        #mapContainer {{
            width: 100%; height: 380px; position: relative; background-color: #FFFFFF;
            border: 1px solid #CBD5E1; border-radius: 12px; box-shadow: 0px 4px 12px rgba(0,0,0,0.03);
        }}
        canvas {{ width: 100%; height: 100%; display: block; cursor: pointer; }}
        .kpi-panel {{
            position: absolute; top: 12px; right: 15px; background: rgba(15, 23, 42, 0.95);
            border: 1px solid #F59E0B; border-radius: 10px; padding: 8px 14px; color: #FFFFFF;
            font-size: 11px; font-weight: 800; box-shadow: 0px 4px 12px rgba(0,0,0,0.3); z-index: 10;
        }}
        .kpi-title {{ color: #F59E0B; font-size: 10px; text-align: center; margin-bottom: 4px; border-bottom: 1px solid #334155; padding-bottom: 2px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .kpi-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; text-align: center; }}
        .kpi-val {{ font-size: 15px; color: #38BDF8; font-weight: 900; }}
        .tooltip {{
            position: absolute; display: none; background: rgba(15, 23, 42, 0.95); color: #FFFFFF;
            padding: 8px 12px; border-radius: 8px; font-size: 11px; pointer-events: none;
            border: 1px solid #F59E0B; box-shadow: 0px 4px 10px rgba(0,0,0,0.3); z-index: 100; line-height: 1.4;
        }}
    </style>
</head>
<body>
    <div id="mapContainer">
        <div class="kpi-panel">
            <div class="kpi-title">📊 MÉTRICAS DE VUELTAS Y DINÁMICA DE TURNO</div>
            <div class="kpi-grid">
                <div><span>META VTS</span><div class="kpi-val" id="kpiMeta">{vueltas_totales_meta}</div></div>
                <div><span>ACTUAL</span><div class="kpi-val" style="color:#10B981;" id="kpiActual">0</div></div>
                <div><span>M. FACTOR</span><div class="kpi-val" style="color:#10B981;" id="kpiMF">{mf_base_exacto}</div></div>
                <div><span>FLOTA ACT.</span><div class="kpi-val" style="color:#E2E8F0;" id="kpiFlota">{len(lista_caex_js)}/{len(lista_caex_js)}</div></div>
            </div>
        </div>
        <canvas id="gpsCanvas"></canvas>
        <div id="tooltip" class="tooltip"></div>
    </div>

    <script>
        const canvas = document.getElementById('gpsCanvas');
        const ctx = canvas.getContext('2d');
        const tooltip = document.getElementById('tooltip');

        function resizeCanvas() {{ canvas.width = canvas.offsetWidth; canvas.height = canvas.offsetHeight; }}
        resizeCanvas();

        const caexList = {caex_json_str};
        const palasListRaw = {palas_json_str};
        const cfListRaw = {cf_json_str};
        const isTrackingActive = {acarreo_activo_bool};
        const globalFL = {fl_valor};
        const pythonExactMF = {match_factor};

        const distKm = {distancia_acarreo_km};
        const speedLoadedKmh = {vel_cargado_efectiva_kmh};
        const speedEmptyKmh = {vel_vacio_kmh};

        const imgCaexCargado = new Image(); imgCaexCargado.src = "{img_caex_cargado_b64 or ''}";
        const imgCaexVacio = new Image(); imgCaexVacio.src = "{img_caex_vacio_b64 or ''}";
        const imgPala = new Image(); imgPala.src = "{img_pala_b64 or ''}";
        const imgCF = new Image(); imgCF.src = "{img_cf_b64 or ''}";

        const timeLoading = 2.20;
        const timeHaul = {t_ida_min};
        const timeDumping = 2.30;
        const timeReturn = {t_retorno_min};
        const totalCycleUnits = {tiempo_ciclo_efectivo_min};

        const simSpeed = 0.0004;
        const totalNumCaex = Math.max(1, caexList.length);
        const staggerInterval = totalCycleUnits / totalNumCaex;

        let totalVueltasCompletadas = 0;
        let palasList = palasListRaw.map(p => ({{ ...p, stoppedByFault: false }}));
        let cfList = cfListRaw.map(cf => ({{ ...cf, stoppedByFault: false }}));

        let vehicles = caexList.map((c, idx) => {{
            let offset = idx * staggerInterval;
            let assignedEq = idx % Math.max(1, (palasList.length + cfList.length));
            return {{
                id: c.id, modelo: c.modelo, capTon: c.capTon || 90.0, operador: c.operador, rend: c.rend,
                cycleTime: offset, prevCycleTime: offset, vueltas: 0, x: 0, y: 0,
                isLoaded: false, statusText: "Postura Previa (Listo para Cargar)",
                speedKmh: 0, isReturning: false, equipmentAssigned: assignedEq, stoppedByFault: false
            }};
        }});

        let palaHitboxes = []; let cfHitboxes = [];

        function recalculateDynamicMF() {{
            let activeCaex = vehicles.filter(v => !v.stoppedByFault).length;
            let activePalas = palasList.filter(p => !p.stoppedByFault).length;
            let activeCF = cfList.filter(cf => !cf.stoppedByFault).length;
            let totalActiveLoading = Math.max(1, activePalas + activeCF);

            // SINCRONIZACIÓN ESTRICTA: Se usa exactamente la misma base matemática de Python adaptada a las fallas en vivo
            let dynamicMF = (activeCaex * 2.20 * totalActiveLoading) > 0 ? (activeCaex * 2.20) / (totalActiveLoading * {t_ciclo_base_exacto}) : pythonExactMF;
            let mfEvalJS = Number(dynamicMF.toFixed(2));
            
            let elemMF = document.getElementById('kpiMF');
            elemMF.innerText = mfEvalJS.toFixed(2);
            document.getElementById('kpiFlota').innerText = activeCaex + "/" + vehicles.length;

            if (mfEvalJS >= 0.92 && mfEvalJS <= 1.08) {{ elemMF.style.color = "#10B981"; }}
            else if (mfEvalJS < 0.85 || mfEvalJS > 1.15) {{ elemMF.style.color = "#EF4444"; }}
            else {{ elemMF.style.color = "#F59E0B"; }}
        }}

        function drawCaexTruck(x, y, isLoaded, isReturning, isStopped) {{
            ctx.save(); ctx.translate(x, y);
            if (isReturning) {{ ctx.scale(-1, 1); }}

            ctx.fillStyle = isStopped ? "#EF4444" : (isLoaded ? "#D97706" : "#CBD5E1");
            ctx.strokeStyle = "#0F172A"; ctx.lineWidth = 1.5;
            ctx.beginPath(); ctx.roundRect(-18, -10, 26, 16, 2); ctx.fill(); ctx.stroke();

            if (isLoaded && !isStopped) {{
                ctx.fillStyle = "#78350F"; ctx.beginPath(); ctx.arc(-5, -5, 6, Math.PI, 0); ctx.fill();
            }}

            ctx.fillStyle = isStopped ? "#991B1B" : "#F59E0B";
            ctx.beginPath(); ctx.roundRect(8, -6, 9, 12, 2); ctx.fill(); ctx.stroke();

            ctx.fillStyle = "#1E293B"; ctx.beginPath();
            ctx.arc(-10, 8, 4, 0, 2 * Math.PI); ctx.arc(6, 8, 4, 0, 2 * Math.PI);
            ctx.arc(-10, -8, 4, 0, 2 * Math.PI); ctx.arc(6, -8, 4, 0, 2 * Math.PI); ctx.fill();
            ctx.restore();
        }}

        function animate() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            const paddingL = 170; const paddingR = 170;
            const trackWidth = canvas.width - paddingL - paddingR;
            const yIda = canvas.height * 0.35; const yRetorno = canvas.height * 0.65;
            const xInicio = paddingL; const xFin = paddingL + trackWidth;

            palaHitboxes = []; cfHitboxes = [];

            ctx.beginPath(); ctx.setLineDash([8, 6]); ctx.strokeStyle = "#10B981"; ctx.lineWidth = 4;
            ctx.moveTo(xInicio, yIda); ctx.lineTo(xFin, yIda); ctx.stroke();

            ctx.beginPath(); ctx.setLineDash([]); ctx.strokeStyle = "#DC2626"; ctx.lineWidth = 4;
            ctx.moveTo(xInicio, yRetorno); ctx.lineTo(xFin, yRetorno); ctx.stroke();

            ctx.font = "bold 11px Arial"; ctx.fillStyle = "#10B981"; ctx.textAlign = "left";
            ctx.fillText("VÍA IDA CARGADO (" + distKm.toFixed(1) + " km @ " + speedLoadedKmh.toFixed(1) + " km/h)", xInicio, yIda - 22);
            ctx.fillStyle = "#DC2626";
            ctx.fillText("VÍA RETORNO VACÍO (" + distKm.toFixed(1) + " km @ " + speedEmptyKmh.toFixed(1) + " km/h)", xInicio, yRetorno - 22);

            palasList.forEach((p, idx) => {{
                let py = yIda - 20 - (idx * 46); let px = xInicio - 65; let size = 48;
                if (imgPala.complete && imgPala.naturalWidth > 0 && !p.stoppedByFault) {{
                    ctx.drawImage(imgPala, px, py - (size / 2), size, size);
                }} else {{
                    ctx.fillStyle = p.stoppedByFault ? "#EF4444" : "#F59E0B";
                    ctx.fillRect(px, py - 20, 38, 38);
                }}
                palaHitboxes.push({{ x: px + (size / 2), y: py, radius: 25, index: idx, data: p }});
                ctx.fillStyle = p.stoppedByFault ? "#DC2626" : "#0F172A";
                ctx.font = "bold 11px Arial"; ctx.textAlign = "right";
                let statusTag = p.stoppedByFault ? " (FALLA)" : "";
                ctx.fillText("Pala " + p.id + statusTag, px - 8, py + 4);
            }});

            cfList.forEach((cf, idx) => {{
                let totalPalas = palasList.length;
                let py = yIda - 20 - ((totalPalas + idx) * 46); let px = xInicio - 65; let size = 38;
                if (imgCF.complete && imgCF.naturalWidth > 0 && !cf.stoppedByFault) {{
                    ctx.drawImage(imgCF, px, py - (size / 2), size, size);
                }} else {{
                    ctx.fillStyle = cf.stoppedByFault ? "#EF4444" : "#F59E0B";
                    ctx.fillRect(px, py - 15, 30, 30);
                }}
                cfHitboxes.push({{ x: px + (size / 2), y: py, radius: 22, index: idx, data: cf }});
                ctx.fillStyle = cf.stoppedByFault ? "#DC2626" : "#0F172A";
                ctx.font = "bold 11px Arial"; ctx.textAlign = "right";
                let statusTag = cf.stoppedByFault ? " (FALLA)" : "";
                ctx.fillText("CF " + cf.id + statusTag, px - 8, py + 4);
            }});

            ctx.fillStyle = "#DC2626"; ctx.beginPath();
            ctx.arc(xFin + 25, (yIda + yRetorno) / 2, 12, 0, 2 * Math.PI); ctx.fill();

            ctx.font = "bold 11px Arial"; ctx.fillStyle = "#DC2626"; ctx.textAlign = "left";
            const yCentro = (yIda + yRetorno) / 2;
            ctx.fillText("• BOTADERO", xFin + 45, yCentro - 14);
            ctx.fillText("• CHANCADOR", xFin + 45, yCentro + 3);
            ctx.fillText("• PILA DE ACOPIO", xFin + 45, yCentro + 20);

            let totalEquiposCarguio = Math.max(1, palasList.length + cfList.length);

            vehicles.forEach((v, idx) => {{
                if (isTrackingActive && !v.stoppedByFault) {{
                    v.prevCycleTime = v.cycleTime;
                    v.cycleTime = (v.cycleTime + simSpeed) % totalCycleUnits;
                    if (v.cycleTime < v.prevCycleTime) {{ v.vueltas++; totalVueltasCompletadas++; }}
                }}

                let t = v.cycleTime;
                let eqIndex = (totalEquiposCarguio > 0) ? (v.equipmentAssigned % totalEquiposCarguio) : 0;
                let targetY = yIda - 20 - (eqIndex * 46);
                let eqNombre = "Pala/CF";

                if (eqIndex < palasList.length) {{ eqNombre = palasList[eqIndex] ? palasList[eqIndex].id : "Pala"; }}
                else {{ let cfIdx = eqIndex - palasList.length; eqNombre = cfList[cfIdx] ? cfList[cfIdx].id : "CF"; }}

                if (!v.stoppedByFault) {{
                    if (!isTrackingActive) {{
                        v.x = xInicio; v.y = targetY; v.isLoaded = false; v.isReturning = false;
                        v.statusText = "Postura Previa (Acolado en " + eqNombre + ")"; v.speedKmh = 0;
                    }} else if (t < timeLoading) {{
                        v.x = xInicio; v.y = targetY; v.isLoaded = false; v.isReturning = false;
                        v.statusText = "En Carga (" + eqNombre + ")"; v.speedKmh = 0;
                    }} else if (t < timeLoading + timeHaul) {{
                        let progressRatio = (t - timeLoading) / timeHaul;
                        v.x = xInicio + (progressRatio * trackWidth); v.y = targetY + progressRatio * (yIda - targetY);
                        v.isLoaded = true; v.isReturning = false; v.statusText = "Acarreo Ida -> Botadero/Chancador/Pila"; v.speedKmh = speedLoadedKmh;
                    }} else if (t < timeLoading + timeHaul + timeDumping) {{
                        v.x = xFin; v.y = (yIda + yRetorno) / 2; 
                        v.isLoaded = false; v.isReturning = true; v.statusText = "En Volteo / Descarga"; v.speedKmh = 0;
                    }} else {{
                        let progressRatio = (t - (timeLoading + timeHaul + timeDumping)) / timeReturn;
                        v.x = xFin - (progressRatio * trackWidth); v.y = yRetorno; 
                        v.isLoaded = false; v.isReturning = true; v.statusText = "Retorno Vacío -> " + eqNombre; v.speedKmh = speedEmptyKmh;
                    }}
                }} else {{ v.statusText = "🔴 DETENIDO POR FALLA / MANTENCIÓN"; v.speedKmh = 0; }}

                let imgToDraw = v.isLoaded ? imgCaexCargado : imgCaexVacio;
                ctx.save(); ctx.translate(v.x, v.y);
                if (v.isReturning) {{ ctx.scale(-1, 1); }}

                if (!v.stoppedByFault && imgToDraw.complete && imgToDraw.naturalWidth > 0 && imgToDraw.src.length > 50) {{
                    ctx.drawImage(imgToDraw, -20, -20, 40, 40);
                }} else {{ drawCaexTruck(0, 0, v.isLoaded, false, v.stoppedByFault); }}
                ctx.restore();

                ctx.font = "bold 10px Arial"; ctx.textAlign = "center";
                if (v.stoppedByFault) {{
                    ctx.fillStyle = "#DC2626"; ctx.fillText("🔴 CAEX " + v.id + " (FALLA)", v.x, v.y + 26);
                }} else {{
                    ctx.fillStyle = "#0F172A";
                    let speedLabel = v.speedKmh > 0 ? " [" + v.speedKmh.toFixed(0) + " km/h]" : " [0 km/h]";
                    ctx.fillText("CAEX " + v.id + " (" + v.capTon + "T) - " + v.vueltas + " vts" + speedLabel, v.x, v.y + 26);
                }}
            }});

            if (isTrackingActive) {{ document.getElementById('kpiActual').innerText = totalVueltasCompletadas; }}
            recalculateDynamicMF(); requestAnimationFrame(animate);
        }}

        requestAnimationFrame(animate);

        canvas.addEventListener('click', function(e) {{
            const rect = canvas.getBoundingClientRect();
            const clickX = e.clientX - rect.left; const clickY = e.clientY - rect.top;

            vehicles.forEach(v => {{
                let dist = Math.hypot(clickX - v.x, clickY - v.y);
                if (dist < 28) {{ v.stoppedByFault = !v.stoppedByFault; }}
            }});

            palaHitboxes.forEach(p => {{
                let dist = Math.hypot(clickX - p.x, clickY - p.y);
                if (dist < p.radius) {{ palasList[p.index].stoppedByFault = !palasList[p.index].stoppedByFault; }}
            }});

            cfHitboxes.forEach(cf => {{
                let dist = Math.hypot(clickX - cf.x, clickY - cf.y);
                if (dist < cf.radius) {{ cfList[cf.index].stoppedByFault = !cfList[cf.index].stoppedByFault; }}
            }});
        }});

        canvas.addEventListener('mousemove', function(e) {{
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left; const mouseY = e.clientY - rect.top;
            let hovered = false;

            vehicles.forEach(v => {{
                let dist = Math.hypot(mouseX - v.x, mouseY - v.y);
                if (dist < 28) {{
                    hovered = true; tooltip.style.display = 'block';
                    tooltip.style.left = (v.x + 15) + 'px'; tooltip.style.top = (v.y - 35) + 'px';
                    let capEfectiva = v.capTon * globalFL;
                    let tonAprox = (v.vueltas * capEfectiva).toFixed(0);
                    let toggleMsg = v.stoppedByFault ? "<span style='color:#10B981;'><b>(Haz clic para REANUDAR)</b></span>" : "<span style='color:#EF4444;'><b>(Haz clic para DETENER POR FALLA)</b></span>";
                    tooltip.innerHTML = '<b>🚛 CAMIÓN CAEX ' + v.id + '</b><br>' +
                                        '• Operador(a): <b>' + (v.operador || "Sin Asignar") + '</b><br>' +
                                        '• Modelo: ' + (v.modelo || "CAEX") + '<br>' +
                                        '• Cap. Nominal: ' + v.capTon + ' Ton<br>' +
                                        '• Cap. Efectiva (FL ' + (globalFL*100).toFixed(0) + '%): ' + capEfectiva.toFixed(1) + ' Ton<br>' +
                                        '• Vueltas Completadas: ' + v.vueltas + '<br>' +
                                        '• Tonelaje Movido Aprox.: ' + tonAprox + ' Ton<br>' +
                                        '• Estado: ' + v.statusText + '<br>' + toggleMsg;
                }}
            }});

            if (!hovered) {{
                palaHitboxes.forEach(p => {{
                    let dist = Math.hypot(mouseX - p.x, mouseY - p.y);
                    if (dist < p.radius) {{
                        hovered = true; tooltip.style.display = 'block';
                        tooltip.style.left = (p.x + 20) + 'px'; tooltip.style.top = (p.y - 35) + 'px';
                        let pData = palasList[p.index];
                        let toggleMsg = pData.stoppedByFault ? "<span style='color:#10B981;'><b>(Haz clic para REANUDAR)</b></span>" : "<span style='color:#EF4444;'><b>(Haz clic para DETENER POR FALLA)</b></span>";
                        let statusText = pData.stoppedByFault ? "🔴 DETENIDA POR FALLA" : "🟢 Operando Normal";
                        tooltip.innerHTML = '<b>🏗️ PALA DE CARGUÍO ' + pData.id + '</b><br>' +
                                            '• Operador(a): <b>' + (pData.operador || "Sin Asignar") + '</b><br>' +
                                            '• Modelo: ' + (pData.modelo || "R9200") + '<br>' +
                                            '• Capacidad Nominal: 1216 Ton/h<br>' +
                                            '• Factor Llenado (FL): ' + (globalFL*100).toFixed(0) + '%<br>' +
                                            '• Estado: ' + statusText + '<br>' + toggleMsg;
                    }}
                }});
            }}

            if (!hovered) {{
                cfHitboxes.forEach(cf => {{
                    let dist = Math.hypot(mouseX - cf.x, mouseY - cf.y);
                    if (dist < cf.radius) {{
                        hovered = true; tooltip.style.display = 'block';
                        tooltip.style.left = (cf.x + 20) + 'px'; tooltip.style.top = (cf.y - 35) + 'px';
                        let cfData = cfList[cf.index];
                        let toggleMsg = cfData.stoppedByFault ? "<span style='color:#10B981;'><b>(Haz clic para REANUDAR)</b></span>" : "<span style='color:#EF4444;'><b>(Haz clic para DETENER POR FALLA)</b></span>";
                        let statusText = cfData.stoppedByFault ? "🔴 DETENIDO POR FALLA" : "🟢 Operando Normal";
                        tooltip.innerHTML = '<b>🚜 CARGADOR FRONTAL ' + cfData.id + '</b><br>' +
                                            '• Operador(a): <b>' + (cfData.operador || "Sin Asignar") + '</b><br>' +
                                            '• Modelo: ' + (cfData.modelo || "WA900") + '<br>' +
                                            '• Rendimiento: ' + cfData.rend + ' Ton/h<br>' +
                                            '• Factor Llenado (FL): ' + (globalFL*100).toFixed(0) + '%<br>' +
                                            '• Estado: ' + statusText + '<br>' + toggleMsg;
                    }}
                }});
            }}

            if (!hovered) {{ tooltip.style.display = 'none'; }}
        }});
    </script>
</body>
</html>
"""

components.html(html_gps_canvas, height=400)

# ==============================================================================
# 16. REPORTE, CONCILIACIÓN Y TASA DE ADOPCIÓN PRESCRIPTIVA COMPACTA
# ==============================================================================
st.markdown("---")
col_exp1, col_exp2 = st.columns([2, 1])

with col_exp1:
    st.subheader("📄 Reporte y Ficha Prescriptiva Pre-Turno")

with col_exp2:
    df_export = pd.DataFrame([{
        "N° Agendamiento": num_agendamiento, "Fecha": fecha_str, "Hora": hora_str,
        "Faena / Mina": nombre_mina, "Turno Operativo": turno_seleccionado,
        "Régimen Guardia": regimen_guardia, "Tipo de Mineral": tipo_mineral,
        "Responsable Agendamiento": st.session_state.get("usuario_activo", "Mauricio L. Cepeda Mondaca"),
        "Disponibilidad Física (%)": round(disponibilidad_fisica_val, 1),
        "Factor de Llenado (%)": round(fl_valor * 100, 0),
        "Merma por Traslado (%)": round(merma_pct_real, 1),
        "Match Factor Calculado": round(match_factor, 2),
        "Toneladas Cargadas (Ton)": round(tonelaje_cargado, 0),
        "Toneladas Efectivas Entregadas (Ton)": round(tonelaje_efectivo, 0),
        "Consumo Diésel Total (Lts)": round(litros_diesel_turno, 0),
        "Consumo Específico (Lts/Ton)": round(consumo_especifico_lts_ton, 3),
        "Huella CO2 Operativa (kg CO2/Ton)": round(co2_por_ton, 2),
        "OPEX Total Turno (USD)": round(costo_opex_total_turno, 2),
        "Costo Unitario (USD/Ton Efectiva)": round(costo_unitario_ton, 2),
        "Beneficio Neto Proyectado (USD)": round(beneficio_neto_usd, 2),
        "Prescripcion Aceptada": prescripcion_aceptada_val,
    }])

    csv_data = df_export.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="📥 Descargar Ficha Pre-Turno (Excel / CSV)", data=csv_data,
        file_name=f"Ficha_Agendamiento_{num_agendamiento}.csv", mime="text/csv", use_container_width=True,
    )

st.markdown("---")
st.subheader("🔄 Conciliación y Cierre de Turno (Plan vs. Actual)")

conn_conc = sqlite3.connect(DB_FILE)
df_lista_ag = pd.read_sql_query(
    "SELECT num_agendamiento, fecha_registro, turno, jefe_turno, ton_movidas, ton_efectivas, consumo_diesel_lts, opex_total_usd, match_factor, prescripcion_aceptada FROM historico_agendamientos ORDER BY id DESC",
    conn_conc,
)
conn_conc.close()

if not df_lista_ag.empty:
    opciones_ag = df_lista_ag.apply(lambda row: f"{row['num_agendamiento']} | {row['fecha_registro']} | {row['turno']} | Resp: {row['jefe_turno']}", axis=1).tolist()
    ag_seleccionado_str = st.selectbox("🔍 Seleccionar Agendamiento Guardado para Cierre:", opciones_ag)
    num_ag_selected = ag_seleccionado_str.split(" | ")[0]

    datos_plan = df_lista_ag[df_lista_ag["num_agendamiento"] == num_ag_selected].iloc[0]
    ton_plan = float(datos_plan["ton_efectivas"]) if float(datos_plan["ton_efectivas"]) > 0 else float(datos_plan["ton_movidas"])
    diesel_plan = float(datos_plan["consumo_diesel_lts"])
    mf_plan = float(datos_plan["match_factor"])

    st.info(f"📋 **Datos Planificados en {num_ag_selected}:** Toneladas Efectivas Proyectadas = **{fmt_num(ton_plan, 0)} Ton** | Diésel Presupuestado = **{fmt_num(diesel_plan, 0)} Lts** | Match Factor = **{fmt_num(mf_plan, 2)}**")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("#### 📥 Ingreso de Datos Reales de Terreno (Post-Turno)")
        st.markdown("**Toneladas Reales Extraídas (Ton):**")
        ton_reales = st.number_input("", value=ton_plan, step=500.0, key="input_ton_reales", label_visibility="collapsed")

        st.markdown("**Consumo Diésel Real (Litros):**")
        diesel_real = st.number_input("", value=diesel_plan, step=200.0, key="input_diesel_reales", label_visibility="collapsed")

        opciones_causales = ["Falla Mecánica de CAEX", "Falla de Pala / Cargador", "Inasistencia de Operador", "Lluvia / Condición Climática", "Voladura / Tronadura Atrasada", "Atasco / Detención en Chancado", "Otra"]
        st.markdown("**Causas de Desviación / Imprevistos en Turno (Selección Múltiple):**")
        causas_seleccionadas = st.multiselect("", options=opciones_causales, default=[], placeholder="Elija opciones", label_visibility="collapsed")

        st.markdown("**Observaciones / Bitácora de Terreno:**")
        observaciones_turno = st.text_input("", value="", placeholder="Ej: CA321 fuera a las 11:00 hrs; PA622 detenida 45 min...", label_visibility="collapsed")

    with col_c2:
        st.markdown("#### 📊 Indicadores de Efectividad Operativa")
        adherencia_plan = (ton_reales / ton_plan * 100) if ton_plan > 0 else 0.0
        costo_real_usd = n_puestos_carguio * 441.0 * horas_turno + n_caex_activos * 290.0 * horas_turno + (diesel_real * precio_diesel)
        costo_real_ton = (costo_real_usd / ton_reales) if ton_reales > 0 else 0.0

        if adherencia_plan >= 95.0:
            st.markdown(f'<p class="adh-green-large">Adherencia al Plan de Mina: {fmt_num(adherencia_plan, 1)}%</p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<p class="adh-red-large">Adherencia al Plan de Mina: {fmt_num(adherencia_plan, 1)}%</p>', unsafe_allow_html=True)

        st.progress(min(adherencia_plan / 100.0, 1.0))
        
        if "prescripcion_aceptada" in df_lista_ag.columns:
            tasa_adopcion_val = df_lista_ag["prescripcion_aceptada"].mean() * 100
            col_adop1, col_adop2 = st.columns([1.5, 1])
            with col_adop1:
                st.metric("Tasa de Adopción Prescriptiva (Lean Mining)", f"{tasa_adopcion_val:.1f}%", delta="Banda de Eficiencia Lean")

        texto_causas = ", ".join(causas_seleccionadas) if causas_seleccionadas else "Sin imprevistos registrados"

        if adherencia_plan >= 98.0:
            st.success(f"🎯 **AGENDAMIENTO EXITOSO:** Cumplimiento del {fmt_num(adherencia_plan, 1)}% de la meta proyectada ({num_ag_selected}).")
        elif adherencia_plan >= 85.0:
            st.warning(f"⚠️ **CUMPLIMIENTO PARCIAL ({fmt_num(adherencia_plan, 1)}%):** Desviación menor atribuida a: {texto_causas}.")
        else:
            st.error(f"🚨 **DESVIACIÓN CRÍTICA ({fmt_num(adex_plan = ... if ... else ...)}%):** Impacto severo por eventos múltiples ({texto_causas}). Costo Real: ${fmt_num(costo_real_ton, 2)} USD/Ton.")
else:
    st.info("Aún no hay agendamientos guardados en la base de datos para conciliar.")

# ==============================================================================
# 17. HISTÓRICO DE AGENDAMIENTOS Y AUDITORÍA GERENCIAL DIRECTA CON GRÁFICOS
# ==============================================================================
st.markdown("---")
col_h1, col_h2 = st.columns([3, 1])

with col_h1:
    st.subheader("Histórico de Agendamientos y Auditoría Gerencial")

with col_h2:
    if st.session_state.get("user_id") == "mcepeda":
        if st.button("🗑 Borrar Histórico (Admin)", type="primary", use_container_width=True):
            borrar_historico_db()
            st.success("Histórico eliminado correctamente.")
            st.rerun()

conn = sqlite3.connect(DB_FILE)
df_hist = pd.read_sql_query("SELECT * FROM historico_agendamientos ORDER BY id DESC", conn)
conn.close()

if not df_hist.empty:
    rol_actual = st.session_state.get("rol_activo")
    usuario_actual = st.session_state.get("usuario_activo")

    if rol_actual in ["Administrador", "Gerente Operaciones / Evaluador"]:
        st.markdown("<h3 style='text-align: center;'>[EXCLUSIVO GERENCIA] Panel de control y Auditoría por períodos</h3>", unsafe_allow_html=True)

        c_f1, c_f2 = st.columns(2)
        with c_f1:
            supervisores_lista = ["Todos"] + list(df_hist["jefe_turno"].unique())
            sup_filtro = st.selectbox("👤 Seleccionar Jefe de Mina:", supervisores_lista)
        with c_f2:
            periodo_filtro = st.selectbox("📅 Seleccionar Período de Consolidación:", ["Semanal (Ciclo 7x7)", "Mensual", "Anual", "Histórico Completo"])

        df_gerencia = df_hist.copy()
        if sup_filtro != "Todos":
            df_gerencia = df_gerencia[df_gerencia["jefe_turno"] == sup_filtro]

        st.dataframe(df_gerencia, use_container_width=True)

        with st.expander(f"📈 Evaluación de Rendimiento Gerencial ({periodo_filtro}) — Supervisor: {sup_filtro}", expanded=True):
            if periodo_filtro == "Semanal (Ciclo 7x7)":
                dias_semana = [f"Día {i+1}" for i in range(7)]
                if len(df_gerencia) >= 7:
                    df_7dias = df_gerencia.iloc[:7].copy().iloc[::-1].reset_index(drop=True)
                    df_7dias["Dia_Turno"] = [f"Día {i+1} ({row['fecha_registro']})" for i, row in df_7dias.iterrows()]
                    ton_propuestas = df_7dias["ton_movidas"].values
                    ton_reales = (df_7dias["ton_movidas"] * np.random.uniform(0.92, 0.99, size=len(df_7dias))).values
                    mf_valores = df_7dias["match_factor"].values
                else:
                    n = len(df_gerencia)
                    dias_labels = [f"Día {i+1}" for i in range(7)]
                    ton_propuestas = np.zeros(7)
                    ton_reales = np.zeros(7)
                    mf_valores = np.zeros(7)
                    if n > 0:
                        df_rev = df_gerencia.iloc[::-1].reset_index(drop=True)
                        for i in range(min(n, 7)):
                            dias_labels[i] = f"Día {i+1} ({df_rev.loc[i, 'fecha_registro']})"
                            ton_propuestas[i] = df_rev.loc[i, "ton_movidas"]
                            ton_reales[i] = df_rev.loc[i, "ton_movidas"] * 0.95
                            mf_valores[i] = df_rev.loc[i, "match_factor"]

                    df_7dias = pd.DataFrame({"Dia_Turno": dias_labels, "ton_movidas": ton_propuestas, "match_factor": mf_valores})

                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(x=df_7dias["Dia_Turno"], y=ton_propuestas, name="Toneladas Propuestas (Plan)", marker_color="#0284C7", text=[f"{fmt_num(v, 0)} T" for v in ton_propuestas], textposition="auto"), secondary_y=False)
                fig.add_trace(go.Bar(x=df_7dias["Dia_Turno"], y=ton_reales, name="Toneladas Reales Logradas", marker_color="#10B981", text=[f"{fmt_num(v, 0)} T" for v in ton_reales], textposition="auto"), secondary_y=False)
                fig.add_trace(go.Scatter(x=df_7dias["Dia_Turno"], y=mf_valores, name="Match Factor Promedio", mode="lines+markers+text", line=dict(color="#F59E0B", width=3), marker=dict(size=9, color="#F59E0B"), text=[f"MF: {v:.2f}" for v in mf_valores], textposition="top center"), secondary_y=True)

                fig.update_layout(title_text="<b>Cumplimiento Operativo Diario y Match Factor (Ciclo Semanal 7x7)</b>", barmode="group", template="plotly_white", height=450, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(l=20, r=20, t=60, b=20))
                fig.update_xaxes(title_text="<b>Día de Turno de Agendamiento</b>")
                fig.update_yaxes(title_text="<b>Toneladas (Ton)</b>", secondary_y=False)
                fig.update_yaxes(title_text="<b>Match Factor (MF)</b>", secondary_y=True, range=[0, 1.5])
                st.plotly_chart(fig, use_container_width=True)

                tot_proyectado = sum(ton_propuestas)
                tot_real = sum(ton_reales)
                avg_mf = np.mean(mf_valores[mf_valores > 0]) if any(mf_valores > 0) else 0.0
                cumplimiento_ciclo = (tot_real / tot_proyectado * 100) if tot_proyectado > 0 else 0.0

                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                m_col1.metric("Ton Proyectadas (Ciclo)", f"{fmt_num(tot_proyectado, 0)} Ton")
                m_col2.metric("Ton Reales (Ciclo)", f"{fmt_num(tot_real, 0)} Ton")
                m_col3.metric("Cumplimiento Ciclo", f"{fmt_num(cumplimiento_ciclo, 1)}%")
                m_col4.metric("Match Factor Promedio", f"{fmt_num(avg_mf, 2)}")
            else:
                df_chart = pd.DataFrame({
                    "Agendamiento / Fecha": df_gerencia["num_agendamiento"] + " (" + df_gerencia["fecha_registro"] + ")",
                    "Toneladas Proyectadas (Target)": df_gerencia["ton_movidas"],
                    "Toneladas Reales Entregadas": df_gerencia["ton_movidas"] * 0.96,
                }).set_index("Agendamiento / Fecha")

                st.line_chart(df_chart, use_container_width=True)

                tot_proyectado = df_gerencia["ton_movidas"].sum()
                tot_opex = df_gerencia["opex_total_usd"].sum()
                avg_costo_ton = df_gerencia["costo_ton_usd"].mean()
                avg_mf = df_gerencia["match_factor"].mean()

                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                m_col1.metric("Total Ton Proyectadas", f"{fmt_num(tot_proyectado, 0)} Ton")
                m_col2.metric("OPEX Acumulado", f"${fmt_num(tot_opex, 2)} USD")
                m_col3.metric("Costo Promedio", f"${fmt_num(avg_costo_ton, 2)} USD/Ton")
                m_col4.metric("Match Factor Promedio", f"{fmt_num(avg_mf, 2)}")
    else:
        st.markdown(f"### 👤 **Control Operativo de Turno Actual — Supervisor:** `{usuario_actual}`")
        df_turno_hoy = df_hist[(df_hist["jefe_turno"] == usuario_actual) & (df_hist["fecha_registro"] == fecha_str)]

        if not df_turno_hoy.empty:
            st.dataframe(df_turno_hoy, use_container_width=True)
            st.success("📌 Mostrando únicamente el agendamiento activo de la jornada actual.")
