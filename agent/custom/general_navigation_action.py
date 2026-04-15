import json
import os
import time
import logging
import traceback
import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# --- 独立日志配置 ---
_nav_logger = logging.getLogger("GeneralNavigation")
if not _nav_logger.handlers: # 防止重复添加 Handler
    _nav_logger.setLevel(logging.DEBUG)
    _log_file = os.path.join(os.path.dirname(__file__), 'nav_debug.log')
    fh = logging.FileHandler(_log_file, mode='w', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    _nav_logger.addHandler(fh)

# --- 中英文章节名映射表 (根据实际资源文件名补充) ---
CHAPTER_MAP = {
    "冬木": "Fuyuki",
    "奥尔良": "Orleans",
    "七丘之城": "Septem",
    "俄刻阿诺斯": "Okeanos",
    "伦敦": "London",
    "合众为一": "Unum",
    "卡美洛": "Camelot",
    "巴比伦尼亚": "Babylonia",
    "新宿": "Shinjuku",
    "雅戈泰": "Agartha",
    "下总国": "Shimousa",
    "塞勒姆": "Salem",
    "阿纳斯塔西娅": "Anastasia",
    "诸神黄昏": "Gotterdammerung",
    "SIN": "SIN",
    "由伽刹多罗": "Yugakshetra",
    "亚特兰蒂斯": "Atlantis",
    "奥林波斯": "Olympus",
    "平安京": "Heiankyo",
    "阿瓦隆勒菲": "AvalonleFae",
    "Traum": "Traum",
    "平面之月": "PaperMoon",
    "原型肇始": "Gehenna",
    "伊底": "Archetype",
    "梅塔特罗诺斯": "Metatronius",
}

@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action (FGO-py 纯复刻版)"""
        _nav_logger.info("="*50)
        _nav_logger.info("[Nav] general_navigation action started (CV2 Shell Mode)!")
        try:
            # 1. 从地图坐标导航节点获取参数
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get("attach", {})
            chapter_cn = attach_data.get("chapter", "")
            target_quest = attach_data.get("quests", "")
            
            if not chapter_cn or not target_quest:
                return CustomAction.RunResult(success=False)
            
            # 2. 章节名映射 (中文 -> 英文文件名)
            map_name = CHAPTER_MAP.get(chapter_cn, chapter_cn)
            _nav_logger.info(f"[Nav] Mapped chapter '{chapter_cn}' to file '{map_name}'")

            # 3. 加载地图坐标映射 JSON
            AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            map_file = os.path.join(AGENT_ROOT, "utils", "map_coordinates.json")
            
            with open(map_file, 'r', encoding='utf-8') as f:
                coordinates_data = json.load(f)
            
            quest_list = coordinates_data.get("maps", {}).get(map_name, [])
            quest_coordinates = None
            
            for item in quest_list:
                if isinstance(item, list) and len(item) >= 2:
                    q_name, q_pos = item[0], item[1]
                    if q_name == target_quest:
                        quest_coordinates = q_pos
                        break
                        
            if not quest_coordinates:
                return CustomAction.RunResult(success=False)
            
            target_x, target_y = quest_coordinates

            # 4. 加载大地图模板 (采用 FGO-py 的 cv2.imread 方式)
            _nav_logger.info("[Nav] Step 4: Loading map template...")
            # AGENT_ROOT 指向 agent/，resource 在其父目录（根目录）下
            map_template_path = os.path.join(AGENT_ROOT, "..", "resource", "common", "image", "地图坐标导航", f"{map_name}.png")
            
            map_template = cv2.imread(map_template_path)
            
            _nav_logger.info(f"[Nav] Template shape: {map_template.shape if map_template is not None else 'None'}")
            if map_template is None:
                _nav_logger.error(f"[Nav] Error: Failed to load template image at {map_template_path}")
                return CustomAction.RunResult(success=False)

            # 5. 【核心】采用 FGO-py 方式的截图 (通过 ADB Shell)
            controller = context.tasker.controller
            context.run_task("UI隐藏")
            time.sleep(1)
            
            # 执行 adb shell screencap -p 获取 PNG 二进制流
            png_data = controller.post_command(["shell", "screencap", "-p"]).wait().get()
            screen = cv2.imdecode(np.frombuffer(png_data, np.uint8), cv2.IMREAD_COLOR)
            
            if screen is None:
                return CustomAction.RunResult(success=False)

            # 6. 预处理与反向匹配 (完全照搬 FGO-py 逻辑)
            map_region = screen[200:520, 200:1080]
            resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
            
            result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            
            if min_val > 0.5:
                _nav_logger.error(f"[Nav] Initial match failed! min_val={min_val}")
                return CustomAction.RunResult(success=False)

            current_x = int(min_loc[0] / 0.3 + 440)
            current_y = int(min_loc[1] / 0.3 + 160)
            _nav_logger.info(f"[Nav] Initial pos: ({current_x}, {current_y})")

            # 7. 导航循环
            poly = np.array([
                [230, 40], [230, 200], [40, 200], [40, 450],
                [150, 450], [220, 520], [630, 520], [630, 680],
                [980, 680], [980, 570], [1240, 570], [1240, 40]
            ])
            
            for iteration in range(10):
                dx = target_x - current_x
                dy = target_y - current_y
                screen_target_x = 640 + dx
                screen_target_y = 360 + dy
                
                if cv2.pointPolygonTest(poly, (float(screen_target_x), float(screen_target_y)), False) >= 0:
                    _nav_logger.info("[Nav] Target visible. Clicking...")
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)
                    controller.post_click(int(screen_target_x), int(screen_target_y)).wait()
                    return CustomAction.RunResult(success=True)
                
                # 滑动逻辑
                distance = (dx**2 + dy**2)**0.5
                if distance == 0: break
                scale = min(590/abs(dx) if dx != 0 else float('inf'), 310/abs(dy) if dy != 0 else float('inf'), 0.5)
                slide_dx, slide_dy = dx * scale, dy * scale
                
                controller.post_swipe(int(640 + slide_dx), int(360 + slide_dy), int(640 - slide_dx), int(360 - slide_dy), 1000).wait()
                time.sleep(1.5)
                
                # 重新定位 (同样使用 Shell 截图)
                png_data = controller.post_command(["shell", "screencap", "-p"]).wait().get()
                screen = cv2.imdecode(np.frombuffer(png_data, np.uint8), cv2.IMREAD_COLOR)
                if screen is None: return CustomAction.RunResult(success=False)
                
                map_region = screen[200:520, 200:1080]
                resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
                result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
                min_val, _, min_loc, _ = cv2.minMaxLoc(result)
                
                if min_val > 0.5: return CustomAction.RunResult(success=False)
                
                current_x = int(min_loc[0] / 0.3 + 440)
                current_y = int(min_loc[1] / 0.3 + 160)
                _nav_logger.info(f"[Nav] New pos: ({current_x}, {current_y})")

            return CustomAction.RunResult(success=False)
                
        except Exception as e:
            _nav_logger.error(f"[Nav] CRITICAL: {str(e)}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)
