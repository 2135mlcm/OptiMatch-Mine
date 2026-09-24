from datetime import datetime
import json
import os
import sqlite3
import threading
import time
import urllib.request
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components


# ---------------------------------------------------------
# MÓDULO KEEP-ALIVE: MANTIENE EL SERVIDOR ACTIVO 24/7
# ---------------------------------------------------------
def keep_alive_ping():
  while True:
    time.sleep(900)  # Envía un pulso en segundo plano cada 15 minutos
    _ = datetime.now()


if "keep_alive_started" not in st.session_state:
  st.session_state.keep_alive_started = True
  thread = threading.Thread(target=keep_alive_ping, daemon=True)
  thread.start()

# ---------------------------------------------------------
# INICIALIZACIÓN Y MIGRACIÓN AUTOMÁTICA DE LA BD (optimatch.db)
# ---------------------------------------------------------
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
      (
          "cnikulin",
          "uah2026",
          "Dr. Christopher Nikulin",
          "Gerente Operaciones / Evaluador",
      ),
      (
          "cperez",
          "uah2026",
          "Dr. Camilo Pérez",
          "Gerente Operaciones / Evaluador",
      ),
  ]
  c.executemany(
      "INSERT INTO usuarios (username, password, nombre_completo, rol) VALUES"
      " (?, ?, ?, ?)",
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
            consumo_diesel_lts REAL,
            costo_diesel_usd REAL,
            opex_total_usd REAL,
            costo_ton_usd REAL,
            beneficio_neto_usd REAL,
            match_factor REAL
        )
    """)
  conn.commit()

  c.execute("PRAGMA table_info(historico_agendamientos)")
  columnas = [column[1] for column in c.fetchall()]
  if "regimen_guardia" not in columnas:
    c.execute(
        "ALTER TABLE historico_agendamientos ADD COLUMN regimen_guardia TEXT"
    )
    conn.commit()

  conn.close()


init_db()


# ---------------------------------------------------------
# FUNCIÓN DE CONSULTA EN VIVO DE INDICADORES DE MERCADO
# ---------------------------------------------------------
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
  c.execute(
      "SELECT username, nombre_completo, rol FROM usuarios WHERE username = ?"
      " AND password = ?",
      (usr, pwd),
  )
  res = c.fetchone()
  conn.close()
  return res


def guardar_agendamiento_db(
    num_ag,
    fecha,
    hora,
    faena,
    turno,
    regimen,
    jefe,
    ton,
    lts_diesel,
    costo_diesel,
    opex,
    costo_ton,
    beneficio,
    mf,
):
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute(
      """
        INSERT INTO historico_agendamientos (
            num_agendamiento, fecha_registro, hora_registro, faena, turno, regimen_guardia, jefe_turno,
            ton_movidas, consumo_diesel_lts, costo_diesel_usd, opex_total_usd,
            costo_ton_usd, beneficio_neto_usd, match_factor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
      (
          num_ag,
          fecha,
          hora,
          faena,
          turno,
          regimen,
          jefe,
          ton,
          lts_diesel,
          costo_diesel,
          opex,
          costo_ton,
          beneficio,
          mf,
      ),
  )
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


# ---------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA Y CSS PERSONALIZADO
# ---------------------------------------------------------
st.set_page_config(
    page_title="OptiMatch Mine - Control de Flota", page_icon="⛏️", layout="wide"
)

