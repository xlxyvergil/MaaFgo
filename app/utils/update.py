#   This file is part of MFW-ChainFlow Assistant.

#   MFW-ChainFlow Assistant is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published
#   by the Free Software Foundation, either version 3 of the License,
#   or (at your option) any later version.

#   MFW-ChainFlow Assistant is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License
#   along with MFW-ChainFlow Assistant. If not, see <https://www.gnu.org/licenses/>.

#   Contact: err.overflow@gmail.com
#   Copyright (C) 2024-2025  MFW-ChainFlow Assistant. All rights reserved.

"""
MFW-ChainFlow Assistant
MFW-ChainFlow Assistant 更新单元
作者:overflow65537
"""

from PySide6.QtCore import QThread, SignalInstance, QObject, Signal
from enum import Enum
from datetime import datetime
from time import perf_counter
import requests
from requests import Response, HTTPError
import jsonc
import os
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Literal, Optional, TYPE_CHECKING, cast
from urllib.parse import unquote, urlparse

import platform

if TYPE_CHECKING:
    from app.core.core import ServiceCoordinator
    from app.common.config import QConfig

from app.utils.logger import logger
from app.common.config import cfg, Config
from app.utils.crypto import crypto_manager
from app.common.signal_bus import signalBus
from app.core.core import ServiceCoordinator


