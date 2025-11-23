import socket
import threading
import json
import time
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer, ThreadingMixIn
import math
from collections import deque
import traceback

# ================= 配置区域 =================
TCP_PORT = 16666
HTTP_PORT = 8000
ENABLE_CONVERT_TO_GCJ02 = True

TRAIL_MAX = 1000
STAY_THRESHOLD_METERS = 20  # 停留判断阈值（米）
# 简化判定参数（按你思路）
BYPASS_SECONDS = 3600            # 与上次有效点时间间隔超过 1 小时 -> 跳过过滤（秒）
MAX_JUMP_METERS_SIMPLE = 50000.0 # 跳变阈值：50 公里（米）
# ===========================================

# 数据结构（全局）
trail = deque(maxlen=TRAIL_MAX)
latest = {
    "lat": 0, "lon": 0, "time": "等待连接...",
    "sats": 0, "alt": 0, "stay_duration": "0秒", "speed_kmh": 0.0
}
lock = threading.Lock()

# ---------- 工具函数 ----------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1); delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def format_duration(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{h}小时{m}分{s}秒"
    elif m > 0: return f"{m}分{s}秒"
    else: return f"{s}秒"

# 坐标转换 WGS84 -> GCJ02（保留原实现）
def out_of_china(lat, lon):
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)

def wgs84_to_gcj02(lat, lon):
    try:
        lat = float(lat); lon = float(lon)
    except:
        return lat, lon
    if out_of_china(lat, lon): return lat, lon
    a = 6378245.0; ee = 0.00669342162296594323
    def _tLat(x, y):
        ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return ret
    def _tLon(x, y):
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret
    dLat = _tLat(lon - 105.0, lat - 35.0); dLon = _tLon(lon - 105.0, lat - 35.0)
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat); magic = 1 - ee * magic * magic; sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * math.pi)
    dLon = (dLon * 180.0) / (a / sqrtMagic * math.cos(radLat) * math.pi)
    return lat + dLat, lon + dLon

# ---------- TCP 服务器 ----------
def tcp_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', TCP_PORT))
    s.listen(5)
    print(f"TCP server listening on 0.0.0.0:{TCP_PORT}")
    while True:
        try:
            client, addr = s.accept()
            threading.Thread(target=handle_client, args=(client, addr), daemon=True).start()
        except Exception:
            print("tcp_server.accept 异常：")
            traceback.print_exc()

def handle_client(client, addr):
    print(f"Client connected: {addr}")
    client.settimeout(60)
    buf = b""

    while True:
        try:
            data = client.recv(1024)
            if not data:
                break
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                try:
                    j = json.loads(line.decode('utf-8', 'ignore').strip())
                except Exception:
                    continue

                # 只关注含 lat/lon 的上报
                if 'lat' in j and 'lon' in j:
                    try:
                        lat = float(j['lat']); lon = float(j['lon'])
                        if ENABLE_CONVERT_TO_GCJ02:
                            lat, lon = wgs84_to_gcj02(lat, lon)
                        lat, lon = round(lat, 6), round(lon, 6)

                        speed_kmh = float(j.get('speed_kmh', 0.0))
                        alt = float(j.get('alt', 0.0))
                        sats = int(j.get('sats', 0))

                        now_ts = time.time()

                        # 获取上一个被接受的有效点（全局，来自 trail 的最后一个点）
                        with lock:
                            if len(trail) > 0:
                                last_pt = trail[-1]
                                last_valid_lat = last_pt['lat']
                                last_valid_lon = last_pt['lon']
                                last_valid_ts = last_pt.get('last_ts', last_pt.get('start_ts', None))
                            else:
                                last_valid_lat = last_valid_lon = last_valid_ts = None

                        # 判定逻辑（按你要求的简化规则）
                        if last_valid_ts is None:
                            # 没有历史点，首次接受
                            accept = True
                            reason = "首次有效点，直接接受"
                        else:
                            dt = now_ts - last_valid_ts
                            if dt > BYPASS_SECONDS:
                                # 距离上次超过 1 小时 -> 跳过过滤，直接接受
                                accept = True
                                reason = f"与上次有效点间隔 {int(dt)}s > {BYPASS_SECONDS}s，跳过过滤"
                            else:
                                # 否则计算距离，若超过 50km 则丢弃
                                dist = haversine(last_valid_lat, last_valid_lon, lat, lon)
                                if dist > MAX_JUMP_METERS_SIMPLE:
                                    accept = False
                                    reason = f"短时间内跳变过大 ({dist:.1f} m)，丢弃"
                                else:
                                    accept = True
                                    reason = f"短时间内跳变可接受 ({dist:.1f} m)"

                        if not accept:
                            # 丢弃该点（不更新经纬），仅更新最新时间以示收到并记录日志
                            print(f"丢弃点: {lat},{lon} 原因: {reason} sats={sats}")
                            with lock:
                                latest['time'] = time.strftime("%H:%M:%S", time.localtime(now_ts))
                            continue

                        # 接受该点：加入 trail 并更新 latest（保留停留判定逻辑）
                        with lock:
                            is_staying = False
                            if len(trail) > 0:
                                last_pt = trail[-1]
                                if haversine(last_pt['lat'], last_pt['lon'], lat, lon) < STAY_THRESHOLD_METERS:
                                    is_staying = True
                                    last_pt['last_ts'] = now_ts
                                    last_pt['duration_str'] = format_duration(now_ts - last_pt['start_ts'])
                                    latest['stay_duration'] = last_pt['duration_str']

                            if not is_staying:
                                trail.append({
                                    "lat": lat, "lon": lon, "speed_kmh": speed_kmh, "alt": alt, "sats": sats,
                                    "start_ts": now_ts, "last_ts": now_ts,
                                    "time_str": time.strftime("%H:%M:%S", time.localtime(now_ts)),
                                    "duration_str": "0秒"
                                })
                                latest['stay_duration'] = "移动中"

                            # 更新 latest（只在点被接受时更新经纬等）
                            latest.update(j)
                            latest['lat'] = lat; latest['lon'] = lon; latest['alt'] = alt; latest['sats'] = sats
                            latest['time'] = time.strftime("%H:%M:%S", time.localtime(now_ts))
                            latest['speed_kmh'] = round(speed_kmh, 2)

                    except Exception:
                        print("处理经纬度时异常：")
                        traceback.print_exc()
        except socket.timeout:
            break
        except Exception:
            print("handle_client recv 异常：")
            traceback.print_exc()
            break
    try:
        client.close()
    except:
        pass
    print(f"Client disconnected: {addr}")

