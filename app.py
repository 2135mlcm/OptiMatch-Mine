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
# 6. MOTOR DE SIMULACIÓN ANALÍTICA ESTOCÁSTICA MULTIMODELO
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
# 8. ESTILOS VISUALES MEJORADOS Y COMPACTACIÓN DE ESPACIOS
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
# 10. CARÁTULA Y BARRA LATERAL (SIDEBAR) REORGANIZADA COMPLETA
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

st.sidebar.header("🏢 Registro Operativo Mina")
nombre_mina = st.sidebar.text_input("Nombre de la Mina / Faena", value="Mina Atacama Norte")

num_agendamiento_auto = obtener_siguiente_agendamiento()
num_agendamiento = st.sidebar.text_input("N° de Agendamiento Correlativo", value=num_agendamiento_auto)

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

st.sidebar.markdown('<div class="amber-card">', unsafe_allow_html=True)
st.sidebar.markdown('<span class="selector-label-centered">RÉGIMEN Y GUARDIA DE TRABAJO</span>', unsafe_allow_html=True)
tipo_turno_sel = st.sidebar.selectbox("", ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"], key="select_regimen_box")
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"
st.sidebar.markdown("</div>", unsafe_allow_html=True)

st.sidebar.markdown('<div class="amber-card">', unsafe_allow_html=True)
st.sidebar.markdown('<span class="selector-label-centered">SELECCIONAR TURNO OPERATIVO</span>', unsafe_allow_html=True)
turno_seleccionado = st.sidebar.selectbox("", ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"], key="select_turno_box")
st.sidebar.markdown("</div>", unsafe_allow_html=True)

horas_turno = st.sidebar.number_input("Horas Efectivas Turno", value=10.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.header("⚙ Presets de Terreno y Granulometría")
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
    "No Metálicos / Canteras": {"razon": "Ton Brutas / Ton Roca Comercial", "costo": "USD / Ton Material", "val_razon": 1.1, "val_usd": 5.00},
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
        {"Item": 9, "Agendar": False, "Estado": "🟢 Disponible", "ID": "CA327", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 11800.0, "Operador": "Javier Fuentes", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 10, "Agendar": False, "Estado": "🟢 Disponible", "ID": "CA328", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 7600.0, "Operador": "Cristian Muñoz", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 11, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CA329", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 14500.0, "Operador": "Sin Asignar", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 12, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CA330", "Modelo": "CAT 785D", "Cap_Ton": 140.0, "Horómetro Entrada": 16200.0, "Operador": "Sin Asignar", "Rend_TonH": 320, "Consumo_LtsH": 65.0, "Costo_USDH": 380.00},
    ])

if "Cap_Ton" not in st.session_state.caex_df.columns:
    st.session_state.caex_df["Cap_Ton"] = 90.0

# ==============================================================================
# 12. TABLAS DE GESTIÓN DE FLOTA Y ALERTAS DE MANTENCIÓN (PESTAÑAS + TARJETAS)
# ==============================================================================
b64_logo = obtener_base64_img(LOGO_PATH) or obtener_base64_img("Logo_OptiMatch.png")
img_tag_logo = f'<img src="{b64_logo}" style="height: 32px; width: auto; vertical-align: middle; margin-right: 8px;">' if b64_logo else '<span style="font-size: 22px; vertical-align: middle; margin-right: 8px;">⛏️</span>'

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

MATRIZ_TALLER_MP = {
    "CAEX": {"horas_min": 48.0, "horas_max": 68.0, "perdida_df_min": 2.4, "perdida_df_max": 3.4},
    "Pala": {"horas_min": 72.0, "horas_max": 96.0, "perdida_df_min": 3.6, "perdida_df_max": 4.8},
    "Cargador": {"horas_min": 48.0, "horas_max": 59.0, "perdida_df_min": 2.4, "perdida_df_max": 2.9},
}

def evaluar_alerta_equipo(id_equipo, horometro_actual, tipo_equipo="CAEX"):
    intervalo_base = 250.0
    horas_para_pm = intervalo_base - (horometro_actual % intervalo_base)
    proximo_horometro = horometro_actual + horas_para_pm
    
    if proximo_horometro % 2000 == 0:
        tipo_pm = "PM 2.000 hrs (Overhaul)"
    elif proximo_horometro % 1000 == 0:
        tipo_pm = "PM 1.000 hrs (Tren Potencia)"
    elif proximo_horometro % 500 == 0:
        tipo_pm = "PM 500 hrs (Aceites / Filtros)"
    else:
        tipo_pm = "PM 250 hrs (Inspección / Engrase)"
        
    if horas_para_pm <= 0:
        estado_alerta = "🔴 PAUTA VENCIDA"
    elif horas_para_pm <= 20.0:
        estado_alerta = f"⚠️ PM CERCANO ({horas_para_pm:.1f}h)"
    else:
        estado_alerta = "🟢 En Regla"
        
    return {
        "ID Equipo": id_equipo,
        "Horómetro Actual (h)": horometro_actual,
        "Faltan (h)": round(horas_para_pm, 1),
        "Próxima Pauta": tipo_pm,
        "Estado PM": estado_alerta
    }

st.markdown("---")
st.subheader("🛠️ Monitoreo Individual de Mantenimiento y Alertas de Taller")

tab_maint1, tab_maint2 = st.tabs(["📋 Estado de Pautas por Equipo", "📊 Impacto en Disponibilidad Física (DF)"])

with tab_maint1:
    alertas_caex = [evaluar_alerta_equipo(row["ID"], row["Horómetro Entrada"], "CAEX") for _, row in ed_caex.iterrows() if row["Estado"] == "🟢 Disponible"]
    alertas_palas = [evaluar_alerta_equipo(row["ID"], row["Horómetro Entrada"], "Pala") for _, row in ed_palas.iterrows() if row["Estado"] == "🟢 Disponible"]
    alertas_cf = [evaluar_alerta_equipo(row["ID"], row["Horómetro Entrada"], "Cargador") for _, row in ed_cf.iterrows() if row["Estado"] == "🟢 Disponible"]

    todos_equipos = alertas_caex + alertas_palas + alertas_cf
    criticos = [eq for eq in todos_equipos if "⚠️" in eq["Estado PM"] or "🔴" in eq["Estado PM"]]

    if criticos:
        for eq in criticos:
            st.markdown(f"""
                <div style="
                    background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
                    border-left: 5px solid #F59E0B;
                    border-top: 1px solid #334155;
                    border-right: 1px solid #334155;
                    border-bottom: 1px solid #334155;
                    border-radius: 10px;
                    padding: 8px 14px;
                    margin-bottom: 12px;
                    box-shadow: 0px 4px 12px rgba(245, 158, 11, 0.15);
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                ">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 20px;">⚠️</span>
                        <div>
                            <span style="color: #F59E0B; font-weight: 900; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; display: block;">
                                ALERTA PREVENTIVA DE TALLER EN VIVO — EQUIPO {eq['ID Equipo']}
                            </span>
                            <span style="color: #F8FAFC; font-size: 12px; font-weight: 600;">
                                Próxima pauta: <b style="color: #38BDF8;">{eq['Próxima Pauta']}</b> | Horómetro actual: <b>{eq['Horómetro Actual (h)']} hrs</b>
                            </span>
                        </div>
                    </div>
                    <div style="
                        background-color: #FEF3C7;
                        border: 1px solid #F59E0B;
                        color: #92400E;
                        padding: 4px 10px;
                        border-radius: 6px;
                        font-weight: 900;
                        font-size: 12px;
                        white-space: nowrap;
                    ">
                        ⏳ FALTAN {eq['Faltan (h)']} HRS
                    </div>
                </div>
            """, unsafe_allow_html=True)

    subtab_caex, subtab_palas, subtab_cf = st.tabs([
        f"🚚 Flota CAEX ({len(alertas_caex)})", 
        f"🏗️ Palas de Carguío ({len(alertas_palas)})", 
        f"🚜 Cargadores Frontales ({len(alertas_cf)})"
    ])

    def renderizar_tarjetas_equipo(lista_equipos):
        if not lista_equipos:
            st.info("No hay equipos activos agendados en esta categoría.")
            return

        cols = st.columns(3)
        for idx, eq in enumerate(lista_equipos):
            col_target = cols[idx % 3]
            es_critico = "⚠️" in eq["Estado PM"] or "🔴" in eq["Estado PM"]
            
            border_color = "#F59E0B" if es_critico else "#CBD5E1"
            bg_badge = "#FEF3C7" if es_critico else "#D1FAE5"
            text_badge = "#92400E" if es_critico else "#065F46"
            
            with col_target:
                st.markdown(f"""
                    <div style="
                        background-color: #FFFFFF;
                        border: 1.5px solid {border_color};
                        border-radius: 10px;
                        padding: 10px 12px;
                        margin-bottom: 10px;
                        box-shadow: 0px 2px 6px rgba(0,0,0,0.04);
                    ">
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #E2E8F0; padding-bottom: 6px; margin-bottom: 8px;">
                            <span style="font-size: 15px; font-weight: 900; color: #0F172A;">🆔 {eq['ID Equipo']}</span>
                            <span style="background-color: {bg_badge}; color: {text_badge}; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 4px;">
                                {eq['Estado PM']}
                            </span>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 11px;">
                            <div>
                                <span style="color: #64748B; font-weight: 700; display: block;">HORÓMETRO ACTUAL</span>
                                <span style="color: #0F172A; font-weight: 900; font-size: 13px;">{eq['Horómetro Actual (h)']} hrs</span>
                            </div>
                            <div>
                                <span style="color: #64748B; font-weight: 700; display: block;">FALTA PARA PM</span>
                                <span style="color: {'#DC2626' if es_critico else '#0284C7'}; font-weight: 900; font-size: 13px;">{eq['Faltan (h)']} hrs</span>
                            </div>
                        </div>
                        <div style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed #E2E8F0;">
                            <span style="color: #64748B; font-weight: 700; font-size: 10px; display: block;">PRÓXIMA PAUTA DE MANTENCIÓN</span>
                            <span style="color: #0F172A; font-weight: 800; font-size: 11px;">🛠️ {eq['Próxima Pauta']}</span>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

    with subtab_caex:
        renderizar_tarjetas_equipo(alertas_caex)

    with subtab_palas:
        renderizar_tarjetas_equipo(alertas_palas)

    with subtab_cf:
        renderizar_tarjetas_equipo(alertas_cf)

with tab_maint2:
    st.markdown("#### 📊 Balance de Disponibilidad Física (Pérdidas MP vs. Regla 80/20)")
    col_m1, col_m2, col_m3 = st.columns(3)

    horas_mp_caex_prom = (MATRIZ_TALLER_MP["CAEX"]["horas_min"] + MATRIZ_TALLER_MP["CAEX"]["horas_max"]) / 2.0
    perdida_mp_prom = (MATRIZ_TALLER_MP["CAEX"]["perdida_df_min"] + MATRIZ_TALLER_MP["CAEX"]["perdida_df_max"]) / 2.0
    df_maxima_teorica = 100.0 - perdida_mp_prom

    col_m1.metric("Pérdida Directa MP (Taller)", f"{perdida_mp_prom:.1f}%", delta=f"{horas_mp_caex_prom:.0f} hrs en taller / 2.000h")
    col_m2.metric("DF Máxima Teórica (Solo MP)", f"{df_maxima_teorica:.1f}%", delta="Escenario Ideal Taller")
    col_m3.metric("DF Real Operativa Terreno", f"{disponibilidad_fisica_val:.1f}%", delta=f"Impacto MC / LOTO: {max(0.0, df_maxima_teorica - disponibilidad_fisica_val):.1f}%", delta_color="normal" if disponibilidad_fisica_val >= 83 else "inverse")

    st.info("💡 **Nota de Gestión de Activos:** La diferencia entre la Disponibilidad Máxima Teórica (96,6% – 97,6%) y la DF Real de Terreno (83% – 88%) se debe al **Mantenimiento Correctivo (MC)** imprevisto y a **demoras operacionales en taller** (lavado, traslado, repuestos y tarjeteo de seguridad LOTO).")

# ==============================================================================
# 13. EJECUCIÓN DEL MOTOR DE SIMULACIÓN Y CÁLCULOS UNIFICADOS
# ==============================================================================
palas_activas = ed_palas[(ed_palas["Agendar"] == True) & (ed_palas["Estado"] == "🟢 Disponible")]
cf_activos = ed_cf[(ed_cf["Agendar"] == True) & (ed_cf["Estado"] == "🟢 Disponible")]
caex_activos = ed_caex[(ed_caex["Agendar"] == True) & (ed_caex["Estado"] == "🟢 Disponible")]

n_puestos_carguio = max(1, len(palas_activas) + len(cf_activos))
n_caex_activos = len(caex_activos)

res_sim = ejecutar_simulacion_analitica(
    caex_activos_df=caex_activos,
    n_palas=n_puestos_carguio,
    cv=0.3,
    duracion_horas=horas_turno,
    costo_pala_h=441.0,
    costo_camion_h=290.0,
    fl_factor=fl_valor,
    merma_base_pct=merma_base_valor,
    perfil_rampa_key=perfil_rampa_sel,
    distancia_km=distancia_acarreo_km,
    vel_cargado_base=vel_cargado_kmh,
    vel_vacio_base=vel_vacio_kmh,
)

match_factor = res_sim["MF"]
tonelaje_cargado = res_sim["ton_cargadas"]
tonelaje_merma = res_sim["ton_merma"]
merma_pct_real = res_sim["merma_pct"]
tonelaje_efectivo = res_sim["ton_totales"]
litros_diesel_turno = res_sim["litros_totales"]
consumo_especifico_lts_ton = res_sim["consumo_especifico_l_ton"]
costo_opex_total_turno = res_sim["costo_opex"]
costo_unitario_ton = res_sim["costo_unitario_usd_ton"]
tiempo_cola_promedio = res_sim["cola_min"]
tiempo_ciclo_efectivo_min = res_sim["t_ciclo_min"]
vel_cargado_efectiva_kmh = res_sim["vel_cargado_efectiva"]
t_ida_min = res_sim["t_ida_min"]
t_retorno_min = res_sim["t_retorno_min"]

costo_diesel_turno = litros_diesel_turno * precio_diesel
ingreso_bruto_usd = tonelaje_efectivo * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - costo_opex_total_turno
costo_diesel_por_ton = (costo_diesel_turno / tonelaje_efectivo) if tonelaje_efectivo > 0 else 0

emisiones_co2_kg = litros_diesel_turno * 2.68
co2_por_ton = (emisiones_co2_kg / tonelaje_efectivo) if tonelaje_efectivo > 0 else 0.0
vueltas_totales_meta = int((horas_turno * 60.0 / tiempo_ciclo_efectivo_min) * max(1, n_caex_activos))

prescripcion_aceptada_val = 1 if (0.92 <= match_factor <= 1.08) else 0

if st.sidebar.button("🔒 CIERRE Y GUARDADO EN BD", use_container_width=True):
    guardar_agendamiento_db(
        num_agendamiento, fecha_str, hora_str, nombre_mina, turno_seleccionado, regimen_guardia,
        st.session_state.get("usuario_activo", "Mauricio L. Cepeda Mondaca"), tonelaje_cargado,
        tonelaje_efectivo, tonelaje_merma, litros_diesel_turno, costo_diesel_turno,
        costo_opex_total_turno, costo_unitario_ton, beneficio_neto_usd, match_factor,
        disponibilidad_fisica_val, fl_valor, prescripcion_aceptada_val
    )
    st.sidebar.success(f"✅ Agendamiento {num_agendamiento} guardado exitosamente.")
    st.session_state.autenticado = False
    st.rerun()

# ==============================================================================
# 14. DASHBOARD DE RESULTADOS Y CONTROL VISUAL HEADER
# ==============================================================================
st.markdown("---")
st.header(f"📈 Resumen de Agendamiento Pre-Turno: {num_agendamiento}")

b64_logo_atacama = obtener_base64_img(LOGO_ATACAMA_PATH)

if b64_logo_atacama:
    faena_header_html = f"""
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
            <img src="{b64_logo_atacama}" style="height: 38px; width: auto; vertical-align: middle; object-fit: contain;">
            <h3 style="margin: 0; padding: 0; color: #0F172A; font-size: 16px; font-weight: 800; display: inline-block;">
                Faena: {nombre_mina} | Fecha y Hora: {nombre_dia_actual}, {fecha_str} {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})
            </h3>
        </div>
    """
else:
    faena_header_html = f"""
        <h3 style="margin: 0 0 10px 0; padding: 0; color: #0F172A; font-size: 16px; font-weight: 800;">
            Faena: {nombre_mina} | Fecha y Hora: {nombre_dia_actual}, {fecha_str} {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})
        </h3>
    """

st.markdown(faena_header_html, unsafe_allow_html=True)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Disp. Física (DF)", f"{disponibilidad_fisica_val:.1f}%", delta=f"{caex_disponibles}/{total_caex} CAEX Activos")
k2.metric("Match Factor (MF)", f"{fmt_num(match_factor, 2)}", delta="Banda Lean OK" if 0.92 <= match_factor <= 1.08 else "Fuera de Rango")
k3.metric("Ton Entregadas Netas", f"{fmt_num(tonelaje_efectivo, 0)} Ton", delta=f"Merma: {merma_pct_real:.1f}% ({fmt_num(tonelaje_merma, 0)}T)")
k4.metric("Consumo Diésel Total", f"{fmt_num(litros_diesel_turno, 0)} Lts")
k5.metric("Costo Real/Ton", f"${fmt_num(costo_unitario_ton, 2)} USD/Ton")
k6.metric("Beneficio Neto", f"${fmt_num(beneficio_neto_usd, 2)} USD")
