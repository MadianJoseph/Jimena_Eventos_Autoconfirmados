import time
import requests
import threading
import os
import re
from datetime import datetime
import pytz
from flask import Flask
from playwright.sync_api import sync_playwright

# ================= CONFIGURACIÓN =================
URL_LOGIN = "https://eventossistema.com.mx/login.html"
URL_EVENTS = "https://eventossistema.com.mx/confirmaciones/default.html"
CHECK_INTERVAL = 90 
TZ = pytz.timezone("America/Mexico_City")

# Credenciales (Asegúrate de poner las de ella en Render)
USER = os.getenv("WEB_USER")
PASS = os.getenv("WEB_PASS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Puestos aceptados para los filtros automáticos
PUESTOS_PEPSI = ["SEGURIDAD", "BOLETAJE", "ACOMODADOR EE"]
PUESTOS_CONCIERTOS = ["SEGURIDAD", "LOCAL CREW", "BOLETAJE", "ACOMODADOR EE"]

app = Flask(__name__)

@app.route("/")
def home(): 
    return f"Bot Asistente Personal - Online - {datetime.now(TZ).strftime('%H:%M:%S')}"

def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    try: 
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def extraer_datos_tabla(html_content):
    info = {"puesto": "", "turnos": "0", "lugar": "", "indicaciones": ""}
    try:
        p_match = re.search(r'PUESTO</td><td.*?>(.*?)</td>', html_content)
        if p_match: info['puesto'] = p_match.group(1).strip().upper()
        
        l_match = re.search(r'LUGAR</td><td.*?>(.*?)</td>', html_content)
        if l_match: info['lugar'] = l_match.group(1).strip().upper()

        i_match = re.search(r'INDICACIONES</td><td.*?>(.*?)</td>', html_content)
        if i_match: info['indicaciones'] = i_match.group(1).strip().upper()
        
        h_match = re.search(r'HORARIO</td><td.*?>(.*?)</td>', html_content, re.DOTALL)
        if h_match:
            texto_h = h_match.group(1)
            t_match = re.search(r'TURNOS\s*(\d+\.?\d*)', texto_h, re.IGNORECASE)
            if t_match: info['turnos'] = t_match.group(1)
    except: pass
    return info

def analizar_filtros(info, titulo_card):
    titulo = titulo_card.upper()
    puesto = info['puesto']
    turnos = info['turnos']
    lugar = info['lugar']
    todo_texto = (titulo + info['indicaciones']).upper()

    # --- FILTRO 1: PEPSI CENTER WTC ---
    if "PEPSI CENTER" in titulo or "PEPSI CENTER" in lugar:
        if puesto in PUESTOS_PEPSI:
            return True, "PEPSI CENTER (Auto-Confirmar)", True

    # --- FILTRO 2: TYLER THE CREATOR (24 y 25 de Marzo) ---
    if "TYLER THE CREATOR" in titulo:
        es_fecha_correcta = "24/03/2026" in todo_texto or "25/03/2026" in todo_texto
        no_es_acreditacion = "ACREDITACION" not in todo_texto and "ACREDITACIONES" not in todo_texto
        
        if es_fecha_correcta and no_es_acreditacion and turnos == "1" and puesto in PUESTOS_CONCIERTOS:
            return True, f"TYLER ({puesto}) - Auto", True

    # --- FILTRO 3: DEFTONES (29 de Marzo) ---
    if "DEFTONES" in titulo:
        es_fecha_deftones = "29/03/2026" in todo_texto
        no_es_acreditacion = "ACREDITACION" not in todo_texto and "ACREDITACIONES" not in todo_texto
        
        if es_fecha_deftones and no_es_acreditacion and puesto in PUESTOS_CONCIERTOS:
            # Prioridad Acomodador EE (Igual confirma pero lo marcamos en el log)
            motivo = "DEFTONES (ACOMODADOR EE) - Prioridad" if puesto == "ACOMODADOR EE" else "DEFTONES - Auto"
            return True, motivo, True

    # --- NOTIFICACIÓN MANUAL PARA TODO LO DEMÁS ---
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
                # Ignorar si ya está en confirmados
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
                            send(f"🎯 *EVENTO CONFIRMADO AUTOMÁTICAMENTE*\n📌 {titulo_texto}\n👤 Puesto: {info['puesto']}\n✅ Motivo: {motivo}")
                        else:
                            send(f"⚠️ *ATENCIÓN:* Criterios OK para {titulo_texto} pero no hallé el botón.")
                    else:
                        send(f"🔔 *NUEVO EVENTO DISPONIBLE:* {titulo_texto}\n👉 Puesto: {info['puesto']}\n(Revisión Manual)")
            
            browser.close()
    except Exception as e:
        print(f"Error en el ciclo: {e}")

def monitor_loop():
    while True:
        run_once()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    threading.Thread(target=monitor_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