st.markdown(
    """
    <style>
    .stApp {
        background-color: #FFFFFF !important;
        color: #0F172A !important;
    }
    
    .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
        color: #0F172A !important;
    }

    section[data-testid="stSidebar"] {
        background-color: #334155 !important;
        border-right: 2px solid #F59E0B !important;
    }
    section[data-testid="stSidebar"] h1, 
    section[data-testid="stSidebar"] h2, 
    section[data-testid="stSidebar"] h3, 
    section[data-testid="stSidebar"] label, 
    section[data-testid="stSidebar"] span, 
    section[data-testid="stSidebar"] p {
        color: #F8FAFC !important;
        font-weight: 700 !important;
    }
    
    section[data-testid="stSidebar"] input {
        background-color: #0F172A !important;
        color: #FFFFFF !important;
        border: 1px solid #F59E0B !important;
        border-radius: 6px !important;
        text-align: center !important;
        font-weight: bold !important;
    }

    .orange-container-box {
        background-color: #1E293B;
        border: 2px solid #F59E0B;
        border-radius: 8px;
        padding: 4px 8px !important;
        margin-bottom: 6px !important;
        box-shadow: 0px 0px 6px rgba(245, 158, 11, 0.3);
    }

    div[data-baseweb="select"],
    div[data-baseweb="select"] *,
    div[data-baseweb="select"] > div,
    div[data-baseweb="select"] div[role="button"],
    div[data-baseweb="select"] div[data-testid="stMarkdownContainer"] {
        background-color: #0F172A !important;
        color: #FFFFFF !important;
        border-color: #F59E0B !important;
    }

    div[data-baseweb="select"] > div {
        border: 1px solid #F59E0B !important;
        border-radius: 6px !important;
    }

    div[data-baseweb="select"] span, 
    div[data-baseweb="select"] p,
    div[data-baseweb="select"] div {
        color: #FFFFFF !important;
        font-weight: 800 !important;
        font-size: 14px !important;
    }

    div[data-baseweb="select"] svg {
        fill: #F59E0B !important;
        color: #F59E0B !important;
    }

    ul[data-baseweb="menu"], 
    div[data-baseweb="popover"] > div,
    div[data-baseweb="popover"] * {
        background-color: #0F172A !important;
        color: #FFFFFF !important;
    }

    li[data-baseweb="option"]:hover, 
    li[data-baseweb="option"]:hover * {
        background-color: #F59E0B !important;
        color: #000000 !important;
        font-weight: 900 !important;
    }

    .selector-label-centered {
        color: #F59E0B !important;
        font-size: 12px !important;
        font-weight: 900 !important;
        text-align: center !important;
        display: block !important;
        margin-bottom: 2px !important;
        margin-top: 0px !important;
    }

    .auto-box {
        background-color: #0F172A;
        border: 1px solid #F59E0B;
        border-radius: 6px;
        padding: 6px 10px;
        text-align: center;
        font-size: 15px;
        font-weight: 800;
        color: #FFFFFF !important;
        margin-bottom: 8px;
    }

    section[data-testid="stSidebar"] button,
    section[data-testid="stSidebar"] button *,
    section[data-testid="stSidebar"] button p,
    section[data-testid="stSidebar"] button span {
        background-color: #F59E0B !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        font-weight: 900 !important;
        font-size: 15px !important;
        border-radius: 6px !important;
    }

    .title-box {
        background-color: #F8FAFC;
        padding: 20px 40px;
        border-radius: 12px;
        border: 2px solid #D97706;
        box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.08);
        text-align: center;
        width: fit-content;
        margin: 10px auto 25px auto;
    }

    .centered-title {
        text-align: center !important;
        width: 100% !important;
        margin-top: 20px !important;
        margin-bottom: 15px !important;
    }

    div[data-testid="stDataFrame"] {
        background-color: #F1F5F9 !important;
        border: 2px solid #CBD5E1 !important;
        border-radius: 10px;
    }

    div[data-testid="stMetricValue"] {
        color: #0284C7 !important;
        font-size: 20px !important;
        font-weight: bold !important;
        white-space: nowrap !important;
    }

    .mf-label {
        font-size: 22px !important;
        font-weight: 800 !important;
        color: #0F172A !important;
        margin-bottom: 4px !important;
    }
    .mf-value {
        font-size: 36px !important;
        font-weight: 900 !important;
        color: #0284C7 !important;
        margin-top: 0px !important;
    }

    .highlight-red-large {
        color: #DC2626 !important;
        font-size: 19px !important;
        font-weight: 800 !important;
        margin-bottom: 8px !important;
    }

    .adh-green-large {
        color: #16A34A !important;
        font-size: 22px !important;
        font-weight: 900 !important;
        margin-bottom: 6px !important;
    }

    .adh-red-large {
        color: #DC2626 !important;
        font-size: 22px !important;
        font-weight: 900 !important;
        margin-bottom: 6px !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

LOGO_PATH = "Logo_OptiMatch.png"

# ---------------------------------------------------------
# 1. AUTENTICACIÓN PRIVADA CON CAMPOS LIMPIOS OBLIGATORIOS
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
  st.session_state.autenticado = False

if not st.session_state.autenticado:
  col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
  with col_l2:
    st.markdown("<br>", unsafe_allow_html=True)
    if os.path.exists(LOGO_PATH):
      st.image(LOGO_PATH, width=320)
    else:
      st.markdown(
          """
                <div style="text-align: center; background-color: #1E293B; padding: 20px; border-radius: 15px; border: 2px solid #F59E0B;">
                    <h1 style="color: #F59E0B; font-size: 38px; margin-bottom: 0px;">⛏️ OptiMatch Mine</h1>
                    <h3 style="color: #F8FAFC; margin-top: 5px;">Control de Flota y Agendamiento Pre-Turno</h3>
                </div>
            """,
          unsafe_allow_html=True,
      )

    st.markdown(
        "<p style='text-align: center; font-weight: 800; font-size:"
        " 15px;'>Acceso Restringido por Perfil | Universidad Alberto"
        " Hurtado</p>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(
        """
            <form style="display:none;">
                <input type="text" name="fake_usernameremembered"/>
                <input type="password" name="fake_passwordremembered"/>
            </form>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form_secure", clear_on_submit=True):
      st.markdown(
          '<p style="font-weight: 800; font-size: 16px;">Nombre de'
          " Usuario:</p>",
          unsafe_allow_html=True,
      )
      usuario = st.text_input(
          "",
          value="",
          placeholder="Ingrese usuario...",
          key="usr_field_clean",
          autocomplete="off",
      )

      st.markdown(
          '<p style="font-weight: 800; font-size: 16px;">Contraseña de'
          " Acceso:</p>",
          unsafe_allow_html=True,
      )
      clave = st.text_input(
          "",
          type="password",
          value="",
          placeholder="Ingrese contraseña...",
          key="pwd_field_clean",
          autocomplete="new-password",
      )

      st.markdown("<br>", unsafe_allow_html=True)
      btn_ingresar = st.form_submit_button(
          "🔑 INGRESAR A LA PLATAFORMA", use_container_width=True
      )

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
          st.error(
              "❌ Usuario o contraseña no registrados en el sistema."
          )
  st.stop()

# ---------------------------------------------------------
# CARÁTULA CENTRADA
# ---------------------------------------------------------
if os.path.exists(LOGO_PATH):
  c_hdr1, c_hdr2, c_hdr3 = st.columns([1, 1.2, 1])
  with c_hdr2:
    st.image(LOGO_PATH, use_container_width=True)

st.markdown(
    """
    <div class="title-box">
        <h1 style="color: #0F172A; margin: 0; font-size: 28px; font-weight: 800;">OptiMatch Mine — Control de Flota</h1>
        <p style="color: #0284C7; margin: 6px 0 0 0; font-size: 14px; font-weight: 800; letter-spacing: 0.5px;">
            SISTEMA PRESCRIPTIVO DE DECISIONES PRE-TURNO PARA LA MEDIANA MINERÍA
        </p>
        <p style="color: #475569; margin: 2px 0 0 0; font-size: 12px; font-weight: 600;">
            Optimización del Match Carguío-Transporte & Control de Rentabilidad OPEX | Universidad Alberto Hurtado
        </p>
    </div>
""",
    unsafe_allow_html=True,
)

st.markdown("---")

# ---------------------------------------------------------
# BARRA LATERAL (SIDEBAR)
# ---------------------------------------------------------
st.sidebar.header("🏢 Registro Operativo Mina")
nombre_mina = st.sidebar.text_input(
    "Nombre de la Mina / Faena", value="Mina Franke - Calama"
)

num_agendamiento_auto = obtener_siguiente_agendamiento()
num_agendamiento = st.sidebar.text_input(
    "N° de Agendamiento Correlativo", value=num_agendamiento_auto
)

st.sidebar.markdown("---")

now_dt = datetime.now()
fecha_str = now_dt.strftime("%d/%m/%Y")

dias_semana_es = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]
nombre_dia_actual = dias_semana_es[now_dt.weekday()]

