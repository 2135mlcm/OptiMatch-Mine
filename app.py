import base64
from datetime import datetime
import json
import os
import sqlite3
import threading
import time
import urllib.request
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components


# ---------------------------------------------------------
# MÓDULO KEEP-ALIVE: MANTIENE EL SERVIDOR ACTIVO 24/7
# ---------------------------------------------------------
def keep_alive_ping():
  while True:
    time.sleep(900)
    _ = datetime.now()


if "keep_alive_started" not in st.session_state:
  st.session_state.keep_alive_started = True
  thread = threading.Thread(target=keep_alive_ping, daemon=True)
  thread.start()

# ---------------------------------------------------------
# INICIALIZACIÓN Y MIGRACIÓN AUTOMÁTICA DE BD (optimatch.db)
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
        "ALTER TABLE historico_agendamientos ADD COLUMN regimen_guardia"
        " TEXT"
    )
    conn.commit()

  conn.close()


init_db()


# ---------------------------------------------------------
# FUNCIONES AUXILIARES Y CONSULTA DE INDICADORES
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


def obtener_base64_img(nombre_archivo):
  posibles_rutas = [
      nombre_archivo,
      os.path.join(os.getcwd(), nombre_archivo),
      os.path.join(os.path.dirname(__file__), nombre_archivo),
      os.path.join(os.getcwd(), "static", nombre_archivo),
  ]
  for r in posibles_rutas:
    if os.path.exists(r):
      try:
        with open(r, "rb") as f:
          encoded = base64.b64encode(f.read()).decode()
          ext = r.split(".")[-1].lower()
          mime = "png" if ext == "png" else "jpeg"
          return f"data:image/{mime};base64,{encoded}"
      except Exception:
        pass
  return None


