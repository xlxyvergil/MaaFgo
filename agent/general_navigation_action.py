import json
import os
import time
import logging
import traceback

# --- 独立日志配置 ---
_nav_logger = logging.getLogger("GeneralNavigation")
if not _nav_logger.handlers: # 防止重复添加 Handler
    _nav_logger.setLevel(logging.DEBUG)
    _log_file = os.path.join(os.path.dirname(__file__), 'nav_debug.log')
    fh = logging.FileHandler(_log_file, mode='w', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    _nav_logger.addHandler(fh)
# --------------------

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action
        
        从地图坐标导航节点获取章节和关卡参数，执行地图相机定位和导航
        """
        _nav_logger.info("="*50)
        _nav_logger.info("[Nav] general_navigation action started!")
        try:
            # 1. 从地图坐标导航节点获取参数
            _nav_logger.info("[Nav] Step 1: Getting node data...")
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                _nav_logger.error("[Nav] Error: node_data is None")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get("attach", {})
            chapter_code = attach_data.get("chapter", "")
            target_quest = attach_data.get("quests", "")
            _nav_logger.info(f"[Nav] Params: chapter={chapter_code}, quest={target_quest}")
            
            if not chapter_code or not target_quest:
                _nav_logger.error(f"[Nav] Error: Incomplete parameters")
                return CustomAction.RunResult(success=False)
            
            # 移除前缀 "c" 获取地图名称
            map_name = chapter_code.replace("c", "", 1) if chapter_code.startswith("c") else chapter_code
            _nav_logger.info(f"[Nav] Map name resolved: {map_name}")
            
            # 2. 加载地图坐标映射
            _nav_logger.info("[Nav] Step 2: Loading map_coordinates.json...")
            try:
                map_file = os.path.join(os.path.dirname(__file__), "map_coordinates.json")
                with open(map_file, 'r', encoding='utf-8') as f:
                    coordinates_data = json.load(f)
            except Exception as e:
                _nav_logger.error(f"[Nav] Error loading JSON: {e}")
                return CustomAction.RunResult(success=False)
            
            # 3. 获取目标关卡坐标
            _nav_logger.info(f"[Nav] Step 3: Searching for {map_name} -> {target_quest}")
            quest_list = coordinates_data.get("maps", {}).get(map_name, [])
            quest_coordinates = None
            
            # 遍历列表查找匹配的关卡名（适配 JSON 数组结构）
            for item in quest_list:
                if isinstance(item, list) and len(item) >= 2:
                    q_name, q_pos = item[0], item[1]
                    if q_name == target_quest:
                        quest_coordinates = q_pos
                        break
                        
            if not quest_coordinates:
                _nav_logger.error(f"[Nav] Error: Coordinates not found in JSON")
                return CustomAction.RunResult(success=False)
            
            target_x, target_y = quest_coordinates
            _nav_logger.info(f"[Nav] Target coordinates found: ({target_x}, {target_y})")
            
            # 4. 加载大地图模板
            _nav_logger.info("[Nav] Step 4: Loading map template...")
            # 适配打包后的结构：agent -> resource -> common -> image
            map_template_path = os.path.join(os.path.dirname(__file__), "..", "resource", "common", "image", "地图坐标导航", f"{map_name}.png")
            _nav_logger.info(f"[Nav] Template path resolved: {os.path.abspath(map_template_path)}")
            
            if not os.path.exists(map_template_path):
                _nav_logger.error(f"[Nav] Error: Template file missing at {map_template_path}")
                return CustomAction.RunResult(success=False)
            
            # 检查文件大小
            file_size = os.path.getsize(map_template_path)
            _nav_logger.info(f"[Nav] Template file size: {file_size} bytes ({file_size/1024:.1f} KB)")
            
            # 使用 cv2.imdecode 读取中文路径图片
            with open(map_template_path, 'rb') as f:
                file_data = f.read()
            map_template = cv2.imdecode(np.frombuffer(file_data, dtype=np.uint8), cv2.IMREAD_COLOR)
            
            _nav_logger.info(f"[Nav] Template shape: {map_template.shape if map_template is not None else 'None'}")
            if map_template is None:
                _nav_logger.error(f"[Nav] Error: Failed to load template image")
                return CustomAction.RunResult(success=False)
            
            _nav_logger.info(f"[Nav] Template shape: {map_template.shape}")
            
            # 5. 检测并隐藏 UI
            _nav_logger.info("[Nav] Step 5: Detecting UI hide button...")
            controller = context.tasker.controller
            
            # 使用 Pipeline 节点识别并点击 UI 隐藏按钮
            _nav_logger.info("[Nav] Running pipeline node 'UI隐藏'...")
            context.run_task("UI隐藏")
            time.sleep(1)  # 等待 UI 隐藏动画完成
            
            # 获取截图
            screen = controller.post_screencap().wait().get()
            
            if screen is None or screen.size == 0:
                _nav_logger.error("[Nav] Error: Screenshot failed")
                return CustomAction.RunResult(success=False)
            
            # 裁剪地图区域（与 FGO-py 保持一致）
            # [FIX] 确保截图是 3 通道 RGB（防止 RGBA 导致匹配失败）
            if screen.shape[2] == 4:
                screen = cv2.cvtColor(screen, cv2.COLOR_RGBA2RGB)
                
            map_region = screen[200:520, 200:1080]
            
            # 调整大小以提高匹配速度
            resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
            
            # [DEBUG] 保存实际裁剪的地图区域，方便对比
            cv2.imwrite(os.path.join(os.path.dirname(__file__), 'debug_map_region.png'), map_region)
            _nav_logger.info("[Nav] Saved debug crop to debug_map_region.png")
            
            # 反向模板匹配：在大地图中找截图位置（与 FGO-py 一致）
            # cv2.matchTemplate(大图, 小模板, 方法)
            result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # 检查匹配质量
            _nav_logger.info(f"[Nav] Match quality (min_val): {min_val:.4f}")
            if min_val > 0.5:
                _nav_logger.error(f"[Nav] Error: Map template match failed! min_val={min_val}")
                return CustomAction.RunResult(success=False)

            # 计算当前位置（还原到原始坐标，与 FGO-py 公式一致）
            current_x = int(min_loc[0] / 0.3 + 440)
            current_y = int(min_loc[1] / 0.3 + 160)
            _nav_logger.info(f"[Nav] Initial camera position: ({current_x}, {current_y})")
            
            # 6. 执行导航（改进版：循环滑动直到目标可见）
            
            # 定义地图可视区域多边形（与 FGO-py 一致）
            poly = np.array([
                [230, 40], [230, 200], [40, 200], [40, 450],
                [150, 450], [220, 520], [630, 520], [630, 680],
                [980, 680], [980, 570], [1240, 570], [1240, 40]
            ])
            
            max_iterations = 10  # 防止无限循环
            for iteration in range(max_iterations):
                _nav_logger.info(f"[Nav] --- Iteration {iteration + 1}/{max_iterations} ---")
                
                # 1. 计算目标点相对于相机中心的偏移量
                dx = target_x - current_x
                dy = target_y - current_y
                
                # 2. 计算目标点在屏幕上的实际坐标 (参考 FGO-py: p = center + offset)
                screen_target_x = 640 + dx
                screen_target_y = 360 + dy
                
                # 3. 检查目标点在屏幕上的位置是否在可视区域内
                target_point_on_screen = np.array([screen_target_x, screen_target_y])
                if cv2.pointPolygonTest(poly, tuple(target_point_on_screen.astype(float)), False) >= 0:
                    _nav_logger.info(f"[Nav] Target is VISIBLE on screen at ({int(screen_target_x)}, {int(screen_target_y)})")
                    
                    # [FIX] 与 FGO-py 一致：定位完成后，先点击两次关闭地图说明弹窗
                    _nav_logger.info("[Nav] Closing map info popup...")
                    controller.post_click(1231, 687).wait()  # 第一次
                    time.sleep(0.3)
                    controller.post_click(1231, 687).wait()  # 第二次
                    time.sleep(0.3)
                    
                    # 点击屏幕上的对应位置
                    controller.post_click(int(screen_target_x), int(screen_target_y)).wait()
                    _nav_logger.info("[Nav] Click executed.")
                    _nav_logger.info("[Nav] Returning success=True")
                    return CustomAction.RunResult(success=True)
                
                _nav_logger.info(f"[Nav] Target not visible (Screen pos: {int(screen_target_x)}, {int(screen_target_y)}). Swiping...")
                # 计算滑动向量（限制最大距离）
                distance = (dx**2 + dy**2)**0.5
                if distance == 0:
                    break
                
                scale = min(590/abs(dx) if dx != 0 else float('inf'),
                           310/abs(dy) if dy != 0 else float('inf'),
                           0.5)
                slide_dx = dx * scale
                slide_dy = dy * scale
                
                # 执行滑动（从中心向相反方向滑动）
                start_x = 640 + slide_dx
                start_y = 360 + slide_dy
                end_x = 640 - slide_dx
                end_y = 360 - slide_dy
                
                _nav_logger.info(f"[Nav] Swiping from ({int(start_x)}, {int(start_y)}) to ({int(end_x)}, {int(end_y)})")
                # 执行滑动 (标准写法)
                controller.post_swipe(int(start_x), int(start_y), int(end_x), int(end_y), 1000).wait()
                _nav_logger.info("[Nav] Swipe executed.")
                
                # 等待滑动完成并重新定位
                _nav_logger.info("[Nav] Waiting for swipe to stabilize...")
                time.sleep(1.5)
                
                # 重新获取截图并定位
                _nav_logger.info("[Nav] Re-capturing screenshot for re-positioning...")
                screen = controller.post_screencap().wait().get()
                if screen is None or screen.size == 0:
                    return CustomAction.RunResult(success=False)
                
                # [FIX] 循环内同样需要处理通道和颜色
                if screen.shape[2] == 4:
                    screen = cv2.cvtColor(screen, cv2.COLOR_RGBA2RGB)
                    
                map_region = screen[200:520, 200:1080]
                resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
                
                # [FIX] 修正参数顺序：在大地图中搜索截图
                result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                
                if min_val > 0.5:
                    _nav_logger.error(f"[Nav] Re-position match failed! min_val={min_val}")
                    return CustomAction.RunResult(success=False)
                    
                current_x = int(min_loc[0] / 0.3 + 440)
                current_y = int(min_loc[1] / 0.3 + 160)
                _nav_logger.info(f"[Nav] New camera position: ({current_x}, {current_y})")
            
            _nav_logger.warning("[Nav] Navigation timed out after max iterations.")
            return CustomAction.RunResult(success=False)
                
        except Exception as e:
            error_trace = traceback.format_exc()
            _nav_logger.error(f"[Nav] CRITICAL EXCEPTION: {str(e)}\n{error_trace}")
            return CustomAction.RunResult(success=False)
