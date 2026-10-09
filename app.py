# ==============================================================================
# 1. IMPORTS Y LIBRERÍAS ESTÁNDAR / TERCEROS (PEP 8)
# ==============================================================================
import base64
from datetime import date, datetime
import html
import json
import os
import hashlib
import hmac
import sqlite3
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
    page_title="OptiMatch Mine v3.1 — Control Prescriptivo",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded"
)
 
# ==============================================================================
# 3. (SECCIÓN ELIMINADA EN v3.1: EL HILO KEEP-ALIVE NO MANTENÍA ACTIVO EL SERVIDOR)
# ==============================================================================
 
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
        # Las contraseñas se guardan cifradas (PBKDF2-SHA256, formato "sal$hash"); no hay claves en texto plano.
        ("mcepeda", "c8af41c9737cade4abde89210d416324$6b855ddb22e92d4bf1c4b9b3da7b824b1c4e3e78723b2b6e1e8aff051df8c12b", "Mauricio L. Cepeda Mondaca", "Administrador"),
        ("avidela", "558acc41736cfd447405ecd483962169$c30038f9268347d437a0a3e8be1eb541845921544c102d921704eb857d576ceb", "Andy Videla Obregón", "Supervisor Mina"),
        ("ddaines", "6ec698d8f91af6b59aa4f7e2a2f5d5ab$77faab5668d6f8de81ccf72153c4211b85d888c3f07fd88bcf28d9098456e31c", "Daniel Daines Araya", "Supervisor Mina"),
        ("cnikulin", "222210e37891c410b8b9858863c2e1fa$91832aef9a393b7b2ba3a5ae629cc013a8ff524df512676e20f6d96dbd685e05", "Dr. Christopher Nikulin", "Gerente Operaciones / Evaluador"),
        ("cperez", "9f7d956901690cef9c44a07840fccd59$4a25ef86503c586cb81e9bae61ea910fd38fed7a72d417b41ce55869c04d68c4", "Dr. Camilo Pérez", "Gerente Operaciones / Evaluador"),
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
 
    # Columnas nuevas v3.1 (material del turno, destino, prescripción y costos totales)
    nuevas_columnas = {
        "material": "TEXT DEFAULT 'Mineral'", "destino": "TEXT", "perfil_rampa": "TEXT",
        "distancia_km": "REAL", "n_caex": "INTEGER DEFAULT 0", "n_carguio": "INTEGER DEFAULT 0",
        "t_carguio_min": "REAL", "espera_min": "REAL", "n_prescrito": "INTEGER",
        "en_banda_verde": "INTEGER DEFAULT 0", "costo_total_usd": "REAL DEFAULT 0.0",
        "costo_total_ton_usd": "REAL DEFAULT 0.0",
    }
    for col_nueva, tipo_col in nuevas_columnas.items():
        if col_nueva not in columnas:
            c.execute(f"ALTER TABLE historico_agendamientos ADD COLUMN {col_nueva} {tipo_col}")
    conn.commit()
 
    # Cierres de turno (datos reales ingresados después del turno)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cierres_turno (
            num_agendamiento TEXT PRIMARY KEY,
            fecha_cierre TEXT,
            responsable TEXT,
            ton_reales REAL,
            diesel_real_lts REAL,
            costo_real_usd REAL,
            costo_real_ton_usd REAL,
            adherencia_pct REAL,
            causas TEXT,
            observaciones TEXT
        )
    """)
    conn.commit()
 
    conn.close()
 
init_db()
 
# ==============================================================================
# 6. MOTOR DE CÁLCULO DEL CIRCUITO CARGUÍO-TRANSPORTE (MF, ESPERA, TASA EFECTIVA)
# ==============================================================================
T_MANIOBRAS_MIN = 2.30   # acople + volteo (min)
CONSUMO_MOV_REF = 45.0   # L/h de referencia de un CAEX de 90 t en movimiento
CONSUMO_ESP_REF = 28.0   # L/h de referencia del mismo CAEX en espera (ralentí)
FACTOR_CO2 = 2.68        # kg de CO2 por litro de diésel
 
 
def funcion_espera(mf):
    """Espera media en cola (min/ciclo) según el MF. Coeficientes: supuesto del prototipo."""
    if mf <= 0:
        return 0.0
    if mf <= 0.94:
        return 2.0 * (mf / 0.94)
    return 2.0 + 8.5 * ((mf - 0.94) ** 1.3)
 
 
def clasificar_semaforo(mf):
    """Banda Lean ±8 %. El MF se redondea a 2 decimales antes de clasificar."""
    m = round(mf, 2)
    if 0.92 <= m <= 1.08:
        return "Verde"
    if 0.85 <= m <= 1.15:
        return "Amarillo"
    return "Rojo"
 
 
def ejecutar_simulacion_analitica(
    caex_activos_df,
    n_carguio=1,
    rend_carguio_total_th=1216.0,
    costo_carguio_h=441.0,
    duracion_horas=10.0,
    fl_factor=0.88,
    merma_base_pct=1.2,
    perfil_rampa_key="Plano / Pista Recta (0% - 3%)",
    distancia_km=3.2,
    vel_cargado_base=18.0,
    vel_vacio_base=30.0,
    precio_diesel=1.15,
):
    """
    Circuito de UN material y UN destino:
      t_carguío = 60 x carga efectiva del camión / rendimiento medio del equipo de carguío (t/h)
      t_ciclo   = t_carguío + t_ida + t_retorno + t_maniobras
      MF        = N_camiones x t_carguío / (N_carguío x t_ciclo)
      Toneladas = mín(capacidad de transporte, capacidad de carguío)   (Tasa Efectiva)
    """
    n_camiones = len(caex_activos_df)
    n_carguio = max(1, int(n_carguio))
    rampa_info = PERFIL_RAMPAS.get(perfil_rampa_key, PERFIL_RAMPAS["Plano / Pista Recta (0% - 3%)"])
 
    def _media(col, defecto):
        if n_camiones > 0 and col in caex_activos_df.columns:
            return float(pd.to_numeric(caex_activos_df[col], errors="coerce").fillna(defecto).mean())
        return defecto
 
    # Tiempos de viaje
    vel_cargado_efectiva = vel_cargado_base * rampa_info["vel_adj"]
    t_ida = (distancia_km / vel_cargado_efectiva) * 60.0 if vel_cargado_efectiva > 0 else 0.0
    t_retorno = (distancia_km / vel_vacio_base) * 60.0 if vel_vacio_base > 0 else 0.0
 
    # Carga por viaje y tiempo de carguío según el rendimiento real de la pala / cargador
    cap_tolva_nominal = _media("Cap_Ton", 90.0)
    cap_tolva_efectiva = cap_tolva_nominal * fl_factor
    rend_medio = rend_carguio_total_th / n_carguio if rend_carguio_total_th > 0 else 1216.0
    t_carguio = 60.0 * cap_tolva_efectiva / rend_medio
    t_ciclo_base = t_carguio + t_ida + t_retorno + T_MANIOBRAS_MIN
 
    # Match Factor y espera en cola
    mf = (n_camiones * t_carguio) / (n_carguio * t_ciclo_base) if t_ciclo_base > 0 else 0.0
    espera = funcion_espera(mf)
    t_ciclo_efectivo = t_ciclo_base + espera
    vueltas = (duracion_horas * 60.0) / t_ciclo_efectivo if t_ciclo_efectivo > 0 else 0.0
 
    # Tasa Efectiva: no se puede cargar más de lo que permite el equipo de carguío
    ton_transporte = n_camiones * vueltas * cap_tolva_efectiva
    capacidad_carguio_turno = rend_carguio_total_th * duracion_horas
    limitado_por_carguio = ton_transporte > capacidad_carguio_turno
    toneladas_cargadas = min(ton_transporte, capacidad_carguio_turno)
    if limitado_por_carguio and n_camiones > 0 and cap_tolva_efectiva > 0:
        vueltas = toneladas_cargadas / (n_camiones * cap_tolva_efectiva)
 
    merma_efectiva_pct = merma_base_pct * rampa_info["f_merma"]
    toneladas_merma = toneladas_cargadas * (merma_efectiva_pct / 100.0)
    toneladas_efectivas = toneladas_cargadas - toneladas_merma
 
    # Diésel de los CAEX: horas en movimiento y en espera (el diésel del carguío no se modela)
    consumo_mov = _media("Consumo_LtsH", CONSUMO_MOV_REF)
    consumo_esp = consumo_mov * CONSUMO_ESP_REF / CONSUMO_MOV_REF
    horas_mov = min(duracion_horas, vueltas * t_ciclo_base / 60.0)
    horas_esp = max(0.0, duracion_horas - horas_mov) if n_camiones > 0 else 0.0
    litros_totales = n_camiones * (horas_mov * consumo_mov + horas_esp * consumo_esp) * rampa_info["f_consumo"]
 
    # Costos: costo horario de cada equipo agendado + diésel
    if n_camiones > 0 and "Costo_USDH" in caex_activos_df.columns:
        costo_flota_h = float(pd.to_numeric(caex_activos_df["Costo_USDH"], errors="coerce").fillna(290.0).sum())
    else:
        costo_flota_h = 290.0 * n_camiones
    costo_opex = (costo_flota_h + costo_carguio_h) * duracion_horas
    costo_diesel = litros_totales * precio_diesel
    costo_total = costo_opex + costo_diesel
 
    def _por_ton(valor):
        return valor / toneladas_efectivas if toneladas_efectivas > 0 else 0.0
 
    return {
        "MF": mf,
        "cola_min": espera,
        "t_carguio_min": t_carguio,
        "t_ciclo_base": t_ciclo_base,
        "t_ciclo_min": t_ciclo_efectivo,
        "t_ida_min": t_ida,
        "t_retorno_min": t_retorno,
        "vel_cargado_efectiva": vel_cargado_efectiva,
        "vueltas_por_camion": vueltas,
        "cap_tolva_efectiva": cap_tolva_efectiva,
        "capacidad_carguio_turno": capacidad_carguio_turno,
        "limitado_por_carguio": limitado_por_carguio,
        "ton_cargadas": toneladas_cargadas,
        "ton_merma": toneladas_merma,
        "merma_pct": merma_efectiva_pct,
        "ton_totales": toneladas_efectivas,
        "litros_totales": litros_totales,
        "consumo_especifico_l_ton": _por_ton(litros_totales),
        "costo_opex": costo_opex,
        "costo_unitario_usd_ton": _por_ton(costo_opex),
        "costo_diesel": costo_diesel,
        "costo_total": costo_total,
        "costo_total_usd_ton": _por_ton(costo_total),
    }
 
 
def barrido_flota(caex_activos_df, n_max, **parametros):
    """Evalúa N = 1 ... n_max camiones iguales al camión promedio agendado (prescripción)."""
    if caex_activos_df is not None and len(caex_activos_df) > 0:
        tipo = {c: float(pd.to_numeric(caex_activos_df[c], errors="coerce").mean())
                for c in ("Cap_Ton", "Costo_USDH", "Consumo_LtsH") if c in caex_activos_df.columns}
    else:
        tipo = {"Cap_Ton": 90.0, "Costo_USDH": 290.0, "Consumo_LtsH": 45.0}
    resultados, previo = [], None
    for n in range(1, max(1, int(n_max)) + 1):
        r = ejecutar_simulacion_analitica(pd.DataFrame([tipo] * n), **parametros)
        r["N"] = n
        r["semaforo"] = clasificar_semaforo(r["MF"])
        r["costo_marginal"] = None
        if previo is not None and r["ton_totales"] > previo["ton_totales"] + 1e-6:
            r["costo_marginal"] = (r["costo_total"] - previo["costo_total"]) / (r["ton_totales"] - previo["ton_totales"])
        resultados.append(r)
        previo = r
    return resultados
 
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
    c.execute("SELECT COALESCE(MAX(id), 0) FROM historico_agendamientos")
    ultimo = c.fetchone()[0]
    conn.close()
    return f"AGN-{datetime.now().year}-{ultimo + 1:03d}"
 
def _hash_clave(clave, sal_hex):
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal_hex), 200000).hex()
 
def validar_usuario(usr, pwd):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, nombre_completo, rol, password FROM usuarios WHERE username = ?", (usr,))
    fila = c.fetchone()
    conn.close()
    if not fila or "$" not in fila[3]:
        return None
    sal_hex, hash_guardado = fila[3].split("$", 1)
    if hmac.compare_digest(_hash_clave(pwd, sal_hex), hash_guardado):
        return fila[:3]
    return None
 
def guardar_agendamiento_db(registro):
    """Guarda un agendamiento. registro = {columna: valor}."""
    columnas_sql = ", ".join(registro.keys())
    marcas = ", ".join(["?"] * len(registro))
    conn = sqlite3.connect(DB_FILE)
    conn.execute(f"INSERT INTO historico_agendamientos ({columnas_sql}) VALUES ({marcas})", list(registro.values()))
    conn.commit()
    conn.close()
 
def guardar_cierre_db(registro):
    """Guarda (o reemplaza) el cierre real de un agendamiento."""
    columnas_sql = ", ".join(registro.keys())
    marcas = ", ".join(["?"] * len(registro))
    conn = sqlite3.connect(DB_FILE)
    conn.execute(f"INSERT OR REPLACE INTO cierres_turno ({columnas_sql}) VALUES ({marcas})", list(registro.values()))
    conn.commit()
    conn.close()
 
def borrar_historico_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM historico_agendamientos")
    c.execute("DELETE FROM cierres_turno")
    conn.commit()
    conn.close()
 
def fmt_num(val, dec=0):
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "—"
    if val != val:  # NaN
        return "—"
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
 
# Imagen del cargador frontal incluida en el código (no requiere subir archivos de imagen)
ICONO_CARGADOR_URI = "data:image/png;base64,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"
# Procedimiento de uso (PDF en el repositorio público) y su código QR
URL_PROCEDIMIENTO = "https://raw.githubusercontent.com/2135mlcm/OptiMatch-Mine/main/Procedimiento_OptiMatch-Mine.pdf"
QR_PROCEDIMIENTO_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAkwAAAJMAQAAAAAgH8RuAAAEFklEQVR4nO2dQY7sKgxFr7/ePNlB739ZvQOyAv8BYJtUjypVT+lXx2qVCA1HZHBlwDiY60V2/PcqkgQKFChQoECBAgUKFChQoN6Nkru7l0Xh5lGvzfujN2nz8Vvqo4u7e7vnC4IC9UtQXUdTjN5ku7xJh6UAZ70Oe+xy9xcEBer2KDOzXZLCzXUZ2pebWX/sFrrLLm8bFShQH4jybxvO7jBt7t+mzc2su8JeP8p/cVSgQH0Oyr6G7vrM03b1R01X2Cv/8qhAgfpHUX+i5KVgm3cnOOrLo7ZmWzt1efWoQIH6PNRh3RTO7ms4vvH3lX5wFEqXd40KFKh/HzX8YPVoLvlh/lBWFB66vHpUoEB9DmrGB2f0YViJDHobbUZksIcLI4Ao4oOgQF2yjPFV0TX1mv4vdx9KbKo1EbJHg6BAPW3h47qmVHzf8IYnF6kRsk8H6mgQFKjnrfrB9G5Nob5Fj0ovOdxiYy4KCtQVm3syx+6H+bFrc5Osb7lEPELyw3oz29w2t62Z5BG8ePGoQIH6HNQyF11+Y1Wo4iJLl6jHD4ICdcVCULnPqbkwjNmmVJeEKcY2p6xoEBSoZy012FY9KvX1wyZMy5PbaBAUqCtWNZjxvpYyDOlpjVykEtEgKFAX7BximGUpZ6S9XFeLKkm+czl5zxcEBeo3ofr5z0jg9W/rle7ey2HuPjJ58xDpL3hBUKDuh0o/KOXBmGHp49L9Lfs2sXWDHwQF6hWokb27eRXjyBbs8UF3HZbLw57eu793VKBA/dOozB/U1kba4GGeYfrd52MP349yRO2J0YMCdc1yK6YmRGg5FPo4HT0HJpiLggL1rOVeaJu6a8th0bplOsTYJJUD3mgQFKgLVt3cqNqK1kKb7qdfSaujvOcLggJ1c9Sau6RyMq2qLLIntH7mdyshQjQICtRTlnPRuSrML/rWxPlZmR1LGzQICtSzlvuiY7dT87sx5ftpNqOHOvaxWTrbR5u7viAoUDdHlfVgPfxZF4mRQtgyLq+HT83gB0GBes5iLiqts9BImS8HY05SFXNRUKAuWy4DV+nVNIpUX5u9ahs0CArUBQsNnrxb2Pl+pZJMkUlMaBAUqGftUV+jUOalOV+t2UyzJRoEBeqK+YPl5LPs1Sypuz96TDQICtQFlIXt0mGROdj1GJdK9FtBew7FyLB456hAgfogVF30dd3t4z+htbj4rNvQIzm8oEBdsjVGHwH3Y49Kn5eBDl+4NZMiif49owIF6nNQf36om4mB8ySMa6YTpgy3pmP3YzfyB0GBumanGH2mLGkJ0Of+TFuCg8ToQYG6aOcgYCmsaRHrSbaSRoEGQYG6Ykt8UA83D7bR5vH+F2IToEC9wpardS/Zcc8XBAUKFChQoECBAgUKFChQP9r/PJ/6lQ0HkMYAAAAASUVORK5CYII="
 
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
 
        st.markdown(f"""
            <div style="text-align: center; margin-top: 14px;">
                <img src="{QR_PROCEDIMIENTO_URI}" style="width: 120px; height: 120px; border: 1px solid #CBD5E1; border-radius: 8px;">
                <p style="font-size: 12px; color: #64748B; font-weight: 700; margin: 6px 0 0 0;">
                    📘 ¿Primera vez? Escanee el código o abra el
                    <a href="{URL_PROCEDIMIENTO}" target="_blank">Procedimiento de uso</a>
                </p>
            </div>
        """, unsafe_allow_html=True)
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
        <h1 style="color: #0F172A; margin: 0; font-size: 24px; font-weight: 900; tracking-tight;">OptiMatch Mine — Control Prescriptivo v3.1</h1>
        <p style="color: #0284C7; margin: 2px 0 0 0; font-size: 12px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">
            SISTEMA DE SOPORTE A LA DECISIÓN PRE-TURNO PARA LA MEDIANA MINERÍA
        </p>
        <p style="color: #64748B; margin: 2px 0 0 0; font-size: 11px; font-weight: 600;">
            Optimización del Match Carguío-Transporte & Control de Rentabilidad OPEX | Universidad Alberto Hurtado
        </p>
    </div>
""", unsafe_allow_html=True)
 
