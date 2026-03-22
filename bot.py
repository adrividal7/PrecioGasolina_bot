import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
import urllib3
from http.server import BaseHTTPRequestHandler, HTTPServer

# Configuración de seguridad y avisos
urllib3.disable_warnings()

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    print("❌ ERROR: Configura TELEGRAM_TOKEN en Render.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# Memoria del Bot
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 
busquedas_usuarios = {}

# --- 1. TRADUCTOR DE DIRECCIONES (GEOLOCALIZACIÓN) ---
def obtener_coordenadas(direccion):
    query = f"{direccion}, España" if "España" not in direccion else direccion
    url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
    headers = {'User-Agent': f'BotGasolinera_Pro_{TOKEN[:5]}'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except:
        pass
    return None

# --- 2. DESCARGA DE DATOS (SEGUNDO PLANO) ---
def actualizar_datos_ministerio():
    try:
        print("📥 Actualizando base de datos del Ministerio...")
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(API_URL, headers=headers, verify=False, timeout=100)
        if r.status_code == 200:
            cache['datos'] = r.json().get('ListaEESSPrecio', [])
            cache['ultima_actualizacion'] = time.time()
            print(f"✅ {len(cache['datos'])} gasolineras listas.")
            return True
    except Exception as e:
        print(f"❌ Error descarga: {e}")
    return False

def bucle_datos():
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE)

# --- 3. CÁLCULO DE DISTANCIA ---
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# --- 4. MENÚS UNIFICADOS ---
def menu_distancia(chat_id, titulo="📍 Ubicación recibida"):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📍 10 km", callback_data="d_10"),
        InlineKeyboardButton("📍 20 km", callback_data="d_20"),
        InlineKeyboardButton("📍 30 km", callback_data="d_30")
    )
    bot.send_message(chat_id, f"{titulo}\n¿A qué distancia máxima buscamos?", reply_markup=markup, parse_mode="Markdown")

# --- 5. RECEPCIÓN DE ENTRADAS ---
@bot.message_handler(commands=['start'])
def inicio(message):
    bot.reply_to(message, "⛽️ ¡Hola! Envíame una **calle**, un **municipio** o tu **ubicación actual** 📎.")

@bot.message_handler(content_types=['location'])
def manejar_ubicacion_gps(message):
    """Maneja cuando el usuario envía el 'clip' de ubicación de Telegram"""
    chat_id = message.chat.id
    busquedas_usuarios[chat_id] = {
        'tipo': 'gps', 
        'lat': message.location.latitude, 
        'lon': message.location.longitude
    }
    menu_distancia(chat_id, "📍 *Ubicación GPS detectada*")

@bot.message_handler(content_types=['text'])
def manejar_texto(message):
    """Maneja cuando el usuario escribe una calle o pueblo"""
    texto = message.text
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, 'find_location')
    
    coords = obtener_coordenadas(texto)
    if coords:
        busquedas_usuarios[chat_id] = {'tipo': 'gps', 'lat': coords[0], 'lon': coords[1]}
        menu_distancia(chat_id, f"📍 *{texto}* localizado")
    else:
        # Si no es una calle, lo buscamos como nombre de municipio
        busquedas_usuarios[chat_id] = {'tipo': 'texto', 'valor': texto.upper()}
        preguntar_combustible(chat_id)

# --- 6. PROCESAMIENTO ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('d_'))
def set_distancia(call):
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
    txt = "Selecciona el combustible:"
    if mid: bot.edit_message_text(txt, chat_id=chat_id, message_id=mid, reply_markup=markup)
    else: bot.send_message(chat_id, txt, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('f_'))
def buscar_y_mostrar(call):
    chat_id =
