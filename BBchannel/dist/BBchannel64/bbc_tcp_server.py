# ==================== BBchannel TCP Server ====================
# 纯 TCP 模式，替代 HTTP API，用于 MaaFgo 通信
# 协议: 4字节长度(big-endian) + JSON数据

tcp_server_instance = None
popup_event_queue = None
original_messagebox = None
CT = None
Battle = None
_bb_window_global = None

# 配置
TCP_PORT = 25001

# 客户端连接列表
_tcp_clients = []
import threading
_tcp_clients_lock = threading.Lock()

# 弹窗等待响应
_popup_wait_dict = {}
_popup_wait_lock = None

def update_bb_window(bb_window):
    """更新全局 bb_window 引用"""
    global _bb_window_global
    _bb_window_global = bb_window
    log_to_file(f"[TCP-Server] bb_window updated: {bb_window}")

def _remove_popup_from_queue(popup_id):
    """从队列中移除指定弹窗"""
    global popup_event_queue
    if popup_event_queue is None:
        return
    
    temp_list = []
    while not popup_event_queue.empty():
        try:
            p = popup_event_queue.get_nowait()
            if p['id'] != popup_id:
                temp_list.append(p)
        except:
            break
    
    for p in temp_list:
        popup_event_queue.put(p)

def _broadcast_to_clients(data):
    """向所有 TCP 客户端广播消息"""
    global _tcp_clients, _tcp_clients_lock
    if not _tcp_clients:
        return
    
    import json
    try:
        msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
        msg_with_len = len(msg).to_bytes(4, 'big') + msg
        
        with _tcp_clients_lock:
            disconnected = []
            for client in _tcp_clients:
                try:
                    client.sendall(msg_with_len)
                except:
                    disconnected.append(client)
            
            for client in disconnected:
                _tcp_clients.remove(client)
                try:
                    client.close()
                except:
                    pass
    except Exception as e:
        log_to_file(f"[TCP] 广播失败: {e}")

