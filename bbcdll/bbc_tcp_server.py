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

# 当前任务参数（用于弹窗自动处理）
_current_task_args = {}

# 任务结束标志（当用户选择取消时设置）
_task_should_end = False
_task_end_reason = ''

# 最后解决的弹窗信息（后覆盖前，用于返回给客户端）
_last_resolved_popup = None

def update_bb_window(bb_window):
    """更新全局 bb_window 引用"""
    global _bb_window_global
    _bb_window_global = bb_window

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
    
    CONTROLLED_POPUPS = [
        "免责声明！",           # showinfo
        "自动连接失败",         # askokcancel (MuMu/雷电)
        "助战排序不符合",       # askyesno
        "队伍配置错误！",       # askokcancel
        "正在结束任务！",       # showwarning
        "脚本停止！",           # showwarning
        "其他任务运行中",       # showwarning
        "自动关机中！"          # askyesno
    ]
    
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
            
            # 免责声明：延迟2秒后自动确认
            if '免责声明' in title:
                def auto_disclaimer():
                    time.sleep(2)
                    _resolve_popup(popup_id, 'ok')
                threading.Thread(target=auto_disclaimer, daemon=True).start()
            
            # 助战排序不符合：根据参数处理
            elif '助战排序不符合' in title:
                def auto_assist():
                    global _task_should_end, _task_end_reason
                    time.sleep(0.5)
                    support_continue = _current_task_args.get('support_order_mismatch', True)
                    if support_continue:
                        action = 'yes'
                    else:
                        action = 'no'
                        _task_should_end = True
                        _task_end_reason = 'assist_order_mismatch_cancelled'
                    _resolve_popup(popup_id, action)
                threading.Thread(target=auto_assist, daemon=True).start()
            
            # 队伍配置错误：根据参数处理
            elif '队伍配置错误' in title:
                def auto_team():
                    global _task_should_end, _task_end_reason
                    time.sleep(0.5)
                    team_continue = _current_task_args.get('team_config_error', True)
                    if team_continue:
                        action = 'ok'
                    else:
                        action = 'cancel'
                        _task_should_end = True
                        _task_end_reason = 'team_config_error_cancelled'
                    _resolve_popup(popup_id, action)
                threading.Thread(target=auto_team, daemon=True).start()
            
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
                        break
                time.sleep(0.1)
            
            # 等待弹窗实际关闭（最多3秒）
            for _ in range(30):
                hwnd = user32.FindWindowW(None, title)
                if not hwnd:
                    # 弹窗已关闭
                    break
                time.sleep(0.1)
            
            # 从队列移除
            _remove_popup_from_queue(popup_id)
            
            # 弹窗关闭通知功能已移除
            
            # 免责声明关闭后，导入模块
            if "免责声明" in title:
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
            pass
        finally:
            with _tcp_clients_lock:
                if client in _tcp_clients:
                    _tcp_clients.remove(client)
            try:
                client.close()
            except:
                pass
            pass
    
    def ensure_imports():
        """确保模块已导入（延迟导入，避免在免责声明前初始化）"""
        global CT, Battle, Windows, LDdevice, Mumudevice
        if CT is not None:
            return
        
        try:
            from consts import Consts as CT
        except Exception as e:
            pass
            class MockCT:
                Gold = "gold"; Silver = "silver"; Copper = "copper"
                Blue = "blue"; Colorful = "colorful"
                BATTLE_TYPE = ['连续出击(或强化本)', '自动编队爬塔(应用操作序列设置)']
            CT = MockCT()
        
        try:
            from device import Windows, LDdevice, Mumudevice
        except Exception as e:
            pass
        
        try:
            from FGObattle import Battle
        except Exception as e:
            pass
    
    def handle_command(cmd):
        """处理命令"""
        ensure_imports()
        
        # 确保 cmd 是 dict
        if isinstance(cmd, list):
            cmd = cmd[0] if cmd else {}
        if not isinstance(cmd, dict):
            return {'success': False, 'error': f'Invalid command format: {type(cmd)}'}
        
        command = cmd.get('cmd', '')
        args = cmd.get('args', {})
        if not isinstance(args, dict):
            args = {}
        
        pass
        
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
                api_set_apple_type(page, apple_type)
                return {'success': True}
            
            elif command == 'set_runcount':
                page = _bb_window_global.pages[0]
                times = args.get('times')
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
            
            elif command == 'run_bbc_task':
                # 执行完整BBC任务流程
                result = api_run_bbc_task(args)
                return result
            
            else:
                return {'success': False, 'error': f'Unknown command: {command}'}
                
        except Exception as e:
            import traceback
            return {'success': False, 'error': str(e), 'traceback': traceback.format_exc()}
    
    def run_server():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(5)
        tcp_server_instance = sock
        print(f"[TCP-Server] Started on 127.0.0.1:{port}")
        
        while True:
            try:
                client, addr = sock.accept()
                threading.Thread(target=handle_client, args=(client, addr), daemon=True).start()
            except Exception as e:
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
                import traceback
                traceback.print_exc()
        
        # 6. 保存配置
        bb.saveJsons()
        
        return True
    except Exception as e:
        pass
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
        return False

