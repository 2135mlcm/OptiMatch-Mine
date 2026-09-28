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
# INICIALIZACIÓN DE BASE DE DATOS
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
# FUNCIONES AUXILIARES Y BASE64 EXCLUSIVO DE TUS IMÁGENES
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
# CONFIGURACIÓN PÁGINA Y CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="OptiMatch Mine - Control de Flota", page_icon="⛏️", layout="wide"
)

st.markdown(
    """
    <style>
    .stApp { background-color: #FFFFFF !important; color: #0F172A !important; }
    .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: #0F172A !important; }
    section[data-testid="stSidebar"] { background-color: #334155 !important; border-right: 2px solid #F59E0B !important; }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3, 
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span, section[data-testid="stSidebar"] p {
        color: #F8FAFC !important; font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] input {
        background-color: #0F172A !important; color: #FFFFFF !important; border: 1px solid #F59E0B !important;
        border-radius: 6px !important; text-align: center !important; font-weight: bold !important;
    }
    .orange-container-box {
        background-color: #1E293B; border: 2px solid #F59E0B; border-radius: 8px; padding: 4px 8px !important; margin-bottom: 6px !important;
    }
    div[data-baseweb="select"], div[data-baseweb="select"] * { background-color: #0F172A !important; color: #FFFFFF !important; }
    div[data-baseweb="select"] > div { border: 1px solid #F59E0B !important; border-radius: 6px !important; }
    .title-box {
        background-color: #F8FAFC; padding: 20px 40px; border-radius: 12px; border: 2px solid #D97706;
        box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.08); text-align: center; width: fit-content; margin: 10px auto 25px auto;
    }
    .centered-title { text-align: center !important; width: 100% !important; margin-top: 20px !important; margin-bottom: 15px !important; }
    div[data-testid="stDataFrame"] { background-color: #F1F5F9 !important; border: 2px solid #CBD5E1 !important; border-radius: 10px; }
    div.stButton > button[kind="primary"] {
        background-color: #DC2626 !important; color: #FFFFFF !important; border: none !important;
        font-weight: 900 !important; font-size: 16px !important; border-radius: 30px !important; height: 48px !important;
    }
    div.stButton > button[kind="primary"]:hover { background-color: #B91C1C !important; }
    </style>
""",
    unsafe_allow_html=True,
)

LOGO_PATH = "Logo_OptiMatch.png"

# ---------------------------------------------------------
# AUTENTICACIÓN
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
          "<div style='text-align: center; background-color: #1E293B; padding:"
          " 20px; border-radius: 15px; border: 2px solid #F59E0B;'><h1"
          " style='color: #F59E0B;'>⛏️ OptiMatch Mine</h1></div>",
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
          st.error("❌ Usuario o contraseña no registrados.")
  st.stop()

# ---------------------------------------------------------
# ENCABEZADO Y CARÁTULA
# ---------------------------------------------------------
if os.path.exists(LOGO_PATH):
  c_hdr1, c_hdr2, c_hdr3 = st.columns([1, 1.2, 1])
  with c_hdr2:
    st.image(LOGO_PATH, use_container_width=True)

st.markdown(
    """
    <div class="title-box">
        <h1 style="color: #0F172A; margin: 0; font-size: 28px; font-weight: 800;">OptiMatch Mine — Control de Flota</h1>
        <p style="color: #0284C7; margin: 6px 0 0 0; font-size: 14px; font-weight: 800;">
            SISTEMA PRESCRIPTIVO DE DECISIONES PRE-TURNO PARA LA MEDIANA MINERÍA
        </p>
    </div>
""",
    unsafe_allow_html=True,
)

st.markdown("---")

# ---------------------------------------------------------
# BARRA LATERAL
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

st.sidebar.markdown(
    f"<div class='auto-box'>{nombre_dia_actual}, {fecha_str}</div>",
    unsafe_allow_html=True,
)

hora_str = now_dt.strftime("%H:%M:%S")

tipo_turno_sel = st.sidebar.selectbox(
    "RÉGIMEN DE TRABAJO",
    ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"],
    key="select_regimen_box",
)
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"
turno_seleccionado = st.sidebar.selectbox(
    "TURNO OPERATIVO",
    ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"],
    key="select_turno_box",
)
horas_turno = st.sidebar.number_input(
    "Horas Efectivas Turno", value=10.0, step=0.5
)

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
factor_yodo = st.sidebar.number_input(
    "Ton Mineral / Ton Cu Fino", value=120.0, step=0.1
)
valor_ton_usd = st.sidebar.number_input(
    "USD / Ton Mineral", value=15.50, step=0.01
)
distancia_acarreo_km = st.sidebar.number_input(
    "Distancia Promedio Acarreo (km)", value=3.5, step=0.5
)

