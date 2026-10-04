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
    
    .amber-card {
        background-color: #1E293B; border: 2px solid #F59E0B; border-radius: 12px; padding: 8px 12px !important;
        margin-bottom: 8px !important; box-shadow: 0px 0px 10px rgba(245, 158, 11, 0.25);
    }
    
    div[data-baseweb="select"], div[data-baseweb="select"] *, div[data-baseweb="select"] > div {
        background-color: #1E293B !important; color: #FFFFFF !important;
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
        border-radius: 10px !important; padding: 10px 12px !important; text-align: center !important;
    }

    .mf-label { font-size: 16px !important; font-weight: 800 !important; color: #0F172A !important; margin-bottom: 2px !important; }
    .mf-value { font-size: 34px !important; font-weight: 900 !important; color: #0284C7 !important; margin-top: 0px !important; }
    .highlight-red-large { color: #DC2626 !important; font-size: 14px !important; font-weight: 800 !important; margin-bottom: 4px !important; }
    .adh-green-large { color: #10B981 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }
    .adh-red-large { color: #EF4444 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }

    hr { margin-top: 12px !important; margin-bottom: 12px !important; }
    </style>
""", unsafe_allow_html=True)

LOGO_PATH = "Logo_OptiMatch.png"
LOGO_ATACAMA_PATH = "Logo_Atacama_Norte.png"

# ==============================================================================
# 9. SISTEMA DE AUTENTICACIÓN PRIVADO
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
                <div style="text-align: center; background-color: #0F172A; padding: 25px; border-radius: 16px; border: 2px solid #F59E0B;">
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
# 10. CARÁTULA Y BARRA LATERAL (SIDEBAR) REORGANIZADA
# ==============================================================================
if os.path.exists(LOGO_PATH):
    c_hdr1, c_hdr2, c_hdr3 = st.columns([1, 1.2, 1])
    with c_hdr2:
        st.image(LOGO_PATH, use_container_width=True)

st.markdown("""
    <div class="main-title-card">
        <h1 style="color: #0F172A; margin: 0; font-size: 24px; font-weight: 900;">OptiMatch Mine — Control Prescriptivo v3.0</h1>
        <p style="color: #0284C7; margin: 2px 0 0 0; font-size: 12px; font-weight: 800; text-transform: uppercase;">
            SISTEMA DE SOPORTE A LA DECISIÓN PRE-TURNO PARA LA MEDIANA MINERÍA
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

turno_seleccionado = st.sidebar.selectbox("Seleccionar Turno Operativo", ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"], key="select_turno_box")
regimen_guardia = f"Turno 7x7 ({nombre_dia_actual})"
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

tc_mercado, diesel_mercado = obtener_indicadores_mercado()
precio_diesel = st.sidebar.number_input("Precio Diésel (USD / Litro Contrato)", value=float(diesel_mercado), step=0.01)
tipo_mineral = "Cobre (Cu)"
valor_ton_usd = 15.50

distancia_acarreo_km = st.sidebar.number_input("Distancia Promedio Acarreo (km)", value=3.2, step=0.1)
vel_cargado_kmh = st.sidebar.number_input("Velocidad Base Ida (km/h)", value=18.0, step=1.0)
vel_vacio_kmh = st.sidebar.number_input("Velocidad Retorno Vacío (km/h)", value=30.0, step=1.0)

# ==============================================================================
# 11. INICIALIZACIÓN DE FLOTA MULTIMODELO
# ==============================================================================
if "palas_df" not in st.session_state:
    st.session_state.palas_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "PA622", "Modelo": "Liebherr R9200", "Horómetro Entrada": 14250.0, "Operador": "Carlos Araya", "Rend_TonH": 1216, "Consumo_LtsH": 120.0, "Costo_USDH": 441.00},
        {"Item": 2, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "PA623", "Modelo": "CAT 6020B", "Horómetro Entrada": 11800.5, "Operador": "Sin Asignar", "Rend_TonH": 1216, "Consumo_LtsH": 115.0, "Costo_USDH": 420.00},
    ])

if "cf_df" not in st.session_state:
    st.session_state.cf_df = pd.DataFrame([
        {"Item": 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "CF437", "Modelo": "Komatsu WA900", "Horómetro Entrada": 8400.0, "Operador": "Sin Asignar", "Rend_TonH": 685, "Consumo_LtsH": 75.0, "Costo_USDH": 342.50},
    ])

if "caex_df" not in st.session_state:
    st.session_state.caex_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA319", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 12450.0, "Operador": "Pedro Morales", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 2, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA320", "Modelo": "Komatsu HD465-7", "Cap_Ton": 60.0, "Horómetro Entrada": 11200.5, "Operador": "Luis Tapia", "Rend_TonH": 143, "Consumo_LtsH": 35.0, "Costo_USDH": 250.00},
        {"Item": 3, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA321", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 12241.5, "Operador": "Andrés Castro", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
        {"Item": 4, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA322", "Modelo": "Komatsu HD785-7", "Cap_Ton": 90.0, "Horómetro Entrada": 15300.2, "Operador": "Diego Rojas", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
    ])

# ==============================================================================
# 12. TABLAS DE GESTIÓN Y ALERTAS PM
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
    st.markdown("### Pala de Carguío")
    ed_palas = st.data_editor(st.session_state.palas_df, hide_index=True, key="editor_palas")
    st.session_state.palas_df = reindexar_flota(ed_palas)

with col_t2:
    st.markdown("### Cargador Frontal")
    ed_cf = st.data_editor(st.session_state.cf_df, hide_index=True, key="editor_cf")
    st.session_state.cf_df = reindexar_flota(ed_cf)

with col_t3:
    st.markdown("### Camión CAEX")
    ed_caex = st.data_editor(st.session_state.caex_df, hide_index=True, key="editor_caex")
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
            es_critico = "⚠️️" in eq["Estado PM"] or "🔴" in eq["Estado PM"]
            
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
palas_activas = st.session_state.palas_df[(st.session_state.palas_df["Agendar"] == True) & (st.session_state.palas_df["Estado"] == "🟢 Disponible")]
caex_activos = st.session_state.caex_df[(st.session_state.caex_df["Agendar"] == True) & (st.session_state.caex_df["Estado"] == "🟢 Disponible")]

n_puestos_carguio = max(1, len(palas_activas))
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
costo_opex_total_turno = res_sim["costo_opex"]
costo_unitario_ton = res_sim["costo_unitario_usd_ton"]

ingreso_bruto_usd = tonelaje_efectivo * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - costo_opex_total_turno

if st.sidebar.button("🔒 CIERRE Y GUARDADO EN BD", use_container_width=True):
    guardar_agendamiento_db(
        num_agendamiento, fecha_str, "08:00", nombre_mina, turno_seleccionado, regimen_guardia,
        st.session_state.get("usuario_activo", "Mauricio L. Cepeda Mondaca"), tonelaje_cargado,
        tonelaje_efectivo, tonelaje_merma, litros_diesel_turno, litros_diesel_turno * precio_diesel,
        costo_opex_total_turno, costo_unitario_ton, beneficio_neto_usd, match_factor,
        disponibilidad_fisica_val, fl_valor, 1
    )
    st.sidebar.success(f"✅ Agendamiento {num_agendamiento} guardado exitosamente.")
    st.session_state.autenticado = False
    st.rerun()

# ==============================================================================
# 14. DASHBOARD DE RESULTADOS
# ==============================================================================
st.markdown("---")
st.header(f"📈 Resumen de Agendamiento Pre-Turno: {num_agendamiento}")

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Disp. Física (DF)", f"{disponibilidad_fisica_val:.1f}%")
k2.metric("Match Factor (MF)", f"{fmt_num(match_factor, 2)}")
k3.metric("Ton Entregadas Netas", f"{fmt_num(tonelaje_efectivo, 0)} Ton")
k4.metric("Consumo Diésel Total", f"{fmt_num(litros_diesel_turno, 0)} Lts")
k5.metric("Costo Real/Ton", f"${fmt_num(costo_unitario_ton, 2)} USD/Ton")
k6.metric("Beneficio Neto", f"${fmt_num(beneficio_neto_usd, 2)} USD")
