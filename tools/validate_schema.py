#!/usr/bin/env python3
import json
import sys
import tempfile
import argparse
import subprocess
from pathlib import Path
from jsonschema import Draft7Validator, Draft202012Validator
from jsonschema.exceptions import ValidationError

try:
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012, DRAFT7
    import referencing.retrieval

    HAS_REFERENCING = True
except ImportError:
    # 如果没有 referencing 库，回退到旧的 RefResolver
    from jsonschema import RefResolver

    HAS_REFERENCING = False


def strip_jsonc_comments(text):
    """
    移除 JSONC 注释，保持 JSON 结构完整
    """
    # 状态机：0=正常, 1=字符串中, 2=转义字符
    result = []
    state = 0
    i = 0

    while i < len(text):
        char = text[i]

        if state == 0:  # 正常状态
            if char == '"':
                result.append(char)
                state = 1
                i += 1
            elif i + 1 < len(text) and text[i : i + 2] == "//":
                # 单行注释，跳到行尾
                while i < len(text) and text[i] != "\n":
                    i += 1
                if i < len(text):
                    result.append("\n")  # 保留换行
                    i += 1
            elif i + 1 < len(text) and text[i : i + 2] == "/*":
                # 多行注释，跳到 */
                i += 2
                while i + 1 < len(text) and text[i : i + 2] != "*/":
                    if text[i] == "\n":
                        result.append("\n")  # 保留换行以维持行号
                    i += 1
                i += 2  # 跳过 */
            else:
                result.append(char)
                i += 1
        elif state == 1:  # 字符串中
            result.append(char)
            if char == "\\":
                state = 2
            elif char == '"':
                state = 0
            i += 1
        elif state == 2:  # 转义字符
            result.append(char)
            state = 1
            i += 1

    return "".join(result)


