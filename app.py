import time
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL DE LA PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="OptiMatch Mine — Sistema Prescriptivo OPEX",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==============================================================================
# 2. INICIALIZACIÓN DE SESIÓN (PERSISTENCIA DE DATOS)
# ==============================================================================
if "historico_agendamientos" not in st.session_state:
  st.session_state["historico_agendamientos"] = pd.DataFrame(columns=[
      "ID_Turno",
      "Fecha",
      "Fase",
      "Palas",
      "Camiones",
      "MatchFactor",
      "TonProyectadas",
      "DieselTotal_L",
      "CO2_Kg",
      "USD_Ton",
  ])

# ==============================================================================
# 3. BARRA LATERAL (SIDEBAR)
# ==============================================================================
st.sidebar.title("OptiMatch Mine")
st.sidebar.caption("Plataforma Prescriptiva Pre-Turno | UAH Grupo N° 5")

menu_opcion = st.sidebar.radio(
    "Seleccione Módulo de Operación:",
    [
        "📋 Agendamiento Pre-Turno (DSM)",
        "🗺️ Simulación y Estado de Circuito",
        "📊 Conciliación de Turno (Plan vs. Real)",
        "📚 Marco Metodológico (5 Steps)",
    ],
)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Parámetros Fijos OPEX")
precio_diesel = st.sidebar.number_input(
    "Precio Diésel (USD/L):", value=1.05, step=0.05
)
factor_emision_co2 = st.sidebar.number_input(
    "Factor CO2 (kg/L):", value=2.68, step=0.01
)
costo_hora_pala = st.sidebar.number_input(
    "Costo Operativo Pala (USD/h):", value=250.0
)
costo_hora_camion = st.sidebar.number_input(
    "Costo Operativo Camión (USD/h):", value=120.0
)

# ==============================================================================
# MÓDULO 1: AGENDAMIENTO PRE-TURNO Y CÁLCULO DE MATCH FACTOR
# ==============================================================================
if menu_opcion == "📋 Agendamiento Pre-Turno (DSM)":
  st.title("📋 Agendamiento Prescriptivo de Pre-Turno")
  st.markdown(
      "Defina la disponibilidad de flota y parámetros de la fase de minado"
      " para calcular el balance óptimo del **Match Factor** previo al inicio"
      " de la jornada."
  )

  col_conf1, col_conf2 = st.columns(2)

  with col_conf1:
    st.subheader("🚛 Configuración de Flota")
    num_palas = st.slider("Número de Palas Disponibles (Np):", 1, 4, 2)
    num_camiones = st.slider("Número de Camiones Disponibles (Nc):", 1, 15, 5)
    cap_balde = st.number_input(
        "Capacidad Balde Pala (Ton):", value=35.0, step=5.0
    )
    cap_tolva = st.number_input(
        "Capacidad Tolva Camión (Ton):", value=150.0, step=10.0
    )

  with col_conf2:
    st.subheader("⏱️ Distancias y Horarios")
    distancia_km = st.number_input(
        "Distancia Acarreo Ida/Vuelta (km):", value=3.5, step=0.5
    )
    vel_prom_kph = st.number_input(
        "Velocidad Promedio Flota (km/h):", value=22.0, step=1.0
    )
    duracion_turno_h = st.number_input(
        "Duración Nominal Turno (Horas):", value=10.0, step=0.5
    )
    tiempo_colacion_h = st.number_input(
        "Pausa Colación y Cambios (Horas):", value=1.5, step=0.25
    )

  # CÁLCULOS TÉCNICOS (STEP 3: DSM)
  tiempo_efectivo_h = duracion_turno_h - tiempo_colacion_h
  tiempo_carga_min = (cap_tolva / cap_balde) * 0.8
  tiempo_viaje_min = (distancia_km / vel_prom_kph) * 60
  tiempo_acople_volteo_min = 3.0
  tiempo_ciclo_camion_min = (
      tiempo_carga_min + tiempo_viaje_min + tiempo_acople_volteo_min
  )

  match_factor = (num_camiones * tiempo_carga_min) / (
      num_palas * tiempo_ciclo_camion_min
  )

  cap_efectiva_carguio = (
      num_palas * (60 / tiempo_carga_min) * cap_tolva * tiempo_efectivo_h
  )
  cap_efectiva_transporte = (
      num_camiones
      * (60 / tiempo_ciclo_camion_min)
      * cap_tolva
      * tiempo_efectivo_h
  )

  toneladas_proyectadas = min(cap_efectiva_carguio, cap_efectiva_transporte)

  consumo_estimado_diesel_L = (
      (num_camiones * 35.0 * tiempo_efectivo_h)
      + (num_palas * 65.0 * tiempo_efectivo_h)
  )
  emisiones_co2_kg = consumo_estimado_diesel_L * factor_emision_co2
  costo_total_opex = (
      (num_palas * costo_hora_pala * duracion_turno_h)
      + (num_camiones * costo_hora_camion * duracion_turno_h)
      + (consumo_estimado_diesel_L * precio_diesel)
  )
  usd_ton = (
      costo_total_opex / toneladas_proyectadas
      if toneladas_proyectadas > 0
      else 0
  )

  st.markdown("---")
  st.subheader("📊 Indicadores Prescriptivos de Pre-Turno")

  m1, m2, m3, m4 = st.columns(4)

  if 0.92 <= match_factor <= 1.08:
    estado_mf = "🟢 ÓPTIMO (SISTEMA BALANCEADO)"
  elif match_factor < 0.92:
    estado_mf = "⚠️ DÉFICIT DE CAMIONES"
  else:
    estado_mf = "🚨 EXCESO DE CAMIONES"

  m1.metric("Match Factor (MF)", f"{match_factor:.2f}", estado_mf)
  m2.metric(
      "Producción Proyectada",
      f"{toneladas_proyectadas:,.0f} Ton",
      f"Horas Ef: {tiempo_efectivo_h:.1f} h",
  )
  m3.metric(
      "Consumo Diésel Est.",
      f"{consumo_estimado_diesel_L:,.0f} Lts",
      f"CO2: {emisiones_co2_kg:,.0f} kg",
  )
  m4.metric("Costo Unitario (OPEX)", f"${usd_ton:.2f} USD/Ton")

  if st.button("💾 Validar y Emitir Pauta Prescriptiva"):
    nuevo_reg = pd.DataFrame([{
        "ID_Turno": f"TRN-{int(time.time())}",
        "Fecha": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "Fase": "Fase Norte (Acarreo 3.5 km)",
        "Palas": num_palas,
        "Camiones": num_camiones,
        "MatchFactor": round(match_factor, 2),
        "TonProyectadas": round(toneladas_proyectadas, 0),
        "DieselTotal_L": round(consumo_estimado_diesel_L, 0),
        "CO2_Kg": round(emisiones_co2_kg, 0),
        "USD_Ton": round(usd_ton, 2),
    }])
    st.session_state["historico_agendamientos"] = pd.concat(
        [st.session_state["historico_agendamientos"], nuevo_reg],
        ignore_index=True,
    )
    st.success("✅ Pauta prescriptiva emitida y guardada exitosamente.")