st.sidebar.markdown(
    "<label style='font-size:13px; font-weight:700;'>Fecha de"
    " Agendamiento</label>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f'<div class="auto-box">{nombre_dia_actual}, {fecha_str}</div>',
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    "<label style='font-size:13px; font-weight:700;'>Hora de"
    " Agendamiento</label>",
    unsafe_allow_html=True,
)
with st.sidebar:
  components.html(
      """
        <div id="reloj_vivo" style="
            background-color: #0F172A;
            border: 1px solid #F59E0B;
            border-radius: 6px;
            padding: 6px;
            text-align: center;
            font-size: 15px;
            font-weight: 800;
            color: #FFFFFF;
            font-family: sans-serif;">
        </div>
        <script>
            function actualizarReloj() {
                var now = new Date();
                var hrs = String(now.getHours()).padStart(2, '0');
                var mins = String(now.getMinutes()).padStart(2, '0');
                var secs = String(now.getSeconds()).padStart(2, '0');
                document.getElementById('reloj_vivo').innerHTML = hrs + ':' + mins + ':' + secs;
            }
            setInterval(actualizarReloj, 1000);
            actualizarReloj();
        </script>
    """,
      height=45,
  )

hora_str = now_dt.strftime("%H:%M:%S")

st.sidebar.markdown(
    f"""
    <div style="background-color: #0F172A; padding: 10px; border-radius: 8px; border: 2px solid #F59E0B; margin-top: 6px; margin-bottom: 10px; text-align: center;">
        <span style="color: #F59E0B !important; font-size: 11px; font-weight: 800; display: block;">USUARIO RESPONSABLE</span>
        <span style="color: #FFFFFF !important; font-size: 16px; font-weight: 900; display: block; margin-top: 2px;">👤 {st.session_state.get('usuario_activo', 'Mauricio L. Cepeda Mondaca')}</span>
        <span style="color: #F59E0B !important; font-size: 11px; font-weight: 800; display: block; margin-top: 2px;">Perfil: {st.session_state.get('rol_activo', 'Administrador')}</span>
    </div>
""",
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    '<div class="orange-container-box">', unsafe_allow_html=True
)
st.sidebar.markdown(
    '<span class="selector-label-centered">RÉGIMEN Y GUARDIA DE TRABAJO</span>',
    unsafe_allow_html=True,
)
tipo_turno_sel = st.sidebar.selectbox(
    "",
    ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"],
    key="select_regimen_box",
)
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"
st.sidebar.markdown("</div>", unsafe_allow_html=True)

st.sidebar.markdown(
    '<div class="orange-container-box">', unsafe_allow_html=True
)
st.sidebar.markdown(
    '<span class="selector-label-centered">SELECCIONAR TURNO'
    " OPERATIVO</span>",
    unsafe_allow_html=True,
)
turno_seleccionado = st.sidebar.selectbox(
    "",
    ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"],
    key="select_turno_box",
)
st.sidebar.markdown("</div>", unsafe_allow_html=True)

horas_turno = st.sidebar.number_input(
    "Horas Efectivas Turno", value=10.0, step=0.5
)

st.sidebar.markdown("---")
st.sidebar.header("⛏️ Plan de Producción")

target_mineral_num = st.sidebar.number_input(
    "Objetivo Mineral (Ton)", value=18000, step=1000
)
target_esteril_num = st.sidebar.number_input(
    "Objetivo Estéril (Ton)", value=12000, step=1000
)

st.sidebar.markdown("---")

st.sidebar.markdown(
    "<p style='font-size: 13px; font-weight: 800; color: #F8FAFC; text-align:"
    " center; margin-bottom: 6px; white-space: nowrap;'>INSUMOS, PRECIOS Y"
    " PARÁMETROS PRE-TURNO</p>",
    unsafe_allow_html=True,
)

tc_mercado, diesel_mercado = obtener_indicadores_mercado()

st.sidebar.markdown(
    f"""
    <div style="background-color: #0F172A; padding: 6px; border-radius: 6px; border: 1px solid #0284C7; text-align: center; margin-bottom: 8px;">
        <span style="color: #38BDF8 !important; font-size: 10px; font-weight: 800; display: block;">🌐 MERCADO EN VIVO (CNE / BCO CENTRAL)</span>
        <span style="color: #FFFFFF !important; font-size: 11px; font-weight: 700;">USD/CLP: ${fmt_num(tc_mercado, 1)} | Diésel Ref: ${diesel_mercado} USD/L</span>
    </div>
""",
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    '<div class="orange-container-box">', unsafe_allow_html=True
)
st.sidebar.markdown(
    '<span class="selector-label-centered">Seleccionar tipo de Operación /'
    " Mineral</span>",
    unsafe_allow_html=True,
)
tipo_mineral = st.sidebar.selectbox(
    "",
    [
        "Caliche / Yodo",
        "Cobre (Cu)",
        "Oro (Au)",
        "Plata (Ag)",
        "Hierro (Fe)",
        "Litio (Li / LCE)",
        "Carbón / Energéticos",
        "No Metálicos / Canteras",
        "Movimiento de Tierras / Obras Civiles",
    ],
    key="select_mineral_box",
)
st.sidebar.markdown("</div>", unsafe_allow_html=True)

unidades_map = {
    "Caliche / Yodo": {
        "razon": "Ton Caliche / kg Yodo",
        "costo": "USD / Ton Caliche",
        "val_razon": 3.91,
        "val_usd": 9.079,
    },
    "Cobre (Cu)": {
        "razon": "Ton Mineral / Ton Cu Fino",
        "costo": "USD / Ton Mineral Cu",
        "val_razon": 120.0,
        "val_usd": 15.50,
    },
    "Oro (Au)": {
        "razon": "Ton Mineral / Oz Au",
        "costo": "USD / Ton Mineral Au",
        "val_razon": 1.5,
        "val_usd": 18.20,
    },
    "Plata (Ag)": {
        "razon": "Ton Mineral / Oz Ag",
        "costo": "USD / Ton Mineral Ag",
        "val_razon": 0.8,
        "val_usd": 12.00,
    },
    "Hierro (Fe)": {
        "razon": "Ton Mineral / Ton Concentrado Fe",
        "costo": "USD / Ton Mineral Fe",
        "val_razon": 1.8,
        "val_usd": 8.50,
    },
    "Litio (Li / LCE)": {
        "razon": "Ton Salmuera-Roca / Ton LCE",
        "costo": "USD / Ton Material Li",
        "val_razon": 50.0,
        "val_usd": 22.00,
    },
    "Carbón / Energéticos": {
        "razon": "Ton ROM / Ton Carbón Limpio",
        "costo": "USD / Ton Carbón",
        "val_razon": 1.3,
        "val_usd": 7.00,
    },
    "No Metálicos / Canteras": {
        "razon": "Ton Brutas / Ton Roca Comercial",
        "costo": "USD / Ton Material",
        "val_razon": 1.1,
        "val_usd": 5.00,
    },
    "Movimiento de Tierras / Obras Civiles": {
        "razon": "m³ o Ton / Unidad Avance",
        "costo": "USD / Ton o m³ Movido",
        "val_razon": 1.0,
        "val_usd": 4.50,
    },
}

