# ==============================================================================
# 1. IMPORTS
# ==============================================================================
from contextlib import closing
from datetime import date, datetime
import base64
import hashlib
import hmac
import html
import json
import os
import secrets as pysecrets
import sqlite3
import urllib.request
 
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components
 
from motor import (
    FACTORES_MATERIAL, PERFIL_RAMPAS, T_CARGUIO_MIN, T_MANIOBRAS_MIN, FACTOR_CO2_KG_L,
    ejecutar_simulacion_analitica, barrido_flota, prescribir_n, clasificar_semaforo,
)
 
# ==============================================================================
# 2. CONFIGURACIÓN DE PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="OptiMatch Mine v3.1 — Control Prescriptivo",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded",
)
 
# ==============================================================================
# 3. BASE DE DATOS (optimatch.db) Y USUARIOS
#    Las credenciales NO están en el código: se leen de st.secrets
#    (.streamlit/secrets.toml en local o "Secrets" en Streamlit Cloud)
#    y se guardan en la BD solo como hash PBKDF2-SHA256.
# ==============================================================================
DB_FILE = "optimatch.db"
PBKDF2_ITER = 200_000
 
 
def conectar():
    return sqlite3.connect(DB_FILE)
 
 
def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt_hex = salt_hex or pysecrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITER)
    return h.hex(), salt_hex
 
 
def usuarios_desde_secrets() -> dict:
    try:
        return {k: dict(v) for k, v in st.secrets["usuarios"].items()}
    except Exception:
        return {}
 
 
COLUMNAS_HISTORICO = {
    "num_agendamiento": "TEXT", "fecha_registro": "TEXT", "hora_registro": "TEXT", "faena": "TEXT",
    "turno": "TEXT", "regimen_guardia": "TEXT", "jefe_turno": "TEXT", "perfil_rampa": "TEXT",
    "n_caex": "INTEGER DEFAULT 0", "n_carguio": "INTEGER DEFAULT 0",
    "ton_movidas": "REAL", "ton_efectivas": "REAL DEFAULT 0.0", "merma_ton": "REAL DEFAULT 0.0",
    "consumo_diesel_lts": "REAL", "costo_diesel_usd": "REAL", "opex_total_usd": "REAL",
    "costo_ton_usd": "REAL", "costo_total_usd": "REAL DEFAULT 0.0", "costo_total_ton_usd": "REAL DEFAULT 0.0",
    "beneficio_neto_usd": "REAL", "match_factor": "REAL", "espera_min": "REAL DEFAULT 0.0",
    "disponibilidad_fisica": "REAL DEFAULT 100.0", "factor_llenado": "REAL DEFAULT 0.88",
    "n_prescrito": "INTEGER", "en_banda_verde": "INTEGER DEFAULT 0", "prescripcion_aceptada": "INTEGER DEFAULT 0",
}
 
 
def init_db():
    with closing(conectar()) as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        nombre_completo TEXT NOT NULL,
                        rol TEXT NOT NULL)""")
        cols_u = [r[1] for r in c.execute("PRAGMA table_info(usuarios)").fetchall()]
        if "salt" not in cols_u:
            # La versión 3.0 guardaba contraseñas en texto plano: se eliminan y se regeneran desde secrets.
            c.execute("DELETE FROM usuarios")
            c.execute("ALTER TABLE usuarios ADD COLUMN salt TEXT")
 
        # Sincroniza usuarios desde secrets (alta o actualización, nunca texto plano)
        for username, datos in usuarios_desde_secrets().items():
            fila = c.execute("SELECT password, salt FROM usuarios WHERE username = ?", (username,)).fetchone()
            if fila and fila[1] and hmac.compare_digest(hash_password(datos["password"], fila[1])[0], fila[0]):
                c.execute("UPDATE usuarios SET nombre_completo = ?, rol = ? WHERE username = ?",
                          (datos["nombre"], datos["rol"], username))
                continue
            h, salt = hash_password(datos["password"])
            c.execute("""INSERT INTO usuarios (username, password, salt, nombre_completo, rol) VALUES (?, ?, ?, ?, ?)
                         ON CONFLICT(username) DO UPDATE SET password = excluded.password, salt = excluded.salt,
                         nombre_completo = excluded.nombre_completo, rol = excluded.rol""",
                      (username, h, salt, datos["nombre"], datos["rol"]))
 
        c.execute("CREATE TABLE IF NOT EXISTS historico_agendamientos (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        existentes = [r[1] for r in c.execute("PRAGMA table_info(historico_agendamientos)").fetchall()]
        for col, tipo in COLUMNAS_HISTORICO.items():
            if col not in existentes:
                c.execute(f"ALTER TABLE historico_agendamientos ADD COLUMN {col} {tipo}")
 
        c.execute("""CREATE TABLE IF NOT EXISTS cierres_turno (
                        num_agendamiento TEXT PRIMARY KEY,
                        fecha_cierre TEXT, responsable TEXT,
                        ton_reales REAL, diesel_real_lts REAL,
                        costo_real_usd REAL, costo_real_ton_usd REAL, adherencia_pct REAL,
                        causas TEXT, observaciones TEXT)""")
        conn.commit()
 
 
def validar_usuario(usr, pwd):
    with closing(conectar()) as conn:
        fila = conn.execute("SELECT username, nombre_completo, rol, password, salt FROM usuarios WHERE username = ?",
                            (usr,)).fetchone()
    if not fila or not fila[4]:
        return None
    if hmac.compare_digest(hash_password(pwd, fila[4])[0], fila[3]):
        return fila[:3]
    return None
 
 
def obtener_siguiente_agendamiento():
    with closing(conectar()) as conn:
        ultimo = conn.execute("SELECT COALESCE(MAX(id), 0) FROM historico_agendamientos").fetchone()[0]
    return f"AGN-{datetime.now().year}-{ultimo + 1:03d}"
 
 
def guardar_agendamiento_db(registro: dict):
    cols = ", ".join(registro.keys())
    marcas = ", ".join(["?"] * len(registro))
    with closing(conectar()) as conn:
        conn.execute(f"INSERT INTO historico_agendamientos ({cols}) VALUES ({marcas})", list(registro.values()))
        conn.commit()
 
 
def guardar_cierre_db(registro: dict):
    cols = ", ".join(registro.keys())
    marcas = ", ".join(["?"] * len(registro))
    with closing(conectar()) as conn:
        conn.execute(f"INSERT OR REPLACE INTO cierres_turno ({cols}) VALUES ({marcas})", list(registro.values()))
        conn.commit()
 
 
def borrar_historico_db():
    with closing(conectar()) as conn:
        conn.execute("DELETE FROM historico_agendamientos")
        conn.execute("DELETE FROM cierres_turno")
        conn.commit()
 
 
init_db()
 
# ==============================================================================
# 4. FUNCIONES AUXILIARES
# ==============================================================================
@st.cache_data(ttl=3600)
def obtener_indicadores_mercado():
    """Dólar observado (mindicador.cl). El diésel de referencia supone 1.080 CLP/L."""
    try:
        req = urllib.request.Request("https://mindicador.cl/api", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            usd_clp = float(data["dolar"]["valor"])
            return usd_clp, round(1080.0 / usd_clp, 2), True
    except Exception:
        return 940.0, 1.15, False
 
 
def fmt_num(val, dec=0):
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "—"
    if dec == 0:
        return f"{val:,.0f}".replace(",", ".")
    entero, decimal = f"{val:,.{dec}f}".split(".")
    return f"{entero.replace(',', '.')},{decimal}"
 
 
def obtener_base64_img(nombre_archivo):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for r in (nombre_archivo, os.path.join(os.getcwd(), nombre_archivo),
              os.path.join(base_dir, nombre_archivo), os.path.join(os.getcwd(), "static", nombre_archivo)):
        if os.path.exists(r):
            try:
                with open(r, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode()
                ext = r.rsplit(".", 1)[-1].lower()
                mime = {"png": "png", "gif": "gif", "jpg": "jpeg", "jpeg": "jpeg"}.get(ext, "png")
                return f"data:image/{mime};base64,{encoded}"
            except OSError:
                pass
    return None
 
 
DISPONIBLE = "🟢 Disponible"
OPCIONES_ESTADO = [DISPONIBLE, "🟡 Mantenimiento / Resguardo", "🔴 Falla Mecánica"]
 
# ==============================================================================
# 5. ESTILOS VISUALES
# ==============================================================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }
    .block-container { padding: 1rem 1.5rem 1.5rem 1.5rem !important; }
    .stApp { background-color: #F1F5F9 !important; color: #0F172A !important; }
    section[data-testid="stSidebar"] {
        background-color: #0F172A !important; border-right: 2px solid #F59E0B !important;
        box-shadow: 4px 0px 15px rgba(0,0,0,0.15) !important;
    }
    section[data-testid="stSidebar"] > div:first-child { padding-top: 1rem !important; padding-bottom: 1rem !important; }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span, section[data-testid="stSidebar"] p {
        color: #F8FAFC !important; font-weight: 700 !important; margin-top: 2px !important; margin-bottom: 2px !important;
    }
    section[data-testid="stSidebar"] hr { margin: 8px 0 !important; border-color: #334155 !important; }
    section[data-testid="stSidebar"] input {
        background-color: #1E293B !important; color: #FFFFFF !important; border: 1px solid #334155 !important;
        border-radius: 8px !important; text-align: center !important; font-weight: bold !important; padding: 4px 8px !important;
    }
    section[data-testid="stSidebar"] input:focus { border-color: #F59E0B !important; }
    div[data-baseweb="select"], div[data-baseweb="select"] *, div[data-baseweb="select"] > div,
    div[data-baseweb="select"] div[role="button"] { background-color: #1E293B !important; color: #FFFFFF !important; border-color: #F59E0B !important; }
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
        color: #F59E0B !important; font-size: 11px !important; font-weight: 900 !important; text-align: center !important;
        display: block !important; margin-bottom: 2px !important; letter-spacing: 0.3px;
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
        background-color: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 10px !important;
        padding: 10px 12px !important; box-shadow: 0px 2px 8px rgba(0, 0, 0, 0.03) !important; text-align: center !important;
    }
    div[data-testid="stMetricLabel"] p { color: #64748B !important; font-weight: 800 !important; font-size: 11px !important; }
    div[data-testid="stMetricValue"] div { color: #0F172A !important; font-size: 18px !important; font-weight: 900 !important; white-space: nowrap !important; }
    .mf-label { font-size: 16px !important; font-weight: 800 !important; color: #0F172A !important; margin-bottom: 2px !important; }
    .mf-value { font-size: 34px !important; font-weight: 900 !important; color: #0284C7 !important; margin-top: 0px !important; }
    .highlight-red-large { color: #DC2626 !important; font-size: 14px !important; font-weight: 800 !important; margin-bottom: 4px !important; }
    .adh-green-large { color: #10B981 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }
    .adh-red-large { color: #EF4444 !important; font-size: 18px !important; font-weight: 900 !important; margin-bottom: 4px !important; }
    div[data-testid="stDataFrame"] { background-color: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 10px; }
    div.stButton > button[kind="primary"] {
        background-color: #EF4444 !important; color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important;
        border: none !important; font-weight: 900 !important; font-size: 13px !important; border-radius: 8px !important;
        height: 38px !important; padding: 0px 14px !important;
    }
    div.stButton > button[kind="primary"] p, div.stButton > button[kind="primary"] span {
        color: #FFFFFF !important; -webkit-text-fill-color: #FFFFFF !important; font-weight: 900 !important;
    }
    div.stButton > button[kind="primary"]:hover { background-color: #DC2626 !important; }
    hr { margin-top: 12px !important; margin-bottom: 12px !important; }
    .tabla-om-wrap { overflow: auto; border-radius: 10px; border: 2px solid #0F172A; margin-bottom: 10px; }
    .tabla-om { width: 100%; border-collapse: collapse; font-size: 12.5px; color: #0F172A; background: #FFFFFF; }
    .tabla-om th { background: #0F172A; color: #FFFFFF; font-weight: 900; font-size: 11.5px; padding: 8px 6px;
                   border-bottom: 3px solid #F59E0B; border-right: 1px solid #334155; white-space: normal;
                   min-width: 78px; line-height: 1.25; text-align: center; vertical-align: middle; position: sticky; top: 0; z-index: 2; }
    .tabla-om td { padding: 7px 8px; border-bottom: 1px solid #CBD5E1; border-right: 1px solid #E2E8F0;
                   white-space: nowrap; text-align: center; font-weight: 700; }
    .tabla-om tr:nth-child(even) td { background-color: #F1F5F9; }
    .tabla-om tr:hover td { background-color: #FEF3C7; }
    </style>
""", unsafe_allow_html=True)
 
 
def tabla_html(df: pd.DataFrame, fondos: dict | None = None, max_height: int | None = None) -> str:
    """Tabla HTML con el estilo institucional. fondos = {columna: función(valor) -> color}."""
    fondos = fondos or {}
    enc = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    cuerpo = ""
    for _, fila in df.iterrows():
        celdas = ""
        for c in df.columns:
            color = fondos[c](fila[c]) if c in fondos else ""
            estilo = f' style="background:{color};"' if color else ""
            celdas += f"<td{estilo}>{html.escape(str(fila[c]))}</td>"
        cuerpo += f"<tr>{celdas}</tr>"
    alto = f' style="max-height:{max_height}px;"' if max_height else ""
    return f'<div class="tabla-om-wrap"{alto}><table class="tabla-om"><thead><tr>{enc}</tr></thead><tbody>{cuerpo}</tbody></table></div>'
 
 