# ==============================================================================
# MÓDULO 2: SIMULACIÓN Y ESTADO DE CIRCUITO
# ==============================================================================
elif menu_opcion == "🗺️ Simulación y Estado de Circuito":
  st.title("🗺️ Simulación y Monitoreo de Circuito")
  st.markdown(
      "Estado operativo de la flota en tiempo real basado en la simulación"
      " por tiempos y eventos de terreno."
  )

  datos_flota = pd.DataFrame([
      {
          "Equipo": "Pala PA624",
          "Tipo": "Unidad Carguío",
          "Estado": "🟢 Cargando",
          "Operador": "M. Cepeda",
          "Vueltas_Esp": "-",
          "Vueltas_Real": "-",
          "Diesel_Lts": 650.0,
          "CO2_kg": 1742.0,
          "Diagnostico": "Ritmo: 1,850 Ton/h | Match Factor: 0.99",
      },
      {
          "Equipo": "Pala PA625",
          "Tipo": "Unidad Carguío",
          "Estado": "🟢 Cargando",
          "Operador": "A. Videla",
          "Vueltas_Esp": "-",
          "Vueltas_Real": "-",
          "Diesel_Lts": 610.0,
          "CO2_kg": 1634.0,
          "Diagnostico": "Ritmo: 1,780 Ton/h | Balde: 35 Ton",
      },
      {
          "Equipo": "CAEX CA319",
          "Tipo": "Camión Acarreo",
          "Estado": "🟢 En Ruta",
          "Operador": "J. Pérez",
          "Vueltas_Esp": 10,
          "Vueltas_Real": 9,
          "Diesel_Lts": 340.5,
          "CO2_kg": 912.5,
          "Diagnostico": "Tolerancia OK (+3 min en rampa)",
      },
      {
          "Equipo": "CAEX CA320",
          "Tipo": "Camión Acarreo",
          "Estado": "⏸️ Colación",
          "Operador": "C. Ruiz",
          "Vueltas_Esp": 8,
          "Vueltas_Real": 8,
          "Diesel_Lts": 280.0,
          "CO2_kg": 750.4,
          "Diagnostico": "En pausa programada por horario de almuerzo",
      },
      {
          "Equipo": "CAEX CA321",
          "Tipo": "Camión Acarreo",
          "Estado": "🔴 Falla Mecánica",
          "Operador": "P. Soto",
          "Vueltas_Esp": 10,
          "Vueltas_Real": 4,
          "Diesel_Lts": 150.2,
          "CO2_kg": 402.5,
          "Diagnostico": "🚨 Alerta Timeout RFID: Detenido por falla hidráulica",
      },
      {
          "Equipo": "CAEX CA322",
          "Tipo": "Camión Acarreo",
          "Estado": "🟢 En Ruta",
          "Operador": "D. Daines",
          "Vueltas_Esp": 10,
          "Vueltas_Real": 9,
          "Diesel_Lts": 310.0,
          "CO2_kg": 830.8,
          "Diagnostico": "Retorno a fosa norte sin novedad",
      },
      {
          "Equipo": "CAEX CA323",
          "Tipo": "Camión Acarreo",
          "Estado": "🟢 En Ruta",
          "Operador": "L. Morales",
          "Vueltas_Esp": 10,
          "Vueltas_Real": 10,
          "Diesel_Lts": 335.0,
          "CO2_kg": 897.8,
          "Diagnostico": "Lectura RFID registrada en pórtico chancador",
      },
  ])

  st.dataframe(datos_flota, use_container_width=True)

  st.subheader("📋 Ficha Técnica por Equipo")
  equipo_sel = st.selectbox(
      "Seleccione un equipo para ver detalle:", datos_flota["Equipo"]
  )
  info = datos_flota[datos_flota["Equipo"] == equipo_sel].iloc[0]

  c1, c2, c3 = st.columns(3)
  with c1:
    st.metric("Estado Operativo", info["Estado"])
    st.write(f"**Tipo:** {info['Tipo']}")
    st.write(f"**Operador:** {info['Operador']}")
  with c2:
    st.metric("Diésel Consumido", f"{info['Diesel_Lts']} Lts")
    st.metric("Huella de Carbono", f"{info['CO2_kg']} kg CO₂")
  with c3:
    st.metric(
        "Vueltas (Reales vs Esp)", f"{info['Vueltas_Real']} / {info['Vueltas_Esp']}"
    )
    st.info(f"**Diagnóstico:** {info['Diagnostico']}")

