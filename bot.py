import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import math
import os

# 1. Configuración básica y Seguridad
# El token ahora se lee de las variables de entorno de tu servidor (Render)
TOKEN = os.environ.get('TELEGRAM_TOKEN')

if not TOKEN:
    print("¡ERROR! No se ha encontrado el Token. Asegúrate de configurar la variable TELEGRAM_TOKEN.")
    exit()

bot = telebot.TeleBot(TOKEN)
API_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"

# 2. Sistema de Caché y Estado
cache = {'datos': None, 'ultima_actualizacion': 0}
TIEMPO_CACHE = 1800 # 30 minutos

# Diccionario para recordar qué estaba buscando cada usuario
busquedas_usuarios = {}

# 3. Funciones Auxiliares
# Asegúrate de tener 'import requests' y 'import time' arriba del todo

def obtener_datos():
    """Descarga los datos solo si la caché ha caducado"""
    tiempo_actual = time.time()
    if cache['datos'] is None or (tiempo_actual - cache['ultima_actualizacion'] > TIEMPO_CACHE):
        print("Descargando datos del Ministerio... ⏳")
        try:
            # 1. Disfrazamos nuestro bot de Google Chrome
            cabeceras = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            # 2. Hacemos la petición con las cabeceras, un tiempo máximo de espera y verify=False
            respuesta = requests.get(API_URL, headers=cabeceras, verify=False, timeout=15)
            
            # Comprobamos que la respuesta es 200 (OK)
            if respuesta.status_code == 200:
                cache['datos'] = respuesta.json()['ListaEESSPrecio']
                cache['ultima_actualizacion'] = tiempo_actual
                print("¡Datos descargados con éxito! ✅")
            else:
                print(f"❌ La API devolvió un error: {respuesta.status_code}")
                return None
                
        except Exception as e:
            # Si falla, imprimimos el error real en la consola para saber qué pasa
            print(f"❌ Error técnico al conectar: {e}")
            return None
            
    return cache['datos']
    
def limpiar_precio(precio_str):
    """Convierte el precio de texto con coma a número decimal"""
    if not precio_str:
        return float('inf')
    return float(precio_str.replace(',', '.'))

def calcular_distancia(lat1, lon1, lat2, lon2):
    """Calcula la distancia en km entre dos puntos GPS"""
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

# 5. Recibir Datos y Mostrar Botones
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
    preguntar_combustible(message.chat.id)

def preguntar_combustible(chat_id):
    markup = InlineKeyboardMarkup()
    btn_95 = InlineKeyboardButton("⛽️ Gasolina 95", callback_data="Precio Gasolina 95 E5")
    btn_diesel = InlineKeyboardButton("🛢 Diésel", callback_data="Precio Gasoleo A")
    markup.add(btn_95, btn_diesel)
    
    bot.send_message(chat_id, "¿Qué combustible utilizas?", reply_markup=markup)

# 6. Procesar Botones y Mostrar Resultados
@bot.callback_query_handler(func=lambda call: True)
def procesar_seleccion(call):
    chat_id = call.message.chat.id
    tipo_combustible = call.data 
    
    if chat_id not in busquedas_usuarios:
        bot.answer_callback_query(call.id, "Búsqueda caducada. Envía tu ubicación o municipio de nuevo.")
        return

    busqueda = busquedas_usuarios[chat_id]
    
    bot.edit_message_text("Calculando los mejores precios... ⏳", chat_id=chat_id, message_id=call.message.message_id)
    
    datos = obtener_datos()
    if not datos:
        bot.edit_message_text("Error de conexión con el Ministerio.", chat_id=chat_id, message_id=call.message.message_id)
        return

    gasolineras = []
    
    # Filtrar por Municipio
    if busqueda['tipo'] == 'municipio':
        municipio = busqueda['valor']
        for estacion in datos:
            if estacion['Municipio'] == municipio:
                precio = limpiar_precio(estacion[tipo_combustible])
                if precio != float('inf'):
                    gasolineras.append({'direccion': estacion['Dirección'], 'rotulo': estacion['Rótulo'], 'precio': precio})
        titulo = f"en **{municipio}**"

    # Filtrar por Ubicación (Radio de 10km)
    elif busqueda['tipo'] == 'ubicacion':
        for estacion in datos:
            try:
                lat_estacion = float(estacion['Latitud'].replace(',', '.'))
                lon_estacion = float(estacion['Longitud (WGS84)'].replace(',', '.'))
                distancia = calcular_distancia(busqueda['lat'], busqueda['lon'], lat_estacion, lon_estacion)
                
                if distancia <= 10.0:
                    precio = limpiar_precio(estacion[tipo_combustible])
                    if precio != float('inf'):
                        gasolineras.append({'direccion': estacion['Dirección'], 'rotulo': estacion['Rótulo'], 'precio': precio, 'distancia': distancia})
            except:
                continue
        titulo = "a menos de 10km"

    # Formatear el mensaje final
    if gasolineras:
        gasolineras.sort(key=lambda x: x['precio'])
        
        nombre_comb = "Gasolina 95" if tipo_combustible == "Precio Gasolina 95 E5" else "Diésel"
        resultados = []
        
        for g in gasolineras[:5]:
            texto_estacion = f"🏪 **{g['rotulo']}** ({g['direccion']})\n💶 Precio: {g['precio']}€"
            if 'distancia' in g:
                texto_estacion += f" | 📏 {g['distancia']:.1f} km"
            resultados.append(texto_estacion)
            
        mensaje_final = f"Top 5 de {nombre_comb} {titulo}:\n\n" + "\n\n".join(resultados)
    else:
        mensaje_final = "No he encontrado gasolineras con ese combustible en la zona."

    bot.edit_message_text(mensaje_final, chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown")
    
    del busquedas_usuarios[chat_id]
# ... (tu código sigue exactamente igual hasta aquí) ...

# 7. Servidor Web Falso (Para que Render nos deje usar la capa gratuita)
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

class Manejador(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"El bot esta funcionando OK")

def iniciar_servidor():
    # Render nos dira en que puerto escuchar a traves de la variable PORT
    puerto = int(os.environ.get("PORT", 8080)) 
    servidor = HTTPServer(('0.0.0.0', puerto), Manejador)
    servidor.serve_forever()
# 7. Iniciar Bot
if __name__ == '__main__':
    print("Bot en ejecución...")
    bot.infinity_polling()