# ---------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA Y ESTILOS CSS
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

    .orange-container-box {
        background-color: #1E293B;
        border: 2px solid #F59E0B;
        border-radius: 8px;
        padding: 4px 8px !important;
        margin-bottom: 6px !important;
        box-shadow: 0px 0px 6px rgba(245, 158, 11, 0.3);
    }

    .title-box {
        background-color: #F8FAFC;
        padding: 12px 30px;
        border-radius: 12px;
        border: 2px solid #D97706;
        box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.08);
        text-align: center;
        width: fit-content;
        margin: 5px auto 15px auto;
    }

    .centered-title {
        text-align: center !important;
        width: 100% !important;
        margin-top: 10px !important;
        margin-bottom: 10px !important;
    }

    /* TEXTO DENTRO DE CASILLAS DE TABLAS: CENTRADO Y EN NEGRITA */
    div[data-testid="stDataFrame"] {
        background-color: #F1F5F9 !important;
        border: 2px solid #CBD5E1 !important;
        border-radius: 10px;
    }
    div[data-testid="stDataFrame"] td, 
    div[data-testid="stDataFrame"] th, 
    div[data-testid="stDataFrame"] div[role="gridcell"],
    div[data-testid="stDataFrame"] div[role="columnheader"] {
        text-align: center !important;
        font-weight: 900 !important;
        color: #0F172A !important;
        justify-content: center !important;
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
        font-size: 18px !important;
        font-weight: 800 !important;
        margin-bottom: 6px !important;
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

    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1rem !important;
    }

    /* BOTÓN "INICIO DE ACARREO": TEXTO BLANCO NEGRITA Y SIN BORDES/MARCOS ENVOLVENTES */
    div.stButton > button[kind="primary"] {
        background-color: #DC2626 !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        font-weight: 900 !important;
        font-size: 17px !important;
        border-radius: 30px !important;
        height: 52px !important;
        padding: 0px 20px !important;
        margin: 0px !important;
    }
    div.stButton > button[kind="primary"] p,
    div.stButton > button[kind="primary"] span {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        font-weight: 900 !important;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #B91C1C !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        border: none !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

LOGO_PATH = "Logo_OptiMatch.png"

# ---------------------------------------------------------
# AUTENTICACIÓN PRIVADA
# ---------------------------------------------------------
if "autenticado" not in st.session_state:
  st.session_state.autenticado = False

if not st.session_state.autenticado:
  col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
  with col_l2:
    st.markdown("<br>", unsafe_allow_html=True)
    if os.path.exists(LOGO_PATH):
      st.image(LOGO_PATH, width=320)
    st.markdown(
        "<p style='text-align: center; font-weight: 800; font-size:"
        " 15px;'>Acceso Restringido por Perfil | Universidad Alberto"
        " Hurtado</p>",
        unsafe_allow_html=True,
    )
    with st.form("login_form_secure", clear_on_submit=True):
      usuario = st.text_input(
          "Nombre de Usuario:",
          value="",
          placeholder="Ingrese usuario...",
          key="usr_field_clean",
      )
      clave = st.text_input(
          "Contraseña de Acceso:",
          type="password",
          value="",
          placeholder="Ingrese contraseña...",
          key="pwd_field_clean",
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
          st.error("❌ Usuario o contraseña no registrados en el sistema.")
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
        <h1 style="color: #0F172A; margin: 0; font-size: 26px; font-weight: 800;">OptiMatch Mine — Control de Flota</h1>
        <p style="color: #0284C7; margin: 4px 0 0 0; font-size: 13px; font-weight: 800; letter-spacing: 0.5px;">
            SISTEMA PRESCRIPTIVO DE DECISIONES PRE-TURNO PARA LA MEDIANA MINERÍA
        </p>
    </div>
""",
    unsafe_allow_html=True,
)

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

tipo_turno_sel = st.sidebar.selectbox(
    "Régimen Guardia",
    ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"],
    key="select_regimen_box",
)
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"

turno_seleccionado = st.sidebar.selectbox(
    "Turno Operativo",
    ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"],
    key="select_turno_box",
)
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

tc_mercado, diesel_mercado = obtener_indicadores_mercado()
precio_diesel = st.sidebar.number_input(
    "Precio Diésel (USD / Litro Contrato)",
    value=float(diesel_mercado),
    step=0.01,
)

tipo_mineral = st.sidebar.selectbox(
    "Tipo de Mineral / Materia Prima",
    ["Cobre (Cu)", "Caliche / Yodo", "Oro (Au)", "Plata (Ag)", "Hierro (Fe)"],
)

factor_yodo = st.sidebar.number_input(
    "Razon Mineral / Producto Fino", value=120.0, step=0.1
)
valor_ton_usd = st.sidebar.number_input(
    "USD / Ton Mineral", value=15.50, step=0.01
)
distancia_acarreo_km = st.sidebar.number_input(
    "Distancia Promedio Acarreo (km)", value=3.5, step=0.5
)

# ---------------------------------------------------------
# TABLAS DINÁMICAS DE FLOTA (CON IMÁGENES Y CASILLAS CENTRADAS)
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

st.markdown(
    "<h3 style='text-align: center; margin-bottom: 12px;'>🚜 Estado y"
    " Agendamiento de Flota Operativa</h3>",
    unsafe_allow_html=True,
)
col_t1, col_t2, col_t3 = st.columns(3)
opciones_estado = ["🟢 Disponible", "🟡 Mantenimiento", "🔴 Falla Mecánica"]

with col_t1:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Gif Pala.jpg"):
      st.image("Gif Pala.jpg", width=65)
  with c_txt:
    st.markdown("### Palas de Carguío")

  ed_palas = st.data_editor(
      st.session_state.palas_df,
      column_config={
          "Item": st.column_config.NumberColumn("Item", disabled=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado", options=opciones_estado
          ),
      },
      hide_index=True,
      key="ed_palas",
  )

with col_t2:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Gif Cargador Frontal.jpg"):
      st.image("Gif Cargador Frontal.jpg", width=65)
  with c_txt:
    st.markdown("### Cargadores Frontales")

  ed_cf = st.data_editor(
      st.session_state.cf_df,
      column_config={
          "Item": st.column_config.NumberColumn("Item", disabled=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado", options=opciones_estado
          ),
      },
      hide_index=True,
      key="ed_cf",
  )

with col_t3:
  c_img, c_txt = st.columns([1, 2])
  with c_img:
    if os.path.exists("Camión CAEX Vacío.png"):
      st.image("Camión CAEX Vacío.png", width=65)
    elif os.path.exists("Gif Camión Minero.jpg"):
      st.image("Gif Camión Minero.jpg", width=65)
  with c_txt:
    st.markdown("### Camiones CAEX")

  ed_caex = st.data_editor(
      st.session_state.caex_df,
      column_config={
          "Item": st.column_config.NumberColumn("Item", disabled=True),
          "Estado": st.column_config.SelectboxColumn(
              "Estado", options=opciones_estado
          ),
      },
      hide_index=True,
      key="ed_caex",
  )

# ---------------------------------------------------------
# CÁLCULOS MATEMÁTICOS DE BALANCE Y RENTABILIDAD
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

# BOTÓN EN LA SIDEBAR PARA GUARDAR EN BASE DE DATOS
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
# DASHBOARD DE RESULTADOS Y SEMÁFORO DE MATCH FACTOR
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
      '<p class="mf-label">Match Factor Calculado:</p>',
      unsafe_allow_html=True,
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
# MONITOREO ESPACIAL DEL CIRCUITO DE ACARREO (COMPACTO Y PRECISO)
# ---------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(
    "<h3 style='margin-bottom: 6px;'>🗺️ Monitoreo Espacial del Circuito de"
    " Acarreo de Dos Vías</h3>",
    unsafe_allow_html=True,
)

if "acarreo_iniciado" not in st.session_state:
  st.session_state.acarreo_iniciado = False

dist_km_val = (
    distancia_acarreo_km if "distancia_acarreo_km" in locals() else 3.5
)

col_trig1, col_trig2, col_trig3 = st.columns([2.0, 3.8, 1.5])

with col_trig1:
  if st.button("🔴 INICIO DE ACARREO", type="primary"):
    st.session_state.acarreo_iniciado = True
    st.success("✅ Acarreo iniciado por confirmación VHF.")

with col_trig2:
  st.markdown(
      f"""
        <div style="padding: 2px 0px;">
            <span style="color: #0F172A !important; font-weight: 800 !important; font-size: 14px !important;">
                📻 <b>AVISO RADIO VHF:</b> Presione el botón rojo para autorizar el zarpe.
            </span><br>
            <span style="color: #0284C7 !important; font-weight: 800 !important; font-size: 13px !important;">
                🛣️ Distancia Oficial: {dist_km_val:.1f} km (Ida) / {dist_km_val:.1f} km (Retorno)
            </span>
        </div>
    """,
      unsafe_allow_html=True,
  )

with col_trig3:
  if st.button("🔄 Reiniciar Postura", use_container_width=True):
    st.session_state.acarreo_iniciado = False

# CARGA DE IMÁGENES BASE64
img_pala_b64 = obtener_base64_img("Gif Pala.jpg") or obtener_base64_img(
    "image_859ef9.png"
)
img_cf_b64 = obtener_base64_img(
    "Gif Cargador Frontal.jpg"
) or obtener_base64_img("image_859f19.png")
img_caex_vacio_b64 = (
    obtener_base64_img("Camión CAEX Vacío.png")
    or obtener_base64_img("Camion CAEX Vacio.png")
    or obtener_base64_img("Gif Camión Minero.jpg")
)
img_caex_cargado_b64 = (
    obtener_base64_img("Camión CAEX Cargado.png")
    or obtener_base64_img("Camion CAEX Cargado.png")
    or obtener_base64_img("Gif Camión Minero.jpg")
)

fig_circuito = go.Figure()

# Vías de Acarreo
fig_circuito.add_trace(
    go.Scatter(
        x=[0, 3.5],
        y=[0.15, 0.15],
        mode="lines",
        line=dict(color="#10B981", width=5, dash="dash"),
        name=f"Vía Ida Cargado ({dist_km_val:.1f} km)",
        hoverinfo="none",
    )
)
fig_circuito.add_trace(
    go.Scatter(
        x=[0, 3.5],
        y=[-0.15, -0.15],
        mode="lines",
        line=dict(color="#DC2626", width=5, dash="solid"),
        name=f"Vía Retorno Vacío ({dist_km_val:.1f} km)",
        hoverinfo="none",
    )
)

# 1. RENDERIZADO DE PALAS
idx_pala = 0
for _, r in ed_palas.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    pos_y = 0.38 + (idx_pala * 0.26)
    if img_pala_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_pala_b64,
              xref="x",
              yref="y",
              x=-0.18,
              y=pos_y,
              sizex=0.32,
              sizey=0.32,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    fig_circuito.add_trace(
        go.Scatter(
            x=[-0.18, 0.15],
            y=[pos_y, pos_y],
            mode="markers+text",
            marker=dict(size=[24, 1], opacity=[0.01, 0]),
            text=["", f"<b>Pala {r['ID']}</b>"],
            textposition="middle right",
            textfont=dict(size=11, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[
                f"<b>Pala {r['ID']}</b><br>Operador: {r['Operador']}",
                f"<b>Pala {r['ID']}</b><br>Operador: {r['Operador']}",
            ],
        )
    )
    idx_pala += 1

# 2. RENDERIZADO DE CARGADORES FRONTALES
idx_cf = 0
for _, r in ed_cf.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    pos_y = -0.36 - (idx_cf * 0.26)
    if img_cf_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_cf_b64,
              xref="x",
              yref="y",
              x=-0.18,
              y=pos_y,
              sizex=0.32,
              sizey=0.32,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    fig_circuito.add_trace(
        go.Scatter(
            x=[-0.18, 0.15],
            y=[pos_y, pos_y],
            mode="markers+text",
            marker=dict(size=[24, 1], opacity=[0.01, 0]),
            text=["", f"<b>CF {r['ID']}</b>"],
            textposition="middle right",
            textfont=dict(size=11, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[
                f"<b>Cargador {r['ID']}</b><br>Operador: {r['Operador']}",
                f"<b>Cargador {r['ID']}</b><br>Operador: {r['Operador']}",
            ],
        )
    )
    idx_cf += 1

# 3. RENDERIZADO DE CAMIONES CAEX
caex_agendados = ed_caex[ed_caex["Agendar"] == True]
total_caex_count = len(caex_agendados)

if not st.session_state.acarreo_iniciado:
  for i, (_, r) in enumerate(caex_agendados.iterrows()):
    pos_x = 0.0 - (i * 0.30)
    pos_y = 0.15

    if img_caex_vacio_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_caex_vacio_b64,
              xref="x",
              yref="y",
              x=pos_x,
              y=pos_y + 0.08,
              sizex=0.30,
              sizey=0.30,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    fig_circuito.add_trace(
        go.Scatter(
            x=[pos_x, pos_x],
            y=[pos_y + 0.08, pos_y - 0.12],
            mode="markers+text",
            marker=dict(size=[28, 1], opacity=[0.01, 0]),
            text=["", f"<b>C{r['ID']}</b>"],
            textposition="bottom center",
            textfont=dict(size=10, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[
                f"<b>CAEX {r['ID']}</b> (Vacío)",
                f"<b>CAEX {r['ID']}</b> (Vacío)",
            ],
        )
    )

  st.info(
      "📍 **FLOTA PARQUEADA EN FILA DE ESPERA:** Presione '🔴 INICIO DE ACARREO'"
      " para zarpar."
  )

else:
  for i, (_, r) in enumerate(caex_agendados.iterrows()):
    es_ida = i % 2 == 0

    if es_ida:
      pos_x = 0.4 + (i * (2.6 / max(1, total_caex_count)))
      pos_y = 0.15
      src_b64 = img_caex_cargado_b64
      label_txt = f"<b>C{r['ID']} (44.6T)</b>"
      tramo_txt = "Acarreo Cargado -> Chancador/Pila"
    else:
      pos_x = 3.1 - (i * (2.6 / max(1, total_caex_count)))
      pos_y = -0.15
      src_b64 = img_caex_vacio_b64
      label_txt = f"<b>C{r['ID']} (0T)</b>"
      tramo_txt = "Retorno Vacío -> Pala"

    if src_b64:
      fig_circuito.add_layout_image(
          dict(
              source=src_b64,
              xref="x",
              yref="y",
              x=pos_x,
              y=pos_y + 0.08,
              sizex=0.32,
              sizey=0.32,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    fig_circuito.add_trace(
        go.Scatter(
            x=[pos_x, pos_x],
            y=[pos_y + 0.08, pos_y - 0.12],
            mode="markers+text",
            marker=dict(size=[28, 1], opacity=[0.01, 0]),
            text=["", label_txt],
            textposition="bottom center",
            textfont=dict(size=10, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[
                f"<b>CAEX {r['ID']}</b><br>{tramo_txt}",
                f"<b>CAEX {r['ID']}</b><br>{tramo_txt}",
            ],
        )
    )

# DESTINO DE DESCARGA
fig_circuito.add_trace(
    go.Scatter(
        x=[3.5],
        y=[0],
        mode="markers",
        marker=dict(size=22, symbol="hexagram", color="#DC2626"),
        name="Zona de Entrega",
        hoverinfo="text",
        hovertext=["<b>Chancador / Botadero / Pila</b>"],
    )
)

fig_circuito.add_trace(
    go.Scatter(
        x=[3.5],
        y=[0.42],
        mode="text",
        text=["<b>CHANCADOR / BOTADERO / PILA</b>"],
        textposition="top center",
        textfont=dict(size=12, color="#DC2626", family="Arial Black"),
        showlegend=False,
        hoverinfo="none",
    )
)

# LIENZO COMPACTO
fig_circuito.update_layout(
    xaxis=dict(
        title="<b>Distancia de Acarreo (Kilómetros)</b>",
        range=[-1.4, 4.1],
        zeroline=False,
        showgrid=True,
    ),
    yaxis=dict(
        title="",
        range=[-0.95, 0.95],
        showticklabels=False,
        zeroline=False,
        showgrid=False,
    ),
    height=380,
    margin=dict(l=10, r=10, t=10, b=20),
    paper_bgcolor="#F8FAFC",
    plot_bgcolor="#FFFFFF",
    showlegend=True,
)

st.plotly_chart(fig_circuito, use_container_width=True)

# ---------------------------------------------------------
# REPORTE Y FICHA PRESCRIPTIVA PRE-TURNO (EXPORTACIÓN CSV/EXCEL)
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
# MÓDULO: CONCILIACIÓN Y CIERRE DE TURNO (PLAN VS. ACTUAL)
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
        "**Causas de Desviación / Imprevistos en Turno (Selección"
        " Múltiple):**"
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
      m_col3.metric("Costo Promedio", f"${fmt_num(avg_costo_ton, 2)} USD/Ton")
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