# ---------------------------------------------------------
# TABLAS DE FLOTA
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

st.markdown("<h2 class='centered-title'>🚜 Flota Operativa</h2>", unsafe_allow_html=True)
col_t1, col_t2, col_t3 = st.columns(3)

with col_t1:
  st.markdown("### Palas de Carguío")
  ed_palas = st.data_editor(
      st.session_state.palas_df, hide_index=True, key="ed_palas"
  )

with col_t2:
  st.markdown("### Cargadores Frontales")
  ed_cf = st.data_editor(
      st.session_state.cf_df, hide_index=True, key="ed_cf"
  )

with col_t3:
  st.markdown("### Camiones CAEX")
  ed_caex = st.data_editor(
      st.session_state.caex_df, hide_index=True, key="ed_caex"
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

ingreso_bruto_usd = tonelaje_proyectado * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - costo_opex_total_turno
costo_unitario_ton = (
    (costo_opex_total_turno / tonelaje_proyectado)
    if tonelaje_proyectado > 0
    else 0
)

# ---------------------------------------------------------
# MÓDULO DE SEGUIMIENTO ESPACIAL EN DOS VÍAS CON TOOLTIP CLIC
# ---------------------------------------------------------
st.markdown("---")
st.subheader("MAPA ESPACIAL DE CIRCUITO DE ACARREO")

if "acarreo_iniciado" not in st.session_state:
  st.session_state.acarreo_iniciado = False

dist_km_val = (
    distancia_acarreo_km if "distancia_acarreo_km" in locals() else 3.5
)

st.markdown(
    f"""
    <div style="background-color: #0F172A; border: 3px solid #F59E0B; border-radius: 10px; padding: 12px 20px; text-align: center; margin-bottom: 15px;">
        <span style="color: #FFFFFF !important; font-size: 20px !important; font-weight: 900 !important;">
            DISTANCIA OFICIAL DE ACARREO: 
            <span style="color: #38BDF8 !important; font-size: 24px !important; font-weight: 900 !important;">{dist_km_val:.1f} KM (IDA)</span> / 
            <span style="color: #EF4444 !important; font-size: 24px !important; font-weight: 900 !important;">{dist_km_val:.1f} KM (RETORNO)</span>
        </span>
    </div>
""",
    unsafe_allow_html=True,
)

col_trig1, col_trig2, col_trig3 = st.columns([1.8, 3.2, 1.5])
with col_trig1:
  if st.button(
      "🔴   INICIO DE ACARREO", type="primary", use_container_width=True
  ):
    st.session_state.acarreo_iniciado = True
    st.success("✅ Acarreo iniciado por confirmación VHF.")

with col_trig2:
  st.markdown(
      "<div style='padding: 10px 0px;'><span style='color: #0F172A;"
      " font-weight: 900;'>📻 <b>AVISO RADIO VHF:</b> Presione el botón rojo"
      " para autorizar el zarpe tras el primer balde cargado.</span></div>",
      unsafe_allow_html=True,
  )

with col_trig3:
  if st.button("🔄 Reiniciar Postura", use_container_width=True):
    st.session_state.acarreo_iniciado = False

# CARGA DE IMÁGENES EXACTAS SIN FALLBACKS DE ICONOS WEB
img_pala_b64 = obtener_base64_img("image_859ef9.png") or obtener_base64_img(
    "Gif Pala.jpg"
)
img_cf_b64 = obtener_base64_img("image_859f19.png") or obtener_base64_img(
    "Gif Cargador Frontal.jpg"
)
img_caex_vacio_b64 = obtener_base64_img("image_85a67d.png")
img_caex_cargado_b64 = obtener_base64_img("image_86137b.png")

fig_circuito = go.Figure()

# Vía Ida Cargado
fig_circuito.add_trace(
    go.Scatter(
        x=[0, 3.5],
        y=[0.15, 0.15],
        mode="lines",
        line=dict(color="#10B981", width=6, dash="dash"),
        name=f"Vía Ida Cargado ({dist_km_val:.1f} km)",
        hoverinfo="none",
    )
)

# Vía Retorno Vacío
fig_circuito.add_trace(
    go.Scatter(
        x=[0, 3.5],
        y=[-0.15, -0.15],
        mode="lines",
        line=dict(color="#DC2626", width=6, dash="solid"),
        name=f"Vía Retorno Vacío ({dist_km_val:.1f} km)",
        hoverinfo="none",
    )
)

# 1. RENDERIZADO DE PALAS Y CARGADORES CON DETALLES AL HACER CLIC
idx_pala = 0
for _, r in ed_palas.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    pos_y = 0.40 + (idx_pala * 0.28)

    if img_pala_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_pala_b64,
              xref="x",
              yref="y",
              x=-0.15,
              y=pos_y,
              sizex=0.35,
              sizey=0.35,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    # Capa interactiva de clic sobre la imagen
    hover_details = (
        f"<b>EQUIPO DE CARGUÍO: Pala {r['ID']}</b><br>"
        f"• Modelo: {r['Modelo']}<br>"
        f"• Operador Asignado: {r['Operador']}<br>"
        f"• Rendimiento: {r['Rend_TonH']} Ton/h<br>"
        f"• Consumo Diésel: {r['Consumo_LtsH']} Lts/h<br>"
        f"• Costo Fijo: ${r['Costo_USDH']} USD/h"
    )

    fig_circuito.add_trace(
        go.Scatter(
            x=[-0.15, 0.18],
            y=[pos_y, pos_y],
            mode="markers+text",
            marker=dict(size=[28, 1], opacity=[0.01, 0]),
            text=["", f"<b>Pala {r['ID']}</b>"],
            textposition="middle right",
            textfont=dict(size=11, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[hover_details, hover_details],
        )
    )
    idx_pala += 1

idx_cf = 0
for _, r in ed_cf.iterrows():
  if r["Agendar"] and r["Estado"] == "🟢 Disponible":
    pos_y = -0.38 - (idx_cf * 0.28)

    if img_cf_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_cf_b64,
              xref="x",
              yref="y",
              x=-0.15,
              y=pos_y,
              sizex=0.35,
              sizey=0.35,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    hover_details = (
        f"<b>EQUIPO DE CARGUÍO: Cargador {r['ID']}</b><br>"
        f"• Modelo: {r['Modelo']}<br>"
        f"• Operador Asignado: {r['Operador']}<br>"
        f"• Rendimiento: {r['Rend_TonH']} Ton/h<br>"
        f"• Consumo Diésel: {r['Consumo_LtsH']} Lts/h<br>"
        f"• Costo Fijo: ${r['Costo_USDH']} USD/h"
    )

    fig_circuito.add_trace(
        go.Scatter(
            x=[-0.15, 0.18],
            y=[pos_y, pos_y],
            mode="markers+text",
            marker=dict(size=[28, 1], opacity=[0.01, 0]),
            text=["", f"<b>CF {r['ID']}</b>"],
            textposition="middle right",
            textfont=dict(size=11, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[hover_details, hover_details],
        )
    )
    idx_cf += 1

# 2. RENDERIZADO DE CAMIONES CAEX Y FICHA TÉCNICA
caex_agendados = ed_caex[ed_caex["Agendar"] == True]
total_caex_count = len(caex_agendados)

if not st.session_state.acarreo_iniciado:
  for i, (_, r) in enumerate(caex_agendados.iterrows()):
    pos_x = 0.0 - (i * 0.32)
    pos_y = 0.15

    if img_caex_vacio_b64:
      fig_circuito.add_layout_image(
          dict(
              source=img_caex_vacio_b64,
              xref="x",
              yref="y",
              x=pos_x,
              y=pos_y + 0.10,
              sizex=0.32,
              sizey=0.32,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    hover_caex = (
        f"<b>CAMIÓN MINERO CAEX {r['ID']}</b><br>"
        f"• Estado: En Fila de Espera (Pala)<br>"
        f"• Modelo: {r['Modelo']}<br>"
        f"• Operador: {r['Operador']}<br>"
        f"• Carga Actual: 0.0 Ton (Vacío)<br>"
        f"• Consumo Diésel: {r['Consumo_LtsH']} Lts/h<br>"
        f"• Costo OPEX: ${r['Costo_USDH']} USD/h"
    )

    fig_circuito.add_trace(
        go.Scatter(
            x=[pos_x, pos_x],
            y=[pos_y + 0.10, pos_y - 0.14],
            mode="markers+text",
            marker=dict(size=[30, 1], opacity=[0.01, 0]),
            text=["", f"<b>C{r['ID']}</b>"],
            textposition="bottom center",
            textfont=dict(size=10, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[hover_caex, hover_caex],
        )
    )

  st.info(
      "📍 **FLOTA PARQUEADA EN FILA:** Presione '🔴 INICIO DE ACARREO' para"
      " despejar la ruta."
  )

else:
  for i, (_, r) in enumerate(caex_agendados.iterrows()):
    es_ida = i % 2 == 0

    if es_ida:
      pos_x = 0.4 + (i * (2.6 / max(1, total_caex_count)))
      pos_y = 0.15
      src_b64 = img_caex_cargado_b64
      carga_txt = "44.6 Ton (Cargado)"
      label_txt = f"<b>C{r['ID']} (44.6T)</b>"
      tramo_txt = "Acarreo Cargado -> Chancador/Pila"
    else:
      pos_x = 3.1 - (i * (2.6 / max(1, total_caex_count)))
      pos_y = -0.15
      src_b64 = img_caex_vacio_b64
      carga_txt = "0.0 Ton (Vacío)"
      label_txt = f"<b>C{r['ID']} (0T)</b>"
      tramo_txt = "Retorno Vacío -> Pala"

    if src_b64:
      fig_circuito.add_layout_image(
          dict(
              source=src_b64,
              xref="x",
              yref="y",
              x=pos_x,
              y=pos_y + 0.10,
              sizex=0.34,
              sizey=0.34,
              xanchor="center",
              yanchor="middle",
              layer="above",
          )
      )

    hover_caex = (
        f"<b>CAMIÓN MINERO CAEX {r['ID']}</b><br>"
        f"• Tramo: {tramo_txt}<br>"
        f"• Modelo: {r['Modelo']}<br>"
        f"• Operador Asignado: {r['Operador']}<br>"
        f"• Capacidad / Carga: {carga_txt}<br>"
        f"• Rendimiento Acarreo: {r['Rend_TonH']} Ton/h<br>"
        f"• Consumo Diésel: {r['Consumo_LtsH']} Lts/h<br>"
        f"• Costo Fijo Turno: ${r['Costo_USDH']} USD/h"
    )

    fig_circuito.add_trace(
        go.Scatter(
            x=[pos_x, pos_x],
            y=[pos_y + 0.10, pos_y - 0.14],
            mode="markers+text",
            marker=dict(size=[32, 1], opacity=[0.01, 0]),
            text=["", label_txt],
            textposition="bottom center",
            textfont=dict(size=10, color="#0F172A", family="Arial Black"),
            showlegend=False,
            hoverinfo="text",
            hovertext=[hover_caex, hover_caex],
        )
    )

# 3. DESTINO DE DESCARGA
fig_circuito.add_trace(
    go.Scatter(
        x=[3.5],
        y=[0],
        mode="markers",
        marker=dict(size=26, symbol="hexagram", color="#DC2626"),
        name="Zona de Entrega",
        hoverinfo="text",
        hovertext=[
            "<b>Chancador / Botadero / Pila</b><br>Punto de Descarga Final"
        ],
    )
)

fig_circuito.add_trace(
    go.Scatter(
        x=[3.5],
        y=[0.48],
        mode="text",
        text=["<b>CHANCADOR / BOTADERO / PILA</b>"],
        textposition="top center",
        textfont=dict(size=13, color="#DC2626", family="Arial Black"),
        showlegend=False,
        hoverinfo="none",
    )
)

fig_circuito.update_layout(
    xaxis=dict(
        title="<b>Distancia de Acarreo (Kilómetros)</b>",
        range=[-1.5, 4.2],
        zeroline=False,
        showgrid=True,
    ),
    yaxis=dict(
        title="",
        range=[-1.1, 1.1],
        showticklabels=False,
        zeroline=False,
        showgrid=False,
    ),
    height=460,
    margin=dict(l=20, r=20, t=30, b=30),
    paper_bgcolor="#F8FAFC",
    plot_bgcolor="#FFFFFF",
    showlegend=True,
)

st.plotly_chart(fig_circuito, use_container_width=True)