# ==============================================================================
# MÓDULO 3: CONCILIACIÓN DE TURNO
# ==============================================================================
elif menu_opcion == "📊 Conciliación de Turno (Plan vs. Real)":
  st.title("📊 Conciliación de Turno (Plan vs. Real)")
  st.markdown(
      "Comparativa entre las toneladas proyectadas en el agendamiento y el"
      " tonelaje real registrado al cierre de la jornada."
  )

  col_c1, col_c2 = st.columns(2)
  with col_c1:
    ton_planificadas = st.number_input(
        "Toneladas Planificadas:", value=13500.0, step=500.0
    )
    ton_reales = st.number_input(
        "Toneladas Reales Movidas:", value=12100.0, step=500.0
    )

  adherencia = (
      (ton_reales / ton_planificadas) * 100 if ton_planificadas > 0 else 0
  )

  st.markdown("---")
  c1, c2, c3 = st.columns(3)
  c1.metric("Adherencia al Plan", f"{adherencia:.1f}%")
  c2.metric("Brecha", f"{ton_reales - ton_planificadas:,.0f} Ton")
  c3.metric(
      "Evaluación",
      (
          "🟢 CONFORME"
          if adherencia >= 95
          else "⚠️ DESVIACIÓN ACEPTABLE"
          if adherencia >= 85
          else "🔴 DESVIACIÓN CRÍTICA"
      ),
  )

# ==============================================================================
# MÓDULO 4: MARCO METODOLÓGICO
# ==============================================================================
elif menu_opcion == "📚 Marco Metodológico (5 Steps)":
  st.title("📚 Fundamentos Metodológicos de OptiMatch Mine")
  st.markdown("""
    1. **STEP 1 (Vester):** Causa raíz -> Asignación empírica y falta de simulador prescriptivo.
    2. **STEP 2 (OTSM-TRIZ):** Resolución de contradicción -> Precisión vs. Velocidad con Principios TRIZ N° 1 y N° 28.
    3. **STEP 3 (DSM):** Matriz de dependencias y cálculo de Tasa Efectiva $\min(\text{Carguío}, \text{Transporte})$.
    4. **STEP 4 (Lean Mining):** Tolerancia de Match Factor ($1.00 \pm 0.08$) con semáforos operacionales.
    5. **STEP 5 (Scrum):** Iteración ágil del software prescriptivo en Python/Streamlit.
    """)
