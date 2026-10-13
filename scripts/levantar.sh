#!/usr/bin/env bash
# Levanta el muro completo desde cero (o reconcilia lo que ya exista).
#
# Por que no basta con un "terraform apply":
#   los servicios de Cloud Run necesitan que sus imagenes ya existan, y las imagenes
#   necesitan el registro, que crea Terraform. Terraform no ve esa dependencia, asi que
#   se aplica en dos fases con la subida de imagenes en medio:
#     A) APIs + registro  ->  imagenes  ->  B) todo lo demas.
#
# Uso (desde cualquier carpeta, en Cloud Shell):   ./scripts/levantar.sh
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA="$RAIZ/infra"
TFVARS="$INFRA/terraform.tfvars"

for programa in terraform docker gcloud; do
  command -v "$programa" >/dev/null || { echo "Falta '$programa' en el PATH." >&2; exit 1; }
done

# Lee una variable de cadena de terraform.tfvars:  nombre = "valor"
valor() {
  sed -n "s/^$1[[:space:]]*=[[:space:]]*\"\(.*\)\"[[:space:]]*\$/\1/p" "$TFVARS" | head -n 1
}

PROYECTO="$(valor proyecto)"
IMG_BACKEND="$(valor imagen_backend)"
IMG_FRONTEND="$(valor imagen_frontend)"
for v in PROYECTO IMG_BACKEND IMG_FRONTEND; do
  [ -n "${!v}" ] || { echo "No se pudo leer '$v' de $TFVARS" >&2; exit 1; }
done
SERVIDOR="${IMG_BACKEND%%/*}"        # p. ej. us-central1-docker.pkg.dev

INTENTOS=40
subir() {   # docker push con reintentos: la red de Cloud Shell a veces rechaza conexiones
  local imagen="$1" intento
  for intento in $(seq 1 "$INTENTOS"); do
    if docker push "$imagen"; then return 0; fi
    echo ">> push fallido (intento $intento/$INTENTOS); reintento en 5 s..."
    sleep 5
  done
  cat >&2 <<MENSAJE

No se pudo subir $imagen tras $INTENTOS intentos.
Si el error es "connect: connection refused" contra una IP de Google, es un problema de red
de Cloud Shell, no del codigo. Prueba, en este orden:
  1. Esperar unos minutos y volver a ejecutar este script (es seguro repetirlo).
  2. Reiniciar la sesion de Cloud Shell y volver a ejecutarlo.
  3. Ver a que IPs resuelve el registro y probar cada una:
       getent ahostsv4 $SERVIDOR
       curl -sI --resolve $SERVIDOR:443:<IP> https://$SERVIDOR/v2/
MENSAJE
  return 1
}

echo "== 0. API de arranque (Terraform la necesita para administrar las demas) =="
gcloud services enable cloudresourcemanager.googleapis.com --project "$PROYECTO"

echo "== 1. terraform init =="
terraform -chdir="$INFRA" init -input=false

echo "== 2. Fase A: APIs y registro =="
terraform -chdir="$INFRA" apply -input=false -auto-approve \
  -target=google_project_service.apis \
  -target=google_artifact_registry_repository.imagenes

echo "== 3. Imagenes =="
gcloud auth configure-docker "$SERVIDOR" --quiet
docker build -t "$IMG_BACKEND" "$RAIZ/backend"
docker build -t "$IMG_FRONTEND" "$RAIZ/frontend"
subir "$IMG_BACKEND"
subir "$IMG_FRONTEND"

echo "== 4. Fase B: base, cuentas, servicios y permisos =="
if ! terraform -chdir="$INFRA" apply -input=false -auto-approve; then
  cat >&2 <<'MENSAJE'

La fase B fallo. Causas habituales tras un "terraform destroy":
  - El nombre de la base de Firestore aun no se libero: espera ~5 minutos y repite.
  - Una API recien habilitada no ha propagado: espera 1-2 minutos y repite.
Es seguro volver a ejecutar este script.
MENSAJE
  exit 1
fi

echo "== Listo =="
terraform -chdir="$INFRA" output