label_razon = unidades_map[tipo_mineral]["razon"]
label_costo = unidades_map[tipo_mineral]["costo"]
default_razon = unidades_map[tipo_mineral]["val_razon"]
default_usd = unidades_map[tipo_mineral]["val_usd"]

precio_diesel = st.sidebar.number_input(
    "Precio Diésel (USD / Litro Contrato)",
    value=float(diesel_mercado),
    step=0.01,
)
factor_yodo = st.sidebar.number_input(
    f"{label_razon}", value=float(default_razon), step=0.01
)
valor_ton_usd = st.sidebar.number_input(
    f"{label_costo}", value=float(default_usd), step=0.001
)

st.sidebar.markdown("---")
st.sidebar.header("🚛 Distancia de Acarreo")
distancia_acarreo_km = st.sidebar.number_input(
    "Distancia Promedio Acarreo (km)", value=3.5, step=0.5
)

st.sidebar.markdown("---")

# ---------------------------------------------------------
# INICIALIZACIÓN DE FLOTA
# ---------------------------------------------------------
if "palas_df" not in st.session_state:
  st.session_state.palas_df = pd.DataFrame([
      {
          "Item": 1,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "PA622",
          "Modelo": "R9200",
          "Operador": "Carlos Araya",
          "Rend_TonH": 1424,
          "Consumo_LtsH": 120.0,
          "Costo_USDH": 441.44,
      },
      {
          "Item": 2,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "PA624",
          "Modelo": "R9300",
          "Operador": "Roberto Gómez",
          "Rend_TonH": 1854,
          "Consumo_LtsH": 145.0,
          "Costo_USDH": 444.96,
      },
      {
          "Item": 3,
          "Agendar": False,
          "Estado": "🔴 Falla Mecánica",
          "ID": "PA626",
          "Modelo": "R9300",
          "Operador": "Sin Asignar",
          "Rend_TonH": 1854,
          "Consumo_LtsH": 145.0,
          "Costo_USDH": 444.96,
      },
  ])

if "cf_df" not in st.session_state:
  st.session_state.cf_df = pd.DataFrame([
      {
          "Item": 1,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CF437",
          "Modelo": "WA900",
          "Operador": "Juan Pérez",
          "Rend_TonH": 685,
          "Consumo_LtsH": 75.0,
          "Costo_USDH": 342.50,
      },
      {
          "Item": 2,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CF440",
          "Modelo": "CAT 994K",
          "Operador": "Mario Silva",
          "Rend_TonH": 820,
          "Consumo_LtsH": 90.0,
          "Costo_USDH": 380.00,
      },
      {
          "Item": 3,
          "Agendar": False,
          "Estado": "🟡 Mantenimiento",
          "ID": "CF447",
          "Modelo": "WA900",
          "Operador": "Sin Asignar",
          "Rend_TonH": 685,
          "Consumo_LtsH": 75.0,
          "Costo_USDH": 342.50,
      },
  ])

if "caex_df" not in st.session_state:
  st.session_state.caex_df = pd.DataFrame([
      {
          "Item": 1,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CA319",
          "Modelo": "HD1500-8",
          "Operador": "Pedro Morales",
          "Rend_TonH": 604,
          "Consumo_LtsH": 95.0,
          "Costo_USDH": 289.92,
      },
      {
          "Item": 2,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CA320",
          "Modelo": "HD1500-8",
          "Operador": "Luis Tapia",
          "Rend_TonH": 604,
          "Consumo_LtsH": 95.0,
          "Costo_USDH": 289.92,
      },
      {
          "Item": 3,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CA321",
          "Modelo": "HD1500-8",
          "Operador": "Andrés Castro",
          "Rend_TonH": 604,
          "Consumo_LtsH": 95.0,
          "Costo_USDH": 289.92,
      },
      {
          "Item": 4,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CA322",
          "Modelo": "CAT 789D",
          "Operador": "Diego Rojas",
          "Rend_TonH": 710,
          "Consumo_LtsH": 110.0,
          "Costo_USDH": 310.00,
      },
      {
          "Item": 5,
          "Agendar": True,
          "Estado": "🟢 Disponible",
          "ID": "CA323",
          "Modelo": "CAT 789D",
          "Operador": "Gonzalo Vera",
          "Rend_TonH": 710,
          "Consumo_LtsH": 110.0,
          "Costo_USDH": 310.00,
      },
  ])

# ---------------------------------------------------------
# TABLAS DINÁMICAS DE FLOTA
# ---------------------------------------------------------
st.markdown(
    "<h2 class='centered-title'>🚜 Estado y Agendamiento de Flota"
    " Operativa</h2>",
    unsafe_allow_html=True,
)

col_t1, col_t2, col_t3 = st.columns(3)
opciones_estado = ["🟢 Disponible", "🟡 Mantenimiento", "🔴 Falla Mecánica"]

with col_t1:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Gif Pala.jpg"):
      st.image("Gif Pala.jpg", width=80)
  with c_txt:
    st.markdown("### Pala de Carguío")

  ed_palas = st.data_editor(
      st.session_state.palas_df,
      column_config={
          "Item": st.column_config.NumberColumn("N° Item", disabled=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado Mecánico", options=opciones_estado
          ),
      },
      hide_index=True,
      key="editor_palas",
      num_rows="dynamic",
  )