def start_tcp_server(bb_window, port=25001):
    """启动 TCP 服务器"""
    import socket
    import threading
    import queue
    import json
    from tkinter import messagebox
    
    global CT, Battle, Windows, LDdevice, Mumudevice
    global tcp_server_instance, popup_event_queue, original_messagebox
    global _popup_wait_lock
    
    popup_event_queue = queue.Queue()
    _popup_wait_lock = threading.Lock()
    
    # 先设置弹窗拦截（最重要）
    # 模块导入延迟到免责声明处理完之后
    
    # 拦截 messagebox
    original_messagebox = {
        'showinfo': messagebox.showinfo,
        'showwarning': messagebox.showwarning,
        'showerror': messagebox.showerror,
        'askokcancel': messagebox.askokcancel,
        'askyesno': messagebox.askyesno,
        'askretrycancel': messagebox.askretrycancel
    }
    
    CONTROLLED_POPUPS = ["免责声明", "助战排序不符合", "队伍配置错误", "自动连接失败", "脚本停止！", "正在结束任务！"]
    
    def create_popup_wrapper(func_name, original_func):
        def wrapper(title, message, **kwargs):
            is_controlled = any(keyword in title for keyword in CONTROLLED_POPUPS)
            if not is_controlled:
                return original_func(title, message, **kwargs)
            
            import time
            popup_id = str(time.time())
            
            with _popup_wait_lock:
                _popup_wait_dict[popup_id] = {
                    'result': None,
                    'title': title,
                    'message': message,
                    'type': func_name,
                    'status': 'waiting'
                }
            
            # 修复编码
            def fix_encoding(s):
                if isinstance(s, bytes):
                    return s.decode('utf-8', errors='replace')
                try:
                    return s.encode('latin-1').decode('gbk', errors='replace')
                except:
                    return s
            
            popup_data = {
                'type': 'popup',
                'id': popup_id,
                'popup_type': func_name,
                'title': fix_encoding(title),
                'message': fix_encoding(message)
            }
            popup_event_queue.put(popup_data)
            log_to_file(f"[Popup] {title}")
            
            # TCP 广播
            _broadcast_to_clients(popup_data)
            
            return create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs)
        return wrapper
    
    def create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs):
        import threading
        import time
        import ctypes
        
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        
        popup_data = {'value': None, 'resolved': False}
        
        def monitor():
            # 免责声明也走外部决策流程，不再自动关闭
            
            while not popup_data['resolved']:
                with _popup_wait_lock:
                    info = _popup_wait_dict.get(popup_id)
                    if info and info.get('status') == 'resolved':
                        popup_data['value'] = info.get('result')
                        popup_data['resolved'] = True
                        # 关闭弹窗窗口
                        hwnd = user32.FindWindowW(None, title)
                        if hwnd:
                            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                            log_to_file(f"[Popup] 发送关闭消息: {title}")
                        break
                time.sleep(0.1)
            
            # 等待弹窗实际关闭（最多3秒）
            for _ in range(30):
                hwnd = user32.FindWindowW(None, title)
                if not hwnd:
                    # 弹窗已关闭
                    log_to_file(f"[Popup] 弹窗已确认关闭: {title}")
                    break
                time.sleep(0.1)
            
            # 从队列移除
            _remove_popup_from_queue(popup_id)
            
            # 推送弹窗已关闭的信息给所有客户端
            close_notify = {
                'type': 'popup_closed',
                'id': popup_id,
                'title': title,
                'result': popup_data['value']
            }
            _broadcast_to_clients(close_notify)
            log_to_file(f"[TCP] 弹窗关闭通知已广播: {popup_id}")
            
            # 免责声明关闭后，导入模块
            if "免责声明" in title:
                log_to_file("[Auto] 免责声明已关闭，开始导入模块")
                ensure_imports()
        
        t = threading.Thread(target=monitor, daemon=True)
        t.start()
        
        original_func(title, message, **kwargs)
        t.join(timeout=1)
        
        with _popup_wait_lock:
            _popup_wait_dict.pop(popup_id, None)
        _remove_popup_from_queue(popup_id)
        
        result = popup_data['value'] if popup_data['resolved'] else None
        if func_name == 'askyesno':
            return result == 'yes'
        elif func_name == 'askokcancel':
            return result == 'ok'
        elif func_name == 'askretrycancel':
            return result == 'retry'
        return None
    
    # 替换 messagebox
    messagebox.showinfo = create_popup_wrapper('showinfo', original_messagebox['showinfo'])
    messagebox.showwarning = create_popup_wrapper('showwarning', original_messagebox['showwarning'])
    messagebox.showerror = create_popup_wrapper('showerror', original_messagebox['showerror'])
    messagebox.askokcancel = create_popup_wrapper('askokcancel', original_messagebox['askokcancel'])
    messagebox.askyesno = create_popup_wrapper('askyesno', original_messagebox['askyesno'])
    messagebox.askretrycancel = create_popup_wrapper('askretrycancel', original_messagebox['askretrycancel'])
    
    def handle_client(client, addr):
        """处理客户端连接"""
        log_to_file(f"[TCP] Client connected: {addr}")
        with _tcp_clients_lock:
            _tcp_clients.append(client)
        
        try:
            while True:
                # 读取4字节长度
                len_bytes = client.recv(4)
                if not len_bytes or len(len_bytes) < 4:
                    break
                
                msg_len = int.from_bytes(len_bytes, 'big')
                if msg_len > 65535:
                    break
                
                # 读取JSON数据
                data_bytes = b''
                while len(data_bytes) < msg_len:
                    chunk = client.recv(msg_len - len(data_bytes))
                    if not chunk:
                        break
                    data_bytes += chunk
                
                if len(data_bytes) != msg_len:
                    break
                
                # 解析命令
                try:
                    cmd = json.loads(data_bytes.decode('utf-8'))
                    response = handle_command(cmd)
                except Exception as e:
                    response = {'success': False, 'error': str(e)}
                
                # 发送响应
                resp_bytes = json.dumps(response, ensure_ascii=False).encode('utf-8')
                client.sendall(len(resp_bytes).to_bytes(4, 'big') + resp_bytes)
                
        except Exception as e:
            log_to_file(f"[TCP] Client error: {e}")
        finally:
            with _tcp_clients_lock:
                if client in _tcp_clients:
                    _tcp_clients.remove(client)
            try:
                client.close()
            except:
                pass
            log_to_file(f"[TCP] Client disconnected: {addr}")
    
    def ensure_imports():
        """确保模块已导入（延迟导入，避免在免责声明前初始化）"""
        global CT, Battle, Windows, LDdevice, Mumudevice
        if CT is not None:
            return
        
        try:
            from consts import Consts as CT
            log_to_file("[Import] CT imported")
        except Exception as e:
            log_to_file(f"[Import Warning] CT: {e}")
            class MockCT:
                Gold = "gold"; Silver = "silver"; Copper = "copper"
                Blue = "blue"; Colorful = "colorful"
                BATTLE_TYPE = ['连续出击(或强化本)', '自动编队爬塔(应用操作序列设置)']
            CT = MockCT()
        
        try:
            from device import Windows, LDdevice, Mumudevice
            log_to_file("[Import] device imported")
        except Exception as e:
            log_to_file(f"[Import Warning] device: {e}")
        
        try:
            from FGObattle import Battle
            log_to_file("[Import] Battle imported")
        except Exception as e:
            log_to_file(f"[Import Warning] Battle: {e}")
    
    def handle_command(cmd):
        """处理命令"""
        ensure_imports()
        log_to_file(f"[API] handle_command raw: {cmd}, type={type(cmd)}")
        
        # 确保 cmd 是 dict
        if isinstance(cmd, list):
            cmd = cmd[0] if cmd else {}
        if not isinstance(cmd, dict):
            return {'success': False, 'error': f'Invalid command format: {type(cmd)}'}
        
        command = cmd.get('cmd', '')
        args = cmd.get('args', {})
        if not isinstance(args, dict):
            args = {}
        
        log_to_file(f"[API] command={command}, args={args}")
        
        try:
            if command == 'connect_mumu':
                result = api_connect_mumu(_bb_window_global, type('Args', (), {
                    'path': args.get('path'),
                    'index': args.get('index', 0),
                    'pkg': args.get('pkg'),
                    'app_index': args.get('app_index', 0)
                })())
                return {'success': result}
            
            elif command == 'connect_ld':
                result = api_connect_ld(_bb_window_global, type('Args', (), {
                    'path': args.get('path'),
                    'index': args.get('index', 0)
                })())
                return {'success': result}
            
            elif command == 'connect_adb':
                result = api_connect_adb(_bb_window_global, type('Args', (), {
                    'ip': args.get('ip')
                })())
                return {'success': result}
            
            elif command == 'set_appletype':
                page = _bb_window_global.pages[0]
                apple_type = args.get('type')
                log_to_file(f"[API] set_appletype: {apple_type}, page={page}, appleSet={page.appleSet}")
                api_set_apple_type(page, apple_type)
                return {'success': True}
            
            elif command == 'set_runcount':
                page = _bb_window_global.pages[0]
                times = args.get('times')
                log_to_file(f"[API] set_runcount: {times}, page={page}, appleSet={page.appleSet}")
                api_set_run_times(page, times)
                return {'success': True}
            
            elif command == 'set_battletype':
                page = _bb_window_global.pages[0]
                api_set_battle_type(page, args.get('type'))
                return {'success': True}
            
            elif command == 'start':
                page = _bb_window_global.pages[0]
                result = api_start_battle(page)
                return {'success': result}
            
            elif command == 'stop':
                page = _bb_window_global.pages[0]
                if page.device.running:
                    page.device.stop()
                return {'success': True}
            
            elif command == 'get_status':
                return {
                    'success': True,
                    'popup_queue': popup_event_queue.qsize() if popup_event_queue else 0
                }
            
            elif command == 'get_popups':
                # 获取等待中的弹窗列表
                popups = []
                temp_list = []
                while not popup_event_queue.empty():
                    try:
                        p = popup_event_queue.get_nowait()
                        temp_list.append(p)
                        with _popup_wait_lock:
                            is_waiting = p['id'] in _popup_wait_dict
                        if is_waiting:
                            popups.append({
                                'id': p['id'],
                                'title': p['title'],
                                'message': p['message'],
                                'type': p['type']
                            })
                    except:
                        break
                
                for p in temp_list:
                    popup_event_queue.put(p)
                
                return {'success': True, 'popups': popups}
            
            elif command == 'load_config':
                filename = args.get('filename', '')
                if not filename:
                    return {'success': False, 'error': 'filename required'}
                result = api_load_config(_bb_window_global, filename)
                return {'success': result}
            
            elif command == 'popup_response':
                popup_id = args.get('id', '')
                action = args.get('action', '')
                
                with _popup_wait_lock:
                    popup_info = _popup_wait_dict.get(popup_id)
                    if popup_info and popup_info.get('status') == 'waiting':
                        popup_info['result'] = action
                        popup_info['status'] = 'resolved'
                        return {'success': True}
                    else:
                        return {'success': False, 'message': 'Popup not found or resolved'}
            
            else:
                return {'success': False, 'error': f'Unknown command: {command}'}
                
        except Exception as e:
            import traceback
            log_to_file(f"[Command Error] {command}: {e}")
            return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}
    
    def run_server():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(5)
        tcp_server_instance = sock
        print(f"[TCP-Server] Started on 127.0.0.1:{port}")
        log_to_file(f"[TCP-Server] Started on port {port}")
        
        while True:
            try:
                client, addr = sock.accept()
                threading.Thread(target=handle_client, args=(client, addr), daemon=True).start()
            except Exception as e:
                log_to_file(f"[TCP] Server error: {e}")
                break
    
    threading.Thread(target=run_server, daemon=True).start()