b64_logo_sidebar = obtener_base64_img(LOGO_PATH)
if b64_logo_sidebar:
    st.sidebar.markdown(
        f'<div style="background:#FFFFFF; border-radius:10px; padding:6px; margin-bottom:6px; text-align:center;">'
        f'<img src="{b64_logo_sidebar}" style="width:100%; height:auto; display:block;"></div>',
        unsafe_allow_html=True)
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
 
# Procedimiento de uso: título visible + QR (el QR también es un enlace al PDF)
st.sidebar.markdown(
    '<span class="selector-label-centered">📘 PROCEDIMIENTO DE USO</span>'
    f'<div style="text-align:center; margin: 4px 0 10px 0;">'
    f'<a href="{URL_PROCEDIMIENTO}" target="_blank" title="Abrir el procedimiento (PDF)">'
    f'<img src="{QR_PROCEDIMIENTO_URI}" style="width:110px; height:110px; border-radius:6px; border:2px solid #F59E0B; background:#FFFFFF;"></a>'
    '<span style="display:block; color:#F8FAFC; font-size:10px; font-weight:700; margin-top:4px;">Escanee o haga clic en el código</span>'
    '</div>',
    unsafe_allow_html=True,
)
 
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
st.sidebar.markdown('<span class="selector-label-centered">DESTINO DEL MATERIAL EN ESTE AGENDAMIENTO</span>', unsafe_allow_html=True)
destino_turno = st.sidebar.selectbox(
    "Destino del material",
    ["Chancador primario", "Pila de acopio (stockpile)", "Botadero (estéril)"],
    key="select_destino_material", label_visibility="collapsed",
    help="Chancador o pila = transporte de mineral. Botadero = transporte de estéril (no genera ingreso, solo costo).",
)
material_turno = "Estéril" if destino_turno.startswith("Botadero") else "Mineral"
if material_turno == "Mineral":
    target_mineral_num = st.sidebar.number_input("Objetivo Mineral (Ton)", value=18000, step=1000, min_value=0, key="meta_mineral")
    target_esteril_num = 0
else:
    st.sidebar.markdown('<div class="auto-box">⛰️ Transporte de ESTÉRIL al botadero</div>', unsafe_allow_html=True)
    target_esteril_num = st.sidebar.number_input("Objetivo Estéril (Ton)", value=12000, step=1000, min_value=0, key="meta_esteril")
    target_mineral_num = 0
meta_activa = target_mineral_num if material_turno == "Mineral" else target_esteril_num
 