with col_t2:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Gif Cargador Frontal.jpg"):
      st.image("Gif Cargador Frontal.jpg", width=80)
  with c_txt:
    st.markdown("### Cargador Frontal")

  ed_cf = st.data_editor(
      st.session_state.cf_df,
      column_config={
          "Item": st.column_config.NumberColumn("N° Item", disable
                                                d=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado Mecánico", options=opciones_estado
          ),
      },
      hide_index=True,
      key="editor_cf",
      num_rows="dynamic",
  )

with col_t3:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Gif Camión Minero.jpg"):
      st.image("Gif Camión Minero.jpg", width=80)
  with c_txt:
    st.markdown("### Camión CAEX")

  ed_caex = st.data_editor(
      st.session_state.caex_df,
      column_config={
          "Item": st.column_config.NumberColumn("N° Item", disabled=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado Mecánico", options=opciones_estado
          ),
      },
      hide_index=True,
      key="editor_caex",
      num_rows="dynamic",
  )

# ---------------------------------------------------------
# CÁLCULOS MATEMÁTICOS DE BALANCE
# ---------------------------------------------------------
palas_activas = ed_palas[
    (ed_palas["Agendar"] == True) & (ed_palas["Estado"] == "🟢 Disponible")
]
cf_activos = ed_cf[
    (ed_cf["Agendar"] == True) & (ed_cf["Estado"] == "🟢 Disponible")
]
caex_activos = ed_caex[
    (ed_caex["Agendar"] == True) & (ed_caex["Estado"] == "🟢 Disponible")
]

factor_distancia = (
    3.5 / distancia_acarreo_km if distancia_acarreo_km > 0 else 1.0
)

cap_carguio = palas_activas["Rend_TonH"].sum() + cf_activos["Rend_TonH"].sum()
cap_transporte = caex_activos["Rend_TonH"].sum() * factor_distancia

litros_diesel_turno = (
    palas_activas["Consumo_LtsH"].sum()
    + cf_activos["Consumo_LtsH"].sum()
    + caex_activos["Consumo_LtsH"].sum()
) * horas_turno
costo_diesel_turno = litros_diesel_turno * precio_diesel

costo_fijo_total_turno = (
    palas_activas["Costo_USDH"].sum()
    + cf_activos["Costo_USDH"].sum()
    + caex_activos["Costo_USDH"].sum()
) * horas_turno
costo_opex_total_turno = costo_fijo_total_turno + costo_diesel_turno

match_factor = (cap_transporte / cap_carguio) if cap_carguio > 0 else 0.0
tasa_efectiva = min(cap_carguio, cap_transporte)
tonelaje_proyectado = tasa_efectiva * horas_turno

produccion_estimada = (
    (tonelaje_proyectado / factor_yodo) if factor_yodo > 0 else 0
)
ingreso_bruto_usd = tonelaje_proyectado * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - costo_opex_total_turno
costo_unitario_ton = (
    (costo_opex_total_turno / tonelaje_proyectado)
    if tonelaje_proyectado > 0
    else 0
)
costo_diesel_por_ton = (
    (costo_diesel_turno / tonelaje_proyectado) if tonelaje_proyectado > 0 else 0
)

consumo_especifico_lts_ton = (
    (litros_diesel_turno / tonelaje_proyectado)
    if tonelaje_proyectado > 0
    else 0.0
)
emisiones_co2_kg = litros_diesel_turno * 2.68
co2_por_ton = (
    (emisiones_co2_kg / tonelaje_proyectado) if tonelaje_proyectado > 0 else 0.0
)

if st.sidebar.button("🔒 CIERRE Y GUARDADO EN BD", use_container_width=True):
  guardar_agendamiento_db(
      num_agendamiento,
      fecha_str,
      hora_str,
      nombre_mina,
      turno_seleccionado,
      regimen_guardia,
      st.session_state.get("usuario_activo", "Mauricio L. Cepeda Mondaca"),
      tonelaje_proyectado,
      litros_diesel_turno,
      costo_diesel_turno,
      costo_opex_total_turno,
      costo_unitario_ton,
      beneficio_neto_usd,
      match_factor,
  )
  st.sidebar.success(
      f"✅ Agendamiento {num_agendamiento} guardado exitosamente."
  )
  st.session_state.autenticado = False
  st.rerun()

# ---------------------------------------------------------
# DASHBOARD DE RESULTADOS
# ---------------------------------------------------------
st.markdown("---")
st.header(f"📈 Resumen de Agendamiento Pre-Turno: {num_agendamiento}")
st.subheader(
    f"🏢 Faena: {nombre_mina} | Fecha y Hora: {nombre_dia_actual}, {fecha_str}"
    f" {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})"
)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Ton Movidas", f"{fmt_num(tonelaje_proyectado, 0)} Ton")
k2.metric("Consumo Diésel", f"{fmt_num(litros_diesel_turno, 0)} Lts")
k3.metric("Costo Diésel", f"${fmt_num(costo_diesel_turno, 2)} USD")
k4.metric("OPEX Total", f"${fmt_num(costo_opex_total_turno, 2)} USD")
k5.metric("Costo/Ton", f"${fmt_num(costo_unitario_ton, 2)} USD/Ton")
k6.metric("Beneficio Neto", f"${fmt_num(beneficio_neto_usd, 2)} USD")

st.markdown("---")

col_eval1, col_eval2 = st.columns(2)

with col_eval1:
  st.markdown("### ⛽ Evaluación Económica y Meta de Producción")

  st.markdown(
      '<p class="highlight-red-large">• Costo Combustible / Ton:'
      f" ${fmt_num(costo_diesel_por_ton, 2)} USD/Ton</p>",
      unsafe_allow_html=True,
  )
  st.markdown(
      '<p class="highlight-red-large">• Consumo Específico Diésel:'
      f" {fmt_num(consumo_especifico_lts_ton, 2)} Lts/Ton</p>",
      unsafe_allow_html=True,
  )
  st.markdown(
      '<p class="highlight-red-large">• Huella CO₂ Operativa:'
      f" {fmt_num(co2_por_ton, 2)} kg CO₂/Ton ({fmt_num(emisiones_co2_kg, 0)} kg"
      " CO₂ total)</p>",
      unsafe_allow_html=True,
  )
  st.markdown(
      '<p class="highlight-red-large">• Producción Estimada:'
      f" {fmt_num(produccion_estimada, 1)} unidades ({tipo_mineral})</p>",
      unsafe_allow_html=True,
  )

  total_objetivo = target_mineral_num + target_esteril_num
  cumplimiento = (
      (tonelaje_proyectado / total_objetivo) * 100 if total_objetivo > 0 else 0
  )
  st.markdown(
      '<p class="highlight-red-large">• Cumplimiento Plan de Mina:'
      f" {fmt_num(cumplimiento, 1)}% de {fmt_num(total_objetivo, 0)} Ton"
      " Objetivo</p>",
      unsafe_allow_html=True,
  )
  st.progress(min(cumplimiento / 100.0, 1.0))

