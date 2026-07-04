from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "release"


def main() -> int:
    version = read_version()
    release_dir = RELEASE_ROOT / f"mc-han-{version}-windows"
    staging_dir = RELEASE_ROOT / "_pyinstaller"
    safe_rmtree(release_dir)
    safe_rmtree(staging_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    build_exe(
        name="mc-han-cli",
        entry=ROOT / "src" / "mc_han" / "cli_main.py",
        windowed=False,
        staging_dir=staging_dir,
        release_dir=release_dir,
    )
    build_exe(
        name="mc-han-gui",
        entry=ROOT / "src" / "mc_han" / "gui_main.py",
        windowed=True,
        staging_dir=staging_dir,
        release_dir=release_dir,
    )
    copy_release_docs(release_dir, version)
    zip_path = RELEASE_ROOT / f"mc-han-{version}-windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    make_zip(release_dir, zip_path)
    write_sha256(zip_path)
    print(f"Release directory: {release_dir}")
    print(f"Release zip: {zip_path}")
    return 0


def read_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find project version in pyproject.toml")
    return match.group(1)


def build_exe(
    *,
    name: str,
    entry: Path,
    windowed: bool,
    staging_dir: Path,
    release_dir: Path,
) -> None:
    dist_dir = staging_dir / "dist"
    build_dir = staging_dir / "build" / name
    spec_dir = staging_dir / "spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--paths",
        str(ROOT / "src"),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--add-data",
        f"{ROOT / 'src' / 'mc_han' / 'data'}{os.pathsep}mc_han/data",
    ]
    command.append("--windowed" if windowed else "--console")
    if not windowed:
        command.extend(["--exclude-module", "tkinter", "--exclude-module", "mc_han.gui"])
    command.append(str(entry))
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run(command, cwd=ROOT, check=True, env=env)
    exe_path = dist_dir / f"{name}.exe"
    if not exe_path.exists():
        raise FileNotFoundError(exe_path)
    shutil.copy2(exe_path, release_dir / exe_path.name)


def copy_release_docs(release_dir: Path, version: str) -> None:
    shutil.copy2(ROOT / "README.md", release_dir / "README.md")
    text = f"""mc-han {version} Windows 版

普通玩家：
1. 双击 mc-han-gui.exe。
2. 选择 Minecraft 整合包目录。
3. 填写 API Key、模型名和 Base URL，点击“测试API”。
4. 选择翻译速度：默认“平衡”；先用并发 1，稳定后可尝试并发 2 或 3。
5. 如需翻译物品/方块/实体/流体名称，在高级选项里开启名称翻译；译文会保留英文原名。
6. 点击“扫描”，再点击“试翻译10条”。
7. 在实时表格里查看原文/译文，满意后点击“继续翻译”。
8. 需要中断时点“暂停”，继续时点“恢复”。
9. 翻译完成后生成客户端资源包、服务端任务包或完整安装包。
10. 点击“安装预演”查看将要安装和覆盖的文件。
11. 点击“安装”会先检查输出并备份已有文件。
12. 需要撤回时点击“回滚安装”。
13. 如需下次免输入，可点击“保存配置”保存本机 Provider、模型、API Key、速度、并发和名称翻译设置。

高级用户：
- mc-han-cli.exe scan <整合包目录>
- mc-han-cli.exe preview <CSV>
- mc-han-cli.exe translate <整合包目录> --provider deepseek --model deepseek-chat --speed-mode balanced --concurrency 1 --limit 20
- mc-han-cli.exe translate <整合包目录> --provider custom --base-url https://example.com/v1 --model model-name --api-key ...
- mc-han-cli.exe all <整合包目录> --provider deepseek --model deepseek-chat --translate-names --limit 20
- mc-han-cli.exe config save --provider deepseek --model deepseek-chat --api-key ... --speed-mode balanced --concurrency 1 --translate-names
- mc-han-cli.exe all <整合包目录> --use-config
- mc-han-cli.exe review <CSV>
- mc-han-cli.exe build <整合包目录>
- mc-han-cli.exe install <整合包目录> --dry-run
- mc-han-cli.exe install <整合包目录>
- mc-han-cli.exe install <整合包目录> --rollback

安全说明：
- API Key 默认只从输入框、命令行参数或环境变量读取。
- 只有点击“保存配置”或使用 --save-config/config save 时才会写入本机配置。
- mc-han 不会把 API Key 写入缓存、报告或构建产物。
- GUI 状态保存在整合包的 .mc-han 目录，翻译缓存保存在 translations.sqlite。
- 安装前会自动检查输出，安装时会备份已有文件并写入 install_manifest.json。
- 不会直接修改 mods/*.jar。
- 名称翻译必须保留英文原名，格式为：中文名 (English Original)。
- 多人联机建议所有玩家安装同一个客户端资源包，否则看到的名称可能不一致。
"""
    (release_dir / "使用说明.txt").write_text(text, encoding="utf-8")


def make_zip(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir.parent))


def write_sha256(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256.txt").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    release_root = RELEASE_ROOT.resolve()
    if not str(resolved).startswith(str(release_root)):
        raise RuntimeError(f"Refusing to delete outside release directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
