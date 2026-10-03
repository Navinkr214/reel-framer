"""Make a self-contained copy of this Mac's ffmpeg and ffprobe for the app bundle:

    <out>/bin/ffmpeg, <out>/bin/ffprobe     the programs
    <out>/lib/*.dylib                       every non-system library they load, recursively
    <out>/minos.txt                         the oldest macOS all of them run on

Homebrew links ffmpeg to its libraries by absolute paths (/opt/homebrew/...) and some by
@rpath. Each library is copied once, under the name it is referenced by; every reference
is rewritten to @rpath/<name>; the programs get the rpath @executable_path/../lib and the
libraries @loader_path; and each changed file is signed again ad hoc, because Apple Silicon
will not run modified code with a broken signature. System libraries (/usr/lib, /System)
stay as they are: every Mac has them. Finally the copy is run with an empty environment to
prove it needs nothing outside itself.

Usage:  python desktop/bundle_ffmpeg.py <out-dir>
Called by: desktop/build_mac.sh.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROGRAMS = ("ffmpeg", "ffprobe")
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def references(path: Path) -> list[str]:
    """Libraries a Mach-O file loads (its own install name left out)."""
    own = run("otool", "-D", str(path)).splitlines()[1:]
    lines = run("otool", "-L", str(path)).splitlines()[1:]
    deps = [line.strip().split(" (compatibility")[0] for line in lines if line.strip()]
    return [dep for dep in deps if dep not in own]


def rpaths(path: Path) -> list[str]:
    return re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+?) \(offset", run("otool", "-l", str(path)))


def min_macos(path: Path) -> tuple[int, ...]:
    match = re.search(r"minos (\d+(?:\.\d+)*)", run("otool", "-l", str(path)))
    return tuple(int(p) for p in match.group(1).split(".")) if match else (0,)


def resolve(dep: str, referrer: Path, program: Path) -> Path | None:
    def expand(prefix_path: str) -> Path:
        return Path(prefix_path.replace("@loader_path", str(referrer.parent))
                    .replace("@executable_path", str(program.parent)))

    if dep.startswith("@rpath/"):
        for rp in rpaths(referrer):
            candidate = expand(rp) / dep[len("@rpath/"):]
            if candidate.exists():
                return candidate.resolve()
        return None
    if dep.startswith(("@loader_path/", "@executable_path/")):
        candidate = expand(dep)
        return candidate.resolve() if candidate.exists() else None
    return Path(dep).resolve() if Path(dep).exists() else None


def bundle(out: Path) -> None:
    bin_dir, lib_dir = out / "bin", out / "lib"
    shutil.rmtree(out, ignore_errors=True)
    bin_dir.mkdir(parents=True)
    lib_dir.mkdir()
    names: dict[Path, str] = {}          # library's real file -> the name it gets in the bundle
    refs: dict[Path, list[tuple[str, Path]]] = {}   # bundled file -> [(reference as written, real file)]
    queue: list[tuple[Path, Path]] = []  # (real file, the program it serves)
    for program in PROGRAMS:
        found = shutil.which(program)
        if not found:
            sys.exit(f"{program} is not installed (macOS: brew install ffmpeg).")
        real = Path(found).resolve()
        shutil.copy2(real, bin_dir / program)
        queue.append((real, real))
        names[real] = program
    seen: set[Path] = set()
    while queue:
        real, program = queue.pop()
        if real in seen:
            continue
        seen.add(real)
        refs[real] = []
        for dep in references(real):
            if dep.startswith(SYSTEM_PREFIXES):
                continue
            target = resolve(dep, real, program)
            if target is None:
                sys.exit(f"{real.name} needs {dep}, which cannot be found.")
            if target not in names:
                names[target] = Path(dep).name
                shutil.copy2(target, lib_dir / names[target])
                queue.append((target, program))
            refs[real].append((dep, target))

    oldest = (0,)
    for real, name in names.items():
        is_program = name in PROGRAMS
        copy = (bin_dir if is_program else lib_dir) / name
        os.chmod(copy, 0o755)
        for dep, target in refs.get(real, []):
            run("install_name_tool", "-change", dep, f"@rpath/{names[target]}", str(copy))
        if not is_program:
            run("install_name_tool", "-id", f"@rpath/{name}", str(copy))
        for rp in rpaths(copy):
            run("install_name_tool", "-delete_rpath", rp, str(copy))
        run("install_name_tool", "-add_rpath", "@executable_path/../lib" if is_program else "@loader_path", str(copy))
        run("codesign", "--force", "--sign", "-", str(copy))
        oldest = max(oldest, min_macos(copy))

    leaks = [f"{f.name}: {d}" for f in [*bin_dir.iterdir(), *lib_dir.iterdir()] for d in references(f)
             if not d.startswith(("@rpath/", *SYSTEM_PREFIXES))]
    if leaks:
        sys.exit("Still points outside the bundle:\n" + "\n".join(leaks))
    for program in PROGRAMS:  # an empty environment: nothing from Homebrew or the shell to lean on
        subprocess.run([str(bin_dir / program), "-hide_banner", "-version"], env={}, check=True, capture_output=True)
    (out / "minos.txt").write_text(".".join(str(p) for p in oldest) + "\n")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"{len(names) - len(PROGRAMS)} libraries, {size / 1e6:.0f} MB, needs macOS {'.'.join(map(str, oldest))}+")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    bundle(Path(sys.argv[1]).resolve())