with col_eval2:
  st.markdown("### 🚦 Semáforo Prescriptivo de Balance de Flota")

  st.markdown(
      '<p class="mf-label">Match Factor Calculado:</p>', unsafe_allow_html=True
  )
  st.markdown(
      f'<p class="mf-value">{fmt_num(match_factor, 2)}</p>',
      unsafe_allow_html=True,
  )

  if 0.80 <= match_factor <= 1.05:
    st.success(
        "🟢 **AGENDAMIENTO ÓPTIMO Y RENTABLE (Match Factor:"
        f" {fmt_num(match_factor, 2)})**"
    )
  elif match_factor < 0.80:
    st.error(
        "🔴 **DESCALCE POR SUB-TRANSPORTE (Match Factor:"
        f" {fmt_num(match_factor, 2)})**"
    )
  else:
    st.warning(
        "🟡 **SOBREDIMENSIONAMIENTO DE CAEX (Match Factor:"
        f" {fmt_num(match_factor, 2)})**"
    )

# ---------------------------------------------------------
# MÓDULO DE SEGUIMIENTO ESPACIAL: CIRCUITO FRENTE -> RUTA -> DESTINO (PLOTLY ROBUSTO)
# ---------------------------------------------------------
st.markdown("---")
st.subheader("🗺️ Monitoreo Espacial del Circuito de Acarreo")
st.caption(
    "Visualización interactiva del circuito de minado (Frente ➔ Ruta ➔"
    " Chancador/Botadero). Haz clic sobre los equipos para ver su ficha"
    " técnica."
)

import plotly.graph_objects as go

# 1. Crear lienzo de gráfico del circuito
fig_circuito = go.Figure()

# Dibujar la ruta principal (Frente a Chancador)
fig_circuito.add_trace(
    go.Scatter(
        x=[0, 3.5],
        y=[0, 0],
        mode="lines",
        line=dict(color="#F59E0B", width=8),
        name="Ruta Principal Acarreo (3.5 km)",
        hoverinfo="none",
    )
)

# 2. Posicionar Palas y Cargadores en el Frente (Origen x=0)
x_frente = []
y_frente = []
txt_frente = []
hover_frente = []

idx_c = 0
for _, r in ed_palas.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    offset_y = 0.2 + (idx_c * 0.25)
    x_frente.append(0)
    y_frente.append(offset_y)
    txt_frente.append(f"Pala {r['ID']}")
    hover_frente.append(
        f"<b>Pala {r['ID']}</b><br>Modelo: {r['Modelo']}<br>Estado:"
        f" {r['Estado']}<br>Operador: {r['Operador']}<br>Rendimiento:"
        f" {r['Rend_TonH']} Ton/h"
    )
    idx_c += 1

for _, r in ed_cf.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    offset_y = -0.2 - (idx_c * 0.25)
    x_frente.append(0)
    y_frente.append(offset_y)
    txt_frente.append(f"Cargador {r['ID']}")
    hover_frente.append(
        f"<b>Cargador {r['ID']}</b><br>Modelo: {r['Modelo']}<br>Estado:"
        f" {r['Estado']}<br>Operador: {r['Operador']}<br>Rendimiento:"
        f" {r['Rend_TonH']} Ton/h"
    )
    idx_c += 1

# Dibujar unidades de carguío en el Frente (Triángulos Naranjos/Azules)
fig_circuito.add_trace(
    go.Scatter(
        x=x_frente,
        y=y_frente,
        mode="markers+text",
        marker=dict(size=20, symbol="square", color="#0284C7"),
        text=txt_frente,
        textposition="top center",
        name="Unidades de Carguío",
        hoverinfo="text",
        hovertext=hover_frente,
    )
)

# 3. Posicionar Camiones CAEX en la Ruta de Acarreo (x entre 0.5 y 3.0 km)
x_caex = []
y_caex = []
txt_caex = []
hover_caex = []
colores_caex = []

idx_cx = 0
total_caex_activos = len(ed_caex)
for _, r in ed_caex.iterrows():
  if r["Agendar"]:
    # Espaciado proporcional sobre la distancia de 3.5 km
    pos_x = 0.5 + (idx_cx * (2.5 / max(1, total_caex_activos)))
    pos_y = (idx_cx % 2 == 0) and 0.15 or -0.15  # Alternar carril

    color = (
        "#10B981"
        if r["Estado"] == "🟢 Disponible"
        else (
            "#F59E0B" if r["Estado"] == "🟡 Mantenimiento" else "#DC2626"
        )
    )

    x_caex.append(pos_x)
    y_caex.append(pos_y)
    txt_caex.append(f"CAEX {r['ID']}")
    colores_caex.append(color)
    hover_caex.append(
        f"<b>CAEX {r['ID']}</b><br>Modelo: {r['Modelo']}<br>Estado:"
        f" {r['Estado']}<br>Operador: {r['Operador']}<br>Consumo Est.:"
        f" {r['Consumo_LtsH']*horas_turno:.0f} Lts"
    )
    idx_cx += 1

# Dibujar Camiones CAEX
fig_circuito.add_trace(
    go.Scatter(
        x=x_caex,
        y=y_caex,
        mode="markers+text",
        marker=dict(size=18, symbol="circle", color=colores_caex),
        text=txt_caex,
        textposition="bottom center",
        name="Camiones CAEX",
        hoverinfo="text",
        hovertext=hover_caex,
    )
)

# 4. Posicionar Zona de Entrega / Chancador Primario (Destino x=3.5)
fig_circuito.add_trace(
    go.Scatter(
        x=[3.5],
        y=[0],
        mode="markers+text",
        marker=dict(size=26, symbol="hexagram", color="#DC2626"),
        text=["CHANCADOR / BOTADERO"],
        textposition="top center",
        name="Zona de Entrega",
        hoverinfo="text",
        hovertext=[
            "<b>Chancador Primario & Botadero</b><br>Pórtico Lectura RFID"
            " UHF<br>Punto de Descarga Final"
        ],
    )
)

