import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
from http.server import BaseHTTPRequestHandler, HTTPServer

# Desactivar advertencias de certificados (el Ministerio a veces falla en esto)
urllib3.disable_warnings()

# 1. Configuración básica y Seguridad
TOKEN = os.environ.get('TELEGRAM_TOKEN')

if not TOKEN:
    print("¡ERROR! No se ha encontrado el Token. Configura la variable TELEGRAM_TOKEN.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# 2. Sistema de Caché y Estado
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800  # 30 minutos
busquedas_usuarios = {}

# 3. Funciones de descarga (Segundo Plano)
def actualizar_datos_ministerio():
    """Descarga los datos del Ministerio y los guarda en la caché global."""
    try:
        print("Iniciando descarga masiva desde el Ministerio... ⏳")
        cabeceras = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://geoportalgasolineras.es/'
        }
        # Timeout largo de 90s para evitar fallos en servidores lentos
        respuesta = requests.get(API_URL, headers=cabeceras, verify=False, timeout=90)
        
        if respuesta.status_code == 200:
            nuevos_datos = respuesta.json().get('ListaEESSPrecio', [])
            if nuevos_datos:
                cache['datos'] = nuevos_datos
                cache['ultima_actualizacion'] = time.time()
                print(f"¡ÉXITO! {len(nuevos_datos)} gasolineras cargadas. ✅")
                return True
        else:
            print(f"❌ Error HTTP: {respuesta.status_code}")
    except Exception as e:
        print(f"❌ Error crítico en la descarga: {e}")
    return False

def bucle_actualizacion_continua():
    """Hilo que actualiza la caché cada 30 minutos."""
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

def obtener_datos():
    """Devuelve los datos de la caché. Si está vacía, intenta descargar."""
    if cache['datos'] is None:
        actualizar_datos_ministerio()
    return cache['datos']

# 4. Funciones Auxiliares Matemáticas
def limpiar_precio(precio_str):
    if not precio_str:
        return float('inf')
    return float(precio_str.replace(',', '.'))

def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2)**2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 5. Manejo de Mensajes y Comandos
@bot.message_handler(commands=['start', 'help'])
def enviar_bienvenida(message):
    texto = ("¡Hola! ⛽️ Soy tu asistente de gasolina.\n\n"
             "Envíame el nombre de tu **municipio** o tu **ubicación actual**.")
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(content_types=['text'])
def recibir_municipio(message):
    busquedas_usuarios[message.chat.id] = {'tipo': 'municipio', 'valor': message.text.upper()}
    preguntar_combustible(message.chat.id)

@bot.message_handler(content_types=['location'])
def recibir_ubicacion(message):
    busquedas_usuarios[message.chat.id] = {
        'tipo': 'ubicacion', 
        'lat': message.location.latitude, 
        'lon': message.location.longitude
    }
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📍 10 km", callback_data="dist_10"),
        InlineKeyboardButton("📍 20 km", callback_data="dist_20"),
        InlineKeyboardButton("📍 30 km", callback_data="dist_30")
    )
    bot.send_message(message.chat.id, "¿En qué radio de distancia busco?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def guardar_distancia(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Búsqueda caducada.")
        return
    busquedas_usuarios[chat_id]['distancia_max'] = float(call.data.split('_')[1])
    preguntar_combustible(chat_id, call.message.message_id)

def preguntar_combustible(chat_id, message_id=None):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⛽️ 95", callback_data="fuel_Precio Gasolina 95 E5"),
        InlineKeyboardButton("🚀 98", callback_data="fuel_Precio Gasolina 98 E5"),
        InlineKeyboardButton("🛢 Diésel", callback_data="fuel_Precio Gasoleo A")
    )
    texto = "¿Qué combustible utilizas?"
    if message_id:
        bot.edit_message_text(texto, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, texto, reply_markup=markup)

# 6. Procesar Resultados y Paginación
@bot.callback_query_handler(func=lambda call: call.data.startswith('fuel_'))
def procesar_busqueda(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Búsqueda caducada.")
        return

    busqueda = busquedas_usuarios[chat_id]
    tipo_combustible = call.data.replace('fuel_', '')
    busqueda['nombre_combustible'] = "Gasolina 95" if "95" in tipo_combustible else ("Gasolina 98" if "98" in tipo_combustible else "Diésel")
    
    bot.edit_message_text("Calculando los mejores precios... ⏳", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = obtener_datos()
    if not datos:
        bot.edit_message_text("❌ Error temporal con los datos. Reintenta en 1 min.", chat_id=chat_id, message_id=call.message.message_id)
        return

    gasolineras = []
    for estacion in datos:
        precio = limpiar_precio(estacion[tipo_combustible])
        if precio == float('inf'): continue
            
        try:
            lat_est = float(estacion['Latitud'].replace(',', '.'))
            lon_est = float(estacion['Longitud (WGS84)'].replace(',', '.'))
            map_url = f"https://www.google.com/maps?q={lat_est},{lon_est}"
        except: map_url = None
            
        if busqueda['tipo'] == 'municipio' and estacion['Municipio'] == busqueda['valor']:
            gasolineras.append({'direccion': estacion['Dirección'], 'rotulo': estacion['Rótulo'], 'precio': precio, 'map_url': map_url})
        elif busqueda['tipo'] == 'ubicacion':
            dist = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_est, lon_est)
            if dist <= busqueda['distancia_max']:
                gasolineras.append({'direccion': estacion['Dirección'], 'rotulo': estacion['Rótulo'], 'precio': precio, 'distancia': dist, 'map_url': map_url})

    gasolineras.sort(key=lambda x: x['precio'])
    busqueda['resultados_completos'] = gasolineras
    mostrar_pagina(chat_id, call.message.message_id, 0)

def mostrar_pagina(chat_id, message_id, pagina):
    busqueda = busquedas_usuarios.get(chat_id)
    resultados = busqueda.get('resultados_completos', [])
    if not resultados:
        bot.edit_message_text("No he encontrado nada con esos criterios.", chat_id=chat_id, message_id=message_id)
        return

    items = 5
    total_pags = math.ceil(len(resultados) / items)
    res_pag = resultados[pagina*items : (pagina+1)*items]

    texto = f"Top {busqueda['nombre_combustible']} (Pág {pagina+1}/{total_pags}):\n\n"
    for i, g in enumerate(res_pag, start=(pagina*items)+1):
        dist = f" | 📏 {g['distancia']:.1f} km" if 'distancia' in g else ""
        texto += f"{i}. 🏪 [{g['rotulo']}]({g['map_url']})\n💶 **{g['precio']}€**{dist}\n📍 {g['direccion']}\n\n"

    markup = InlineKeyboardMarkup()
    btns = []
    if pagina > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"page_{pagina-1}"))
    if pagina < total_pags - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"page_{pagina+1}"))
    if btns: markup.add(*btns)

    bot.edit_message_text(texto, chat_id=chat_id, message_id=message_id, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def cambiar_pagina(call):
    mostrar_pagina(call.message.chat.id, call.message.message_id, int(call.data.split('_')[1]))

# 7. Servidor Web para Render (Evita el "Application exited early")
class Manejador(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo")

def iniciar_servidor():
    puerto = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', puerto), Manejador).serve_forever()

# 8. Ejecución Principal
if __name__ == '__main__':
    # Lanzar servidor web y bucle de datos en hilos separados
    threading.Thread(target=iniciar_servidor, daemon=True).start()
    threading.Thread(target=bucle_actualizacion_continua, daemon=True).start()
    
    print("🤖 Bot en marcha...")
    bot.infinity_polling()