# region 更新
class BaseUpdate(QThread):
    service_coordinator: Optional[ServiceCoordinator]
    stop_flag = False
    channel_map = {0: "stable", 1: "beta", 2: "alpha"}

    def get_proxy_data(self) -> dict | None:
        proxy_value = cfg.get(cfg.http_proxy)
        scheme = {0: "http", 1: "socks5"}.get(cfg.get(cfg.proxy))
        if not proxy_value or not scheme:
            return None
        proxies = {key: f"{scheme}://{proxy_value}" for key in ("http", "https")}
        logger.debug("使用代理配置: %s", proxies)
        return proxies

    def download_file(
        self, url, file_path, progress_signal: SignalInstance, use_proxies
    ) -> tuple[Path | None, str | None]:
        logger.info("  [下载] 开始下载文件...")
        logger.debug("  [下载] URL: %s", url[:100] if url else "N/A")
        logger.debug("  [下载] 保存路径: %s", file_path)

        need_clear_update = False
        error_message: str | None = None
        response = None
        final_path: Path | None = None
        if use_proxies:
            proxies = self.get_proxy_data()
            logger.debug("  [下载] 使用代理: %s", "是" if proxies else "否")
        else:
            proxies = None

        if os.path.exists("NO_SSL"):
            verify = False
            logger.debug("  [下载] 检测到NO_SSL文件，跳过SSL验证")
        else:
            verify = True

        def _derive_filename(resp: Response) -> str:
            return "update.zip"

        def _resolve_target_location(base_path: Path, filename: str) -> Path:
            is_dir = (base_path.exists() and base_path.is_dir()) or str(
                base_path
            ).endswith(os.sep)
            if is_dir:
                return base_path / filename
            return base_path

        try:
            logger.debug("  [下载] 发起请求...")
            response = requests.get(
                url, stream=True, verify=verify, timeout=10, proxies=proxies
            )
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            logger.info(
                "  [下载] 文件大小: %s 字节", total_size if total_size else "未知"
            )

            downloaded_size = 0
            last_log_percent = 0
            # 进度信号节流，减少 UI 重绘频率
            last_emit_time = perf_counter()
            last_emit_percent = 0
            if not final_path:
                filename = _derive_filename(response)
                target_path = Path(file_path)
                final_path = _resolve_target_location(target_path, filename)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            with open(final_path, "wb") as file:
                for data in response.iter_content(chunk_size=4096):
                    if self.stop_flag:
                        logger.warning("  [下载] 收到停止信号，中断下载")
                        response.close()
                        error_message = self.tr("User cancelled")
                        if final_path and final_path.exists():
                            need_clear_update = True
                        break

                    downloaded_size += len(data)
                    file.write(data)
                    # 仅在百分比变化或间隔足够时才发射进度信号，避免过于频繁的 UI 更新
                    now = perf_counter()
                    percent = (
                        int(downloaded_size * 100 / total_size) if total_size > 0 else 0
                    )
                    if (
                        total_size == 0
                        or percent > last_emit_percent
                        or now - last_emit_time >= 0.25
                    ):
                        progress_signal.emit(downloaded_size, total_size)
                        last_emit_time = now
                        last_emit_percent = percent

                    # 每 10% 记录一次日志
                    if total_size > 0:
                        percent = int(downloaded_size * 100 / total_size)
                        if percent >= last_log_percent + 10:
                            logger.debug("  [下载] 进度: %d%%", percent)
                            last_log_percent = percent

            if not need_clear_update and not self.stop_flag:
                logger.info("  [下载] 下载完成，共 %d 字节", downloaded_size)
                return final_path, None
            if not error_message and self.stop_flag:
                error_message = self.tr("User cancelled")
            if not error_message and need_clear_update:
                error_message = self.tr("Download interrupted")
            if final_path and final_path.exists():
                final_path.unlink()
            return None, error_message
        except Exception as e:
            logger.exception(f"下载文件时出错{url} -> {file_path}\n{e}")
            error_message = f"{type(e).__name__}: {e}"
            if final_path and final_path.exists():
                final_path.unlink()
            return None, error_message
        finally:
            if response:
                response.close()

    def extract_archive(
        self, archive_path, extract_to, flatten_assets=False
    ) -> Path | None:
        target_path = Path(archive_path)
        normalized_name = target_path.name.lower()

        if normalized_name.endswith(".tar.gz") or normalized_name.endswith(".tgz"):
            archive_type = "tar"
        elif normalized_name.endswith(".zip"):
            archive_type = "zip"
        else:
            logger.warning(
                "未知压缩格式: %s，默认按照 zip 处理",
                target_path.name,
            )
            archive_type = "zip"

        return self._perform_archive_extraction(
            target_path, extract_to, flatten_assets, archive_type
        )

    def extract_zip(self, zip_file_path, extract_to, flatten_assets=False):
        return self.extract_archive(zip_file_path, extract_to, flatten_assets)

    def _perform_archive_extraction(
        self,
        archive_path: Path,
        extract_to: Path | str,
        flatten_assets: bool,
        archive_type: Literal["zip", "tar"],
    ) -> Path | None:
        extract_to_path = Path(extract_to)
        extract_to_path.mkdir(parents=True, exist_ok=True)

        def _normalize_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(part for part in parts if part and part != ".")

        interface_names = {"interface.json", "interface.jsonc"}
        final_root: Path | None = None

        try:
            if archive_type == "zip":
                with zipfile.ZipFile(
                    archive_path, "r", metadata_encoding="utf-8"
                ) as archive:
                    members = archive.namelist()
                    interface_dir_parts = self._determine_interface_dir(
                        members, _normalize_parts, interface_names
                    )
                    self._extract_members_filtered(
                        archive,
                        members,
                        interface_dir_parts,
                        extract_to_path,
                        _normalize_parts,
                    )
                    final_root = self._resolve_final_root(
                        extract_to_path, interface_dir_parts
                    )
            else:
                with tarfile.open(archive_path, "r:*") as archive:
                    members = archive.getmembers()
                    member_names = [member.name for member in members]
                    interface_dir_parts = self._determine_interface_dir(
                        member_names, _normalize_parts, interface_names
                    )
                    self._extract_members_filtered(
                        archive,
                        members,
                        interface_dir_parts,
                        extract_to_path,
                        _normalize_parts,
                    )
                    final_root = self._resolve_final_root(
                        extract_to_path, interface_dir_parts
                    )

            if flatten_assets and final_root:
                self._normalize_assets_package(final_root)
            return final_root or extract_to_path
        except Exception as e:
            logger.exception("解压文件时出错 %s", e)
            return None

    def _determine_interface_dir(
        self,
        members: list[str],
        normalize: Callable[[tuple[str, ...]], tuple[str, ...]],
        interface_names: set[str],
    ) -> tuple[str, ...] | None:
        for member in members:
            member_path = PurePosixPath(member)
            if member_path.name.lower() in interface_names:
                return normalize(member_path.parent.parts)
        return None

    def _extract_members_filtered(
        self,
        archive: Any,
        members: list[Any],
        interface_dir_parts: tuple[str, ...] | None,
        extract_to_path: Path,
        normalize: Callable[[tuple[str, ...]], tuple[str, ...]],
    ) -> None:
        for member in members:
            member_name = member if isinstance(member, str) else member.name
            member_path = PurePosixPath(member_name)
            member_parts = normalize(member_path.parts)
            if (
                interface_dir_parts
                and member_parts[: len(interface_dir_parts)] != interface_dir_parts
            ):
                continue
            archive.extract(member, extract_to_path)

    def _resolve_final_root(
        self, extract_to_path: Path, interface_dir_parts: tuple[str, ...] | None
    ) -> Path:
        if interface_dir_parts:
            return extract_to_path.joinpath(*interface_dir_parts)
        return extract_to_path

    def _normalize_assets_package(self, extract_to):
        """
        针对只包含一个文件夹的资源包，将 assets 和 interface.json 平移到目标目录。
        检测到 change[s]?.json 后会直接返回，因为这类包已经在目标目录中。
        """
        target_path = Path(extract_to)
        if not target_path.exists():
            return

        change_markers = [
            target_path / name for name in ("change.json", "changes.json")
        ]
        if any(marker.exists() for marker in change_markers):
            return

        candidates = [
            entry
            for entry in target_path.iterdir()
            if entry.is_dir() and entry.name not in {"__MACOSX", ".DS_Store"}
        ]
        if len(candidates) != 1:
            return

        candidate_dir = candidates[0]
        interface_file = candidate_dir / "interface.json"
        if not interface_file.exists():
            return

        assets_src = candidate_dir / "assets"
        assets_dest = target_path / "assets"
        if assets_src.exists():
            if assets_dest.exists():
                shutil.rmtree(assets_dest)
            shutil.move(str(assets_src), str(assets_dest))

        interface_dest = target_path / "interface.json"
        if interface_dest.exists():
            interface_dest.unlink()
        shutil.move(str(interface_file), str(interface_dest))

        try:
            remaining = list(candidate_dir.iterdir())
            if not remaining:
                candidate_dir.rmdir()
        except Exception as cleanup_error:
            logger.debug(f"清理临时目录失败 {candidate_dir}: {cleanup_error}")

    def move_files(self, src, dst):
        """
        移动文件或文件夹。
        移动 src 到 dst。

        """
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            return True
        except Exception as e:
            logger.exception(f"移动文件时出错{src} -> {dst}")
            return False

    def _backup_file(self, target: Path, backup_target: Path) -> None:
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        if backup_target.exists():
            if backup_target.is_dir():
                shutil.rmtree(backup_target)
            else:
                backup_target.unlink()
        shutil.copy2(target, backup_target)
        target.unlink()

    def _backup_directory(self, target: Path, backup_target: Path) -> None:
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        if backup_target.exists():
            shutil.rmtree(backup_target)
        shutil.copytree(target, backup_target)
        shutil.rmtree(target)

    def _cleanup_targets(self, project_path: Path, relatives: list[Path]) -> None:
        for relative in sorted(relatives, key=lambda rel: len(rel.parts), reverse=True):
            target = project_path / relative
            if target.exists():
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except Exception as cleanup_err:
                    logger.warning(f"恢复进程时清理 {target} 失败: {cleanup_err}")

    def _restore_from_backup(self, project_path: Path, backup_root: Path) -> None:
        if not backup_root.exists():
            return
        project_path.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(backup_root, project_path, dirs_exist_ok=True)
        except Exception as restore_err:
            logger.exception(f"备份恢复失败: {restore_err}")

    def _backup_model_dir(self, project_path: Path) -> Path | None:
        """
        备份项目内的 model 目录到临时位置，用于全量覆盖前的保留。
        """
        model_dir = project_path / "model"
        if not model_dir.exists() or not model_dir.is_dir():
            return None
        try:
            backup_root = Path.cwd() / "update"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_dir = backup_root / ".model_backup"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(model_dir, backup_dir, dirs_exist_ok=True)
            logger.info("已备份 model 目录到临时位置: %s", backup_dir)
            return backup_dir
        except Exception as backup_err:
            logger.warning("备份 model 目录失败: %s", backup_err)
            return None

    def _restore_model_dir(self, project_path: Path, backup_dir: Path | None) -> None:
        """
        将备份的 model 目录还原到项目路径，若备份不存在则忽略。
        """
        if not backup_dir or not backup_dir.exists():
            return
        target = project_path / "model"
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(backup_dir, target, dirs_exist_ok=True)
            logger.info("已将 model 目录恢复到: %s", target)
        except Exception as restore_err:
            logger.warning("恢复 model 目录失败: %s", restore_err)

    def _cleanup_paths(self, paths: list[Path]) -> None:
        for path in paths:
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except Exception as cleanup_err:
                logger.warning(f"清理 {path} 时失败: {cleanup_err}")

    def _cleanup_update_artifacts(
        self, download_dir: Path, zip_file_path: Path
    ) -> None:
        metadata_path = download_dir / "update_metadata.json"
        for path in (metadata_path, zip_file_path):
            try:
                if path.exists():
                    path.unlink()
                    logger.info("[步骤5] 清理更新数据: %s", path)
            except Exception as err:
                logger.debug("[步骤5] 清理更新数据失败: %s -> %s", path, err)

    def _write_update_metadata(
        self,
        download_dir: Path,
        source: str,
        mode: str,
        version: str | None,
        attempts: int,
        package_name: str,
    ) -> None:
        data = {
            "source": source,
            "mode": mode,
            "version": str(version) if version else "",
            "package_name": package_name,
            "download_time": datetime.utcnow().isoformat() + "Z",
            "attempts": attempts,
        }
        metadata_path = download_dir / "update_metadata.json"
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                jsonc.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("已写入更新元数据: %s", metadata_path)
        except Exception as exc:
            logger.warning("记录更新元数据失败: %s", exc)

    def _is_under_any(self, relative: Path, parents: list[Path]) -> bool:
        for parent in parents:
            if len(parent.parts) > len(relative.parts):
                continue
            if relative.parts[: len(parent.parts)] == parent.parts:
                return True
        return False

    def remove_temp_files(self, *paths):
        for path in paths:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)

    def Mirror_ckd(self) -> str:
        try:
            cdk_encrypted = cfg.get(cfg.Mcdk)
            if not cdk_encrypted:
                return ""
            decrypted = crypto_manager.decrypt_payload(cdk_encrypted)
            return decrypted.decode("utf-8")
        except Exception as e:
            logger.exception("获取ckd失败")
            return ""

    def _ssl_verify(self) -> bool:
        if os.path.exists("NO_SSL"):
            logger.debug("检测到NO_SSL文件，跳过SSL验证")
            return False
        return True

    def _request_with_error_handling(
        self,
        url: str,
        *,
        context_label: str,
        ssl_result: Dict,
        conn_result: Callable[[Exception], Dict],
        expect_status: bool = False,
        http_error_handler: Optional[
            Callable[[requests.exceptions.HTTPError], Dict]
        ] = None,
        proxies: Optional[dict] = None,
        headers: Optional[dict] = None,
    ):
        verify = self._ssl_verify()
        kwargs = {"timeout": 10, "verify": verify}
        if proxies is not None:
            kwargs["proxies"] = proxies
        if headers is not None:
            kwargs["headers"] = headers

        try:
            response = requests.get(url, **kwargs)
            if expect_status:
                response.raise_for_status()
        except requests.exceptions.SSLError as e:
            logger.error(f"{context_label}更新检查失败（SSL错误）: {e}")
            return ssl_result
        except requests.exceptions.HTTPError as e:
            if http_error_handler:
                return http_error_handler(e)
            logger.error(f"{context_label}更新检查失败（HTTP错误）: {e}")
            return {
                "status": "failed",
                "msg": self.tr("Update check failed HTTP error,code: ") + str(e),
            }
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.RequestException,
        ) as e:
            logger.error(f"{context_label}更新检查失败（连接错误）: {e}")
            return conn_result(e)

        return response

    def _github_http_error_handler(self, error: requests.exceptions.HTTPError) -> Dict:
        if error.response and error.response.status_code == 403:
            logger.warning("GitHub API请求被限制")
            return {
                "status": "failed",
                "msg": self.tr(
                    "GitHub API request limit exceeded,please try again later"
                ),
            }
        logger.error(f"GitHub更新检查失败（HTTP错误）: {error}")
        return {
            "status": "failed",
            "msg": self.tr("Github Update check failed HTTP error,code: ") + str(error),
        }

    def _github_request_headers(self) -> dict[str, str] | None:
        """根据配置构建 GitHub API 请求头以使用授权令牌。"""
        token = cfg.get(cfg.github_api_key)
        if not token:
            return None
        token_str = str(token).strip()
        if not token_str:
            return None
        return {
            "Authorization": f"token {token_str}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _mirror_response(self, url):
        """
        处理镜像源（MirrorChyan）的GET请求并返回响应。
        """
        return self._request_with_error_handling(
            url,
            context_label="镜像源",
            ssl_result={
                "status": "failed_info",
                "msg": self.tr("MirrorChyan Update check failed SSL error"),
            },
            conn_result=lambda e: {
                "status": "failed_info",
                "msg": "Mirror ERROR"
                + "\n"
                + str(e)
                + "\n"
                + self.tr("switching to Github download"),
            },
        )

    def _github_response(self, url):
        """
        处理GitHub的GET请求并返回响应。
        """
        return self._request_with_error_handling(
            url,
            context_label="GitHub",
            expect_status=True,
            proxies=self.get_proxy_data(),
            ssl_result={
                "status": "failed",
                "msg": self.tr("Github Update check failed SSL error"),
            },
            http_error_handler=self._github_http_error_handler,
            headers=self._github_request_headers(),
            conn_result=lambda e: {
                "status": "failed",
                "msg": "Github ERROR" + "\n" + str(e),
            },
        )

    def mirror_check(
        self,
        res_id: str,
        cdk: str,
        version: str,
        os_type: Optional[str] = None,
        arch: Optional[str] = None,
        channel: Optional[str] = "stable",
    ) -> Dict:
        """if multiplatform is True:
            logger.debug("检查到agent字段,使用多平台更新")
            url = f"https://mirrorchyan.com/api/resources/{res_id}/latest?current_version={version}&cdk={cdk}&os={os_type}&arch={arch}&channel={channel}&user_agent=MFW_PyQt6"
        else:
            url = f"https://mirrorchyan.com/api/resources/{res_id}/latest?current_version={version}&cdk={cdk}&channel={channel}&user_agent=MFW_PyQt6
        """
        url = f"https://mirrorchyan.com/api/resources/{res_id}/latest?current_version={version}&cdk={cdk}&os={os_type}&arch={arch}&channel={channel}&user_agent=MFW_PyQt6"

        response = self._mirror_response(url)
        if isinstance(response, dict):
            return response  # 返回错误信息给run方法
        mirror_data: Dict[str, Any] = response.json()
        code = mirror_data.get("code")
        mirror_msg = str(mirror_data.get("msg", ""))
        switch_msg = self.tr("switching to Github download")
        error_translations = {
            1001: self.tr("INVALID_PARAMS"),
            7001: self.tr("KEY_EXPIRED"),
            7002: self.tr("KEY_INVALID"),
            7003: self.tr("RESOURCE_QUOTA_EXHAUSTED"),
            7004: self.tr("KEY_MISMATCHED"),
            8001: self.tr("RESOURCE_NOT_FOUND"),
            8002: self.tr("INVALID_OS"),
            8003: self.tr("INVALID_ARCH"),
            8004: self.tr("INVALID_CHANNEL"),
        }
        if isinstance(code, int) and code not in [None, 0]:
            msg_value = error_translations.get(code, self.tr("Unknown error"))
            logger.warning(f"更新检查失败: {mirror_msg}")
            if code in [7001, 7002, 7003, 7004]:  # 这些错误不会影响更新检查
                mirror_data.update(
                    {
                        "status": "failed_info",
                        "msg": error_translations[code] + "\n" + switch_msg,
                    }
                )
            else:
                return {
                    "status": "failed_info",
                    "msg": msg_value + "\n" + switch_msg,
                }

        data: dict[str, Any] = mirror_data.get("data", {})
        cdk_expired_time = data.get("cdk_expired_time")
        if isinstance(cdk_expired_time, int) and cdk_expired_time > 0:
            cfg.set(cfg.cdk_expired_time, cdk_expired_time)
        if data is not None and data.get("version_name") == version:
            return {"status": "no_need", "msg": self.tr("current version is latest")}
        return mirror_data

    def github_check(self, project_url: str, version: str):
        """
        检查 GitHub 上的更新。
        """
        logger.info(f"开始GitHub更新检查: {project_url}")
        response = None
        try:
            response = self._github_response(project_url)
            if isinstance(response, dict):
                logger.warning(f"GitHub请求失败: {response.get('msg')}")
                return response

            update_dict: dict[str, dict] | dict[str, str] = response.json()
            logger.debug(f"GitHub响应数据: {jsonc.dumps(update_dict, indent=2)}")

            if "message" in update_dict and isinstance(update_dict["message"], str):
                error_msg = self.tr("GitHub API ERROR: ") + update_dict["message"]
                logger.error(error_msg)
                return {"status": "failed", "msg": error_msg}

            if update_dict.get("tag_name", None) == version:
                logger.info("当前已是最新版本")
                return {
                    "status": "no_need",
                    "msg": self.tr("current version is latest"),
                }

            return update_dict
        except jsonc.JSONDecodeError as e:
            if isinstance(response, Response):
                logger.exception(f"GitHub响应解析失败: {response.text[:200]}\n{e}")
            else:
                logger.exception(f"GitHub响应解析失败: 未收到响应\n{e}")
            return {"status": "failed", "msg": "Invalid GitHub response"}
        except Exception as e:
            logger.exception(f"GitHub检查过程中发生未预期错误{e}")
            return {"status": "failed", "msg": str(e)}

    def clear_change(self, target_path):
        # 清理旧文件
        bundle_path = self._get_bundle_path()
        if not bundle_path:
            logger.error("无法获取 bundle 路径，无法执行清理操作")
            return {
                "status": "failed_info",
                "msg": "Bundle path not found",
            }

        change_data_path = os.path.join(target_path, "changes.json")
        try:
            if os.path.exists(change_data_path):

                change_data = self._read_config(change_data_path).get("deleted", [])
                logger.info(f"需要清理 {len(change_data)} 个文件")

                for file in change_data:
                    if "install" in file[:10]:
                        file_path = file.replace("install", bundle_path, 1)
                    elif "resource" in file[:10]:
                        file_path = file.replace(
                            "resource", f"{bundle_path}/resource", 1
                        )
                    else:
                        logger.error(f"未知文件格式: {file}")
                        continue

                    logger.debug(f"尝试删除: {file_path}")
                    if os.path.exists(file_path):
                        try:
                            if os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                            else:
                                os.remove(file_path)
                        except Exception as e:
                            logger.error(f"删除失败 [{file_path}]: {str(e)}")
            else:
                logger.warning("未找到 changes.json 文件,执行全部清理")
                shutil.rmtree(bundle_path)

        except Exception as e:
            logger.exception("清理旧文件时发生错误")
            return {
                "status": "failed_info",
                "msg": self.tr("Failed to clean up temporary files"),
            }

    def _get_bundle_path(self) -> str | None:
        """安全获取当前 bundle 路径"""
        if not self.service_coordinator:
            logger.error("service_coordinator 未初始化")
            return None
        try:
            config = self.service_coordinator.config_service.get_current_config()
            bundle_path = (
                self.service_coordinator.config_service.get_bundle_path_for_config(
                    config
                )
                or ""
            )
            if not bundle_path:
                logger.error("未能获取当前 bundle 路径")
                return None
            return bundle_path
        except FileNotFoundError as e:
            logger.warning(f"当前 bundle 配置不存在: {e}")
            return None
        except Exception as e:
            logger.error(f"获取 bundle 路径失败: {e}")
            return None

    def _get_local_update_flag_path(self) -> str | None:
        bundle_path = self._get_bundle_path()
        if not bundle_path:
            return None
        return os.path.join(bundle_path, "update_flag.txt")

    def _read_local_update_flag(self) -> str | None:
        flag_path = self._get_local_update_flag_path()
        if not flag_path:
            return None
        try:
            with open(flag_path, "r", encoding="utf-8") as file:
                return file.read().strip()
        except FileNotFoundError:
            logger.warning(f"本地 update_flag.txt 不存在: {flag_path}")
        except Exception as exc:
            logger.error(f"读取本地 update_flag 失败: {exc}")
        return None

    def _fetch_remote_update_flag(self, url: str) -> str:
        proxies = self.get_proxy_data()
        response = None
        try:
            response = requests.get(
                url, timeout=10, verify=self._ssl_verify(), proxies=proxies
            )
            response.raise_for_status()
            return response.text.strip()
        except HTTPError as exc:
            status = exc.response.status_code if exc.response else None
            if status == 404:
                logger.warning("远端 update_flag 不存在 (%s)", url)
                return "1"
            logger.error(f"远端 update_flag 获取失败 ({url}): {exc}")
            return "1"
        except requests.RequestException as exc:
            logger.error(f"远端 update_flag 获取失败 ({url}): {exc}")
            return "1"
        finally:
            if response:
                response.close()

    def check_for_hotfix(self, url: str) -> bool:
        local_flag = self._read_local_update_flag()
        if local_flag is None:
            return False

        remote_flag = self._fetch_remote_update_flag(url)
        if remote_flag is None:
            return False

        return local_flag == remote_flag

    def _read_config(self, paths: str) -> Dict:
        """读取指定路径的JSON配置文件。

        Args:
            paths (str): 配置文件的路径。

        Returns:
            dict: 如果文件存在，返回解析后的字典

        """

        if isinstance(paths, str) and os.path.exists(paths):
            with open(paths, "r", encoding="utf-8") as MAA_Config:
                import jsonc

                MAA_data = jsonc.load(MAA_Config)
                return MAA_data
        else:
            return {}


# endregion


class Update(BaseUpdate):

    # 当以“仅检查更新”模式运行时，用于在检查完成后返回结果
    check_result_ready = Signal(dict)

    def __init__(
        self,
        service_coordinator: Optional[ServiceCoordinator],
        stop_signal: SignalInstance,
        progress_signal: SignalInstance,
        info_bar_signal: SignalInstance,
        interface: dict[str, Any],
        force_full_download: bool = False,
        *,
        check_only: bool = False,
    ):
        """
        更新器核心对象。

        - 正常模式（check_only=False）: 调用 start()/run() 会执行完整更新流程。
        - 仅检查模式（check_only=True）: 调用 start()/run() 只会检查是否有更新，
          不会下载或应用更新，结果通过 check_result_ready 信号返回。

        Args:
            service_coordinator: 服务协调器（可选，用于获取 bundle 路径等）
            stop_signal: 停止信号
            progress_signal: 进度信号
            info_bar_signal: 信息栏信号
            interface: 接口配置字典，包含项目信息（name, version, github/url, mirrorchyan_rid 等）
            force_full_download: 是否强制完整下载
            check_only: 是否仅检查更新
        """
        super().__init__()
        self.service_coordinator = service_coordinator
        self.stop_signal = stop_signal
        self.progress_signal = progress_signal
        self.info_bar_signal = info_bar_signal
        self.force_full_download = force_full_download
        self.check_only = check_only

        # 从 interface 中获取参数
        self.interface = interface or {}
        self.project_name = self.interface.get("name", "")
        self.current_version = self.interface.get("version", "v1.0.0")
        if not self.current_version.startswith("v"):
            self.current_version = "v" + self.current_version
        self.url = self.interface.get("github", self.interface.get("url", ""))
        self.current_res_id = self.interface.get("mirrorchyan_rid", "")

        # 从配置中获取 mirror_cdk
        self.mirror_cdk = self.Mirror_ckd()

        # 从配置中获取更新通道
        channel_value = cfg.get(cfg.resource_update_channel)
        self.current_channel_enum = self._normalize_channel(channel_value)
        self.current_channel = self.current_channel_enum.name.lower()

        # 从系统自动检测操作系统和架构
        self.current_os_type = self._normalize_os_type(sys.platform)
        self.current_arch = self._normalize_arch(platform.machine())

        # 其他运行时参数
        self.latest_update_version = self.current_version
        self.download_url: str | None = None
        self.release_note: str = ""
        self.download_attempts: int = 0
        self.version_name: str | None = None
        # 防止重复运行的标记
        self._is_running: bool = False

    def _normalize_channel(self, value) -> Config.UpdateChannel:
        """Convert stored channel value into a valid UpdateChannel enum."""
        try:
            return cfg.UpdateChannel(int(value))
        except (ValueError, TypeError):
            logger.warning("配置的更新通道非法，默认降级为 stable。value=%s", value)
            return cfg.UpdateChannel.STABLE

    def _normalize_os_type(self, value: Optional[str]) -> str:
        normalized = (value or "").lower()
        if normalized.startswith("win"):
            return "win"
        if normalized.startswith("linux"):
            return "linux"
        if normalized.startswith("darwin") or normalized.startswith("mac"):
            return "macos"
        logger.warning(
            "检测到未知操作系统标识 %s，默认归类为 linux",
            value,
        )
        return "linux"

    def _normalize_arch(self, value: Optional[str]) -> str:
        normalized = (value or "").lower()
        if normalized in {"x86_64", "amd64"}:
            return "x86_64"
        if normalized in {"aarch64", "arm64"}:
            return "aarch64"
        logger.warning(
            "检测到未知架构标识 %s，默认归类为 x86_64",
            value,
        )
        return "x86_64"

    def _init_run_context(self, ui_mode: bool = False) -> None:
        """
        初始化一次本次 run 所需的运行时上下文参数。

        注意：主要参数已在 __init__ 中初始化，此方法仅重置运行时状态。

        ui_mode=True 时，用于 UI 自更新（版本/资源ID/GitHub 地址与 bundle 资源不同）。
        """
        # 重置运行时状态
        self.latest_update_version = (
            cfg.get(cfg.latest_update_version) or self.current_version
        )
        self.download_url = None
        self.release_note = ""
        self.download_attempts = 0
        self.version_name = None

        # 打印本次更新/检查使用的关键上下文信息
        logger.info("=" * 50)
        logger.info("开始更新流程")

        if ui_mode:
            # UI 自更新：使用程序自身的信息
            from app.common.__version__ import __version__

            logger.info("当前版本(UI): %s", __version__)
            logger.info("资源ID(UI): %s", "MFW-PyQt6")
            logger.info(
                "GitHub URL(UI): %s", "https://github.com/overflow65537/MFW-PyQt6"
            )
        else:
            # 资源/bundle 更新：使用 interface 中的配置
            logger.info("当前版本: %s", self.current_version)
            logger.info("资源ID: %s", self.current_res_id)
            logger.info("GitHub URL: %s", self.url)

        logger.info("=" * 50)

    def run(self):
        """
        线程入口。

        - 正常模式: 执行完整更新流程。
        - 仅检查模式: 只检查是否有更新，并通过 check_result_ready 返回结果。
        """
        # 防止多次重复运行（包括误调用 run 或多次 start）
        if self._is_running:
            logger.warning("检测到更新线程重复运行请求，本次调用将被忽略")
            return

        self._is_running = True
        # 从配置中获取 mirror_cdk
        self.mirror_cdk = self.Mirror_ckd()
        try:
            # 每次运行前按当前配置初始化上下文（包括 interface / 频道 / 版本 等）
            self._init_run_context()
            if self.check_only and not cfg.get(cfg.multi_resource_adaptation):
                logger.info("以仅检查模式运行更新器（check_only=True）")
                self._run_check_only()
            elif self.check_only:
                logger.info("以仅检查模式运行更新器（check_only=True）")
                self._run_check_only()
            elif cfg.get(cfg.multi_resource_adaptation):
                logger.info("运行UI更新")
                self._run_ui_update()
            else:
                logger.info("以正常模式运行更新器")
                self._run_normal()
        finally:
            self._is_running = False

    def _run_check_only_ui(self) -> None:
        """
        仅检查模式UI更新。
        """
        logger.info("以仅检查模式运行更新器（check_only=True）")
        if not self.service_coordinator:
            logger.warning("service_coordinator 未初始化，无法执行更新检查")
            self.check_result_ready.emit(
                {
                    "enable": False,
                    "source": "",
                    "download_url": "",
                    "release_note": "",
                    "latest_update_version": "",
                }
            )
            return
        # fetch_download_url=False 只检查版本/元数据，不解析完整下载资源
        result = self.check_ui_update(fetch_download_url=False)
        result_info = result if isinstance(result, dict) else {}
        result_data: dict = {
            "enable": bool(result),
            "source": result_info.get("source", ""),
            "download_url": result_info.get("url", ""),
            "release_note": self.release_note or "",
            "latest_update_version": self.latest_update_version or "",
        }
        self.check_result_ready.emit(result_data)

    def _run_check_only(self) -> None:
        """
        检查结果通过 `check_result_ready` 信号返回。
        """
        logger.info("以仅检查模式运行更新器（check_only=True）")
        if not self.service_coordinator:
            logger.warning("service_coordinator 未初始化，无法执行更新检查")
            self.check_result_ready.emit(
                {
                    "enable": False,
                    "source": "",
                    "download_url": "",
                    "release_note": "",
                    "latest_update_version": "",
                }
            )
            return

        # fetch_download_url=False 只检查版本/元数据，不解析完整下载资源
        result = self.check_update(fetch_download_url=False)
        result_info = result if isinstance(result, dict) else {}
        result_data: dict = {
            "enable": bool(result),
            "source": result_info.get("source", ""),
            "download_url": result_info.get("url", ""),
            "release_note": self.release_note or "",
            "latest_update_version": self.latest_update_version or "",
        }
        self.check_result_ready.emit(result_data)

    def _run_ui_update(self) -> None:
        """
        多资源适配模式 UI 更新。
        """
        self.stop_flag = False
        if not self.service_coordinator:
            logger.error("service_coordinator 未初始化，无法执行更新")
            return self._stop_with_notice(0)

        # 步骤1: 检查更新
        logger.info("[步骤1] 开始检查更新...")
        self._emit_info_bar("info", self.tr("Checking for updates..."))
        update_info = self.check_ui_update()

        download_url = update_info.get("url") if isinstance(update_info, dict) else None
        download_source = (
            update_info.get("source") if isinstance(update_info, dict) else "unknown"
        )

        hotfix = (
            update_info.get("update_type") == "incremental"
            if isinstance(update_info, dict)
            else None
        )

        if not download_url:
            if update_info is False:
                logger.info("[步骤1] 当前已是最新版本，无需下载")
                return self._stop_with_notice(0, "info", self.tr("Already up to date"))
            logger.error("[步骤1] 检查完成但未获取到下载地址")
            return self._stop_with_notice(0, "error", self.tr("Download failed"))

        logger.info("[步骤1] 检查完成: 发现新版本 %s", self.latest_update_version)
        logger.info("[步骤1] 下载来源: %s", download_source)
        logger.info("[步骤1] 下载地址: %s", str(download_url)[:100])

        self._emit_info_bar("info", self.tr("Preparing to download update..."))

        download_dir = Path.cwd() / "update" / "new_version"
        download_dir.mkdir(parents=True, exist_ok=True)
        if not download_url:
            logger.error("[步骤2] 未设置下载地址，无法执行下载")
            return self._stop_with_notice(0, "error", self.tr("Download failed"))
        logger.debug("[步骤2] 保存路径: %s", download_dir)

        logger.info("[步骤3] 开始下载更新包...")
        logger.debug("[步骤3] 下载地址: %s", download_url)
        self.download_attempts += 1
        downloaded_zip_path, download_error = self.download_file(
            download_url,
            download_dir,
            self.progress_signal,
            use_proxies=self.get_proxy_data(),
        )
        if not downloaded_zip_path:
            if download_error:
                logger.error("[步骤3] 下载失败: %s", download_error)
                return self._stop_with_notice(
                    0,
                    "error",
                    f"{self.tr('Download failed')}: {download_error}",
                )
            logger.error("[步骤3] 下载失败")
            return self._stop_with_notice(0, "error", self.tr("Download failed"))
        zip_file_path = downloaded_zip_path
        logger.debug("[步骤3] 下载文件: %s", zip_file_path)

        logger.info(
            "[步骤3] 下载完成，大小: %.2f MB",
            zip_file_path.stat().st_size / (1024 * 1024),
        )
        self._emit_info_bar("success", self.tr("Download complete"))

        mode_label = "hotfix" if hotfix else "full"

        self._write_update_metadata(
            download_dir,
            str(download_source),
            mode_label,
            str(self.latest_update_version or ""),
            self.download_attempts,
            zip_file_path.name,
        )

        logger.info("[步骤3] 准备重启以进行更新")
        return self._stop_with_notice(2)

    def _run_normal(self) -> None:
        """
        正常更新流程：检查更新、下载更新包并执行热更新/重启流程。
        """
        self.stop_flag = False
        deleted_backups: list[tuple[Path, Path]] = []
        # 资源目录热更新时使用的备份信息，用于异常时整体回滚
        resource_backup_dir: Path | None = None
        resource_backups: list[tuple[Path, Path]] = []

        try:
            if not self.service_coordinator:
                logger.error("service_coordinator 未初始化，无法执行更新")
                return self._stop_with_notice(0)

            # 步骤1: 检查更新
            logger.info("[步骤1] 开始检查更新...")
            self._emit_info_bar("info", self.tr("Checking for updates..."))
            update_info = self.check_update()

            download_url = (
                update_info.get("url") if isinstance(update_info, dict) else None
            )
            download_source = (
                update_info.get("source")
                if isinstance(update_info, dict)
                else "unknown"
            )

            hotfix = (
                update_info.get("update_type") == "incremental"
                if isinstance(update_info, dict)
                else None
            )

            if not download_url:
                if update_info is False:
                    logger.info("[步骤1] 当前已是最新版本，无需下载")
                    return self._stop_with_notice(
                        0, "info", self.tr("Already up to date")
                    )
                logger.error("[步骤1] 检查完成但未获取到下载地址")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))

            logger.info("[步骤1] 检查完成: 发现新版本 %s", self.latest_update_version)
            logger.info("[步骤1] 下载来源: %s", download_source)
            logger.info("[步骤1] 下载地址: %s", str(download_url)[:100])

            # 步骤2: 检查是否支持热更新
            if self.force_full_download:
                logger.info("[步骤2] 强制下载模式，跳过 update_flag/hotfix 检查")
            elif download_source == "github":
                logger.info("[步骤2] 开始判断Github热更新支持...")
                update_flag_url = self._form_github_url(
                    self.url, "update_flag", str(self.latest_update_version)
                )
                if not update_flag_url:
                    logger.info("[步骤2] 无法获取 update_flag URL，跳过热更新")
                    return self._stop_with_notice(2)

                logger.debug("[步骤2] update_flag URL: %s", update_flag_url)

                # 获取更新标志位判断是否可以热更新
                hotfix = self.check_for_hotfix(update_flag_url)
                logger.info("[步骤2]热更新支持: %s", hotfix)
                if hotfix and download_source == "github":
                    download_url = self._form_github_url(
                        self.url, "hotfix", str(self.latest_update_version)
                    )
                    logger.info("[步骤2] 热更新支持，更换下载地址: %s", download_url)

            self._emit_info_bar("info", self.tr("Preparing to download update..."))

            download_dir = Path.cwd() / "update" / "new_version"
            download_dir.mkdir(parents=True, exist_ok=True)
            if not download_url:
                logger.error("[步骤2] 未设置下载地址，无法执行下载")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))
            logger.debug("[步骤2] 保存路径: %s", download_dir)

            logger.info("[步骤3] 开始下载更新包...")
            logger.debug("[步骤3] 下载地址: %s", download_url)
            self.download_attempts += 1
            downloaded_zip_path, download_error = self.download_file(
                download_url,
                download_dir,
                self.progress_signal,
                use_proxies=self.get_proxy_data(),
            )
            if not downloaded_zip_path:
                if download_error:
                    logger.error("[步骤3] 下载失败: %s", download_error)
                    return self._stop_with_notice(
                        0,
                        "error",
                        f"{self.tr('Download failed')}: {download_error}",
                    )
                logger.error("[步骤3] 下载失败")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))
            zip_file_path = downloaded_zip_path
            logger.debug("[步骤3] 下载文件: %s", zip_file_path)

            logger.info(
                "[步骤3] 下载完成，大小: %.2f MB",
                zip_file_path.stat().st_size / (1024 * 1024),
            )
            self._emit_info_bar("success", self.tr("Download complete"))

            mode_label = "hotfix" if hotfix else "full"

            self._write_update_metadata(
                download_dir,
                str(download_source),
                mode_label,
                str(self.latest_update_version or ""),
                self.download_attempts,
                zip_file_path.name,
            )

            if download_source == "mirror":
                logger.info("[步骤3] 准备重启以进行镜像更新")
                return self._stop_with_notice(2)

            # 步骤3: 判断是否可以热更新
            if not hotfix:
                logger.info("[步骤3] 热更新标志位仍不匹配，转向补丁准备流程")
                return self._stop_with_notice(2)
            logger.info("[步骤4] 开始执行热更新，准备解压更新包...")
            self._emit_info_bar("info", self.tr("Applying hotfix..."))

            logger.debug("[步骤4] 解压更新包到 hotfix 目录")
            hotfix_dir = Path.cwd() / "hotfix"
            hotfix_root = self.extract_zip(zip_file_path, hotfix_dir)
            if not hotfix_root:
                logger.error("[步骤4] 解压更新包失败")
                return self._stop_with_notice(2)
            logger.info("[步骤4] 更新包解压完成: %s", hotfix_root)

            # 获取 bundle 路径
            bundle_path = self._get_bundle_path()
            if not bundle_path:
                logger.warning("[步骤4] Bundle 配置不存在，跳过热更新")
                return self._stop_with_notice(2)
            bundle_path_obj = Path(bundle_path)
            logger.debug("[步骤4] Bundle 路径: %s", bundle_path_obj)

            logger.info("[步骤5] 使用安全覆盖模式进行热更新")
            project_path = bundle_path_obj
            if not hotfix_root or not hotfix_root.exists():
                logger.error("[步骤5] hotfix 目录不存在，无法覆盖")
                return self._stop_with_notice(2)

            # 备份并删除资源文件中的 pipeline 目录，以供后续无损覆盖
            resource_backup_dir = Path.cwd() / "backup" / "resource"
            resource_backup_dir.mkdir(parents=True, exist_ok=True)
            resource_list = self.interface.get("resource", [])
            known_resources: list[str] = []
            resource_backups.clear()
            for resource in resource_list:
                for resource_path_str in resource.get("path", []):
                    resource_path = Path(
                        resource_path_str.replace("{PROJECT_DIR}", ".")
                    )
                    if resource_path.is_dir() and (
                        resource_path_str not in known_resources
                    ):
                        backup_target = resource_backup_dir / resource_path.name
                        try:
                            # 先备份资源
                            if backup_target.is_dir():
                                shutil.rmtree(backup_target)
                            shutil.copytree(str(resource_path), str(backup_target))
                            logger.debug("[步骤5] 已备份资源目录: %s", resource_path)

                            resource_backups.append((resource_path, backup_target))
                            known_resources.append(resource_path_str)

                            # 再删除旧的 pipeline 目录，避免影响后续覆盖
                            pipeline_path = resource_path / "pipeline"
                            if pipeline_path.exists():
                                shutil.rmtree(str(pipeline_path))
                                logger.debug(
                                    "[步骤5] 已删除旧 pipeline 目录: %s", pipeline_path
                                )
                        except Exception as backup_err:
                            logger.exception(
                                "[步骤5] 备份或清理资源目录时出错: %s -> %s",
                                resource_path,
                                backup_err,
                            )
                            raise

            logger.info("[步骤5] 开始覆盖项目目录: %s", project_path)
            # 允许目标目录已存在（Python 3.8+ 支持 dirs_exist_ok）
            # 这样在 bundle 目录本身已存在时不会因 WinError 183 直接失败
            shutil.copytree(hotfix_root, project_path, dirs_exist_ok=True)

            interface_path = [
                bundle_path_obj / "interface.jsonc",
                bundle_path_obj / "interface.json",
            ]

            for path in interface_path:
                if path.exists():
                    interface = self._read_config(str(path))
                    if interface:
                        interface["version"] = self.latest_update_version
                        with open(path, "w", encoding="utf-8") as f:
                            jsonc.dump(interface, f, indent=4, ensure_ascii=False)
                        logger.info("[步骤5] 更新 interface.jsonc 成功")
                        break
            logger.info("[步骤5] interface 配置同步完毕")
            # 步骤5: 完成
            logger.info("[步骤5] 热更新成功完成!")
            logger.info("=" * 50)
            self._emit_info_bar("success", self.tr("Update applied successfully"))
            self._cleanup_update_artifacts(download_dir, zip_file_path)
            # 触发服务协调器重新初始化
            signalBus.fs_reinit_requested.emit()
            self.stop_signal.emit(1)

        except Exception as e:
            # 资源目录异常回滚
            if resource_backups:
                logger.warning("[步骤5] 更新失败，正在恢复资源备份目录...")
                for original_path, backup_path in reversed(resource_backups):
                    try:
                        if not backup_path.exists():
                            continue
                        # 清理已被部分覆盖/删除的原目录
                        if original_path.exists():
                            if original_path.is_file():
                                original_path.unlink()
                            else:
                                shutil.rmtree(original_path)
                        # 使用备份进行还原
                        if backup_path.is_dir():
                            shutil.copytree(backup_path, original_path)
                        else:
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_path, original_path)
                        logger.debug("[步骤5] 已恢复资源目录: %s", original_path)
                    except Exception as restore_err:
                        logger.exception(
                            "[步骤5] 恢复资源目录失败: %s -> %s",
                            original_path,
                            restore_err,
                        )
                resource_backups.clear()

            if deleted_backups:
                logger.warning("[步骤5] 更新失败，正在恢复已删除文件...")
                for original_path, backup_path in reversed(deleted_backups):
                    try:
                        if backup_path.exists():
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_path, original_path)
                            logger.debug("[步骤5] 已恢复文件: %s", original_path)
                    except Exception as restore_err:
                        logger.exception(
                            "[步骤5] 恢复文件失败: %s -> %s",
                            original_path,
                            restore_err,
                        )
                deleted_backups.clear()
            logger.exception("更新过程中出现错误: %s", e)
            self._stop_with_notice(0, "error", self.tr("Failed to update"))
        finally:
            # 清理资源备份目录
            if resource_backup_dir and resource_backup_dir.exists():
                try:
                    shutil.rmtree(resource_backup_dir)
                except Exception as cleanup_err:
                    logger.debug("[步骤5] 清理资源备份目录失败: %s", cleanup_err)

    def check_update(self, fetch_download_url: bool = True) -> dict | bool:
        # 防止外部直接调用 check_update 时上下文未初始化
        self._init_run_context()
        logger.info("  [检查更新] 开始检查...")
        logger.debug(
            "  [检查更新] 资源ID: %s, CDK: %s",
            self.current_res_id,
            "***" if self.mirror_cdk else "无",
        )
        # 记录本次检查的关键开关状态，便于排查“为何走 GitHub / Mirror / 强制下载模式”
        try:
            force_github_flag = cfg.get(cfg.force_github)
        except Exception:
            force_github_flag = None
        logger.debug(
            "  [检查更新] 开关状态: force_full_download=%s, force_github=%s",
            self.force_full_download,
            force_github_flag,
        )

        self.download_url = None
        self.version_name = None
        self.release_note = ""

        # 尝试 Mirror 源（强制下载模式跳过）
        if not self.force_full_download and self.current_res_id:
            logger.info("  [检查更新] 尝试 MirrorChyan 源...")
            mirror_result = self.mirror_check(
                res_id=self.current_res_id,
                cdk=self.mirror_cdk,
                version=self.current_version,
                channel=self.current_channel,
                os_type=self.current_os_type,
                arch=self.current_arch,
            )

            mirror_status = mirror_result.get("status")
            mirror_data = mirror_result.get("data", {})
            mirror_url = mirror_data.get("url")
            mirror_version = mirror_data.get("version_name")
            self.release_note = mirror_data.get("release_note", "")
            logger.debug("  [检查更新] Mirror 返回状态: %s", mirror_result.get("msg"))

            # Mirror 检查表示当前版本已是最新
            if mirror_status == "no_need":
                logger.info("  [检查更新] Mirror: 当前已是最新版本")
                self.latest_update_version = self.current_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                return False
            elif mirror_status == "failed_info":
                logger.info(
                    "  [检查更新] Mirror 检查失败: %s", mirror_result.get("msg")
                )
                self._emit_info_bar("warning", mirror_result.get("msg"))

            # 记录 Mirror 返回的版本，用于后续逻辑或 GitHub 回退
            if mirror_version:
                self.version_name = mirror_version
                self.latest_update_version = mirror_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                if not fetch_download_url:
                    # 只需要版本信息就提前返回
                    self._emit_info_bar(
                        "info",
                        self.tr("Found update: ") + str(self.latest_update_version),
                    )
                    result_data = {
                        "source": "mirror",
                        "version": self.latest_update_version,
                        "update_type": mirror_data.get("update_type"),
                    }
                    if self.release_note:
                        result_data["release_note"] = self.release_note
                    if mirror_url:
                        result_data["url"] = mirror_url
                    return result_data

                # 下载模式下，Mirror 有直链则直接返回，否则继续 GitHub 获取下载
                if isinstance(mirror_url, str) and mirror_url:
                    logger.info("  [检查更新] Mirror: 找到新版本 %s", mirror_version)
                    logger.debug(
                        "  [检查更新] Mirror 下载地址: %s",
                        mirror_url[:80] if mirror_url else "N/A",
                    )
                    self.download_url = mirror_url
                    self._emit_info_bar(
                        "info",
                        self.tr("Found update: ") + str(self.latest_update_version),
                    )
                    return {
                        "url": mirror_url,
                        "update_type": mirror_data.get("update_type"),
                        "source": "mirror",
                        "version": self.latest_update_version,
                    }
                else:
                    logger.info(
                        "  [检查更新] Mirror 返回版本 %s 但无下载地址，回退 GitHub 获取包",
                        mirror_version,
                    )
            else:
                logger.info("  [检查更新] Mirror 未提供版本号，回退 GitHub 检查")
        else:
            # 进入该分支的两种典型情况：
            # 1) force_full_download=True：来自“重置资源”等场景
            # 2) current_res_id 为空：当前资源未配置 MirrorChyan ID
            if self.force_full_download:
                logger.info(
                    "  [检查更新] 因 force_full_download=True，使用强制下载模式，跳过 Mirror 源"
                )
            elif not self.current_res_id:
                logger.info(
                    "  [检查更新] 因未配置 MirrorChyan 资源ID，直接跳过 Mirror 源"
                )
            logger.info(
                "  [检查更新] 强制下载模式：跳过 Mirror 源，直接使用 GitHub 最新版本"
            )

        # 尝试 GitHub
        logger.info("  [检查更新] 切换到 GitHub 源...")
        if not self.url:
            logger.warning("  [检查更新] GitHub: 未配置项目地址")
            return False

        if self.version_name and not self.force_full_download:
            github_api_url = self._form_github_url(
                self.url, "download", self.version_name
            )
        else:
            github_api_url = self._form_github_url(self.url, "download")
        if not github_api_url:
            logger.warning("  [检查更新] GitHub: API 地址解析失败")
            return False

        logger.debug("  [检查更新] GitHub API: %s", github_api_url)

        # 调用 GitHub 接口查询最新 release
        github_result = self.github_check(
            github_api_url,
            version="" if self.force_full_download else self.current_version,
        )

        if not isinstance(github_result, dict):
            self._emit_info_bar("warning", self.tr("GitHub update check failed"))
            return False

        if github_result.get("status"):
            status = github_result.get("status")
            logger.info("  [检查更新] GitHub 返回状态: %s", status)
            if status == "failed":
                raw_msg = github_result.get("msg")
                msg = (
                    str(raw_msg)
                    if raw_msg is not None
                    else self.tr("GitHub update check failed")
                )
                self._emit_info_bar("error", msg)
                return False
            if status == "no_need" and not self.force_full_download:
                logger.info("  [检查更新] GitHub: 当前已是最新版本")
                self.latest_update_version = self.current_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                return False

        tag_name = github_result.get("tag_name") or github_result.get("name")
        target_version = str(
            tag_name or self.latest_update_version or self.current_version
        )
        body = github_result.get("body", "")
        self.release_note = str(body) if body is not None else ""

        if not fetch_download_url:
            self.latest_update_version = str(target_version)
            cfg.set(cfg.latest_update_version, self.latest_update_version)
            self._emit_info_bar(
                "info", self.tr("Found update: ") + str(self.latest_update_version)
            )
            result_data = {
                "source": "github",
                "version": self.latest_update_version,
            }
            if self.release_note:
                result_data["release_note"] = self.release_note
            return result_data

        download_asset = self._select_github_asset_by_keywords(
            github_result.get("assets", []) or [], target_version
        )
        if not download_asset:
            logger.warning("  [检查更新] GitHub: 未找到下载地址")
            return False

        download_url = download_asset.get("browser_download_url")

        if not download_url:
            logger.warning("  [检查更新] GitHub: 未找到下载地址")
            return False
        logger.info("  [检查更新] GitHub: 找到新版本 %s", tag_name)
        logger.debug(
            "  [检查更新] GitHub 下载地址: %s",
            download_url[:80] if download_url else "N/A",
        )
        self.download_url = download_url
        self.latest_update_version = str(target_version)
        cfg.set(cfg.latest_update_version, self.latest_update_version)
        self._emit_info_bar(
            "info", self.tr("Found update: ") + str(self.latest_update_version)
        )
        result_data = {
            "url": download_url,
            "source": "github",
            "version": self.latest_update_version,
        }
        if self.release_note:
            result_data["release_note"] = self.release_note
        return result_data

    def _select_github_asset_by_keywords(
        self,
        assets: Any,
        target_version: str,
        primary_name: str | None = None,
    ) -> dict | None:
        """
        在 GitHub release 资产中使用项目名、版本、OS、架构和压缩后缀组合匹配命中率最高的文件。
        """
        normalized_tokens = []
        for part in (
            primary_name or self.project_name,
            target_version,
            self.current_os_type,
            self.current_arch,
        ):
            if isinstance(part, str) and part:
                normalized_tokens.append(part.lower())

        version = str(target_version or "")
        stripped_version = version.lstrip("vV")
        if stripped_version and stripped_version != version:
            normalized_tokens.append(stripped_version.lower())

        normalized_assets = assets if isinstance(assets, list) else []
        best_asset = None
        best_score = -1
        for asset in normalized_assets:
            if not isinstance(asset, dict):
                continue
            asset_name = asset.get("name")
            if not isinstance(asset_name, str):
                continue
            normalized_name = asset_name.lower()
            if normalized_name.endswith(".tar.gz"):
                pass
            elif normalized_name.endswith(".zip"):
                pass
            else:
                continue

            score = sum(1 for token in normalized_tokens if token in normalized_name)
            if (
                best_asset is None
                or score > best_score
                or (
                    score == best_score
                    and len(normalized_name) < len(best_asset.get("name", "") or "")
                )
            ):
                best_asset = asset
                best_score = int(score)

        return best_asset

    def check_ui_update(self, fetch_download_url: bool = True) -> dict | bool:
        """
        检查 UI 更新：
        - 资源 ID 固定为 UI 程序自身（MFW-PyQt6）
        - 版本号固定使用应用的 __version__
        - 逻辑整体与 check_update 保持一致（Mirror 优先，其次 GitHub）
        """
        # 保证运行环境（os_type / arch / current_version 等）已初始化（UI 模式）
        self._init_run_context(ui_mode=True)

        logger.info("  [检查更新-UI] 开始检查 UI 更新...")
        from app.common.__version__ import __version__

        mirror_id = "MFW-PyQt6"
        fixed_version = __version__
        url = "https://github.com/overflow65537/MFW-PyQt6"
        ui_name = "MFW-PyQt6"
        self.release_note = ""

        # 将当前版本也同步为固定版本，避免与多资源上下文的版本不一致
        self.current_version = fixed_version

        logger.debug(
            "  [检查更新-UI] 资源ID: %s, CDK: %s",
            mirror_id,
            "***" if self.mirror_cdk else "无",
        )

        # 记录 UI 更新检查的关键开关状态
        try:
            force_github_flag = cfg.get(cfg.force_github)
        except Exception:
            force_github_flag = None
        logger.debug(
            "  [检查更新-UI] 开关状态: force_full_download=%s, force_github=%s",
            self.force_full_download,
            force_github_flag,
        )

        # 尝试 Mirror 源（强制下载模式跳过）
        if not self.force_full_download:
            logger.info("  [检查更新] 尝试 MirrorChyan 源...")
            mirror_result = self.mirror_check(
                res_id=mirror_id,
                cdk=self.mirror_cdk,
                version=fixed_version,
                channel="stable",
                os_type=self.current_os_type,
                arch=self.current_arch,
            )

            mirror_status = mirror_result.get("status")
            mirror_data = mirror_result.get("data", {})
            mirror_url = mirror_data.get("url")
            mirror_version = mirror_data.get("version_name")
            self.release_note = mirror_data.get("release_note", "")
            logger.debug("  [检查更新] Mirror 返回状态: %s", mirror_result.get("msg"))

            # Mirror 检查表示当前版本已是最新
            if mirror_status == "no_need":
                logger.info("  [检查更新] Mirror: 当前已是最新版本")
                self.latest_update_version = self.current_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                return False
            elif mirror_status == "failed_info":
                logger.info(
                    "  [检查更新] Mirror 检查失败: %s", mirror_result.get("msg")
                )
                self._emit_info_bar("warning", mirror_result.get("msg"))

            # 记录 Mirror 返回的版本，用于后续逻辑或 GitHub 回退
            if mirror_version:
                self.version_name = mirror_version
                self.latest_update_version = mirror_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                if not fetch_download_url:
                    # 只需要版本信息就提前返回
                    self._emit_info_bar(
                        "info",
                        self.tr("Found update: ") + str(self.latest_update_version),
                    )
                    result_data = {
                        "source": "mirror",
                        "version": self.latest_update_version,
                        "update_type": mirror_data.get("update_type"),
                    }
                    if self.release_note:
                        result_data["release_note"] = self.release_note
                    if mirror_url:
                        result_data["url"] = mirror_url
                    return result_data

                # 下载模式下，Mirror 有直链则直接返回，否则继续 GitHub 获取下载
                if isinstance(mirror_url, str) and mirror_url:
                    logger.info("  [检查更新] Mirror: 找到新版本 %s", mirror_version)
                    logger.debug(
                        "  [检查更新] Mirror 下载地址: %s",
                        mirror_url[:80] if mirror_url else "N/A",
                    )
                    self.download_url = mirror_url
                    self._emit_info_bar(
                        "info",
                        self.tr("Found update: ") + str(self.latest_update_version),
                    )
                    return {
                        "url": mirror_url,
                        "source": "mirror",
                        "version": self.latest_update_version,
                    }
                else:
                    logger.info(
                        "  [检查更新] Mirror 返回版本 %s 但无下载地址，回退 GitHub 获取包",
                        mirror_version,
                    )
            else:
                logger.info("  [检查更新] Mirror 未提供版本号，回退 GitHub 检查")
        else:
            logger.info(
                "  [检查更新] 强制下载模式：跳过 Mirror 源，直接使用 GitHub 最新版本"
            )

        # 尝试 GitHub
        logger.info("  [检查更新] 切换到 GitHub 源...")
        if not url:
            logger.warning("  [检查更新] GitHub: 未配置项目地址")
            return False

        if self.version_name and not self.force_full_download:
            github_api_url = self._form_github_url(url, "download", self.version_name)
        else:
            github_api_url = self._form_github_url(url, "download")
        if not github_api_url:
            logger.warning("  [检查更新] GitHub: API 地址解析失败")
            return False

        logger.debug("  [检查更新] GitHub API: %s", github_api_url)

        # 调用 GitHub 接口查询最新 release
        github_result = self.github_check(
            github_api_url,
            version="" if self.force_full_download else fixed_version,
        )

        if not isinstance(github_result, dict):
            self._emit_info_bar("warning", self.tr("GitHub update check failed"))
            return False

        if github_result.get("status"):
            status = github_result.get("status")
            logger.info("  [检查更新] GitHub 返回状态: %s", status)
            if status == "failed":
                raw_msg = github_result.get("msg")
                msg = (
                    str(raw_msg)
                    if raw_msg is not None
                    else self.tr("GitHub update check failed")
                )
                self._emit_info_bar("error", msg)
                return False
            if status == "no_need" and not self.force_full_download:
                logger.info("  [检查更新] GitHub: 当前已是最新版本")
                self.latest_update_version = self.current_version
                cfg.set(cfg.latest_update_version, self.latest_update_version)
                return False

        tag_name = github_result.get("tag_name") or github_result.get("name")
        target_version = str(
            tag_name or self.latest_update_version or self.current_version
        )
        body = github_result.get("body", "")
        self.release_note = str(body) if body is not None else ""

        if not fetch_download_url:
            self.latest_update_version = str(target_version)
            cfg.set(cfg.latest_update_version, self.latest_update_version)
            self._emit_info_bar(
                "info", self.tr("Found update: ") + str(self.latest_update_version)
            )
            result_data = {
                "source": "github",
                "version": self.latest_update_version,
            }
            if self.release_note:
                result_data["release_note"] = self.release_note
            return result_data

        download_asset = self._select_github_asset_by_keywords(
            github_result.get("assets", []) or [],
            target_version,
            primary_name=ui_name,
        )
        if not download_asset:
            logger.warning("  [检查更新] GitHub: 未找到下载地址")
            return False

        download_url = download_asset.get("browser_download_url")

        if not download_url:
            logger.warning("  [检查更新] GitHub: 未找到下载地址")
            return False
        logger.info("  [检查更新] GitHub: 找到新版本 %s", tag_name)
        logger.debug(
            "  [检查更新] GitHub 下载地址: %s",
            download_url[:80] if download_url else "N/A",
        )
        self.download_url = download_url
        self.latest_update_version = str(target_version)
        cfg.set(cfg.latest_update_version, self.latest_update_version)
        self._emit_info_bar(
            "info", self.tr("Found update: ") + str(self.latest_update_version)
        )
        result_data = {
            "url": download_url,
            "source": "github",
            "version": self.latest_update_version,
        }
        if self.release_note:
            result_data["release_note"] = self.release_note
        return result_data

    def stop(self):
        self.stop_flag = True
        self.stop_signal.emit(0)

    def _emit_info_bar(self, level: str, message: str | None):
        """向主界面请求显示 InfoBar 提示"""
        # 在仅检查模式下不打扰用户界面，避免弹出 InfoBar 提示
        if self.check_only:
            return
        if message:
            self.info_bar_signal.emit(level or "info", message)

    def _stop_with_notice(
        self, code: int, level: str | None = None, message: str | None = None
    ) -> None:
        """统一处理终止信号和可选的 InfoBar 通知。"""
        if level and message:
            self._emit_info_bar(level, message)
        self.stop_signal.emit(code)

    def _form_github_url(
        self, url: str, mode: str, version: str | None = None
    ) -> str | None:
        """根据给定的URL和模式返回相应的链接。

        Args:
            url (str): GitHub项目的URL。
            mode (str): 模式（"issue"、"download"、"about"或"update_flag"）。
            version (str | None): 指定版本，仅在 update_flag 模式下使用。

        Returns:
            str | None: 对应的链接。
        """
        parts = url.split("/")
        try:
            username = parts[3]
            repository = parts[4]
        except IndexError:
            return None
        return_url = None
        if mode == "issue":
            return_url = f"https://github.com/{username}/{repository}/issues"
        elif mode == "download":
            if version:
                return_url = f"https://api.github.com/repos/{username}/{repository}/releases/tags/{version}"
            else:
                return_url = f"https://api.github.com/repos/{username}/{repository}/releases/latest"
        elif mode == "about":
            return_url = f"https://github.com/{username}/{repository}"
        elif mode == "update_flag":
            if not version:
                return None
            return_url = f"https://raw.githubusercontent.com/{username}/{repository}/{version}/update_flag.txt"
        elif mode == "hotfix":
            if not version:
                return None
            return_url = f"https://api.github.com/repos/{username}/{repository}/zipball/{version}"
        return return_url


