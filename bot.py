import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
import html
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

# --- 1. GEOLOCALIZACIÓN Y RUTAS ---
def obtener_coordenadas(direccion):
    """Busca hasta 5 coincidencias para una dirección dada."""
    query = f"{direccion}, España" if "España" not in direccion else direccion
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=5"
    headers = {'User-Agent': f'GasolinerasBot_Rutas_{TOKEN[:5]}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        resultados = []
        if data:
            for item in data:
                # Acortamos el nombre para que quepa bien en el botón de Telegram
                nombre_corto = ", ".join(item.get('display_name', '').split(',')[0:3])
                resultados.append({
                    'lat': float(item['lat']), 
                    'lon': float(item['lon']),
                    'nombre': nombre_corto
                })
        return resultados
    except:
        pass
    return []

def obtener_distancia_coche(lat1, lon1, lat2, lon2):
    """Calcula la distancia precisa por carretera (en km) usando OSRM."""
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            data = r.json()
            if data and 'routes' in data and len(data['routes']) > 0:
                return data['routes'][0]['distance'] / 1000.0
    except:
        pass
    return None

def calcular_distancia_recta(lat1, lon1, lat2, lon2):
    """Calcula distancia rápida en línea recta como filtro previo."""
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

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

# --- 3. MENÚS ---
def enviar_menu_distancia(chat_id, titulo="📍 <b>Ubicación recibida</b>"):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📍 10 km", callback_data="dist_10"),
        InlineKeyboardButton("📍 20 km", callback_data="dist_20"),
        InlineKeyboardButton("📍 30 km", callback_data="dist_30")
    )
    bot.send_message(chat_id, f"{titulo}\n\n¿A qué distancia máxima en coche buscamos?", 
                     reply_markup=markup, parse_mode="HTML")

