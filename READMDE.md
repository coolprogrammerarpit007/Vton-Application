# AI Virtual Try-On Studio (VTON)

An asynchronous, API-driven Virtual Try-On web application powered by FastAPI, Vanilla JS, and the FASHN.ai `tryon-max` core.


**Organization:** Microprixs Solutions  

---

## 🏗️ Architecture Overview
* **Backend:** FastAPI (Python) for asynchronous workload routing.
* **Frontend:** Vanilla JavaScript, HTML5, CSS3 with a dual-tab capture system.
* **Database:** MySQL (via SQLAlchemy) for state and job tracking.
* **AI Engine:** FASHN.ai API (`tryon-max` model).
* **File Storage:** Local chunked stream saving (avoids RAM bloat).

---

## 💻 Local Development Setup (Windows)

### 1. Prerequisites
* Python 3.9+ installed and added to PATH.
* MySQL Server running locally.
* [Ngrok](https://ngrok.com/) installed for exposing local static files to the FASHN API.
* A valid FASHN.ai API key.

### 2. Database Configuration
Create a new MySQL database for the application:
```sql
CREATE DATABASE vton_db;
```

### 3. Backend Setup
Open a terminal (Command Prompt or PowerShell) and execute the following:

```cmd
:: 1. Navigate to the backend directory
cd backend

:: 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 3. Install dependencies
pip install fastapi uvicorn sqlalchemy pymysql httpx python-multipart pydantic-settings
```

Create a `.env` file inside the `backend` folder:
```ini
DATABASE_URL=mysql+pymysql://root:yourpassword@localhost:3306/vton_db
FASHN_API_KEY=fa-your_actual_api_key_here
```

### 4. Exposing the Application (Crucial for AI Access)
Because the FASHN.ai cloud engine needs to download your uploaded images, your local `localhost` must be exposed to the internet.

1. Open a new terminal and start Ngrok:
   ```cmd
   ngrok http 8000
   ```
2. Copy the forwarding URL (e.g., `https://a1b2-34-56-78.ngrok-free.app`).
3. Open `backend/app/main.py` and update the `base_url` variable in the `create_tryon_job` function:
   ```python
   base_url = "[https://your-ngrok-subdomain.ngrok-free.app/static_uploads](https://your-ngrok-subdomain.ngrok-free.app/static_uploads)"
   ```

### 5. Running the Application (One-Click)
Ensure you have the `start_servers.bat` file in your root project directory. Double-click it to automatically launch both the FastAPI backend and the Python HTTP frontend server simultaneously.

---

## 🚀 Production Deployment (Linux)

Deploying a Python application on Linux requires a process manager to handle memory fragmentation and worker recycling. We use **Gunicorn** to manage our Uvicorn workers. 

*Note: Gunicorn does not work on Windows, which is why we only use Uvicorn locally.*

### 1. Server Preparation
```bash
sudo apt update
sudo apt install python3-pip python3-venv mysql-server nginx
```

### 2. Clone and Setup Environment
```bash
git clone <your-repo-url> /var/www/vton-app
cd /var/www/vton-app/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn # Required for Linux production
```

### 3. Configure the Systemd Service
To ensure the backend runs continuously, restarts on failure, and strictly adheres to memory limits, create a systemd service.

```bash
sudo nano /etc/systemd/system/vton-backend.service
```

Paste the following configuration (adjusting paths to match your Linux user):

```ini
[Unit]
Description=Gunicorn instance to serve VTON FastAPI Application
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/var/www/vton-app/backend
Environment="PATH=/var/www/vton-app/backend/.venv/bin"
Environment="MALLOC_ARENA_MAX=2"  # Prevents Python RAM fragmentation

# Starts Gunicorn with 4 workers. Restarts workers every 1000 requests to clear RAM.
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --workers 4 --bind 0.0.0.0:8000

# Strict RAM limits
MemoryHigh=1.2G
MemoryMax=1.5G
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl start vton-backend
sudo systemctl enable vton-backend
```

### 4. Nginx Reverse Proxy & Static File Handling
Nginx handles incoming web traffic, serves the frontend interface, and securely routes API calls to the internal Gunicorn port.

```bash
sudo nano /etc/nginx/sites-available/vton-app
```

```nginx
server {
    listen 80;
    server_name vton.yourdomain.com;

    # 1. Serve Frontend UI
    location / {
        root /var/www/vton-app/frontend;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # 2. Route API calls to FastAPI/Gunicorn
    location /api/ {
        proxy_pass [http://127.0.0.1:8000/api/](http://127.0.0.1:8000/api/);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 3. Expose Uploaded Static Files to the Internet (For FASHN.ai)
    location /static_uploads/ {
        alias /var/www/vton-app/backend/static_uploads/;
        autoindex off;
    }
}
```

Enable the configuration and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/vton-app /etc/nginx/sites-enabled
sudo nginx -t
sudo systemctl restart nginx
```

---

## 🛠️ Common Troubleshooting

* **502 Bad Gateway from FASHN:** The FASHN.ai server cannot reach your images. Verify that your `base_url` points to an active Ngrok tunnel (Local) or your verified domain (Production), and that the Nginx `/static_uploads/` block is configured correctly.
* **CORS "Failed to Fetch":** Ensure `CORSMiddleware` is active in `main.py` and that `allow_origins` includes your frontend domain.
* **Out of Memory (OOM) Kills in Prod:** If Linux is killing the service, verify that `save_upload_file` is using `shutil.copyfileobj` instead of `.read()` to prevent large image byte arrays from filling up the RAM.
```