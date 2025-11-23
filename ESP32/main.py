# gps_main.py - 稳健的 GGA 解析 + VTG/RMC 速度解析 + DTU 上传（MicroPython）
from machine import UART
import time
import ujson
import sys

# 配置
GPS_UART_ID = 2
GPS_BAUD = 9600
GPS_RX = 16
GPS_TX = 17

DTU_UART_ID = 1
DTU_BAUD = 115200
DTU_TX = 4
DTU_RX = 15

BOOT_WAIT = 20      # 启动后等待 DTU 联网时间（秒）
PRINT_INTERVAL = 1  # 控制台打印间隔（秒）
UPLOAD_INTERVAL = 2 # 上传间隔（秒）

# 初始化串口
gps_uart = UART(GPS_UART_ID, baudrate=GPS_BAUD, rx=GPS_RX, tx=GPS_TX, timeout=1000)
dtu_uart = UART(DTU_UART_ID, baudrate=DTU_BAUD, tx=DTU_TX, rx=DTU_RX, timeout=1000)

def print_flush(*args, **kwargs):
    print(*args, **kwargs)
    try:
        sys.stdout.flush()
    except:
        pass

print_flush("摩托车定位器启动成功！等待 GPS 定位与 DTU 联网...")
time.sleep(BOOT_WAIT)

last_print = 0
last_upload = 0
latest_fix = None  # (timestr, lat, lon, sats, alt, qual)
latest_speed_kmh = 0.0

def parse_gga(line):
    """
    解析单条 GGA 句子，返回 (timestr, lat, lon, sats, alt, qual) 或 None
    """
    parts = line.split(',')
    if len(parts) < 10:
        return None
    qual = parts[6]
    if qual in ('0', ''):
        return None
    lat_raw = parts[2]
    lat_dir = parts[3] if len(parts) > 3 else ''
    lon_raw = parts[4]
    lon_dir = parts[5] if len(parts) > 5 else ''
    try:
        if not lat_raw or not lon_raw:
            return None
        lat = int(float(lat_raw[:2])) + float(lat_raw[2:]) / 60
        if lat_dir == 'S': lat = -lat
        lon = int(float(lon_raw[:3])) + float(lon_raw[3:]) / 60
        if lon_dir == 'W': lon = -lon
        t = parts[1]
        timestr = f"{t[:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else t
        sats = int(parts[7] or 0)
        alt = float(parts[9] or 0)
        return timestr, lat, lon, sats, alt, qual
    except Exception as e:
        print_flush("GGA 解析异常:", e)
        return None

# 主循环：读取串口，解析 GGA/VTG/RMC，打印并定期上传最新位置与速度
while True:
    try:
        if gps_uart.any():
            raw = gps_uart.readline()
            if not raw:
                time.sleep(0.05)
                continue
            try:
                text = raw.decode('ascii', 'ignore')
            except:
                text = repr(raw)

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                print_flush("原始 NMEA 数据:", line)

                # 处理 VTG（速度，km/h 在 parts[7]）
                try:
                    if len(line) >= 6 and line[0] == '$' and line[3:6] == 'VTG':
                        parts = line.split(',')
                        if len(parts) > 7 and parts[7]:
                            try:
                                sp_kmh = float(parts[7])
                                latest_speed_kmh = sp_kmh
                            except:
                                pass
                except Exception as e:
                    print_flush("VTG 解析异常:", e)

                # 处理 RMC（速度，knots 在 parts[7]，需 *1.852 -> km/h）
                try:
                    if len(line) >= 6 and line[0] == '$' and (line[3:6] == 'RMC' or line[3:6] == 'GPR' or line[1:4] == 'GNR'):
                        # 兼容 GNRMC/GPRMC
                        parts = line.split(',')
                        if len(parts) > 7 and parts[7]:
                            try:
                                sp_knots = float(parts[7])
                                latest_speed_kmh = sp_knots * 1.852
                            except:
                                pass
                except Exception as e:
                    # 不要阻塞主循环
                    print_flush("RMC 解析异常:", e)

                # 处理 GGA（位置）
                if len(line) >= 6 and line[0] == '$' and line[3:6] == 'GGA':
                    res = parse_gga(line)
                    if res:
                        latest_fix = res
                        now = time.time()

                        # 打印（每秒一次）
                        if now - last_print >= PRINT_INTERVAL:
                            timestr, lat, lon, sats, alt, qual = latest_fix
                            print_flush("时间:", timestr)
                            print_flush("纬度: {:.6f}°{}".format(abs(lat), "S" if lat < 0 else "N"))
                            print_flush("经度: {:.6f}°{}".format(abs(lon), "W" if lon < 0 else "E"))
                            print_flush("卫星: {} 颗   海拔: {} 米   质量: {}".format(sats, alt, qual))
                            print_flush("速度: {:.2f} km/h".format(latest_speed_kmh))
                            print_flush("原始数据:", line)
                            print_flush("-" * 60)
                            last_print = now

                        # 上传（每 UPLOAD_INTERVAL 秒）
                        if time.time() - last_upload >= UPLOAD_INTERVAL:
                            try:
                                timestr, lat, lon, sats, alt, qual = latest_fix
                                payload = ujson.dumps({
                                    "lat": round(lat, 6),
                                    "lon": round(lon, 6),
                                    "alt": alt,
                                    "sats": sats,
                                    "quality": qual,
                                    "speed_kmh": round(latest_speed_kmh, 2),
                                    "time": int(time.time())
                                }) + "\r\n"
                                dtu_uart.write(payload.encode())
                                print_flush("↑ 已上传到 DTU:", payload.strip())
                                last_upload = time.time()
                            except Exception as e:
                                print_flush("上传时发生错误:", e)
    except KeyboardInterrupt:
        print_flush("检测到 KeyboardInterrupt，停止运行（REPL 中断）。")
        break
    except Exception as e:
        print_flush("串口读取或处理异常:", e)
        try:
            sys.print_exception(e)
        except:
            pass
        time.sleep(1)

    time.sleep(0.1)
