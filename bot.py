import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 1. Configuración básica y Seguridad
TOKEN = os.environ.get('TELEGRAM_TOKEN')

if not TOKEN:
    print("¡ERROR! No se ha encontrado el Token. Asegúrate de configurar la variable TELEGRAM_TOKEN.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# 2. Sistema de Caché y Estado
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 # 30 minutos

# Diccionario para recordar las búsquedas y poder paginar
busquedas_usuarios = {}

import urllib3
urllib3.disable_warnings() # Esto evita que los logs se llenen de advertencias por el verify=False

def obtener_datos():
    """Simplemente devuelve lo que hay en caché. Si está vacío, intenta descargar una vez."""
    if cache['datos'] is not None:
        return cache['datos']
    
    # Si la caché está vacía (ej. justo al arrancar), intentamos una descarga forzada
    print("Caché vacía. Intentando descarga de emergencia...")
    actualizar_datos_ministerio()
    return cache['datos']

def actualizar_datos_ministerio():
    """Esta función hace el trabajo pesado de descarga"""
    try:
        print("Iniciando descarga masiva desde el Ministerio... ⏳ (Puede tardar hasta 90s)")
        cabeceras = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://geoportalgasolineras.es/'
        }
        # ESTA ES LA LÍNEA 72: Asegúrate de que tenga exactamente 8 espacios antes de 'respuesta'
        respuesta = requests.get(API_URL, headers=cabeceras, verify=False, timeout=90)
        
        if respuesta.status_code == 200:
            nuevos_datos = respuesta.json().get('ListaEESSPrecio', [])
            if nuevos_datos:
                cache['datos'] = nuevos_datos
                cache['ultima_actualizacion'] = time.time()
                print(f"¡ÉXITO! {len(nuevos_datos)} gasolineras cargadas en memoria. ✅")
                return True
        else:
            print(f"❌ La API del Ministerio respondió con error: {respuesta.status_code}")
    except Exception as e:
        print(f"❌ Error crítico en la descarga: {e}")
    return False
    
