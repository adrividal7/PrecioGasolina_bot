import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
from http.server import BaseHTTPRequestHandler, HTTPServer

# Configuración de seguridad
urllib3.disable_warnings()

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    print("❌ ERROR: No se detectó TELEGRAM_TOKEN.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# Memoria del bot
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 
busquedas_usuarios = {}

# --- 1. GEOLOCALIZACIÓN (Para texto) ---
def obtener_coordenadas(direccion):
    url = f"https://nominatim.openstreetmap.org/search?q={direccion}, España&format=json&limit=1"
    headers = {'User-Agent': f'GasolinerasBot_Final_{TOKEN[:5]}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except:
        pass
    return None

# --- 2. GESTIÓN DE DATOS ---
def actualizar_datos_ministerio():
    try:
        print("📥 Actualizando base de datos... ⏳")
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(API_URL, headers=headers, verify=False, timeout=120)
        if r.status_code == 200:
            cache['datos'] = r.json().get('ListaEESSPrecio', [])
            cache['ultima_actualizacion'] = time.time()
            print(f"✅ Datos cargados: {len(cache['datos'])} estaciones.")
            return True
    except Exception as e:
        print(f"❌ Error Ministerio: {e}")
    return False

def bucle_actualizacion():
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

# --- 3. CÁLCULO DISTANCIA ---
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# --- 4. MENÚS ---
def enviar_menu_distancia(chat_id, titulo="📍 Ubicación recibida"):
    markup = InlineKeyboardMarkup()
    # UNIFICADO A 10, 20, 30 KM
    markup.add(
        InlineKeyboardButton("📍 10 km", callback_data="dist_10"),
        InlineKeyboardButton("📍 20 km", callback_data="dist_20"),
        InlineKeyboardButton("📍 30 km", callback_data="dist_30")
    )
    bot.send_message(chat_id, f"{titulo}\n\n¿A qué distancia máxima quieres buscar?", 
                     reply_markup=markup, parse_mode="Markdown")

def preguntar_combustible(chat_id, message_id=None):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⛽️ 95", callback_data="fuel_Precio Gasolina 95 E5"),
        InlineKeyboardButton("🛢 Diésel", callback_data="fuel_Precio Gasoleo A"),
        InlineKeyboardButton("🚀 98", callback_data="fuel_Precio Gasolina 98 E5")
    )
    txt = "Selecciona el combustible:"
    if message_id:
        bot.edit_message_text(txt, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, txt, reply_markup=markup)

# --- 5. RECEPCIÓN (EL CAMBIO IMPORTANTE ESTÁ AQUÍ) ---
@bot.message_handler(commands=['start', 'help'])
def bienvenida(message):
    texto = """¡Hola! ⛽️ Soy tu buscador de gasolineras baratas.

Puedes enviarme:
1. 📍 Tu <b>ubicación actual</b> (usando el clip 📎 de Telegram).
2. 🏠 Una <b>calle o sitio</b> (ej: <i>Gran Vía, Madrid</i>).
3. 🏙 Un <b>municipio</b> (ej: <i>Sevilla</i>)."""
    bot.reply_to(message, texto, parse_mode="HTML")
    
# AHORA ACEPTA 'LOCATION' Y 'VENUE' (Sitios específicos)
@bot.message_handler(content_types=['location', 'venue'])
def recibir_ubicacion_gps(message):
    chat_id = message.chat.id
    # Extraemos lat/lon sea cual sea el tipo de mensaje de mapa
    if message.location:
        lat, lon = message.location.latitude, message.location.longitude
    elif message.venue:
        lat, lon = message.venue.location.latitude, message.venue.location.longitude
    
    busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': lat, 'lon': lon}
    enviar_menu_distancia(chat_id, "📍 *Ubicación detectada correctamente*")

@bot.message_handler(content_types=['text'])
def recibir_texto(message):
    chat_id = message.chat.id
    texto = message.text
    bot.send_chat_action(chat_id, 'find_location')
    
    coords = obtener_coordenadas(texto)
    if coords:
        busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': coords[0], 'lon': coords[1]}
        enviar_menu_distancia(chat_id, f"📍 He localizado: *{texto}*")
    else:
        busquedas_usuarios[chat_id] = {'tipo': 'texto', 'valor': texto.upper()}
        preguntar_combustible(chat_id)

# --- 6. PROCESAMIENTO ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def set_distancia(call):
    chat_id = call.message.chat.id
    if chat_id in busquedas_usuarios:
        busquedas_usuarios[chat_id]['radio'] = float(call.data.split('_')[1])
        preguntar_combustible(chat_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('fuel_'))
def buscar(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios: return

    tipo_f = call.data.replace('fuel_', '')
    bot.edit_message_text("🔍 Buscando precios...", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = cache['datos']
    if not datos:
        bot.edit_message_text("❌ Datos no listos. Espera 10s.", chat_id=chat_id, message_id=call.message.message_id)
        return

    b = busquedas_usuarios[chat_id]
    encontradas = []

    for est in datos:
        try:
            p = float(est[tipo_f].replace(',', '.'))
            lat_e = float(est['Latitud'].replace(',', '.'))
            lon_e = float(est['Longitud (WGS84)'].replace(',', '.'))
            
            if b['tipo'] == 'gps':
                dist = calcular_distancia(b['lat'], b['lon'], lat_e, lon_e)
                if dist <= b['radio']:
                    encontradas.append({'r': est['Rótulo'], 'p': p, 'd': est['Dirección'], 'dist': dist, 'lat': lat_e, 'lon': lon_e})
            else:
                if b['valor'] in est['Municipio'].upper() or b['valor'] in est['Dirección'].upper():
                    encontradas.append({'r': est['Rótulo'], 'p': p, 'd': est['Dirección'], 'lat': lat_e, 'lon': lon_e})
        except: continue

    encontradas.sort(key=lambda x: x['p'])
    b['res'] = encontradas
    mostrar_resultados(chat_id, call.message.message_id, 0)

def mostrar_resultados(chat_id, mid, pag):
    res = busquedas_usuarios[chat_id].get('res', [])
    if not res:
        bot.edit_message_text("❌ No hay nada en ese radio. Prueba con 20 o 30 km.", chat_id=chat_id, message_id=mid)
        return

    items = 5
    total = math.ceil(len(res) / items)
    lista = res[pag*items : (pag+1)*items]
    
    txt = f"⛽️ *Resultados* (Pág {pag+1}/{total}):\n\n"
    for i, g in enumerate(lista, 1):
        dist = f" | 📏 {g['dist']:.1f}km" if 'dist' in g else ""
        map_link = f"https://www.google.com/maps/search/?api=1&query={g['lat']},{g['lon']}"
        txt += f"{i}. *{g['p']}€* - [{g['r']}]({map_link}){dist}\n📍 _{g['d']}_\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pag > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"page_{pag-1}"))
    if pag < total - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"page_{pag+1}"))
    if btns: markup.add(*btns)
    
    bot.edit_message_text(txt, chat_id=chat_id, message_id=mid, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def paginar(call):
    mostrar_resultados(call.message.chat.id, call.message.message_id, int(call.data.split('_')[1]))

# --- 7. WEB SERVER Y ARRANQUE ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == '__main__':
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Health).serve_forever(), daemon=True).start()
    threading.Thread(target=bucle_actualizacion, daemon=True).start()
    print("🤖 Bot listo con soporte para Venues y distancias 10-20-30km...")
    bot.infinity_polling()