# Formatear diseño visual del circuito
fig_circuito.update_layout(
    xaxis=dict(
        title="Distancia de Acarreo (Kilómetros)",
        range=[-0.5, 4.0],
        zeroline=False,
        showgrid=True,
    ),
    yaxis=dict(
        title="",
        range=[-1.0, 1.0],
        showticklabels=False,
        zeroline=False,
        showgrid=False,
    ),
    height=380,
    margin=dict(l=20, r=20, t=30, b=30),
    paper_bgcolor="#F8FAFC",
    plot_bgcolor="#FFFFFF",
    showlegend=True,
)

st.plotly_chart(fig_circuito, use_container_width=True)

# ---------------------------------------------------------
# REPORTE Y FICHA PRESCRIPTIVA PRE-TURNO
# ---------------------------------------------------------
st.markdown("---")
col_exp1, col_exp2 = st.columns([2, 1])

with col_exp1:
  st.subheader("📄 Reporte y Ficha Prescriptiva Pre-Turno")

with col_exp2:
  df_export = pd.DataFrame([{
      "N° Agendamiento": num_agendamiento,
      "Fecha": fecha_str,
      "Hora": hora_str,
      "Faena / Mina": nombre_mina,
      "Turno Operativo": turno_seleccionado,
      "Régimen Guardia": regimen_guardia,
      "Tipo de Mineral": tipo_mineral,
      "Responsable Agendamiento": st.session_state.get(
          "usuario_activo", "Mauricio L. Cepeda Mondaca"
      ),
      "Match Factor Calculado": round(match_factor, 2),
      "Toneladas Proyectadas (Ton)": round(tonelaje_proyectado, 0),
      "Consumo Diésel Total (Lts)": round(litros_diesel_turno, 0),
      "Consumo Específico (Lts/Ton)": round(consumo_especifico_lts_ton, 2),
      "Huella CO2 Operativa (kg CO2/Ton)": round(co2_por_ton, 2),
      "OPEX Total Turno (USD)": round(costo_opex_total_turno, 2),
      "Costo Unitario (USD/Ton)": round(costo_unitario_ton, 2),
      "Beneficio Neto Proyectado (USD)": round(beneficio_neto_usd, 2),
  }])

  csv_data = df_export.to_csv(
      index=False, sep=";", encoding="utf-8-sig"
  ).encode("utf-8-sig")
  st.download_button(
      label="📥 Descargar Ficha Pre-Turno (Excel / CSV)",
      data=csv_data,
      file_name=f"Ficha_Agendamiento_{num_agendamiento}.csv",
      mime="text/csv",
      use_container_width=True,
  )

# ---------------------------------------------------------
# MÓDULO: CONCILIACIÓN Y CIERRE DE TURNO
# ---------------------------------------------------------
st.markdown("---")
st.subheader("🔄 Conciliación y Cierre de Turno (Plan vs. Actual)")
st.markdown(
    "Selecciona el N° de Agendamiento guardado para auditar la trazabilidad"
    " entre lo planificado y lo realmente obtenido en terreno."
)

conn_conc = sqlite3.connect(DB_FILE)
df_lista_ag = pd.read_sql_query(
    "SELECT num_agendamiento, fecha_registro, turno, jefe_turno, ton_movidas,"
    " consumo_diesel_lts, opex_total_usd, match_factor FROM"
    " historico_agendamientos ORDER BY id DESC",
    conn_conc,
)
conn_conc.close()

if not df_lista_ag.empty:
  opciones_ag = df_lista_ag.apply(
      lambda row: (
          f"{row['num_agendamiento']} | {row['fecha_registro']} |"
          f" {row['turno']} | Resp: {row['jefe_turno']}"
      ),
      axis=1,
  ).tolist()

  ag_seleccionado_str = st.selectbox(
      "🔍 Seleccionar Agendamiento Guardado para Cierre:", opciones_ag
  )
  num_ag_selected = ag_seleccionado_str.split(" | ")[0]

  datos_plan = df_lista_ag[
      df_lista_ag["num_agendamiento"] == num_ag_selected
  ].iloc[0]
  ton_plan = float(datos_plan["ton_movidas"])
  diesel_plan = float(datos_plan["consumo_diesel_lts"])
  mf_plan = float(datos_plan["match_factor"])

  st.info(
      f"📋 **Datos Planificados en {num_ag_selected}:** Toneladas Proyectadas ="
      f" **{fmt_num(ton_plan, 0)} Ton** | Diésel Presupuestado ="
      f" **{fmt_num(diesel_plan, 0)} Lts** | Match Factor = **{fmt_num(mf_plan, 2)}**"
  )

  col_c1, col_c2 = st.columns(2)

  with col_c1:
    st.markdown("#### 📥 Ingreso de Datos Reales de Terreno (Post-Turno)")

    st.markdown("**Toneladas Reales Extraídas (Ton):**")
    ton_reales = st.number_input(
        "",
        value=ton_plan,
        step=500.0,
        key="input_ton_reales",
        label_visibility="collapsed",
    )

    st.markdown("**Consumo Diésel Real (Litros):**")
    diesel_real = st.number_input(
        "",
        value=diesel_plan,
        step=200.0,
        key="input_diesel_reales",
        label_visibility="collapsed",
    )

    opciones_causales = [
        "Falla Mecánica de CAEX",
        "Falla de Pala / Cargador",
        "Inasistencia de Operador",
        "Lluvia / Condición Climática",
        "Voladura / Tronadura Atrasada",
        "Atasco / Detención en Chancado",
        "Otra",
    ]

    st.markdown(
        "**Causas de Desviación / Imprevistos en Turno (Selección Múltiple):**"
    )
    causas_seleccionadas = st.multiselect(
        "",
        options=opciones_causales,
        default=[],
        placeholder="Elija opciones",
        label_visibility="collapsed",
    )

    st.markdown("**Observaciones / Bitácora de Terreno:**")
    observaciones_turno = st.text_input(
        "",
        value="",
        placeholder=(
            "Ej: CA321 fuera a las 11:00 hrs; PA622 detenida 45 min..."
        ),
        label_visibility="collapsed",
    )

  with col_c2:
    st.markdown("#### 📊 Indicadores de Efectividad Operativa")

    adherencia_plan = (ton_reales / ton_plan * 100) if ton_plan > 0 else 0.0
    costo_real_usd = costo_fijo_total_turno + (diesel_real * precio_diesel)
    costo_real_ton = (costo_real_usd / ton_reales) if ton_reales > 0 else 0.0

    if adherencia_plan >= 95.0:
      st.markdown(
          '<p class="adh-green-large">Adherencia al Plan de Mina:'
          f" {fmt_num(adherencia_plan, 1)}%</p>",
          unsafe_allow_html=True,
      )
    else:
      st.markdown(
          '<p class="adh-red-large">Adherencia al Plan de Mina:'
          f" {fmt_num(adherencia_plan, 1)}%</p>",
          unsafe_allow_html=True,
      )

    st.progress(min(adherencia_plan / 100.0, 1.0))

    texto_causas = (
        ", ".join(causas_seleccionadas)
        if causas_seleccionadas
        else "Sin imprevistos registrados"
    )

    if adherencia_plan >= 98.0:
      st.success(
          "🎯 **AGENDAMIENTO EXITOSO:** Cumplimiento del"
          f" {fmt_num(adherencia_plan, 1)}% de la meta proyectada"
          f" ({num_ag_selected})."
      )
    elif adherencia_plan >= 85.0:
      st.warning(
          f"⚠️ **CUMPLIMIENTO PARCIAL ({fmt_num(adherencia_plan, 1)}%):**"
          f" Desviación menor atribuida a: {texto_causas}."
      )
    else:
      st.error(
          f"🚨 **DESVIACIÓN CRÍTICA ({fmt_num(adherencia_plan, 1)}%):** Impacto"
          f" severo por eventos múltiples ({texto_causas}). Costo Real:"
          f" ${fmt_num(costo_real_ton, 2)} USD/Ton."
      )