# ==================== API Functions ====================

def api_connect_mumu(bb, args):
    """连接 MuMu 模拟器"""
    import os
    import json
    
    page = bb.pages[0]
    path = getattr(args, 'path', None)
    index = getattr(args, 'index', 0) or 0
    pkg = getattr(args, 'pkg', None)
    app_index = getattr(args, 'app_index', 0) or 0
    
    if not path:
        if os.path.exists("MuMuInstallPath.txt"):
            with open("MuMuInstallPath.txt", "r", encoding="utf8") as f:
                path = f.read().strip()
        if not path:
            print("[API错误] 未指定MuMu安装路径")
            return False
    
    try:
        path = Mumudevice.check_mumuInstallPath(path)
        with open("MuMuInstallPath.txt", "w", encoding="utf8") as f:
            f.write(path)
        Mumudevice.mumuPath = path
        
        emulator_name = f"MuMu模拟器12-{index}" if index > 0 else "MuMu模拟器12"
        pkg_name = pkg if pkg else "com.bilibili.fatego"
        
        device = Mumudevice(path, index, app_index, pkg_name, use_manager=True)
        serialno = {'name': emulator_name, 'pkg': pkg_name, 'appIndex': app_index}
        device.set_serialno(json.dumps(serialno, ensure_ascii=False))
        device.snapshot()
        
        page.snapshotDevice = page.operateDevice = page.device.snapshotDevice = page.device.operateDevice = device
        bb.pagebar.tags[page.idx].createText(True)
        bb.updateConnectLst(page.idx)
        return True
    except Exception as e:
        log_to_file(f"[API Error] MuMu connect: {e}")
        return False