def bucle_actualizacion_continua():
    """Hilo secundario que actualiza los datos cada 30 minutos sin molestar"""
    while True:
        actualizar_datos_ministerio()
        time.sleep(TIEMPO_CACHE) # Espera 30 minutos
            
            # AUMENTAMOS EL TIMEOUT A 60 SEGUNDOS
            respuesta = requests.get(API_URL, headers=cabeceras, verify=False, timeout=90)
            
            # Comprobamos que la respuesta es 200 (OK)
            if respuesta.status_code == 200:
                cache['datos'] = respuesta.json().get('ListaEESSPrecio', [])
                cache['ultima_actualizacion'] = tiempo_actual
                print("¡Datos descargados con éxito! ✅")
            else:
                print(f"❌ La API devolvió un error HTTP: {respuesta.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            print("❌ Error: El servidor del Ministerio ha tardado más de 60 segundos en responder (Timeout).")
            return None
        except Exception as e:
            # Si falla, imprimimos el error real en la consola de Render
            print(f"❌ Error técnico al conectar: {e}")
            return None
            
    return cache['datos']
    
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

# 4. Manejo de Comandos
@bot.message_handler(commands=['start', 'help'])
def enviar_bienvenida(message):
    texto = ("¡Hola! ⛽️ Soy tu asistente de gasolina.\n\n"
             "Envíame el nombre de tu **municipio** o tu **ubicación actual** (usando el clip 📎 de Telegram).")
    bot.reply_to(message, texto, parse_mode="Markdown")

# 5. Recibir Datos 
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
    
    distancia = float(call.data.split('_')[1])
    busquedas_usuarios[chat_id]['distancia_max'] = distancia
    preguntar_combustible(chat_id, call.message.message_id)

# 6. Preguntar Combustible 
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

# 7. Procesar Búsqueda y Preparar Paginación
@bot.callback_query_handler(func=lambda call: call.data.startswith('fuel_'))
def procesar_busqueda(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Búsqueda caducada. Envía tu ubicación o municipio de nuevo.")
        return

    busqueda = busquedas_usuarios[chat_id]
    tipo_combustible = call.data.replace('fuel_', '')
    
    if "95" in tipo_combustible:
        busqueda['nombre_combustible'] = "Gasolina 95"
    elif "98" in tipo_combustible:
        busqueda['nombre_combustible'] = "Gasolina 98"
    else:
        busqueda['nombre_combustible'] = "Diésel"
    
    bot.edit_message_text("Descargando y calculando los mejores precios... ⏳", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = obtener_datos()
    if not datos:
        bot.edit_message_text("❌ Error de conexión con el Ministerio. Por favor, inténtalo de nuevo en unos minutos.", chat_id=chat_id, message_id=call.message.message_id)
        return

    gasolineras = []
    
    # Filtrar datos
    for estacion in datos:
        precio = limpiar_precio(estacion[tipo_combustible])
        if precio == float('inf'):
            continue
            
        # Extraer coordenadas para crear el enlace a Google Maps
        try:
            lat_est = float(estacion['Latitud'].replace(',', '.'))
            lon_est = float(estacion['Longitud (WGS84)'].replace(',', '.'))
            # Generamos la URL oficial de búsqueda de Google Maps
            map_url = f"https://www.google.com/maps/search/?api=1&query={lat_est},{lon_est}"
        except:
            map_url = None
            
        if busqueda['tipo'] == 'municipio':
            if estacion['Municipio'] == busqueda['valor']:
                gasolineras.append({
                    'direccion': estacion['Dirección'], 
                    'rotulo': estacion['Rótulo'], 
                    'precio': precio,
                    'map_url': map_url
                })
                
        elif busqueda['tipo'] == 'ubicacion':
            try:
                distancia = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_est, lon_est)
                
                if distancia <= busqueda['distancia_max']:
                    gasolineras.append({
                        'direccion': estacion['Dirección'], 
                        'rotulo': estacion['Rótulo'], 
                        'precio': precio, 
                        'distancia': distancia,
                        'map_url': map_url
                    })
            except:
                continue

    # Ordenar y guardar en memoria para la paginación
    gasolineras.sort(key=lambda x: x['precio'])
    busqueda['resultados_completos'] = gasolineras
    
    mostrar_pagina(chat_id, call.message.message_id, pagina=0)

# 8. Función para mostrar páginas
def mostrar_pagina(chat_id, message_id, pagina):
    busqueda = busquedas_usuarios.get(chat_id)
    resultados = busqueda.get('resultados_completos', [])
    
    if not resultados:
        bot.edit_message_text("No he encontrado gasolineras con esos criterios.", chat_id=chat_id, message_id=message_id)
        return

    elementos_por_pagina = 5
    total_paginas = math.ceil(len(resultados) / elementos_por_pagina)
    inicio = pagina * elementos_por_pagina
    fin = inicio + elementos_por_pagina
    resultados_pagina = resultados[inicio:fin]

    if busqueda['tipo'] == 'municipio':
        titulo = f"en **{busqueda['valor']}**"
    else:
        titulo = f"a menos de {int(busqueda['distancia_max'])}km"

    texto_final = f"Top de {busqueda['nombre_combustible']} {titulo} (Pág {pagina+1}/{total_paginas}):\n\n"
    
    for i, g in enumerate(resultados_pagina, start=inicio+1):
        # Si tenemos URL de mapa, hacemos el título clickable
        if g.get('map_url'):
            texto_estacion = f"{i}. 🏪 [{g['rotulo']}]({g['map_url']}) ({g['direccion']})\n💶 Precio: **{g['precio']}€**"
        else:
            texto_estacion = f"{i}. 🏪 **{g['rotulo']}** ({g['direccion']})\n💶 Precio: **{g['precio']}€**"
            
        if 'distancia' in g:
            texto_estacion += f" | 📏 {g['distancia']:.1f} km"
            
        texto_final += texto_estacion + "\n\n"

    # Botones de paginación
    markup = InlineKeyboardMarkup()
    botones = []
    if pagina > 0:
        botones.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"page_{pagina-1}"))
    if pagina < total_paginas - 1:
        botones.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"page_{pagina+1}"))
    
    if botones:
        markup.add(*botones)

    # disable_web_page_preview=True es vital para que Telegram no genere vistas previas gigantes de Google Maps
    bot.edit_message_text(
        texto_final, 
        chat_id=chat_id, 
        message_id=message_id, 
        parse_mode="Markdown", 
        reply_markup=markup,
        disable_web_page_preview=True 
    )

# 9. Escuchar clicks de Paginación
@bot.callback_query_handler(func=lambda call: call.data.startswith('page_'))
def cambiar_pagina(call):
    chat_id = call.message.chat.id
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Búsqueda caducada.")
        return
        
    nueva_pagina = int(call.data.split('_')[1])
    mostrar_pagina(chat_id, call.message.message_id, nueva_pagina)

# 10. Servidor Web Falso (Para que Render nos deje usar la capa gratuita)
class Manejador(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"El bot esta funcionando OK")

def iniciar_servidor():
    puerto = int(os.environ.get("PORT", 8080)) 
    servidor = HTTPServer(('0.0.0.0', puerto), Manejador)
    servidor.serve_forever()
# 11. Iniciar Bot
if __name__ == '__main__':
    # 1. Arrancar Servidor Web para Render
    threading.Thread(target=iniciar_servidor, daemon=True).start()
    
    # 2. Arrancar Hilo de actualización de datos (esto descarga los precios en segundo plano)
    threading.Thread(target=bucle_actualizacion_continua, daemon=True).start()
    
    # 3. Iniciar el Bot de Telegram
    print("🤖 Bot listo y descargando datos en segundo plano...")
    bot.infinity_polling()infinity_polling()