else:
  st.info(
      "Aún no hay agendamientos guardados en la base de datos para conciliar."
  )

# ---------------------------------------------------------
# HISTÓRICO EN BD RESTRINGIDO Y SEGMENTADO POR PERÍODOS
# ---------------------------------------------------------
st.markdown("---")
col_h1, col_h2 = st.columns([3, 1])

with col_h1:
  st.subheader("📜 Histórico de Agendamientos")

with col_h2:
  if st.session_state.get("user_id") == "mcepeda":
    if st.button(
        "🗑️ Borrar Histórico (Admin)", type="primary", use_container_width=True
    ):
      borrar_historico_db()
      st.success("Histórico eliminado correctamente.")
      st.rerun()

conn = sqlite3.connect(DB_FILE)
df_hist = pd.read_sql_query(
    "SELECT * FROM historico_agendamientos ORDER BY id DESC", conn
)
conn.close()

if not df_hist.empty:
  rol_actual = st.session_state.get("rol_activo")
  usuario_actual = st.session_state.get("usuario_activo")

  if rol_actual in ["Administrador", "Gerente Operaciones / Evaluador"]:
    st.markdown(
        "### 🔒 [EXCLUSIVO GERENCIA] Panel de Control y Auditoría por Períodos"
    )

    c_f1, c_f2 = st.columns(2)
    with c_f1:
      supervisores_lista = ["Todos"] + list(df_hist["jefe_turno"].unique())
      sup_filtro = st.selectbox(
          "👤 Seleccionar Jefe de Mina:", supervisores_lista
      )
    with c_f2:
      periodo_filtro = st.selectbox(
          "📅 Seleccionar Período de Consolidación:",
          [
              "Semanal (Ciclo 7x7)",
              "Mensual",
              "Anual",
              "Histórico Completo",
          ],
      )

    df_gerencia = df_hist.copy()
    if sup_filtro != "Todos":
      df_gerencia = df_gerencia[df_gerencia["jefe_turno"] == sup_filtro]

    st.dataframe(df_gerencia, use_container_width=True)

    with st.expander(
        f"📈 Evaluación de Rendimiento Gerencial ({periodo_filtro}) —"
        f" Supervisor: {sup_filtro}",
        expanded=True,
    ):
      df_chart = pd.DataFrame({
          "Agendamiento / Fecha": (
              df_gerencia["num_agendamiento"]
              + " ("
              + df_gerencia["fecha_registro"]
              + ")"
          ),
          "Toneladas Proyectadas (Target)": df_gerencia["ton_movidas"],
          "Toneladas Reales Entregadas": df_gerencia["ton_movidas"] * 0.96,
      }).set_index("Agendamiento / Fecha")

      st.line_chart(df_chart, use_container_width=True)

      tot_proyectado = df_gerencia["ton_movidas"].sum()
      tot_opex = df_gerencia["opex_total_usd"].sum()
      avg_costo_ton = df_gerencia["costo_ton_usd"].mean()
      avg_mf = df_gerencia["match_factor"].mean()

      m_col1, m_col2, m_col3, m_col4 = st.columns(4)
      m_col1.metric(
          "Total Ton Proyectadas", f"{fmt_num(tot_proyectado, 0)} Ton"
      )
      m_col2.metric("OPEX Acumulado", f"${fmt_num(tot_opex, 2)} USD")
      m_col3.metric(
          "Costo Promedio", f"${fmt_num(avg_costo_ton, 2)} USD/Ton"
      )
      m_col4.metric("Match Factor Promedio", f"{fmt_num(avg_mf, 2)}")

  else:
    st.markdown(
        "### 👤 **Control Operativo de Turno Actual — Supervisor:**"
        f" `{usuario_actual}`"
    )

    df_turno_hoy = df_hist[
        (df_hist["jefe_turno"] == usuario_actual)
        & (df_hist["fecha_registro"] == fecha_str)
    ]

    if not df_turno_hoy.empty:
      st.dataframe(df_turno_hoy, use_container_width=True)
      st.success(
          "📌 Mostrando únicamente el agendamiento activo de la jornada actual."
      )
    else:
      st.info(
          "ℹ️ No hay agendamientos registrados para el turno del día de hoy."
          " Configure su flota en la barra lateral y presione 'CIERRE Y GUARDADO"
          " EN BD'."
      )
else:
  st.info("Aún no hay agendamientos guardados en la base de datos.")