def api_run_bbc_task(args):
    """执行完整BBC任务流程"""
    import time
    global _current_task_args, _task_should_end, _task_end_reason
    
    # 重置结束标志
    _task_should_end = False
    _task_end_reason = ''
    
    team_config = args.get('team_config', '')
    run_count = args.get('run_count', 1)
    apple_type = args.get('apple_type', 'gold')
    battle_type = args.get('battle_type', 'continuous')
    connect = args.get('connect', 'auto')
    support_order_mismatch = args.get('support_order_mismatch', False)
    team_config_error = args.get('team_config_error', False)
    mumu_path = args.get('mumu_path', '')
    mumu_index = args.get('mumu_index', 0)
    ld_path = args.get('ld_path', '')
    ld_index = args.get('ld_index', 0)
    manual_port = args.get('manual_port', '')
    
    # 保存参数到全局变量，供弹窗处理使用
    _current_task_args = {
        'support_order_mismatch': support_order_mismatch,
        'team_config_error': team_config_error
    }
    
    # 等待免责声明关闭（自动处理，等待足够时间）
    time.sleep(5)  # 等待5秒确保免责声明已处理
    
    # 执行连接
    # 检查bb_window是否已设置
    if _bb_window_global is None:
        return {'success': False, 'reason': 'bb_window_not_set'}
    
    if connect == 'auto':
        # auto模式：不执行连接，依赖BBC自动连接
        pass
    elif connect == 'mumu' and mumu_path:
        if not api_connect_mumu(_bb_window_global, type('Args', (), {
            'path': mumu_path, 'index': mumu_index
        })()):
            return {
                'success': False,
                'reason': 'mumu_connect_failed',
                'result': {'type': 'connect_failed', 'category': 'error', 'description': 'MuMu模拟器连接失败'}
            }
    elif connect == 'ldplayer' and ld_path:
        if not api_connect_ld(_bb_window_global, type('Args', (), {
            'path': ld_path, 'index': ld_index
        })()):
            return {
                'success': False,
                'reason': 'ldplayer_connect_failed',
                'result': {'type': 'connect_failed', 'category': 'error', 'description': '雷电模拟器连接失败'}
            }
    elif connect == 'manual' and manual_port:
        if not api_connect_adb(_bb_window_global, type('Args', (), {
            'ip': manual_port
        })()):
            return {
                'success': False,
                'reason': 'adb_connect_failed',
                'result': {'type': 'connect_failed', 'category': 'error', 'description': 'ADB设备连接失败'}
            }
    # 等待连接完成
    time.sleep(1)
    
    # 加载配置
    if not api_load_config(_bb_window_global, team_config):
        return {
            'success': False,
            'error': f'配置文件加载失败: {team_config}'
        }
    
    # 设置参数
    page = _bb_window_global.pages[0]
    api_set_run_times(page, run_count)
    api_set_apple_type(page, apple_type)
    api_set_battle_type(page, battle_type)
    
    # 启动战斗
    if not api_start_battle(page):
        return {
            'success': False,
            'error': '战斗启动失败，请检查队伍配置'
        }
    
    # 轮询等待战斗结束
    # 无限等待战斗结束
    while True:
        # 检查是否有结束弹窗
        # 通过检查popup队列中是否有停止相关弹窗
        temp_list = []
        battle_ended = False
        end_reason = ''
        while not popup_event_queue.empty():
            try:
                p = popup_event_queue.get_nowait()
                temp_list.append(p)
                title = p.get('title', '')
                # 判断为战斗结束的弹窗
                end_popups = ['脚本停止！']
                if any(popup in title for popup in end_popups):
                    battle_ended = True
                    end_reason = p.get('message', '')
            except:
                break
        for p in temp_list:
            popup_event_queue.put(p)
        
        # 检查是否用户选择取消（从_last_resolved_popup获取弹窗信息）
        if _task_should_end:
            global _last_resolved_popup
            if _last_resolved_popup:
                return {
                    'success': False,
                    'popup_title': _last_resolved_popup['title'],
                    'popup_message': _last_resolved_popup['message'],
                    'user_decision': _last_resolved_popup['result']
                }
            else:
                return {
                    'success': False,
                    'popup_title': '',
                    'popup_message': _task_end_reason,
                    'user_decision': ''
                }
        
        if battle_ended:
            # 关闭结束弹窗并获取弹窗信息
            end_popup_info = None
            end_popups = ['脚本停止！']
            for p in temp_list:
                title = p.get('title', '')
                if any(popup in title for popup in end_popups):
                    end_popup_info = p
                    popup_id = p.get('id')
                    if popup_id:
                        _resolve_popup(popup_id, 'ok')
            
            if end_popup_info:
                return {
                    'success': True,
                    'popup_title': end_popup_info.get('title', ''),
                    'popup_message': end_popup_info.get('message', ''),
                    'user_decision': 'ok'
                }
            else:
                return {
                    'success': True,
                    'popup_title': '脚本停止！',
                    'popup_message': end_reason,
                    'user_decision': 'ok'
                }
        
        time.sleep(1)

def _resolve_popup(popup_id, action):
    """处理弹窗响应"""
    global _last_resolved_popup
    with _popup_wait_lock:
        popup_info = _popup_wait_dict.get(popup_id)
        if popup_info and popup_info.get('status') == 'waiting':
            popup_info['result'] = action
            popup_info['status'] = 'resolved'
            # 保存弹窗信息（后覆盖前，最后的就是结束的）
            _last_resolved_popup = {
                'title': popup_info.get('title', ''),
                'message': popup_info.get('message', ''),
                'result': action
            }

