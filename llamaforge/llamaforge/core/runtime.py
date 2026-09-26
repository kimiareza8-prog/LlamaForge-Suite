from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .net import request_json, open_url, UA

GITHUB_LATEST_RELEASE = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
GITHUB_RELEASES_LIST = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=50"

# Conservative compatibility floors for architectures introduced well after older builds.
# These are only used to block definitely-too-old runtimes; newer runtimes are still
# allowed to perform the authoritative model load.
MIN_BUILD_BY_ARCH = {
    "gemma4": 8700,
}


class RuntimeManager:
    def __init__(self, runtime_dir: str, custom_server_path: str = ""):
        self.runtime_dir = Path(runtime_dir).expanduser()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.custom_server_path = custom_server_path
        self._caps_cache: tuple[str, float, set[str]] | None = None

    def set_custom_server(self, path: str) -> None:
        self.custom_server_path = str(Path(path).expanduser()) if path else ""
        self._caps_cache = None

    def use_managed_runtime(self) -> None:
        """Stop forcing a user-selected executable and use the managed install."""
        self.custom_server_path = ""
        self._caps_cache = None

    @staticmethod
    def parse_build_number(text: str) -> int | None:
        """Extract a plausible llama.cpp bNNNN build number.

        Some official Windows binaries currently print ``version: 0`` even
        though the release/archive and install directory carry the real bNNNN
        build.  Zero therefore means "unknown", not build zero.  If several
        candidates are present, prefer the largest positive candidate.
        """
        text = text or ""
        candidates: list[int] = []
        patterns = (
            r"\bversion:\s*b?(\d+)\b",
            r"\bbuild:\s*b?(\d+)\b",
            r"\bbuild(?:[_ -]?number)?\s*[=:]\s*b?(\d+)\b",
            r"(?:^|[\\/_.-])b(\d{3,})(?=$|[\\/_.-])",
            r"\bb(\d{3,})\b",
        )
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE):
                try:
                    value = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                if value > 0:
                    candidates.append(value)
        return max(candidates) if candidates else None

    def _installed_manifest(self) -> dict:
        pointer = self.runtime_dir / "installed.json"
        if not pointer.exists():
            return {}
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _detect_build_number(self, server: str, version_output: str = "") -> tuple[int | None, str]:
        """Detect build using output first, then managed metadata/path.

        Returns ``(build, source)``.  Unknown is deliberately represented by
        ``None`` so compatibility preflight never blocks a model solely because
        an upstream binary reported ``version: 0``.
        """
        sources: list[tuple[str, str]] = []
        if version_output:
            sources.append(("version output", version_output))
        if server:
            sources.append(("runtime path", str(server)))

        manifest = self._installed_manifest()
        tag = str(manifest.get("tag", "") or "")
        managed_path = str(manifest.get("path", "") or "")
        try:
            is_managed = bool(server and managed_path and Path(server).resolve().is_relative_to(Path(managed_path).resolve()))
        except (OSError, ValueError, AttributeError):
            # Python < 3.9 fallback / unusual filesystem edge case.
            try:
                is_managed = bool(server and managed_path and os.path.commonpath([str(Path(server).resolve()), str(Path(managed_path).resolve())]) == str(Path(managed_path).resolve()))
            except Exception:
                is_managed = False
        if is_managed and tag:
            sources.append(("installed release tag", tag))

        found: list[tuple[int, str]] = []
        for label, raw in sources:
            value = self.parse_build_number(raw)
            if value is not None:
                found.append((value, label))
        if not found:
            return None, "unknown"
        # Prefer the highest plausible build. This specifically avoids an
        # upstream ``version: 0`` masking b10819 in the managed runtime path.
        value, label = max(found, key=lambda x: x[0])
        return value, label

    def build_prerequisites(self) -> tuple[bool, list[str]]:
        missing = [name for name in ("git", "cmake") if not shutil.which(name)]
        return (not missing, missing)

    def _exe(self, name: str) -> str:
        return name + (".exe" if platform.system() == "Windows" else "")

    def find_binary(self, name: str) -> str | None:
        target = self._exe(name)
        if self.custom_server_path and name in {"llama-server", "llama-bench", "llama-cli"}:
            p = Path(self.custom_server_path).expanduser()
            if p.is_file():
                chosen = p if name == "llama-server" else p.parent / target
                # Never tune a custom server using an unrelated managed build.
                return str(chosen.resolve()) if chosen.is_file() else None
        pointer = self.runtime_dir / "installed.json"
        if pointer.exists():
            try:
                root = Path(json.loads(pointer.read_text(encoding="utf-8")).get("path", ""))
                if root.exists():
                    for p in root.rglob(target):
                        if p.is_file():
                            return str(p)
            except Exception:
                pass
        local_candidates = [
            Path.cwd() / target,
            Path.cwd() / "llama.cpp" / target,
            Path.cwd() / "llama.cpp" / "build" / "bin" / target,
            self.runtime_dir / "source-build" / target,
            self.runtime_dir / target,
            self.runtime_dir / "bin" / target,
        ]
        for p in local_candidates:
            if p.exists() and p.is_file():
                return str(p.resolve())
        for p in self.runtime_dir.rglob(target):
            if any(part.endswith(".installing") for part in p.parts): continue
            if p.is_file():
                return str(p)
        return shutil.which(target) or shutil.which(name)

    def status(self) -> dict:
        server = self.find_binary("llama-server")
        cli = self.find_binary("llama-cli")
        bench = self.find_binary("llama-bench")
        version = ""
        raw_version_output = ""
        if server:
            try:
                raw_version_output = subprocess.check_output(
                    [server, "--version"], stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", timeout=7
                )
                lines = [ln.strip() for ln in raw_version_output.splitlines() if ln.strip()]
                # Some Windows builds print backend loading messages before the actual version.
                preferred = next((ln for ln in lines if ln.lower().startswith("version:")), None)
                if preferred is None:
                    preferred = next((ln for ln in lines if "version" in ln.lower() and "backend" not in ln.lower()), None)
                version = preferred or (lines[-1] if lines else "")
            except Exception:
                pass
        build_number, build_source = self._detect_build_number(server or "", raw_version_output or version)
        rpc = self.find_rpc_binary()
        manifest = self._installed_manifest()
        asset_name = str(manifest.get("asset", "") or "")
        is_custom = bool(server and self.custom_server_path and Path(server) == Path(self.custom_server_path).expanduser())
        low_asset = asset_name.lower()
        if is_custom:
            backend = "Custom/unknown"
        elif "vulkan" in low_asset:
            backend = "Vulkan"
        elif "cuda" in low_asset or "cudart" in low_asset:
            backend = "CUDA"
        elif "rocm" in low_asset:
            backend = "ROCm"
        elif "sycl" in low_asset:
            backend = "SYCL"
        elif "openvino" in low_asset:
            backend = "OpenVINO"
        elif asset_name:
            backend = "CPU"
        else:
            backend = "Custom/unknown" if self.custom_server_path else "Unknown"
        return {
            "server": server, "cli": cli, "bench": bench, "rpc": rpc, "version": version,
            "build_number": build_number, "build_source": build_source,
            "backend": backend, "asset": asset_name,
            "source": "custom" if is_custom else "managed/auto",
        }

    def model_compatibility(self, architecture: str | None) -> dict:
        """Return a conservative preflight result for the active llama-server.

        We only reject combinations that are known to be impossible. The actual
        llama.cpp loader remains authoritative for everything else.
        """
        arch = (architecture or "").strip().lower()
        st = self.status()
        build = st.get("build_number")
        minimum = MIN_BUILD_BY_ARCH.get(arch)
        if minimum and build is not None and build < minimum:
            return {
                "ok": False,
                "architecture": arch,
                "build": build,
                "minimum": minimum,
                "message": (
                    f"The active llama.cpp runtime is build {build}, which is too old for model architecture '{arch}'. "
                    f"Install/activate a current CPU runtime before loading this model."
                ),
            }
        return {"ok": True, "architecture": arch, "build": build, "minimum": minimum, "message": ""}

    def find_rpc_binary(self) -> str | None:
        """Find the llama.cpp RPC worker binary across current/legacy names."""
        for name in ("ggml-rpc-server", "rpc-server"):
            found = self.find_binary(name)
            if found:
                return found
        return None

    def binary_supported_flags(self, binary: str | None, refresh: bool = False) -> set[str]:
        """Probe CLI flags for any managed llama.cpp binary."""
        if not binary:
            return set()
        try:
            out = subprocess.check_output(
                [str(binary), "--help"], stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", timeout=12
            )
            return set(re.findall(r"(?<![\w-])(--[a-zA-Z0-9][a-zA-Z0-9-]*|-[A-Za-z])(?=\s|,|$)", out))
        except Exception:
            return set()

    def supported_server_flags(self, refresh: bool = False) -> set[str]:
        server = self.find_binary("llama-server")
        if not server:
            return set()
        try:
            mtime = Path(server).stat().st_mtime
        except OSError:
            mtime = 0.0
        if not refresh and self._caps_cache and self._caps_cache[:2] == (server, mtime):
            return set(self._caps_cache[2])
        try:
            out = subprocess.check_output(
                [server, "--help"], stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", timeout=12
            )
            flags = set(re.findall(r"(?<![\w-])(--[a-zA-Z0-9][a-zA-Z0-9-]*)", out))
        except Exception:
            flags = set()
        self._caps_cache = (server, mtime, flags)
        return flags

    @staticmethod
    def _release_summary(data: dict) -> dict:
        return {
            "tag": data.get("tag_name", ""),
            "name": data.get("name", ""),
            "html_url": data.get("html_url", ""),
            "published_at": data.get("published_at", ""),
            "prerelease": bool(data.get("prerelease", False)),
            "assets": [
                {
                    "name": a.get("name", ""),
                    "url": a.get("browser_download_url", ""),
                    "size": a.get("size", 0),
                }
                for a in data.get("assets", [])
                if a.get("name") and a.get("browser_download_url")
            ],
        }

    def latest_release(self) -> dict:
        """Return GitHub's release marked Latest.

        Important: llama.cpp's semver release marked Latest may intentionally be
        source-only. Installation must therefore *not* assume this release has
        platform binaries; use latest_compatible_cpu_release() for installs.
        """
        _, data = request_json(
            GITHUB_LATEST_RELEASE,
            headers={"Accept": "application/vnd.github+json", "User-Agent": UA},
            timeout=30,
        )
        return self._release_summary(data)

    def recent_releases(self) -> list[dict]:
        """Return recent public releases in GitHub API order (newest first)."""
        _, data = request_json(
            GITHUB_RELEASES_LIST,
            headers={"Accept": "application/vnd.github+json", "User-Agent": UA},
            timeout=30,
        )
        if not isinstance(data, list):
            raise RuntimeError("GitHub returned an unexpected releases response")
        return [self._release_summary(r) for r in data if isinstance(r, dict)]

    def select_cpu_asset(self, release: dict) -> dict | None:
        """Select a native CPU archive and never silently cross architectures."""
        sysname = platform.system()
        machine = platform.machine().lower()
        is_x64 = machine in ("amd64", "x86_64", "x64")
        is_arm64 = machine in ("arm64", "aarch64") or ("arm" in machine and "64" in machine)
        gpu_markers = ("cuda", "cudart", "vulkan", "rocm", "sycl", "openvino", "opencl", "adreno")
        candidates: list[tuple[int, dict]] = []

        for a in release.get("assets", []):
            n = str(a.get("name", "")).lower()
            if not n.endswith((".zip", ".tar.gz", ".tgz")):
                continue
            if any(x in n for x in gpu_markers):
                continue

            score = 0
            if sysname == "Windows":
                if "win" not in n:
                    continue
                # Official Windows CPU archives explicitly contain `cpu`.
                if "cpu" not in n:
                    continue
                if is_x64:
                    if "x64" not in n or "arm64" in n:
                        continue
                    score += 50
                elif is_arm64:
                    if "arm64" not in n:
                        continue
                    score += 50
                else:
                    continue
                score += 30

            elif sysname == "Darwin":
                if "macos" not in n:
                    continue
                if is_x64:
                    if "x64" not in n or "arm64" in n:
                        continue
                    score += 50
                elif is_arm64:
                    if "arm64" not in n:
                        continue
                    score += 50
                else:
                    continue
                score += 30

            elif sysname == "Linux":
                if not any(x in n for x in ("ubuntu", "linux")):
                    continue
                if is_x64:
                    if "x64" not in n or "arm64" in n:
                        continue
                    score += 50
                elif is_arm64:
                    if "arm64" not in n:
                        continue
                    score += 50
                else:
                    continue
                if "cpu" in n:
                    score += 10
                score += 30
            else:
                continue

            # Prefer canonical llama binary bundles over unrelated archives.
            if "bin-" in n:
                score += 10
            if n.startswith("llama-"):
                score += 5
            candidates.append((score, a))

        return max(candidates, key=lambda x: x[0])[1] if candidates else None

    def select_vulkan_asset(self, release: dict) -> dict | None:
        """Select the official Vulkan binary for this platform/architecture.

        Vulkan is the portable accelerated path used for AMD and Intel iGPUs on
        Windows and also works with many discrete GPUs.  It is deliberately
        separate from CUDA/ROCm so one managed runtime can serve the common
        CPU+iGPU hybrid case without vendor-specific SDK setup.
        """
        sysname = platform.system()
        machine = platform.machine().lower()
        is_x64 = machine in ("amd64", "x86_64", "x64")
        is_arm64 = machine in ("arm64", "aarch64") or ("arm" in machine and "64" in machine)
        candidates: list[tuple[int, dict]] = []
        for a in release.get("assets", []):
            n = str(a.get("name", "")).lower()
            if "vulkan" not in n or not n.endswith((".zip", ".tar.gz", ".tgz")):
                continue
            score = 0
            if sysname == "Windows":
                if "win" not in n:
                    continue
                # llama.cpp currently publishes Vulkan for Windows x64. Keep
                # architecture checks strict so an ARM package is never used.
                if not is_x64 or "x64" not in n or "arm64" in n:
                    continue
                score += 80
            elif sysname == "Linux":
                if not any(x in n for x in ("ubuntu", "linux")):
                    continue
                if is_x64:
                    if "x64" not in n or "arm64" in n:
                        continue
                elif is_arm64:
                    if "arm64" not in n:
                        continue
                else:
                    continue
                score += 70
            else:
                continue
            if "bin-" in n:
                score += 10
            if n.startswith("llama-"):
                score += 5
            candidates.append((score, a))
        return max(candidates, key=lambda x: x[0])[1] if candidates else None

    def latest_compatible_cpu_release(self) -> tuple[dict, dict]:
        """Find the newest recent release that actually contains our CPU binary.

        llama.cpp currently has two release streams: semver releases may be
        source-only, while nightly bNNNN releases carry platform binaries.  We
        therefore scan recent releases instead of trusting /releases/latest.
        """
        errors: list[str] = []
        try:
            releases = self.recent_releases()
        except Exception as exc:
            errors.append(str(exc))
            releases = []

        # In case GitHub's list endpoint is filtered/proxied unexpectedly, also
        # try the marked-Latest release as a fallback candidate.
        if not releases:
            try:
                releases = [self.latest_release()]
            except Exception as exc:
                errors.append(str(exc))

        for rel in releases:
            asset = self.select_cpu_asset(rel)
            if asset:
                return rel, asset

        sysname = platform.system()
        machine = platform.machine()
        detail = f" Checked {len(releases)} recent releases." if releases else ""
        if errors:
            detail += " GitHub error: " + " | ".join(errors[:2])
        raise RuntimeError(
            f"No compatible llama.cpp CPU binary was found for {sysname} {machine}."
            f"{detail} You can still choose an existing llama-server or build CPU from source."
        )

    def latest_compatible_vulkan_release(self) -> tuple[dict, dict]:
        errors: list[str] = []
        try:
            releases = self.recent_releases()
        except Exception as exc:
            errors.append(str(exc)); releases = []
        if not releases:
            try:
                releases = [self.latest_release()]
            except Exception as exc:
                errors.append(str(exc))
        for rel in releases:
            asset = self.select_vulkan_asset(rel)
            if asset:
                return rel, asset
        detail = f" Checked {len(releases)} recent releases." if releases else ""
        if errors:
            detail += " GitHub error: " + " | ".join(errors[:2])
        raise RuntimeError(
            f"No compatible llama.cpp Vulkan binary was found for {platform.system()} {platform.machine()}."
            f"{detail}"
        )

    @staticmethod
    def _safe_extract_zip(archive: Path, dest: Path) -> None:
        root = dest.resolve()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if root != target and root not in target.parents:
                    raise RuntimeError("Unsafe path found in runtime archive")
            z.extractall(dest)

    @staticmethod
    def _safe_extract_tar(archive: Path, dest: Path) -> None:
        root = dest.resolve()
        with tarfile.open(archive, "r:gz") as t:
            for member in t.getmembers():
                target = (dest / member.name).resolve()
                if root != target and root not in target.parents:
                    raise RuntimeError("Unsafe path found in runtime archive")
            try:
                t.extractall(dest, filter="data")
            except TypeError:
                t.extractall(dest)

    def _install_release(
        self,
        rel: dict,
        asset: dict,
        progress_cb: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        if progress_cb:
            progress_cb(0, max(1, int(asset.get("size", 0) or 1)), f"Selected {rel.get('tag') or 'release'} / {asset['name']}")
        with tempfile.TemporaryDirectory(prefix="llamaforge-") as td:
            archive = Path(td) / asset["name"]
            req = urllib.request.Request(asset["url"], headers={"User-Agent": UA}, method="GET")
            try:
                response = open_url(req, timeout=180)
            except Exception as exc:
                raise RuntimeError(f"Runtime download failed: {exc}") from exc
            with response as r, archive.open("wb") as f:
                total = int(r.headers.get("Content-Length", "0") or asset.get("size", 0) or 0); done = 0
                while True:
                    if cancel and cancel.is_set():
                        raise RuntimeError("Installation cancelled")
                    chunk = r.read(256 * 1024)
                    if not chunk: break
                    f.write(chunk); done += len(chunk)
                    if progress_cb: progress_cb(done, total, f"Downloading {asset['name']}")

            # Immutable install directories keep running Windows DLLs and the
            # previous usable build untouched, including if promotion fails.
            tag = re.sub(r"[^A-Za-z0-9._-]", "_", str(rel["tag"]))[:80]
            install_id = f"{tag}-{uuid.uuid4().hex[:12]}"
            stage = self.runtime_dir / f".{install_id}.installing"
            final = self.runtime_dir / install_id
            stage.mkdir(parents=True, exist_ok=False)
            pointer_tmp = self.runtime_dir / f".{install_id}.json"
            try:
                if progress_cb: progress_cb(1, 1, "Extracting and verifying runtime…")
                if archive.name.endswith(".zip"):
                    self._safe_extract_zip(archive, stage)
                elif archive.name.endswith((".tar.gz", ".tgz")):
                    self._safe_extract_tar(archive, stage)
                else:
                    raise RuntimeError("Unsupported runtime archive type")
                server_name = self._exe("llama-server")
                if not any(p.is_file() for p in stage.rglob(server_name)):
                    raise RuntimeError("Downloaded archive did not contain llama-server")
                if cancel and cancel.is_set(): raise RuntimeError("Installation cancelled")
                stage.replace(final)
                pointer_tmp.write_text(json.dumps({"tag":rel["tag"], "asset":asset["name"], "path":str(final)}, indent=2), encoding="utf-8")
                pointer_tmp.replace(self.runtime_dir / "installed.json")
            finally:
                shutil.rmtree(stage, ignore_errors=True)
                pointer_tmp.unlink(missing_ok=True)
            self._caps_cache = None
        return {"tag": rel["tag"], "asset": asset["name"], "status": self.status()}

    def install_cpu_release(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        rel, asset = self.latest_compatible_cpu_release()
        return self._install_release(rel, asset, progress_cb=progress_cb, cancel=cancel)

    def install_vulkan_release(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        rel, asset = self.latest_compatible_vulkan_release()
        return self._install_release(rel, asset, progress_cb=progress_cb, cancel=cancel)

    def install_best_release(
        self,
        prefer_vulkan: bool = True,
        progress_cb: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        if prefer_vulkan:
            try:
                return self.install_vulkan_release(progress_cb=progress_cb, cancel=cancel)
            except Exception as exc:
                if cancel and cancel.is_set():
                    raise
                if progress_cb:
                    progress_cb(0, 1, f"Vulkan runtime unavailable ({exc}); falling back to CPU runtime")
        return self.install_cpu_release(progress_cb=progress_cb, cancel=cancel)

    @staticmethod
    def _run_logged(cmd: list[str], progress_cb: Callable[[str], None] | None = None) -> None:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        assert proc.stdout
        for line in proc.stdout:
            if progress_cb: progress_cb(line.rstrip())
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Command failed with exit code {rc}: {' '.join(cmd)}")

    def build_cpu_from_source(self, progress_cb: Callable[[str], None] | None = None) -> dict:
        ok, missing = self.build_prerequisites()
        if not ok:
            raise RuntimeError(
                "Build from source is unavailable because these tools are missing: "
                + ", ".join(missing)
                + ". Use the prebuilt CPU runtime instead, or install the missing developer tools first."
            )
        src = self.runtime_dir / "llama.cpp-src"; build = src / "build"
        if src.exists():
            if progress_cb: progress_cb("Updating llama.cpp source…")
            self._run_logged(["git", "-C", str(src), "pull", "--ff-only"], progress_cb)
        else:
            if progress_cb: progress_cb("Cloning llama.cpp…")
            self._run_logged(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", str(src)], progress_cb)
        if progress_cb: progress_cb("Configuring native CPU build…")
        self._run_logged([
            "cmake", "-S", str(src), "-B", str(build), "-DGGML_NATIVE=ON", "-DGGML_CUDA=OFF",
            "-DGGML_VULKAN=OFF", "-DGGML_RPC=ON", "-DLLAMA_CURL=OFF", "-DBUILD_SHARED_LIBS=ON", "-DCMAKE_BUILD_TYPE=Release",
        ], progress_cb)
        if progress_cb: progress_cb("Building llama-server, llama-cli, llama-bench and RPC worker…")
        self._run_logged(["cmake", "--build", str(build), "--config", "Release", "-j"], progress_cb)
        source_dirs = [build / "bin" / "Release", build / "bin"]
        source_dir = next((d for d in source_dirs if (d / self._exe("llama-server")).exists()), None)
        if not source_dir:
            raise RuntimeError("Build completed, but llama-server was not found")
        dest = self.runtime_dir / "source-build"; shutil.rmtree(dest, ignore_errors=True); shutil.copytree(source_dir, dest)
        # Activate the RPC-capable source build as one atomic runtime set.  This
        # prevents llama-server from coming from an older prebuilt release while
        # ggml-rpc-server comes from a different build/protocol revision.
        (self.runtime_dir / "installed.json").write_text(
            json.dumps({"tag": "source-rpc", "asset": "local source build", "path": str(dest)}, indent=2), encoding="utf-8"
        )
        self.custom_server_path = ""
        try:
            self.use_managed_runtime()
        except Exception:
            pass
        self._caps_cache = None
        return self.status()