COLOR_SEMAFORO = {"Verde": "#BBF7D0", "Amarillo": "#FDE68A", "Rojo": "#FECACA"}
 
 
def fondo_mf(valor):
    try:
        return COLOR_SEMAFORO[clasificar_semaforo(float(str(valor).replace(",", ".")))]
    except (TypeError, ValueError):
        return ""
 
 
LOGO_PATH = "Logo_OptiMatch.png"
LOGO_ATACAMA_PATH = "Logo_Atacama_Norte.png"
 
# ==============================================================================
# 6. AUTENTICACIÓN
# ==============================================================================
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
 
if not st.session_state.autenticado:
    _, col_l2, _ = st.columns([1, 2, 1])
    with col_l2:
        st.markdown("<br>", unsafe_allow_html=True)
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=320)
        else:
            st.markdown("""
                <div style="text-align: center; background-color: #0F172A; padding: 25px; border-radius: 16px; border: 2px solid #F59E0B;">
                    <h1 style="color: #F59E0B; font-size: 34px; margin-bottom: 0px; font-weight: 900;">⛏️ OptiMatch Mine</h1>
                    <h3 style="color: #F8FAFC; margin-top: 5px; font-size: 16px; font-weight: 600;">Control Prescriptivo Pre-Turno</h3>
                </div>""", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; font-weight: 700; font-size: 13px; color: #64748B; margin-top: 10px;'>"
                    "Acceso restringido por perfil | Universidad Alberto Hurtado</p>", unsafe_allow_html=True)
 
        if not usuarios_desde_secrets():
            st.error("No hay usuarios configurados. Cree el archivo .streamlit/secrets.toml (ver "
                     "secrets.toml.example) o cargue los usuarios en Settings → Secrets de Streamlit Cloud.")
            st.stop()
 
        with st.form("login_form_secure", clear_on_submit=True):
            usuario = st.text_input("Usuario", placeholder="Ingrese usuario...", autocomplete="off")
            clave = st.text_input("Contraseña", type="password", placeholder="Ingrese contraseña...",
                                  autocomplete="current-password")
            if st.form_submit_button("🔑 Ingresar a la plataforma", use_container_width=True):
                datos_val = validar_usuario(usuario.strip(), clave.strip())
                if datos_val:
                    st.session_state.autenticado = True
                    st.session_state.user_id, st.session_state.usuario_activo, st.session_state.rol_activo = datos_val
                    st.session_state.hora_ingreso = datetime.now()
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")
    st.stop()
 
USUARIO_ACTIVO = st.session_state.usuario_activo
ROL_ACTIVO = st.session_state.rol_activo
 
# ==============================================================================
# 7. CARÁTULA Y BARRA LATERAL
# ==============================================================================
if os.path.exists(LOGO_PATH):
    _, c_hdr2, _ = st.columns([1, 1.2, 1])
    with c_hdr2:
        st.image(LOGO_PATH, use_container_width=True)
 
st.markdown("""
    <div class="main-title-card">
        <h1 style="color: #0F172A; margin: 0; font-size: 24px; font-weight: 900;">OptiMatch Mine — Control Prescriptivo v3.1</h1>
        <p style="color: #0284C7; margin: 2px 0 0 0; font-size: 12px; font-weight: 800;">
            Sistema de soporte a la decisión pre-turno para la mediana minería</p>
        <p style="color: #64748B; margin: 2px 0 0 0; font-size: 11px; font-weight: 600;">
            Match carguío-transporte, colas, diésel y costo unitario | Universidad Alberto Hurtado</p>
    </div>""", unsafe_allow_html=True)
 
b64_logo_sidebar = obtener_base64_img(LOGO_PATH)
if b64_logo_sidebar:
    st.sidebar.markdown(
        f'<div style="background:#FFFFFF; border-radius:10px; padding:6px; margin-bottom:6px; text-align:center;">'
        f'<img src="{b64_logo_sidebar}" style="width:100%; height:auto; display:block;"></div>', unsafe_allow_html=True)
st.sidebar.header("Registro Operativo Mina")
 
 
def etiqueta_lateral(texto):
    st.sidebar.markdown(f'<span class="selector-label-centered">{texto}</span>', unsafe_allow_html=True)
 
 
etiqueta_lateral("Nombre de la mina / faena")
nombre_mina = st.sidebar.text_input("Nombre de la mina", value="Mina Atacama Norte", label_visibility="collapsed")
 
etiqueta_lateral("N° de agendamiento correlativo")
num_agendamiento = st.sidebar.text_input("N° de agendamiento", value=obtener_siguiente_agendamiento(),
                                         label_visibility="collapsed")
st.sidebar.markdown("---")
 
now_dt = datetime.now()
fecha_str = now_dt.strftime("%d/%m/%Y")
hora_str = now_dt.strftime("%H:%M:%S")
nombre_dia_actual = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][now_dt.weekday()]
 
st.sidebar.markdown("<label style='font-size:11px;'>Fecha de agendamiento</label>", unsafe_allow_html=True)
st.sidebar.markdown(f'<div class="auto-box">{nombre_dia_actual}, {fecha_str}</div>', unsafe_allow_html=True)
st.sidebar.markdown("<label style='font-size:11px;'>Hora de agendamiento</label>", unsafe_allow_html=True)
with st.sidebar:
    components.html("""
        <div id="reloj_vivo" style="background-color: #1E293B; border: 1px solid #F59E0B; border-radius: 6px; padding: 4px;
             text-align: center; font-size: 14px; font-weight: 800; color: #F59E0B; font-family: sans-serif;"></div>
        <script>
            function actualizarReloj() {
                var n = new Date(); var p = function(x){ return String(x).padStart(2, '0'); };
                document.getElementById('reloj_vivo').innerHTML = p(n.getHours()) + ':' + p(n.getMinutes()) + ':' + p(n.getSeconds());
            }
            setInterval(actualizarReloj, 1000); actualizarReloj();
        </script>""", height=38)
 
st.sidebar.markdown(f"""
    <div style="background-color: #1E293B; padding: 6px 8px; border-radius: 8px; border: 1px solid #334155; margin: 4px 0 6px 0; text-align: center;">
        <span style="color: #F59E0B !important; font-size: 9px; font-weight: 800; display: block;">Usuario responsable</span>
        <span style="color: #FFFFFF !important; font-size: 13px; font-weight: 900; display: block;">👤 {html.escape(USUARIO_ACTIVO)}</span>
        <span style="color: #F59E0B !important; font-size: 9px; font-weight: 800; display: block;">Perfil: {html.escape(ROL_ACTIVO)}</span>
    </div>""", unsafe_allow_html=True)
 
etiqueta_lateral("Régimen y guardia de trabajo")
tipo_turno_sel = st.sidebar.selectbox("Régimen", ["Turno 7x7", "Turno 4x3", "Turno 8x6", "Turno 5x2", "Otro"],
                                      label_visibility="collapsed")
regimen_guardia = f"{tipo_turno_sel} ({nombre_dia_actual})"
 
etiqueta_lateral("Turno operativo")
turno_seleccionado = st.sidebar.selectbox("Turno", ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"],
                                          label_visibility="collapsed")
horas_turno = st.sidebar.number_input("Horas efectivas del turno", value=10.0, min_value=1.0, max_value=24.0, step=0.5)
 
st.sidebar.markdown("---")
st.sidebar.header("⚙️ Material y llenado")
preset_fl = st.sidebar.select_slider("Tipo de material y factor de llenado", options=list(FACTORES_MATERIAL.keys()),
                                     value="Estándar (88%)")
fl_valor = FACTORES_MATERIAL[preset_fl]["fl"]
merma_base_valor = FACTORES_MATERIAL[preset_fl]["merma_base_pct"]
 
st.sidebar.markdown("---")
st.sidebar.header("Pistas de acarreo")
perfil_rampa_sel = st.sidebar.selectbox("Pendiente y calidad de camino", list(PERFIL_RAMPAS.keys()), index=1,
                                        help="El caso del artículo (pendiente media 6 %) usa «Rampa Moderada».")
 
st.sidebar.markdown("---")
st.sidebar.header("⛏️ Plan de producción del turno")
target_mineral_num = st.sidebar.number_input("Objetivo mineral (t)", value=18000, step=1000, min_value=0)
target_esteril_num = st.sidebar.number_input("Objetivo estéril (t)", value=12000, step=1000, min_value=0)
 
