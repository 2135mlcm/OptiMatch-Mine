import os
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime

# ---------------------------------------------------------
# INICIALIZACIÓN DE LA BASE DE DATOS SQLITE (optimatch.db)
# ---------------------------------------------------------
DB_FILE = "optimatch.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. Tabla de Usuarios Autorizados
    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nombre_completo TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    """)
    
    # Actualizar nómina de usuarios con mcepeda como Administrador (admin2026)
    c.execute("DELETE FROM usuarios")
    
    usuarios_oficiales = [
        ("mcepeda", "admin2026", "Mauricio L. Cepeda Mondaca", "Administrador"),
        ("avidela", "mina2026", "Andy Videla Obregón", "Alumno"),
        ("ddaines", "mina2026", "Daniel Daines Araya", "Alumno"),
        ("cnikulin", "uah2026", "Dr. Christopher Nikulin", "Profesor Evaluador"),
        ("cperez", "uah2026", "Dr. Camilo Pérez", "Profesor Evaluador")
    ]
    c.executemany("INSERT INTO usuarios (username, password, nombre_completo, rol) VALUES (?, ?, ?, ?)", usuarios_oficiales)
    conn.commit()

    # 2. Tabla de Histórico de Agendamientos
    c.execute("""
        CREATE TABLE IF NOT EXISTS historico_agendamientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            num_agendamiento TEXT,
            fecha_registro TEXT,
            hora_registro TEXT,
            faena TEXT,
            turno TEXT,
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
    conn.close()

# Ejecutar inicialización de BD
init_db()

# Función para validar credenciales de ingreso
def validar_usuario(usr, pwd):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, nombre_completo, rol FROM usuarios WHERE username = ? AND password = ?", (usr, pwd))
    res = c.fetchone()
    conn.close()
    return res

# Función para guardar agendamiento en la BD
def guardar_agendamiento_db(num_ag, fecha, hora, faena, turno, jefe, ton, lts_diesel, costo_diesel, opex, costo_ton, beneficio, mf):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO historico_agendamientos (
            num_agendamiento, fecha_registro, hora_registro, faena, turno, jefe_turno,
            ton_movidas, consumo_diesel_lts, costo_diesel_usd, opex_total_usd,
            costo_ton_usd, beneficio_neto_usd, match_factor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (num_ag, fecha, hora, faena, turno, jefe, ton, lts_diesel, costo_diesel, opex, costo_ton, beneficio, mf))
    conn.commit()
    conn.close()

# Función exclusiva de Administrador para borrar histórico
def borrar_historico_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM historico_agendamientos")
    conn.commit()
    conn.close()

# Función para dar formato a números con punto en miles
def fmt_num(val, dec=0):
    if dec == 0:
        return f"{val:,.0f}".replace(",", ".")
    else:
        formatted = f"{val:,.{dec}f}"
        main_part, dec_part = formatted.split(".")
        main_part = main_part.replace(",", ".")
        return f"{main_part},{dec_part}"

# ---------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ---------------------------------------------------------
st.set_page_config(
    page_title="OptiMatch Mine - Control de Flota",
    page_icon="⛏️",
    layout="wide"
)

st.markdown("""
    <style>
    /* Fondo Blanco General */
    .stApp {
        background-color: #FFFFFF !important;
        color: #0F172A !important;
    }
    
    h1, h2, h3, h4, h5, h6, p, label, span, div {
        color: #0F172A !important;
    }

    /* Carátula y Encabezado */
    .header-container {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        width: 100%;
        margin-top: 10px;
        margin-bottom: 20px;
        text-align: center;
    }

    .title-box {
        background-color: #F8FAFC;
        padding: 20px 40px;
        border-radius: 12px;
        border: 2px solid #D97706;
        box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.08);
        text-align: center;
        width: fit-content;
        margin-top: 15px;
    }

    .centered-title {
        text-align: center !important;
        width: 100% !important;
        margin-top: 20px !important;
        margin-bottom: 15px !important;
    }

    /* Estilos de Barra Lateral */
    section[data-testid="stSidebar"] {
        background-color: #1E293B !important;
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
        border: 1px solid #38BDF8 !important;
        text-align: center !important;
    }

    /* FORZADO DE TEXTO NEGRO Y NEGRITA EN EL BOTÓN DE CIERRE SIDEBAR */
    section[data-testid="stSidebar"] button,
    section[data-testid="stSidebar"] button *,
    section[data-testid="stSidebar"] button p,
    section[data-testid="stSidebar"] button span {
        background-color: #F59E0B !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        font-weight: 900 !important;
        font-size: 15px !important;
    }

    /* Tablas data editor */
    div[data-testid="stDataFrame"] {
        background-color: #F1F5F9 !important;
        border: 2px solid #CBD5E1 !important;
        border-radius: 10px;
    }

    /* Tarjetas de Métricas Generales */
    div[data-testid="stMetricValue"] {
        color: #0284C7 !important;
        font-size: 20px !important;
        font-weight: bold !important;
        white-space: nowrap !important;
    }

    /* Match Factor Grande */
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

    /* Destacados en rojo para Evaluación Económica */
    .highlight-red-large {
        color: #DC2626 !important;
        font-size: 19px !important;
        font-weight: 800 !important;
        margin-bottom: 8px !important;
    }
    </style>
""", unsafe_allow_html=True)

