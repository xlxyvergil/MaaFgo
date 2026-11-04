"""Python识图与框架实际加载层一致；兼容没有实际层标记的旧资源包。"""
import mfaalog


def resolve_resource_package(context, *, caller):
    config = context.get_node_data("资源包配置") or {}
    attach = config.get("attach") or {}
    configured = "cn" if str(attach.get("resource_package") or "base").strip() == "cn" else "base"
    loaded = attach.get("loaded_resource_package")
    if loaded is None:
        return configured
    if loaded not in ("base", "cn"):
        raise ValueError(f"{caller}实际资源包标记无效：{loaded!r}")
    if loaded != configured:
        mfaalog.warning(
            f"[{caller}] 任务资源包标识={configured} 与实际加载素材={loaded} 不一致；"
            "按实际加载素材识别"
        )
    return loaded