st.sidebar.markdown("---")
tc_mercado, diesel_mercado = obtener_indicadores_mercado()
 
st.sidebar.markdown(f"""
    <div style="background-color: #1E293B; padding: 6px; border-radius: 6px; border: 1px solid #0284C7; text-align: center; margin-bottom: 6px;">
        <span style="color: #38BDF8 !important; font-size: 9px; font-weight: 800; display: block; text-transform: uppercase;">🌐 MERCADO EN VIVO (MINDICADOR.CL · DÓLAR OBSERVADO)</span>
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
distancia_acarreo_km = st.sidebar.number_input(f"Distancia al {destino_turno} (km)", value=3.2, step=0.1, min_value=0.1, key=f"dist_{material_turno}")
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
        st.markdown(f'<img src="{ICONO_CARGADOR_URI}" style="width: 70px; height: auto;">', unsafe_allow_html=True)
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
# Fecha y hora en columnas separadas: el Jefe de Turno Mina elige la fecha en un
# calendario y la hora en una lista de 00:00 a 23:30 (24 horas).
# -----------------------------------------------------------------------------
INTERVALO_HORA_MIN = 30   # paso del selector de hora en minutos (use 60 para solo horas enteras)
OPCIONES_HORA = [f"{m // 60:02d}:{m % 60:02d}" for m in range(0, 24 * 60, INTERVALO_HORA_MIN)]
hoy_fecha = now_dt.date()
 
COL_F_INI, COL_H_INI = "Fecha Inicio Detención", "Hora Inicio Detención"
COL_F_ETR, COL_H_ETR = "Fecha Estimada Salida (ETR)", "Hora Estimada Salida (ETR)"
 
lista_logistica_turno_opciones = ["Mecánica / Turno A", "Contratista / Turno B", "Logística / Turno A", "Logística / Turno B"]
lista_autoriza_opciones = ["Gerente Mina", "Jefe Oper. Mina", "Jefe Turno Mina (A)", "Jefe Turno (B)", "Jefe de Taller", "Jefe Taller", "AdC Minera"]
 
columnas_control_estandar = ["ID- Equipo", "Tipo / Flota", "Ubicación Actual", "Estado de Mantención", "Tipo de Falla / Trabajo",
                             COL_F_INI, COL_H_INI, COL_F_ETR, COL_H_ETR, "Logística / Turno", "Plazo Extra Días", "Quien Autoriza"]
 
 
def _a_fecha(valor):
    """Convierte cualquier formato de fecha (date, Timestamp, texto dd-mm-aaaa) a date."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor is pd.NaT:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return pd.to_datetime(str(valor), dayfirst=True).date()
    except (ValueError, TypeError):
        return None
 
 
def _separar_fecha_hora(valor):
    """Formato antiguo «dd/mm/aaaa HH:MM» o «dd-mm-aaaa HH:MM» → (fecha, hora)."""
    texto = str(valor).strip()
    for formato in ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            dt = datetime.strptime(texto, formato)
            return dt.date(), dt.strftime("%H:%M")
        except ValueError:
            pass
    return hoy_fecha, "00:00"
 
 
def _migrar_formato_control(df):
    """Adapta tablas guardadas en sesión con el formato anterior (fecha y hora juntas)."""
    df = df.copy()
    if "Logística / Turno A" in df.columns and "Logística / Turno" not in df.columns:
        df = df.rename(columns={"Logística / Turno A": "Logística / Turno"})
    for antigua, col_f, col_h in [("Inicio Detención", COL_F_INI, COL_H_INI),
                                  ("Estimado de Salida (ETR)", COL_F_ETR, COL_H_ETR)]:
        if antigua in df.columns:
            if col_f not in df.columns:
                partes = [_separar_fecha_hora(v) for v in df[antigua]]
                df[col_f] = [p[0] for p in partes]
                df[col_h] = [p[1] for p in partes]
            df = df.drop(columns=[antigua])
    return df
 
 
def _fila_control(eq_id, tipo, ubicacion, estado_txt, trabajo, hora_ini, hora_etr, logistica, plazo, autoriza):
    return {"ID- Equipo": eq_id, "Tipo / Flota": tipo, "Ubicación Actual": ubicacion,
            "Estado de Mantención": estado_txt, "Tipo de Falla / Trabajo": trabajo,
            COL_F_INI: hoy_fecha, COL_H_INI: hora_ini, COL_F_ETR: hoy_fecha, COL_H_ETR: hora_etr,
            "Logística / Turno": logistica, "Plazo Extra Días": plazo, "Quien Autoriza": autoriza}
 
 
equipos_no_disponibles = []
for _, r in ed_palas[ed_palas["Estado"] != "🟢 Disponible"].iterrows():
    mant = "Mantenimiento" in r["Estado"]
    equipos_no_disponibles.append(_fila_control(
        r["ID"], "Pala Eléctrica", "Taller Central - Bahía 1",
        "Programada (PM 500 hrs)" if mant else "Correctivo (Emergencia)",
        "Inspección y mantenimiento preventivo" if mant else "Falla mecánica reportada en terreno",
        "08:00", "20:00", "Mecánica / Turno A", 2, "Jefe Turno Mina (A)"))
for _, r in ed_cf[ed_cf["Estado"] != "🟢 Disponible"].iterrows():
    mant = "Mantenimiento" in r["Estado"]
    equipos_no_disponibles.append(_fila_control(
        r["ID"], "Cargador Frontal", "Taller de Neumáticos",
        "Programada (PM 500 hrs)" if mant else "Correctivo (Emergencia)",
        "Cambio de neumáticos y fluidos" if mant else "Reparación de transmisión",
        "10:30", "22:00", "Contratista / Turno B", 1, "Jefe Taller"))
for _, r in ed_caex[ed_caex["Estado"] != "🟢 Disponible"].iterrows():
    mant = "Mantenimiento" in r["Estado"]
    equipos_no_disponibles.append(_fila_control(
        r["ID"], "Camión CAEX", "Taller Central - Bahía 3",
        "Programada (PM 500 hrs)" if mant else "Correctivo (Emergencia)",
        "Mantención preventivo 500 hrs" if mant else "Falla en sistema de frenos / motor",
        "07:00", "18:00", "Mecánica / Turno A", 2, "Jefe Oper. Mina"))
 
# Conserva lo que ya ingresó el Jefe de Turno y actualiza solo el estado de mantención
if "control_estados_mina_df" not in st.session_state:
    st.session_state.control_estados_mina_df = pd.DataFrame(equipos_no_disponibles, columns=columnas_control_estandar)
else:
    df_previo = _migrar_formato_control(st.session_state.control_estados_mina_df)
    nuevos_rows = []
    for eq in equipos_no_disponibles:
        match_prev = df_previo[df_previo["ID- Equipo"] == eq["ID- Equipo"]] if "ID- Equipo" in df_previo.columns else pd.DataFrame()
        if not match_prev.empty:
            row_dict = {**eq, **{k: v for k, v in match_prev.iloc[0].to_dict().items() if k in columnas_control_estandar}}
            row_dict["Estado de Mantención"] = eq["Estado de Mantención"]
            nuevos_rows.append(row_dict)
        else:
            nuevos_rows.append(eq)
    st.session_state.control_estados_mina_df = pd.DataFrame(nuevos_rows, columns=columnas_control_estandar)
 
df_ctrl = st.session_state.control_estados_mina_df.copy()
for col_f in (COL_F_INI, COL_F_ETR):
    df_ctrl[col_f] = df_ctrl[col_f].map(_a_fecha)
 
contenedor_alertas = st.container()     # las alertas se muestran arriba, pero se calculan después de editar
contenedor_tabla = st.container()
 
if not df_ctrl.empty:
    with st.expander("✏️ Ingreso de fecha y hora — Jefe de Turno Mina", expanded=True):
        st.caption("Elija la **fecha** en el calendario y la **hora** en la lista (00:00 a 23:30). "
                   "La Tabla de Control de arriba y las alertas se actualizan al instante.")
        ed_control_estados = st.data_editor(
            df_ctrl,
            column_config={
                "ID- Equipo": st.column_config.TextColumn("ID- Equipo", disabled=True),
                "Tipo / Flota": st.column_config.TextColumn("Tipo / Flota", disabled=True),
                COL_F_INI: st.column_config.DateColumn("📅 Fecha Inicio Detención", format="DD-MM-YYYY", required=True),
                COL_H_INI: st.column_config.SelectboxColumn("🕒 Hora Inicio Detención", options=OPCIONES_HORA, required=True),
                COL_F_ETR: st.column_config.DateColumn("📅 Fecha Estimada Salida (ETR)", format="DD-MM-YYYY", required=True),
                COL_H_ETR: st.column_config.SelectboxColumn("🕒 Hora Estimada Salida (ETR)", options=OPCIONES_HORA, required=True),
                "Logística / Turno": st.column_config.SelectboxColumn("Logística / Turno", options=lista_logistica_turno_opciones),
                "Plazo Extra Días": st.column_config.SelectboxColumn("Plazo Extra Días", options=[i for i in range(31)]),
                "Quien Autoriza": st.column_config.SelectboxColumn("Quien Autoriza", options=lista_autoriza_opciones),
            },
            column_order=columnas_control_estandar,
            hide_index=True,
            key="editor_control_estados_mina_fh",
            use_container_width=True,
        )
    st.session_state.control_estados_mina_df = ed_control_estados
 
    # --- Estado de cada ETR con fecha y hora exactas ---
    def _fecha_hora(fecha_val, hora_val):
        f = _a_fecha(fecha_val)
        try:
            h = datetime.strptime(str(hora_val)[:5], "%H:%M").time()
        except (ValueError, TypeError):
            return None
        return datetime.combine(f, h) if f else None
 
    def _horas_txt(horas):
        return f"{fmt_num(horas / 24, 1)} días" if horas >= 48 else f"{fmt_num(horas, 1)} h"
 
    filas_vista, equipos_vencidos, equipos_hoy, equipos_inconsistentes = [], [], [], []
    for _, row in ed_control_estados.iterrows():
        ini = _fecha_hora(row[COL_F_INI], row[COL_H_INI])
        etr = _fecha_hora(row[COL_F_ETR], row[COL_H_ETR])
        detenido = (now_dt - ini).total_seconds() / 3600 if ini and ini <= now_dt else 0.0
        if etr is None:
            estado_etr, clase = "⚪ Sin fecha/hora de ETR", "sin"
        elif ini and etr < ini:
            estado_etr, clase = "⚠️ ETR anterior al inicio", "inc"
            equipos_inconsistentes.append(row["ID- Equipo"])
        elif etr < now_dt:
            vencido_h = (now_dt - etr).total_seconds() / 3600
            estado_etr, clase = f"🔴 VENCIDO hace {_horas_txt(vencido_h)}", "venc"
            equipos_vencidos.append(f"{row['ID- Equipo']} (ETR {etr:%d-%m-%Y %H:%M})")
        elif etr.date() == now_dt.date():
            faltan_h = (etr - now_dt).total_seconds() / 3600
            estado_etr, clase = f"🟡 Vence HOY en {_horas_txt(faltan_h)}", "hoy"
            equipos_hoy.append(f"{row['ID- Equipo']} ({etr:%H:%M} h)")
        else:
            faltan_h = (etr - now_dt).total_seconds() / 3600
            estado_etr, clase = f"🟢 En plazo ({_horas_txt(faltan_h)})", "ok"
        fila = row.to_dict()
        fila[COL_F_INI] = ini.strftime("%d-%m-%Y") if ini else "—"
        fila[COL_F_ETR] = etr.strftime("%d-%m-%Y") if etr else "—"
        fila["Horas Detenido"] = _horas_txt(detenido)
        fila["Estado ETR"] = estado_etr
        fila["_clase"] = clase
        filas_vista.append(fila)
 
    with contenedor_alertas:
        if equipos_vencidos:
            st.markdown(f'<div style="background-color: #DC2626; color: #FFFFFF; padding: 10px; border-radius: 8px; font-weight: 800; margin-bottom: 8px;">🔴 ALERTA DE VENCIMIENTO CRÍTICO: {", ".join(equipos_vencidos)} — ETR vencido. Revise taller urgentemente.</div>', unsafe_allow_html=True)
        if equipos_hoy:
            st.markdown(f'<div style="background-color: #F59E0B; color: #0F172A; padding: 10px; border-radius: 8px; font-weight: 800; margin-bottom: 8px;">🟡 ALERTA DE VENCIMIENTO HOY: {", ".join(equipos_hoy)} — vencen su ETR durante la jornada ({fecha_str}). Planifique relevo con el Jefe de Turno.</div>', unsafe_allow_html=True)
        if equipos_inconsistentes:
            st.markdown(f'<div style="background-color: #7C3AED; color: #FFFFFF; padding: 10px; border-radius: 8px; font-weight: 800; margin-bottom: 8px;">⚠️ REVISAR REGISTRO: en {", ".join(equipos_inconsistentes)} la fecha/hora de salida (ETR) es anterior al inicio de la detención.</div>', unsafe_allow_html=True)
 
    # --- Tabla de Control oficial: todo en negrita y encabezado destacado ---
    COLUMNAS_VISTA = ["ID- Equipo", "Tipo / Flota", "Ubicación Actual", "Estado de Mantención", "Tipo de Falla / Trabajo",
                      COL_F_INI, COL_H_INI, COL_F_ETR, COL_H_ETR, "Horas Detenido", "Estado ETR",
                      "Logística / Turno", "Plazo Extra Días", "Quien Autoriza"]
    fondo_estado = {"venc": "#FECACA", "hoy": "#FDE68A", "ok": "#BBF7D0", "inc": "#DDD6FE", "sin": "#E2E8F0"}
    encabezado = "".join(f"<th>{html.escape(c)}</th>" for c in COLUMNAS_VISTA)
    cuerpo = ""
    for fila in filas_vista:
        celdas = ""
        for c in COLUMNAS_VISTA:
            estilo = f' style="background:{fondo_estado[fila["_clase"]]};"' if c == "Estado ETR" else ""
            celdas += f"<td{estilo}>{html.escape(str(fila.get(c, '')))}</td>"
        cuerpo += f"<tr>{celdas}</tr>"
    with contenedor_tabla:
        st.markdown(f"""
            <style>
            .tabla-control-wrap {{ overflow-x: auto; border-radius: 10px; border: 2px solid #0F172A; margin-bottom: 10px; }}
            .tabla-control {{ width: 100%; border-collapse: collapse; font-weight: 800 !important; font-size: 12.5px; color: #0F172A; background: #FFFFFF; }}
            .tabla-control th {{ background: #0F172A; color: #FFFFFF; font-weight: 900; text-transform: uppercase; font-size: 11.5px;
                                 letter-spacing: 0.3px; padding: 8px 6px; border-bottom: 3px solid #F59E0B; border-right: 1px solid #334155;
                                 white-space: normal; min-width: 78px; line-height: 1.25; text-align: center; vertical-align: middle; }}
            .tabla-control td {{ padding: 7px 8px; border-bottom: 1px solid #CBD5E1; border-right: 1px solid #E2E8F0;
                                 white-space: nowrap; text-align: center; font-weight: 800; }}
            .tabla-control tr:nth-child(even) td {{ background-color: #F1F5F9; }}
            .tabla-control tr:hover td {{ background-color: #FEF3C7; }}
            </style>
            <div class="tabla-control-wrap"><table class="tabla-control">
                <thead><tr>{encabezado}</tr></thead><tbody>{cuerpo}</tbody>
            </table></div>
        """, unsafe_allow_html=True)
else:
    st.info("🟢 Todos los equipos de la flota se encuentran Disponibles. No hay equipos en mantenimiento o taller actualmente.")
 
# ==============================================================================
# 13. EJECUCIÓN DEL MOTOR DE SIMULACIÓN Y CÁLCULOS UNIFICADOS
# ==============================================================================
palas_activas = ed_palas[(ed_palas["Agendar"] == True) & (ed_palas["Estado"] == "🟢 Disponible")]
cf_activos = ed_cf[(ed_cf["Agendar"] == True) & (ed_cf["Estado"] == "🟢 Disponible")]
caex_activos = ed_caex[(ed_caex["Agendar"] == True) & (ed_caex["Estado"] == "🟢 Disponible")]
 
n_carguio_real = len(palas_activas) + len(cf_activos)
n_puestos_carguio = max(1, n_carguio_real)
n_caex_activos = len(caex_activos)
 
# Rendimiento y costo horario reales de las palas / cargadores agendados
rend_carguio_total = float(pd.to_numeric(pd.concat([palas_activas["Rend_TonH"], cf_activos["Rend_TonH"]]), errors="coerce").fillna(0).sum())
costo_carguio_total_h = float(pd.to_numeric(pd.concat([palas_activas["Costo_USDH"], cf_activos["Costo_USDH"]]), errors="coerce").fillna(0).sum())
if rend_carguio_total <= 0:
    rend_carguio_total = 1216.0  # referencia mientras no se agende una unidad de carguío
 
parametros_motor = dict(
    n_carguio=n_puestos_carguio,
    rend_carguio_total_th=rend_carguio_total,
    costo_carguio_h=costo_carguio_total_h,
    duracion_horas=horas_turno,
    fl_factor=fl_valor,
    merma_base_pct=merma_base_valor,
    perfil_rampa_key=perfil_rampa_sel,
    distancia_km=distancia_acarreo_km,
    vel_cargado_base=vel_cargado_kmh,
    vel_vacio_base=vel_vacio_kmh,
    precio_diesel=precio_diesel,
)
res_sim = ejecutar_simulacion_analitica(caex_activos_df=caex_activos, **parametros_motor)
 
match_factor = res_sim["MF"]
semaforo_actual = clasificar_semaforo(match_factor)
tonelaje_cargado = res_sim["ton_cargadas"]
tonelaje_merma = res_sim["ton_merma"]
merma_pct_real = res_sim["merma_pct"]
tonelaje_efectivo = res_sim["ton_totales"]
litros_diesel_turno = res_sim["litros_totales"]
consumo_especifico_lts_ton = res_sim["consumo_especifico_l_ton"]
costo_opex_total_turno = res_sim["costo_opex"]
costo_unitario_ton = res_sim["costo_unitario_usd_ton"]
costo_diesel_turno = res_sim["costo_diesel"]
costo_total_turno = res_sim["costo_total"]
costo_total_ton = res_sim["costo_total_usd_ton"]
tiempo_cola_promedio = res_sim["cola_min"]
tiempo_ciclo_efectivo_min = res_sim["t_ciclo_min"]
t_ciclo_base_exacto = res_sim["t_ciclo_base"]
t_carguio_min = res_sim["t_carguio_min"]
cap_tolva_efectiva_val = res_sim["cap_tolva_efectiva"]
vel_cargado_efectiva_kmh = res_sim["vel_cargado_efectiva"]
t_ida_min = res_sim["t_ida_min"]
t_retorno_min = res_sim["t_retorno_min"]
 
# El estéril no genera ingresos: para estéril el indicador es el costo de remoción
ingreso_bruto_usd = tonelaje_efectivo * valor_ton_usd if material_turno == "Mineral" else 0.0
beneficio_neto_usd = ingreso_bruto_usd - costo_total_turno
costo_diesel_por_ton = (costo_diesel_turno / tonelaje_efectivo) if tonelaje_efectivo > 0 else 0
 
emisiones_co2_kg = litros_diesel_turno * FACTOR_CO2
co2_por_ton = (emisiones_co2_kg / tonelaje_efectivo) if tonelaje_efectivo > 0 else 0.0
vueltas_totales_meta = int(res_sim["vueltas_por_camion"] * n_caex_activos)
en_banda_verde_val = 1 if semaforo_actual == "Verde" else 0
 
# Prescripción: menor número de camiones con semáforo verde (mismo motor, N = 1 ... N máx)
n_max_barrido = max(caex_disponibles, n_caex_activos, 1) + 3
resultados_barrido = barrido_flota(caex_activos, n_max_barrido, **parametros_motor)
recomendado = next((r for r in resultados_barrido if r["semaforo"] == "Verde"), None)
n_prescrito = recomendado["N"] if recomendado else None
 
st.sidebar.markdown("---")
prescripcion_aceptada_val = 1 if st.sidebar.checkbox(
    "Despacho según la prescripción del sistema",
    value=(n_prescrito is not None and n_caex_activos == n_prescrito),
    help="Marque si el Jefe de Turno despacha la flota prescrita. Se registra como adopción de la prescripción.",
) else 0
 
if st.sidebar.button("🔒 CIERRE Y GUARDADO EN BD", use_container_width=True):
    guardar_agendamiento_db({
        "num_agendamiento": num_agendamiento, "fecha_registro": fecha_str, "hora_registro": hora_str,
        "faena": nombre_mina, "turno": turno_seleccionado, "regimen_guardia": regimen_guardia,
        "jefe_turno": st.session_state.get("usuario_activo", "Sin usuario"),
        "material": material_turno, "destino": destino_turno, "perfil_rampa": perfil_rampa_sel,
        "distancia_km": distancia_acarreo_km, "n_caex": n_caex_activos, "n_carguio": n_carguio_real,
        "t_carguio_min": t_carguio_min, "espera_min": tiempo_cola_promedio, "n_prescrito": n_prescrito,
        "ton_movidas": tonelaje_cargado, "ton_efectivas": tonelaje_efectivo, "merma_ton": tonelaje_merma,
        "consumo_diesel_lts": litros_diesel_turno, "costo_diesel_usd": costo_diesel_turno,
        "opex_total_usd": costo_opex_total_turno, "costo_ton_usd": costo_unitario_ton,
        "costo_total_usd": costo_total_turno, "costo_total_ton_usd": costo_total_ton,
        "beneficio_neto_usd": beneficio_neto_usd, "match_factor": match_factor,
        "disponibilidad_fisica": disponibilidad_fisica_val, "factor_llenado": fl_valor,
        "en_banda_verde": en_banda_verde_val, "prescripcion_aceptada": prescripcion_aceptada_val,
    })
    st.sidebar.success(f"✅ Agendamiento {num_agendamiento} guardado exitosamente.")
    st.session_state.autenticado = False
    st.rerun()
 
# ==============================================================================
# 14. DASHBOARD DE RESULTADOS Y CONTROL VISUAL HEADER (UNIFICACIÓN TOTAL MF)
# ==============================================================================
st.markdown("---")
st.markdown(f"<h2 style='text-align: center;'>Resumen de Agendamiento Pre-Turno: {num_agendamiento}</h2>", unsafe_allow_html=True)
 
b64_logo_atacama = obtener_base64_img(LOGO_ATACAMA_PATH)
logo_faena_html = (f'<img src="{b64_logo_atacama}" style="height: 38px; width: auto; vertical-align: middle; object-fit: contain;">'
                   if b64_logo_atacama else "")
st.markdown(
    f'<div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 10px; text-align: center;">'
    f'{logo_faena_html}'
    f'<p style="margin: 0; color: #0F172A; font-size: 16px; font-weight: 800;">'
    f'Faena: {html.escape(nombre_mina)} | {nombre_dia_actual}, {fecha_str} {hora_str} hrs — {turno_seleccionado} ({regimen_guardia})<br>'
    f'Circuito: {material_turno} → {destino_turno} ({fmt_num(distancia_acarreo_km, 1)} km)</p></div>',
    unsafe_allow_html=True,
)
 
if n_carguio_real == 0:
    st.error("⚠️ No hay palas ni cargadores agendados y disponibles. Agende al menos una unidad de carguío; mientras tanto se usa 1.216 t/h como referencia.")
if n_caex_activos == 0:
    st.warning("⚠️ No hay camiones CAEX agendados y disponibles: el agendamiento no mueve material.")
 
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Disp. Física (DF)", f"{disponibilidad_fisica_val:.1f}%", delta=f"{caex_disponibles}/{total_caex} CAEX Activos")
k2.metric("Match Factor (MF)", f"{fmt_num(match_factor, 2)}", delta=f"Semáforo {semaforo_actual}",
          delta_color="normal" if semaforo_actual == "Verde" else "inverse")
k3.metric(f"Ton {material_turno} Entregadas", f"{fmt_num(tonelaje_efectivo, 0)} Ton", delta=f"Merma: {merma_pct_real:.1f}% ({fmt_num(tonelaje_merma, 0)}T)", delta_color="off")
k4.metric("Consumo Diésel CAEX", f"{fmt_num(litros_diesel_turno, 0)} Lts")
k5.metric("Costo Total/Ton", f"${fmt_num(costo_total_ton, 2)} USD/Ton",
          delta=f"Equipos ${fmt_num(costo_unitario_ton, 2)} + diésel ${fmt_num(costo_diesel_por_ton, 2)}", delta_color="off")
if material_turno == "Mineral":
    k6.metric("Beneficio Neto", f"${fmt_num(beneficio_neto_usd, 2)} USD")
else:
    k6.metric("Costo Remoción Estéril", f"${fmt_num(costo_total_turno, 2)} USD", delta="El estéril no genera ingreso", delta_color="off")
 
st.markdown("---")
 
col_eval1, col_eval2 = st.columns(2)
 
with col_eval1:
    st.markdown("### ⛽ Evaluación Económica, Merma y Ruta")
    lineas_eval = [
        f"Tiempo de Carguío: {fmt_num(t_carguio_min, 2)} min por camión ({fmt_num(rend_carguio_total, 0)} t/h en {n_puestos_carguio} unidad(es))",
        f"Ciclo: {fmt_num(t_ciclo_base_exacto, 2)} min + espera {fmt_num(tiempo_cola_promedio, 2)} min = {fmt_num(tiempo_ciclo_efectivo_min, 2)} min",
        f"Velocidad Efectiva Subida: {fmt_num(vel_cargado_efectiva_kmh, 1)} km/h ({perfil_rampa_sel})",
        f"Pérdida en Ruta (Merma): {fmt_num(tonelaje_merma, 0)} Ton ({merma_pct_real:.1f}% del total cargado)",
        f"Costo Equipos: ${fmt_num(costo_opex_total_turno, 0)} USD | Diésel: ${fmt_num(costo_diesel_turno, 0)} USD",
        f"Consumo Específico Diésel: {fmt_num(consumo_especifico_lts_ton, 3)} Lts/Ton Entregada",
        f"Huella CO₂ Operativa: {fmt_num(co2_por_ton, 2)} kg CO₂/Ton ({fmt_num(emisiones_co2_kg, 0)} kg CO₂ total)",
        f"Capacidad Máxima de Carguío del Turno: {fmt_num(res_sim['capacidad_carguio_turno'], 0)} Ton",
    ]
    for linea in lineas_eval:
        st.markdown(f'<p class="highlight-red-large">• {linea}</p>', unsafe_allow_html=True)
    if res_sim["limitado_por_carguio"]:
        st.warning("⚠️ Los camiones podrían transportar más de lo que la unidad de carguío alcanza a cargar: "
                   "la producción quedó limitada por el carguío.")
 
    cumplimiento = (tonelaje_efectivo / meta_activa * 100) if meta_activa > 0 else 0
    st.markdown(f'<p class="highlight-red-large">• Cumplimiento Meta de {material_turno}: {fmt_num(cumplimiento, 1)}% de {fmt_num(meta_activa, 0)} Ton</p>', unsafe_allow_html=True)
    st.progress(min(cumplimiento / 100.0, 1.0))
    st.caption("El diésel de palas y cargadores no se incluye en el cálculo.")
 
with col_eval2:
    st.markdown("### 🚦 Semáforo Prescriptivo de Balance de Flota")
    st.markdown('<p class="mf-label">Match Factor Calculado (Físico):</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="mf-value">{fmt_num(match_factor, 2)}</p>', unsafe_allow_html=True)
 
    mf_txt = fmt_num(match_factor, 2)
    if semaforo_actual == "Verde":
        st.success(f"🟢 **AGENDAMIENTO EN BANDA VERDE (Match Factor: {mf_txt})** — *Flota acoplada, espera acotada y costo unitario cercano al mínimo.*")
    elif semaforo_actual == "Amarillo":
        st.warning(f"🟡 **DESCALCE LEVE EN BANDA AMARILLA (Match Factor: {mf_txt})** — *Evalúe ajustar 1 CAEX según prioridad de tonelaje vs costo.*")
    elif match_factor < 1:
        st.error(f"🔴 **DESCALCE SEVERO POR SUB-TRANSPORTE (Match Factor: {mf_txt})** — *Faltan camiones: la unidad de carguío queda subutilizada.*")
    else:
        st.error(f"🔴 **DESCALCE SEVERO POR EXCESO DE CAMIONES (Match Factor: {mf_txt})** — *Se forman colas en la unidad de carguío.*")
 
    if n_prescrito is not None:
        texto_presc = (f"📌 **Prescripción del sistema: {n_prescrito} CAEX** (MF {fmt_num(recomendado['MF'], 2)}, "
                       f"{fmt_num(recomendado['ton_totales'], 0)} Ton, {fmt_num(recomendado['costo_total_usd_ton'], 2)} USD/Ton). "
                       f"Actualmente hay {n_caex_activos} agendados.")
        if n_prescrito > caex_disponibles:
            texto_presc += f" Atención: solo hay {caex_disponibles} CAEX disponibles."
        st.info(texto_presc)
    else:
        st.info("Ningún tamaño de flota evaluado queda en banda verde con estos parámetros.")
    st.caption("El semáforo clasifica el MF redondeado a dos decimales (banda verde 0,92 – 1,08).")
 
with st.expander("📊 Tabla de prescripción: resultado por número de camiones", expanded=False):
    df_barrido = pd.DataFrame([{
        "N° CAEX": r["N"],
        "Match Factor": fmt_num(r["MF"], 2),
        "Semáforo": {"Verde": "🟢 Verde", "Amarillo": "🟡 Amarillo", "Rojo": "🔴 Rojo"}[r["semaforo"]],
        "Espera (min)": fmt_num(r["cola_min"], 2),
        "Ton Entregadas": fmt_num(r["ton_totales"], 0),
        "Costo Total (USD/Ton)": fmt_num(r["costo_total_usd_ton"], 2),
        "Costo Marginal (USD/Ton adicional)": fmt_num(r["costo_marginal"], 2) if r["costo_marginal"] is not None else "—",
    } for r in resultados_barrido])
    st.dataframe(df_barrido, use_container_width=True, hide_index=True)
    st.caption("Calculado con el camión promedio de los CAEX agendados. Costo marginal: costo extra de cada tonelada adicional al sumar un camión.")
 
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
    f"(Carga: {fmt_num(t_carguio_min, 2)}m | Ida @ {fmt_num(vel_cargado_efectiva_kmh, 1)} km/h: {fmt_num(t_ida_min, 2)}m | "
    f"Descarga: 2.3m | Retorno @ {vel_vacio_kmh} km/h: {fmt_num(t_retorno_min, 2)}m | Espera: {fmt_num(tiempo_cola_promedio, 2)}m)</div>", unsafe_allow_html=True
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
img_cf_b64 = ICONO_CARGADOR_URI
 
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
        const capEfMedia = {cap_tolva_efectiva_val};
        const materialTurno = {json.dumps(material_turno)};
        const destinoTurno = {json.dumps(destino_turno)};
 
        const distKm = {distancia_acarreo_km};
        const speedLoadedKmh = {vel_cargado_efectiva_kmh};
        const speedEmptyKmh = {vel_vacio_kmh};
 
        const imgCaexCargado = new Image(); imgCaexCargado.src = "{img_caex_cargado_b64 or ''}";
        const imgCaexVacio = new Image(); imgCaexVacio.src = "{img_caex_vacio_b64 or ''}";
        const imgPala = new Image(); imgPala.src = "{img_pala_b64 or ''}";
        const imgCF = new Image(); imgCF.src = "{img_cf_b64 or ''}";
 
        const timeLoading = {t_carguio_min};
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
 
        // Cada CAEX recorre: cola -> carga (con viraje) -> ida -> descarga -> retorno -> cola ...
        // Cada pala/cargador atiende un camión a la vez; los demás esperan al final de la línea roja.
        const nCarguio = Math.max(1, palasList.length + cfList.length);
        const colas = Array.from({{ length: nCarguio }}, () => []);
        const cargando = Array.from({{ length: nCarguio }}, () => null);
        let vehicles = caexList.map((c, idx) => {{
            const v = {{
                id: c.id, modelo: c.modelo, capTon: c.capTon || 90.0, operador: c.operador, rend: c.rend,
                vueltas: 0, x: 0, y: 0, x0: 0, y0: 0, ubicado: false, isLoaded: false, isReturning: true,
                statusText: "Postura previa (en cola)", speedKmh: 0,
                equipmentAssigned: idx % nCarguio, stoppedByFault: false, estado: "cola", t: 0
            }};
            colas[v.equipmentAssigned].push(v);
            return v;
        }});
        function cargadorDe(i) {{ return i < palasList.length ? palasList[i] : cfList[i - palasList.length]; }}
        function tiempoCargaDe(i) {{
            const eq = cargadorDe(i);
            return (eq && eq.rend > 0) ? 60.0 * capEfMedia / eq.rend : timeLoading;
        }}
        function avanzarSimulacion(dt) {{
            // 1) Cada unidad de carguío libre (y sin falla) toma al primer camión de su cola
            for (let i = 0; i < nCarguio; i++) {{
                const eq = cargadorDe(i);
                if (cargando[i] === null && !(eq && eq.stoppedByFault)) {{
                    const k = colas[i].findIndex(v => !v.stoppedByFault);
                    if (k >= 0) {{
                        const v = colas[i].splice(k, 1)[0];
                        v.estado = "carga"; v.t = 0; v.x0 = v.x; v.y0 = v.y;
                        cargando[i] = v;
                    }}
                }}
            }}
            // 2) Avance de cada camión según su estado
            vehicles.forEach(v => {{
                if (v.stoppedByFault) return;
                const i = v.equipmentAssigned;
                const eq = cargadorDe(i);
                if (v.estado === "carga") {{
                    if (eq && eq.stoppedByFault) return;   // pala o cargador en falla: la carga se detiene
                    v.t += dt;
                    if (v.t >= tiempoCargaDe(i)) {{ v.estado = "ida"; v.t = 0; cargando[i] = null; }}
                }} else if (v.estado === "ida") {{
                    v.t += dt; if (v.t >= timeHaul) {{ v.estado = "descarga"; v.t = 0; }}
                }} else if (v.estado === "descarga") {{
                    v.t += dt;
                    if (v.t >= timeDumping) {{ v.estado = "retorno"; v.t = 0; v.vueltas++; totalVueltasCompletadas++; }}
                }} else if (v.estado === "retorno") {{
                    v.t += dt;
                }}
            }});
        }}
 
        let palaHitboxes = []; let cfHitboxes = [];
 
        function recalculateDynamicMF() {{
            let activeCaex = vehicles.filter(v => !v.stoppedByFault).length;
            let activePalas = palasList.filter(p => !p.stoppedByFault).length;
            let activeCF = cfList.filter(cf => !cf.stoppedByFault).length;
            let totalActiveLoading = Math.max(1, activePalas + activeCF);
 
            // SINCRONIZACIÓN ESTRICTA: Se usa exactamente la misma base matemática de Python adaptada a las fallas en vivo
            // Misma ecuación del motor: llegada de material de los camiones / rendimiento de las unidades de carguío activas
            let activeRate = palasList.filter(p => !p.stoppedByFault).reduce((s, p) => s + p.rend, 0)
                           + cfList.filter(cf => !cf.stoppedByFault).reduce((s, cf) => s + cf.rend, 0);
            let dynamicMF = activeRate > 0 ? (activeCaex * capEfMedia * 60.0 / {t_ciclo_base_exacto}) / activeRate : 0;
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
                    ctx.drawImage(imgCF, px - 12, py - 16, 56, 31);
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
            ctx.fillText("• " + destinoTurno.toUpperCase(), xFin + 45, yCentro - 6);
            ctx.fillText("(" + materialTurno + ")", xFin + 45, yCentro + 11);
 
            const ESPACIO_COLA = 46;
            function posCarga(i) {{ return {{ x: xInicio, y: yIda - 20 - i * 46 }}; }}
            function posCola(i, k) {{ return {{ x: xInicio + 22 + k * ESPACIO_COLA, y: yRetorno + i * 30 }}; }}
 
            // Ubicación inicial: todos los CAEX en la fila de espera (postura previa)
            vehicles.forEach(v => {{
                if (!v.ubicado) {{
                    const p = posCola(v.equipmentAssigned, Math.max(0, colas[v.equipmentAssigned].indexOf(v)));
                    v.x = p.x; v.y = p.y; v.ubicado = true;
                }}
            }});
            if (isTrackingActive) {{ avanzarSimulacion(simSpeed); }}
 
            // Un camión que vuelve vacío se detiene al llegar al último puesto de la fila de espera
            vehicles.forEach(v => {{
                if (v.estado !== "retorno" || v.stoppedByFault) return;
                const i = v.equipmentAssigned;
                const xRet = xFin - Math.min(1, v.t / timeReturn) * trackWidth;
                if (xRet <= posCola(i, colas[i].length).x || v.t >= timeReturn) {{
                    v.estado = "cola"; v.t = 0; colas[i].push(v);
                }}
            }});
 
            vehicles.forEach(v => {{
                const i = v.equipmentAssigned;
                const eq = cargadorDe(i);
                const eqNombre = eq ? eq.id : "Pala/CF";
                const pc = posCarga(i);
 
                if (v.estado === "cola") {{
                    const k = Math.max(0, colas[i].indexOf(v));
                    const p = posCola(i, k);
                    if (!v.ubicado) {{ v.x = p.x; v.y = p.y; v.ubicado = true; }}
                    v.x += (p.x - v.x) * 0.15; v.y += (p.y - v.y) * 0.15;   // avanza suavemente al puesto libre
                    v.isLoaded = false; v.isReturning = true; v.speedKmh = 0;
                    v.statusText = isTrackingActive ? ("En cola para " + eqNombre + " (puesto " + (k + 1) + ")")
                                                    : ("Postura previa en cola de " + eqNombre);
                }} else if (v.estado === "carga") {{
                    const tc = tiempoCargaDe(i);
                    const r = Math.min(1, v.t / (0.25 * tc));                  // viraje en el primer 25 % del carguío
                    const cx = xInicio - 45, cy = (v.y0 + pc.y) / 2, a = 1 - r;  // curva desde la fila hasta la pala
                    v.x = a * a * v.x0 + 2 * a * r * cx + r * r * pc.x;
                    v.y = a * a * v.y0 + 2 * a * r * cy + r * r * pc.y;
                    v.isLoaded = false; v.isReturning = r < 1; v.speedKmh = 0;
                    v.statusText = r < 1 ? ("Viraje hacia " + eqNombre) : ("En carga (" + eqNombre + ")");
                }} else if (v.estado === "ida") {{
                    const r = Math.min(1, v.t / timeHaul);
                    v.x = pc.x + r * trackWidth; v.y = pc.y + r * (yIda - pc.y);
                    v.isLoaded = true; v.isReturning = false; v.speedKmh = speedLoadedKmh;
                    v.statusText = "Acarreo " + materialTurno + " -> " + destinoTurno;
                }} else if (v.estado === "descarga") {{
                    v.x = xFin; v.y = (yIda + yRetorno) / 2;
                    v.isLoaded = false; v.isReturning = true; v.speedKmh = 0;
                    v.statusText = "En descarga (" + destinoTurno + ")";
                }} else {{
                    const r = Math.min(1, v.t / timeReturn);
                    v.x = xFin - r * trackWidth; v.y = yRetorno;
                    v.isLoaded = false; v.isReturning = true; v.speedKmh = speedEmptyKmh;
                    v.statusText = "Retorno vacío -> fila de " + eqNombre;
                }}
                if (v.stoppedByFault) {{ v.statusText = "🔴 DETENIDO POR FALLA / MANTENCIÓN"; v.speedKmh = 0; }}
 
                let imgToDraw = v.isLoaded ? imgCaexCargado : imgCaexVacio;
                ctx.save(); ctx.translate(v.x, v.y);
                if (v.isReturning) {{ ctx.scale(-1, 1); }}
                if (!v.stoppedByFault && imgToDraw.complete && imgToDraw.naturalWidth > 0 && imgToDraw.src.length > 50) {{
                    ctx.drawImage(imgToDraw, -20, -20, 40, 40);
                }} else {{ drawCaexTruck(0, 0, v.isLoaded, false, v.stoppedByFault); }}
                ctx.restore();
 
                ctx.font = "bold 10px Arial"; ctx.textAlign = "center";
                if (v.stoppedByFault) {{
                    ctx.fillStyle = "#DC2626"; ctx.fillText("🔴 " + v.id + " (FALLA)", v.x, v.y + 26);
                }} else if (v.estado === "cola") {{
                    ctx.fillStyle = "#B45309"; ctx.fillText(v.id, v.x, v.y + 26);   // etiqueta corta en la fila
                }} else {{
                    ctx.fillStyle = "#0F172A";
                    let speedLabel = v.speedKmh > 0 ? " [" + v.speedKmh.toFixed(0) + " km/h]" : " [0 km/h]";
                    ctx.fillText("CAEX " + v.id + " (" + v.capTon + "T) - " + v.vueltas + " vts" + speedLabel, v.x, v.y + 26);
                }}
            }});
 
            // Texto de la fila de espera
            for (let i = 0; i < nCarguio; i++) {{
                const enCola = colas[i].length;
                if (enCola > 0) {{
                    const p = posCola(i, 0);
                    ctx.font = "bold 10px Arial"; ctx.textAlign = "left"; ctx.fillStyle = "#B45309";
                    ctx.fillText("FILA DE ESPERA " + (cargadorDe(i) ? cargadorDe(i).id : "") + ": " + enCola + " CAEX", p.x - 20, p.y + 40);
                }}
            }}
 
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
                                            '• Rendimiento: ' + pData.rend + ' Ton/h<br>' +
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
# 16. REPORTE, CONCILIACIÓN Y CIERRE DE TURNO (PLAN VS. REAL)
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
        "Material Transportado": material_turno, "Destino": destino_turno,
        "Distancia (km)": distancia_acarreo_km, "Perfil de Rampa": perfil_rampa_sel,
        "Responsable Agendamiento": st.session_state.get("usuario_activo", "Sin usuario"),
        "CAEX Agendados": n_caex_activos, "Unidades de Carguío": n_carguio_real, "N° CAEX Prescrito": n_prescrito,
        "Disponibilidad Física (%)": round(disponibilidad_fisica_val, 1),
        "Factor de Llenado (%)": round(fl_valor * 100, 0),
        "Tiempo de Carguío (min)": round(t_carguio_min, 2),
        "Espera en Cola (min/ciclo)": round(tiempo_cola_promedio, 2),
        "Merma por Traslado (%)": round(merma_pct_real, 1),
        "Match Factor Calculado": round(match_factor, 2),
        "Semáforo": semaforo_actual,
        "Toneladas Cargadas (Ton)": round(tonelaje_cargado, 0),
        "Toneladas Efectivas Entregadas (Ton)": round(tonelaje_efectivo, 0),
        "Consumo Diésel Total (Lts)": round(litros_diesel_turno, 0),
        "Consumo Específico (Lts/Ton)": round(consumo_especifico_lts_ton, 3),
        "Huella CO2 Operativa (kg CO2/Ton)": round(co2_por_ton, 2),
        "Costo Equipos Turno (USD)": round(costo_opex_total_turno, 2),
        "Costo Diésel Turno (USD)": round(costo_diesel_turno, 2),
        "Costo Total (USD/Ton Efectiva)": round(costo_total_ton, 2),
        "Beneficio Neto Proyectado (USD)": round(beneficio_neto_usd, 2),
        "Despacho según Prescripción": prescripcion_aceptada_val,
    }])
 
    csv_data = df_export.to_csv(index=False, sep=";").encode("utf-8-sig")
    st.download_button(
        label="📥 Descargar Ficha Pre-Turno (Excel / CSV)", data=csv_data,
        file_name=f"Ficha_Agendamiento_{num_agendamiento}.csv", mime="text/csv", use_container_width=True,
    )
 
