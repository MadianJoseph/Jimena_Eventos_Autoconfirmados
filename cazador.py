import time
import requests
import threading
import os
import re
from datetime import datetime, timedelta
import pytz
from flask import Flask
from playwright.sync_api import sync_playwright

# ================= CONFIGURACIÓN =================
URL_LOGIN = "https://eventossistema.com.mx/login.html"
URL_EVENTS = "https://eventossistema.com.mx/confirmaciones/default.html"
CHECK_INTERVAL = 90 
TZ = pytz.timezone("America/Mexico_City")

USER = os.getenv("WEB_USER")
PASS = os.getenv("WEB_PASS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

@app.route("/")
def home(): 
    return f"Bot Asistente Jimena V4.0 - Online - {datetime.now(TZ).strftime('%H:%M:%S')}"

def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try: 
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def extraer_datos_tabla(html_content):
    info = {"puesto": "", "turnos": "0", "lugar": "", "indicaciones": "", "mins_entrada": 0, "fecha_dt": None}
    try:
        # Puesto
        p_match = re.search(r'PUESTO</td><td.*?>(.*?)</td>', html_content)
        if p_match: info['puesto'] = p_match.group(1).strip().upper()
        
        # Lugar
        l_match = re.search(r'LUGAR</td><td.*?>(.*?)</td>', html_content)
        if l_match: info['lugar'] = l_match.group(1).strip().upper()

        # Indicaciones
        i_match = re.search(r'INDICACIONES</td><td.*?>(.*?)</td>', html_content)
        if i_match: info['indicaciones'] = i_match.group(1).strip().upper()
        
        # Horario y Fecha
        h_match = re.search(r'HORARIO</td><td.*?>(.*?)</td>', html_content, re.DOTALL)
        if h_match:
            texto_h = h_match.group(1)
            # Turnos
            t_match = re.search(r'TURNOS\s*(\d+\.?\d*)', texto_h, re.IGNORECASE)
            if t_match: info['turnos'] = t_match.group(1)
            
            # Hora de entrada (minutos desde las 00:00)
            hora_m = re.search(r'(\d{2}):(\d{2})', texto_h)
            if hora_m:
                h, m = int(hora_m.group(1)), int(hora_m.group(2))
                info['mins_entrada'] = (h * 60) + m
            
            # Fecha para cálculos de horas
            f_match = re.search(r'(\d{2}/\d{2}/\d{2,4})', texto_h)
            if f_match and hora_m:
                fecha_str = f"{f_match.group(1)} {hora_m.group(1)}:{hora_m.group(2)}"
                fmt = "%d/%m/%y %H:%M" if len(f_match.group(1).split('/')[-1]) == 2 else "%d/%m/%Y %H:%M"
                info['fecha_dt'] = TZ.localize(datetime.strptime(fecha_str, fmt))
    except: pass
    return info

def analizar_filtros(info, titulo_card):
    titulo = titulo_card.upper()
    puesto = info['puesto']
    turnos = info['turnos']
    lugar = info['lugar']
    mins = info['mins_entrada']
    todo_texto = (titulo + info['indicaciones'] + lugar).upper()
    ahora = datetime.now(TZ)

    # --- 1. PEPSI CENTER WTC (Mantiene anterior) ---
    if "PEPSI CENTER" in todo_texto:
        if puesto in ["SEGURIDAD", "BOLETAJE", "ACOMODADOR EE"]:
            return True, "PEPSI CENTER (Auto)", True

    # --- 2. ALFREDO HARP HELU (DIABLOS) ---
    if "ESTADIO ALFREDO HARP HELU (DIABLOS)" in todo_texto or "DIABLOS" in todo_texto:
        if turnos == "1" and puesto in ["SEGURIDAD", "LOCAL CREW", "BOLETAJE"]:
            if puesto != "ACOMODADOR EE": # Refuerzo de seguridad
                return True, "DIABLOS (Auto)", True

    # --- 3. CCXP - CENTRO BANAMEX ---
    if "CCXP" in todo_texto or "CENTRO BANAMEX" in todo_texto:
        # Filtro No Nocturnas (Inician entre 19:30 y 07:30)
        es_nocturna = (mins >= 1170 or mins <= 450)
        if es_nocturna: return True, "CCXP Nocturna (Manual)", False
        
        if puesto in ["SEGURIDAD", "LOCAL CREW"]:
            fecha_str = info['fecha_dt'].strftime("%d/%m") if info['fecha_dt'] else ""
            # Reglas por fecha
            if "23/04" in fecha_str and turnos == "1" and mins == 930: # 15:30
                return True, "CCXP 23/04 Prioridad (Auto)", True
            elif "24/04" in fecha_str and turnos == "1.5" and mins == 570: # 09:30
                return True, "CCXP 24/04 (Auto)", True
            elif "25/04" in fecha_str and turnos == "1.5" and mins == 540: # 09:00
                return True, "CCXP 25/04 (Auto)", True
            elif "26/04" in fecha_str and turnos == "1.5" and mins == 510: # 08:30
                return True, "CCXP 26/04 (Auto)", True

    # --- 4. ESTADIO GNP (Regla de las 80 horas) ---
    if "ESTADIO GNP" in todo_texto:
        if "OVG" in todo_texto or "ACREDITACIONES" in todo_texto or "ACREDITACION" in todo_texto:
            return True, "GNP (OVG/Acred - Manual)", False
        
        if (turnos == "1.5" and puesto == "SEGURIDAD") or (turnos == "1" and puesto == "BOLETAJE"):
            # Regla de nocturnas condicional (más de 80 horas de anticipación)
            es_nocturna = (mins >= 1170 or mins <= 450)
            if es_nocturna and info['fecha_dt']:
                diferencia = info['fecha_dt'] - ahora
                horas_dif = diferencia.total_seconds() / 3600
                if horas_dif >= 80:
                    return True, f"GNP Nocturna >80h ({int(horas_dif)}h) - Auto", True
                else:
                    return True, f"GNP Nocturna <80h ({int(horas_dif)}h) - Manual", False
            elif not es_nocturna:
                return True, "GNP Normal (Auto)", True

    # --- 5. PALACIO DE LOS DEPORTES ---
    if "PALACIO DE LOS DEPORTES" in todo_texto:
        # Filtro No Nocturnas (Turnos normales 14:00 - 16:00)
        if 840 <= mins <= 960: # Entre 14:00 y 16:00
            if turnos == "1" and puesto in ["SEGURIDAD", "BOLETAJE", "ACOMODADOR EE"]:
                return True, "PALACIO (Auto)", True

    # --- 6. TYLER & DEFTONES (Mantiene anterior) ---
    if "TYLER THE CREATOR" in todo_texto and turnos == "1" and puesto in ["SEGURIDAD", "LOCAL CREW", "BOLETAJE", "ACOMODADOR EE"]:
        if "24/03" in todo_texto or "25/03" in todo_texto:
            if "ACREDITACION" not in todo_texto: return True, "TYLER (Auto)", True
    
    if "DEFTONES" in todo_texto and "29/03" in todo_texto and puesto in ["SEGURIDAD", "LOCAL CREW", "BOLETAJE", "ACOMODADOR EE"]:
        if "ACREDITACION" not in todo_texto: return True, "DEFTONES (Auto)", True

    return True, "Evento Nuevo (Revisión Manual)", False

def run_once():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(user_agent="Mozilla/5.0...")
            page = context.new_page()

            page.goto(URL_LOGIN, wait_until="networkidle", timeout=60000)
            page.fill("input[name='usuario']", USER)
            page.fill("input[name='password']", PASS)
            page.click("button[type='submit']")
            page.wait_for_timeout(5000)

            page.goto(URL_EVENTS, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            
            cards = page.query_selector_all(".card.border")
            for card in cards:
                if card.evaluate("(node) => node.closest('#div_eventos_confirmados') !== null"):
                    continue

                titulo_elem = card.query_selector("h6 a")
                if not titulo_elem: continue
                titulo_texto = titulo_elem.inner_text().strip()

                titulo_elem.click()
                page.wait_for_timeout(1500)
                tabla = card.query_selector(".table-responsive")
                
                if tabla:
                    info = extraer_datos_tabla(tabla.inner_html())
                    interesa, motivo, auto = analizar_filtros(info, titulo_texto)

                    if auto:
                        btn = card.query_selector("button:has-text('CONFIRMAR')")
                        if btn:
                            btn.click()
                            page.wait_for_timeout(3000)
                            send(f"🎯 *CONFIRMADO:* {titulo_texto}\n👤 Puesto: {info['puesto']}\n✅ Filtro: {motivo}")
                        else:
                            send(f"⚠️ *AVISO:* Criterios OK para {titulo_texto} pero botón no hallado.")
                    else:
                        send(f"🔔 *NUEVO EVENTO:* {titulo_texto}\n👉 Puesto: {info['puesto']}\n(Manual)")
            
            browser.close()
    except Exception as e:
        print(f"Error: {e}")

def monitor_loop():
    while True:
        run_once()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    threading.Thread(target=monitor_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
    
