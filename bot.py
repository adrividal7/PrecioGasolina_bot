import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
from http.server import BaseHTTPRequestHandler, HTTPServer

# Configuración de seguridad para evitar avisos innecesarios
urllib3.disable_warnings()

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    print("❌ ERROR: No se detectó TELEGRAM_TOKEN en las variables de entorno de Render.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# Memoria temporal y configuración
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 # 30 minutos
busquedas_usuarios = {}

# --- 1. GEOLOCALIZACIÓN (Para calles y sitios) ---
def obtener_coordenadas(direccion):
    """Convierte texto en coordenadas usando Nominatim (OpenStreetMap)"""
    query = f"{direccion}, España" if "España" not in direccion else direccion
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
    headers = {'User-Agent': f'GasolinerasBot_Final_{TOKEN[:5]}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except:
        pass
    return None

# --- 2. GESTIÓN DE DATOS (Background Task para Render) ---
def actualizar_datos_ministerio():
    """Descarga los datos en segundo plano para que el bot responda al instante"""
    try:
        print("📥 Descargando precios actualizados... ⏳")
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(API_URL, headers=headers, verify=False, timeout=120)
        if r.status_code == 200:
            cache['datos'] = r.json().get('ListaEESSPrecio', [])
            cache['ultima_actualizacion'] = time.time()
            print(f"✅ Base de datos cargada: {len(cache['datos'])} estaciones.")
            return True
    except Exception as e:
        print(f"❌ Error al conectar con el Ministerio: {e}")
    return False

def bucle_actualizacion_datos():
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

# --- 3. CÁLCULO DE DISTANCIA ---
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# --- 4. MENÚS DE INTERACCIÓN ---
def enviar_menu_distancia(chat_id, titulo="📍 Ubicación recibida"):
    markup = InlineKeyboardMarkup()
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
    texto = "Selecciona el combustible para comparar precios:"
    if message_id:
        bot.edit_message_text(texto, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, texto, reply_markup=markup)

# --- 5. RECEPCIÓN DE MENSAJES ---
@bot.message_handler(commands=['start', 'help'])
def bienvenida(message):
    bot.reply_to(message, "¡Hola! ⛽️ Soy tu buscador de gasolineras baratas.\n\n"
                          "Puedes enviarme:\n"
                          "1. 📍 Tu **ubicación actual** (usando el clip 📎 de Telegram).\n"
                          "2. 🏠 Una **calle o sitio** (ej: _Gran Vía, Madrid_).\n"
                          "3. 🏙 Un **municipio** (ej: _Sevilla_).")

@bot.message_handler(content_types=['location'])
def recibir_ubicacion_gps(message):
    chat_id = message.chat.id
    busquedas_usuarios[chat_id] = {
        'tipo': 'gps', 
        'lat': message.location.latitude, 
        'lon': message.location.longitude
    }
    enviar_menu_distancia(chat_id, "📍 *Ubicación GPS detectada correctamente*")

@bot.message_handler(content_types=['text'])
def recibir_texto(message):
    chat_id = message.chat.id
    texto = message.text
    bot.send_chat_action(chat_id, 'find_location')
    
    # Intentamos ver si es una calle o lugar específico
    coords = obtener_coordenadas(texto)
    
    if coords:
        busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': coords[0], 'lon': coords[1]}
        enviar_menu_distancia(chat_id, f"📍 He localizado: *{texto}*")
    else:
        # Si no lo reconoce el mapa, lo tratamos como nombre de municipio
        busquedas_usuarios[chat_id] = {'tipo': 'texto', 'valor': texto.upper()}
        preguntar_combustible(chat_id)

# --- 6. PROCESAMIENTO DE BÚSQUEDA ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def set_distancia(call):
    chat_id = call.message.chat.id
    if chat_id in busquedas_usuarios:
        busquedas_usuarios[chat_id]['radio'] = float(call.data.split('_')[1])
        preguntar_combustible(chat_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('fuel_'))
def procesar_y_buscar(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Error: Inicia la búsqueda de nuevo.")
        return

    tipo_f = call.data.replace('fuel_', '')
    bot.edit_message_text("🔍 Buscando los mejores precios... esto tardará un segundo.", 
                          chat_id=chat_id, message_id=call.message.message_id)
    
    datos = cache['datos']
    if not datos:
        bot.edit_message_text("❌ Los datos aún se están cargando. Prueba en 10 segundos.", 
                              chat_id=chat_id, message_id=call.message.message_id)
        return

    busqueda = busquedas_usuarios[chat_id]
    encontradas = []

    for est in datos:
        try:
            # Limpiar y convertir precio
            p_str = est[tipo_f].replace(',', '.')
            if not p_str: continue
            precio = float(p_str)
            
            lat_e = float(est['Latitud'].replace(',', '.'))
            lon_e = float(est['Longitud (WGS84)'].replace(',', '.'))
            
            if busqueda['tipo'] == 'gps':
                dist = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_e, lon_e)
                if dist <= busqueda['radio']:
                    encontradas.append({'r': est['Rótulo'], 'p': precio, 'd': est['Dirección'], 'dist': dist, 'lat': lat_e, 'lon': lon_e})
            else:
                # Búsqueda por nombre de municipio o dirección
                if busqueda['valor'] in est['Municipio'].upper() or busqueda['valor'] in est['Dirección'].upper():
                    encontradas.append({'r': est['Rótulo'], 'p': precio, 'd': est['Dirección'], 'lat': lat_e, 'lon': lon_e})
        except:
            continue

    encontradas.sort(key=lambda x: x['p'])
    busqueda['res'] = encontradas
    mostrar_resultados(chat_id, call.message.message_id, 0)

def mostrar_resultados(chat_id, message_id, pagina):
    res = busquedas_usuarios[chat_id].get('res', [])
    if not res:
        bot.edit_message_text("❌ No he encontrado gasolineras. Prueba con un radio mayor o revisa el nombre del municipio.", 
                              chat_id=chat_id, message_id=message_id)
        return

    items_por_pag = 5
    total_paginas = math.ceil(len(res) / items_por_pag)
    inicio = pagina * items_por_pag
    fin = inicio + items_por_pag
    lista_actual = res[inicio:fin]
    
    txt = f"⛽️ *Gasolineras más baratas* (Pág {pagina+1}/{total_paginas}):\n\n"
    for i, g in enumerate(lista_actual, 1):
        dist_txt = f" | 📏 {g['dist']:.1f}km" if 'dist' in g else ""
        # Enlace a Google Maps
        map_link = f"https://www.google.com/maps/search/?api=1&query={g['lat']},{g['lon']}"
        txt += f"{i}. *{g['p']}€* - [{g['r']}]({map_link}){dist_txt}\n📍 _{g['d']}_\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pagina > 0:
        btns.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"page_{pagina-1}"))
    if pagina < total_paginas - 1:
        btns.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"page_{pagina+1}"))
    
    if btns:
        markup.add(*btns)
    
    bot.edit_message_text(txt, chat_id=chat_id, message_id=message_id, 
                          parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def cambiar_pagina(call):
    chat_id = call.message.chat.id
    nueva_pag = int(call.data.split('_')[1])
    mostrar_resultados(chat_id, call.message.message_id, nueva_pag)

# --- 7. SERVIDOR WEB (KEEP-ALIVE PARA RENDER) ---
class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Online")

def ejecutar_servidor():
    puerto = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', puerto), HealthCheck).serve_forever()

# --- 8. ARRANQUE ---
if __name__ == '__main__':
    # Lanzar servidor web (Render necesita esto)
    threading.Thread(target=ejecutar_servidor, daemon=True).start()
    
    # Lanzar actualizador de precios (Segundo plano)
    threading.Thread(target=bucle_actualizacion_datos, daemon=True).start()
    
    print("🤖 El bot está funcionando. Esperando mensajes en Telegram...")
    bot.infinity_polling()