class MultiResourceUpdate(Update):
    """
    多资源更新子类，专门用于处理 bundle 的多资源更新。
    继承自 Update 类，重写 run 方法以使用多资源更新流程。
    """

    def _get_bundle_path(self) -> str | None:
        """根据 interface 中的 name 获取对应的 bundle 路径"""
        if not self.service_coordinator:
            logger.error("service_coordinator 未初始化")
            return None

        # 从 interface 中获取 bundle 名称
        bundle_name = self.interface.get("name", "")
        if not bundle_name:
            logger.error("interface 中未找到 bundle 名称")
            return None

        try:
            # 根据 bundle 名称获取 bundle 信息
            bundle_info = self.service_coordinator.config_service.get_bundle(
                bundle_name
            )
            bundle_path = bundle_info.get("path", "")

            if not bundle_path:
                logger.error(f"Bundle '{bundle_name}' 没有路径信息")
                return None

            # 处理相对路径
            bundle_path_obj = Path(bundle_path)
            if not bundle_path_obj.is_absolute():
                bundle_path_obj = Path.cwd() / bundle_path_obj

            logger.debug(f"获取到 bundle '{bundle_name}' 的路径: {bundle_path_obj}")
            return str(bundle_path_obj)
        except FileNotFoundError as e:
            logger.warning(f"Bundle '{bundle_name}' 不存在: {e}")
            return None
        except Exception as e:
            logger.error(f"获取 bundle '{bundle_name}' 路径失败: {e}")
            return None

    def run(self):
        """
        线程入口，使用多资源更新流程。
        """
        # 防止多次重复运行（包括误调用 run 或多次 start）
        if self._is_running:
            logger.warning("检测到更新线程重复运行请求，本次调用将被忽略")
            return

        self._is_running = True
        try:
            # 每次运行前按当前配置初始化上下文（包括 interface / 频道 / 版本 等）
            self._init_run_context()
            if self.check_only:
                logger.info("以仅检查模式运行更新器（check_only=True）")
                self._run_check_only()
            else:
                logger.info("以多资源适配模式运行更新器")
                self._run_multi_resource_update()
        finally:
            self._is_running = False

    def _run_multi_resource_update(self) -> None:
        """
        多资源适配模式更新流程：检查更新、下载更新包并执行热更新流程。
        """
        self.stop_flag = False

        deleted_backups: list[tuple[Path, Path]] = []
        # 资源目录热更新时使用的备份信息，用于异常时整体回滚
        resource_backup_dir: Path | None = None
        resource_backups: list[tuple[Path, Path]] = []

        try:
            if not self.service_coordinator:
                logger.error("service_coordinator 未初始化，无法执行更新")
                return self._stop_with_notice(0)

            # 步骤1: 检查更新
            logger.info("[步骤1] 开始检查更新...")
            self._emit_info_bar("info", self.tr("Checking for updates..."))
            update_info = self.check_update()

            download_url = (
                update_info.get("url") if isinstance(update_info, dict) else None
            )
            download_source = (
                update_info.get("source")
                if isinstance(update_info, dict)
                else "unknown"
            )
            if download_source == "github" and not self.interface.get(
                "mirrorchyan_multiplatform", False
            ):
                download_url = self._form_github_url(
                    self.url, "hotfix", str(self.latest_update_version)
                )

            if not download_url:
                if update_info is False:
                    logger.info("[步骤1] 当前已是最新版本，无需下载")
                    return self._stop_with_notice(
                        0, "info", self.tr("Already up to date")
                    )
                logger.error("[步骤1] 检查完成但未获取到下载地址")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))

            logger.info("[步骤1] 检查完成: 发现新版本 %s", self.latest_update_version)
            logger.info("[步骤1] 下载来源: %s", download_source)
            logger.info("[步骤1] 下载地址: %s", str(download_url)[:100])

            self._emit_info_bar("info", self.tr("Preparing to download update..."))

            download_dir = Path.cwd() / "update" / "new_version"
            download_dir.mkdir(parents=True, exist_ok=True)
            if not download_url:
                logger.error("[步骤2] 未设置下载地址，无法执行下载")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))
            logger.debug("[步骤2] 保存路径: %s", download_dir)

            logger.info("[步骤3] 开始下载更新包...")
            logger.debug("[步骤3] 下载地址: %s", download_url)
            self.download_attempts += 1
            downloaded_zip_path, download_error = self.download_file(
                download_url,
                download_dir,
                self.progress_signal,
                use_proxies=self.get_proxy_data(),
            )
            if not downloaded_zip_path:
                if download_error:
                    logger.error("[步骤3] 下载失败: %s", download_error)
                    return self._stop_with_notice(
                        0,
                        "error",
                        f"{self.tr('Download failed')}: {download_error}",
                    )
                logger.error("[步骤3] 下载失败")
                return self._stop_with_notice(0, "error", self.tr("Download failed"))
            zip_file_path = downloaded_zip_path
            logger.debug("[步骤3] 下载文件: %s", zip_file_path)

            logger.info(
                "[步骤3] 下载完成，大小: %.2f MB",
                zip_file_path.stat().st_size / (1024 * 1024),
            )
            self._emit_info_bar("success", self.tr("Download complete"))

            logger.info("[步骤4] 开始执行热更新，准备解压更新包...")
            self._emit_info_bar("info", self.tr("Applying hotfix..."))

            logger.debug("[步骤4] 解压更新包到 hotfix 目录")
            hotfix_dir = Path.cwd() / "hotfix"
            hotfix_root = self.extract_zip(zip_file_path, hotfix_dir)
            if not hotfix_root:
                logger.error("[步骤4] 解压更新包失败")
                return self._stop_with_notice(2)
            logger.info("[步骤4] 更新包解压完成: %s", hotfix_root)

            # 获取 bundle 路径
            bundle_path = self._get_bundle_path()
            if not bundle_path:
                logger.warning("[步骤4] Bundle 配置不存在，跳过热更新")
                return self._stop_with_notice(2)
            bundle_path_obj = Path(bundle_path)
            logger.debug("[步骤4] Bundle 路径: %s", bundle_path_obj)

            logger.info("[步骤5] 使用安全覆盖模式进行热更新")

            project_path = bundle_path_obj
            if not hotfix_root or not hotfix_root.exists():
                logger.error("[步骤5] hotfix 目录不存在，无法覆盖")
                return self._stop_with_notice(2)

            # 备份并删除资源文件中的 pipeline 目录，以供后续无损覆盖
            resource_backup_dir = Path.cwd() / "backup" / "resource"
            resource_backup_dir.mkdir(parents=True, exist_ok=True)
            resource_list = self.interface.get("resource", [])
            known_resources: list[str] = []
            resource_backups.clear()
            for resource in resource_list:
                logger.debug("[步骤5] 处理资源: %s", resource.get("name", ""))

                for resource_path_str in resource.get("path", []):
                    logger.debug("[步骤5] 处理资源路径: %s", resource_path_str)
                    resource_path = Path(resource_path_str.replace("{PROJECT_DIR}", ""))
                    if resource_path.is_dir() and (
                        resource_path_str not in known_resources
                    ):
                        backup_target = resource_backup_dir / resource_path.name
                        try:
                            # 先备份资源
                            if backup_target.is_dir():
                                shutil.rmtree(backup_target)
                            shutil.copytree(str(resource_path), str(backup_target))
                            logger.debug("[步骤5] 已备份资源目录: %s", resource_path)

                            resource_backups.append((resource_path, backup_target))
                            known_resources.append(resource_path_str)

                            # 再删除旧的 pipeline 目录，避免影响后续覆盖
                            pipeline_path = resource_path / "pipeline"
                            if pipeline_path.exists():
                                shutil.rmtree(str(pipeline_path))
                                logger.debug(
                                    "[步骤5] 已删除旧 pipeline 目录: %s",
                                    pipeline_path,
                                )
                        except Exception as backup_err:
                            logger.exception(
                                "[步骤5] 备份或清理资源目录时出错: %s -> %s",
                                resource_path,
                                backup_err,
                            )
                            raise

            logger.info("[步骤5] 开始覆盖项目目录: %s", project_path)
            # 允许目标目录已存在（Python 3.8+ 支持 dirs_exist_ok）
            # 这样在 bundle 目录本身已存在时不会因 WinError 183 直接失败
            shutil.copytree(hotfix_root, project_path, dirs_exist_ok=True)

            interface_path = [
                bundle_path_obj / "interface.jsonc",
                bundle_path_obj / "interface.json",
            ]

            for path in interface_path:
                if path.exists():
                    interface = self._read_config(str(path))
                    if interface:
                        interface["version"] = self.latest_update_version
                        with open(path, "w", encoding="utf-8") as f:
                            jsonc.dump(interface, f, indent=4, ensure_ascii=False)
                        logger.info("[步骤5] 更新 interface.jsonc 成功")
                        break
            logger.info("[步骤5] interface 配置同步完毕")

            # 步骤5: 完成
            logger.info("[步骤5] 热更新成功完成!")
            logger.info("=" * 50)
            self._emit_info_bar("success", self.tr("Update applied successfully"))
            self._cleanup_update_artifacts(download_dir, zip_file_path)
            # 触发服务协调器重新初始化
            signalBus.fs_reinit_requested.emit()
            self.stop_signal.emit(1)

        except Exception as e:
            # 资源目录异常回滚
            if resource_backups:
                logger.warning("[步骤5] 更新失败，正在恢复资源备份目录...")
                for original_path, backup_path in reversed(resource_backups):
                    try:
                        if not backup_path.exists():
                            continue
                        # 清理已被部分覆盖/删除的原目录
                        if original_path.exists():
                            if original_path.is_file():
                                original_path.unlink()
                            else:
                                shutil.rmtree(original_path)
                        # 使用备份进行还原
                        if backup_path.is_dir():
                            shutil.copytree(backup_path, original_path)
                        else:
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_path, original_path)
                        logger.debug("[步骤5] 已恢复资源目录: %s", original_path)
                    except Exception as restore_err:
                        logger.exception(
                            "[步骤5] 恢复资源目录失败: %s -> %s",
                            original_path,
                            restore_err,
                        )
                resource_backups.clear()

            if deleted_backups:
                logger.warning("[步骤5] 更新失败，正在恢复已删除文件...")
                for original_path, backup_path in reversed(deleted_backups):
                    try:
                        if backup_path.exists():
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup_path, original_path)
                            logger.debug("[步骤5] 已恢复文件: %s", original_path)
                    except Exception as restore_err:
                        logger.exception(
                            "[步骤5] 恢复文件失败: %s -> %s",
                            original_path,
                            restore_err,
                        )
                deleted_backups.clear()
            logger.exception("更新过程中出现错误: %s", e)
            self._stop_with_notice(0, "error", self.tr("Failed to update"))
        finally:
            # 清理资源备份目录
            if resource_backup_dir and resource_backup_dir.exists():
                try:
                    shutil.rmtree(resource_backup_dir)
                except Exception as cleanup_err:
                    logger.debug("[步骤5] 清理资源备份目录失败: %s", cleanup_err)