# 启动 TCP 接收线程
threading.Thread(target=tcp_server, daemon=True).start()

# ---------- HTTP 服务 ----------
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith('/data'):
                with lock:
                    out = {"latest": latest, "trail": list(trail)}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())
                return

            # 返回地图页面（高德）
            html = """
            <!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>智能轨迹地图</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script src="https://webapi.amap.com/maps?v=2.0&key=替换你自己的key"></script>
            <style>
                body,html{margin:0;height:100%}
                #map{width:100%;height:100%}
                #info{position:absolute;top:10px;left:10px;background:#fff;padding:10px;border-radius:5px;box-shadow:0 2px 5px rgba(0,0,0,0.3);z-index:999;font-size:14px;max-width:200px;}
            </style>
            </head><body>
            <div id="map"></div>
            <div id="info">等待数据...</div>
            <script>
            var map = new AMap.Map('map', {zoom:17});
            var polyline = new AMap.Polyline({strokeColor:"#3366FF", strokeWeight:6, lineJoin:'round'});
            polyline.setMap(map);
            var carMarker = new AMap.Marker({icon: '//a.amap.com/jsapi_demos/static/demo-center/icons/poi-marker-default.png', 
            size: new AMap.Size(10, 15),        // 图标显示尺寸（你可以调小，例如 20x30）
            imageSize: new AMap.Size(10, 15),   // 实际图标缩放尺寸
            offset: new AMap.Pixel(-10,-25)});
            carMarker.setMap(map);

            var stopMarkers = [];
            var infoWindow = new AMap.InfoWindow({offset: new AMap.Pixel(0, -30)});

            function clearStopMarkers(){
                for(var i=0;i<stopMarkers.length;i++){
                    try{ stopMarkers[i].setMap(null); }catch(e){}
                }
                stopMarkers = [];
            }

            function update() {
                fetch('/data').then(r=>r.json()).then(d=>{
                    var trail = d.trail;
                    var latest = d.latest;
                    if(!trail || trail.length===0) return;

                    var path = trail.map(p => [p.lon, p.lat]);
                    polyline.setPath(path);
                    carMarker.setPosition(path[path.length-1]);

                    clearStopMarkers();

                    trail.forEach(p => {
                        if(p.duration_str && p.duration_str !== "0秒" && p.duration_str !== "0分0秒"){
                            var markerContent = `<div style="background:red;width:8px;height:8px;border-radius:50%;border:2px solid white;box-shadow:0 0 3px #000;"></div>`;
                            var marker = new AMap.Marker({
                                position: [p.lon, p.lat],
                                content: markerContent,
                                offset: new AMap.Pixel(-6, -6),
                                anchor: 'center'
                            });
                            marker.on('click', (function(pt){
                                return function(e){
                                    infoWindow.setContent(`
                                        <div style="font-size:14px;">
                                            <b>停留点详情</b><br>
                                            开始时间: ${pt.time_str}<br>
                                            停留时长: <span style="color:red;font-weight:bold">${pt.duration_str}</span><br>
                                            海拔: ${pt.alt} 米<br>
                                            卫星数: ${pt.sats} 颗
                                        </div>
                                    `);
                                    infoWindow.open(map, e.target.getPosition());
                                };
                            })(p));
                            marker.setMap(map);
                            stopMarkers.push(marker);
                        }
                    });

                    document.getElementById('info').innerHTML = `
                        <b>当前状态: ${latest.stay_duration==="移动中"?"行驶中":"<span style='color:red'>停留</span>"}</b><br>
                        当前停留: ${latest.stay_duration}<br>
                        速度: ${latest.speed_kmh} km/h<br>
                        时间: ${latest.time}<br>
                        海拔: ${latest.alt} 米<br>
                        卫星数: ${latest.sats} 颗<br>
                        历史停留点: ${stopMarkers.length} 个
                    `;

                    if(!window.inited) { map.setFitView(); window.inited=true; }

                }).catch(function(e){
                    console.log("fetch /data 出错:", e);
                });
                setTimeout(update, 2000);
            }
            update();
            </script></body></html>
            """

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())
        except Exception:
            print("Handler.do_GET 异常：")
            traceback.print_exc()
            try:
                self.send_response(500)
                self.end_headers()
            except:
                pass

# 支持多线程的 HTTP Server
class ThreadingTCPServer(ThreadingMixIn, TCPServer):
    daemon_threads = True
    allow_reuse_address = True

print(f"HTTP server listening on 0.0.0.0:{HTTP_PORT}")
ThreadingTCPServer(('0.0.0.0', HTTP_PORT), Handler).serve_forever()