def load_jsonc(file_path):
    """加载 JSONC 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 移除注释
    clean_content = strip_jsonc_comments(content)

    try:
        return json.loads(clean_content)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in {file_path}: {e}")
        # 调试：保存清理后的内容
        debug_file = Path(tempfile.gettempdir()) / f"debug_{Path(file_path).name}"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(clean_content)
        print(f"Cleaned content saved to {debug_file}")
        raise


def get_validator_class(schema):
    """根据 schema 的 $schema 字段选择合适的验证器"""
    schema_uri = schema.get("$schema", "")

    if "draft-07" in schema_uri or "draft/07" in schema_uri:
        return Draft7Validator
    elif "2020-12" in schema_uri:
        return Draft202012Validator
    else:
        # 默认使用 2020-12
        return Draft202012Validator


def find_line_number(file_path, json_path):
    """在文件中查找JSON路径对应的行号

    为了避免找到错误的子字段，只返回顶层对象的行号
    例如：/NoSmallGlobe/recognition -> 返回 NoSmallGlobe 的行号
    """
    if not json_path or json_path == "/":
        return None

    parts = [p for p in json_path.split("/") if p]
    if not parts:
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 只查找第一层（顶层对象）
        # 确保找到的是键名（后面跟着冒号），而不是注释或字符串值
        key = parts[0]
        import re

        # 匹配 "key": 或 "key" : 的模式
        pattern = re.compile(rf'"{re.escape(key)}"\s*:')

        for i, line in enumerate(lines):
            if pattern.search(line):
                return i + 1  # 行号从1开始

    except:
        pass

    return None


def validate_file(file_path, validator):
    """验证单个文件"""
    try:
        data = load_jsonc(file_path)
        errors = list(validator.iter_errors(data))

        if errors:
            print(f"\n❌ Validation failed for {file_path}:")
            print(f"   Found {len(errors)} error(s):")
            for idx, error in enumerate(errors[:10], 1):
                path = "/" + "/".join(str(p) for p in error.path) if error.path else "/"
                # print(f"   {idx}. {path}: {error.message}")

                # 尝试找到行号并输出GitHub Actions格式的错误注解
                line_num = find_line_number(file_path, path)
                if line_num:
                    print(
                        f"::error file={file_path},line={line_num},title=Schema Validation Error::{path}: {error.message}"
                    )
                else:
                    print(
                        f"::error file={file_path},title=Schema Validation Error::{path}: {error.message}"
                    )
            return False

        print(f"✓ {file_path}")
        return True
    except Exception as e:
        print(f"\n❌ Error validating {file_path}: {e}")
        # 输出GitHub Actions格式的错误注解
        print(f"::error file={file_path},title=Validation Error::{e}")
        return False


def validate_battle_preset_file(file_path):
    """Check shipped BattleShareData with the same offline validator as the editor."""
    try:
        project_root = str(Path(__file__).resolve().parents[1])
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from agent.battle_editor.validation import validate_battle

        report = validate_battle(load_jsonc(file_path))
        errors = [issue for issue in report["issues"] if issue["severity"] == "error"]
        if not report["valid"]:
            print(f"\n❌ Battle preset validation failed for {file_path}:")
            for issue in errors[:10]:
                print(
                    f"::error file={file_path},title=Battle Preset Validation Error::"
                    f"{issue['path']}: {issue['message']}"
                )
            return False
        print(f"✓ Battle preset {file_path}")
        return True
    except Exception as exc:
        print(f"\n❌ Error validating battle preset {file_path}: {exc}")
        print(f"::error file={file_path},title=Battle Preset Validation Error::{exc}")
        return False


def validate_resource_file(file_path, pipeline_validator):
    """Only the dedicated base/battle_presets data directory has a different contract."""
    file_path = Path(file_path)
    if file_path.parent.name == "battle_presets" and file_path.parent.parent.name == "base":
        return validate_battle_preset_file(file_path)
    return validate_file(file_path, pipeline_validator)


def create_validator(schema, schema_store):
    """创建 validator，使用新的 referencing API 或回退到 RefResolver"""
    ValidatorClass = get_validator_class(schema)

    if HAS_REFERENCING:
        # 使用新的 referencing API
        registry = Registry()

        # 根据 schema 类型选择规范
        spec = DRAFT202012 if ValidatorClass == Draft202012Validator else DRAFT7

        # 添加所有 schema 到 registry
        for uri, schema_content in schema_store.items():
            resource = Resource.from_contents(
                schema_content, default_specification=spec
            )
            registry = registry.with_resource(uri, resource)

        return ValidatorClass(schema, registry=registry)
    else:
        # 回退到旧的 RefResolver
        # 从 schema_store 中找到主 schema 的 URI
        schema_uri = None
        for uri, content in schema_store.items():
            if content == schema:
                schema_uri = uri
                break

        if schema_uri is None:
            schema_uri = "file:///schema.json"

        resolver = RefResolver(base_uri=schema_uri, referrer=schema, store=schema_store)
        return ValidatorClass(schema, resolver=resolver)


def validate_interface_references(interface_path, require_tracked=False):
    """验证导入文件与任务入口，发布校验可额外拒绝本机未跟踪依赖。"""
    interface_path = Path(interface_path).resolve()
    documents, visiting = {}, set()

    def visit(path):
        path = path.resolve()
        if path in visiting:
            raise ValueError(f"Import cycle: {path}")
        if path in documents:
            return
        if not path.is_file():
            raise ValueError(f"Missing import: {path}")
        if require_tracked and path != interface_path:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", path.name],
                cwd=path.parent, capture_output=True,
            )
            if result.returncode:
                raise ValueError(f"Untracked import: {path}")
        visiting.add(path)
        document = load_jsonc(path)
        if not isinstance(document, dict):
            raise ValueError(f"Interface import must be an object: {path}")
        imports = document.get("import", [])
        if not isinstance(imports, list) or any(not isinstance(item, str) for item in imports):
            raise ValueError(f"Import must be a list of paths: {path}")
        for imported in imports:
            visit(path.parent / imported)
        visiting.remove(path)
        documents[path] = document

    try:
        visit(interface_path)
        resources = [r for d in documents.values() for r in d.get("resource", [])]
        nodes = set()
        for resource in resources:
            for relative in resource.get("path", []):
                bundle = interface_path.parent / relative
                for pattern in ("*.json", "*.jsonc"):
                    for path in (bundle / "pipeline").rglob(pattern):
                        if not path.name.endswith(".mpe.json"):
                            nodes.update(load_jsonc(path))
        for document in documents.values():
            for task in document.get("task", []):
                if task.get("entry") and task["entry"] not in nodes:
                    raise ValueError(f"Missing task entry: {task['entry']}")
        return True
    except (OSError, ValueError, TypeError) as exc:
        print(f"::error file={interface_path},title=Interface reference error::{exc}")
        return False


def main():
    # Windows 管道默认可能为 GBK，状态符号也不能令整个校验器崩溃。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="Validate JSON/JSONC files against JSON Schema"
    )
    parser.add_argument(
        "--schema-dir",
        type=str,
        default="tools/schema",
        help="Directory containing schema files (default: tools/schema)",
    )
    parser.add_argument(
        "--resource-dirs",
        type=str,
        nargs="+",
        default=["assets/resource"],
        help="Directories containing resource files to validate (default: assets/resource)",
    )
    parser.add_argument(
        "--exclude-dirs",
        type=str,
        nargs="*",
        default=[],
        help="Directories to exclude from pipeline validation (default: none)",
    )
    parser.add_argument(
        "--interface-files",
        type=str,
        nargs="+",
        default=["assets/interface.json"],
        help="Path to interface.json files (default: assets/interface.json)",
    )
    parser.add_argument(
        "--task-dirs",
        type=str,
        nargs="*",
        default=[],
        help="Directories containing task files to validate against interface_import.schema.json (default: none)",
    )

    parser.add_argument("--require-tracked-imports", action="store_true",
                        help="Reject imported files absent from Git's index")
    args = parser.parse_args()

    all_valid = True

    # 加载所有 schema 文件
    schema_dir = Path(args.schema_dir).resolve()
    required = ["pipeline.schema.json", "interface.schema.json"]
    if args.task_dirs:
        required.append("interface_import.schema.json")
    missing = [name for name in required if not (schema_dir / name).is_file()]
    if missing:
        parser.error("Missing required schemas: " + ", ".join(missing))
    schema_store = {}

    print("Loading schemas...")
    for schema_file in schema_dir.glob("*.json"):
        try:
            schema = load_jsonc(schema_file)
            # 使用多种格式的 URI 作为 key
            file_uri = schema_file.as_uri()
            relative_path = f"./{schema_file.name}"
            absolute_path = f"/{schema_file.name}"

            schema_store[file_uri] = schema
            schema_store[schema_file.name] = schema
            schema_store[relative_path] = schema
            schema_store[absolute_path] = schema
        except Exception as e:
            print(f"Warning: Failed to load schema {schema_file}: {e}")

    # 加载并创建 pipeline validator
    pipeline_schema_path = schema_dir / "pipeline.schema.json"
    pipeline_schema = load_jsonc(pipeline_schema_path)
    pipeline_schema_uri = pipeline_schema_path.as_uri()
    schema_store[pipeline_schema_uri] = pipeline_schema

    pipeline_validator = create_validator(pipeline_schema, schema_store)

    # 准备排除目录列表
    exclude_paths = [Path(d).resolve() for d in args.exclude_dirs]

    def is_excluded(file_path):
        """检查文件是否在排除目录中"""
        file_path = Path(file_path).resolve()
        for exclude_path in exclude_paths:
            try:
                file_path.relative_to(exclude_path)
                return True
            except ValueError:
                continue
        return False

    print("Validating pipeline resources...")
    # 验证 pipeline 资源文件
    for resource_dir in args.resource_dirs:
        resource_path = Path(resource_dir)
        if not resource_path.exists():
            print(
                f"Error: Resource directory {resource_dir} does not exist"
            )
            all_valid = False
            continue

        for file_path in resource_path.rglob("*.json"):
            # 跳过 MaaPipelineEditor 的配置文件
            if file_path.name.endswith(".mpe.json"):
                continue
            if is_excluded(file_path):
                continue
            if not validate_resource_file(file_path, pipeline_validator):
                all_valid = False

        for file_path in resource_path.rglob("*.jsonc"):
            if is_excluded(file_path):
                continue
            if not validate_resource_file(file_path, pipeline_validator):
                all_valid = False

    print("\nValidating interface files...")
    # 验证 interface 文件
    interface_schema_path = schema_dir / "interface.schema.json"
    if interface_schema_path.exists():
        interface_schema = load_jsonc(interface_schema_path)
        interface_schema_uri = interface_schema_path.as_uri()
        schema_store[interface_schema_uri] = interface_schema

        interface_validator = create_validator(interface_schema, schema_store)

        for interface_file in args.interface_files:
            interface_path = Path(interface_file)
            if interface_path.exists():
                if not validate_file(interface_path, interface_validator):
                    all_valid = False
                elif not validate_interface_references(interface_path, args.require_tracked_imports):
                    all_valid = False
            else:
                print(
                    f"Error: Interface file {interface_file} does not exist"
                )
                all_valid = False

    # 验证 task 文件
    if args.task_dirs:
        print("\nValidating task files...")
        task_schema_path = schema_dir / "interface_import.schema.json"
        if task_schema_path.exists():
            task_schema = load_jsonc(task_schema_path)
            task_schema_uri = task_schema_path.as_uri()
            schema_store[task_schema_uri] = task_schema

            task_validator = create_validator(task_schema, schema_store)

            for task_dir in args.task_dirs:
                task_path = Path(task_dir)
                if not task_path.exists():
                    print(
                        f"Error: Task directory {task_dir} does not exist"
                    )
                    all_valid = False
                    continue

                for file_path in task_path.rglob("*.json"):
                    if not validate_file(file_path, task_validator):
                        all_valid = False

                for file_path in task_path.rglob("*.jsonc"):
                    if not validate_file(file_path, task_validator):
                        all_valid = False
        else:
            print(
                f"Error: Task schema {task_schema_path} does not exist"
            )
            all_valid = False

    if all_valid:
        print("\n✅ All validations passed!")
        sys.exit(0)
    else:
        print("\n❌ Some validations failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