st.markdown("---")
st.subheader("🔄 Conciliación y Cierre de Turno (Plan vs. Real)")
 
conn_conc = sqlite3.connect(DB_FILE)
df_lista_ag = pd.read_sql_query(
    "SELECT num_agendamiento, fecha_registro, turno, jefe_turno, COALESCE(material, 'Mineral') AS material, "
    "ton_movidas, ton_efectivas, consumo_diesel_lts, opex_total_usd, costo_total_ton_usd, match_factor, "
    "prescripcion_aceptada FROM historico_agendamientos ORDER BY id DESC",
    conn_conc,
)
df_cierres = pd.read_sql_query("SELECT * FROM cierres_turno", conn_conc)
conn_conc.close()
 
if not df_lista_ag.empty:
    opciones_ag = df_lista_ag.apply(lambda row: f"{row['num_agendamiento']} | {row['fecha_registro']} | {row['material']} | {row['turno']} | Resp: {row['jefe_turno']}", axis=1).tolist()
    ag_seleccionado_str = st.selectbox("🔍 Seleccionar Agendamiento Guardado para Cierre:", opciones_ag)
    num_ag_selected = ag_seleccionado_str.split(" | ")[0]
 
    datos_plan = df_lista_ag[df_lista_ag["num_agendamiento"] == num_ag_selected].iloc[0]
    ton_ef_plan = float(datos_plan["ton_efectivas"] or 0)
    ton_plan = ton_ef_plan if ton_ef_plan > 0 else float(datos_plan["ton_movidas"] or 0)
    diesel_plan = float(datos_plan["consumo_diesel_lts"] or 0)
    mf_plan = float(datos_plan["match_factor"] or 0)
    cierre_previo = df_cierres[df_cierres["num_agendamiento"] == num_ag_selected]
 
    st.info(f"📋 **Datos Planificados en {num_ag_selected} ({datos_plan['material']}):** Toneladas Efectivas Proyectadas = **{fmt_num(ton_plan, 0)} Ton** | "
            f"Diésel Presupuestado = **{fmt_num(diesel_plan, 0)} Lts** | Match Factor = **{fmt_num(mf_plan, 2)}**"
            + (" | ✅ Cierre ya registrado" if not cierre_previo.empty else ""))
 
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("#### 📥 Ingreso de Datos Reales de Terreno (Post-Turno)")
        valor_ton_inicial = float(cierre_previo["ton_reales"].iloc[0]) if not cierre_previo.empty else ton_plan
        valor_diesel_inicial = float(cierre_previo["diesel_real_lts"].iloc[0]) if not cierre_previo.empty else diesel_plan
        st.markdown("**Toneladas Reales Extraídas (Ton):**")
        ton_reales = st.number_input("Toneladas reales", value=valor_ton_inicial, step=500.0, min_value=0.0,
                                     key=f"input_ton_reales_{num_ag_selected}", label_visibility="collapsed")
 
        st.markdown("**Consumo Diésel Real (Litros):**")
        diesel_real = st.number_input("Diésel real", value=valor_diesel_inicial, step=200.0, min_value=0.0,
                                      key=f"input_diesel_reales_{num_ag_selected}", label_visibility="collapsed")
 
        opciones_causales = ["Falla Mecánica de CAEX", "Falla de Pala / Cargador", "Inasistencia de Operador", "Lluvia / Condición Climática", "Voladura / Tronadura Atrasada", "Atasco / Detención en Chancado", "Otra"]
        st.markdown("**Causas de Desviación / Imprevistos en Turno (Selección Múltiple):**")
        causas_seleccionadas = st.multiselect("Causas", options=opciones_causales, default=[], placeholder="Elija opciones",
                                              key=f"causas_{num_ag_selected}", label_visibility="collapsed")
 
        st.markdown("**Observaciones / Bitácora de Terreno:**")
        observaciones_turno = st.text_input("Observaciones", value="", placeholder="Ej: CA321 fuera a las 11:00 hrs; PA622 detenida 45 min...",
                                            key=f"obs_{num_ag_selected}", label_visibility="collapsed")
 
    with col_c2:
        st.markdown("#### 📊 Indicadores de Efectividad Operativa")
        adherencia_plan = (ton_reales / ton_plan * 100) if ton_plan > 0 else 0.0
        # Costo real = costo de equipos guardado en ESE agendamiento + diésel real del turno
        costo_real_usd = float(datos_plan["opex_total_usd"] or 0) + diesel_real * precio_diesel
        costo_real_ton = (costo_real_usd / ton_reales) if ton_reales > 0 else 0.0
 
        if adherencia_plan >= 95.0:
            st.markdown(f'<p class="adh-green-large">Adherencia al Plan de Mina: {fmt_num(adherencia_plan, 1)}%</p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<p class="adh-red-large">Adherencia al Plan de Mina: {fmt_num(adherencia_plan, 1)}%</p>', unsafe_allow_html=True)
        st.progress(min(adherencia_plan / 100.0, 1.0))
 
        col_adop1, col_adop2 = st.columns(2)
        with col_adop1:
            st.metric("Costo Real", f"${fmt_num(costo_real_ton, 2)} USD/Ton",
                      delta=f"Plan ${fmt_num(datos_plan['costo_total_ton_usd'], 2)}", delta_color="off")
        with col_adop2:
            tasa_adopcion_val = df_lista_ag["prescripcion_aceptada"].fillna(0).mean() * 100
            st.metric("Tasa de Adopción Prescriptiva", f"{tasa_adopcion_val:.1f}%", delta="Declarada por el Jefe de Turno", delta_color="off")
 
        texto_causas = ", ".join(causas_seleccionadas) if causas_seleccionadas else "Sin imprevistos registrados"
        if adherencia_plan >= 98.0:
            st.success(f"🎯 **AGENDAMIENTO EXITOSO:** Cumplimiento del {fmt_num(adherencia_plan, 1)}% de la meta proyectada ({num_ag_selected}).")
        elif adherencia_plan >= 85.0:
            st.warning(f"⚠️ **CUMPLIMIENTO PARCIAL ({fmt_num(adherencia_plan, 1)}%):** Desviación menor atribuida a: {texto_causas}.")
        else:
            st.error(f"🚨 **DESVIACIÓN CRÍTICA ({fmt_num(adherencia_plan, 1)}%):** Impacto severo por eventos múltiples ({texto_causas}). Costo Real: ${fmt_num(costo_real_ton, 2)} USD/Ton.")
 
        if st.button("💾 GUARDAR CIERRE DE TURNO", use_container_width=True):
            guardar_cierre_db({
                "num_agendamiento": num_ag_selected, "fecha_cierre": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "responsable": st.session_state.get("usuario_activo", "Sin usuario"),
                "ton_reales": ton_reales, "diesel_real_lts": diesel_real, "costo_real_usd": costo_real_usd,
                "costo_real_ton_usd": costo_real_ton, "adherencia_pct": adherencia_plan,
                "causas": "; ".join(causas_seleccionadas), "observaciones": observaciones_turno,
            })
            st.success(f"✅ Cierre de {num_ag_selected} guardado.")
            st.rerun()
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
    if st.session_state.get("rol_activo") == "Administrador":
        confirmar_borrado = st.checkbox("Confirmo borrar todo el histórico")
        if st.button("🗑 Borrar Histórico (Admin)", type="primary", use_container_width=True, disabled=not confirmar_borrado):
            borrar_historico_db()
            st.success("Histórico eliminado correctamente.")
            st.rerun()
 