def api_set_apple_type(page, apple_type):
    """设置苹果类型"""
    if not apple_type:
        return
    
    apple_map = {
        "gold": CT.Gold,
        "silver": CT.Silver,
        "blue": CT.Blue,
        "copper": CT.Copper,
        "colorful": CT.Colorful
    }
    
    if apple_type in apple_map:
        page.appleSet.appleType = apple_map[apple_type]
        try:
            page.appleSet.appleIconPhoto = page.appleSet.getAppleIconPhoto()
            page.appleSet.appleIcon.config(image=page.appleSet.appleIconPhoto)
            print(f"[API] 苹果类型已设置: {apple_type}")
        except Exception as e:
            print(f"[API警告] 苹果类型已设置但UI更新失败: {e}")
            print(f"[API] 苹果类型已设置: {apple_type}")

def api_set_run_times(page, times):
    """设置运行次数"""
    if times is not None:
        page.appleSet.runTimes.set(times)
        print(f"[API] 运行次数: {times}")

def api_set_battle_type(page, battle_type):
    """设置战斗类型"""
    if battle_type == "continuous":
        page.battletype.set(CT.BATTLE_TYPE[0])
        print("[API] 战斗类型: 连续出击")
    elif battle_type == "single":
        page.battletype.set(CT.BATTLE_TYPE[1])
        print("[API] 战斗类型: 单次")