LOGO_PATH = "Logo_OptiMatch.png"

# ---------------------------------------------------------
# 1. AUTENTICACIÓN DE USUARIOS VÍA BASE DE DATOS
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
            st.markdown("""
                <div style="text-align: center; background-color: #1E293B; padding: 20px; border-radius: 15px; border: 2px solid #38BDF8;">
                    <h1 style="color: #38BDF8; font-size: 38px; margin-bottom: 0px;">⛏️ OptiMatch Mine</h1>
                    <h3 style="color: #F8FAFC; margin-top: 5px;">Control de Flota y Agendamiento Pre-Turno</h3>
                </div>
            """, unsafe_allow_html=True)
            
        st.markdown("<p style='text-align: center; font-weight: 800; font-size: 15px;'>Acceso Restringido por Perfil | Universidad Alberto Hurtado</p>", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        
        with st.form("login_form", clear_on_submit=True):
            st.markdown('<p style="font-weight: 800; font-size: 16px;">Nombre de Usuario (ej: mcepeda, cnikulin):</p>', unsafe_allow_html=True)
            usuario = st.text_input("", value="", placeholder="Ingresa tu usuario", key="input_usr")
            
            st.markdown('<p style="font-weight: 800; font-size: 16px;">Contraseña de Acceso:</p>', unsafe_allow_html=True)
            clave = st.text_input("", type="password", value="", placeholder="Ingresa tu contraseña", key="input_pwd")
            
            st.markdown("<br>", unsafe_allow_html=True)
            boton_ingresar = st.form_submit_button("🔑 INGRESAR A LA PLATAFORMA", use_container_width=True)

        if boton_ingresar:
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
# LOGO Y ENCABEZADO CENTRADO EN LA CARÁTULA
# ---------------------------------------------------------
st.markdown('<div class="header-container">', unsafe_allow_html=True)
if os.path.exists(LOGO_PATH):
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        st.image(LOGO_PATH, width=320)

st.markdown("""
    <div style="display: flex; justify-content: center; width: 100%;">
        <div class="title-box">
            <h1 style="color: #0F172A; margin: 0; font-size: 28px; font-weight: 800;">OptiMatch Mine — Control de Flota</h1>
            <p style="color: #0284C7; margin: 6px 0 0 0; font-size: 14px; font-weight: 800; letter-spacing: 0.5px;">
                SISTEMA PRESCRIPTIVO DE DECISIONES PRE-TURNO PARA LA MEDIANA MINERÍA
            </p>
            <p style="color: #475569; margin: 2px 0 0 0; font-size: 12px; font-weight: 600;">
                Optimización del Match Carguío-Transporte & Control de Rentabilidad OPEX | Universidad Alberto Hurtado
            </p>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("---")

# ---------------------------------------------------------
# BARRA LATERAL (SIDEBAR)
# ---------------------------------------------------------
st.sidebar.header("🏢 Registro Operativo Mina")
nombre_mina = st.sidebar.text_input("Nombre de la Mina / Faena", value="Mina Franke - Calama")
num_agendamiento = st.sidebar.text_input("N° de Agendamiento", value="AGN-2026-089")

st.sidebar.markdown("---")
st.sidebar.header("🗓️ Configuración del Agendamiento")

now_dt = st.session_state.get("hora_ingreso", datetime.now())
fecha_agendamiento = st.sidebar.date_input("Fecha de Agendamiento", now_dt.date())
hora_agendamiento = st.sidebar.time_input("Hora de Agendamiento (Automática)", now_dt.time())

# RECUADRO DEL USUARIO RESPONSABLE (VALIDADO EN BD)
st.sidebar.markdown(f"""
    <div style="background-color: #020617; padding: 12px; border-radius: 8px; border: 2px solid #38BDF8; margin-top: 10px; margin-bottom: 10px; text-align: center;">
        <span style="color: #38BDF8 !important; font-size: 11px; font-weight: 800; display: block;">USUARIO RESPONSABLE</span>
        <span style="color: #FFFFFF !important; font-size: 17px; font-weight: 900; display: block; margin-top: 4px;">👤 {st.session_state.get('usuario_activo', 'Mauricio L. Cepeda Mondaca')}</span>
        <span style="color: #38BDF8 !important; font-size: 11px; font-weight: 800; display: block; margin-top: 2px;">Perfil: {st.session_state.get('rol_activo', 'Administrador')}</span>
    </div>
""", unsafe_allow_html=True)

turno_seleccionado = st.sidebar.selectbox("Turno Operativo", ["Turno 1 (Día / 08:00 - 18:00)", "Turno 2 (Noche / 20:00 - 06:00)"])
horas_turno = st.sidebar.number_input("Horas Efectivas Turno", value=10.0, step=0.5)

st.sidebar.markdown("---")
st.sidebar.header("⛏️ Plan de Producción")

target_mineral_num = st.sidebar.number_input("Objetivo Mineral (Ton)", value=18000, step=1000)
target_esteril_num = st.sidebar.number_input("Objetivo Estéril (Ton)", value=12000, step=1000)

st.sidebar.markdown("---")
st.sidebar.header("⛽ Insumos y Precios")
precio_diesel = st.sidebar.number_input("Precio Diésel (USD / Litro)", value=1.15, step=0.05)
factor_yodo = st.sidebar.number_input("Ton Caliche / kg Yodo", value=3.91, step=0.01)
valor_ton_usd = st.sidebar.number_input("USD / Ton Caliche", value=9.079, step=0.001)

st.sidebar.markdown("---")

# MÓDULO EXCLUSIVO DE ADMINISTRACIÓN (SOLO mcepeda)
if st.session_state.get("user_id") == "mcepeda":
    with st.sidebar.expander("⚙️ PANEL ADMINISTRADOR (mcepeda)"):
        st.caption("Control de Usuarios en BD")
        conn = sqlite3.connect(DB_FILE)
        df_usr = pd.read_sql_query("SELECT id, username, password, nombre_completo, rol FROM usuarios", conn)
        conn.close()
        st.dataframe(df_usr, hide_index=True)

# ---------------------------------------------------------
# INICIALIZACIÓN DE INVENTARIO DE FLOTA
# ---------------------------------------------------------
if "palas_df" not in st.session_state:
    st.session_state.palas_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "PA622", "Modelo": "R9200", "Operador": "Carlos Araya", "Rend_TonH": 1424, "Consumo_LtsH": 120.0, "Costo_USDH": 441.44},
        {"Item": 2, "Agendar": True, "Estado": "🟢 Disponible", "ID": "PA624", "Modelo": "R9300", "Operador": "Roberto Gómez", "Rend_TonH": 1854, "Consumo_LtsH": 145.0, "Costo_USDH": 444.96},
        {"Item": 3, "Agendar": False, "Estado": "🔴 Falla Mecánica", "ID": "PA626", "Modelo": "R9300", "Operador": "Sin Asignar", "Rend_TonH": 1854, "Consumo_LtsH": 145.0, "Costo_USDH": 444.96},
    ])

if "cf_df" not in st.session_state:
    st.session_state.cf_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CF437", "Modelo": "WA900", "Operador": "Juan Pérez", "Rend_TonH": 685, "Consumo_LtsH": 75.0, "Costo_USDH": 342.50},
        {"Item": 2, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CF440", "Modelo": "CAT 994K", "Operador": "Mario Silva", "Rend_TonH": 820, "Consumo_LtsH": 90.0, "Costo_USDH": 380.00},
        {"Item": 3, "Agendar": False, "Estado": "🟡 Mantenimiento", "ID": "CF447", "Modelo": "WA900", "Operador": "Sin Asignar", "Rend_TonH": 685, "Consumo_LtsH": 75.0, "Costo_USDH": 342.50},
    ])

if "caex_df" not in st.session_state:
    st.session_state.caex_df = pd.DataFrame([
        {"Item": 1, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA319", "Modelo": "HD1500-8", "Operador": "Pedro Morales", "Rend_TonH": 604, "Consumo_LtsH": 95.0, "Costo_USDH": 289.92},
        {"Item": 2, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA320", "Modelo": "HD1500-8", "Operador": "Luis Tapia", "Rend_TonH": 604, "Consumo_LtsH": 95.0, "Costo_USDH": 289.92},
        {"Item": 3, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA321", "Modelo": "HD1500-8", "Operador": "Andrés Castro", "Rend_TonH": 604, "Consumo_LtsH": 95.0, "Costo_USDH": 289.92},
        {"Item": 4, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA322", "Modelo": "CAT 789D", "Operador": "Diego Rojas", "Rend_TonH": 710, "Consumo_LtsH": 110.0, "Costo_USDH": 310.00},
        {"Item": 5, "Agendar": True, "Estado": "🟢 Disponible", "ID": "CA323", "Modelo": "CAT 789D", "Operador": "Gonzalo Vera", "Rend_TonH": 710, "Consumo_LtsH": 110.0, "Costo_USDH": 310.00},
    ])

# ---------------------------------------------------------
# TABLAS DINÁMICAS
# ---------------------------------------------------------
st.markdown("<h2 class='centered-title'>🚜 Estado y Agendamiento de Flota Operativa</h2>", unsafe_allow_html=True)

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
        column_config={"Item": st.column_config.NumberColumn("N° Item", disabled=True), "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado)},
        hide_index=True,
        key="editor_palas",
        num_rows="dynamic"
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
        column_config={"Item": st.column_config.NumberColumn("N° Item", disabled=True), "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado)},
        hide_index=True,
        key="editor_cf",
        num_rows="dynamic"
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
        column_config={"Item": st.column_config.NumberColumn("N° Item", disabled=True), "Estado": st.column_config.SelectboxColumn("Estado Mecánico", options=opciones_estado)},
        hide_index=True,
        key="editor_caex",
        num_rows="dynamic"
    )

# ---------------------------------------------------------
# MOTOR MATEMÁTICO DE BALANCE Y RENTABILIDAD
# ---------------------------------------------------------
palas_activas = ed_palas[(ed_palas["Agendar"] == True) & (ed_palas["Estado"] == "🟢 Disponible")]
cf_activos = ed_cf[(ed_cf["Agendar"] == True) & (ed_cf["Estado"] == "🟢 Disponible")]
caex_activos = ed_caex[(ed_caex["Agendar"] == True) & (ed_caex["Estado"] == "🟢 Disponible")]

cap_carguio = palas_activas["Rend_TonH"].sum() + cf_activos["Rend_TonH"].sum()
cap_transporte = caex_activos["Rend_TonH"].sum()

litros_diesel_turno = (palas_activas["Consumo_LtsH"].sum() + cf_activos["Consumo_LtsH"].sum() + caex_activos["Consumo_LtsH"].sum()) * horas_turno
costo_diesel_turno = litros_diesel_turno * precio_diesel

costo_fijo_total_turno = (palas_activas["Costo_USDH"].sum() + cf_activos["Costo_USDH"].sum() + caex_activos["Costo_USDH"].sum()) * horas_turno
costo_opex_total_turno = costo_fijo_total_turno + costo_diesel_turno

match_factor = (cap_transporte / cap_carguio) if cap_carguio > 0 else 0.0
tasa_efectiva = min(cap_carguio, cap_transporte)
tonelaje_proyectado = tasa_efectiva * horas_turno

produccion_yodo_kg = (tonelaje_proyectado / factor_yodo) if factor_yodo > 0 else 0
ingreso_bruto_usd = tonelaje_proyectado * valor_ton_usd
beneficio_neto_usd = ingreso_bruto_usd - costo_opex_total_turno
costo_unitario_ton = (costo_opex_total_turno / tonelaje_proyectado) if tonelaje_proyectado > 0 else 0
costo_diesel_por_ton = (costo_diesel_turno / tonelaje_proyectado) if tonelaje_proyectado > 0 else 0

# BOTÓN EN SIDEBAR PARA GUARDAR EN BASE DE DATOS Y CERRAR
if st.sidebar.button("🔒 CIERRE Y GUARDADO EN BD", use_container_width=True):
    guardar_agendamiento_db(
        num_agendamiento,
        fecha_agendamiento.strftime('%Y-%m-%d'),
        hora_agendamiento.strftime('%H:%M:%S'),
        nombre_mina,
        turno_seleccionado,
        st.session_state.get('usuario_activo', 'Mauricio L. Cepeda Mondaca'),
        tonelaje_proyectado,
        litros_diesel_turno,
        costo_diesel_turno,
        costo_opex_total_turno,
        costo_unitario_ton,
        beneficio_neto_usd,
        match_factor
    )
    st.sidebar.success("✅ Agendamiento guardado exitosamente en la base de datos.")
    st.session_state.autenticado = False
    st.rerun()

# ---------------------------------------------------------
# DASHBOARD DE RESULTADOS
# ---------------------------------------------------------
st.markdown("---")
st.header(f"📈 Resumen de Agendamiento: {num_agendamiento}")
st.subheader(f"🏢 Faena: {nombre_mina} | Fecha y Hora: {fecha_agendamiento.strftime('%d/%m/%Y')} {hora_agendamiento.strftime('%H:%M')} hrs — {turno_seleccionado}")

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
    
    st.markdown(f'<p class="highlight-red-large">• Costo Combustible / Ton: ${fmt_num(costo_diesel_por_ton, 2)} USD/Ton</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Gasto Fijo Equipos: ${fmt_num(costo_fijo_total_turno, 2)} USD</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="highlight-red-large">• Producción Estimada Yodo: {fmt_num(produccion_yodo_kg, 1)} kg Yodo</p>', unsafe_allow_html=True)
    
    total_objetivo = target_mineral_num + target_esteril_num
    cumplimiento = (tonelaje_proyectado / total_objetivo) * 100 if total_objetivo > 0 else 0
    st.markdown(f'<p class="highlight-red-large">• Cumplimiento Plan de Mina: {fmt_num(cumplimiento, 1)}% de {fmt_num(total_objetivo, 0)} Ton Objetivo</p>', unsafe_allow_html=True)
    st.progress(min(cumplimiento / 100.0, 1.0))

with col_eval2:
    st.markdown("### 🚦 Semáforo Prescriptivo de Balance de Flota")
    
    st.markdown('<p class="mf-label">Match Factor Calculado:</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="mf-value">{fmt_num(match_factor, 2)}</p>', unsafe_allow_html=True)
    
    if 0.80 <= match_factor <= 1.05:
        st.success(f"🟢 **AGENDAMIENTO ÓPTIMO Y RENTABLE (Match Factor: {fmt_num(match_factor, 2)})**")
    elif match_factor < 0.80:
        st.error(f"🔴 **DESCALCE POR SUB-TRANSPORTE (Match Factor: {fmt_num(match_factor, 2)})**")
    else:
        st.warning(f"🟡 **SOBREDIMENSIONAMIENTO DE CAEX (Match Factor: {fmt_num(match_factor, 2)})**")

# ---------------------------------------------------------
# GRÁFICOS DE BARRAS DE PRODUCCIÓN VS COSTOS
# ---------------------------------------------------------
st.markdown("---")
st.subheader("📊 Análisis Comparativo: Producción Proyectada vs. Estructura de Costos OPEX")

col_g1, col_g2 = st.columns(2)

with col_g1:
    st.markdown("#### 📦 Tonelaje Proyectado vs. Meta de Producción (Ton)")
    df_prod = pd.DataFrame({
        "Categoría": ["Tonelaje Proyectado", "Meta Plan Mina"],
        "Toneladas": [float(tonelaje_proyectado), float(target_mineral_num + target_esteril_num)]
    })
    st.bar_chart(data=df_prod, x="Categoría", y="Toneladas", use_container_width=True)

with col_g2:
    st.markdown("#### 💰 Desglose del Costo OPEX del Turno (USD)")
    df_costos = pd.DataFrame({
        "Componente": ["Combustible Diésel", "Costo Fijo Equipos"],
        "Monto_USD": [float(costo_diesel_turno), float(costo_fijo_total_turno)]
    })
    st.bar_chart(data=df_costos, x="Componente", y="Monto_USD", use_container_width=True)

# ---------------------------------------------------------
# HISTÓRICO GUARDADO EN BASE DE DATOS Y GESTIÓN ADMINISTRADOR
# ---------------------------------------------------------
st.markdown("---")
col_h1, col_h2 = st.columns([3, 1])

with col_h1:
    st.subheader("📜 Histórico de Agendamientos Guardados en Base de Datos")

with col_h2:
    if st.session_state.get("user_id") == "mcepeda":
        if st.button("🗑️ Borrar Histórico (Admin)", type="primary", use_container_width=True):
            borrar_historico_db()
            st.success("Histórico eliminado correctamente.")
            st.rerun()

conn = sqlite3.connect(DB_FILE)
df_hist = pd.read_sql_query("SELECT * FROM historico_agendamientos ORDER BY id DESC", conn)
conn.close()

if not df_hist.empty:
    st.dataframe(df_hist, use_container_width=True)
else:
    st.info("Aún no hay agendamientos guardados en la base de datos. Haz clic en '🔒 CIERRE Y GUARDADO EN BD' para registrar el primero.")
