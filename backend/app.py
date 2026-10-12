# API del muro de mensajes: listar, publicar y reaccionar, guardando en Firestore.
import os, time, uuid
from flask import Flask, request, jsonify
from google.api_core.exceptions import InvalidArgument, NotFound
from google.cloud import firestore

app = Flask(__name__)
db = firestore.Client(database=os.environ.get("BASE_DATOS", "(default)"))
INSTANCIA = uuid.uuid4().hex[:6]
REACCIONES = ("me_gusta", "corazon")
ORIGENES_PERMITIDOS = [o.strip() for o in os.environ.get("ORIGEN_PERMITIDO", "*").split(",") if o.strip()]

# Copia en memoria del listado: varias pestañas dentro de la misma ventana de tiempo
# comparten una sola lectura a Firestore. Cada instancia tiene la suya.
CACHE_SEGUNDOS = float(os.environ.get("CACHE_SEGUNDOS", "2"))
_cache = {"hasta": 0.0, "mensajes": None}


def _invalidar_cache():
    _cache["mensajes"] = None


@app.after_request
def permitir_navegador(respuesta):
    # CORS: solo los origenes de ORIGEN_PERMITIDO (lista separada por comas) pueden leer
    # las respuestas desde un navegador. Sin la variable queda "*" (desarrollo local).
    # Es una regla del navegador, no autenticacion: curl y otros clientes no la aplican.
    origen = request.headers.get("Origin")
    if "*" in ORIGENES_PERMITIDOS:
        respuesta.headers["Access-Control-Allow-Origin"] = "*"
    else:
        respuesta.headers.add("Vary", "Origin")
        if origen in ORIGENES_PERMITIDOS:
            respuesta.headers["Access-Control-Allow-Origin"] = origen
    if "Access-Control-Allow-Origin" in respuesta.headers:
        respuesta.headers["Access-Control-Allow-Headers"] = "Content-Type"
        respuesta.headers["Access-Control-Allow-Methods"] = "GET, POST"
    return respuesta


@app.get("/")
def salud():
    return "ok"


def _leer_mensajes():
    consulta = (db.collection("mensajes")
                  .order_by("creado", direction=firestore.Query.DESCENDING)
                  .limit(20))
    mensajes = []
    for d in consulta.stream():
        datos = d.to_dict()
        guardadas = datos.get("reacciones") or {}
        mensajes.append({
            "id": d.id,
            "autor": datos.get("autor"),
            "texto": datos.get("texto"),
            "reacciones": {tipo: int(guardadas.get(tipo, 0)) for tipo in REACCIONES},
        })
    return mensajes


@app.get("/mensajes")
def listar():
    ahora = time.monotonic()
    fresco = request.args.get("fresco") == "1"  # lo usa la página tras sus propias acciones
    if fresco or _cache["mensajes"] is None or ahora >= _cache["hasta"]:
        _cache["mensajes"] = _leer_mensajes()
        _cache["hasta"] = ahora + CACHE_SEGUNDOS
    return jsonify(instancia=INSTANCIA, mensajes=_cache["mensajes"])


@app.post("/mensajes")
def crear():
    datos = request.get_json(silent=True) or {}
    autor = str(datos.get("autor", "")).strip()[:40]
    texto = str(datos.get("texto", "")).strip()[:280]
    if not autor or not texto:
        return jsonify(error="faltan autor o texto"), 400
    db.collection("mensajes").add({
        "autor": autor,
        "texto": texto,
        "creado": firestore.SERVER_TIMESTAMP,
    })
    _invalidar_cache()
    return jsonify(ok=True, instancia=INSTANCIA), 201


@app.post("/mensajes/<id_mensaje>/reacciones")
def reaccionar(id_mensaje):
    datos = request.get_json(silent=True) or {}
    tipo = datos.get("tipo")
    if tipo not in REACCIONES:
        return jsonify(error="tipo de reaccion no valido"), 400
    try:
        # Increment es atomico: varias instancias pueden reaccionar a la vez sin pisarse.
        db.collection("mensajes").document(id_mensaje).update(
            {f"reacciones.{tipo}": firestore.Increment(1)})
    except NotFound:
        return jsonify(error="el mensaje no existe"), 404
    except InvalidArgument:
        return jsonify(error="id de mensaje no valido"), 400
    _invalidar_cache()
    return jsonify(ok=True, instancia=INSTANCIA)
