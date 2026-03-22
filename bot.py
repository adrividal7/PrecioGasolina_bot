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
    print("❌ ERROR: Configura TELEGRAM_TOKEN en Render.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# Caché y Variables
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 
busquedas_usuarios = {}

# --- 1. GEOLOCALIZACIÓN MEJORADA ---
def obtener_coordenadas(direccion):
    """Busca coordenadas. Si es una calle, Nominatim es la clave."""
    # Intentamos con España por defecto si no lo incluye
    query = f"{direccion}, España" if "España" not in direccion else direccion
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
    
    # IMPORTANTE: User-Agent único para evitar bloqueos
    headers = {'User-Agent': f'BotGasolinera_Render_{TOKEN[:10]}'}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data:
            lat = float(data[0]['lat'])
            lon = float(data[0]['lon'])
            print(f"✅ Localizado: {direccion} -> ({lat}, {lon})")
            return lat, lon
    except Exception as e:
        print(f"⚠️ Error Nominatim: {e}")
    return None

# --- 2. GESTIÓN DE DATOS ---
def actualizar_datos_ministerio():
    try:
        print("📥 Descargando precios del Ministerio...")
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        r = requests.get(API_URL, headers=headers, verify=False, timeout=90)
        if r.status_code == 200:
            cache['datos'] = r.json().get('ListaEESSPrecio', [])
            cache['ultima_actualizacion'] = time.time()
            print(f"✅ Datos cargados: {len(cache['datos'])} gasolineras.")
            return True
    except Exception as e:
        print(f"❌ Error descarga: {e}")
    return False

def bucle_datos():
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

# --- 3. CÁLCULOS ---
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# --- 4. FLUJO DEL BOT ---
@bot.message_handler(commands=['start'])
def inicio(message):
    bot.reply_to(message, "⛽️ ¡Hola! Dime una **calle, municipio o lugar** (ej: _Calle Mayor Madrid_ o _Getafe_) o envía tu ubicación 📎.")

@bot.message_handler(content_types=['text'])
def manejar_texto(message):
    texto = message.text
    bot.send_chat_action(message.chat.id, 'find_location')
    
    coords = obtener_coordenadas(texto)
    
    if coords:
        busquedas_usuarios[message.chat.id] = {'tipo': 'gps', 'lat': coords[0], 'lon': coords[1]}
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("📍 3 km", callback_data="d_3"),
            InlineKeyboardButton("📍 6 km", callback_data="d_6"),
            InlineKeyboardButton("📍 12 km", callback_data="d_12")
        )
        bot.send_message(message.chat.id, f"📍 Ubicación encontrada.\n¿A qué distancia busco gasolineras?", reply_markup=markup)
    else:
        # Si falla el GPS, buscamos por texto en Municipio o Dirección
        busquedas_usuarios[message.chat.id] = {'tipo': 'texto', 'valor': texto.upper()}
        preguntar_combustible(message.chat.id)

@bot.message_handler(content_types=['location'])
def manejar_gps(message):
    busquedas_usuarios[message.chat.id] = {'tipo': 'gps', 'lat': message.location.latitude, 'lon': message.location.longitude}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📍 5 km", callback_data="d_5"), InlineKeyboardButton("📍 10 km", callback_data="d_10"))
    bot.send_message(message.chat.id, "¿Radio de búsqueda?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('d_'))
def set_dist(call):
    chat_id = call.message.chat.id
    if chat_id in busquedas_usuarios:
        busquedas_usuarios[chat_id]['radio'] = float(call.data.split('_')[1])
        preguntar_combustible(chat_id, call.message.message_id)

def preguntar_combustible(chat_id, mid=None):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⛽️ 95", callback_data="f_Precio Gasolina 95 E5"),
        InlineKeyboardButton("🛢 Diésel", callback_data="f_Precio Gasoleo A"),
        InlineKeyboardButton("🚀 98", callback_data="f_Precio Gasolina 98 E5")
    )
    if mid: bot.edit_message_text("Selecciona combustible:", chat_id=chat_id, message_id=mid, reply_markup=markup)
    else: bot.send_message(chat_id, "Selecciona combustible:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('f_'))
def buscar(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios: return

    tipo_f = call.data.replace('f_', '')
    bot.edit_message_text("Buscando las más baratas... ⏳", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = cache['datos']
    if not datos:
        bot.edit_message_text("❌ Datos no listos. Reintenta en 10s.", chat_id=chat_id, message_id=call.message.message_id)
        return

    busqueda = busquedas_usuarios[chat_id]
    encontradas = []

    for est in datos:
        try:
            p = float(est[tipo_f].replace(',','.'))
            lat_e = float(est['Latitud'].replace(',','.'))
            lon_e = float(est['Longitud (WGS84)'].replace(',','.'))
            
            # FILTRADO
            if busqueda['tipo'] == 'gps':
                dist = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_e, lon_e)
                if dist <= busqueda['radio']:
                    encontradas.append({'r': est['Rótulo'], 'p': p, 'd': est['Dirección'], 'dist': dist, 'lat': lat_e, 'lon': lon_e})
            else:
                # Búsqueda por texto (municipio o dirección)
                val = busqueda['valor']
                if val in est['Municipio'].upper() or val in est['Dirección'].upper():
                    encontradas.append({'r': est['Rótulo'], 'p': p, 'd': est['Dirección'], 'lat': lat_e, 'lon': lon_e})
        except: continue

    encontradas.sort(key=lambda x: x['p'])
    busqueda['res'] = encontradas
    mostrar_res(chat_id, call.message.message_id, 0)

def mostrar_res(chat_id, mid, pag):
    b = busquedas_usuarios[chat_id]
    res = b.get('res', [])
    if not res:
        bot.edit_message_text("❌ No he encontrado nada. Prueba con un radio mayor o revisa el nombre.", chat_id=chat_id, message_id=mid)
        return

    items = 5
    total = math.ceil(len(res)/items)
    actual = res[pag*items : (pag+1)*items]
    
    txt = f"⛽️ *Resultados* (Pág {pag+1}/{total}):\n\n"
    for i, g in enumerate(actual, 1):
        dist = f" | 📏 {g['dist']:.1f}km" if 'dist' in g else ""
        map_link = f"https://www.google.com/maps?q={g['lat']},{g['lon']}"
        txt += f"{i}. *{g['p']}€* - [{g['r']}]({map_link}){dist}\n📍 _{g['d']}_\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pag > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"p_{pag-1}"))
    if pag < total-1: btns.append(InlineKeyboardButton("➡️", callback_data=f"p_{pag+1}"))
    if btns: markup.add(*btns)
    
    bot.edit_message_text(txt, chat_id=chat_id, message_id=mid, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('p_'))
def paginar(call):
    mostrar_res(call.message.chat.id, call.message.message_id, int(call.data.split('_')[1]))

# --- 5. SERVIDOR WEB Y ARRANQUE ---
class WebServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

def run_web():
    p = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', p), WebServer).serve_forever()

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=bucle_datos, daemon=True).start()
    print("🚀 Bot iniciado...")
    bot.infinity_polling()
