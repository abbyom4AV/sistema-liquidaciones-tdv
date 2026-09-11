# Instrucciones para instalar el Sistema TDV en un servidor de oficina

Este sistema es una aplicación interna de liquidaciones.
Se instala en un **servidor Windows físico** de la oficina y se usa
desde el navegador de las PCs de la red.

No es un hosting web tipo cPanel. Debe vivir en una máquina Windows
con Excel y Tesseract, siempre encendida.

---

## 1. Requisitos del servidor

Instalar en el servidor:

1. **Windows Server** o **Windows 10/11 Pro**
2. **Python 3.14**
   - En el instalador marcar: *Add python.exe to PATH*
3. **Microsoft Excel** (con licencia)
4. **Tesseract OCR** en la ruta:
   `C:\Program Files\Tesseract-OCR\`
   - Descargar desde: https://github.com/UB-Mannheim/tesseract/wiki
   - Incluir idioma English (y Spanish si aparece)

Comprobar Tesseract:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
```

Comprobar Python:

```powershell
py -3.14 --version
```

Debe mostrar algo como `Python 3.14.x`.

---

## 2. Copiar el sistema

Copiar toda la carpeta del sistema al servidor, por ejemplo:

```text
C:\Sistema TDV\
```

Deben quedar al menos:

- el código del proyecto
- `.env`
- `db.sqlite3`
- carpeta `media\`
- `requirements.txt`
- carpeta `scripts\`

Nota: el `.venv` de otra PC normalmente **no sirve**.
Hay que crearlo de nuevo en el servidor (paso 3).

---

## 3. Crear el entorno Python en el servidor

Abrir PowerShell en la carpeta del sistema:

```powershell
cd "C:\Sistema TDV"
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Si pide ExecutionPolicy al correr scripts:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 4. Revisar el archivo `.env`

Debe existir `.env` en la raíz del sistema.
Si no existe, copiar `.env.example` y completar:

```env
DJANGO_SECRET_KEY=una_clave_larga_y_segura
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,NOMBRE-DEL-SERVIDOR,IP-DEL-SERVIDOR
```

Para uso en red local, también puede usarse:

```env
DJANGO_ALLOWED_HOSTS=*
```

(solo en red interna de confianza).

---

## 5. Arrancar el sistema

Doble clic en:

```text
scripts\start_sistema_tdv.cmd
```

Eso levanta:

- el servidor web (Django)
- todos los workers (uno por módulo de cliente)

Para detener:

```text
scripts\stop_sistema_tdv.ps1
```

Probar en el mismo servidor:

```text
http://127.0.0.1:8000/
```

Si aparece el login, la instalación básica está bien.

---

## 6. Acceso desde otras PCs de la oficina

El sistema escucha en el puerto **8000**.

Desde otra PC abrir:

```text
http://NOMBRE-DEL-SERVIDOR:8000
```

o

```text
http://IP-DEL-SERVIDOR:8000
```

### Firewall

Si no abre desde otra PC, en el servidor permitir el puerto 8000
en el Firewall de Windows (entrada TCP 8000).

---

## 7. Arranque automático al encender el servidor

Para que no dependa de abrirlo a mano:

1. Abrir **Programador de tareas**
2. Crear tarea básica
3. Nombre: `Sistema TDV`
4. Desencadenador: *Al iniciar sesión* o *Al arrancar el equipo*
5. Acción: Iniciar programa
6. Programa/script:

```text
C:\Sistema TDV\scripts\start_sistema_tdv.cmd
```

7. Marcar que se ejecute aunque el usuario no haya iniciado sesión
   (si la opción está disponible) y con privilegios suficientes.

El servidor debe quedar **siempre encendido**.

---

## 8. Qué son los workers (importante)

Cuando un usuario pulsa **Generar**, la web no arma el Excel ahí mismo.
Guarda un pedido y un proceso aparte (worker) lo procesa.

- Si el servidor web está arriba pero los workers no,
  las generaciones se quedan en “Procesando…” y no terminan.
- Por eso siempre hay que usar `start_sistema_tdv.cmd`,
  que inicia servidor + workers juntos.

Hay un worker por módulo (Master, Di Manno, Orsero, etc.).

---

## 9. Archivos de liquidaciones y Despachos

El sistema necesita acceso a los Excel/PDF de clientes
(acumulativos, Despachos, liquidaciones).

Opciones:

- dejar esas carpetas en el mismo servidor, o
- usar carpetas compartidas / OneDrive accesibles desde el servidor

Reglas:

- no generar la misma liquidación desde dos PCs a la vez
- al generar Di Manno/Orsero, el Excel acumulativo no debe estar
  abierto en otra sesión bloqueándolo

---

## 10. Backups (política liviana)

No hace falta copiar todo todos los días: `media\` y los Excel
pesan mucho. Lo crítico es la base de datos.

| Qué | Frecuencia | Notas |
|-----|------------|--------|
| `db.sqlite3` | **Diario** | Chico. Tiene usuarios, historial y estados. |
| `media\` | **Semanal** | Pesada. Solo si quieren conservar descargas viejas. |
| Excel acumulativos | OneDrive / carpeta compartida | No duplicarlos en el backup del sistema. |

### Script incluido

Respaldo diario (solo base):

```text
scripts\backup_sistema_tdv.cmd
```

Respaldo semanal (base + media):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup_sistema_tdv.ps1 -IncluirMedia
```

Los archivos quedan en `backups\AAAA-MM-DD\`.
El script borra automáticamente respaldos de más de 14 días.

### Programar en el servidor

1. Programador de tareas → tarea diaria →
   `scripts\backup_sistema_tdv.cmd`
2. (Opcional) otra tarea semanal con `-IncluirMedia`

Sin backup de `db.sqlite3` se pierde el historial del sistema.

---

## 11. Checklist rápido de entrega

- [ ] Python 3.14 instalado
- [ ] Excel instalado
- [ ] Tesseract en `C:\Program Files\Tesseract-OCR\`
- [ ] Carpeta del sistema copiada
- [ ] `.venv` creado e instalado con `requirements.txt`
- [ ] `.env` presente
- [ ] `start_sistema_tdv.cmd` abre el login en `127.0.0.1:8000`
- [ ] Se puede entrar desde otra PC de la oficina
- [ ] Arranque automático configurado
- [ ] Backup diario de `db.sqlite3` programado
- [ ] Backup semanal de `media\` definido (opcional)

---

## 12. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|--------|-----------------|-----------|
| No abre el login | no arrancó el servidor | correr `start_sistema_tdv.cmd` |
| Se queda en “Procesando…” | workers apagados | reiniciar con el script de arranque |
| Error de Tesseract en Orsero/Nufri | OCR no instalado o mala ruta | instalar Tesseract en la ruta por defecto |
| Falla Di Manno/Orsero al escribir | Excel no instalado o archivo abierto | instalar Excel / cerrar el acumulativo |
| Otra PC no conecta | firewall o IP incorrecta | permitir puerto 8000 y usar IP/nombre correctos |
| `ModuleNotFoundError` | `.venv` incompleto | reinstalar `requirements.txt` |

---

## Resumen en una frase

> El Sistema TDV vive en un servidor Windows de oficina, se abre por
> navegador en la red local, y necesita Python 3.14 + Excel + Tesseract
> + workers siempre corriendo en esa misma máquina.
