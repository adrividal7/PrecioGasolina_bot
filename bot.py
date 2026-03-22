import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
from http.server import BaseHTTPRequestHandler, HTTPServer

# Desactivar advertencias de certificados del Ministerio
urllib3.disable_warnings()

# 1. Configuración y Seguridad
TOKEN = os.environ.get('TELEGRAM_TOKEN')

if not TOKEN:
    print("¡ERROR! No se ha encontrado el Token. Configura la variable TELEGRAM_TOKEN en Render.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# 2. Caché y Variables Globales
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800  # 30 minutos
busquedas_usuarios = {}

# 3. Geocodificación (Traductor de Calles/Sitios a Coordenadas)
def obtener_coordenadas(direccion):
    """Convierte un texto (calle, monumento, plaza) en coordenadas GPS."""
    # Añadimos 'España' para que la búsqueda sea más precisa
    url = f"https://nominatim.openstreetmap.org/search?q={direccion}, España&format=json&limit=1"
    headers = {'User-Agent': 'MiGasolineraBot_Asistente/1.0'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon']), data[0]['display_name']
    except Exception as e:
        print(f"Error en geocodificación: {e}")
    return None

# 4. Descarga de Datos en Segundo Plano
def actualizar_datos_ministerio():
    """Descarga los datos pesados sin bloquear al usuario."""
    try:
        print("Iniciando descarga masiva desde el Ministerio... ⏳")
        cabeceras = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://geoportalgasolineras.es/'
        }
        # Timeout de 90 segundos para servidores lentos
        respuesta = requests.get(API_URL, headers=cabeceras, verify=False, timeout=90)
        
        if respuesta.status_code == 200:
            nuevos_datos = respuesta.json().get('ListaEESSPrecio', [])
            if nuevos_datos:
                cache['datos'] = nuevos_datos
                cache['ultima_actualizacion'] = time.time()
                print(f"¡ÉXITO! {len(nuevos_datos)} gasolineras cargadas. ✅")
                return True
        else:
            print(f"❌ Error HTTP del Ministerio: {respuesta.status_code}")
    except Exception as e:
        print(f"❌ Error crítico en la descarga: {e}")
    return False

def bucle_actualizacion_continua():
    """Mantiene la caché fresca cada 30 minutos."""
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

def obtener_datos():
    """Devuelve los datos de la memoria."""
    if cache['datos'] is None:
        actualizar_datos_ministerio()
    return cache['datos']

# 5. Auxiliares Matemáticas
def limpiar_precio(precio_str):
    if not precio_str: return float('inf')
    return float(precio_str.replace(',', '.'))

def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# 6. Manejo de Comandos y Texto
@bot.message_handler(commands=['start', 'help'])
def enviar_bienvenida(message):
    texto = ("¡Hola! ⛽️\n\n"
             "Puedes enviarme:\n"
             "1. Una **calle o sitio** (ej: Calle Mayor Madrid).\n"
             "2. Tu **ubicación actual** (usando el clip 📎).\n"
             "3. Un **municipio** (ej: Getafe).")
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(content_types=['text'])
def recibir_texto(message):
    texto = message.text
    bot.send_chat_action(message.chat.id, 'find_location')
    
    # Intentamos ver si es una calle o sitio específico
    res_geo = obtener_coordenadas(texto)
    
    if res_geo:
        lat, lon, nombre_completo = res_geo
        busquedas_usuarios[message.chat.id] = {'tipo': 'ubicacion', 'lat': lat, 'lon': lon}
        
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("📍 3 km", callback_data="dist_3"),
            InlineKeyboardButton("📍 5 km", callback_data="dist_5"),
            InlineKeyboardButton("📍 10 km", callback_data="dist_10")
        )
        bot.send_message(message.chat.id, f"📍 He encontrado: *{texto}*\n¿En qué radio busco?", 
                         reply_markup=markup, parse_mode="Markdown")
    else:
        # Si no lo encuentra como punto GPS, lo guarda como nombre de municipio
        busquedas_usuarios[message.chat.id] = {'tipo': 'municipio', 'valor': texto.upper()}
        preguntar_combustible(message.chat.id)

