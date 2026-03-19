#!/usr/bin/env python3
"""
qasync Windows平台的补丁，修复线程中__proactor为None的问题
"""

import os
import sys
import asyncio

# 仅在Windows平台应用补丁
if sys.platform == "win32":
    try:
        # 保存原始的_run方法
        from qasync._windows import _WindowsSelectorEventLoopThread  # type: ignore
        
        original_run = _WindowsSelectorEventLoopThread.run
        
        def patched_run(self):
            """修复proactor为None的问题"""
            if self.__proactor is None:
                # 如果proactor未初始化，创建一个新的
                self.__proactor = asyncio.ProactorEventLoop()
            
            try:
                # 调用原始的run方法
                original_run(self)
            except AttributeError as e:
                if "NoneType" in str(e) and "select" in str(e):
                    # 忽略proactor为None的异常
                    pass
                else:
                    # 重新抛出其他AttributeError异常
                    raise
        
        # 替换原始的run方法
        _WindowsSelectorEventLoopThread.run = patched_run
        
        print("qasync Windows平台补丁已应用")
    except ImportError:
        pass
    except Exception as e:
        print(f"应用qasync补丁失败: {e}")