st.sidebar.markdown("---")
tc_mercado, diesel_mercado, mercado_ok = obtener_indicadores_mercado()
estado_mercado = "en vivo (mindicador.cl)" if mercado_ok else "sin conexión: valores de respaldo"
st.sidebar.markdown(f"""
    <div style="background-color: #1E293B; padding: 6px; border-radius: 6px; border: 1px solid #0284C7; text-align: center; margin-bottom: 6px;">
        <span style="color: #38BDF8 !important; font-size: 9px; font-weight: 800; display: block;">🌐 Mercado {estado_mercado}</span>
        <span style="color: #FFFFFF !important; font-size: 11px; font-weight: 700;">USD/CLP: ${fmt_num(tc_mercado, 1)} | Diésel ref.: {fmt_num(diesel_mercado, 2)} USD/L</span>
        <span style="color: #94A3B8 !important; font-size: 9px; display: block;">Diésel ref. = 1.080 CLP/L ÷ dólar observado</span>
    </div>""", unsafe_allow_html=True)
 
UNIDADES_MINERAL = {
    "Caliche / Yodo": ("Ton Caliche / kg Yodo", "USD / Ton Caliche", 3.91, 9.079),
    "Cobre (Cu)": ("Ton Mineral / Ton Cu Fino", "USD / Ton Mineral Cu", 120.0, 15.50),
    "Oro (Au)": ("Ton Mineral / Oz Au", "USD / Ton Mineral Au", 1.5, 18.20),
    "Plata (Ag)": ("Ton Mineral / Oz Ag", "USD / Ton Mineral Ag", 0.8, 12.00),
    "Hierro (Fe)": ("Ton Mineral / Ton Concentrado Fe", "USD / Ton Mineral Fe", 1.8, 8.50),
    "Litio (Li / LCE)": ("Ton Salmuera-Roca / Ton LCE", "USD / Ton Material Li", 50.0, 22.00),
    "Carbón / Energéticos": ("Ton ROM / Ton Carbón Limpio", "USD / Ton Carbón", 1.3, 7.00),
    "No Metálicos / Canteras": ("Ton Brutas / Ton Roca Comercial", "USD / Ton Material", 1.1, 5.00),
    "Movimiento de Tierras / Obras Civiles": ("m³ o Ton / Unidad Avance", "USD / Ton o m³ Movido", 1.0, 4.50),
}
tipo_mineral = st.sidebar.selectbox("Tipo de operación / mineral", list(UNIDADES_MINERAL.keys()), index=1)
label_razon, label_valor, default_razon, default_usd = UNIDADES_MINERAL[tipo_mineral]
 
precio_diesel = st.sidebar.number_input("Precio diésel (USD/L contrato)", value=float(diesel_mercado), step=0.01, min_value=0.0)
razon_metalurgica = st.sidebar.number_input(label_razon, value=float(default_razon), step=0.01)
valor_ton_usd = st.sidebar.number_input(f"Valor del material ({label_valor})", value=float(default_usd), step=0.01,
                                        help="Se usa para el beneficio neto y para comparar con el costo marginal.")
 
st.sidebar.markdown("---")
st.sidebar.header("🚛 Parámetros físicos de acarreo")
distancia_acarreo_km = st.sidebar.number_input("Distancia promedio de acarreo (km)", value=3.2, step=0.1, min_value=0.1)
vel_cargado_kmh = st.sidebar.number_input("Velocidad base cargado (km/h)", value=18.0, step=1.0, min_value=1.0)
vel_vacio_kmh = st.sidebar.number_input("Velocidad retorno vacío (km/h)", value=30.0, step=1.0, min_value=1.0)
st.sidebar.markdown("---")
 
# ==============================================================================
# 8. FLOTA INICIAL (palas, cargadores frontales y CAEX de 60, 90 y 140 t)
# ==============================================================================
if "palas_df" not in st.session_state:
    st.session_state.palas_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": DISPONIBLE, "ID": "PA622", "Modelo": "Liebherr R9200", "Horómetro Entrada": 14250.0, "Operador": "Carlos Araya", "Rend_TonH": 1216, "Consumo_LtsH": 120.0, "Costo_USDH": 441.00},
        {"Item": 2, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "PA623", "Modelo": "CAT 6020B", "Horómetro Entrada": 11800.5, "Operador": "Sin Asignar", "Rend_TonH": 1216, "Consumo_LtsH": 115.0, "Costo_USDH": 420.00},
        {"Item": 3, "Agendar": False, "Estado": DISPONIBLE, "ID": "PA624", "Modelo": "Komatsu PC2000", "Horómetro Entrada": 9500.0, "Operador": "Hernán Gómez", "Rend_TonH": 1216, "Consumo_LtsH": 118.0, "Costo_USDH": 430.00},
        {"Item": 4, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "PA625", "Modelo": "Hitachi EX2600", "Horómetro Entrada": 16120.0, "Operador": "Sin Asignar", "Rend_TonH": 1300, "Consumo_LtsH": 128.0, "Costo_USDH": 460.00},
    ])
