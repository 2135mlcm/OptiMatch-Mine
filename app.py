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
 
# Ícono propio de cargador frontal (dibujo vectorial incluido en el código; no requiere archivo de imagen)
ICONO_CARGADOR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 134 84" width="130" height="84">
  <rect x="17" y="18" width="4" height="12" rx="1" fill="#334155"/>
  <rect x="6" y="30" width="50" height="24" rx="4" fill="#F59E0B" stroke="#0F172A" stroke-width="1.8"/>
  <rect x="2" y="36" width="8" height="16" rx="2" fill="#D97706" stroke="#0F172A" stroke-width="1.5"/>
  <line x1="14" y1="36" x2="14" y2="48" stroke="#0F172A" stroke-width="1.2"/>
  <line x1="20" y1="36" x2="20" y2="48" stroke="#0F172A" stroke-width="1.2"/>
  <line x1="26" y1="36" x2="26" y2="48" stroke="#0F172A" stroke-width="1.2"/>
  <path d="M40 30 L42 8 L66 8 L68 30 Z" fill="#F59E0B" stroke="#0F172A" stroke-width="1.8"/>
  <path d="M45 12 L63 12 L64.5 27 L44 27 Z" fill="#BAE6FD" stroke="#0F172A" stroke-width="1.2"/>
  <rect x="39" y="5" width="30" height="4" rx="1.5" fill="#0F172A"/>
  <path d="M56 34 L98 40 L98 54 L56 54 Z" fill="#F59E0B" stroke="#0F172A" stroke-width="1.8"/>
  <path d="M66 34 L72 30 L110 46 L106 53 Z" fill="#FBBF24" stroke="#0F172A" stroke-width="1.8"/>
  <line x1="74" y1="47" x2="104" y2="43" stroke="#64748B" stroke-width="3.5" stroke-linecap="round"/>
  <path d="M106 32 C96 42 96 60 106 70 L128 70 L127 63 L113 62 C107 56 107 44 113 36 Z" fill="#475569" stroke="#0F172A" stroke-width="1.8"/>
  <path d="M128 70 L131 68 L128 66" fill="none" stroke="#0F172A" stroke-width="1.5"/>
  <circle cx="31" cy="64" r="16" fill="#1E293B"/>
  <circle cx="31" cy="64" r="7" fill="#94A3B8" stroke="#0F172A" stroke-width="1.5"/>
  <circle cx="84" cy="64" r="16" fill="#1E293B"/>
  <circle cx="84" cy="64" r="7" fill="#94A3B8" stroke="#0F172A" stroke-width="1.5"/>
</svg>"""
ICONO_CARGADOR_URI = "data:image/svg+xml;base64," + base64.b64encode(ICONO_CARGADOR_SVG.encode("utf-8")).decode()
 
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
st.sidebar.markdown('<span class="selector-label-centered">MATERIAL A TRANSPORTAR EN ESTE AGENDAMIENTO</span>', unsafe_allow_html=True)
material_turno = st.sidebar.radio("Material del agendamiento", ["Mineral", "Estéril"], horizontal=True,
                                  label_visibility="collapsed", key="radio_material_turno",
                                  help="Cada agendamiento es un circuito de un solo material. La meta del otro material queda en 0.")
if material_turno == "Mineral":
    destino_turno = st.sidebar.selectbox("Destino del mineral", ["Chancador primario", "Pila de acopio (stockpile)"], key="select_destino_mineral")
    target_mineral_num = st.sidebar.number_input("Objetivo Mineral (Ton)", value=18000, step=1000, min_value=0, key="meta_mineral")
    target_esteril_num = 0
else:
    destino_turno = "Botadero de estéril"
    st.sidebar.markdown(f'<div class="auto-box">Destino: {destino_turno}</div>', unsafe_allow_html=True)
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
                    ctx.drawImage(imgCF, px - 8, py - 16, 50, 31);
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
                        v.isLoaded = true; v.isReturning = false; v.statusText = "Acarreo " + materialTurno + " -> " + destinoTurno; v.speedKmh = speedLoadedKmh;
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
 
