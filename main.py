from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import time
import uuid
import urllib.request
import urllib.error
import os
import logging

app = Flask(__name__)
CORS(app) # Cho phép mọi nguồn gọi API (GitHub/Cloudflare Pages gọi được)

# Tắt log của Flask để bảo mật (không in token/cookie ra màn hình Render)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) 

ME_URL = "https://discord.com/api/v9/users/@me?with_analytics_token=true"
SCIENCE_URL = "https://discord.com/api/v9/science"
GAMES_CDN_URL = "https://cdn.discordapp.com/detectables/games.json"
QUESTS_URL = "https://discord.com/api/v9/quests/@me"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9253 Chrome/148.0.7778.280 Electron/42.7.1 Safari/537.36"

def safe_request(url, headers, data=None, method="GET", timeout=15):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

def get_analytics_token(token, cookie):
    headers = {"authorization": token, "user-agent": USER_AGENT, "cookie": cookie}
    status, data = safe_request(ME_URL, headers)
    if status == 200 and data:
        return data.get("analytics_token")
    return None

def load_games():
    try:
        req = urllib.request.Request(GAMES_CDN_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        games = []
        for entry in data:
            gid = str(entry.get("id", ""))
            if not gid.isdigit(): continue
            exe = "game.exe"
            for e in entry.get("executables", []):
                if e.get("os") == "win32" and e.get("name"):
                    exe = e["name"]; break
            games.append({"id": gid, "name": entry.get("name", "Unknown"), "exe": exe})
        return games
    except:
        return []

@app.route('/api/claim', methods=['POST'])
def claim_badge():
    try:
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        cookie = data.get("cookie")
        num_games = int(data.get("games", 100))
        hours = float(data.get("hours", 10))
        
        if not token or not cookie:
            return jsonify({"status": "error", "msg": "Thiếu token hoặc cookie"}), 400

        analytics_token = get_analytics_token(token, cookie)
        if not analytics_token:
            return jsonify({"status": "error", "msg": "Token hoặc Cookie sai/hết hạn"}), 401

        all_games = load_games()
        if not all_games:
            return jsonify({"status": "error", "msg": "Lỗi tải danh sách game từ Discord"}), 500

        selected_games = all_games[:num_games]
        duration_ms = int(hours * 3600 * 1000)
        
        sid = str(uuid.uuid4())
        now = int(time.time() * 1000)
        events = []
        
        for game in selected_games:
            events.append({"type": "running_game_heartbeat", "properties": {"client_track_timestamp": now - duration_ms, "client_heartbeat_session_id": sid, "event_sequence_number": 0, "game_id": game["id"], "initial_heartbeat": True, "final_heartbeat": False, "game_session_id": sid, "duration_tracked_ms": 0}})
            events.append({"type": "launch_game", "properties": {"client_track_timestamp": now, "client_heartbeat_session_id": sid, "event_sequence_number": 0, "game_id": game["id"], "game": game["name"], "executable_path": game["exe"], "verified": True, "detection_method": "verified_game"}})
            events.append({"type": "running_game_heartbeat", "properties": {"client_track_timestamp": now, "client_heartbeat_session_id": sid, "event_sequence_number": 0, "game_id": game["id"], "initial_heartbeat": False, "final_heartbeat": True, "game_session_id": sid, "duration_tracked_ms": duration_ms}})

        payload = {"token": analytics_token, "events": events}
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "accept": "*/*", "authorization": token, "content-type": "application/json", "cookie": cookie,
            "origin": "https://discord.com", "referer": "https://discord.com/channels/@me", "user-agent": USER_AGENT,
        }

        status, _ = safe_request(SCIENCE_URL, headers, data=body, method="POST")
        
        if status == 204:
            return jsonify({"status": "success", "msg": f"Thành công! Đã bơm {hours}h cho {len(selected_games)} game."})
        else:
            return jsonify({"status": "error", "msg": f"Lỗi Discord HTTP: {status}"}), 400
            
    except Exception:
        return jsonify({"status": "error", "msg": "Lỗi xử lý phía server"}), 500

@app.route('/api/quest', methods=['POST'])
def do_quests():
    try:
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        
        if not token:
            return jsonify({"status": "error", "msg": "Thiếu token"}), 400

        headers = {"authorization": token, "user-agent": USER_AGENT}
        status, quests = safe_request(QUESTS_URL, headers)
        
        if status != 200 or not quests:
            return jsonify({"status": "error", "msg": "Không lấy được danh sách Quest hoặc Token sai"}), 401

        completed = 0
        total = len(quests)

        for q in quests:
            q_id = q.get("id")
            config = q.get("config", {})
            task_config = config.get("task_config", {})
            task_type = task_config.get("task_type")
            
            if task_type:
                payload = {}
                if task_type == "WATCH_VIDEO":
                    payload = {"quest_id": q_id, "task_type": "WATCH_VIDEO", "seconds_watched": task_config.get("length", 0)}
                elif task_type in ["PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"]:
                    payload = {"quest_id": q_id, "task_type": task_type, "seconds_played": task_config.get("target_seconds", 0)}
                else:
                    continue

                url = f"https://discord.com/api/v9/quests/@me/{q_id}/progress"
                body = json.dumps(payload).encode("utf-8")
                q_headers = {
                    "accept": "*/*", "authorization": token, "content-type": "application/json",
                    "origin": "https://discord.com", "referer": "https://discord.com/channels/@me", "user-agent": USER_AGENT,
                }
                q_status, _ = safe_request(url, q_headers, data=body, method="POST")
                if q_status in [200, 204]:
                    completed += 1

        return jsonify({"status": "success", "msg": f"Đã xử lý {completed}/{total} quests thành công."})
        
    except Exception:
        return jsonify({"status": "error", "msg": "Lỗi xử lý quest phía server"}), 500

@app.route('/')
def index():
    return "API is running..."

if __name__ == '__main__':
    # Tự động nhận PORT do Render cấp
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