def api_load_config(bb, filename):
    """加载队伍配置文件（从第4步开始：直接应用配置）"""
    import os
    import json
    
    try:
        page = bb.pages[0]
        
        # 1. 构建文件路径
        config_path = os.path.join("settings", filename)
        if not os.path.exists(config_path):
            log_to_file(f"[LoadConfig] 配置文件不存在: {config_path}")
            return False
        
        # 2. 读取配置
        with open(config_path, "r", encoding="utf8") as fp:
            SS = json.load(fp)
        
        # 3. 保留当前连接信息
        SS["connectMode"] = page.SS.get("connectMode", None)
        SS["snapshotDevice"] = page.SS.get("snapshotDevice", None)
        SS["operateDevice"] = page.SS.get("operateDevice", None)
        
        # 4. 应用配置
        page.SS = SS
        
        # 5. 重置页面
        while True:
            try:
                page.reset()
                break
            except Exception as e:
                log_to_file(f"[LoadConfig] reset error: {e}, retry...")
                import traceback
                traceback.print_exc()
        
        # 6. 保存配置
        bb.saveJsons()
        
        log_to_file(f"[LoadConfig] 配置已加载: {filename}")
        return True
    except Exception as e:
        log_to_file(f"[LoadConfig] Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def api_connect_ld(bb, args):
    """连接雷电模拟器（高速接口）"""
    import os
    import json
    
    page = bb.pages[0]
    
    path = getattr(args, 'path', None)
    index = getattr(args, 'index', 0) or 0
    
    if not path:
        if os.path.exists("LDInstallPath.txt"):
            with open("LDInstallPath.txt", "r", encoding="utf8") as f:
                path = f.read().strip()
        if not path:
            print("[API错误] 未指定雷电安装路径")
            return False
    
    try:
        path = LDdevice.checkPath(path)
        with open("LDInstallPath.txt", "w", encoding="utf8") as f:
            f.write(path)
        
        device = LDdevice(path, index)
        serialno = {'name': str(index)}
        serialno_str = json.dumps(serialno, ensure_ascii=False)
        device.set_serialno(serialno_str)
        device.snapshot()
        
        # 创建 Windows 触摸设备
        from device import Windows
        touchDevice = Windows(device.player.bndWnd)
        
        # 设置设备
        page.snapshotDevice = page.device.snapshotDevice = device
        page.operateDevice = page.device.operateDevice = touchDevice
        
        bb.pagebar.tags[page.idx].createText(True)
        bb.updateConnectLst(page.idx)
        print(f"[API] 雷电连接成功：编号{index}")
        return True
    except Exception as e:
        print(f"[API错误] 雷电连接失败：{e}")
        import traceback
        traceback.print_exc()
        return False

def api_connect_adb(bb, args):
    """连接 ADB 设备（IP:端口方式）"""
    import os
    import sys
    
    page = bb.pages[0]
    
    ip_port = getattr(args, 'ip', None)
    if not ip_port:
        print("[API错误] ADB连接需要指定ip参数")
        return False
    
    try:
        # 使用 airtest 内置的 adb
        adb_path = os.path.join(os.path.dirname(sys.executable), "airtest", "core", "android", "static", "adb", "windows")
        if not os.path.exists(os.path.join(adb_path, "adb.exe")):
            log_to_file(f"[API错误] 找不到 adb.exe: {adb_path}")
            return False
        
        # 执行 adb connect
        from bbcmd import cmd
        print(cmd(f'"{adb_path}/adb" connect {ip_port}'))
        
        # 创建设备实例
        from device import Android, USE_AS_BOTH
        device = Android(ip_port, page.server, USE_AS_BOTH, cap_method="Minicap")
        
        if not device.available:
            device.disconnect()
            print("[API错误] ADB设备连接失败")
            return False
        
        page.snapshotDevice = page.operateDevice = page.device.snapshotDevice = page.device.operateDevice = device
        bb.pagebar.tags[page.idx].createText(True)
        bb.updateConnectLst(page.idx)
        print(f"[API] ADB连接成功: {ip_port}")
        return True
    except Exception as e:
        print(f"[API错误] ADB连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def api_start_battle(page):
    """开始战斗"""
    if Battle is None:
        return False
    
    try:
        for i in range(3):
            if not page.servantGroup[i].exist:
                return False
        
        btn_x = page.start.winfo_width() // 2
        btn_y = page.start.winfo_height() // 2
        page.start.event_generate("<Button-1>", x=btn_x, y=btn_y)
        return True
    except Exception as e:
        log_to_file(f"[API Error] Start battle: {e}")
        return False

def log_to_file(msg):
    """写入日志"""
    try:
        import os
        from datetime import datetime
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bbc_tcp_server.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
    except:
        pass