conn = sqlite3.connect(DB_FILE)
df_hist = pd.read_sql_query("SELECT * FROM historico_agendamientos ORDER BY id DESC", conn)
df_reales = pd.read_sql_query("SELECT num_agendamiento, ton_reales, adherencia_pct, costo_real_ton_usd FROM cierres_turno", conn)
conn.close()
 
if not df_hist.empty:
    df_hist["material"] = df_hist["material"].fillna("Mineral")
    df_hist = df_hist.merge(df_reales, on="num_agendamiento", how="left")
    rol_actual = st.session_state.get("rol_activo")
    usuario_actual = st.session_state.get("usuario_activo")
 
    if rol_actual in ["Administrador", "Gerente Operaciones / Evaluador"]:
        st.markdown("<h3 style='text-align: center;'>[EXCLUSIVO GERENCIA] Panel de control y Auditoría por períodos</h3>", unsafe_allow_html=True)
 
        c_f1, c_f2, c_f3 = st.columns(3)
        with c_f1:
            supervisores_lista = ["Todos"] + list(df_hist["jefe_turno"].dropna().unique())
            sup_filtro = st.selectbox("👤 Seleccionar Jefe de Mina:", supervisores_lista)
        with c_f2:
            periodo_filtro = st.selectbox("📅 Seleccionar Período de Consolidación:", ["Semanal (Ciclo 7x7)", "Mensual", "Anual", "Histórico Completo"])
        with c_f3:
            material_filtro = st.selectbox("⛏️ Material:", ["Mineral", "Estéril", "Todos"])
 
        df_gerencia = df_hist.copy()
        if sup_filtro != "Todos":
            df_gerencia = df_gerencia[df_gerencia["jefe_turno"] == sup_filtro]
        if material_filtro != "Todos":
            df_gerencia = df_gerencia[df_gerencia["material"] == material_filtro]
        fechas = pd.to_datetime(df_gerencia["fecha_registro"], format="%d/%m/%Y", errors="coerce")
        if periodo_filtro == "Semanal (Ciclo 7x7)":
            df_gerencia = df_gerencia.head(7)
        elif periodo_filtro == "Mensual":
            df_gerencia = df_gerencia[fechas >= pd.Timestamp(now_dt.date()) - pd.Timedelta(days=30)]
        elif periodo_filtro == "Anual":
            df_gerencia = df_gerencia[fechas >= pd.Timestamp(now_dt.date()) - pd.Timedelta(days=365)]
 
        # Tabla gerencial con el mismo formato de la Tabla Control Estados Equipos Mina
        COLUMNAS_GERENCIA = ["num_agendamiento", "fecha_registro", "hora_registro", "turno", "jefe_turno", "material", "destino",
                             "distancia_km", "n_caex", "n_prescrito", "match_factor", "ton_efectivas", "ton_reales", "adherencia_pct",
                             "consumo_diesel_lts", "costo_total_ton_usd", "costo_real_ton_usd", "beneficio_neto_usd",
                             "disponibilidad_fisica", "en_banda_verde", "prescripcion_aceptada"]
        ETIQUETAS_GERENCIA = {
            "num_agendamiento": "N° Agendamiento", "fecha_registro": "Fecha", "hora_registro": "Hora", "turno": "Turno",
            "jefe_turno": "Jefe de Turno", "material": "Material", "destino": "Destino", "distancia_km": "Distancia (km)",
            "n_caex": "CAEX", "n_prescrito": "CAEX Prescrito", "match_factor": "Match Factor",
            "ton_efectivas": "Ton Plan", "ton_reales": "Ton Real (Cierre)", "adherencia_pct": "Adherencia (%)",
            "consumo_diesel_lts": "Diésel Plan (Lts)", "costo_total_ton_usd": "Costo Plan (USD/Ton)",
            "costo_real_ton_usd": "Costo Real (USD/Ton)", "beneficio_neto_usd": "Beneficio Neto (USD)",
            "disponibilidad_fisica": "Disp. Física (%)", "en_banda_verde": "Banda Verde",
            "prescripcion_aceptada": "Despacho según Prescripción",
        }
        DECIMALES_GERENCIA = {"distancia_km": 1, "n_caex": 0, "n_prescrito": 0, "match_factor": 2, "ton_efectivas": 0,
                              "ton_reales": 0, "adherencia_pct": 1, "consumo_diesel_lts": 0, "costo_total_ton_usd": 2,
                              "costo_real_ton_usd": 2, "beneficio_neto_usd": 2, "disponibilidad_fisica": 1}
 
        def _celda_gerencia(col, valor):
            if valor is None or (isinstance(valor, float) and pd.isna(valor)):
                return "—"
            if col in ("en_banda_verde", "prescripcion_aceptada"):
                return "🟢 Sí" if int(valor) == 1 else "🔴 No"
            if col in DECIMALES_GERENCIA:
                return fmt_num(valor, DECIMALES_GERENCIA[col])
            return str(valor)
 
        def _fondo_mf(valor):
            try:
                return {"Verde": "#BBF7D0", "Amarillo": "#FDE68A", "Rojo": "#FECACA"}[clasificar_semaforo(float(valor))]
            except (TypeError, ValueError):
                return ""
 
        cols_ger = [c for c in COLUMNAS_GERENCIA if c in df_gerencia.columns]
        encabezado_ger = "".join(f"<th>{html.escape(ETIQUETAS_GERENCIA.get(c, c))}</th>" for c in cols_ger)
        cuerpo_ger = ""
        for _, fila_g in df_gerencia.iterrows():
            celdas_g = ""
            for c in cols_ger:
                estilo_g = f' style="background:{_fondo_mf(fila_g[c])};"' if c == "match_factor" else ""
                celdas_g += f"<td{estilo_g}>{html.escape(_celda_gerencia(c, fila_g[c]))}</td>"
            cuerpo_ger += f"<tr>{celdas_g}</tr>"
 
        st.markdown(f"""
            <style>
            .tabla-gerencia-wrap {{ overflow: auto; max-height: 460px; border-radius: 10px; border: 2px solid #0F172A; margin-bottom: 10px; }}
            .tabla-gerencia {{ width: 100%; border-collapse: collapse; font-weight: 800 !important; font-size: 12.5px; color: #0F172A; background: #FFFFFF; }}
            .tabla-gerencia th {{ background: #0F172A; color: #FFFFFF; font-weight: 900; text-transform: uppercase; font-size: 11.5px;
                                  letter-spacing: 0.3px; padding: 8px 6px; border-bottom: 3px solid #F59E0B; border-right: 1px solid #334155;
                                  white-space: normal; min-width: 78px; line-height: 1.25; text-align: center; vertical-align: middle;
                                  position: sticky; top: 0; z-index: 2; }}
            .tabla-gerencia td {{ padding: 7px 8px; border-bottom: 1px solid #CBD5E1; border-right: 1px solid #E2E8F0;
                                  white-space: nowrap; text-align: center; font-weight: 800; }}
            .tabla-gerencia tr:nth-child(even) td {{ background-color: #F1F5F9; }}
            .tabla-gerencia tr:hover td {{ background-color: #FEF3C7; }}
            </style>
            <div class="tabla-gerencia-wrap"><table class="tabla-gerencia">
                <thead><tr>{encabezado_ger}</tr></thead><tbody>{cuerpo_ger}</tbody>
            </table></div>
        """, unsafe_allow_html=True)
 
        with st.expander(f"📈 Evaluación de Rendimiento Gerencial ({periodo_filtro}) — Supervisor: {sup_filtro} — Material: {material_filtro}", expanded=True):
            if df_gerencia.empty:
                st.info("No hay agendamientos para los filtros seleccionados.")
            else:
                # Solo datos registrados: las toneladas reales provienen de los cierres de turno guardados
                df_graf = df_gerencia.iloc[::-1].reset_index(drop=True)
                etiquetas = [f"{r['num_agendamiento']} ({r['fecha_registro']})" for _, r in df_graf.iterrows()]
                ton_plan_g = df_graf["ton_efectivas"].fillna(0).values
                ton_real_g = df_graf["ton_reales"].values
                mf_valores = df_graf["match_factor"].fillna(0).values
 
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_trace(go.Bar(x=etiquetas, y=ton_plan_g, name="Toneladas Propuestas (Plan)", marker_color="#0284C7",
                                     text=[f"{fmt_num(v, 0)} T" for v in ton_plan_g], textposition="auto"), secondary_y=False)
                fig.add_trace(go.Bar(x=etiquetas, y=ton_real_g, name="Toneladas Reales (Cierre de Turno)", marker_color="#10B981",
                                     text=[f"{fmt_num(v, 0)} T" if v == v else "sin cierre" for v in ton_real_g], textposition="auto"), secondary_y=False)
                fig.add_trace(go.Scatter(x=etiquetas, y=mf_valores, name="Match Factor", mode="lines+markers+text",
                                         line=dict(color="#F59E0B", width=3), marker=dict(size=9, color="#F59E0B"),
                                         text=[f"MF: {v:.2f}" for v in mf_valores], textposition="top center"), secondary_y=True)
                fig.update_layout(title_text="<b>Cumplimiento Operativo y Match Factor por Agendamiento</b>", barmode="group",
                                  template="plotly_white", height=450,
                                  legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(l=20, r=20, t=60, b=20))
                fig.update_xaxes(title_text="<b>Agendamiento</b>")
                fig.update_yaxes(title_text="<b>Toneladas (Ton)</b>", secondary_y=False)
                fig.update_yaxes(title_text="<b>Match Factor (MF)</b>", secondary_y=True, range=[0, 1.6])
                st.plotly_chart(fig, use_container_width=True)
 
                con_cierre = df_gerencia.dropna(subset=["ton_reales"])
                tot_proyectado = df_gerencia["ton_efectivas"].fillna(0).sum()
                cumplimiento_ciclo = (con_cierre["ton_reales"].sum() / con_cierre["ton_efectivas"].sum() * 100
                                      if not con_cierre.empty and con_cierre["ton_efectivas"].sum() > 0 else None)
                m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
                m_col1.metric("Ton Proyectadas", f"{fmt_num(tot_proyectado, 0)} Ton")
                m_col2.metric("Cumplimiento Real", f"{fmt_num(cumplimiento_ciclo, 1)}%" if cumplimiento_ciclo is not None else "Sin cierres",
                              delta=f"{len(con_cierre)}/{len(df_gerencia)} turnos cerrados", delta_color="off")
                m_col3.metric("Match Factor Promedio", f"{fmt_num(df_gerencia['match_factor'].mean(), 2)}")
                m_col4.metric("En Banda Verde", f"{fmt_num(df_gerencia['en_banda_verde'].fillna(0).mean() * 100, 1)}%")
                m_col5.metric("Costo Promedio Plan", f"${fmt_num(df_gerencia['costo_total_ton_usd'].mean(), 2)} USD/Ton")
    else:
        st.markdown(f"### 👤 **Control Operativo de Turno Actual — Supervisor:** `{usuario_actual}`")
        df_turno_hoy = df_hist[(df_hist["jefe_turno"] == usuario_actual) & (df_hist["fecha_registro"] == fecha_str)]
 
        if not df_turno_hoy.empty:
            st.dataframe(df_turno_hoy[["num_agendamiento", "hora_registro", "turno", "material", "destino", "n_caex",
                                       "match_factor", "ton_efectivas", "costo_total_ton_usd", "ton_reales", "adherencia_pct"]],
                         use_container_width=True, hide_index=True)
            st.success("📌 Mostrando únicamente los agendamientos de la jornada actual.")
 