if "cf_df" not in st.session_state:
    st.session_state.cf_df = pd.DataFrame([
        {"Item": 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": "CF437", "Modelo": "Komatsu WA900", "Horómetro Entrada": 8400.0, "Operador": "Sin Asignar", "Rend_TonH": 685, "Consumo_LtsH": 75.0, "Costo_USDH": 342.50},
        {"Item": 2, "Agendar": False, "Estado": DISPONIBLE, "ID": "CF438", "Modelo": "CAT 993K", "Horómetro Entrada": 10250.0, "Operador": "Manuel Torres", "Rend_TonH": 720, "Consumo_LtsH": 82.0, "Costo_USDH": 360.00},
        {"Item": 3, "Agendar": False, "Estado": DISPONIBLE, "ID": "CF439", "Modelo": "LeTourneau L-1850", "Horómetro Entrada": 13100.0, "Operador": "Roberto Marín", "Rend_TonH": 900, "Consumo_LtsH": 90.0, "Costo_USDH": 395.00},
        {"Item": 4, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "CF440", "Modelo": "CAT 992K", "Horómetro Entrada": 7900.0, "Operador": "Sin Asignar", "Rend_TonH": 650, "Consumo_LtsH": 70.0, "Costo_USDH": 325.00},
    ])
if "caex_df" not in st.session_state:
    _c = lambda i, ag, est, id_, mod, cap, hor, op: {
        "Item": i, "Agendar": ag, "Estado": est, "ID": id_, "Modelo": mod, "Cap_Ton": cap, "Horómetro Entrada": hor, "Operador": op,
        "Rend_TonH": {60.0: 143, 90.0: 210, 140.0: 320}[cap], "Consumo_LtsH": {60.0: 35.0, 90.0: 45.0, 140.0: 65.0}[cap],
        "Costo_USDH": {60.0: 250.0, 90.0: 290.0, 140.0: 380.0}[cap]}
    st.session_state.caex_df = pd.DataFrame([
        _c(1, True, DISPONIBLE, "CA319", "Komatsu HD465-7", 60.0, 12450.0, "Pedro Morales"),
        _c(2, True, DISPONIBLE, "CA320", "Komatsu HD465-7", 60.0, 11200.5, "Luis Tapia"),
        _c(3, True, DISPONIBLE, "CA321", "Komatsu HD785-7", 90.0, 12241.5, "Andrés Castro"),
        _c(4, True, DISPONIBLE, "CA322", "Komatsu HD785-7", 90.0, 15300.2, "Diego Rojas"),
        _c(5, True, DISPONIBLE, "CA323", "CAT 777F", 90.0, 8400.0, "Gonzalo Vera"),
        _c(6, True, DISPONIBLE, "CA324", "CAT 777F", 90.0, 10120.0, "Felipe Salinas"),
        _c(7, True, DISPONIBLE, "CA325", "CAT 785D", 140.0, 13400.0, "Jaime Silva"),
        _c(8, True, DISPONIBLE, "CA326", "CAT 785D", 140.0, 9150.0, "Marcelo Soto"),
        _c(9, False, "🟡 Mantenimiento / Resguardo", "CA327", "Komatsu HD465-7", 60.0, 11800.0, "Javier Fuentes"),
        _c(10, False, DISPONIBLE, "CA328", "Komatsu HD465-7", 60.0, 7600.0, "Cristian Muñoz"),
        _c(11, False, "🔴 Falla Mecánica", "CA329", "Komatsu HD785-7", 90.0, 14500.0, "Sin Asignar"),
        _c(12, False, "🔴 Falla Mecánica", "CA330", "CAT 785D", 140.0, 16200.0, "Sin Asignar"),
    ])
if "Cap_Ton" not in st.session_state.caex_df.columns:
    st.session_state.caex_df["Cap_Ton"] = 90.0
 
# ==============================================================================
# 9. TABLAS DE AGENDAMIENTO DE FLOTA
# ==============================================================================
b64_logo = obtener_base64_img(LOGO_PATH)
img_tag_logo = f'<img src="{b64_logo}" style="height: 32px; width: auto; vertical-align: middle; margin-right: 8px;">' if b64_logo else ''
st.markdown(f"""
    <div style="text-align: center; width: 100%; margin-bottom: 10px;">
        <div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
            {img_tag_logo}<h2 style="margin: 0; color: #0F172A; font-size: 20px; font-weight: 800;">Estado y agendamiento de flota operativa</h2>
        </div>
        <p style="color: #64748B; font-weight: 600; margin: 2px 0 0 0; font-size: 12px;">
            Disponibilidad mecánica, horómetros, capacidad de tolva y asignación de equipos al turno</p>
    </div>""", unsafe_allow_html=True)
 
 
def reindexar_flota(df):
    df = df.reset_index(drop=True)
    if not df.empty:
        df["Item"] = df.index + 1
    return df
 
 
def bloque_flota(columna, titulo, imagenes, clave, nuevo_equipo, config_extra=None):
    with columna:
        c_img, c_txt = st.columns([1, 2])
        with c_img:
            for img in imagenes:
                if os.path.exists(img):
                    st.image(img, width=70)
                    break
        with c_txt:
            st.markdown(f"### {titulo}")
        b1, b2 = st.columns(2)
        df_key = f"{clave}_df"
        with b1:
            if st.button("➖ Quitar último", key=f"del_{clave}", use_container_width=True) and len(st.session_state[df_key]) > 0:
                st.session_state[df_key] = reindexar_flota(st.session_state[df_key].iloc[:-1])
                st.rerun()
        with b2:
            if st.button("➕ Agregar", key=f"add_{clave}", use_container_width=True):
                n = len(st.session_state[df_key])
                st.session_state[df_key] = pd.concat([st.session_state[df_key], pd.DataFrame([nuevo_equipo(n)])],
                                                     ignore_index=True)
                st.rerun()
        config = {
            "Item": st.column_config.NumberColumn("N° Item", disabled=True),
            "Estado": st.column_config.SelectboxColumn("Estado mecánico", options=OPCIONES_ESTADO),
            "Horómetro Entrada": st.column_config.NumberColumn("Horómetro entrada", min_value=0.0, format="%.1f"),
            "Costo_USDH": st.column_config.NumberColumn("Costo (USD/h)", min_value=0.0, format="%.2f"),
            "Consumo_LtsH": st.column_config.NumberColumn("Consumo (L/h)", min_value=0.0, format="%.1f"),
        }
        config.update(config_extra or {})
        editado = st.data_editor(st.session_state[df_key], column_config=config, hide_index=True,
                                 key=f"editor_{clave}", num_rows="fixed")
        st.session_state[df_key] = reindexar_flota(editado)
        return editado
 
 
col_t1, col_t2, col_t3 = st.columns(3)
ed_palas = bloque_flota(col_t1, "Pala de carguío", ["Gif Pala.jpg"], "palas", lambda n: {
    "Item": n + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": f"PA{621 + n}", "Modelo": "Liebherr R9200",
    "Horómetro Entrada": 10000.0, "Operador": "Sin Asignar", "Rend_TonH": 1216, "Consumo_LtsH": 120.0, "Costo_USDH": 441.00})
ed_cf = bloque_flota(col_t2, "Cargador frontal", ["Gif Cargador Frontal.jpg"], "cf", lambda n: {
    "Item": n + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": f"CF{436 + n}", "Modelo": "CAT 993K",
    "Horómetro Entrada": 8000.0, "Operador": "Sin Asignar", "Rend_TonH": 700, "Consumo_LtsH": 80.0, "Costo_USDH": 350.00})
ed_caex = bloque_flota(col_t3, "Camión CAEX", ["Camión CAEX Vacío.png", "Gif Camión Minero.jpg"], "caex", lambda n: {
    "Item": n + 1, "Agendar": False, "Estado": "🟡 Mantenimiento / Resguardo", "ID": f"CA{319 + n}", "Modelo": "Komatsu HD785-7",
    "Cap_Ton": 90.0, "Horómetro Entrada": 10000.0, "Operador": "Sin Asignar", "Rend_TonH": 210, "Consumo_LtsH": 45.0, "Costo_USDH": 290.00},
    {"Cap_Ton": st.column_config.NumberColumn("Capacidad (t)", min_value=10.0, max_value=400.0, format="%.0f t")})
 
total_caex = len(ed_caex)
caex_disponibles = int((ed_caex["Estado"] == DISPONIBLE).sum())
disponibilidad_fisica_val = (caex_disponibles / total_caex * 100.0) if total_caex > 0 else 0.0
 
# ==============================================================================
# 10. TABLA DE CONTROL DE ESTADOS DE EQUIPOS (taller / mantención, ETR)
# ==============================================================================
st.markdown("---")
st.markdown("<h3 style='text-align: center;'>Tabla de control de estados de equipos mina</h3>", unsafe_allow_html=True)
 
INTERVALO_HORA_MIN = 30
OPCIONES_HORA = [f"{m // 60:02d}:{m % 60:02d}" for m in range(0, 24 * 60, INTERVALO_HORA_MIN)]
hoy_fecha = now_dt.date()
COL_F_INI, COL_H_INI = "Fecha Inicio Detención", "Hora Inicio Detención"
COL_F_ETR, COL_H_ETR = "Fecha Estimada Salida (ETR)", "Hora Estimada Salida (ETR)"
LOGISTICA_OPC = ["Mecánica / Turno A", "Contratista / Turno B", "Logística / Turno A", "Logística / Turno B"]
AUTORIZA_OPC = ["Gerente Mina", "Jefe Oper. Mina", "Jefe Turno Mina (A)", "Jefe Turno Mina (B)", "Jefe de Taller", "AdC Minera"]
COLUMNAS_CONTROL = ["ID- Equipo", "Tipo / Flota", "Ubicación Actual", "Estado de Mantención", "Tipo de Falla / Trabajo",
                    COL_F_INI, COL_H_INI, COL_F_ETR, COL_H_ETR, "Logística / Turno", "Plazo Extra Días", "Quien Autoriza"]
 
 
def _a_fecha(valor):
    if valor is None or valor is pd.NaT or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return pd.to_datetime(str(valor), dayfirst=True).date()
    except (ValueError, TypeError):
        return None
 
 
def _fila_control(eq_id, tipo, ubicacion, mant, trabajo_pm, trabajo_falla, h_ini, h_etr, logistica, plazo, autoriza):
    return {"ID- Equipo": eq_id, "Tipo / Flota": tipo, "Ubicación Actual": ubicacion,
            "Estado de Mantención": "Programada (PM 500 hrs)" if mant else "Correctivo (Emergencia)",
            "Tipo de Falla / Trabajo": trabajo_pm if mant else trabajo_falla,
            COL_F_INI: hoy_fecha, COL_H_INI: h_ini, COL_F_ETR: hoy_fecha, COL_H_ETR: h_etr,
            "Logística / Turno": logistica, "Plazo Extra Días": plazo, "Quien Autoriza": autoriza}
 
 
equipos_no_disponibles = []
for _, r in ed_palas[ed_palas["Estado"] != DISPONIBLE].iterrows():
    equipos_no_disponibles.append(_fila_control(r["ID"], "Pala", "Taller Central - Bahía 1", "Mantenimiento" in r["Estado"],
        "Inspección y mantenimiento preventivo", "Falla mecánica reportada en terreno", "08:00", "20:00",
        "Mecánica / Turno A", 2, "Jefe Turno Mina (A)"))
for _, r in ed_cf[ed_cf["Estado"] != DISPONIBLE].iterrows():
    equipos_no_disponibles.append(_fila_control(r["ID"], "Cargador Frontal", "Taller de Neumáticos", "Mantenimiento" in r["Estado"],
        "Cambio de neumáticos y fluidos", "Reparación de transmisión", "10:30", "22:00",
        "Contratista / Turno B", 1, "Jefe de Taller"))
for _, r in ed_caex[ed_caex["Estado"] != DISPONIBLE].iterrows():
    equipos_no_disponibles.append(_fila_control(r["ID"], "Camión CAEX", "Taller Central - Bahía 3", "Mantenimiento" in r["Estado"],
        "Mantención preventiva 500 hrs", "Falla en sistema de frenos / motor", "07:00", "18:00",
        "Mecánica / Turno A", 2, "Jefe Oper. Mina"))
 
# Conserva lo ingresado por el Jefe de Turno y actualiza solo el estado de mantención
previo = st.session_state.get("control_estados_mina_df", pd.DataFrame(columns=COLUMNAS_CONTROL))
filas = []
for eq in equipos_no_disponibles:
    match_prev = previo[previo["ID- Equipo"] == eq["ID- Equipo"]] if "ID- Equipo" in previo.columns else pd.DataFrame()
    if not match_prev.empty:
        fila = {**eq, **{k: v for k, v in match_prev.iloc[0].to_dict().items() if k in COLUMNAS_CONTROL}}
        fila["Estado de Mantención"] = eq["Estado de Mantención"]
        filas.append(fila)
    else:
        filas.append(eq)
df_ctrl = pd.DataFrame(filas, columns=COLUMNAS_CONTROL)
for col_f in (COL_F_INI, COL_F_ETR):
    df_ctrl[col_f] = df_ctrl[col_f].map(_a_fecha)
 
contenedor_alertas = st.container()
contenedor_tabla = st.container()
 
if not df_ctrl.empty:
    with st.expander("✏️ Ingreso de fecha y hora — Jefe de Turno Mina", expanded=True):
        st.caption("Elija la fecha en el calendario y la hora en la lista (00:00 a 23:30). La tabla y las alertas se actualizan al instante.")
        ed_control = st.data_editor(
            df_ctrl,
            column_config={
                "ID- Equipo": st.column_config.TextColumn("ID- Equipo", disabled=True),
                "Tipo / Flota": st.column_config.TextColumn("Tipo / Flota", disabled=True),
                COL_F_INI: st.column_config.DateColumn("📅 Fecha inicio detención", format="DD-MM-YYYY", required=True),
                COL_H_INI: st.column_config.SelectboxColumn("🕒 Hora inicio detención", options=OPCIONES_HORA, required=True),
                COL_F_ETR: st.column_config.DateColumn("📅 Fecha estimada salida (ETR)", format="DD-MM-YYYY", required=True),
                COL_H_ETR: st.column_config.SelectboxColumn("🕒 Hora estimada salida (ETR)", options=OPCIONES_HORA, required=True),
                "Logística / Turno": st.column_config.SelectboxColumn("Logística / Turno", options=LOGISTICA_OPC),
                "Plazo Extra Días": st.column_config.SelectboxColumn("Plazo extra (días)", options=list(range(31))),
                "Quien Autoriza": st.column_config.SelectboxColumn("Quién autoriza", options=AUTORIZA_OPC),
            },
            column_order=COLUMNAS_CONTROL, hide_index=True, key="editor_control_estados_mina_fh", use_container_width=True,
        )
    st.session_state.control_estados_mina_df = ed_control
 
    def _fecha_hora(fecha_val, hora_val):
        f = _a_fecha(fecha_val)
        try:
            h = datetime.strptime(str(hora_val)[:5], "%H:%M").time()
        except (ValueError, TypeError):
            return None
        return datetime.combine(f, h) if f else None
 
    def _horas_txt(horas):
        return f"{fmt_num(horas / 24, 1)} días" if horas >= 48 else f"{fmt_num(horas, 1)} h"
 
    filas_vista, vencidos, hoy_vence, inconsistentes = [], [], [], []
    FONDO_ETR = {"venc": "#FECACA", "hoy": "#FDE68A", "ok": "#BBF7D0", "inc": "#DDD6FE", "sin": "#E2E8F0"}
    for _, row in ed_control.iterrows():
        ini = _fecha_hora(row[COL_F_INI], row[COL_H_INI])
        etr = _fecha_hora(row[COL_F_ETR], row[COL_H_ETR])
        detenido = (now_dt - ini).total_seconds() / 3600 if ini and ini <= now_dt else 0.0
        if etr is None:
            txt, clase = "⚪ Sin fecha/hora de ETR", "sin"
        elif ini and etr < ini:
            txt, clase = "⚠️ ETR anterior al inicio", "inc"
            inconsistentes.append(row["ID- Equipo"])
        elif etr < now_dt:
            txt, clase = f"🔴 Vencido hace {_horas_txt((now_dt - etr).total_seconds() / 3600)}", "venc"
            vencidos.append(f"{row['ID- Equipo']} (ETR {etr:%d-%m-%Y %H:%M})")
        elif etr.date() == now_dt.date():
            txt, clase = f"🟡 Vence hoy en {_horas_txt((etr - now_dt).total_seconds() / 3600)}", "hoy"
            hoy_vence.append(f"{row['ID- Equipo']} ({etr:%H:%M} h)")
        else:
            txt, clase = f"🟢 En plazo ({_horas_txt((etr - now_dt).total_seconds() / 3600)})", "ok"
        fila = row.to_dict()
        fila[COL_F_INI] = ini.strftime("%d-%m-%Y") if ini else "—"
        fila[COL_F_ETR] = etr.strftime("%d-%m-%Y") if etr else "—"
        fila["Horas Detenido"] = _horas_txt(detenido)
        fila["Estado ETR"] = txt
        fila["_clase"] = clase
        filas_vista.append(fila)
 
    with contenedor_alertas:
        if vencidos:
            st.error(f"🔴 ETR vencido: {', '.join(vencidos)}. Revise el taller con urgencia.")
        if hoy_vence:
            st.warning(f"🟡 Vence hoy ({fecha_str}): {', '.join(hoy_vence)}. Planifique el relevo con el Jefe de Turno.")
        if inconsistentes:
            st.info(f"⚠️ Revisar registro: en {', '.join(inconsistentes)} la salida (ETR) es anterior al inicio de la detención.")
 
    df_vista = pd.DataFrame(filas_vista)
    clases = dict(zip(df_vista["Estado ETR"], df_vista["_clase"]))
    df_vista = df_vista[COLUMNAS_CONTROL[:9] + ["Horas Detenido", "Estado ETR"] + COLUMNAS_CONTROL[9:]]
    with contenedor_tabla:
        st.markdown(tabla_html(df_vista, {"Estado ETR": lambda v: FONDO_ETR.get(clases.get(v, "sin"), "")}),
                    unsafe_allow_html=True)
else:
    st.session_state.control_estados_mina_df = df_ctrl
    st.info("🟢 Todos los equipos están disponibles. No hay equipos en mantención o taller.")
 
# ==============================================================================
# 11. CÁLCULO DEL AGENDAMIENTO (motor.py)
# ==============================================================================
def activos(df):
    return df[(df["Agendar"] == True) & (df["Estado"] == DISPONIBLE)]  # noqa: E712
 
 
palas_activas, cf_activos, caex_activos = activos(ed_palas), activos(ed_cf), activos(ed_caex)
n_carguio_real = len(palas_activas) + len(cf_activos)
n_puestos_carguio = max(1, n_carguio_real)
n_caex_activos = len(caex_activos)
costo_carguio_h = float(pd.concat([palas_activas["Costo_USDH"], cf_activos["Costo_USDH"]]).astype(float).sum())
 
parametros_motor = dict(
    n_palas=n_puestos_carguio, duracion_horas=horas_turno, costo_carguio_total_h=costo_carguio_h,
    fl_factor=fl_valor, merma_base_pct=merma_base_valor, perfil_rampa_key=perfil_rampa_sel,
    distancia_km=distancia_acarreo_km, vel_cargado_base=vel_cargado_kmh, vel_vacio_base=vel_vacio_kmh,
    precio_diesel=precio_diesel,
)
res = ejecutar_simulacion_analitica(caex_activos, **parametros_motor)
 
match_factor = res["MF"]
semaforo_actual = res["semaforo"]
tonelaje_efectivo = res["ton_totales"]
costo_diesel_turno = res["costo_diesel"]
ingreso_bruto_usd = tonelaje_efectivo * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - res["costo_total"]
co2_por_ton = res["co2_kg"] / tonelaje_efectivo if tonelaje_efectivo > 0 else 0.0
vueltas_totales_meta = int(res["vueltas_por_camion"] * n_caex_activos)
 
# --- Barrido de flota: mismo motor para N = 1 ... N máximo (Algoritmo 1) ---------
if n_caex_activos > 0:
    camion_tipo = {c: float(caex_activos[c].astype(float).mean()) for c in ("Cap_Ton", "Costo_USDH", "Consumo_LtsH")}
else:
    camion_tipo = {"Cap_Ton": 90.0, "Costo_USDH": 290.0, "Consumo_LtsH": 45.0}
n_max_barrido = max(caex_disponibles, n_caex_activos, 1) + 3
resultados_barrido = barrido_flota(camion_tipo, 1, n_max_barrido, **parametros_motor)
recomendado = prescribir_n(resultados_barrido)
n_prescrito = recomendado["N"] if recomendado else None
 
# ==============================================================================
# 12. RESUMEN DEL AGENDAMIENTO Y SEMÁFORO
# ==============================================================================
st.markdown("---")
st.markdown(f"<h2 style='text-align: center;'>Resumen de agendamiento pre-turno: {html.escape(num_agendamiento)}</h2>",
            unsafe_allow_html=True)
b64_logo_atacama = obtener_base64_img(LOGO_ATACAMA_PATH)
logo_faena = (f'<img src="{b64_logo_atacama}" style="height: 38px; width: auto; vertical-align: middle;">'
              if b64_logo_atacama else "")
st.markdown(f"""
    <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 10px;">
        {logo_faena}<h3 style="margin: 0; color: #0F172A; font-size: 16px; font-weight: 800;">
        Faena: {html.escape(nombre_mina)} | {nombre_dia_actual}, {fecha_str} {hora_str} h — {turno_seleccionado} ({regimen_guardia})</h3>
    </div>""", unsafe_allow_html=True)
 
if n_carguio_real == 0:
    st.error("No hay palas ni cargadores agendados y disponibles. Agende al menos una unidad de carguío.")
if n_caex_activos == 0:
    st.warning("No hay camiones agendados y disponibles: el agendamiento no mueve material.")
 
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Disp. física CAEX", f"{fmt_num(disponibilidad_fisica_val, 1)}%", delta=f"{caex_disponibles}/{total_caex} disponibles")
k2.metric("Match Factor (MF)", fmt_num(match_factor, 2), delta=f"Semáforo {semaforo_actual}",
          delta_color="normal" if semaforo_actual == "Verde" else "inverse")
k3.metric("Toneladas entregadas", f"{fmt_num(tonelaje_efectivo)} t", delta=f"Merma {fmt_num(res['merma_pct'], 2)}%",
          delta_color="off")
k4.metric("Diésel CAEX", f"{fmt_num(res['litros_totales'])} L")
k5.metric("Costo total / t", f"${fmt_num(res['costo_total_usd_ton'], 2)}",
          delta=f"Equipos ${fmt_num(res['costo_unitario_usd_ton'], 2)} + diésel", delta_color="off")
k6.metric("Beneficio neto", f"${fmt_num(beneficio_neto_usd, 0)} USD")
 
st.markdown("---")
col_eval1, col_eval2 = st.columns(2)
with col_eval1:
    st.markdown("### ⛽ Evaluación económica, merma y ruta")
    total_objetivo = target_mineral_num + target_esteril_num
    cumplimiento = (tonelaje_efectivo / total_objetivo * 100) if total_objetivo > 0 else 0.0
    for linea in (
        f"Ciclo base {fmt_num(res['t_ciclo_base'], 2)} min + espera {fmt_num(res['cola_min'], 2)} min = "
        f"{fmt_num(res['t_ciclo_min'], 2)} min por vuelta",
        f"Velocidad efectiva cargado: {fmt_num(res['vel_cargado_efectiva'], 1)} km/h ({perfil_rampa_sel})",
        f"Pérdida en ruta (merma): {fmt_num(res['ton_merma'])} t ({fmt_num(res['merma_pct'], 2)}% de lo cargado)",
        f"Costo de equipos: ${fmt_num(res['costo_opex'], 0)} USD | diésel: ${fmt_num(costo_diesel_turno, 0)} USD",
        f"Diésel específico: {fmt_num(res['consumo_especifico_l_ton'], 3)} L/t entregada",
        f"Huella CO₂: {fmt_num(co2_por_ton, 2)} kg CO₂/t ({fmt_num(res['co2_kg'] / 1000, 1)} t CO₂ en el turno)",
        f"Capacidad de servicio del carguío: {fmt_num(res['capacidad_carguio_th'])} t/h",
        f"Cumplimiento del plan: {fmt_num(cumplimiento, 1)}% de {fmt_num(total_objetivo)} t",
    ):
        st.markdown(f'<p class="highlight-red-large">• {linea}</p>', unsafe_allow_html=True)
    st.progress(min(cumplimiento / 100.0, 1.0))
    st.caption("El diésel de palas y cargadores no está incluido en el modelo.")
 
with col_eval2:
    st.markdown("### 🚦 Semáforo prescriptivo de balance de flota")
    st.markdown('<p class="mf-label">Match Factor calculado:</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="mf-value">{fmt_num(match_factor, 2)}</p>', unsafe_allow_html=True)
    mf_txt = fmt_num(match_factor, 2)
    if semaforo_actual == "Verde":
        st.success(f"🟢 Banda verde (MF {mf_txt}): flota acoplada, espera acotada y costo unitario cercano al mínimo.")
    elif semaforo_actual == "Amarillo":
        st.warning(f"🟡 Descalce leve (MF {mf_txt}): evalúe ajustar un camión según la prioridad de tonelaje frente a costo.")
    elif match_factor < 1:
        st.error(f"🔴 Falta transporte (MF {mf_txt}): las unidades de carguío quedan subutilizadas.")
    else:
        st.error(f"🔴 Exceso de camiones (MF {mf_txt}): se forman colas en el carguío.")
 
    if n_prescrito is not None:
        texto = f"**Prescripción:** {n_prescrito} camiones (MF {fmt_num(recomendado['MF'], 2)}), el menor tamaño en banda verde."
        if n_prescrito > caex_disponibles:
            texto += f" Hay solo {caex_disponibles} CAEX disponibles."
        st.info(texto)
    else:
        st.info("Ningún tamaño de flota evaluado queda en banda verde con estos parámetros.")
    st.caption("El semáforo clasifica el MF redondeado a dos decimales (banda verde 0,92–1,08).")
 
with st.expander("📊 Barrido de tamaño de flota y costo marginal", expanded=False):
    st.caption(f"Camión tipo: promedio de los CAEX agendados ({fmt_num(camion_tipo['Cap_Ton'])} t, "
               f"{fmt_num(camion_tipo['Costo_USDH'], 2)} USD/h). Costo marginal = costo adicional por tonelada adicional.")
    df_barrido = pd.DataFrame([{
        "N": r["N"], "MF": fmt_num(r["MF"], 2), "Semáforo": r["semaforo"], "Espera (min)": fmt_num(r["cola_min"], 2),
        "t/turno": fmt_num(r["ton_totales"]), "L/t": fmt_num(r["consumo_especifico_l_ton"], 3),
        "USD/t equipos": fmt_num(r["costo_unitario_usd_ton"], 2), "USD/t total": fmt_num(r["costo_total_usd_ton"], 2),
        "Costo marginal (USD/t)": fmt_num(r["costo_marginal"], 2) if r["costo_marginal"] is not None else "–",
    } for r in resultados_barrido])
    st.markdown(tabla_html(df_barrido, {"Semáforo": lambda v: COLOR_SEMAFORO.get(v, "")}, max_height=420),
                unsafe_allow_html=True)
    st.caption(f"Si el costo marginal es menor que el valor del material ({fmt_num(valor_ton_usd, 2)} USD/t) y el plan "
               "y el chancador admiten más tonelaje, agregar camiones aumenta el margen aunque salga de la banda verde.")
 
# ==============================================================================
# 13. CIERRE Y GUARDADO DEL AGENDAMIENTO
# ==============================================================================
st.sidebar.markdown("**Cierre del agendamiento**")
adopta = st.sidebar.checkbox("Despacho según la prescripción del sistema",
                             value=(n_prescrito is not None and n_caex_activos == n_prescrito),
                             help="Marque si el Jefe de Turno despacha la flota prescrita. Se registra como adopción.")
if st.sidebar.button("🔒 Guardar agendamiento en BD", use_container_width=True):
    guardar_agendamiento_db({
        "num_agendamiento": num_agendamiento, "fecha_registro": fecha_str, "hora_registro": hora_str,
        "faena": nombre_mina, "turno": turno_seleccionado, "regimen_guardia": regimen_guardia, "jefe_turno": USUARIO_ACTIVO,
        "perfil_rampa": perfil_rampa_sel, "n_caex": n_caex_activos, "n_carguio": n_carguio_real,
        "ton_movidas": res["ton_cargadas"], "ton_efectivas": tonelaje_efectivo, "merma_ton": res["ton_merma"],
        "consumo_diesel_lts": res["litros_totales"], "costo_diesel_usd": costo_diesel_turno,
        "opex_total_usd": res["costo_opex"], "costo_ton_usd": res["costo_unitario_usd_ton"],
        "costo_total_usd": res["costo_total"], "costo_total_ton_usd": res["costo_total_usd_ton"],
        "beneficio_neto_usd": beneficio_neto_usd, "match_factor": match_factor, "espera_min": res["cola_min"],
        "disponibilidad_fisica": disponibilidad_fisica_val, "factor_llenado": fl_valor, "n_prescrito": n_prescrito,
        "en_banda_verde": int(semaforo_actual == "Verde"), "prescripcion_aceptada": int(adopta),
    })
    st.sidebar.success(f"✅ Agendamiento {num_agendamiento} guardado.")
    st.rerun()
 
if st.sidebar.button("🚪 Cerrar sesión", use_container_width=True):
    for k in ("autenticado", "user_id", "usuario_activo", "rol_activo"):
        st.session_state.pop(k, None)
    st.rerun()
 
# ==============================================================================
# 14. MONITOREO ESPACIAL DEL CIRCUITO (Canvas / JavaScript)
#     El MF dinámico usa la misma ecuación (2) con los equipos que no están en falla.
# ==============================================================================
st.markdown("---")
st.markdown("<h3 style='text-align: center;'>Monitoreo espacial del circuito y control de fallas</h3>", unsafe_allow_html=True)
st.markdown(
    f"<div style='text-align: center;'>💡 <b>Ciclo calculado: {fmt_num(res['t_ciclo_min'], 2)} min</b> "
    f"(carga {fmt_num(T_CARGUIO_MIN, 1)} | ida a {fmt_num(res['vel_cargado_efectiva'], 1)} km/h: {fmt_num(res['t_ida_min'], 2)} | "
    f"descarga {fmt_num(T_MANIOBRAS_MIN, 1)} | retorno a {fmt_num(vel_vacio_kmh, 1)} km/h: {fmt_num(res['t_retorno_min'], 2)} | "
    f"espera {fmt_num(res['cola_min'], 2)} min)</div>", unsafe_allow_html=True)
 
if "acarreo_iniciado" not in st.session_state:
    st.session_state.acarreo_iniciado = False
col_trig1, col_trig2, col_trig3 = st.columns([1.8, 3.5, 1.5])
with col_trig1:
    if st.button("🔴 Iniciar acarreo", type="primary"):
        st.session_state.acarreo_iniciado = True
with col_trig2:
    st.markdown("<p style='text-align:center; font-weight:800; font-size:12px; margin-top:8px;'>📻 Animación ilustrativa "
                "basada en el ciclo calculado. Clic sobre un equipo para simular una falla.</p>", unsafe_allow_html=True)
with col_trig3:
    if st.button("🔄 Reiniciar postura", use_container_width=True):
        st.session_state.acarreo_iniciado = False
 
img_caex_cargado_b64 = obtener_base64_img("Camion_CAEX_Cargado.png") or obtener_base64_img("Camión CAEX Cargado.png")
img_caex_vacio_b64 = obtener_base64_img("Camion_CAEX_Vacio.png") or obtener_base64_img("Camión CAEX Vacío.png")
img_pala_b64 = obtener_base64_img("Gif Pala.jpg")
img_cf_b64 = obtener_base64_img("Gif Cargador Frontal.jpg")
 
 
def a_js(df, campos):
    return json.dumps([{k: (float(r[c]) if isinstance(r[c], (int, float, np.number)) else str(r[c])) for k, c in campos.items()}
                       for _, r in df.iterrows()])
 
 
caex_json = a_js(caex_activos, {"id": "ID", "modelo": "Modelo", "capTon": "Cap_Ton", "operador": "Operador", "rend": "Rend_TonH"})
palas_json = a_js(palas_activas, {"id": "ID", "modelo": "Modelo", "operador": "Operador", "rend": "Rend_TonH"})
cf_json = a_js(cf_activos, {"id": "ID", "modelo": "Modelo", "operador": "Operador", "rend": "Rend_TonH"})
 
html_gps_canvas = f"""
<!DOCTYPE html><html><head><style>
  body {{ margin: 0; background-color: #F8FAFC; font-family: Arial, sans-serif; overflow: hidden; }}
  #mapContainer {{ width: 100%; height: 380px; position: relative; background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 12px; }}
  canvas {{ width: 100%; height: 100%; display: block; cursor: pointer; }}
  .kpi-panel {{ position: absolute; top: 12px; right: 15px; background: rgba(15,23,42,0.95); border: 1px solid #F59E0B;
               border-radius: 10px; padding: 8px 14px; color: #FFF; font-size: 11px; font-weight: 800; z-index: 10; }}
  .kpi-title {{ color: #F59E0B; font-size: 10px; text-align: center; margin-bottom: 4px; border-bottom: 1px solid #334155; padding-bottom: 2px; }}
  .kpi-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; text-align: center; }}
  .kpi-val {{ font-size: 15px; color: #38BDF8; font-weight: 900; }}
  .tooltip {{ position: absolute; display: none; background: rgba(15,23,42,0.95); color: #FFF; padding: 8px 12px; border-radius: 8px;
             font-size: 11px; pointer-events: none; border: 1px solid #F59E0B; z-index: 100; line-height: 1.4; }}
</style></head><body>
<div id="mapContainer">
  <div class="kpi-panel"><div class="kpi-title">📊 Vueltas y dinámica del turno</div>
    <div class="kpi-grid">
      <div><span>Meta vts</span><div class="kpi-val">{vueltas_totales_meta}</div></div>
      <div><span>Actual</span><div class="kpi-val" style="color:#10B981;" id="kpiActual">0</div></div>
      <div><span>MF</span><div class="kpi-val" id="kpiMF">{match_factor:.2f}</div></div>
      <div><span>Flota act.</span><div class="kpi-val" style="color:#E2E8F0;" id="kpiFlota">0/0</div></div>
    </div></div>
  <canvas id="gpsCanvas"></canvas><div id="tooltip" class="tooltip"></div>
</div>
<script>
const canvas = document.getElementById('gpsCanvas'); const ctx = canvas.getContext('2d'); const tooltip = document.getElementById('tooltip');
function resizeCanvas() {{ canvas.width = canvas.offsetWidth; canvas.height = canvas.offsetHeight; }}
resizeCanvas(); window.addEventListener('resize', resizeCanvas);
const caexList = {caex_json}; const palasListRaw = {palas_json}; const cfListRaw = {cf_json};
const isTrackingActive = {"true" if st.session_state.acarreo_iniciado else "false"};
const globalFL = {fl_valor}; const distKm = {distancia_acarreo_km};
const speedLoadedKmh = {res['vel_cargado_efectiva']}; const speedEmptyKmh = {vel_vacio_kmh};
const timeLoading = {T_CARGUIO_MIN}; const timeHaul = {res['t_ida_min']}; const timeDumping = {T_MANIOBRAS_MIN};
const timeReturn = {res['t_retorno_min']}; const tCicloBase = {res['t_ciclo_base']}; const totalCycleUnits = {res['t_ciclo_min']};
const imgCaexCargado = new Image(); imgCaexCargado.src = "{img_caex_cargado_b64 or ''}";
const imgCaexVacio = new Image(); imgCaexVacio.src = "{img_caex_vacio_b64 or ''}";
const imgPala = new Image(); imgPala.src = "{img_pala_b64 or ''}";
const imgCF = new Image(); imgCF.src = "{img_cf_b64 or ''}";
const simSpeed = 0.0004; const staggerInterval = totalCycleUnits / Math.max(1, caexList.length);
let totalVueltas = 0;
let palasList = palasListRaw.map(p => ({{ ...p, stoppedByFault: false }}));
let cfList = cfListRaw.map(c => ({{ ...c, stoppedByFault: false }}));
let vehicles = caexList.map((c, idx) => ({{ ...c, cycleTime: idx * staggerInterval, prevCycleTime: idx * staggerInterval, vueltas: 0,
    x: 0, y: 0, isLoaded: false, isReturning: false, statusText: "Postura previa", speedKmh: 0,
    equipmentAssigned: idx % Math.max(1, palasList.length + cfList.length), stoppedByFault: false }}));
let hitboxes = [];
 
function semaforoColor(mf) {{
  const m = Number(mf.toFixed(2));
  if (m >= 0.92 && m <= 1.08) return "#10B981";
  if (m >= 0.85 && m <= 1.15) return "#F59E0B";
  return "#EF4444";
}}
function recalcularMF() {{
  const camiones = vehicles.filter(v => !v.stoppedByFault).length;
  const carguio = palasList.filter(p => !p.stoppedByFault).length + cfList.filter(c => !c.stoppedByFault).length;
  const mf = carguio > 0 ? (camiones * timeLoading) / (carguio * tCicloBase) : 0;   // ecuación (2)
  const el = document.getElementById('kpiMF'); el.innerText = mf.toFixed(2); el.style.color = semaforoColor(mf);
  document.getElementById('kpiFlota').innerText = camiones + "/" + vehicles.length;
}}
function drawTruck(x, y, loaded, stopped) {{
  ctx.save(); ctx.translate(x, y);
  ctx.fillStyle = stopped ? "#EF4444" : (loaded ? "#D97706" : "#CBD5E1"); ctx.strokeStyle = "#0F172A"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.roundRect(-18, -10, 26, 16, 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = stopped ? "#991B1B" : "#F59E0B"; ctx.beginPath(); ctx.roundRect(8, -6, 9, 12, 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#1E293B"; [[-10, 8], [6, 8], [-10, -8], [6, -8]].forEach(w => {{ ctx.beginPath(); ctx.arc(w[0], w[1], 4, 0, 2 * Math.PI); ctx.fill(); }});
  ctx.restore();
}}
function drawLoader(img, item, px, py, size, label) {{
  if (img.complete && img.naturalWidth > 0 && !item.stoppedByFault) ctx.drawImage(img, px, py - size / 2, size, size);
  else {{ ctx.fillStyle = item.stoppedByFault ? "#EF4444" : "#F59E0B"; ctx.fillRect(px, py - size / 2, size * 0.8, size * 0.8); }}
  ctx.fillStyle = item.stoppedByFault ? "#DC2626" : "#0F172A"; ctx.font = "bold 11px Arial"; ctx.textAlign = "right";
  ctx.fillText(label + " " + item.id + (item.stoppedByFault ? " (FALLA)" : ""), px - 8, py + 4);
}}
function animate() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const xInicio = 170, xFin = canvas.width - 170, trackWidth = xFin - xInicio;
  const yIda = canvas.height * 0.35, yRetorno = canvas.height * 0.65, yCentro = (yIda + yRetorno) / 2;
  hitboxes = [];
  ctx.beginPath(); ctx.setLineDash([8, 6]); ctx.strokeStyle = "#10B981"; ctx.lineWidth = 4; ctx.moveTo(xInicio, yIda); ctx.lineTo(xFin, yIda); ctx.stroke();
  ctx.beginPath(); ctx.setLineDash([]); ctx.strokeStyle = "#DC2626"; ctx.moveTo(xInicio, yRetorno); ctx.lineTo(xFin, yRetorno); ctx.stroke();
  ctx.font = "bold 11px Arial"; ctx.textAlign = "left";
  ctx.fillStyle = "#10B981"; ctx.fillText("Ida cargado (" + distKm.toFixed(1) + " km a " + speedLoadedKmh.toFixed(1) + " km/h)", xInicio, yIda - 22);
  ctx.fillStyle = "#DC2626"; ctx.fillText("Retorno vacío (" + distKm.toFixed(1) + " km a " + speedEmptyKmh.toFixed(1) + " km/h)", xInicio, yRetorno - 22);
  ctx.beginPath(); ctx.arc(xFin + 25, yCentro, 12, 0, 2 * Math.PI); ctx.fill();
  ctx.fillText("• Botadero", xFin + 45, yCentro - 14); ctx.fillText("• Chancador", xFin + 45, yCentro + 3); ctx.fillText("• Acopio", xFin + 45, yCentro + 20);
  const px = xInicio - 65;
  palasList.forEach((p, i) => {{ const py = yIda - 20 - i * 46; drawLoader(imgPala, p, px, py, 48, "Pala");
    hitboxes.push({{ x: px + 24, y: py, r: 25, kind: "pala", item: p }}); }});
  cfList.forEach((c, i) => {{ const py = yIda - 20 - (palasList.length + i) * 46; drawLoader(imgCF, c, px, py, 38, "CF");
    hitboxes.push({{ x: px + 19, y: py, r: 22, kind: "cf", item: c }}); }});
  const totalEq = Math.max(1, palasList.length + cfList.length);
  vehicles.forEach(v => {{
    if (isTrackingActive && !v.stoppedByFault) {{
      v.prevCycleTime = v.cycleTime; v.cycleTime = (v.cycleTime + simSpeed) % totalCycleUnits;
      if (v.cycleTime < v.prevCycleTime) {{ v.vueltas++; totalVueltas++; }}
    }}
    const t = v.cycleTime, eqIndex = v.equipmentAssigned % totalEq, targetY = yIda - 20 - eqIndex * 46;
    const eqNombre = eqIndex < palasList.length ? palasList[eqIndex].id : (cfList[eqIndex - palasList.length] || {{ id: "CF" }}).id;
    if (v.stoppedByFault) {{ v.statusText = "🔴 Detenido por falla"; v.speedKmh = 0; }}
    else if (!isTrackingActive || t < timeLoading) {{ v.x = xInicio; v.y = targetY; v.isLoaded = false; v.isReturning = false;
      v.statusText = isTrackingActive ? "En carga (" + eqNombre + ")" : "Postura previa (" + eqNombre + ")"; v.speedKmh = 0; }}
    else if (t < timeLoading + timeHaul) {{ const r = (t - timeLoading) / timeHaul; v.x = xInicio + r * trackWidth; v.y = targetY + r * (yIda - targetY);
      v.isLoaded = true; v.isReturning = false; v.statusText = "Acarreo cargado"; v.speedKmh = speedLoadedKmh; }}
    else if (t < timeLoading + timeHaul + timeDumping) {{ v.x = xFin; v.y = yCentro; v.isLoaded = false; v.isReturning = true;
      v.statusText = "En descarga"; v.speedKmh = 0; }}
    else {{ const r = Math.min(1, (t - (timeLoading + timeHaul + timeDumping)) / timeReturn); v.x = xFin - r * trackWidth; v.y = yRetorno;
      v.isLoaded = false; v.isReturning = true; v.statusText = (t > timeLoading + timeHaul + timeDumping + timeReturn) ? "En cola (" + eqNombre + ")" : "Retorno vacío";
      v.speedKmh = r < 1 ? speedEmptyKmh : 0; }}
    const img = v.isLoaded ? imgCaexCargado : imgCaexVacio;
    ctx.save(); ctx.translate(v.x, v.y); if (v.isReturning) ctx.scale(-1, 1);
    if (!v.stoppedByFault && img.complete && img.naturalWidth > 0) ctx.drawImage(img, -20, -20, 40, 40); else {{ ctx.restore(); drawTruck(v.x, v.y, v.isLoaded, v.stoppedByFault); ctx.save(); }}
    ctx.restore();
    ctx.font = "bold 10px Arial"; ctx.textAlign = "center"; ctx.fillStyle = v.stoppedByFault ? "#DC2626" : "#0F172A";
    ctx.fillText("CAEX " + v.id + " (" + v.capTon + " t) - " + v.vueltas + " vts", v.x, v.y + 26);
    hitboxes.push({{ x: v.x, y: v.y, r: 28, kind: "caex", item: v }});
  }});
  if (isTrackingActive) document.getElementById('kpiActual').innerText = totalVueltas;
  recalcularMF(); requestAnimationFrame(animate);
}}
requestAnimationFrame(animate);
function hit(e) {{ const rc = canvas.getBoundingClientRect(); const x = e.clientX - rc.left, y = e.clientY - rc.top;
  return hitboxes.find(h => Math.hypot(x - h.x, y - h.y) < h.r); }}
canvas.addEventListener('click', e => {{ const h = hit(e); if (h) h.item.stoppedByFault = !h.item.stoppedByFault; }});
canvas.addEventListener('mousemove', e => {{
  const h = hit(e); if (!h) {{ tooltip.style.display = 'none'; return; }}
  const it = h.item, accion = it.stoppedByFault ? "<span style='color:#10B981;'><b>(Clic para reanudar)</b></span>" : "<span style='color:#EF4444;'><b>(Clic para simular falla)</b></span>";
  let cuerpo;
  if (h.kind === "caex") {{ const capEf = it.capTon * globalFL;
    cuerpo = '<b>🚛 CAEX ' + it.id + '</b><br>• Operador: <b>' + it.operador + '</b><br>• Modelo: ' + it.modelo +
      '<br>• Capacidad nominal: ' + it.capTon + ' t<br>• Capacidad efectiva (FL ' + (globalFL * 100).toFixed(0) + '%): ' + capEf.toFixed(1) +
      ' t<br>• Vueltas: ' + it.vueltas + '<br>• Tonelaje aprox.: ' + (it.vueltas * capEf).toFixed(0) + ' t<br>• Estado: ' + it.statusText; }}
  else {{ cuerpo = '<b>' + (h.kind === "pala" ? '🏗️ Pala ' : '🚜 Cargador ') + it.id + '</b><br>• Operador: <b>' + it.operador +
      '</b><br>• Modelo: ' + it.modelo + '<br>• Rendimiento nominal: ' + it.rend + ' t/h<br>• Estado: ' + (it.stoppedByFault ? '🔴 Detenido' : '🟢 Operando'); }}
  tooltip.innerHTML = cuerpo + '<br>' + accion; tooltip.style.display = 'block';
  tooltip.style.left = (h.x + 15) + 'px'; tooltip.style.top = Math.max(0, h.y - 35) + 'px';
}});
</script></body></html>
"""
components.html(html_gps_canvas, height=400)
 
# ==============================================================================
# 15. FICHA PRE-TURNO (CSV)
# ==============================================================================
st.markdown("---")
col_exp1, col_exp2 = st.columns([2, 1])
with col_exp1:
    st.subheader("📄 Ficha prescriptiva pre-turno")
with col_exp2:
    ficha = pd.DataFrame([{
        "N° Agendamiento": num_agendamiento, "Fecha": fecha_str, "Hora": hora_str, "Faena": nombre_mina,
        "Turno": turno_seleccionado, "Régimen": regimen_guardia, "Material": tipo_mineral, "Responsable": USUARIO_ACTIVO,
        "Perfil de rampa": perfil_rampa_sel, "CAEX agendados": n_caex_activos, "Unidades de carguío": n_carguio_real,
        "N prescrito": n_prescrito, "Disponibilidad física (%)": round(disponibilidad_fisica_val, 1),
        "Factor de llenado (%)": round(fl_valor * 100), "Merma (%)": round(res["merma_pct"], 2),
        "Match Factor": round(match_factor, 2), "Semáforo": semaforo_actual, "Espera (min/ciclo)": round(res["cola_min"], 2),
        "Toneladas cargadas": round(res["ton_cargadas"]), "Toneladas entregadas": round(tonelaje_efectivo),
        "Diésel (L)": round(res["litros_totales"]), "Diésel (L/t)": round(res["consumo_especifico_l_ton"], 3),
        "CO2 (kg/t)": round(co2_por_ton, 2), "Costo equipos (USD)": round(res["costo_opex"], 2),
        "Costo diésel (USD)": round(costo_diesel_turno, 2), "Costo total (USD/t)": round(res["costo_total_usd_ton"], 2),
        "Beneficio neto (USD)": round(beneficio_neto_usd, 2), "Despacho según prescripción": int(adopta),
    }])
    st.download_button("📥 Descargar ficha pre-turno (CSV)", data=ficha.to_csv(index=False, sep=";").encode("utf-8-sig"),
                       file_name=f"Ficha_Agendamiento_{num_agendamiento}.csv", mime="text/csv", use_container_width=True)
 
# ==============================================================================
# 16. CONCILIACIÓN Y CIERRE DE TURNO (plan vs. real)
# ==============================================================================
st.markdown("---")
st.subheader("🔄 Conciliación y cierre de turno (plan vs. real)")
with closing(conectar()) as conn:
    df_hist = pd.read_sql_query("SELECT * FROM historico_agendamientos ORDER BY id DESC", conn)
    df_cierres = pd.read_sql_query("SELECT * FROM cierres_turno", conn)
 
if df_hist.empty:
    st.info("Aún no hay agendamientos guardados. Use «Guardar agendamiento en BD» en la barra lateral.")
else:
    opciones = [f"{r.num_agendamiento} | {r.fecha_registro} | {r.turno} | Resp.: {r.jefe_turno}" for r in df_hist.itertuples()]
    ag_sel = st.selectbox("🔍 Agendamiento a cerrar", opciones)
    num_sel = ag_sel.split(" | ")[0]
    plan = df_hist[df_hist["num_agendamiento"] == num_sel].iloc[0]
    ton_plan = float(plan["ton_efectivas"] or plan["ton_movidas"] or 0.0)
    diesel_plan = float(plan["consumo_diesel_lts"] or 0.0)
    cierre_prev = df_cierres[df_cierres["num_agendamiento"] == num_sel]
    st.info(f"📋 Plan {num_sel}: {fmt_num(ton_plan)} t entregadas | {fmt_num(diesel_plan)} L de diésel | "
            f"MF {fmt_num(plan['match_factor'], 2)}" + (" | ✅ ya tiene cierre registrado" if not cierre_prev.empty else ""))
 
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("#### 📥 Datos reales de terreno (post-turno)")
        v_ton = float(cierre_prev["ton_reales"].iloc[0]) if not cierre_prev.empty else ton_plan
        v_die = float(cierre_prev["diesel_real_lts"].iloc[0]) if not cierre_prev.empty else diesel_plan
        ton_reales = st.number_input("Toneladas reales entregadas (t)", value=v_ton, step=500.0, min_value=0.0, key=f"ton_{num_sel}")
        diesel_real = st.number_input("Diésel real (L)", value=v_die, step=200.0, min_value=0.0, key=f"die_{num_sel}")
        CAUSAS = ["Falla mecánica de CAEX", "Falla de pala / cargador", "Inasistencia de operador", "Lluvia / condición climática",
                  "Tronadura atrasada", "Detención en chancado", "Otra"]
        causas = st.multiselect("Causas de desviación", CAUSAS, placeholder="Elija opciones", key=f"cau_{num_sel}")
        observaciones = st.text_input("Observaciones / bitácora", placeholder="Ej.: CA321 fuera a las 11:00; PA622 detenida 45 min",
                                      key=f"obs_{num_sel}")
    with col_c2:
        st.markdown("#### 📊 Indicadores de efectividad")
        adherencia = (ton_reales / ton_plan * 100) if ton_plan > 0 else 0.0
        # Costo real = costo de equipos del agendamiento guardado + diésel real (no usa la flota de la pantalla actual)
        costo_real = float(plan["opex_total_usd"] or 0.0) + diesel_real * precio_diesel
        costo_real_ton = costo_real / ton_reales if ton_reales > 0 else 0.0
        clase = "adh-green-large" if adherencia >= 95 else "adh-red-large"
        st.markdown(f'<p class="{clase}">Adherencia al plan: {fmt_num(adherencia, 1)}%</p>', unsafe_allow_html=True)
        st.progress(min(adherencia / 100.0, 1.0))
        st.metric("Costo real", f"${fmt_num(costo_real_ton, 2)} USD/t",
                  delta=f"Plan ${fmt_num(plan['costo_total_ton_usd'], 2)}", delta_color="off")
        texto_causas = ", ".join(causas) if causas else "sin causas registradas"
        if adherencia >= 98:
            st.success(f"🎯 Cumplimiento de {fmt_num(adherencia, 1)}% del plan.")
        elif adherencia >= 85:
            st.warning(f"⚠️ Cumplimiento parcial ({fmt_num(adherencia, 1)}%): {texto_causas}.")
        else:
            st.error(f"🚨 Desviación crítica ({fmt_num(adherencia, 1)}%): {texto_causas}.")
        if st.button("💾 Guardar cierre de turno", use_container_width=True):
            guardar_cierre_db({
                "num_agendamiento": num_sel, "fecha_cierre": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "responsable": USUARIO_ACTIVO, "ton_reales": ton_reales, "diesel_real_lts": diesel_real,
                "costo_real_usd": costo_real, "costo_real_ton_usd": costo_real_ton, "adherencia_pct": adherencia,
                "causas": "; ".join(causas), "observaciones": observaciones,
            })
            st.success(f"Cierre de {num_sel} guardado.")
            st.rerun()
 
# ==============================================================================
# 17. HISTÓRICO Y AUDITORÍA GERENCIAL (solo datos registrados; sin valores simulados)
# ==============================================================================
st.markdown("---")
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.subheader("Histórico de agendamientos y auditoría gerencial")
with col_h2:
    if ROL_ACTIVO == "Administrador":
        confirmar = st.checkbox("Confirmo borrar todo el histórico")
        if st.button("🗑 Borrar histórico", type="primary", use_container_width=True, disabled=not confirmar):
            borrar_historico_db()
            st.success("Histórico eliminado.")
            st.rerun()
 
if not df_hist.empty:
    df_full = df_hist.merge(df_cierres, on="num_agendamiento", how="left")
 
    if ROL_ACTIVO in ("Administrador", "Gerente Operaciones / Evaluador"):
        st.markdown("<h3 style='text-align: center;'>Panel gerencial de control y auditoría</h3>", unsafe_allow_html=True)
        c_f1, c_f2 = st.columns(2)
        with c_f1:
            sup_filtro = st.selectbox("👤 Jefe de turno", ["Todos"] + sorted(df_full["jefe_turno"].dropna().unique().tolist()))
        with c_f2:
            periodo_filtro = st.selectbox("📅 Período", ["Últimos 7 agendamientos (ciclo 7x7)", "Últimos 30 días",
                                                        "Últimos 365 días", "Histórico completo"])
        df_g = df_full.copy()
        if sup_filtro != "Todos":
            df_g = df_g[df_g["jefe_turno"] == sup_filtro]
        df_g["fecha_dt"] = pd.to_datetime(df_g["fecha_registro"], format="%d/%m/%Y", errors="coerce")
        if periodo_filtro.startswith("Últimos 7"):
            df_g = df_g.head(7)
        elif periodo_filtro != "Histórico completo":
            dias = 30 if "30" in periodo_filtro else 365
            df_g = df_g[df_g["fecha_dt"] >= pd.Timestamp(now_dt.date()) - pd.Timedelta(days=dias)]
 
        vista = pd.DataFrame({
            "N° Agendamiento": df_g["num_agendamiento"], "Fecha": df_g["fecha_registro"], "Turno": df_g["turno"],
            "Jefe de turno": df_g["jefe_turno"], "Perfil rampa": df_g["perfil_rampa"].fillna("—"),
            "CAEX": df_g["n_caex"].fillna(0).astype(int), "N prescrito": df_g["n_prescrito"].map(lambda v: "—" if pd.isna(v) else int(v)),
            "MF": df_g["match_factor"].map(lambda v: fmt_num(v, 2)),
            "t plan": df_g["ton_efectivas"].map(fmt_num),
            "t real": df_g["ton_reales"].map(lambda v: "sin cierre" if pd.isna(v) else fmt_num(v)),
            "Adherencia (%)": df_g["adherencia_pct"].map(lambda v: "—" if pd.isna(v) else fmt_num(v, 1)),
            "USD/t plan": df_g["costo_total_ton_usd"].map(lambda v: fmt_num(v, 2)),
            "USD/t real": df_g["costo_real_ton_usd"].map(lambda v: "—" if pd.isna(v) else fmt_num(v, 2)),
            "Diésel plan (L)": df_g["consumo_diesel_lts"].map(fmt_num),
            "Banda verde": df_g["en_banda_verde"].map(lambda v: "🟢 Sí" if v == 1 else "🔴 No"),
            "Despacho según prescripción": df_g["prescripcion_aceptada"].map(lambda v: "Sí" if v == 1 else "No"),
        })
        st.markdown(tabla_html(vista, {"MF": fondo_mf}, max_height=460), unsafe_allow_html=True)
 
        with st.expander(f"📈 Evaluación de rendimiento ({periodo_filtro}) — {sup_filtro}", expanded=True):
            if df_g.empty:
                st.info("No hay agendamientos en el período seleccionado.")
            else:
                df_c = df_g.iloc[::-1]
                etiquetas = df_c["num_agendamiento"] + " (" + df_c["fecha_registro"] + ")"
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(x=etiquetas, y=df_c["ton_efectivas"], name="Toneladas plan", marker_color="#0284C7"), secondary_y=False)
                fig.add_trace(go.Bar(x=etiquetas, y=df_c["ton_reales"], name="Toneladas reales (cierre)", marker_color="#10B981"), secondary_y=False)
                fig.add_trace(go.Scatter(x=etiquetas, y=df_c["match_factor"], name="Match Factor", mode="lines+markers",
                                         line=dict(color="#F59E0B", width=3)), secondary_y=True)
                for y in (0.92, 1.08):
                    fig.add_hline(y=y, line_dash="dot", line_color="#10B981", secondary_y=True)
                fig.update_layout(title_text="<b>Plan vs. real y Match Factor por agendamiento</b>", barmode="group",
                                  template="plotly_white", height=450, margin=dict(l=20, r=20, t=60, b=20),
                                  legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                fig.update_yaxes(title_text="<b>Toneladas</b>", secondary_y=False)
                fig.update_yaxes(title_text="<b>MF</b>", secondary_y=True, range=[0, 1.6])
                st.plotly_chart(fig, use_container_width=True)
 
                con_cierre = df_g.dropna(subset=["ton_reales"])
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Toneladas plan", f"{fmt_num(df_g['ton_efectivas'].sum())} t")
                m2.metric("Cumplimiento (con cierre)",
                          f"{fmt_num(con_cierre['ton_reales'].sum() / con_cierre['ton_efectivas'].sum() * 100, 1)}%"
                          if not con_cierre.empty and con_cierre["ton_efectivas"].sum() > 0 else "—",
                          delta=f"{len(con_cierre)}/{len(df_g)} cerrados", delta_color="off")
                m3.metric("MF promedio", fmt_num(df_g["match_factor"].mean(), 2))
                m4.metric("En banda verde", f"{fmt_num(df_g['en_banda_verde'].mean() * 100, 1)}%")
                m5.metric("Adopción declarada", f"{fmt_num(df_g['prescripcion_aceptada'].mean() * 100, 1)}%")
    else:
        st.markdown(f"### 👤 Agendamientos de hoy — {html.escape(USUARIO_ACTIVO)}")
        df_hoy = df_full[(df_full["jefe_turno"] == USUARIO_ACTIVO) & (df_full["fecha_registro"] == fecha_str)]
        if df_hoy.empty:
            st.info("No tiene agendamientos guardados hoy.")
        else:
            st.dataframe(df_hoy[["num_agendamiento", "hora_registro", "turno", "n_caex", "match_factor", "ton_efectivas",
                                 "costo_total_ton_usd", "ton_reales", "adherencia_pct"]], use_container_width=True, hide_index=True)
 
