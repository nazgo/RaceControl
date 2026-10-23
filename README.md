# RaceControl — GitHub + Render + Neon

App para competencias deportivas de la familia y turnos laborales. Turnos de 7 días activos y 14 libres a partir del lunes 2026-11-02, repetidos cada 21 días.

## Arquitectura
- Python Flask, Gunicorn y PWA web.
- PostgreSQL alojado en Neon (`DATABASE_URL`) para persistencia en producción.
- Render Web Service conectado a un repositorio GitHub y desplegado con `render.yaml`.
- Protección por autenticación HTTP Basic (`APP_USERNAME`/`APP_PASSWORD`), adecuada para un prototipo privado sobre HTTPS. Evolucionar a sesiones y protección CSRF antes de permitir múltiples usuarios.
- SQLite local de respaldo si `DATABASE_URL` no está definida.

## Publicación
1. Crea un repositorio GitHub **privado** llamado `racecontrol` y sube el contenido de esta carpeta a la raíz del repositorio. No subas `.env` ni bases SQLite.
2. En [Neon](https://console.neon.tech/) crea un proyecto PostgreSQL, copia la cadena de conexión con `sslmode=require` y guárdala como secreto. No la compartas por chat ni la publiques en GitHub.
3. En [Render](https://dashboard.render.com/) elige New > Blueprint, conecta el repositorio de GitHub y permite usar el `render.yaml`.
4. Cuando Render solicite variables de entorno, ingresa `DATABASE_URL` (cadena de Neon), `APP_USERNAME` y `APP_PASSWORD` (contraseña segura distinta de la de GitHub). En los siguientes despliegues el servicio utilizará Neon.
5. Verifica que `/healthz` responda, abre la URL HTTPS de Render y autentícate. Registra un evento de prueba y recarga la página para comprobar persistencia.

En local: `pip install -r requirements.txt` y `python app.py`. Sin `DATABASE_URL` usa SQLite; con la variable configurada usa Neon.

## Limitaciones
El proyecto inicial conserva los endpoints y modelo de eventos originales. La autenticación Basic protege la app completa, pero no es un sistema multiusuario ni un reemplazo de login con sesiones/CSRF. La aplicación es un prototipo personal; para uso público o compartido es necesario reforzar seguridad, pruebas y controles de autorización. La exportación ICS no sincroniza automáticamente. Los datos introducidos en el SQLite anterior no se migran solos a Neon.
