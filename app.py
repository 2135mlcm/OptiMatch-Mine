import time
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

# ==============================================================================
# 1. CONFIGURACIÓN INICIAL DE LA PÁGINA (STREAMLIT)
# ==============================================================================
st.set_page_config(
    page_title="OptiMatch Mine — Plataforma Prescriptiva OPEX",
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Estilos CSS personalizados para estética gerencial/minera
st.markdown(
    """
    <style>
    .main { background-color: #0E1117; }
    .stMetric { background-color: #1F2937; padding: 12px; border-radius: 8px; border: 1px solid #374151; }
    .badge-ok { background-color: #059669; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .badge-alert { background-color: #DC2626; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .badge-warn { background-color: #D97706; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    </style>
""",
    unsafe_allow_html=True,
)

# ==============================================================================
# 2. INICIALIZACIÓN DE LA SESIÓN (PERSISTENCIA DE DATOS EN MEMORIA)
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

if "evento_falla" not in st.session_state:
  st.session_state["evento_falla"] = {
      "CA321": False
  }  # Control de estado de equipo

# ==============================================================================
# 3. BARRA LATERAL (SIDEBAR) - NAVEGACIÓN Y CONFIGURACIÓN GENERAL
# ==============================================================================
st.sidebar.image(
    "https://raw.githubusercontent.com/2135mlcm/OptiMatch-Mine/main/Logo_OptiMatch.png",
    width=220,
)
st.sidebar.title("OptiMatch Mine")
st.sidebar.caption("Sistema Prescriptivo Pre-Turno | UAH Grupo N° 5")

menu_opcion = st.sidebar.radio(
    "Seleccione Módulo de Operación:",
    [
        "📋 Agendamiento Pre-Turno (DSM)",
        "🗺️ Monitoreo & Mapa de Flota (PyDeck)",
        "📊 Conciliación & Cierre (Plan vs. Real)",
        "📚 Marco Metodológico (5 Steps)",
    ],
)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Parámetros Fijos de Operación")
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
# MÓDULO 1: AGENDAMIENTO PRE-TURNO Y CÁLCULO DE MATCH FACTOR (STEP 3 & 4)
# ==============================================================================
if menu_opcion == "📋 Agendamiento Pre-Turno (DSM)":
  st.title("📋 Agendamiento Prescriptivo de Pre-Turno")
  st.markdown(
      "Defina la disponibilidad de flota y parámetros de la fase de minado"
      " para calcular el balance óptimo del **Match Factor** e imprevistos"
      " previos al despliegue en terreno."
  )

  col_conf1, col_conf2 = st.columns(2)

  with col_conf1:
    st.subheader("🚛 Configuración de Flota y Tiempos")
    num_palas = st.slider("Número de Palas Disponibles (Np):", 1, 4, 2)
    num_camiones = st.slider("Número de Camiones Disponibles (Nc):", 1, 15, 5)
    cap_balde = st.number_input(
        "Capacidad Balde Pala (Ton):", value=35.0, step=5.0
    )
    cap_tolva = st.number_input(
        "Capacidad Tolva Camión (Ton):", value=150.0, step=10.0
    )

  with col_conf2:
    st.subheader("⏱️ Distancias y Malla Horaria")
    distancia_km = st.number_input(
        "Distancia Acarreo - Ida/Vuelta (km):", value=3.5, step=0.5
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

  # --- CÁLCULOS ALGORÍTMICOS (STEP 3: DSM / TASA EFECTIVA) ---
  tiempo_efectivo_h = duracion_turno_h - tiempo_colacion_h
  tiempo_carga_min = (cap_tolva / cap_balde) * 0.8  # ~2.8 a 3 min por camión
  tiempo_viaje_min = (distancia_km / vel_prom_kph) * 60
  tiempo_acople_volteo_min = 3.0  # Maniobra y descarga en chancador
  tiempo_ciclo_camion_min = (
      tiempo_carga_min + tiempo_viaje_min + tiempo_acople_volteo_min
  )

  # Fórmula del Match Factor
  match_factor = (num_camiones * tiempo_carga_min) / (
      num_palas * tiempo_ciclo_camion_min
  )

  # Capacidades Efectivas (DSM)
  cap_efectiva_carguio = (
      num_palas * (60 / tiempo_carga_min) * cap_tolva * tiempo_efectivo_h
  )
  cap_efectiva_transporte = (
      num_camiones
      * (60 / tiempo_ciclo_camion_min)
      * cap_tolva
      * tiempo_efectivo_h
  )

  # Tasa Efectiva Operacional = min(Carguío, Transporte)
  toneladas_proyectadas = min(cap_efectiva_carguio, cap_efectiva_transporte)

  # Estimación de Consumo y OPEX
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

  # Estado del Match Factor según Lean Mining (0.92 - 1.08)
  if 0.92 <= match_factor <= 1.08:
    estado_mf = "🟢 ÓPTIMO (SISTEMA BALANCEADO)"
  elif match_factor < 0.92:
    estado_mf = "⚠️ DÉFICIT DE CAMIONES (Pala Subutilizada)"
  else:
    estado_mf = "🚨 EXCESO DE CAMIONES (Colas en Frente)"

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

  if st.button("💾 Validar y Emitir Pauta Prescriptiva de Turno"):
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
    st.success(
        "✅ Pauta prescriptiva emitida y respaldada en la base de datos de"
        " OptiMatch Mine."
    )

# ==============================================================================
# MÓDULO 2: MONITOREO Y MAPA INTERACTIVO DE FLOTA (PYDECK / SIMULACIÓN)
# ==============================================================================
elif menu_opcion == "🗺️ Monitoreo & Mapa de Flota (PyDeck)":
  st.title("🗺️ Monitoreo y Simulación de Circuito en Tiempo Real")
  st.markdown(
      "Visualización espacial de las unidades de carguío y transporte sobre"
      " el mapa del rajo minero. Haz clic o pasa el cursor sobre cada equipo"
      " para inspeccionar su ficha técnica."
  )

  # Control de imprevisto/falla en vivo
  st.subheader("🚨 Panel de Control de Eventos de Terreno")
  col_ev1, col_ev2 = st.columns(2)
  with col_ev1:
    falla_ca321 = st.checkbox(
        "Simular Detención / Falla Mecánica en Camión CA321",
        value=st.session_state["evento_falla"]["CA321"],
    )
    st.session_state["evento_falla"]["CA321"] = falla_ca321
  with col_ev2:
    if falla_ca321:
      st.error(
          "🚨 ALERTA TIMEOUT RFID: Equipo CA321 reportado sin paso por"
          " chancador (>45 min). Se descuenta de la capacidad activa."
      )
    else:
      st.success("🟢 Todos los equipos operando dentro de la ventana normal.")

  # Construcción de coordenadas y fichas para PyDeck
  URL_GIF_CAEX = "https://raw.githubusercontent.com/2135mlcm/OptiMatch-Mine/main/Logo_OptiMatch.png"
  URL_GIF_PALA = "https://raw.githubusercontent.com/2135mlcm/OptiMatch-Mine/main/Logo_OptiMatch.png"

  estado_ca321 = (
      "🔴 Falla Mecánica (Detenido)" if falla_ca321 else "🟢 En Ruta Cargado"
  )
  vueltas_ca321 = 4 if falla_ca321 else 9

  datos_flota = pd.DataFrame([
      {
          "Equipo": "Pala PA624",
          "Tipo": "Pala Hidráulica CAT 6060",
          "Estado": "🟢 Cargando activos",
          "lat": -22.3000,
          "lon": -68.9000,
          "icon_url": URL_GIF_PALA,
          "Operador": "M. Cepeda",
          "Diesel_Lts": "650.0 L",
          "CO2_kg": "1,742.0 kg",
          "Diagnostico": "Ritmo: 1,850 Ton/h | Match Factor: 0.99",
      },
      {
          "Equipo": "Pala PA625",
          "Tipo": "Pala Hidráulica Bucyrus",
          "Estado": "🟢 Cargando activos",
          "lat": -22.2980,
          "lon": -68.9020,
          "icon_url": URL_GIF_PALA,
          "Operador": "A. Videla",
          "Diesel_Lts": "610.0 L",
          "CO2_kg": "1,634.0 kg",
          "Diagnostico": "Ritmo: 1,780 Ton/h | Balde: 35 Ton",
      },
      {
          "Equipo": "CAEX CA319",
          "Tipo": "Komatsu HD1500-8 (150 Ton)",
          "Estado": "🟢 En Ruta Cargado",
          "lat": -22.3020,
          "lon": -68.8950,
          "icon_url": URL_GIF_CAEX,
          "Operador": "J. Pérez",
          "Diesel_Lts": "340.5 L",
          "CO2_kg": "912.5 kg",
          "Diagnostico": "Vueltas: 9/10 | Consumo Esp: 2.27 L/Ton",
      },
      {
          "Equipo": "CAEX CA320",
          "Tipo": "Komatsu HD1500-8 (150 Ton)",
          "Estado": "⏸️ En Colación",
          "lat": -22.3010,
          "lon": -68.8980,
          "icon_url": URL_GIF_CAEX,
          "Operador": "C. Ruiz",
          "Diesel_Lts": "280.0 L",
          "CO2_kg": "750.4 kg",
          "Diagnostico": "Pausa programada de almuerzo (13:00-14:00)",
      },
      {
          "Equipo": "CAEX CA321",
          "Tipo": "Komatsu HD1500-8 (150 Ton)",
          "Estado": estado_ca321,
          "lat": -22.3005,
          "lon": -68.8990,
          "icon_url": URL_GIF_CAEX,
          "Operador": "P. Soto",
          "Diesel_Lts": "150.2 L",
          "CO2_kg": "402.5 kg",
          "Diagnostico": f"Vueltas: {vueltas_ca321}/10 | Evento: Fuga hidráulica",
      },
      {
          "Equipo": "CAEX CA322",
          "Tipo": "Komatsu HD1500-8 (150 Ton)",
          "Estado": "🟢 En Ruta Vacío",
          "lat": -22.3030,
          "lon": -68.8940,
          "icon_url": URL_GIF_CAEX,
          "Operador": "D. Daines",
          "Diesel_Lts": "310.0 L",
          "CO2_kg": "830.8 kg",
          "Diagnostico": "Retorno a fosa norte | Vel: 24 km/h",
      },
      {
          "Equipo": "CAEX CA323",
          "Tipo": "Komatsu HD1500-8 (150 Ton)",
          "Estado": "🟢 En Maniobra Balanza",
          "lat": -22.3040,
          "lon": -68.8920,
          "icon_url": URL_GIF_CAEX,
          "Operador": "L. Morales",
          "Diesel_Lts": "335.0 L",
          "CO2_kg": "897.8 kg",
          "Diagnostico": "Registro RFID detectado en pórtico chancado",
      },
  ])

  datos_flota["icon_data"] = [
      {"url": r["icon_url"], "width": 128, "height": 128, "anchorY": 128}
      for _, r in datos_flota.iterrows()
  ]

  capa_iconos = pdk.Layer(
      "IconLayer",
      datos_flota,
      get_icon="icon_data",
      get_size=4,
      size_scale=10,
      get_position=["lon", "lat"],
      pickable=True,
  )

  vista_inicial = pdk.ViewState(
      latitude=-22.3010, longitude=-68.8970, zoom=14.5, pitch=45
  )

  tooltip_config = {
      "html": """
        <div style="background-color: #111827; color: white; padding: 12px; border-radius: 8px; border: 2px solid #F59E0B;">
            <h3 style="margin:0; color:#F59E0B; font-size:16px;">🚚 {Equipo}</h3>
            <b>Modelo:</b> {Tipo}<br>
            <b>Estado Operativo:</b> {Estado}<br>
            <b>Operador Asignado:</b> {Operador}<br>
            <hr style="border-color: #374151; margin:6px 0;">
            <b>⛽ Diésel Consumido:</b> {Diesel_Lts} | <b>🌱 CO₂:</b> {CO2_kg}<br>
            <p style="font-size:11px; color:#FBBF24; margin-top:5px; margin-bottom:0;"><b>Diagnóstico:</b> {Diagnostico}</p>
        </div>
    """,
      "style": {"color": "white"},
  }

  r = pdk.Deck(
      layers=[capa_iconos],
      initial_view_state=vista_inicial,
      tooltip=tooltip_config,
      map_style="mapbox://styles/mapbox/dark-v10",
  )

  st.pydeck_chart(r)

# ==============================================================================
# MÓDULO 3: CONCILIACIÓN Y CIERRE DE TURNO (PLAN VS. ACTUAL)
# ==============================================================================
elif menu_opcion == "📊 Conciliación & Cierre (Plan vs. Real)":
  st.title("📊 Conciliación de Turno (Plan vs. Actual)")
  st.markdown(
      "Auditabilidad del cumplimiento del plan de minado al término de la"
      " jornada. Comparativa entre la simulación prescriptiva y las toneladas"
      " reales capturadas vía RFID/Balanza."
  )

  col_c1, col_c2 = st.columns(2)
  with col_c1:
    ton_planificadas = st.number_input(
        "Toneladas Planificadas (Pre-Turno):", value=13500.0, step=500.0
    )
    ton_reales = st.number_input(
        "Toneladas Reales Movidas (Balanza):", value=12100.0, step=500.0
    )
  with col_c2:
    causa_desviacion = st.selectbox(
        "Causa Principal de Desviación (Si aplica):",
        [
            "Ninguna / Dentro de Margen",
            "Falla Mecánica de CAEX (Ej. CA321)",
            "Espera por Tronadura No Programada",
            "Baja Velocidad por Polvo / Visibilidad",
            "Cuello de Botella en Chancador Primario",
        ],
    )

  adherencia = (ton_reales / ton_planificadas) * 100

  st.markdown("---")
  c1, c2, c3 = st.columns(3)
  c1.metric("Adherencia al Plan", f"{adherencia:.1f}%")
  c2.metric("Brecha de Tonelaje", f"{ton_reales - ton_planificadas:,.0f} Ton")
  c3.metric(
      "Estado de Conciliación",
      (
          "🟢 CONFORME"
          if adherencia >= 95
          else "⚠️ DESVIACIÓN ACEPTABLE"
          if adherencia >= 85
          else "🔴 REVISIÓN REQUERIDA"
      ),
  )

  # Gráfico comparativo Plotly
  df_comp = pd.DataFrame({
      "Categoría": ["Planificado (Prescriptivo)", "Real (Terreno)"],
      "Toneladas": [ton_planificadas, ton_reales],
  })
  fig_comp = px.bar(
      df_comp,
      x="Categoría",
      y="Toneladas",
      color="Categoría",
      color_discrete_sequence=["#10B981", "#F59E0B"],
      text="Toneladas",
  )
  fig_comp.update_layout(height=350, showlegend=False)
  st.plotly_chart(fig_comp, use_container_width=True)

# ==============================================================================
# MÓDULO 4: MARCO METODOLÓGICO Y MATRIZ DE INTEGRACIÓN (DOCUMENTACIÓN)
# ==============================================================================
elif menu_opcion == "📚 Marco Metodológico (5 Steps)":
  st.title("📚 Fundamentos Metodológicos de OptiMatch Mine")
  st.markdown(
      "Arquitectura de ingeniería utilizada para resolver el descalce de"
      " flotas en la mediana minería sin incurrir en costos de FMS tradicionales:"
  )

  st.markdown("""
    * **STEP 1 (Matriz de Vester):** Aislamiento de la causa raíz: la asignación empírica e inexistencia de un simulador prescriptivo previo al turno.
    * **STEP 2 (OTSM-TRIZ):** Resolución de la contradicción entre Precisión Algorítmica vs. Velocidad de Respuesta mediante los Principios N.° 1 (Modularidad) y N.° 28 (Sistema Prescriptivo Discreto).
    * **STEP 3 (Design Structure Matrix - DSM):** Estructuración de dependencias operativas para calcular la Tasa Efectiva $\min(\text{Carguío}, \text{Transporte})$ y acoplamiento óptimo.
    * **STEP 4 (Lean Mining):** Definición de rangos estrictos de tolerancia del Match Factor ($1.00 \pm 0.08$) con semáforos operacionales para mitigar la *Muda* (desperdicios).
    * **STEP 5 (Scrum & Iteración):** Desarrollo ágil del software prescriptivo con bucle de autocorrección antes de emitir la pauta definitiva a terreno.
    """)