def preguntar_combustible(chat_id, message_id=None):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⛽️ 95", callback_data="fuel_Precio Gasolina 95 E5"),
        InlineKeyboardButton("🛢 Diésel", callback_data="fuel_Precio Gasoleo A"),
        InlineKeyboardButton("🚀 98", callback_data="fuel_Precio Gasolina 98 E5")
    )
    txt = "Selecciona el combustible:"
    if message_id:
        bot.edit_message_text(txt, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(chat_id, txt, reply_markup=markup, parse_mode="HTML")

# --- 4. RECEPCIÓN ---
@bot.message_handler(commands=['start', 'help'])
def bienvenida(message):
    texto = """¡Hola! ⛽️ Soy tu buscador de gasolineras baratas.

Puedes enviarme:
1. 📍 Tu <b>ubicación actual</b> (usando el clip 📎 de Telegram).
2. 🏠 Una <b>calle o sitio</b> (ej: <i>Gran Vía, Madrid</i>).
3. 🏙 Un <b>municipio</b> (ej: <i>Sevilla</i>)."""
    bot.reply_to(message, texto, parse_mode="HTML")

@bot.message_handler(content_types=['location', 'venue'])
def recibir_ubicacion_gps(message):
    chat_id = message.chat.id
    if message.location:
        lat, lon = message.location.latitude, message.location.longitude
    elif message.venue:
        lat, lon = message.venue.location.latitude, message.venue.location.longitude
    
    busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': lat, 'lon': lon}
    enviar_menu_distancia(chat_id, "📍 <b>Ubicación detectada correctamente</b>")

@bot.message_handler(content_types=['text'])
def recibir_texto(message):
    chat_id = message.chat.id
    texto = message.text
    bot.send_chat_action(chat_id, 'find_location')
    
    opciones_coords = obtener_coordenadas(texto)
    
    if opciones_coords:
        if len(opciones_coords) == 1:
            # Si solo hay 1 coincidencia
            busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': opciones_coords[0]['lat'], 'lon': opciones_coords[0]['lon']}
            enviar_menu_distancia(chat_id, f"📍 He localizado: <b>{html.escape(opciones_coords[0]['nombre'])}</b>")
        else:
            # Selector de opciones múltiples
            markup = InlineKeyboardMarkup()
            for i, loc in enumerate(opciones_coords):
                llave_temp = f"loc_{i}"
                busquedas_usuarios[f"{chat_id}_{llave_temp}"] = loc 
                markup.add(InlineKeyboardButton(loc['nombre'], callback_data=llave_temp))
            
            bot.send_message(chat_id, f"🔎 He encontrado varias opciones para <b>{html.escape(texto)}</b>.\nElige la correcta:", 
                             reply_markup=markup, parse_mode="HTML")
    else:
        busquedas_usuarios[chat_id] = {'tipo': 'texto', 'valor': texto.upper()}
        preguntar_combustible(chat_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('loc_'))
def seleccionar_opcion_calle(call):
    chat_id = call.message.chat.id
    llave = f"{chat_id}_{call.data}"
    
    if llave in busquedas_usuarios:
        seleccion = busquedas_usuarios[llave]
        busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': seleccion['lat'], 'lon': seleccion['lon']}
        bot.edit_message_text(f"📍 Seleccionado: <b>{html.escape(seleccion['nombre'])}</b>", 
                              chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML")
        enviar_menu_distancia(chat_id)
    else:
        bot.answer_callback_query(call.id, "Búsqueda caducada. Repite tu búsqueda.")

# --- 5. PROCESAMIENTO CON RUTAS PRECISAS ---
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
    bot.edit_message_text("🔍 Calculando rutas en coche y buscando precios... 🚗\n_(Esto tardará unos 2-3 segundos)_", 
                          chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML")
    
    datos = cache['datos']
    if not datos:
        bot.edit_message_text("❌ Datos no listos. Espera 10s.", chat_id=chat_id, message_id=call.message.message_id)
        return

    b = busquedas_usuarios[chat_id]
    candidatas = []

    # PASO 1: Filtrar todas las gasolineras usando línea recta (para descartar el 99% de España rápido)
    for est in datos:
        try:
            p = float(est[tipo_f].replace(',', '.'))
            lat_e = float(est['Latitud'].replace(',', '.'))
            lon_e = float(est['Longitud (WGS84)'].replace(',', '.'))
            
            if b['tipo'] == 'gps':
                dist_recta = calcular_distancia_recta(b['lat'], b['lon'], lat_e, lon_e)
                if dist_recta <= (b['radio'] * 1.5): # Damos margen para curvas de carretera
                    candidatas.append({'est': est, 'p': p, 'lat': lat_e, 'lon': lon_e, 'dist_recta': dist_recta})
            else:
                if b['valor'] in est['Municipio'].upper() or b['valor'] in est['Dirección'].upper():
                    candidatas.append({'est': est, 'p': p, 'lat': lat_e, 'lon': lon_e})
        except: continue

    encontradas = []

    # PASO 2: Calcular distancia REAL por carretera
    if b['tipo'] == 'gps':
        candidatas.sort(key=lambda x: x['dist_recta'])
        
        # Consultamos las rutas de las 12 más cercanas para no bloquear la API y agilizar
        for c in candidatas[:12]:
            dist_coche = obtener_distancia_coche(b['lat'], b['lon'], c['lat'], c['lon'])
            
            if dist_coche is not None:
                dist_final = dist_coche
                icono_dist = "🚗" # Marca que es ruta por coche
            else:
                dist_final = c['dist_recta']
                icono_dist = "📏" # Marca que es línea recta si falló el servidor de rutas
                
            time.sleep(0.1) # Micropausa de seguridad
            
            if dist_final <= b['radio']:
                encontradas.append({
                    'r': c['est']['Rótulo'], 'p': c['p'], 'd': c['est']['Dirección'], 
                    'dist': dist_final, 'lat': c['lat'], 'lon': c['lon'], 'icono': icono_dist
                })
    else:
        for c in candidatas:
            encontradas.append({'r': c['est']['Rótulo'], 'p': c['p'], 'd': c['est']['Dirección'], 'lat': c['lat'], 'lon': c['lon']})

    encontradas.sort(key=lambda x: x['p'])
    b['res'] = encontradas
    mostrar_resultados(chat_id, call.message.message_id, 0)

def mostrar_resultados(chat_id, mid, pag):
    res = busquedas_usuarios[chat_id].get('res', [])
    if not res:
        bot.edit_message_text("❌ No hay nada en ese radio (en coche). Prueba más km.", chat_id=chat_id, message_id=mid, parse_mode="HTML")
        return

    items = 5
    total = math.ceil(len(res) / items)
    lista = res[pag*items : (pag+1)*items]
    
    txt = f"⛽️ <b>Resultados más baratos</b> (Pág {pag+1}/{total}):\n\n"
    for i, g in enumerate(lista, 1):
        icono = g.get('icono', '')
        dist = f" | {icono} <b>{g['dist']:.1f} km</b>" if 'dist' in g else ""
        
        # Enlace para que Google Maps trace la ruta en el móvil del usuario
        map_link = f"https://www.google.com/maps/search/?api=1&query={g['lat']},{g['lon']}"
        
        rotulo = html.escape(g['r'])
        dir_segura = html.escape(g['d'])
        
        txt += f"{i}. <b>{g['p']}€</b> - <a href='{map_link}'>{rotulo}</a>{dist}\n📍 <i>{dir_segura}</i>\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pag > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"page_{pag-1}"))
    if pag < total - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"page_{pag+1}"))
    if btns: markup.add(*btns)
    
    bot.edit_message_text(txt, chat_id=chat_id, message_id=mid, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def paginar(call):
    mostrar_resultados(call.message.chat.id, call.message.message_id, int(call.data.split('_')[1]))

# --- 6. WEB SERVER Y ARRANQUE ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == '__main__':
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Health).serve_forever(), daemon=True).start()
    threading.Thread(target=bucle_actualizacion, daemon=True).start()
    print("🤖 Bot listo con rutas de coche precisas (OSRM), selectores múltiples y HTML...")
    bot.infinity_polling()