@bot.message_handler(content_types=['location'])
def recibir_ubicacion_gps(message):
    busquedas_usuarios[message.chat.id] = {
        'tipo': 'ubicacion', 
        'lat': message.location.latitude, 
        'lon': message.location.longitude
    }
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📍 5 km", callback_data="dist_5"),
        InlineKeyboardButton("📍 10 km", callback_data="dist_10"),
        InlineKeyboardButton("📍 20 km", callback_data="dist_20")
    )
    bot.send_message(message.chat.id, "¿A qué distancia máxima buscamos?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def guardar_distancia(call):
    chat_id = call.message.chat.id
    if chat_id in busquedas_usuarios:
        busquedas_usuarios[chat_id]['distancia_max'] = float(call.data.split('_')[1])
        preguntar_combustible(chat_id, call.message.message_id)

def preguntar_combustible(chat_id, message_id=None):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⛽️ 95", callback_data="fuel_Precio Gasolina 95 E5"),
        InlineKeyboardButton("🚀 98", callback_data="fuel_Precio Gasolina 98 E5"),
        InlineKeyboardButton("🛢 Diésel", callback_data="fuel_Precio Gasoleo A")
    )
    texto = "¿Qué combustible quieres comparar?"
    if message_id:
        bot.edit_message_text(texto, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, texto, reply_markup=markup)

# 7. Procesamiento y Resultados
@bot.callback_query_handler(func=lambda call: call.data.startswith('fuel_'))
def procesar_busqueda(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios: return

    busqueda = busquedas_usuarios[chat_id]
    tipo_combustible = call.data.replace('fuel_', '')
    busqueda['nombre_combustible'] = "Gasolina 95" if "95" in tipo_combustible else ("Gasolina 98" if "98" in tipo_combustible else "Diésel")
    
    bot.edit_message_text("Buscando en la base de datos... ⏳", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = obtener_datos()
    if not datos:
        bot.edit_message_text("❌ Los datos se están descargando. Prueba en 10 segundos.", chat_id=chat_id, message_id=call.message.message_id)
        return

    gasolineras = []
    for est in datos:
        precio = limpiar_precio(est[tipo_combustible])
        if precio == float('inf'): continue
            
        try:
            lat_e, lon_e = float(est['Latitud'].replace(',','.')), float(est['Longitud (WGS84)'].replace(',','.'))
            map_url = f"https://www.google.com/maps/search/?api=1&query={lat_e},{lon_e}"
        except: map_url = None
            
        if busqueda['tipo'] == 'municipio':
            if busqueda['valor'] in est['Municipio'].upper():
                gasolineras.append({'rotulo': est['Rótulo'], 'precio': precio, 'dir': est['Dirección'], 'url': map_url})
        else:
            dist = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_e, lon_e)
            if dist <= busqueda['distancia_max']:
                gasolineras.append({'rotulo': est['Rótulo'], 'precio': precio, 'dir': est['Dirección'], 'dist': dist, 'url': map_url})

    gasolineras.sort(key=lambda x: x['precio'])
    busqueda['resultados'] = gasolineras
    mostrar_pagina(chat_id, call.message.message_id, 0)

def mostrar_pagina(chat_id, message_id, pagina):
    busqueda = busquedas_usuarios.get(chat_id)
    res = busqueda.get('resultados', [])
    if not res:
        bot.edit_message_text("No he encontrado nada cerca. Intenta con más distancia.", chat_id=chat_id, message_id=message_id)
        return

    por_pag = 5
    total_pags = math.ceil(len(res) / por_pag)
    lista = res[pagina*por_pag : (pagina+1)*por_pag]

    texto = f"⛽️ *{busqueda['nombre_combustible']}* (Pág {pagina+1}/{total_pags}):\n\n"
    for i, g in enumerate(lista, start=(pagina*por_pag)+1):
        dist_txt = f" | 📏 {g['dist']:.1f} km" if 'dist' in g else ""
        texto += f"{i}. *{g['precio']}€* - [{g['rotulo']}]({g['url']}){dist_txt}\n📍 _{g['dir']}_\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pagina > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"page_{pagina-1}"))
    if pagina < total_pags - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"page_{pagina+1}"))
    if btns: markup.add(*btns)

    bot.edit_message_text(texto, chat_id=chat_id, message_id=message_id, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def cambiar_pagina(call):
    mostrar_pagina(call.message.chat.id, call.message.message_id, int(call.data.split('_')[1]))

# 8. Servidor Web (Keep-Alive para Render)
class Manejador(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot funcionando")

def iniciar_servidor():
    puerto = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', puerto), Manejador).serve_forever()

# 9. Ejecución
if __name__ == '__main__':
    threading.Thread(target=iniciar_servidor, daemon=True).start()
    threading.Thread(target=bucle_actualizacion_continua, daemon=True).start()
    print("🤖 Bot listo. Esperando mensajes...")
    bot.infinity_polling()
